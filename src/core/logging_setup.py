"""애플리케이션 전역 표준 logging 설정, 태그 포맷터, 콘솔과 순환 파일 핸들러 모듈

- 파일 기록은 큐로 넘겨 전용 스레드가 하게 해, GUI 스레드가 로그 한 줄에 파일 syscall을 하지 않게 함
- 순환 검사(os.stat, seek)가 GUI 스레드에서 돌면 다른 스레드와 GIL을 다투느라 화면이 멈춤
"""

import atexit
import logging
import os
import sys
import threading
import time
import traceback
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from queue import Queue

from src.core.messages import (
    LOG_TAG_DEBUG,
    LOG_TAG_ERROR,
    LOG_TAG_INFO,
    LOG_TAG_WARN,
)
from src.core.paths import get_data_dir

APP_LOGGER_NAME: str = "src"
CONSOLE_HANDLER_NAME: str = "video_scripts_console"
FILE_HANDLER_NAME: str = "video_scripts_file"
LOG_DIR_NAME: str = "logs"
LOG_FILE_NAME: str = "app.log"
LOG_FILE_MAX_BYTES: int = 2 * 1024 * 1024
LOG_FILE_BACKUP_COUNT: int = 3
FILE_LOG_FORMAT: str = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
RAW_MESSAGE_FORMAT: str = "%(message)s"
USER_LOG_TIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"
# - 종료할 때 큐에 남은 줄을 다 쓰도록 기다리는 상한. 넘으면 버퍼를 비우고 끝냄
QUEUE_FLUSH_TIMEOUT_SEC: float = 5.0


def level_tag(levelno: int) -> str:
    """로그 레벨 번호를 표준 태그([DEBUG], [INFO], [WARN], [ERROR])로 변환"""
    if levelno >= logging.ERROR:
        return LOG_TAG_ERROR
    if levelno >= logging.WARNING:
        return LOG_TAG_WARN
    if levelno >= logging.INFO:
        return LOG_TAG_INFO
    return LOG_TAG_DEBUG


class UserLogFormatter(logging.Formatter):
    """표준 로그 레코드를 '[YYYY-MM-DD HH:MM:SS] [INFO] ...' 형식의 단일 줄로 변환

    - 시각은 레코드 생성 시점 기준이며, 콘솔과 GUI 작업 기록 창이 이 포맷터를 함께 사용
    full_traceback이 False면 예외 스택 대신 마지막 줄(예외 종류와 메시지)만 붙여 GUI 로그 창을 간결하게 유지
    """

    def __init__(self, *, full_traceback: bool = True) -> None:
        super().__init__()
        self._full_traceback: bool = full_traceback

    def format(self, record: logging.LogRecord) -> str:
        """시각과 표준 태그 접두사를 붙여 포맷된 문자열 반환"""
        formatted_msg = record.getMessage()
        if record.exc_info:
            formatted_msg = f"{formatted_msg}\n{self._format_exception(record)}"
        stamp = time.strftime(USER_LOG_TIME_FORMAT, time.localtime(record.created))
        return f"[{stamp}] {level_tag(record.levelno)} {formatted_msg}"

    def _format_exception(self, record: logging.LogRecord) -> str:
        exc_info = record.exc_info
        if self._full_traceback:
            return self.formatException(exc_info) if exc_info else ""
        exc_value = exc_info[1] if exc_info else None
        if exc_value is None:
            return ""
        return "".join(traceback.format_exception_only(exc_value)).strip()


class _DynamicStdoutHandler(logging.Handler):
    """호출 시점 sys.stdout 출력으로 redirect_stdout 및 노트북 캡처 지원"""

    def emit(self, record: logging.LogRecord) -> None:
        stream = sys.stdout
        if stream is None:
            return
        try:
            msg = self.format(record)
            stream.write(msg + "\n")
            stream.flush()
        except Exception:
            self.handleError(record)


def get_app_logger(name: str | None = None) -> logging.Logger:
    """패키지('src') 기준 표준 로거 인스턴스 반환"""
    if not name or name == APP_LOGGER_NAME:
        return logging.getLogger(APP_LOGGER_NAME)
    if name.startswith(f"{APP_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{APP_LOGGER_NAME}.{name}")


def ensure_console_logging(level: int = logging.INFO) -> logging.Logger:
    """콘솔/노트북 환경에서 중복 없이 표준 stdout 로깅 핸들러 초기화"""
    pkg_logger = logging.getLogger(APP_LOGGER_NAME)
    if pkg_logger.level == logging.NOTSET or pkg_logger.level > level:
        pkg_logger.setLevel(level)
    pkg_logger.propagate = False

    if any(h.get_name() == CONSOLE_HANDLER_NAME for h in pkg_logger.handlers):
        return pkg_logger

    handler = _DynamicStdoutHandler()
    handler.set_name(CONSOLE_HANDLER_NAME)
    handler.setLevel(level)
    handler.setFormatter(UserLogFormatter())
    pkg_logger.addHandler(handler)
    return pkg_logger


