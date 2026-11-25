"""설정 좌측 범주 내비게이션(자동 아이콘 레일, 배지, 접기) 검증"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QSizePolicy

from src.gui.settings.settings_nav import (
    AUTO_RAIL_WIDTH,
    BADGE_HEIGHT,
    BADGE_MIN_WIDTH,
    NAV_CATEGORIES,
    NAV_ICON_GAP_PX,
    NAV_ICON_SIZE,
    NAV_TEXT_INSET_PX,
    RESULTS_INDEX,
    RESULTS_LABEL,
    WIDTH_EXPANDED,
    WIDTH_RAIL,
    NavButton,
    SettingsSidebar,
)
from src.gui.styles.icons import get_active_palette
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE, ThemePalette
from src.gui.styles.qss_dialogs import build_dialogs_qss
from tests._gui_settings_base import count_pixels_by_lightness

_APP = QApplication.instance() or QApplication([])


def _count_exact_color(image: QImage, color: str, rect: QRect | None = None) -> int:
    """지정 영역에서 RGB가 정확히 같은 픽셀 수를 셈"""
    target = QColor(color)
    area = rect if rect is not None else image.rect()
    count = 0
    for y in range(area.top(), area.bottom() + 1):
        for x in range(area.left(), area.right() + 1):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() <= 200:
                continue
            if (
                pixel.red() == target.red()
                and pixel.green() == target.green()
                and pixel.blue() == target.blue()
            ):
                count += 1
    return count


def _two_mode_icon(normal: str, disabled: str) -> QIcon:
    """일반, 비활성 모드 픽스맵을 서로 다른 단색으로 채운 아이콘"""
    icon = QIcon()
    for color, mode in ((normal, QIcon.Mode.Normal), (disabled, QIcon.Mode.Disabled)):
        pixmap = QPixmap(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))
        pixmap.fill(QColor(color))
        icon.addPixmap(pixmap, mode)
    return icon


def _opposite_palette(palette: ThemePalette) -> ThemePalette:
    """주어진 팔레트와 반대 밝기의 팔레트"""
    return LIGHT_PALETTE if palette.is_dark else DARK_PALETTE


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

    def test_manual_toggle_switches_between_158_and_52(self) -> None:
        """수동 접기 버튼이 사이드바 폭과 아이콘 전용 표시를 오가는지 검증"""
        self.assertEqual(WIDTH_EXPANDED, 158)
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
        self.assertFalse(button.grab().isNull())
        button.set_badge(None)
        self.assertTrue(button.badge.isHidden())

    def test_badge_set_before_show_lands_at_right_edge(self) -> None:
        """표시 전에 배지를 지정해도 0, 0이 아니라 오른쪽 끝에 놓이는지 검증"""
        button = NavButton("판별 기준", "sliders")
        self.addCleanup(button.deleteLater)
        button.resize(184, 32)
        self.assertTrue(button.badge.isHidden())
        button.set_badge(12)
        icon_right = NAV_TEXT_INSET_PX + NAV_ICON_SIZE
        self.assertFalse(button.badge.isVisible())
        self.assertGreater(button.badge.geometry().left(), icon_right)
        self.assertLessEqual(button.badge.geometry().right(), button.width())
        button.show()
        _APP.processEvents()
        self.assertGreater(button.badge.geometry().left(), icon_right)
        self.assertLessEqual(button.badge.geometry().right(), button.width())

    def test_clearing_badge_restores_full_label_width(self) -> None:
        """배지를 지우면 말줄임이 풀린 전체 이름 영역으로 다시 그리는지 검증"""
        button = NavButton("해상도 및 분석", "sliders")
        self.addCleanup(button.deleteLater)
        button.resize(160, 32)
        button.show()
        _APP.processEvents()
        button.set_badge(12)
        _APP.processEvents()
        squeezed = button.text_rect().width()
        elided = button.fontMetrics().elidedText(
            button.label_text, Qt.TextElideMode.ElideRight, squeezed
        )
        self.assertNotEqual(elided, button.label_text)
        with patch.object(NavButton, "update") as update_mock:
            button.set_badge(None)
        self.assertTrue(button.badge.isHidden())
        self.assertEqual(update_mock.call_count, 1)
        restored = button.text_rect().width()
        self.assertGreater(restored, squeezed)
        self.assertEqual(
            button.fontMetrics().elidedText(
                button.label_text, Qt.TextElideMode.ElideRight, restored
            ),
            button.label_text,
        )

    def test_compact_toggle_keeps_explicitly_applied_palette(self) -> None:
        """접기 전환이 명시 적용한 팔레트를 활성 팔레트로 덮어쓰지 않는지 검증"""
        active = get_active_palette()
        explicit = _opposite_palette(active)
        button = NavButton("판별 기준", "sliders")
        self.addCleanup(button.deleteLater)
        button.resize(WIDTH_EXPANDED, 32)
        button.apply_palette(explicit)
        self.assertIs(button._palette, explicit)

        button.set_compact(True)
        self.assertIs(button._palette, explicit)
        self.assertEqual(button.iconSize().width(), NAV_ICON_SIZE)
        self.assertIn(explicit.text_secondary, button.badge.styleSheet())
        self.assertNotIn(active.text_secondary, button.badge.styleSheet())

        button.set_compact(False)
        self.assertIs(button._palette, explicit)
        self.assertEqual(button.iconSize().width(), NAV_ICON_SIZE + NAV_ICON_GAP_PX)
        self.assertIn(explicit.text_secondary, button.badge.styleSheet())
        self.assertNotEqual(explicit.name, active.name)

    def test_disabled_nav_button_renders_muted_label_and_icon(self) -> None:
        """비활성 버튼이 흐린 이름색과 비활성 아이콘 모드로 그려지는지 검증"""
        rendered: dict[bool, QImage] = {}
        for enabled in (True, False):
            button = NavButton("판별 기준", "sliders")
            self.addCleanup(button.deleteLater)
            button.apply_palette(DARK_PALETTE)
            button.setIcon(_two_mode_icon("#00FF00", "#FF00FF"))
            button.resize(184, 32)
            button.show()
            button.setEnabled(enabled)
            _APP.processEvents()
            rendered[enabled] = button.grab().toImage()

        enabled_image = rendered[True]
        disabled_image = rendered[False]
        icon_rect = QRect(NAV_TEXT_INSET_PX, 0, NAV_ICON_SIZE, 32)
        self.assertGreater(_count_exact_color(enabled_image, "#00FF00", icon_rect), 0)
        self.assertEqual(_count_exact_color(enabled_image, "#FF00FF", icon_rect), 0)
        self.assertGreater(_count_exact_color(disabled_image, "#FF00FF", icon_rect), 0)
        self.assertEqual(_count_exact_color(disabled_image, "#00FF00", icon_rect), 0)

        label_rect = QRect(
            NAV_TEXT_INSET_PX + NAV_ICON_SIZE, 0, 184 - NAV_TEXT_INSET_PX - NAV_ICON_SIZE, 32
        )
        self.assertGreater(
            _count_exact_color(disabled_image, DARK_PALETTE.text_muted, label_rect), 0
        )
        self.assertEqual(
            _count_exact_color(enabled_image, DARK_PALETTE.text_muted, label_rect), 0
        )

    def test_all_nav_buttons_expand_to_same_full_width(self) -> None:
        """다섯 범주 버튼과 전체 결과 버튼이 같은 가로 확장 폭을 쓰는지 검증"""
        buttons = [self.sidebar.button(page) for page in range(5)] + [self.sidebar.btn_results]
        self.sidebar.set_results_visible(True)
        _APP.processEvents()
        widths: set[int] = set()
        for button in buttons:
            self.assertEqual(
                button.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Expanding
            )
            self.assertEqual(button.width(), self.sidebar.width() - 8)
            widths.add(button.width())
        self.assertEqual(len(widths), 1)

    def test_badge_never_overlaps_long_category_text(self) -> None:
        """184px, 13pt에서 긴 이름도 배지와 겹치지 않고 버튼 안에 머무는지 검증"""
        font = QFont()
        font.setPointSize(13)
        self.sidebar.setFont(font)
        self.sidebar.resize(WIDTH_EXPANDED, 320)
        self.sidebar.set_results_visible(True)
        _APP.processEvents()
        for index in (1, RESULTS_INDEX):
            button = self.sidebar.button(index)
            button.set_badge_value("12")
            _APP.processEvents()
            self.assertEqual(BADGE_HEIGHT, button.badge.height())
            self.assertGreaterEqual(button.badge.minimumWidth(), BADGE_MIN_WIDTH)
            self.assertLessEqual(button.badge.geometry().right(), button.width())
            self.assertGreaterEqual(
                button.text_rect().right(), 0
            )
            self.assertLess(button.text_rect().right(), button.badge.geometry().left())
            # 배지가 있는 상태의 직접 그리기도 실행해 아이콘, 이름 그리기 경로를 검증
            pixmap = button.grab()
            self.assertFalse(pixmap.isNull())

    def test_toggled_button_switches_badge_between_accent_and_neutral(self) -> None:
        """선택 여부가 배지 색조를 액센트, 중립으로 바꾸고 테마 양쪽에 적용되는지 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            button = NavButton("판별 기준", "sliders")
            self.addCleanup(button.deleteLater)
            button.resize(176, 34)
            button.set_badge_value("12")
            button.apply_palette(palette)
            self.assertIn(palette.bg_elevated, button.badge.styleSheet())
            self.assertIn(palette.text_secondary, button.badge.styleSheet())
            button.setChecked(True)
            self.assertIn(palette.accent_blue, button.badge.styleSheet())
            self.assertIn(palette.text_on_accent, button.badge.styleSheet())
            button.setChecked(False)
            self.assertIn(palette.bg_elevated, button.badge.styleSheet())

    def test_dialogs_qss_has_sidebar_divider_and_checked_border(self) -> None:
        """사이드바 카드 바탕, 우측 구분선, 머리글 구분선, 선택 테두리가 QSS에 들어가는지 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            qss = build_dialogs_qss(palette)
            self.assertIn("QWidget#SettingsSidebar", qss)
            self.assertIn(f"background-color: {palette.bg_card};", qss)
            self.assertIn(f"border-right: 1px solid {palette.border_subtle}", qss)
            self.assertIn(f"QFrame#SettingsHeaderDivider {{\n    background-color: {palette.border_subtle};", qss)
            self.assertIn(f"border: 1px solid {palette.action_primary_border}", qss)
            self.assertIn("border: 1px solid transparent", qss)
            self.assertIn("border-radius: 9px", qss)
            self.assertIn("border-radius: 6px", qss)
            self.assertIn(f"background-color: {palette.bg_selected};", qss)
            self.assertIn(f"background-color: {palette.bg_hover};", qss)
            self.assertNotIn(f"QWidget#SettingsSidebar {{\n    background-color: {palette.bg_base};", qss)


if __name__ == "__main__":
    unittest.main(verbosity=2)

