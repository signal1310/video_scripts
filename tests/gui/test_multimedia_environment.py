"""GUI 진입 환경 설정과 진단 시작 순서 검증: 하드웨어 디코더 우회 기본값, 로그 파일 핸들러 순서"""

import logging
import os
import sys
import tempfile
import unittest
from unittest import mock

from src.core.diagnostics.runtime import DiagnosticsRuntime
from src.core.logging_setup import FILE_HANDLER_NAME, ensure_file_logging
from src.gui import (
    MULTIMEDIA_HW_DECODE_ENV,
    bootstrap_diagnostics,
    configure_multimedia_environment,
    diagnostics_qt,
)


class ConfigureMultimediaEnvironmentTests(unittest.TestCase):
    """Windows에서만 소프트웨어 디코더 기본값을 두고 사용자가 지정한 값은 유지하는지 검증"""

    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(MULTIMEDIA_HW_DECODE_ENV, None)

    def test_windows_defaults_to_the_software_decoder(self) -> None:
        """Windows에서 하드웨어 디코더 우회 값이 없으면 소프트웨어 디코더로 설정하는지 검증"""
        with mock.patch.object(sys, "platform", "win32"):
            configure_multimedia_environment()
        self.assertEqual(os.environ.get(MULTIMEDIA_HW_DECODE_ENV), "none")

    def test_windows_keeps_a_user_specified_value(self) -> None:
        """사용자가 직접 지정한 디코더 값을 덮어쓰지 않는지 검증"""
        os.environ[MULTIMEDIA_HW_DECODE_ENV] = "d3d11va"
        with mock.patch.object(sys, "platform", "win32"):
            configure_multimedia_environment()
        self.assertEqual(os.environ[MULTIMEDIA_HW_DECODE_ENV], "d3d11va")

    def test_other_platforms_leave_the_environment_untouched(self) -> None:
        """Windows가 아니면 환경 변수를 만들지 않는지 검증"""
        with mock.patch.object(sys, "platform", "linux"):
            configure_multimedia_environment()
        self.assertNotIn(MULTIMEDIA_HW_DECODE_ENV, os.environ)


class BootstrapDiagnosticsTests(unittest.TestCase):
    """진단 시작 전에 로그 파일 핸들러가 준비되고 시작 직후 기록이 파일에 남는지 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log_path = os.path.join(self._tmp.name, "app.log")
        patcher = mock.patch(
            "src.core.logging_setup.default_log_file_path", return_value=self.log_path
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._remove_file_handlers)

    def _remove_file_handlers(self) -> None:
        """테스트가 붙인 파일 핸들러를 닫고 제거해 다른 테스트로 새지 않게 함"""
        diagnostics_qt.stop_gui_diagnostics()
        pkg_logger = logging.getLogger("src")
        for handler in list(pkg_logger.handlers):
            if handler.get_name() == FILE_HANDLER_NAME:
                pkg_logger.removeHandler(handler)
                handler.close()

    def test_file_logging_is_ready_before_diagnostics_start(self) -> None:
        """진단을 시작하는 시점에 파일 핸들러가 이미 붙어 있는지 검증"""
        seen: list[bool] = []

        def probe(*, developer_mode: bool = False) -> None:
            names = [handler.get_name() for handler in logging.getLogger("src").handlers]
            seen.append(FILE_HANDLER_NAME in names)

        with mock.patch("src.gui.start_gui_diagnostics", side_effect=probe):
            bootstrap_diagnostics()
        self.assertEqual(seen, [True])

    def test_message_after_start_reaches_app_log_file(self) -> None:
        """진단 시작 직후 기록한 메시지가 앱 로그 파일에 남는지 검증"""
        with (
            mock.patch.object(
                diagnostics_qt,
                "start_diagnostics",
                return_value=DiagnosticsRuntime(directory=self._tmp.name),
            ),
            mock.patch.object(diagnostics_qt, "install_qt_message_handler"),
        ):
            bootstrap_diagnostics()
        logging.getLogger("src.diagnostics").warning("시작 직후 기록 확인")
        for handler in logging.getLogger("src").handlers:
            handler.flush()
        with open(self.log_path, encoding="utf-8") as handle:
            written = handle.read()
        self.assertIn("시작 직후 기록 확인", written)

    def test_ensure_file_logging_is_reused(self) -> None:
        """로그 파일 핸들러가 이미 있으면 다시 붙이지 않는지 검증"""
        ensure_file_logging(self.log_path)
        ensure_file_logging(self.log_path)
        names = [handler.get_name() for handler in logging.getLogger("src").handlers]
        self.assertEqual(names.count(FILE_HANDLER_NAME), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
