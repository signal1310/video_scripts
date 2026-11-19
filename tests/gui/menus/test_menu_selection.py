"""편집 메뉴 끝의 선택 영상 항목이 옮긴 세 항목을 빼고 남은 항목만 가지는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow

from src.gui.menus.menu_base import MenuContext
from src.gui.menus.menu_edit import build_edit_menu
from src.gui.menus.menu_selection import build_selection_actions
from tests._gui_helpers import make_menu_callbacks, menu_bar_of

_APP = QApplication.instance() or QApplication([])


class SelectionActionsTests(unittest.TestCase):
    """편집 메뉴 끝 그룹의 항목 구성과 콜백 연결을 검증"""

    def setUp(self) -> None:
        self.window = QMainWindow()
        self.addCleanup(self.window.deleteLater)
        self.callbacks = make_menu_callbacks()
        self.fired: list[str] = []
        self.callbacks.on_copy_selected_names = lambda: self.fired.append("copy")
        self.ctx = MenuContext(self.window)
        edit = build_edit_menu(self.ctx, menu_bar_of(self.window), self.callbacks)
        self.actions = build_selection_actions(self.ctx, edit.menu, self.callbacks, lambda: None)
        self.edit_texts = [a.text() for a in edit.menu.actions() if not a.isSeparator()]

    def test_moved_items_are_not_left_in_the_edit_menu(self) -> None:
        """재생, 미리 보기, 위치 열기가 편집 메뉴에 남지 않았는지 검증"""
        for text in ("선택 영상 재생", "미리 보기", "탐색기에서 위치 열기"):
            with self.subTest(text=text):
                self.assertNotIn(text, self.edit_texts)

    def test_remaining_items_stay_at_the_end_of_the_edit_menu(self) -> None:
        """미리 보기 설정, 복사, 예외 규칙 항목이 편집 메뉴 끝에 남는지 검증"""
        for text in ("미리 보기 설정...", "선택 파일명 복사", "선택 항목 예외 규칙에 추가", "선택 항목 예외 규칙에서 제거"):
            with self.subTest(text=text):
                self.assertIn(text, self.edit_texts)

    def test_copy_action_calls_its_callback(self) -> None:
        """파일명 복사 항목이 콜백을 호출하는지 검증"""
        self.actions.act_copy_names.trigger()
        self.assertEqual(self.fired, ["copy"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
