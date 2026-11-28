"""범용 위젯 및 메인 창 직속 위젯 QSS 조각 생성 검증 테스트"""

import re
import unittest

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.styles.qss_widgets import build_widgets_qss

_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


def _hex_colors(text: str) -> list[str]:
    """문자열에 든 16진 색 표기 목록"""
    return _HEX_COLOR.findall(text)


def _rule_body(qss: str, selector: str) -> str:
    """선택자에 이어지는 첫 규칙 본문. 같은 이름의 더 긴 선택자가 있으면 그 앞에서 끊음"""
    start = qss.index(selector + " {") + len(selector)
    return qss[start : qss.index("}", start)]


class TestQssWidgets(unittest.TestCase):
    """양쪽 팔레트에서 위젯 전용 QSS와 대표 선택자가 생성되는지 검증"""

    def test_build_widgets_qss_for_both_palettes(self) -> None:
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            qss = build_widgets_qss(palette)
            for selector in (
                "QFrame#HeaderBar",
                "QFrame#DropOverlay",
                "QPushButton#SplitMainButton",
                "QPushButton#ClearColumnFiltersButton",
                'QLineEdit[invalid="true"]',
            ):
                self.assertIn(selector, qss)

    def test_compact_button_checked_rule_uses_palette_tokens(self) -> None:
        """좁게 보기 버튼의 눌림 규칙이 있고 색이 팔레트 토큰 값인지 검증"""
        selector = "QFrame#FilterBar QPushButton:checked"
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            qss = build_widgets_qss(palette)
            self.assertIn(selector, qss)
            rule = _rule_body(qss, selector)
            self.assertIn(f"background-color: {palette.bg_selected};", rule)
            self.assertIn(f"border: 1px solid {palette.action_primary_border};", rule)
            # - 규칙에 쓰인 색은 팔레트 토큰 값뿐이어야 함
            tokens = {palette.bg_selected, palette.action_primary_border}
            self.assertEqual({value for value in _hex_colors(rule)}, tokens)

    def test_filter_bar_buttons_are_flat_at_rest_hover_and_checked(self) -> None:
        """필터 바 버튼이 쉼, 호버, 켜짐, 누름 상태에서 팔레트 토큰만 쓰는 평면 배색인지 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            qss = build_widgets_qss(palette)
            rest = _rule_body(qss, "QFrame#FilterBar QPushButton")
            self.assertIn("background-color: transparent;", rest)
            self.assertIn("border: 1px solid transparent;", rest)
            self.assertIn("border-radius: 7px;", rest)

            hover = _rule_body(qss, "QFrame#FilterBar QPushButton:hover")
            self.assertIn(f"background-color: {palette.bg_hover};", hover)
            self.assertEqual(_hex_colors(hover), [palette.bg_hover])

            checked = _rule_body(qss, "QFrame#FilterBar QPushButton:checked")
            self.assertIn(f"background-color: {palette.bg_selected};", checked)
            self.assertIn(f"border: 1px solid {palette.action_primary_border};", checked)

            pressed = _rule_body(qss, "QFrame#FilterBar QPushButton:pressed")
            self.assertIn(f"background-color: {palette.bg_selected};", pressed)
            self.assertEqual(_hex_colors(pressed), [palette.bg_selected])

            # - 칩은 필터 상태 표시이므로 평면 규칙보다 구체적인 선택자로 기존 칩 배색을 유지
            for chip in (
                "ClearColumnFiltersButton",
                "ClearAllFiltersButton",
                "ReviewFilterChip",
                "VaryFilterChip",
            ):
                self.assertIn(f"QFrame#FilterBar QPushButton#{chip} ", qss)
            review = _rule_body(qss, "QFrame#FilterBar QPushButton#ReviewFilterChip")
            self.assertIn(f"background-color: {palette.badge_pending[0]};", review)
            self.assertIn(f"border: 1px solid {palette.badge_pending[1]};", review)

            vary = _rule_body(qss, "QFrame#FilterBar QPushButton#VaryFilterChip")
            self.assertIn(f"background-color: {palette.badge_vary[0]};", vary)
            self.assertIn(f"color: {palette.badge_vary[1]};", vary)
            self.assertIn(f"border: 1px solid {palette.badge_vary[1]};", vary)
            vary_checked = _rule_body(qss, "QFrame#FilterBar QPushButton#VaryFilterChip:checked")
            self.assertIn(f"background-color: {palette.badge_vary[0]};", vary_checked)
            self.assertIn(f"border: 2px solid {palette.badge_vary[2]};", vary_checked)
