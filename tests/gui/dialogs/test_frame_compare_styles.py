"""화질 비교 스타일 시트 검증: 다크 시네마 토큰, 줌 입력 내부 규칙, 공용 규칙 미포함"""

import unittest

from src.gui.dialogs import frame_compare_styles as styles
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE


class StylesheetTests(unittest.TestCase):
    """팔레트 기반 비교 전용 스타일 시트"""

    def test_cinema_surfaces_use_media_tokens_in_both_themes(self) -> None:
        """테마와 무관하게 상단 바와 캔버스가 같은 영상 팔레트를 쓰는지 검증"""
        for palette in (LIGHT_PALETTE, DARK_PALETTE):
            sheet = styles.build_compare_qss(palette)
            self.assertIn(f"QWidget#CompareTopBar {{\n    background: {palette.media_bg};", sheet)
            self.assertIn(f"QWidget#CompareStageWrap {{ background: {palette.media_bg}; }}", sheet)
            self.assertIn(f"QLabel#CompareFileName {{ color: {palette.media_fg};", sheet)
            self.assertIn(f"QLabel#CompareMetaText {{ color: {palette.media_muted};", sheet)

    def test_top_bar_overlay_and_score_chip_rules(self) -> None:
        sheet = styles.build_compare_qss(LIGHT_PALETTE)
        self.assertIn('QWidget#CompareTopBar[overlay="true"]', sheet)
        self.assertIn(LIGHT_PALETTE.media_overlay_bg, sheet)
        self.assertIn("QLabel#CompareScoreChip {", sheet)
        self.assertIn("QToolButton#CompareCollapseButton", sheet)

    def test_output_row_buttons_share_the_dark_cinema_rule(self) -> None:
        """출력 드롭다운과 이 규격으로 지정 버튼이 비교 대상 버튼과 같은 규칙을 쓰는지 검증"""
        sheet = styles.build_compare_qss(DARK_PALETTE)
        output_rule = sheet.split("QToolButton#CompareOutputButton {", 1)[1].split("}", 1)[0]
        apply_rule = sheet.split("QToolButton#CompareApplyOutputButton {", 1)[1].split("}", 1)[0]
        for rule in (output_rule, apply_rule):
            self.assertIn(f"color: {DARK_PALETTE.media_fg};", rule)
            self.assertIn(f"background: {DARK_PALETTE.media_overlay_bg};", rule)
            self.assertIn(f"border: 1px solid {DARK_PALETTE.media_track};", rule)
        self.assertIn(
            "QToolButton#CompareOutputButton::menu-indicator { image: none; width: 0px; }", sheet
        )
        self.assertIn(
            f"QToolButton#CompareOutputButton:hover {{ background: {DARK_PALETTE.media_track}; }}",
            sheet,
        )
        self.assertIn(
            f"QToolButton#CompareOutputButton:disabled {{ color: {DARK_PALETTE.media_muted}; }}",
            sheet,
        )
        self.assertIn(
            f"QToolButton#CompareApplyOutputButton:hover {{ background: {DARK_PALETTE.media_track}; }}",
            sheet,
        )
        self.assertIn("QToolButton#CompareApplyOutputButton:disabled {", sheet)

    def test_zoom_input_resets_the_inner_line_edit_padding(self) -> None:
        """전역 QLineEdit 여백이 스핀박스 내부에 겹쳐 숫자가 잘리지 않도록 하는지 검증"""
        sheet = styles.build_compare_qss(LIGHT_PALETTE)
        self.assertIn("QSpinBox#CompareZoomInput QLineEdit {", sheet)
        rule = sheet.split("QSpinBox#CompareZoomInput QLineEdit {", 1)[1].split("}", 1)[0]
        self.assertIn("border: none;", rule)
        self.assertIn("padding: 0px 8px;", rule)
        self.assertIn("min-height: 0px;", rule)
        self.assertIn(f"color: {LIGHT_PALETTE.media_fg};", rule)
        self.assertIn("QSpinBox#CompareZoomInput QLineEdit:focus", sheet)

    def test_zoom_input_hides_the_spin_arrows_with_a_readable_height(self) -> None:
        """화살표 서브컨트롤을 QSS로 숨겨 내부 편집 영역과 높이를 확보하는지 검증"""
        sheet = styles.build_compare_qss(LIGHT_PALETTE)
        button_rule = sheet.split(
            "QSpinBox#CompareZoomInput::up-button, QSpinBox#CompareZoomInput::down-button {", 1
        )[1].split("}", 1)[0]
        self.assertIn("width: 0px;", button_rule)
        self.assertIn("border: none;", button_rule)
        arrow_rule = sheet.split(
            "QSpinBox#CompareZoomInput::up-arrow, QSpinBox#CompareZoomInput::down-arrow {", 1
        )[1].split("}", 1)[0]
        self.assertIn("image: none;", arrow_rule)
        self.assertIn("width: 0px;", arrow_rule)
        self.assertIn("height: 0px;", arrow_rule)
        spin_rule = sheet.split("QSpinBox#CompareZoomInput {", 1)[1].split("}", 1)[0]
        self.assertIn("min-height: 22px;", spin_rule)

    def test_top_bar_overlay_uses_the_denser_panel_token(self) -> None:
        """접힘 오버레이가 라벨용 반투명색이 아니라 패널용 88% 불투명 토큰을 쓰는지 검증"""
        for palette in (LIGHT_PALETTE, DARK_PALETTE):
            sheet = styles.build_compare_qss(palette)
            block = sheet.split('QWidget#CompareTopBar[overlay="true"] {', 1)[1].split("}", 1)[0]
            self.assertIn(f"background: {palette.media_panel_bg};", block)
            self.assertNotIn(f"background: {palette.media_overlay_bg};", block)
        self.assertEqual(DARK_PALETTE.media_panel_bg, "rgba(11, 11, 11, 224)")
        self.assertEqual(LIGHT_PALETTE.media_panel_bg, "rgba(11, 11, 11, 224)")

    def test_shared_preview_and_toast_rules_are_not_duplicated(self) -> None:
        """공용 미리 보기 QSS와 단축키 알림 규칙을 다시 넣지 않는지 검증"""
        sheet = styles.build_compare_qss(LIGHT_PALETTE)
        self.assertNotIn("QWidget#PreviewControlBar", sheet)
        self.assertNotIn("QSlider#PreviewPositionSlider", sheet)
        self.assertNotIn("PreviewToast", sheet)
        self.assertNotIn("CompareNoticeToast", sheet)
        self.assertNotIn("QMenu {", sheet)
        self.assertNotIn("QToolTip {", sheet)
        self.assertNotIn("CompareMarkChip", sheet)
        self.assertNotIn("CompareShortcuts", sheet)

    def test_overlay_and_pill_use_the_media_tokens(self) -> None:
        """오버레이와 알약 배지가 라이트 테마 흰색이 아닌 영상 팔레트 색을 쓰는지 검증"""
        sheet = styles.build_compare_qss(LIGHT_PALETTE)
        self.assertIn(f"QWidget#CompareOverlay {{ background: {LIGHT_PALETTE.media_bg};", sheet)
        self.assertIn(f"QLabel#CompareOverlayTitle {{ color: {LIGHT_PALETTE.media_fg};", sheet)
        self.assertIn(f"QLabel#CompareOverlayBody {{ color: {LIGHT_PALETTE.media_muted};", sheet)
        self.assertIn("QLabel#CompareLoadingPill {", sheet)
        self.assertIn(LIGHT_PALETTE.media_overlay_bg, sheet)
        self.assertNotIn(f"QWidget#CompareOverlay {{ background: {LIGHT_PALETTE.bg_base};", sheet)


if __name__ == "__main__":
    unittest.main(verbosity=2)
