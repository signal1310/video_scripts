"""내장 미리 보기 영역 QSS 조각 생성 검증 테스트"""

import unittest

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.styles.qss_preview import build_preview_qss


class TestQssPreview(unittest.TestCase):
    """양쪽 팔레트에서 미리 보기 전용 QSS와 대표 선택자가 생성되는지 검증"""

    def test_build_preview_qss_for_both_palettes(self) -> None:
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            qss = build_preview_qss(palette)
            for selector in (
                "QWidget#PreviewControlBar",
                "QFrame#PreviewInfoOverlayFrame",
                "QSlider#PreviewPositionSlider::groove:horizontal",
                "QListWidget#PreviewOrderList",
            ):
                self.assertIn(selector, qss)
