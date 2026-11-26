"""GUI 진입점: 저장 설정에서 읽는 개발자 모드 기본값과 감시 정지 순서 검증"""

from __future__ import annotations

import unittest
from typing import ClassVar
from unittest.mock import patch

from src.core.settings.state_codec import AppState
from src.gui import configured_developer_mode, run_gui


class ConfiguredDeveloperModeTests(unittest.TestCase):
    """저장된 설정 파일의 개발자 모드 값을 그대로 읽는지 검증"""

    def test_returns_saved_value(self) -> None:
        """저장 설정이 켜져 있으면 켜진 값, 꺼져 있으면 꺼진 값을 돌려주는지 검증"""
        for saved in (True, False):
            with self.subTest(saved=saved):
                state = AppState()
                state.diagnostics.developer_mode = saved
                with patch("src.gui.load_app_state", return_value=state):
                    self.assertIs(configured_developer_mode(), saved)


class _FakeApplication:
    """이벤트 루프에 들어가지 않고 정해 둔 종료 코드만 돌려주는 QApplication 대역"""

    events: ClassVar[list[str]] = []

    def __init__(self, argv: list[str] | None = None) -> None:
        self.argv = argv

    def setFont(self, font: object) -> None:
        """글꼴 설정은 기록하지 않고 무시"""

    def exec(self) -> int:
        """이벤트 루프 대신 정해 둔 종료 코드를 돌려줌"""
        _FakeApplication.events.append("exec")
        return 3


class _CreatedApplication(_FakeApplication):
    """아직 앱이 없는 시점을 흉내 내어 run_gui가 앱을 직접 만들게 하는 대역"""

    @classmethod
    def instance(cls) -> None:
        """아직 만든 앱이 없음을 알림"""

    def __init__(self, argv: list[str] | None = None) -> None:
        super().__init__(argv)
        _FakeApplication.events.append("create")


class _RunningApplication(_FakeApplication):
    """이미 실행 중인 앱을 빌려 쓰는 경우를 흉내 내는 QApplication 대역"""

    @classmethod
    def instance(cls) -> _RunningApplication:
        """이미 있는 앱을 돌려줌"""
        return cls()


class RunGuiWatchdogTests(unittest.TestCase):
    """run_gui가 이벤트 루프가 끝난 직후 멈춤 감시를 정리하는지 검증"""

    def setUp(self) -> None:
        _FakeApplication.events = []

    def test_stops_the_watchdog_right_after_the_event_loop_returns(self) -> None:
        """app.exec() 직후 감시를 멈춰 앱이 사라진 뒤 타이머 정리가 남지 않는지 검증"""
        with (
            patch("src.gui.bootstrap_diagnostics"),
            patch("src.gui.QApplication", _CreatedApplication),
            patch("src.gui.make_ui_font"),
            patch("src.gui.ensure_default_config_files"),
            patch("src.gui.MainWindow"),
            patch("src.gui.start_stall_watchdog_when_ready"),
            patch("src.gui.stop_stall_watchdog", side_effect=lambda: _FakeApplication.events.append("stop")),
        ):
            exit_code = run_gui()
        self.assertEqual(exit_code, 3)
        self.assertEqual(_FakeApplication.events, ["create", "exec", "stop"])

    def test_borrowed_application_returns_without_an_event_loop(self) -> None:
        """이미 있는 앱을 빌려 쓰는 경우에는 이벤트 루프를 돌리지 않아 감시도 멈추지 않는지 검증"""
        with (
            patch("src.gui.bootstrap_diagnostics"),
            patch("src.gui.QApplication", _RunningApplication),
            patch("src.gui.make_ui_font"),
            patch("src.gui.ensure_default_config_files"),
            patch("src.gui.MainWindow"),
            patch("src.gui.start_stall_watchdog_when_ready"),
            patch("src.gui.stop_stall_watchdog") as stop,
        ):
            self.assertEqual(run_gui(), 0)
        stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
