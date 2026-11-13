"""애플리케이션 전역 표준 logging 설정, 포맷터 및 동적 콘솔 핸들러 모듈"""

import logging
import sys

from src.core.messages import (
    LOG_TAG_DEBUG,
    LOG_TAG_ERROR,
    LOG_TAG_INFO,
    LOG_TAG_WARN,
)

APP_LOGGER_NAME: str = "src"
CONSOLE_HANDLER_NAME: str = "video_scripts_console"

_LEVEL_TAGS: dict[int, str] = {
    logging.DEBUG: LOG_TAG_DEBUG,
    logging.INFO: LOG_TAG_INFO,
    logging.WARNING: LOG_TAG_WARN,
    logging.ERROR: LOG_TAG_ERROR,
    logging.CRITICAL: LOG_TAG_ERROR,
}


class UserLogFormatter(logging.Formatter):
    """표준 로그 레코드를 '[INFO] ...', '[WARN] ...' 형식의 단일 줄 태그로 변환"""

    def format(self, record: logging.LogRecord) -> str:
        """로그 레벨에 맞는 표준 태그 접두사를 붙여 포맷된 문자열 반환"""
        if record.levelno >= logging.ERROR:
            tag = LOG_TAG_ERROR
        elif record.levelno >= logging.WARNING:
            tag = LOG_TAG_WARN
        elif record.levelno >= logging.INFO:
            tag = LOG_TAG_INFO
        else:
            tag = _LEVEL_TAGS.get(record.levelno, LOG_TAG_DEBUG)

        formatted_msg = record.getMessage()
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            formatted_msg = f"{formatted_msg}\n{exc_text}"
        return f"{tag} {formatted_msg}"


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