def default_log_file_path() -> str:
    """데이터 폴더 아래 애플리케이션 로그 파일 경로 반환"""
    return os.path.join(get_data_dir(), LOG_DIR_NAME, LOG_FILE_NAME)


class FlushingQueueListener(QueueListener):
    """메인 스레드 대신 전용 스레드에서 파일에 기록하는 리스너

    - 로그 호출은 큐에 넣기만 하므로 순환 검사(os.stat, seek)가 GUI 스레드에서 사라짐
    - 종료할 때 남은 줄을 다 쓴 뒤 파일 핸들러를 닫아 마지막 줄을 잃지 않음
    """

    def __init__(self, file_handler: logging.Handler) -> None:
        self._log_queue: Queue[logging.LogRecord | None] = Queue()
        super().__init__(self._log_queue, file_handler)
        self.queue_handler: QueueHandler = QueueHandler(self._log_queue)

    def start(self) -> None:
        """리스너 스레드를 시작. 두 번 불러도 스레드가 하나만 돎"""
        if self._thread is None:
            super().start()

    def flush(self) -> None:
        """큐에 남은 줄이 모두 기록될 때까지 기다림"""
        if self._thread is not None:
            self._log_queue.join()

    def stop(self) -> None:
        """남은 줄을 모두 쓴 뒤 리스너 스레드와 파일 핸들러를 닫음. 두 번 불러도 안전"""
        thread = self._thread
        if thread is not None:
            self.enqueue_sentinel()
            thread.join(QUEUE_FLUSH_TIMEOUT_SEC)
            self._thread = None
        for handler in self.handlers:
            handler.flush()
            handler.close()


_file_listener: FlushingQueueListener | None = None
_file_lock: threading.Lock = threading.Lock()


def ensure_file_logging(log_path: str | None = None) -> logging.Logger:
    """DEBUG 이상을 순환 로그 파일에 기록하는 큐 핸들러를 중복 없이 연결

    - 파일을 만들 수 없으면 경고만 남기고 콘솔 로깅으로 계속 진행
    - 파일 핸들러가 있어야 진단용 스택 트레이스가 남으므로 로거 레벨을 DEBUG로 내림
    - 포맷은 큐에 넣는 스레드에서 끝내고, 리스너 스레드는 문자열을 파일에 쓰기만 함
      (예외 스택도 레코드가 큐를 건널 때 함께 실려야 잃지 않음)
    """
    global _file_listener
    pkg_logger = logging.getLogger(APP_LOGGER_NAME)
    pkg_logger.setLevel(logging.DEBUG)
    with _file_lock:
        if any(h.get_name() == FILE_HANDLER_NAME for h in pkg_logger.handlers):
            return pkg_logger

        path = log_path or default_log_file_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            file_handler = RotatingFileHandler(
                path,
                maxBytes=LOG_FILE_MAX_BYTES,
                backupCount=LOG_FILE_BACKUP_COUNT,
                encoding="utf-8",
            )
        except OSError:
            logging.getLogger(__name__).warning("로그 파일을 준비하지 못해 콘솔에만 기록합니다", exc_info=True)
            return pkg_logger

        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(RAW_MESSAGE_FORMAT))
        listener = FlushingQueueListener(file_handler)
        listener.start()
        queue_handler = listener.queue_handler
        queue_handler.set_name(FILE_HANDLER_NAME)
        queue_handler.setLevel(logging.DEBUG)
        queue_handler.setFormatter(logging.Formatter(FILE_LOG_FORMAT))
        pkg_logger.addHandler(queue_handler)
        _file_listener = listener
    return pkg_logger


def flush_file_logging() -> None:
    """큐에 쌓인 로그 줄이 파일에 기록될 때까지 기다림"""
    listener = _file_listener
    if listener is not None:
        listener.flush()


def shutdown_file_logging() -> None:
    """큐 로그 핸들러를 떼고 남은 줄을 쓴 뒤 리스너와 파일 핸들러를 닫음. 두 번 불러도 안전"""
    global _file_listener
    pkg_logger = logging.getLogger(APP_LOGGER_NAME)
    with _file_lock:
        listener, _file_listener = _file_listener, None
        if listener is None:
            return
        for handler in list(pkg_logger.handlers):
            if handler.get_name() == FILE_HANDLER_NAME:
                pkg_logger.removeHandler(handler)
                handler.close()
        listener.stop()


atexit.register(shutdown_file_logging)
