"""사용자가 하던 일을 남기는 최근 이벤트 링 버퍼와 파일 기록 모듈

- 마지막 200건만 메모리에 보관해 사고 시점 직전 흐름을 보고서에 넣음
- 파일에는 시작 때 줄인 뒤 한 줄씩 덧붙여 무한히 자라지 않게 함
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from datetime import datetime

from src.core.diagnostics.paths import (
    BREADCRUMBS_MAX_BYTES,
    BREADCRUMBS_NAME,
    diagnostics_file,
    read_tail_lines,
)
from src.core.paths import write_utf8_file

BREADCRUMB_BUFFER_SIZE: int = 200
TIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"
LINE_TEMPLATE: str = "{stamp} [{category}] {message}"
# - 값싼 삭제 표식만으로는 파일이 커지므로, 시작 때 줄이고 기록 줄 수를 세어 다시 줄임
TRIM_CHECK_INTERVAL: int = 50

logger = logging.getLogger("src.diagnostics")


class BreadcrumbTrail:
    """최근 이벤트를 메모리 링 버퍼와 로그 파일에 함께 남기는 기록기"""

    def __init__(self, path: str, *, capacity: int = BREADCRUMB_BUFFER_SIZE) -> None:
        self._path = path
        self._events: deque[str] = deque(maxlen=max(1, capacity))
        self._lock = threading.Lock()
        self._appended_since_trim = 0

    @property
    def path(self) -> str:
        """기록 파일 경로"""
        return self._path

    def events(self) -> list[str]:
        """보관 중인 이벤트를 오래된 순서로 복사해 반환"""
        with self._lock:
            return list(self._events)

    def add(self, category: str, message: str, *, now: datetime | None = None) -> str:
        """이벤트 한 건을 메모리와 파일에 기록하고 기록한 줄 반환

        - 파일 기록 실패는 앱 흐름을 막지 않도록 조용히 넘김
        """
        line = format_event(category, message, now=now)
        with self._lock:
            self._events.append(line)
        self._append(line)
        return line

    def _append(self, line: str) -> None:
        try:
            with open(self._path, "a", encoding="utf-8", buffering=1) as handle:
                handle.write(line + "\n")
            self._appended_since_trim += 1
            if self._appended_since_trim >= TRIM_CHECK_INTERVAL:
                self._appended_since_trim = 0
                if os.path.getsize(self._path) > BREADCRUMBS_MAX_BYTES:
                    self._rewrite_buffer()
        except OSError:
            return

    def _rewrite_buffer(self) -> None:
        """파일이 상한을 넘으면 메모리에 남은 최근 이벤트만으로 다시 씀"""
        content = "\n".join(self.events()) + "\n"
        try:
            write_utf8_file(self._path, content)
        except OSError:
            return


def format_event(category: str, message: str, *, now: datetime | None = None) -> str:
    """시각, 분류, 내용을 한 줄로 합친 이벤트 문구 생성"""
    stamp = (now or datetime.now()).strftime(TIME_FORMAT)
    return LINE_TEMPLATE.format(stamp=stamp, category=category.strip() or "기타", message=message)


def read_breadcrumb_lines(path: str, limit: int = BREADCRUMB_BUFFER_SIZE) -> list[str]:
    """브레드크럼 파일 끝에서 limit 줄을 읽어 반환. 없으면 빈 목록"""
    return read_tail_lines(path, limit, max_bytes=BREADCRUMBS_MAX_BYTES)


def breadcrumbs_path(directory: str | None = None) -> str:
    """브레드크럼 기록 파일 경로 반환"""
    return diagnostics_file(BREADCRUMBS_NAME, directory)


def base_name(path: str) -> str:
    """사용자 폴더 구조를 최소로 노출하려고 경로에서 파일 이름만 남김"""
    name = os.path.basename(os.path.normpath(path)) if path else ""
    return name or "알 수 없음"


def format_list_event(action: str, count: int) -> str:
    """여러 항목을 대상으로 한 동작을 개수와 함께 간결히 표현"""
    return f"{action} {count}건"


# - 이미 남기는 업무 로그를 그대로 브레드크럼으로 옮길 로거(모듈) 목록. 같은 메시지를 두 번 쓰지 않으려고 화이트리스트로 제한
MIRRORED_LOGGER_NAMES: frozenset[str] = frozenset(
    {
        "src.gui.controllers.task_runner",
        "src.gui.controllers.encode_coordinator",
        "src.gui.controllers.session_controller",
        "src.gui.controllers.restore_flow",
        "src.gui.controllers.dialog_coordinator",
        "src.gui.preview.preview_controller",
        "src.core.analysis.prober",
        "src.core.encode.encode_process",
    }
)
MIRROR_HANDLER_NAME: str = "video_scripts_breadcrumbs"
# - 경고 이상은 모듈과 무관하게 남겨 사고 직전 원인을 놓치지 않음
MIRROR_ALL_WARNING_LEVEL: int = logging.WARNING


class LogBreadcrumbHandler(logging.Handler):
    """이미 남기는 업무 로그 레코드를 브레드크럼 링 버퍼와 파일로 복사하는 핸들러

    - 목록에 있는 모듈은 INFO 이상, 그 밖의 모듈은 WARNING 이상만 남겨 행 단위 잡음을 막음
    - 예외 스택은 붙이지 않고 마지막 메시지 한 줄만 남겨 링 버퍼를 짧게 유지
    """

    def __init__(self, trail: BreadcrumbTrail) -> None:
        super().__init__(level=logging.INFO)
        self._trail = trail
        self.set_name(MIRROR_HANDLER_NAME)

    def emit(self, record: logging.LogRecord) -> None:
        """통과한 레코드를 브레드크럼에 한 줄로 남김"""
        if not self._should_mirror(record):
            return
        try:
            self._trail.add(_category_of(record.name), record.getMessage())
        except Exception:
            self.handleError(record)

    @staticmethod
    def _should_mirror(record: logging.LogRecord) -> bool:
        """목록에 있는 모듈의 INFO 이상, 그 밖의 모듈의 WARNING 이상만 통과"""
        if record.name in MIRRORED_LOGGER_NAMES:
            return record.levelno >= logging.INFO
        return record.levelno >= MIRROR_ALL_WARNING_LEVEL


def _category_of(logger_name: str) -> str:
    """로거 이름의 마지막 조각을 짧은 분류 이름으로 사용"""
    tail = logger_name.rsplit(".", 1)[-1]
    return tail.replace("_", " ")


def attach_log_mirror(trail: BreadcrumbTrail) -> LogBreadcrumbHandler:
    """앱 루트 로거에 로그 미러 핸들러를 중복 없이 연결하고 반환"""
    handler = LogBreadcrumbHandler(trail)
    app_logger = logging.getLogger("src")
    if not any(h.get_name() == MIRROR_HANDLER_NAME for h in app_logger.handlers):
        app_logger.addHandler(handler)
    return handler


def detach_log_mirror(handler: LogBreadcrumbHandler | None) -> None:
    """연결한 로그 미러 핸들러를 떼고 자원 정리"""
    if handler is None:
        return
    logging.getLogger("src").removeHandler(handler)
    handler.close()
