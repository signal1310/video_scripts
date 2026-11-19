"""재인코딩 메뉴의 항목 구성, 시작 항목 활성 상태와 사유 표시, 콜백 연결을 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow

from src.core.encode.encode_start_state import ENCODE_RUNNING_TEXT, StartState
from src.gui.menus.menu_base import MenuContext
from src.gui.menus.menu_encode import ENCODE_MENU_TITLE, START_DESCRIPTION, apply_start_state, build_encode_menu
from tests._gui_helpers import make_menu_callbacks, menu_bar_of

_APP = QApplication.instance() or QApplication([])


class EncodeMenuTests(unittest.TestCase):
    """재인코딩 메뉴 조립과 시작 항목 상태 반영을 검증"""

    def setUp(self) -> None:
        self.window = QMainWindow()
        self.addCleanup(self.window.deleteLater)
        self.started: list[str] = []
        self.callbacks = make_menu_callbacks()
        self.callbacks.on_start_encode = lambda: self.started.append("start")
        self.callbacks.on_open_encode_settings = lambda: self.started.append("settings")
        self.ctx = MenuContext(self.window)
        self.menu = build_encode_menu(self.ctx, menu_bar_of(self.window), self.callbacks)

    def test_menu_title_and_items_are_in_order(self) -> None:
        """재인코딩 메뉴가 메뉴 막대에 있고 시작, 설정 항목을 이 순서로 가지는지 검증"""
        titles = [action.text() for action in menu_bar_of(self.window).actions()]
        self.assertIn(ENCODE_MENU_TITLE, titles)
        self.assertEqual([a.text() for a in self.menu.menu.actions()], ["재인코딩 시작...", "재인코딩 설정..."])

    def test_tooltips_are_visible_for_disabled_reasons(self) -> None:
        """비활성 사유를 툴팁으로 보이도록 메뉴의 툴팁 표시가 켜져 있는지 검증"""
        self.assertTrue(self.menu.menu.toolTipsVisible())

    def test_items_are_registered_under_their_shortcut_ids(self) -> None:
        """두 항목이 단축키 명령 ID로 등록되어 단축키 컨트롤러가 찾을 수 있는지 검증"""
        self.assertIs(self.ctx.actions["encode.start"], self.menu.act_start)
        self.assertIs(self.ctx.actions["encode.open_settings"], self.menu.act_settings)

    def test_disabled_state_shows_the_reason_in_tooltip_and_status_tip(self) -> None:
        """비활성 상태는 사유를 툴팁과 상태 표시줄 문구로 같이 보이는지 검증"""
        apply_start_state(self.menu.act_start, StartState(enabled=False, reason=ENCODE_RUNNING_TEXT, pending=False))
        self.assertFalse(self.menu.act_start.isEnabled())
        self.assertEqual(self.menu.act_start.toolTip(), ENCODE_RUNNING_TEXT)
        self.assertEqual(self.menu.act_start.statusTip(), ENCODE_RUNNING_TEXT)

    def test_enabled_state_shows_the_description(self) -> None:
        """활성 상태는 기능 설명을 툴팁과 상태 표시줄 문구로 보이는지 검증"""
        apply_start_state(self.menu.act_start, StartState(enabled=True, reason="", pending=False))
        self.assertTrue(self.menu.act_start.isEnabled())
        self.assertEqual(self.menu.act_start.statusTip(), START_DESCRIPTION)

    def test_triggering_the_items_calls_their_callbacks(self) -> None:
        """두 항목을 실행하면 각 콜백이 호출되는지 검증"""
        self.menu.act_start.trigger()
        self.menu.act_settings.trigger()
        self.assertEqual(self.started, ["start", "settings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
