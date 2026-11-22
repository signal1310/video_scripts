"""화질 비교 창 스타일 시트 검증: 팔레트 토큰 사용, 메뉴와 툴팁 규칙, 재생 조작 막대 규칙

- 색상 하드코딩이 없고 라이트, 다크 팔레트가 그대로 반영되는지 확인
"""

import unittest

from src.gui.dialogs import frame_compare_styles as styles
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE


class StylesheetTests(unittest.TestCase):
    """팔레트 기반 스타일 시트"""

    def test_stylesheet_uses_palette_tokens(self) -> None:
        sheet = styles.build_stylesheet(LIGHT_PALETTE)
        self.assertIn(LIGHT_PALETTE.bg_card, sheet)
        self.assertIn("QWidget#CompareSegmented QToolButton:checked", sheet)
        self.assertNotIn("#121212", sheet)
        self.assertIn(DARK_PALETTE.media_bg, styles.build_stylesheet(DARK_PALETTE))

    def test_stylesheet_covers_menus_tooltips_and_stage_background(self) -> None:
        sheet = styles.build_stylesheet(LIGHT_PALETTE)
        self.assertIn(f"QWidget#CompareStageWrap {{ background: {LIGHT_PALETTE.media_bg};", sheet)
        self.assertIn("QMenu {", sheet)
        self.assertIn("QMenu::item:selected", sheet)
        self.assertIn("QMenu::item:disabled", sheet)
        self.assertIn("QToolTip {", sheet)
        self.assertIn("QToolButton#CompareTargetButton::menu-indicator { image: none; width: 0px; }", sheet)
        self.assertIn("QToolButton#CompareRateButton::menu-indicator { image: none; width: 0px; }", sheet)
        self.assertIn(f"border: 1px solid {LIGHT_PALETTE.action_primary_border}", sheet)
        self.assertNotIn("999px", sheet)
        self.assertNotIn("CompareMarksHint", sheet)

    def test_stylesheet_covers_timeline_and_volume_sliders(self) -> None:
        sheet = styles.build_stylesheet(LIGHT_PALETTE)
        self.assertIn("QSlider#CompareTimelineSlider::groove:horizontal", sheet)
        self.assertIn("QSlider#CompareVolumeSlider::groove:horizontal", sheet)
        self.assertIn(f"background: {LIGHT_PALETTE.action_primary_bg}", sheet)

    def test_overlay_and_pill_use_the_media_tokens(self) -> None:
        """오버레이와 알약 배지가 라이트 테마 흰색이 아닌 영상 팔레트 색을 쓰는지 검증"""
        sheet = styles.build_stylesheet(LIGHT_PALETTE)
        self.assertIn(f"QWidget#CompareOverlay {{ background: {LIGHT_PALETTE.media_bg};", sheet)
        self.assertIn(f"QLabel#CompareOverlayTitle {{ color: {LIGHT_PALETTE.media_fg};", sheet)
        self.assertIn(f"QLabel#CompareOverlayBody {{ color: {LIGHT_PALETTE.media_muted};", sheet)
        self.assertIn("QLabel#CompareLoadingPill {", sheet)
        self.assertIn(LIGHT_PALETTE.media_overlay_bg, sheet)
        self.assertNotIn(f"QWidget#CompareOverlay {{ background: {LIGHT_PALETTE.bg_base};", sheet)


if __name__ == "__main__":
    unittest.main(verbosity=2)
