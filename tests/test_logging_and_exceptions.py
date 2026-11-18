import contextlib
import io
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from src.core import messages as messages_mod
from src.core.exceptions import (
    DEFAULT_ERROR_HINTS,
    DomainAttributeError,
    DomainFileNotFoundError,
    DomainInterruptedError,
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
    FILE_HANDLER_NAME,
    USER_LOG_TIME_FORMAT,
    UserLogFormatter,
    ensure_console_logging,
    ensure_file_logging,
    get_app_logger,
    level_tag,
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

        self.assertTrue(any(line.endswith("[INFO] 스레드 로그 메시지") for line in received))

        bridge.detach()
        logging.getLogger("src.service.cache").warning("분리 이후 경고")
        _APP.processEvents()
        self.assertFalse(any("분리 이후 경고" in msg for msg in received))


class TestLogTagsAndFileLogging(unittest.TestCase):
    """표준 4단계 태그, GUI용 한 줄 예외 포맷, 순환 파일 로그 핸들러를 검증"""

    def test_log_tags_are_the_four_standard_levels(self) -> None:
        """LOG_TAG 상수가 DEBUG, INFO, WARN, ERROR 네 종류만 있는지 검증"""
        tags = {name for name in dir(messages_mod) if name.startswith("LOG_TAG_")}
        self.assertEqual(tags, {"LOG_TAG_DEBUG", "LOG_TAG_INFO", "LOG_TAG_WARN", "LOG_TAG_ERROR"})

    def test_level_tag_maps_levels_to_standard_tags(self) -> None:
        """로그 레벨 번호가 표준 태그로 변환되며 CRITICAL은 ERROR로 수렴함을 검증"""
        self.assertEqual(level_tag(logging.DEBUG), "[DEBUG]")
        self.assertEqual(level_tag(logging.INFO), "[INFO]")
        self.assertEqual(level_tag(logging.WARNING), "[WARN]")
        self.assertEqual(level_tag(logging.ERROR), "[ERROR]")
        self.assertEqual(level_tag(logging.CRITICAL), "[ERROR]")

    def test_gui_formatter_keeps_only_last_exception_line(self) -> None:
        """full_traceback이 False인 포맷터가 예외 스택 대신 마지막 줄만 붙이는지 검증"""
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord("src.x", logging.ERROR, __file__, 1, "작업 실패", None, sys.exc_info())
        text = UserLogFormatter(full_traceback=False).format(record)
        stamp = time.strftime(USER_LOG_TIME_FORMAT, time.localtime(record.created))
        self.assertEqual(text, f"[{stamp}] [ERROR] 작업 실패\nValueError: boom")
        self.assertNotIn("Traceback", text)

    def test_gui_formatter_stamps_record_creation_time(self) -> None:
        """포맷터 시각이 레코드 생성 시각(created) 기준이고 날짜와 시각 형식인지 검증"""
        record = logging.LogRecord("src.x", logging.INFO, __file__, 1, "시각 확인", None, None)
        record.created = 1_700_000_000.0
        text = UserLogFormatter().format(record)
        expected = time.strftime(USER_LOG_TIME_FORMAT, time.localtime(1_700_000_000.0))
        self.assertEqual(text, f"[{expected}] [INFO] 시각 확인")

    def test_file_logging_records_debug_and_traceback_once(self) -> None:
        """파일 핸들러가 DEBUG와 전체 스택을 기록하고 중복 연결하지 않음을 검증"""
        pkg_logger = logging.getLogger(APP_LOGGER_NAME)
        previous_level = pkg_logger.level
        self.addCleanup(pkg_logger.setLevel, previous_level)
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "logs", "app.log")
            ensure_file_logging(log_path)
            ensure_file_logging(log_path)
            handlers = [h for h in pkg_logger.handlers if h.get_name() == FILE_HANDLER_NAME]
            try:
                self.assertEqual(len(handlers), 1)
                child = logging.getLogger("src.test_file_logging")
                child.debug("디버그 메시지")
                try:
                    raise RuntimeError("파일 실패")
                except RuntimeError:
                    child.exception("예외 상세 기록")
                for h in handlers:
                    h.flush()
                with open(log_path, encoding="utf-8") as f:
                    content = f.read()
            finally:
                for h in handlers:
                    pkg_logger.removeHandler(h)
                    h.close()
        self.assertIn("디버그 메시지", content)
        self.assertIn("예외 상세 기록", content)
        self.assertIn("Traceback (most recent call last)", content)

    def test_file_logging_falls_back_to_console_when_directory_fails(self) -> None:
        """로그 폴더를 만들 수 없으면 파일 핸들러 없이 로거를 반환함을 검증"""
        pkg_logger = logging.getLogger(APP_LOGGER_NAME)
        previous_level = pkg_logger.level
        self.addCleanup(pkg_logger.setLevel, previous_level)
        with (
            patch("src.core.logging_setup.os.makedirs", side_effect=OSError("denied")),
            self.assertLogs(APP_LOGGER_NAME, level="WARNING"),
        ):
            ensure_file_logging(os.path.join("unused", "app.log"))
        self.assertFalse(any(h.get_name() == FILE_HANDLER_NAME for h in pkg_logger.handlers))


if __name__ == "__main__":
    unittest.main()
