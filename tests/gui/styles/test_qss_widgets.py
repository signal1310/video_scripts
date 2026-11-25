"""범용 위젯 및 메인 창 직속 위젯 QSS 조각 생성 검증 테스트"""

import re
import unittest

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.styles.qss_widgets import build_widgets_qss

_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


def _hex_colors(text: str) -> list[str]:
    """문자열에 든 16진 색 표기 목록"""
    return _HEX_COLOR.findall(text)


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
        selector = "QPushButton#CompactTableButton:checked"
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            qss = build_widgets_qss(palette)
            self.assertIn(selector, qss)
            start = qss.index("{", qss.index(selector)) + 1
            rule = qss[start : qss.index("}", start)]
            self.assertIn(f"background-color: {palette.bg_selected};", rule)
            self.assertIn(f"border-color: {palette.action_primary_border};", rule)
            # - 규칙에 쓰인 색은 팔레트 토큰 값뿐이어야 함
            tokens = {palette.bg_selected, palette.action_primary_border}
            self.assertEqual({value for value in _hex_colors(rule)}, tokens)
