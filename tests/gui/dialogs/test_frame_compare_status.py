"""화질 비교 로딩 표시 검증: 프레임이 있을 때 알약 배지만, 없을 때 오버레이, 오류 표시"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QWidget

from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs.frame_compare_status import CompareStatusIndicator
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _image() -> QImage:
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("#FF0000"))
    return image


class StatusIndicatorTests(GuiTestCase):
    """프레임 유무에 따라 알약 배지와 전체 오버레이를 나눠 쓰는지"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.stage = layout.build_stage(self.parent, lambda _scale: None, lambda _side: None)
        self.addCleanup(self.stage.canvas.deleteLater)
        self.indicator = CompareStatusIndicator(self.stage)
        self.addCleanup(self.indicator.deleteLater)

    def test_empty_canvas_shows_the_loading_overlay(self) -> None:
        """프레임이 없으면 캔버스 전체 오버레이로 안내하는지 검증"""
        self.indicator.show_loading()
        self.assertFalse(self.stage.overlay.isHidden())
        self.assertEqual(self.stage.title.text(), layout.LOADING_TEXT)
        self.assertEqual(self.stage.body.text(), "")
        self.assertTrue(self.stage.pill.isHidden())

    def test_existing_frame_shows_only_the_pill_after_the_delay(self) -> None:
        """프레임이 있으면 기다렸다가 알약 배지만 띄워 화면을 가리지 않는지 검증"""
        self.stage.canvas.set_images(_image(), _image())
        self.indicator.show_loading()
        self.assertTrue(self.stage.overlay.isHidden())
        self.assertTrue(self.stage.pill.isHidden())
        self.assertEqual(self.stage.pill.text(), layout.LOADING_TEXT)
        self.indicator.reveal()
        self.assertFalse(self.stage.pill.isHidden())
        self.assertTrue(self.stage.overlay.isHidden())

    def test_hide_clears_both_indicators(self) -> None:
        """숨김은 알약과 오버레이를 모두 감추는지 검증"""
        self.indicator.show_loading()
        self.indicator.hide()
        self.assertTrue(self.stage.overlay.isHidden())
        self.assertTrue(self.stage.pill.isHidden())

    def test_error_uses_the_dark_overlay_with_the_reason(self) -> None:
        """오류는 어두운 전체 오버레이에 사유를 쓰고 알약을 숨기는지 검증"""
        self.stage.canvas.set_images(_image(), _image())
        self.indicator.reveal()
        self.indicator.show_error("파일이 없습니다.")
        self.assertFalse(self.stage.overlay.isHidden())
        self.assertEqual(self.stage.title.text(), layout.ERROR_TITLE)
        self.assertEqual(self.stage.body.text(), "파일이 없습니다.")
        self.assertTrue(self.stage.pill.isHidden())

    def test_message_without_body_clears_the_previous_body(self) -> None:
        """본문 없는 안내가 이전 오류 본문을 남기지 않는지 검증"""
        self.indicator.show_error("이전 사유")
        self.indicator.show_loading()
        self.assertEqual(self.stage.body.text(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
