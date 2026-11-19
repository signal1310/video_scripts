"""범용 위젯 및 메인 창 직속 위젯 QSS 조각 생성 검증 테스트"""

import unittest

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.styles.qss_widgets import build_widgets_qss


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
