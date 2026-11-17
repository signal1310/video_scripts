"""애플리케이션 전역 표준 logging 설정, 태그 포맷터, 콘솔과 순환 파일 핸들러 모듈"""

import logging
import os
import sys
import time
import traceback
from logging.handlers import RotatingFileHandler

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
USER_LOG_TIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"


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


def ensure_file_logging(log_path: str | None = None) -> logging.Logger:
    """DEBUG 이상을 순환 로그 파일에 기록하는 핸들러를 중복 없이 연결

    - 파일을 만들 수 없으면 경고만 남기고 콘솔 로깅으로 계속 진행
    - 파일 핸들러가 있어야 진단용 스택 트레이스가 남으므로 로거 레벨을 DEBUG로 내림
    """
    pkg_logger = logging.getLogger(APP_LOGGER_NAME)
    pkg_logger.setLevel(logging.DEBUG)
    if any(h.get_name() == FILE_HANDLER_NAME for h in pkg_logger.handlers):
        return pkg_logger

    path = log_path or default_log_file_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        logging.getLogger(__name__).warning("로그 파일을 준비하지 못해 콘솔에만 기록합니다", exc_info=True)
        return pkg_logger

    handler.set_name(FILE_HANDLER_NAME)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(FILE_LOG_FORMAT))
    pkg_logger.addHandler(handler)
    return pkg_logger
