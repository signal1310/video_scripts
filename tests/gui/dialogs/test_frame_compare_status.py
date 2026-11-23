"""화질 비교 로딩 표시 검증: 프레임이 있을 때 알약 배지만, 없을 때 오버레이, 오류, 단축키 알림"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QWidget

from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs import frame_compare_status as status
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

    def test_notice_is_centered_at_the_top_with_the_short_duration(self) -> None:
        """단축키 알림이 캔버스 위쪽 가운데에 1200ms로 뜨는지 검증"""
        self.indicator.show_notice("재생")
        toast = self.indicator._notice
        self.assertFalse(toast.isHidden())
        self.assertEqual(toast.text(), "재생")
        self.assertEqual(toast._timer.interval(), 1200)
        self.assertEqual(toast.y(), status.NOTICE_MARGIN_PX)
        self.assertEqual(toast.x(), (self.stage.canvas.width() - toast.width()) // 2)
        self.assertTrue(
            toast.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        )

    def test_notice_moves_below_the_collapsed_overlay_inset(self) -> None:
        """접힘 상단 오버레이가 열려 있으면 그 아래로 비켜 놓는지 검증"""
        self.stage.canvas.set_top_inset(60)
        self.indicator.show_notice("분할선 모드")
        self.assertEqual(self.indicator._notice.y(), 60 + status.NOTICE_MARGIN_PX)

    def test_notice_moves_below_the_visible_loading_pill(self) -> None:
        """로딩 알약이 보이면 알약 아래로 겹치지 않게 놓는지 검증"""
        self.stage.pill.show()
        self.indicator.show_notice("즉시 전환 모드")
        pill = self.stage.pill
        self.assertGreaterEqual(
            self.indicator._notice.y(), pill.y() + pill.height() + status.NOTICE_GAP_PX
        )

    def test_repeated_notices_replace_the_text_and_restart_the_timer(self) -> None:
        """연속 알림이 이전 문구를 덮어쓰고 표시 시간을 다시 세는지 검증"""
        self.indicator.show_notice("재생")
        self.indicator.show_notice("일시중지")
        self.assertEqual(self.indicator._notice.text(), "일시중지")
        self.assertTrue(self.indicator._notice._timer.isActive())

    def test_notice_repositions_when_the_canvas_resizes(self) -> None:
        """캔버스 크기가 바뀌면 알림의 가운데 위치를 다시 잡는지 검증"""
        self.indicator.show_notice("재생")
        self.stage.canvas.resize(400, 300)
        QApplication.sendEvent(self.stage.canvas, QEvent(QEvent.Type.Resize))
        toast = self.indicator._notice
        self.assertEqual(toast.x(), (400 - toast.width()) // 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
