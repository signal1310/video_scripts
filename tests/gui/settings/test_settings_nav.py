"""설정 좌측 범주 내비게이션(자동 아이콘 레일, 배지, 접기) 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.gui.settings.settings_nav import (
    AUTO_RAIL_WIDTH,
    NAV_CATEGORIES,
    RESULTS_INDEX,
    RESULTS_LABEL,
    WIDTH_EXPANDED,
    WIDTH_RAIL,
    NavButton,
    SettingsSidebar,
)
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from tests._gui_settings_base import count_pixels_by_lightness

_APP = QApplication.instance() or QApplication([])


class TestSettingsNav(unittest.TestCase):
    """사이드바 폭 전환, 배지, 선택 상태, 테마 갱신 검증"""

    def setUp(self) -> None:
        self.sidebar = SettingsSidebar()
        self.sidebar.show()
        self.addCleanup(self.sidebar.close)

    def test_categories_cover_five_pages_in_order(self) -> None:
        """범주 항목이 다섯 개이고 요구한 순서와 이름을 쓰는지 검증"""
        self.assertEqual(
            [category.label for category in NAV_CATEGORIES],
            ["일반", "해상도 및 분석", "판별 기준", "재인코딩", "단축키"],
        )
        self.assertEqual([category.index for category in NAV_CATEGORIES], [0, 1, 2, 3, 4])
        self.assertEqual(RESULTS_LABEL, "전체 결과")
        self.assertFalse(self.sidebar.btn_results.isVisible())

    def test_manual_toggle_switches_between_184_and_52(self) -> None:
        """수동 접기 버튼이 사이드바 폭과 아이콘 전용 표시를 오가는지 검증"""
        self.assertEqual(WIDTH_EXPANDED, 184)
        self.assertEqual(WIDTH_RAIL, 52)
        self.assertFalse(self.sidebar.is_compact())
        self.sidebar.toggle_collapsed()
        self.assertTrue(self.sidebar.is_compact())
        self.assertEqual(self.sidebar.width(), WIDTH_RAIL)
        self.assertEqual(
            self.sidebar.button(0).toolButtonStyle().name, "ToolButtonIconOnly"
        )
        self.sidebar.toggle_collapsed()
        self.assertEqual(self.sidebar.width(), WIDTH_EXPANDED)

    def test_auto_compact_ignores_manual_toggle(self) -> None:
        """좁은 창 폭 자동 레일 상태에서는 수동 전환 버튼이 잠기는지 검증"""
        self.assertEqual(AUTO_RAIL_WIDTH, 620)
        self.sidebar.set_auto_compact(True)
        self.assertEqual(self.sidebar.width(), WIDTH_RAIL)
        self.assertFalse(self.sidebar.btn_toggle.isEnabled())
        self.assertIn("좁아", self.sidebar.btn_toggle.toolTip())
        self.sidebar.toggle_collapsed()
        self.assertEqual(self.sidebar.width(), WIDTH_RAIL)
        self.sidebar.set_auto_compact(False)
        self.assertEqual(self.sidebar.width(), WIDTH_EXPANDED)
        self.assertTrue(self.sidebar.btn_toggle.isEnabled())

    def test_badges_show_counts_and_hide_zero(self) -> None:
        """일치 개수 배지가 양수만 표시되고 전체 결과는 0건도 표시하는지 검증"""
        self.sidebar.set_badges({0: 3, 1: 0})
        self.assertEqual(self.sidebar.button(0).badge.text(), "3")
        self.assertFalse(self.sidebar.button(0).badge.isHidden())
        self.assertTrue(self.sidebar.button(1).badge.isHidden())

        self.sidebar.set_results_badge(0)
        self.assertFalse(self.sidebar.btn_results.badge.isHidden())
        self.assertEqual(self.sidebar.btn_results.badge.text(), "0")
        self.sidebar.set_badges({})
        self.assertTrue(self.sidebar.button(0).badge.isHidden())

    def test_category_visibility_follows_counts(self) -> None:
        """검색 중 일치 항목이 있는 범주만 남고 전체 결과는 따로 토글되는지 검증"""
        self.sidebar.set_visible_categories({2: 1})
        self.assertFalse(self.sidebar.button(2).isHidden())
        for page in (0, 1, 3, 4):
            self.assertTrue(self.sidebar.button(page).isHidden())
        self.sidebar.show_all_categories()
        for page in (0, 1, 2, 3, 4):
            self.assertFalse(self.sidebar.button(page).isHidden())
        self.assertTrue(self.sidebar.btn_results.isHidden())

        self.sidebar.set_results_visible(True)
        self.assertFalse(self.sidebar.btn_results.isHidden())
        self.sidebar.show_all_categories()
        self.assertFalse(self.sidebar.btn_results.isHidden())

    def test_selection_checks_only_the_current_page(self) -> None:
        """선택 범주만 눌린 상태로 표시되는지 검증"""
        self.sidebar.set_current(RESULTS_INDEX)
        checked = [page for page in range(5) if self.sidebar.button(page).isChecked()]
        self.assertEqual(checked, [])
        self.assertTrue(self.sidebar.btn_results.isChecked())

        self.sidebar.set_current(3)
        self.assertEqual(
            [page for page in range(5) if self.sidebar.button(page).isChecked()], [3]
        )
        self.assertFalse(self.sidebar.btn_results.isChecked())

    def test_category_click_emits_page_index(self) -> None:
        """범주 버튼 클릭이 페이지 인덱스를 신호로 보내는지 검증"""
        emitted: list[int] = []
        self.sidebar.category_selected.connect(emitted.append)
        self.sidebar.button(4).click()
        self.sidebar.btn_results.click()
        self.assertEqual(emitted, [4, RESULTS_INDEX])

    def test_theme_switch_repaints_nav_icons(self) -> None:
        """테마 팔레트를 바꾸면 범주 아이콘과 접기 아이콘이 다시 그려지는지 검증"""
        button = self.sidebar.button(1)
        self.sidebar.apply_theme(DARK_PALETTE)
        self.assertGreater(count_pixels_by_lightness(button.icon(), min_lightness=200), 0)
        self.sidebar.apply_theme(LIGHT_PALETTE)
        self.assertGreater(count_pixels_by_lightness(button.icon(), max_lightness=50), 0)
        self.sidebar.apply_theme(DARK_PALETTE)
        self.assertFalse(self.sidebar.btn_toggle.icon().isNull())

    def test_nav_button_keeps_accessible_name_and_badge_position(self) -> None:
        """범주 버튼이 접근성 이름, 툴팁을 유지하고 배지를 버튼 안에 두는지 검증"""
        button = NavButton("판별 기준", "sliders")
        self.addCleanup(button.deleteLater)
        button.resize(184, 32)
        button.set_badge(12)
        self.assertEqual(button.text(), "판별 기준")
        self.assertEqual(button.accessibleName(), "판별 기준")
        self.assertEqual(button.toolTip(), "판별 기준")
        self.assertLessEqual(button.badge.geometry().right(), button.width())
        button.set_compact(True)
        self.assertLessEqual(button.badge.geometry().right(), button.width())
        self.assertEqual(button.toolButtonStyle().name, "ToolButtonIconOnly")
        button.set_badge(None)
        self.assertTrue(button.badge.isHidden())


if __name__ == "__main__":
    unittest.main(verbosity=2)

