"""개발자 모드의 상세 로그 파일과 FFmpeg 명령 기록 미러 모듈

- 앱 모듈(`src.*`)의 DEBUG 기록만 `data/logs/dev.log`에 남기고 Qt, 라이브러리 잡음은 넣지 않음
- 순환 파일 핸들러로 크기를 제한해 개발자 모드를 오래 켜 두어도 디스크를 채우지 않음
- 파일 기록은 큐로 넘겨 전용 스레드가 해, GUI 스레드가 순환 검사나 seek를 하지 않게 함
- 명령 기록 링 버퍼의 변경을 로거 한 줄로 옮겨, 사고 보고서에 마지막에 실행한 명령이 남게 함
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from logging.handlers import QueueHandler, RotatingFileHandler

from src.core import command_history
from src.core.command_history import CommandRecord
from src.core.diagnostics.paths import DEV_LOG_NAME, diagnostics_file
from src.core.logging_setup import RAW_MESSAGE_FORMAT, FlushingQueueListener

DEV_LOG_MAX_BYTES: int = 2 * 1024 * 1024
DEV_LOG_BACKUP_COUNT: int = 2
DEV_LOG_FORMAT: str = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DEV_LOG_HANDLER_NAME: str = "video_scripts_dev_log"
APP_LOGGER_NAME: str = "src"
# - 앱 루트 로거가 DEBUG까지 흘려야 자식 모듈의 DEBUG가 핸들러까지 도달함
ROOT_LOGGER_LEVEL: int = logging.DEBUG
COMMAND_LOG_TEMPLATE: str = "[{stage}] {target} -> {display} (종료 코드 {code}, {elapsed})"
UNKNOWN_TARGET: str = "대상 없음"
RUNNING_CODE_TEXT: str = "실행 중"
UNKNOWN_ELAPSED_TEXT: str = "소요 알 수 없음"
ELAPSED_TEMPLATE: str = "{elapsed:.2f}초"

logger = logging.getLogger("src.diagnostics")


def format_elapsed(elapsed_sec: float | None) -> str:
    """소요 시간을 소수 둘째 자리 초 문구로 표시. 없으면 안내 문구"""
    return UNKNOWN_ELAPSED_TEXT if elapsed_sec is None else ELAPSED_TEMPLATE.format(elapsed=elapsed_sec)


def format_command_record(record: CommandRecord) -> str:
    """명령 기록 한 건을 개발 로그 한 줄로 변환"""
    code = RUNNING_CODE_TEXT if record.exit_code is None else str(record.exit_code)
    return COMMAND_LOG_TEMPLATE.format(
        stage=record.stage,
        target=record.target_name or UNKNOWN_TARGET,
        display=record.display,
        code=code,
        elapsed=format_elapsed(record.elapsed_sec),
    )


class CommandLogMirror:
    """명령 기록 링 버퍼의 마지막 변경 한 건을 DEBUG로 옮기는 리스너

    - 리스너는 기록을 바꾼 스레드에서 불리므로 파일 기록만 하고 Qt는 건드리지 않음
    - 알림마다 전체 기록을 복사하지 않고 마지막 한 건만 읽어 비용을 O(1)로 유지
    - 링 버퍼에 남은 마지막 기록이 이미 남긴 번호, 종료 코드와 같으면 건너뛰어 중복을 막음
    - 같은 번호라도 종료 코드가 채워지면 시작과 종료 두 줄을 남김
    """

    def __init__(
        self,
        *,
        latest: Callable[[], CommandRecord | None] = command_history.latest,
        target_logger: logging.Logger | None = None,
    ) -> None:
        self._latest = latest
        self._logger = target_logger or logging.getLogger(APP_LOGGER_NAME)
        self._last_state: tuple[int, int | None] | None = None

    def __call__(self) -> None:
        """가장 최근 명령 기록을 한 줄로 남김. 새 상태가 없으면 아무것도 하지 않음"""
        record = self._latest()
        if record is None:
            return
        state = (record.seq, record.exit_code)
        if state == self._last_state:
            return
        self._last_state = state
        self._logger.debug("FFmpeg 명령 %s", format_command_record(record))


@dataclass
class DevLogSession:
    """개발 로그 핸들러와 명령 기록 리스너를 함께 들고 다니는 세션"""

    path: str
    handler: QueueHandler | None
    mirror: CommandLogMirror | None
    listener: Callable[[], None] | None
    previous_level: int | None = None
    # - 파일 기록과 그 스레드를 들고 있어야 정리할 때 남은 줄을 다 쓰고 닫을 수 있음
    queue_listener: FlushingQueueListener | None = None


# - 지금 붙어 있는 미러: 리스너는 동등 비교가 안 되므로 설치 쪽에서 보관해 중복 설치를 막음
_installed_mirror: CommandLogMirror | None = None
# - 지금 도는 큐 리스너: 핸들러만 떨어진 채 남아 파일을 붙잡고 있으면 새로 설치할 때 멈춤
_installed_listener: FlushingQueueListener | None = None
# - 설치 전 레벨: 세션을 잃어버려 핸들러만 떨어진 뒤 다시 설치해도, 이미 올려 둔 DEBUG가 아니라
#   최초 설치 전 레벨로 되돌리도록 모듈에서 한 번만 기억함
_saved_level: int | None = None


def _dev_log_handler_attached(root: logging.Logger) -> bool:
    """앱 루트 로거에 개발 로그 핸들러가 실제로 붙어 있는지 여부"""
    return any(item.get_name() == DEV_LOG_HANDLER_NAME for item in root.handlers)


def _drop_stale_resources() -> None:
    """핸들러가 사라진 뒤 남은 미러와 큐 리스너를 지워 다음 설치가 새로 붙이게 함

    - 세션을 잃어버려 정리되지 않은 리스너가 파일을 붙잡고 있으면 먼저 멈춰야
      새 순환 핸들러가 같은 파일을 열 수 있음
    """
    global _installed_mirror, _installed_listener
    if _installed_mirror is not None:
        command_history.remove_listener(_installed_mirror)
        _installed_mirror = None
    if _installed_listener is not None:
        _installed_listener.stop()
        _installed_listener = None


def install_dev_log(directory: str | None = None) -> DevLogSession:
    """`src.*` DEBUG 기록을 `dev.log`에 남기는 순환 핸들러와 명령 기록 미러를 설치

    - 파일을 만들지 못하면 핸들러 없이 세션만 돌려주고 앱 시작을 막지 않음
    - 설치 여부는 세션이 아니라 앱 루트 로거에 붙어 있는 핸들러 이름으로 판단해,
      세션을 정리하지 않고 잃어버려도 다음 설치가 핸들러와 미러를 새로 붙이게 함
    - 핸들러가 이미 붙어 있으면 핸들러를, 미러가 붙어 있으면 리스너를 새로 붙이지 않음
      (두 번 설치해도 명령 기록이 두 줄로 중복되지 않게 함)
    - 앱 루트 로거의 이전 레벨을 세션에 기억해 정리할 때 되돌림. 세션을 잃어버린 뒤
      다시 설치하는 경우에는 지금 레벨(이전 세션이 올려 둔 DEBUG)이 아니라 최초 설치 전
      레벨을 기억해 정리 뒤 DEBUG가 남지 않게 함
    """
    global _installed_mirror, _installed_listener, _saved_level
    path = diagnostics_file(DEV_LOG_NAME, directory)
    root = logging.getLogger(APP_LOGGER_NAME)
    handler: QueueHandler | None = None
    queue_listener: FlushingQueueListener | None = None
    previous_level: int | None = None
    if not _dev_log_handler_attached(root):
        _drop_stale_resources()
        created = _create_handler(path)
        if created is not None:
            queue_listener = FlushingQueueListener(created)
            queue_listener.start()
            _installed_listener = queue_listener
            handler = queue_listener.queue_handler
            handler.set_name(DEV_LOG_HANDLER_NAME)
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(logging.Formatter(RAW_MESSAGE_FORMAT))
            if _saved_level is None:
                _saved_level = root.level
            previous_level = _saved_level
            root.setLevel(ROOT_LOGGER_LEVEL)
            root.addHandler(handler)
    mirror: CommandLogMirror | None = None
    if _installed_mirror is None:
        mirror = CommandLogMirror()
        command_history.add_listener(mirror)
        _installed_mirror = mirror
    return DevLogSession(
        path=path,
        handler=handler,
        mirror=mirror,
        listener=mirror,
        previous_level=previous_level,
        queue_listener=queue_listener,
    )


def _create_handler(path: str) -> RotatingFileHandler | None:
    """개발 로그 순환 파일 핸들러 생성. 만들지 못하면 None"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=DEV_LOG_MAX_BYTES,
            backupCount=DEV_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        logger.warning("개발자 모드 로그 파일을 준비하지 못했습니다", exc_info=True)
        return None
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(DEV_LOG_FORMAT))
    return handler


