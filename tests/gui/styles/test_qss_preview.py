"""내장 미리 보기 영역 QSS 조각 생성 검증 테스트"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QToolButton

from src.gui.preview.video_preview_controls import PlaybackControls
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.styles.qss_preview import build_preview_qss
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


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
                "QToolButton#PreviewCropButton",
                "QToolButton#PreviewCropButton:hover",
                "QToolButton#PreviewCropButton:pressed",
            ):
                self.assertIn(selector, qss)

    def test_crop_button_rule_has_no_checked_background(self) -> None:
        """여백 미리 보기 버튼이 켜진 상태 전용 배경 규칙을 두지 않는지 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            qss = build_preview_qss(palette)
            base_rule = qss.split("QToolButton#PreviewSettingsButton, QToolButton#PreviewRateButton,", 1)[
                1
            ].split("}", 1)[0]
            self.assertIn("QToolButton#PreviewCropButton", base_rule)
            self.assertIn("background-color: transparent;", base_rule)
            hover_rule = qss.split("QToolButton#PreviewSettingsButton:hover", 1)[1].split("}", 1)[0]
            self.assertIn("QToolButton#PreviewCropButton:hover", hover_rule)
            self.assertIn("QToolButton#PreviewCropButton:pressed", hover_rule)
            self.assertIn(f"background-color: {palette.media_track};", hover_rule)
            self.assertNotIn("QToolButton#PreviewCropButton:checked", qss)
            self.assertNotIn("QToolButton#PreviewCropButton:on", qss)


class CropButtonPaintTests(GuiTestCase):
    """여백 미리 보기 버튼이 네이티브 회색 바탕 없이 투명하게 그려지는지 검증"""

    def _button(self) -> QToolButton:
        controls = PlaybackControls()
        self.addCleanup(controls.deleteLater)
        controls.setStyleSheet(build_preview_qss(LIGHT_PALETTE))
        controls.set_crop_available(True)
        controls.show()
        _APP.processEvents()
        return controls.crop_button

    def test_rendered_background_alpha_is_zero_off_and_on(self) -> None:
        """켜고 끈 양쪽 상태에서 버튼 모서리 픽셀이 완전히 투명한지 검증"""
        button = self._button()
        for checked in (False, True):
            button.setChecked(checked)
            _APP.processEvents()
            image = button.grab().toImage()
            for point in ((1, 1), (image.width() - 2, 1), (1, image.height() - 2)):
                self.assertEqual(
                    image.pixelColor(*point).alpha(),
                    0,
                    f"checked={checked} 지점 {point}에 배경이 그려짐",
                )

    def test_selector_is_what_removes_the_native_background(self) -> None:
        """선택자를 빼면 네이티브 바탕이 다시 그려져, 위 투명 검사가 회귀를 잡는지 확인"""
        controls = PlaybackControls()
        self.addCleanup(controls.deleteLater)
        controls.set_crop_available(True)
        controls.show()
        without_crop = build_preview_qss(LIGHT_PALETTE).replace(
            "QToolButton#PreviewCropButton, ", ""
        ).replace("QToolButton#PreviewCropButton:hover, QToolButton#PreviewCropButton:pressed,\n", "")
        controls.setStyleSheet(without_crop)
        _APP.processEvents()
        self.assertNotEqual(controls.crop_button.grab().toImage().pixelColor(1, 1).alpha(), 0)

