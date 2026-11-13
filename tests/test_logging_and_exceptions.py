import contextlib
import io
import logging
import os
import sys
import threading
import unittest

from PyQt6.QtWidgets import QApplication

from src.core.exceptions import (
    DEFAULT_ERROR_HINTS,
    DomainAttributeError,
    DomainFileNotFoundError,
    DomainInterruptedError,
    DomainRuntimeError,
    DomainTimeoutError,
    DomainTypeError,
    DomainValueError,
    ErrorCode,
    VideoScriptsError,
    format_user_error,
)
from src.core.logging_setup import (
    APP_LOGGER_NAME,
    CONSOLE_HANDLER_NAME,
    ensure_console_logging,
    get_app_logger,
)
from src.gui.logging_bridge import GuiLogBridge

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestDomainExceptions(unittest.TestCase):
    """도메인 예외의 표준 예외 호환성 및 사용자 친화적 오류 포맷터를 검증"""

    def test_standard_exception_compatibility(self) -> None:
        """도메인 예외 클래스가 표준 예외 계층과 ErrorCode를 동시에 만족하는지 검증"""
        cases: list[tuple[type[VideoScriptsError], type[BaseException], ErrorCode]] = [
            (DomainFileNotFoundError, FileNotFoundError, ErrorCode.ROOT_DIR_NOT_FOUND),
            (DomainValueError, ValueError, ErrorCode.ROOT_DIR_INVALID),
            (DomainRuntimeError, RuntimeError, ErrorCode.FILE_MOVE_BATCH_FAILED),
            (DomainTimeoutError, TimeoutError, ErrorCode.PROBE_TIMEOUT),
            (DomainInterruptedError, InterruptedError, ErrorCode.WORK_CANCELLED),
            (DomainAttributeError, AttributeError, ErrorCode.RULE_ATTR_ACCESS_DENIED),
            (DomainTypeError, TypeError, ErrorCode.INVALID_SORT_KEY),
        ]
        for domain_cls, std_cls, code in cases:
            exc = domain_cls("오류 메시지", code=code, hint="해결 힌트")
            self.assertIsInstance(exc, VideoScriptsError)
            self.assertIsInstance(exc, std_cls)
            self.assertEqual(exc.code, code)
            self.assertEqual(exc.hint, "해결 힌트")

    def test_format_user_error_uses_default_and_custom_hints(self) -> None:
        """format_user_error가 도메인 힌트와 OS 예외 폴백 안내를 생성하는지 검증"""
        exc_default = DomainTimeoutError("시간 초과", code=ErrorCode.PROBE_TIMEOUT)
        formatted = format_user_error(exc_default)
        self.assertIn("시간 초과", formatted)
        self.assertIn(DEFAULT_ERROR_HINTS[ErrorCode.PROBE_TIMEOUT], formatted)

        exc_custom = DomainValueError(
            "잘못된 입력", code=ErrorCode.ROOT_DIR_INVALID, hint="직접 힌트"
        )
        self.assertEqual(format_user_error(exc_custom), "잘못된 입력\n\n안내: 직접 힌트")

        perm_msg = format_user_error(PermissionError("access denied"))
        self.assertIn("접근 권한", perm_msg)

        os_msg = format_user_error(OSError("disk error"))
        self.assertIn("디스크 여유 공간", os_msg)


class TestLoggingSetupAndGuiBridge(unittest.TestCase):
    """표준 logging 핸들러 idempotency, 동적 stdout 출력 및 GuiLogBridge 연결을 검증"""

    def test_ensure_console_logging_idempotent_and_redirectable(self) -> None:
        """콘솔 로깅 핸들러 중복 등록 방지, redirect_stdout 캡처 및 stdout=None 방어를 검증"""
        pkg_logger = ensure_console_logging()
        ensure_console_logging()
        console_handlers = [
            h for h in pkg_logger.handlers if h.get_name() == CONSOLE_HANDLER_NAME
        ]
        self.assertEqual(len(console_handlers), 1)
        self.assertEqual(pkg_logger.name, APP_LOGGER_NAME)

        buf = io.StringIO()
        child_logger = get_app_logger("service.cache")
        with contextlib.redirect_stdout(buf):
            child_logger.info("테스트 정보")
            child_logger.warning("테스트 경고")
        output = buf.getvalue()
        self.assertIn("[INFO] 테스트 정보", output)
        self.assertIn("[WARN] 테스트 경고", output)

        orig_stdout = sys.stdout
        try:
            sys.stdout = None
            child_logger.info("stdout 미존재 시 무시")
        finally:
            sys.stdout = orig_stdout

    def test_gui_log_bridge_attach_thread_emit_and_detach(self) -> None:
        """GuiLogBridge가 백그라운드 스레드 로그를 Qt 메인 스레드로 전달하고 detach 후 차단하는지 검증"""
        received: list[str] = []
        bridge = GuiLogBridge(received.append)
        bridge.attach()
        self.addCleanup(bridge.detach)
        bridge.attach()

        pkg_logger = get_app_logger()
        attached_count = sum(1 for h in pkg_logger.handlers if h is bridge.handler)
        self.assertEqual(attached_count, 1)

        def _worker_log() -> None:
            logging.getLogger("src.service.cache").info("스레드 로그 메시지")

        thread = threading.Thread(target=_worker_log)
        thread.start()
        thread.join(timeout=2.0)
        _APP.processEvents()

        self.assertIn("[INFO] 스레드 로그 메시지", received)

        bridge.detach()
        logging.getLogger("src.service.cache").warning("분리 이후 경고")
        _APP.processEvents()
        self.assertFalse(any("분리 이후 경고" in msg for msg in received))


if __name__ == "__main__":
    unittest.main()
