"""GUI 진단 어댑터: 런타임 시작/종료 재진입, Qt 메시지 전달, 폴더 열기 실패 처리 검증"""

import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QtMsgType
from PyQt6.QtWidgets import QApplication

from src.gui import diagnostics_qt

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestGuiDiagnosticsAdapter(unittest.TestCase):
    """시작한 런타임 재사용, 이벤트 기록, 종료 시 표식 닫기 검증"""

    def tearDown(self) -> None:
        diagnostics_qt.stop_gui_diagnostics()

    def test_start_returns_same_runtime_and_notes_events(self) -> None:
        """두 번 시작해도 같은 런타임을 쓰고, 이벤트가 브레드크럼에 남는지 검증"""
        first = diagnostics_qt.start_gui_diagnostics()
        second = diagnostics_qt.start_gui_diagnostics()
        try:
            self.assertIs(first, second)
            self.assertIs(diagnostics_qt.current_runtime(), first)
            trail = diagnostics_qt.current_trail()
            self.assertIsNotNone(trail)
            diagnostics_qt.note_event("미리 보기", "clip.mp4")
            assert trail is not None
            self.assertTrue(trail.events()[-1].endswith("[미리 보기] clip.mp4"))
        finally:
            diagnostics_qt.stop_gui_diagnostics()
        self.assertIsNone(diagnostics_qt.current_runtime())
        self.assertIsNone(diagnostics_qt.current_trail())

    def test_note_event_without_runtime_is_silent(self) -> None:
        """진단을 시작하지 않았으면 이벤트 기록이 예외 없이 무시되는지 검증"""
        diagnostics_qt.note_event("분석", "시작")

    def test_stop_twice_is_safe(self) -> None:
        """종료를 두 번 호출해도 예외가 없는지 검증"""
        diagnostics_qt.start_gui_diagnostics()
        diagnostics_qt.stop_gui_diagnostics()
        diagnostics_qt.stop_gui_diagnostics()


class TestQtMessageHandler(unittest.TestCase):
    """Qt 메시지 핸들러가 이전 핸들러로도 전달하고 로깅에 남기는지 검증"""

    def test_handle_message_forwards_to_previous_handler(self) -> None:
        """설치한 핸들러가 메시지를 표준 로깅과 이전 핸들러 양쪽에 넘기는지 검증"""
        received: list[tuple[object, str]] = []

        def fake_previous(mode: object, context: object, message: str) -> None:
            received.append((mode, message))

        with (
            patch.object(diagnostics_qt, "_previous_handler", fake_previous),
            self.assertLogs("src.diagnostics", level="WARNING") as captured,
        ):
            diagnostics_qt.handle_qt_message(
                QtMsgType.QtWarningMsg, _context(), "창을 표시하지 못했습니다"
            )
        self.assertEqual(received, [(QtMsgType.QtWarningMsg, "창을 표시하지 못했습니다")])
        self.assertTrue(any("창을 표시하지 못했습니다" in line for line in captured.output))

    def test_install_handler_marks_installed_and_runs_twice(self) -> None:
        """Qt 메시지 핸들러를 두 번 설치해도 예외 없이 설치 상태가 유지되는지 검증"""
        diagnostics_qt.install_qt_message_handler()
        self.assertTrue(diagnostics_qt._handler_installed)
        diagnostics_qt.install_qt_message_handler()
        self.assertTrue(diagnostics_qt._handler_installed)


class _FakeContext:
    """QtMessageHandler의 context 대역"""

    file = "window.cpp"
    line = 42
    function = "show"
    category = "qt.qpa.window"


def _context() -> _FakeContext:
    """대역 context 생성"""
    return _FakeContext()


class TestFolderOpening(unittest.TestCase):
    """진단 폴더 열기가 실패를 예외 없이 알리는지 검증"""

    def test_open_folder_rejects_missing_directory(self) -> None:
        """없는 폴더는 열지 않고 False를 돌려주는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(diagnostics_qt.open_folder(os.path.join(tmp, "none")))
            with patch("src.gui.diagnostics_qt._open_path") as opener:
                self.assertTrue(diagnostics_qt.open_folder(tmp))
            self.assertEqual(opener.call_count, 1)

    def test_open_folder_reports_start_failure(self) -> None:
        """외부 프로그램 실행이 실패하면 False를 돌려주는지 검증"""
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.gui.diagnostics_qt._open_path", side_effect=OSError("실행 실패")),
        ):
            self.assertFalse(diagnostics_qt.open_folder(tmp))

    def test_open_path_uses_startfile_on_windows(self) -> None:
        """Windows에서는 os.startfile로 폴더를 여는지 검증"""
        with (
            patch.object(diagnostics_qt.os, "startfile", create=True) as startfile,
        ):
            diagnostics_qt._open_path("logs")
        self.assertEqual(startfile.call_args[0][0], "logs")

    def test_open_crash_report_folder_falls_back_to_diagnostics_dir(self) -> None:
        """사고 폴더를 열지 못하면 진단 폴더로 대체하는지 검증"""
        with patch("src.gui.diagnostics_qt.open_folder", side_effect=[False, True]) as opened:
            self.assertTrue(diagnostics_qt.open_crash_report_folder("없는 폴더"))
        self.assertEqual(opened.call_count, 2)

    def test_diagnostics_folder_falls_back_before_start(self) -> None:
        """진단을 시작하지 않았으면 데이터 폴더 기준 기본 경로를 돌려주는지 검증"""
        diagnostics_qt.stop_gui_diagnostics()
        self.assertTrue(diagnostics_qt.diagnostics_folder().endswith("logs"))


if __name__ == "__main__":
    unittest.main()