def flush_dev_log(session: DevLogSession | None) -> None:
    """개발 로그 큐에 쌓인 줄이 파일에 기록될 때까지 기다림. 세션이 없으면 무시"""
    if session is not None and session.queue_listener is not None:
        session.queue_listener.flush()


def remove_dev_log(session: DevLogSession | None) -> None:
    """설치한 개발 로그 핸들러와 명령 기록 리스너를 떼고 파일을 닫음. 중복 호출 안전

    - 큐 리스너를 먼저 멈춰 남은 줄을 다 쓴 뒤 파일 핸들러를 닫음(닫힌 파일에 쓰지 않게)
    """
    global _installed_mirror, _installed_listener, _saved_level
    if session is None:
        return
    if session.listener is not None:
        command_history.remove_listener(session.listener)
        if _installed_mirror is session.listener:
            _installed_mirror = None
        session.listener = None
    if session.handler is not None:
        root = logging.getLogger(APP_LOGGER_NAME)
        root.removeHandler(session.handler)
        session.handler = None
    if session.queue_listener is not None:
        session.queue_listener.stop()
        if _installed_listener is session.queue_listener:
            _installed_listener = None
        session.queue_listener = None
    if session.previous_level is not None:
        root = logging.getLogger(APP_LOGGER_NAME)
        root.setLevel(session.previous_level)
        if _saved_level == session.previous_level:
            _saved_level = None
        session.previous_level = None
    session.mirror = None
