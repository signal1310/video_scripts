"""재인코딩 진행 창 카드 탭이 탭 추가와 제거, 탭 막대 표시, 팔레트 꾸밈을 올바르게 다루는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from src.gui.dialogs.encode_progress_tabs import CardTabs
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE

_APP = QApplication.instance() or QApplication([])


class CardTabsTests(unittest.TestCase):
    """카드 탭은 카드마다 탭 하나를 두고, 카드가 없으면 탭 영역을 숨기는지 검증"""

    def setUp(self) -> None:
        self.tabs = CardTabs()
        self.card = QWidget()

    def tearDown(self) -> None:
        self.tabs.deleteLater()

    def test_tab_bar_starts_hidden_and_the_tab_area_hides_without_cards(self) -> None:
        """시작 때 탭 영역은 숨겨져 있고, 카드를 넣으면 보이고, 모두 빼면 다시 숨겨지는지 검증"""
        self.assertTrue(self.tabs.isHidden())
        self.tabs.add_card(self.card, 1, "a.mp4")
        self.assertFalse(self.tabs.isHidden())
        self.tabs.remove_card(self.card)
        self.assertEqual(self.tabs.count(), 0)
        self.assertTrue(self.tabs.isHidden())

    def test_added_card_is_titled_by_order_and_tooltip_names_the_full_file(self) -> None:
        """탭 제목은 순서와 파일 이름, 툴팁은 순서와 전체 파일 이름을 보여 주는지 검증"""
        self.tabs.add_card(self.card, 3, "a.mp4")
        self.assertEqual(self.tabs.tabText(0), "3. a.mp4")
        self.assertEqual(self.tabs.tabToolTip(0), "3. a.mp4")

    def test_removing_an_unknown_card_keeps_the_other_tabs(self) -> None:
        """추가되지 않은 카드를 빼도 남은 탭은 그대로인지 검증"""
        other = QWidget()
        self.tabs.add_card(self.card, 1, "a.mp4")
        self.tabs.remove_card(other)
        self.assertEqual(self.tabs.count(), 1)
        other.deleteLater()

    def test_tab_bar_visibility_follows_the_single_panel_setting(self) -> None:
        """탭 막대 표시 설정을 바꾸면 탭 막대가 그 값을 따르는지 검증"""
        tab_bar = self.tabs.tabBar()
        assert tab_bar is not None
        self.tabs.set_tab_bar_visible(False)
        self.assertTrue(tab_bar.isHidden())
        self.tabs.set_tab_bar_visible(True)
        self.assertFalse(tab_bar.isHidden())

    def test_palette_styles_selected_and_idle_tabs_with_its_tokens(self) -> None:
        """팔레트를 적용하면 선택 탭 밑줄과 흐린 글자 색이 해당 팔레트 토큰을 쓰는지 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette):
                self.tabs.apply_palette(palette)
                style = self.tabs.styleSheet()
                self.assertIn(f"border-bottom: 2px solid {palette.progress_fill}", style)
                self.assertIn(f"color: {palette.text_muted}", style)


if __name__ == "__main__":
    unittest.main(verbosity=2)
