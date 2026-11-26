"""GUI 진단 어댑터: 런타임 시작/종료 재진입, Qt 메시지 전달, 폴더 열기 실패 처리 검증"""

import os
import tempfile
import time
import unittest
from collections.abc import Callable
from unittest.mock import patch

from PyQt6.QtCore import QtMsgType
from PyQt6.QtWidgets import QApplication

from src.core.diagnostics.watchdog import MainThreadWatchdog, StallDetector
from src.gui import diagnostics_qt

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class _NoApplication:
    """QApplication이 아직 없는 시점을 흉내 내는 대역"""

    @staticmethod
    def instance() -> None:
        """항상 None을 돌려줌"""
        return None


_NO_APPLICATION = _NoApplication()


class _SteppedClock:
    """테스트가 직접 앞으로 밀 수 있는 시계 대역(실제로 기다리지 않음)"""

    def __init__(self) -> None:
        self._value = 0.0

    def __call__(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        """시계를 지정한 초만큼 앞으로 밂"""
        self._value += seconds


class TestGuiDiagnosticsAdapter(unittest.TestCase):
    """시작한 런타임 재사용, 이벤트 기록, 종료 시 표식 닫기 검증"""

    def tearDown(self) -> None:
        diagnostics_qt.stop_gui_diagnostics()

    def test_start_registers_atexit_instead_of_about_to_quit(self) -> None:
        """시작이 atexit에 종료 처리를 걸고 aboutToQuit는 쓰지 않는지 검증"""
        with patch.object(diagnostics_qt.atexit, "register") as register:
            diagnostics_qt.start_gui_diagnostics()
        register.assert_called_once_with(diagnostics_qt.stop_gui_diagnostics)
        self.assertTrue(diagnostics_qt._atexit_registered)
        with patch.object(diagnostics_qt.atexit, "register") as register:
            diagnostics_qt.start_gui_diagnostics()
        register.assert_not_called()

    def test_start_keeps_native_log_enabled_until_atexit(self) -> None:
        """시작 직후에는 faulthandler가 켜진 채로 남아 있는지 검증"""
        runtime = diagnostics_qt.start_gui_diagnostics()
        assert runtime.native_session is not None
        self.assertTrue(runtime.native_session.is_open)
        diagnostics_qt.stop_gui_diagnostics()
        self.assertFalse(runtime.native_session.is_open)

    def test_stop_unregisters_atexit_hook(self) -> None:
        """종료가 atexit 등록을 해제하고 두 번째 호출에서 다시 해제하지 않는지 검증"""
        diagnostics_qt.start_gui_diagnostics()
        with patch.object(diagnostics_qt.atexit, "unregister") as unregister:
            diagnostics_qt.stop_gui_diagnostics()
            diagnostics_qt.stop_gui_diagnostics()
        unregister.assert_called_once_with(diagnostics_qt.stop_gui_diagnostics)
        self.assertFalse(diagnostics_qt._atexit_registered)

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

    def test_open_diagnostics_folder_opens_the_runtime_directory(self) -> None:
        """진단 폴더 열기가 현재 런타임 폴더를 열도록 넘기는지 검증"""
        with patch("src.gui.diagnostics_qt.open_folder") as opened:
            diagnostics_qt.open_diagnostics_folder()
        self.assertEqual(opened.call_args.args[0], diagnostics_qt.diagnostics_folder())

    def test_create_diagnostic_report_returns_the_archive_path(self) -> None:
        """진단 보고서 만들기가 진단 폴더 안 zip 경로를 돌려주는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "src.gui.diagnostics_qt.diagnostics_folder", return_value=tmp
            ) as folder:
                archive = diagnostics_qt.create_diagnostic_report()
            self.assertEqual(folder.call_count, 1)
            self.assertTrue(archive.startswith(tmp))
            self.assertTrue(archive.endswith(".zip"))
            self.assertTrue(os.path.isfile(archive))

    def test_create_diagnostic_report_returns_empty_on_failure(self) -> None:
        """보고서를 만들지 못하면 빈 문자열을 돌려주고 예외를 내지 않는지 검증"""
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.gui.diagnostics_qt.diagnostics_folder", return_value=tmp),
            patch(
                "src.core.diagnostics.dev_report.create_diagnostic_report",
                side_effect=OSError("기록 실패"),
            ),
        ):
            self.assertEqual(diagnostics_qt.create_diagnostic_report(), "")


class TestStallWatchdogWiring(unittest.TestCase):
    """개발자 모드의 메인 스레드 멈춤 감시 시작과 정리 검증"""

    def tearDown(self) -> None:
        diagnostics_qt.stop_stall_watchdog()

    def test_start_creates_timer_and_watchdog_once(self) -> None:
        """하트비트 타이머와 감시 스레드가 한 번만 만들어지고 주기가 100ms인지 검증"""
        watchdog = diagnostics_qt.start_stall_watchdog(heartbeat_ms=100)
        heartbeat = diagnostics_qt._heartbeat
        try:
            self.assertIsNotNone(heartbeat)
            assert heartbeat is not None
            self.assertTrue(heartbeat.isActive())
            self.assertEqual(heartbeat.interval(), 100)
            self.assertIs(diagnostics_qt.start_stall_watchdog(heartbeat_ms=100), watchdog)
            self.assertIs(diagnostics_qt._heartbeat, heartbeat)
        finally:
            diagnostics_qt.stop_stall_watchdog()
        self.assertIsNone(diagnostics_qt._watchdog)
        self.assertIsNone(diagnostics_qt._heartbeat)

    def test_beat_without_watchdog_is_silent(self) -> None:
        """감시자가 없으면 하트비트가 예외 없이 무시되는지 검증"""
        diagnostics_qt.stop_stall_watchdog()
        diagnostics_qt.beat_main_thread()

    def test_when_ready_starts_the_real_watchdog_in_developer_mode(self) -> None:
        """개발자 모드에서 앱이 준비된 뒤 실제 감시 스레드가 뜨는지 검증"""
        runtime = diagnostics_qt.start_gui_diagnostics(developer_mode=True)
        try:
            self.assertIsNone(diagnostics_qt._watchdog)
            watchdog = diagnostics_qt.start_stall_watchdog_when_ready()
            self.assertIsNotNone(watchdog)
            assert watchdog is not None
            self.assertTrue(watchdog.is_running)
            self.assertIsNotNone(runtime.dev_log)
            self.assertIsNotNone(diagnostics_qt._heartbeat)
            assert diagnostics_qt._heartbeat is not None
            self.assertTrue(diagnostics_qt._heartbeat.isActive())
        finally:
            diagnostics_qt.stop_gui_diagnostics()
        self.assertIsNone(diagnostics_qt._watchdog)
        self.assertIsNone(diagnostics_qt._heartbeat)

    def test_stop_clears_the_remembered_developer_mode(self) -> None:
        """종료 뒤에는 개발자 모드 기억이 남지 않아 준비 시작이 감시를 켜지 않는지 검증"""
        diagnostics_qt.start_gui_diagnostics(developer_mode=True)
        diagnostics_qt.stop_gui_diagnostics()
        self.assertFalse(diagnostics_qt._developer_mode)
        self.assertIsNone(diagnostics_qt.start_stall_watchdog_when_ready())

    def test_start_gui_diagnostics_leaves_the_watchdog_off_by_default(self) -> None:
        """개발자 모드가 아니면 감시 스레드를 띄우지 않는지 검증"""
        diagnostics_qt.start_gui_diagnostics()
        try:
            self.assertIsNone(diagnostics_qt._watchdog)
            self.assertIsNone(diagnostics_qt._heartbeat)
        finally:
            diagnostics_qt.stop_gui_diagnostics()

    def test_when_ready_starts_the_watchdog_only_in_developer_mode(self) -> None:
        """준비 뒤 시작은 개발자 모드에서만 감시를 켜는지 검증"""
        diagnostics_qt.start_gui_diagnostics()
        try:
            self.assertIsNone(diagnostics_qt.start_stall_watchdog_when_ready())
            self.assertIsNone(diagnostics_qt._watchdog)
        finally:
            diagnostics_qt.stop_gui_diagnostics()

    def test_when_ready_waits_for_a_running_application(self) -> None:
        """QApplication이 없는 시점에는 감시를 켜지 않는지 검증"""
        diagnostics_qt.start_gui_diagnostics(developer_mode=True)
        try:
            with patch.object(diagnostics_qt, "QCoreApplication", _NO_APPLICATION):
                self.assertIsNone(diagnostics_qt.start_stall_watchdog_when_ready())
            self.assertIsNone(diagnostics_qt._watchdog)
            self.assertIsNone(diagnostics_qt._heartbeat)
        finally:
            diagnostics_qt.stop_gui_diagnostics()

    def test_heartbeat_timer_ticks_once_the_application_exists(self) -> None:
        """앱이 준비된 뒤 켠 하트비트 타이머가 실제로 감시기에 박자를 보내는지 검증"""
        diagnostics_qt.start_gui_diagnostics(developer_mode=True)
        try:
            with patch.object(diagnostics_qt, "MainThreadWatchdog") as watchdog_factory:
                watchdog = diagnostics_qt.start_stall_watchdog_when_ready(heartbeat_ms=1)
                self.assertIs(watchdog, watchdog_factory.return_value)
                watchdog_factory.return_value.start.assert_called_once_with()
                beats = watchdog_factory.return_value.beat
                self._pump_until(lambda: beats.call_count >= 2)
                self.assertGreaterEqual(beats.call_count, 2)
        finally:
            diagnostics_qt.stop_gui_diagnostics()

    def test_blocked_main_thread_reports_once_and_resets_after_a_beat(self) -> None:
        """감시 스레드가 하트비트가 끊긴 메인 스레드를 한 번 보고하고, 다시 울리면 되돌리는지 검증"""
        clock = _SteppedClock()
        reports: list[float] = []
        detector = StallDetector(
            threshold=0.5,
            clock=clock,
            stack_provider=lambda: "스택",
            sink=lambda elapsed, _stack: reports.append(elapsed),
        )
        watchdog = MainThreadWatchdog(detector, poll_interval=0.001)
        watchdog.start()
        try:
            watchdog.beat()
            clock.advance(0.6)
            self._wait_until(lambda: detector.is_reported)
            self.assertEqual(len(reports), 1)
            watchdog.beat()
            self.assertFalse(detector.is_reported)
            clock.advance(0.9)
            self._wait_until(lambda: len(reports) == 2)
            self.assertEqual(len(reports), 2)
        finally:
            watchdog.stop()

    @staticmethod
    def _wait_until(done: Callable[[], bool], timeout_sec: float = 2.0) -> None:
        """조건이 참이 될 때까지 기다림(감시 스레드가 스스로 확인을 돎)"""
        deadline = time.monotonic() + timeout_sec
        while not done() and time.monotonic() < deadline:
            time.sleep(0.005)

    @staticmethod
    def _pump_until(done: Callable[[], bool], timeout_sec: float = 2.0) -> None:
        """조건이 참이 될 때까지 이벤트 루프를 돌려 타이머를 울림"""
        deadline = time.monotonic() + timeout_sec
        while not done() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.005)


if __name__ == "__main__":
    unittest.main()
