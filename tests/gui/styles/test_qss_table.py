"""테이블 영역 QSS 조각 생성 검증 테스트"""

import unittest

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
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
