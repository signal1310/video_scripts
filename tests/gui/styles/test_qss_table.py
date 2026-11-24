"""테이블 영역 QSS 조각 생성 검증 테스트"""

import unittest

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE, ThemePalette
from src.gui.styles.qss_table import build_table_qss


class TestQssTable(unittest.TestCase):
    """양쪽 팔레트에서 테이블 전용 QSS와 대표 선택자가 생성되는지 검증"""

    def test_build_table_qss_for_both_palettes(self) -> None:
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            qss = build_table_qss(palette)
            for selector in (
                "QTableView",
                "QHeaderView::section",
                "QWidget#TableEmptyOverlay",
                "QFrame#ColumnFilterPopover",
                "QListWidget#ColumnFilterValueList",
            ):
                self.assertIn(selector, qss)

    def test_item_rule_keeps_row_background_visible(self) -> None:
        """항목 규칙이 border를 지정하지 않아야 BackgroundRole 행 배경이 QSS에 덮이지 않음

        - Qt는 QTableView::item에 border가 있으면 항목 배경을 QSS가 직접 그려
          모델의 BackgroundRole을 무시함
        """
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette):
                qss = build_table_qss(palette)
                item_rule = qss.split("QTableView::item {", 1)[1].split("}", 1)[0]
                self.assertNotIn("border", item_rule)
                self.assertIn("padding: 4px 8px;", item_rule)

    def test_selection_and_hover_colors_come_from_the_palette(self) -> None:
        """선택, 호버 색이 팔레트 토큰을 그대로 쓰는지 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            qss = build_table_qss(palette)
            self.assertIn(f"selection-background-color: {palette.bg_selected};", qss)
            self.assertIn(f"background-color: {palette.bg_hover};", qss)


class TestRowTintTokens(unittest.TestCase):
    """D안 재인코딩 진행 행 강조 토큰이 두 테마에 모두 있는지 검증"""

    def test_both_themes_define_running_and_paused_tokens(self) -> None:
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette):
                self.assertIsInstance(palette, ThemePalette)
                for token in (
                    palette.badge_encode_running,
                    palette.row_running_bg,
                    palette.row_running_accent,
                    palette.row_paused_bg,
                    palette.row_paused_accent,
                ):
                    self.assertTrue(token)

    def test_row_tints_are_distinct_from_selection_and_from_each_other(self) -> None:
        """진행 중, 일시중지, 선택 색이 서로 겹치지 않음을 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette):
                tones = {
                    palette.row_running_bg.lower(),
                    palette.row_paused_bg.lower(),
                    palette.bg_selected.lower(),
                }
                self.assertEqual(len(tones), 3)
                self.assertNotEqual(
                    palette.row_running_accent.lower(), palette.row_paused_accent.lower()
                )

    def test_running_badge_border_matches_the_running_accent(self) -> None:
        """진행 중 배지 테두리가 행 왼쪽 띠 색과 같은지 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette):
                self.assertEqual(
                    palette.badge_encode_running[2].lower(),
                    palette.row_running_accent.lower(),
                )
