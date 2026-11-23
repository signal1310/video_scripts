"""화질 비교 스타일 시트 검증: 다크 시네마 토큰, 줌 입력 내부 규칙, 메뉴와 툴팁 규칙"""

import unittest

from src.gui.dialogs import frame_compare_styles as styles
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE


class StylesheetTests(unittest.TestCase):
    """팔레트 기반 스타일 시트"""

    def test_cinema_surfaces_use_media_tokens_in_both_themes(self) -> None:
        """테마와 무관하게 상단 바, 하단 바, 캔버스가 같은 영상 팔레트를 쓰는지 검증"""
        for palette in (LIGHT_PALETTE, DARK_PALETTE):
            sheet = styles.build_stylesheet(palette)
            self.assertIn(f"QWidget#CompareTopBar {{\n    background: {palette.media_bg};", sheet)
            self.assertIn(f"QWidget#CompareTimeline {{\n    background: {palette.media_bg};", sheet)
            self.assertIn(f"QWidget#CompareStageWrap {{ background: {palette.media_bg}; }}", sheet)
            self.assertIn(f"QLabel#CompareFileName {{ color: {palette.media_fg};", sheet)
            self.assertIn(f"QLabel#CompareMetaText {{ color: {palette.media_muted};", sheet)

    def test_top_bar_overlay_and_score_chip_rules(self) -> None:
        sheet = styles.build_stylesheet(LIGHT_PALETTE)
        self.assertIn('QWidget#CompareTopBar[overlay="true"]', sheet)
        self.assertIn(LIGHT_PALETTE.media_overlay_bg, sheet)
        self.assertIn("QLabel#CompareScoreChip {", sheet)
        self.assertIn("QFrame#CompareBarDivider {", sheet)
        self.assertIn("QToolButton#CompareCollapseButton", sheet)
        self.assertIn("QToolButton#ShortcutGuideButton", sheet)

    def test_zoom_input_resets_the_inner_line_edit_padding(self) -> None:
        """전역 QLineEdit 여백이 스핀박스 내부에 겹쳐 숫자가 잘리지 않도록 하는지 검증"""
        sheet = styles.build_stylesheet(LIGHT_PALETTE)
        self.assertIn("QSpinBox#CompareZoomInput QLineEdit {", sheet)
        rule = sheet.split("QSpinBox#CompareZoomInput QLineEdit {", 1)[1].split("}", 1)[0]
        self.assertIn("border: none;", rule)
        self.assertIn("padding: 0px;", rule)
        self.assertIn("min-height: 0px;", rule)
        self.assertIn(f"color: {LIGHT_PALETTE.media_fg};", rule)
        self.assertIn("QSpinBox#CompareZoomInput QLineEdit:focus", sheet)

    def test_menus_tooltips_and_sliders_keep_readable_rules(self) -> None:
        sheet = styles.build_stylesheet(LIGHT_PALETTE)
        self.assertIn("QMenu {", sheet)
        self.assertIn("QMenu::item:selected", sheet)
        self.assertIn("QToolTip {", sheet)
        self.assertIn("QSlider#CompareTimelineSlider::groove:horizontal", sheet)
        self.assertIn("QSlider#CompareVolumeSlider::groove:horizontal", sheet)
        self.assertNotIn("CompareMarkChip", sheet)
        self.assertNotIn("CompareShortcuts", sheet)

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
