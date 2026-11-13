"""표준 logging 레코드를 PyQt6 메인 스레드로 안전하게 전달하는 GUI 로그 브리지 모듈"""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from src.core.logging_setup import APP_LOGGER_NAME, UserLogFormatter


class _GuiLogEmitter(QObject):
    """백그라운드 스레드의 로그 문자열을 메인 Qt 스레드로 전달하는 시그널 브리지"""

    log_received = pyqtSignal(str)


class _QtLogHandler(logging.Handler):
    """표준 logging 레코드를 UserLogFormatter 형식으로 변환해 Qt 시그널로 전달"""

    def __init__(self, emitter: _GuiLogEmitter) -> None:
        super().__init__(level=logging.INFO)
        self._emitter_ref: weakref.ref[_GuiLogEmitter] = weakref.ref(emitter)
        self.setFormatter(UserLogFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        emitter = self._emitter_ref()
        if emitter is None:
            return
        try:
            msg = self.format(record)
            emitter.log_received.emit(msg)
        except RuntimeError:
            # 창 종료 직후 이미 파괴된 C++ QObject로 늦게 도착한 백그라운드 로그 무시
            pass
        except Exception:
            self.handleError(record)


class GuiLogBridge:
    """애플리케이션 루트 로거와 GUI 로그 수신 콜백 간 연결 수명주기 관리"""

    def __init__(
        self,
        on_log: Callable[[str], None] | None = None,
        *,
        level: int = logging.INFO,
    ) -> None:
        self._emitter = _GuiLogEmitter()
        self._handler = _QtLogHandler(self._emitter)
        self._level = level
        self._handler.setLevel(level)
        self._attached = False
        if on_log is not None:
            self._emitter.log_received.connect(on_log)

    @property
    def emitter(self) -> _GuiLogEmitter:
        """로그 시그널을 노출하는 내부 QObject 이미터 반환"""
        return self._emitter

    @property
    def handler(self) -> logging.Handler:
        """로거에 등록되는 Qt 브리지 핸들러 반환"""
        return self._handler

    def attach(self, logger_name: str = APP_LOGGER_NAME) -> None:
        """지정한 로거에 브리지 핸들러 중복 없이 연결"""
        if self._attached:
            return
        app_logger = logging.getLogger(logger_name)
        if self._handler not in app_logger.handlers:
            app_logger.addHandler(self._handler)
        if app_logger.level == logging.NOTSET or app_logger.level > self._level:
            app_logger.setLevel(self._level)
        self._attached = True

    def detach(self, logger_name: str = APP_LOGGER_NAME) -> None:
        """연결된 브리지 핸들러를 로거에서 분리하고 리소스 정리"""
        app_logger = logging.getLogger(logger_name)
        if self._handler in app_logger.handlers:
            app_logger.removeHandler(self._handler)
        self._attached = False
