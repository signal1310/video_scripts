"""메뉴바 컨트롤러의 메뉴 순서, 재인코딩 시작 항목 상태 갱신 시점, 인코더 시험 중 주기 확인을 검증"""

import os
import time
import unittest
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow

from src.core.encode.encode_start_state import ENCODE_RUNNING_TEXT, ENCODER_CHECKING_TEXT, StartState
from src.gui.menus.menu_bar import MainMenuController
from tests._gui_helpers import make_menu_callbacks, menu_bar_of

_APP = QApplication.instance() or QApplication([])
_WAIT_SEC: float = 3.0


def _wait_until(predicate: Callable[[], bool], timeout_sec: float = _WAIT_SEC) -> bool:
    """이벤트를 처리하며 조건이 참이 될 때까지 기다리고, 시간 안에 참이면 참을 돌려줌"""
    end = time.monotonic() + timeout_sec
    while not predicate():
        if time.monotonic() > end:
            return False
        _APP.processEvents()
        time.sleep(0.01)
    return True


class MainMenuControllerEncodeTests(unittest.TestCase):
    """메뉴바 조립 순서와 재인코딩 시작 항목 상태 연동을 검증"""

    def setUp(self) -> None:
        self.window = QMainWindow()
        self.addCleanup(self.window.deleteLater)
        self.state = StartState(enabled=True, reason="", pending=False)
        self.state_calls = 0
        callbacks = make_menu_callbacks()

        def get_state() -> StartState:
            self.state_calls += 1
            return self.state

        callbacks.get_encode_state = get_state
        self.menu = MainMenuController(self.window, callbacks)

    def test_menu_bar_order_places_encode_between_analysis_and_help(self) -> None:
        """메뉴 막대 순서가 파일, 편집, 보기, 분석, 재인코딩, 도움말인지 검증"""
        titles = [action.text() for action in menu_bar_of(self.window).actions()]
        self.assertEqual(titles, ["파일(&F)", "편집(&E)", "보기(&V)", "분석(&A)", "재인코딩(&N)", "도움말(&H)"])

    def test_refresh_applies_the_disabled_reason_to_the_start_item(self) -> None:
        """비활성 상태를 계산하면 시작 항목이 비활성이고 사유를 툴팁으로 가지는지 검증"""
        self.state = StartState(enabled=False, reason=ENCODE_RUNNING_TEXT, pending=False)
        self.menu.refresh_encode_state()
        self.assertFalse(self.menu.act_start_encode.isEnabled())
        self.assertEqual(self.menu.act_start_encode.toolTip(), ENCODE_RUNNING_TEXT)

    def test_busy_change_refreshes_the_start_item(self) -> None:
        """분석 중 상태가 바뀌면 시작 항목 상태를 다시 계산하는지 검증"""
        before = self.state_calls
        self.menu.set_busy(True)
        self.assertGreater(self.state_calls, before)

    def test_opening_the_encode_menu_refreshes_the_start_item(self) -> None:
        """재인코딩 메뉴가 열릴 때 시작 항목 상태를 다시 계산하는지 검증"""
        before = self.state_calls
        encode_menu = menu_bar_of(self.window).actions()[4].menu()
        assert encode_menu is not None
        encode_menu.aboutToShow.emit()
        self.assertGreater(self.state_calls, before)

    def test_pending_trial_is_rechecked_until_it_finishes(self) -> None:
        """인코더 시험이 끝나지 않은 동안 주기 확인으로 상태를 다시 계산하고, 끝나면 활성으로 바뀌는지 검증"""
        self.state = StartState(enabled=False, reason=ENCODER_CHECKING_TEXT, pending=True)
        self.menu.refresh_encode_state()
        self.assertFalse(self.menu.act_start_encode.isEnabled())
        self.state = StartState(enabled=True, reason="", pending=False)
        self.assertTrue(_wait_until(lambda: self.menu.act_start_encode.isEnabled()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
