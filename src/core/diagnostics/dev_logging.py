"""개발자 모드의 상세 로그 파일과 FFmpeg 명령 기록 미러 모듈

- 앱 모듈(`src.*`)의 DEBUG 기록만 `data/logs/dev.log`에 남기고 Qt, 라이브러리 잡음은 넣지 않음
- 순환 파일 핸들러로 크기를 제한해 개발자 모드를 오래 켜 두어도 디스크를 채우지 않음
- 명령 기록 링 버퍼의 변경을 로거 한 줄로 옮겨, 사고 보고서에 마지막에 실행한 명령이 남게 함
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler

from src.core import command_history
from src.core.command_history import CommandRecord
from src.core.diagnostics.paths import DEV_LOG_NAME, diagnostics_file

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
    handler: RotatingFileHandler | None
    mirror: CommandLogMirror | None
    listener: Callable[[], None] | None
    previous_level: int | None = None


def install_dev_log(directory: str | None = None) -> DevLogSession:
    """`src.*` DEBUG 기록을 `dev.log`에 남기는 순환 핸들러와 명령 기록 미러를 설치

    - 파일을 만들지 못하면 핸들러 없이 세션만 돌려주고 앱 시작을 막지 않음
    - 이미 같은 이름의 핸들러가 있으면 중복해서 붙이지 않음
    - 앱 루트 로거의 이전 레벨을 세션에 기억해 정리할 때 되돌림
    """
    path = diagnostics_file(DEV_LOG_NAME, directory)
    handler = _create_handler(path)
    root = logging.getLogger(APP_LOGGER_NAME)
    previous_level: int | None = None
    if handler is not None:
        if not any(item.get_name() == DEV_LOG_HANDLER_NAME for item in root.handlers):
            previous_level = root.level
            root.setLevel(ROOT_LOGGER_LEVEL)
            root.addHandler(handler)
        else:
            handler.close()
            handler = None
    mirror = CommandLogMirror()
    command_history.add_listener(mirror)
    return DevLogSession(
        path=path,
        handler=handler,
        mirror=mirror,
        listener=mirror,
        previous_level=previous_level,
    )


def _create_handler(path: str) -> RotatingFileHandler | None:
    """개발 로그 순환 핸들러 생성. 만들지 못하면 None"""
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
    handler.set_name(DEV_LOG_HANDLER_NAME)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(DEV_LOG_FORMAT))
    return handler


def remove_dev_log(session: DevLogSession | None) -> None:
    """설치한 개발 로그 핸들러와 명령 기록 리스너를 떼고 파일을 닫음. 중복 호출 안전"""
    if session is None:
        return
    if session.listener is not None:
        command_history.remove_listener(session.listener)
        session.listener = None
    if session.handler is not None:
        root = logging.getLogger(APP_LOGGER_NAME)
        root.removeHandler(session.handler)
        session.handler.close()
        session.handler = None
        if session.previous_level is not None:
            logging.getLogger(APP_LOGGER_NAME).setLevel(session.previous_level)
            session.previous_level = None
    session.mirror = None
