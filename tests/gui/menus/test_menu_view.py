"""보기 메뉴의 테마, 표시할 열, 열 프리셋, 열 복원 항목 조립 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow

from src.core.settings.shortcuts import RESET_COLUMNS_DESCRIPTION, RESET_COLUMNS_TEXT
from src.gui.menus.menu_base import MenuContext
from src.gui.menus.menu_view import build_view_menu
from src.gui.theme import ThemeManager
from tests._gui_helpers import make_menu_callbacks, menu_bar_of

_APP = QApplication.instance() or QApplication([])


class ViewMenuTests(unittest.TestCase):
    """보기 메뉴 조립과 항목 문구 검증"""

    def setUp(self) -> None:
        self.window = QMainWindow()
        self.addCleanup(self.window.deleteLater)
        self.selected: list[str] = []
        self.reset_columns: list[str] = []
        self.columns_refreshed: list[str] = []
        callbacks = make_menu_callbacks()
        callbacks.on_reset_columns = lambda: self.reset_columns.append("reset")
        callbacks.on_select_column_preset = self.selected.append
        callbacks.column_presets = {"default": "기본", "specs": "기술 사양"}
        self.view_menu = build_view_menu(
            MenuContext(self.window),
            menu_bar_of(self.window),
            callbacks,
            lambda: self.columns_refreshed.append("columns"),
        )

    def test_column_preset_submenu_lists_callbacks_presets(self) -> None:
        """프리셋 서브메뉴가 호출자가 준 이름과 순서로 항목을 만드는지 검증"""
        labels = [
            action.text()
            for action in self.view_menu.preset_menu.actions()
            if not action.isSeparator()
        ]
        self.assertEqual(labels, ["기본", "기술 사양", "이 프리셋을 기본 배치로 되돌리기"])
        self.view_menu.preset_actions["specs"].trigger()
        self.assertEqual(self.selected, ["specs"])
        self.assertTrue(self.view_menu.preset_actions["specs"].isCheckable())

    def test_reset_columns_item_uses_the_shared_text_and_description(self) -> None:
        """열 복원 항목이 공용 이름과 동작 설명을 툴팁, 상태 안내로 쓰는지 검증"""
        act = self.view_menu.act_reset_columns
        self.assertEqual(act.text(), RESET_COLUMNS_TEXT)
        self.assertEqual(act.toolTip(), RESET_COLUMNS_DESCRIPTION)
        self.assertEqual(act.statusTip(), RESET_COLUMNS_DESCRIPTION)
        act.trigger()
        self.assertEqual(self.reset_columns, ["reset"])

    def test_columns_menu_refreshes_when_opened(self) -> None:
        """표시할 열 서브메뉴가 열릴 때마다 호출자가 항목을 다시 채우는지 검증"""
        self.view_menu.columns_menu.aboutToShow.emit()
        self.assertEqual(self.columns_refreshed, ["columns"])

    def test_theme_actions_start_unchecked(self) -> None:
        """테마 항목은 현재 모드 동기화 전까지 체크되지 않은 라디오 항목으로 만들어짐"""
        actions = self.view_menu.theme_actions
        self.assertEqual(
            set(actions),
            {ThemeManager.MODE_SYSTEM, ThemeManager.MODE_DARK, ThemeManager.MODE_LIGHT},
        )
        self.assertTrue(all(act.isCheckable() for act in actions.values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
