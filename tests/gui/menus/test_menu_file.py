"""파일 메뉴에 옮겨 온 선택 영상 재생, 미리 보기, 위치 열기 항목의 위치와 콜백 연결을 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow

from src.gui.menus.menu_base import MenuContext
from src.gui.menus.menu_encode import DATA_CLEANUP_DESCRIPTION, DATA_CLEANUP_TEXT
from src.gui.menus.menu_file import build_file_menu
from tests._gui_helpers import make_menu_callbacks, menu_bar_of

_APP = QApplication.instance() or QApplication([])


class FileMenuSelectionItemsTests(unittest.TestCase):
    """선택 영상 조작 세 항목이 파일 메뉴에 있고 단축키 ID를 유지하는지 검증"""

    def setUp(self) -> None:
        self.window = QMainWindow()
        self.addCleanup(self.window.deleteLater)
        self.fired: list[str] = []
        callbacks = make_menu_callbacks()
        callbacks.on_play_selected = lambda: self.fired.append("play")
        callbacks.on_preview_selected = lambda: self.fired.append("preview")
        callbacks.on_reveal_selected = lambda: self.fired.append("reveal")
        self.ctx = MenuContext(self.window)
        self.menu = build_file_menu(self.ctx, menu_bar_of(self.window), callbacks)
        file_menu = menu_bar_of(self.window).actions()[0].menu()
        assert file_menu is not None
        self.file_menu_texts = [a.text() for a in file_menu.actions()]

    def test_three_items_are_in_the_file_menu(self) -> None:
        """재생, 미리 보기, 탐색기에서 위치 열기가 파일 메뉴에 있는지 검증"""
        for text in ("선택 영상 재생", "미리 보기", "탐색기에서 위치 열기"):
            with self.subTest(text=text):
                self.assertIn(text, self.file_menu_texts)

    def test_shortcut_ids_are_kept_for_the_moved_items(self) -> None:
        """옮긴 항목이 기존 단축키 명령 ID로 등록되는지 검증"""
        self.assertIs(self.ctx.actions["classify.play_selected"], self.menu.act_play_selected)
        self.assertIs(self.ctx.actions["classify.preview_selected"], self.menu.act_preview_selected)
        self.assertIs(self.ctx.actions["classify.reveal_selected"], self.menu.act_reveal_selected)

    def test_triggering_each_item_calls_its_callback(self) -> None:
        """세 항목을 실행하면 각자의 콜백이 호출되는지 검증"""
        self.menu.act_play_selected.trigger()
        self.menu.act_preview_selected.trigger()
        self.menu.act_reveal_selected.trigger()
        self.assertEqual(self.fired, ["play", "preview", "reveal"])

    def test_data_cleanup_item_shares_the_encode_menu_texts(self) -> None:
        """파일 메뉴의 데이터 정리 항목이 인코딩 메뉴와 같은 문구와 상태 안내를 쓰는지 검증"""
        self.assertEqual(self.menu.act_open_data_cleanup.text(), DATA_CLEANUP_TEXT)
        self.assertEqual(self.menu.act_open_data_cleanup.statusTip(), DATA_CLEANUP_DESCRIPTION)
        self.assertIn(DATA_CLEANUP_TEXT, self.file_menu_texts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
