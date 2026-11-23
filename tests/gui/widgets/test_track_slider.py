"""공용 트랙 슬라이더 검증: 트랙 클릭 위치 이동, 끌기, 핸들 클릭 위임, 빈 사건 무시"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QStyle

from src.gui.widgets.track_slider import TrackSlider
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


class TrackSliderTests(GuiTestCase):
    """트랙 누름과 끌기가 값과 요청 신호로 이어지는지 검증"""

    def setUp(self) -> None:
        self.slider = TrackSlider(Qt.Orientation.Horizontal)
        self.addCleanup(self.slider.deleteLater)
        self.slider.resize(200, 24)
        self.slider.setRange(0, 1000)
        self.slider.setValue(0)
        self.slider.show()
        _APP.processEvents()
        self.events: list[int] = []
        self.slider.track_value_requested.connect(self.events.append)

    def _mouse(self, kind: QEvent.Type, x: float) -> QMouseEvent:
        point = QPointF(x, self.slider.height() / 2)
        return QMouseEvent(
            kind,
            point,
            point,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

    def test_value_at_maps_track_coordinates_to_values(self) -> None:
        low = self.slider.value_at(0)
        high = self.slider.value_at(self.slider.width())
        self.assertLess(low, high)
        self.assertGreaterEqual(low, 0)
        self.assertLessEqual(high, 1000)

    def test_track_press_moves_to_the_clicked_position_and_release_finishes(self) -> None:
        x = self.slider.width() - 4
        self.slider.mousePressEvent(self._mouse(QEvent.Type.MouseButtonPress, x))
        self.assertTrue(self.slider.isSliderDown())
        self.assertTrue(self.events)
        self.assertGreater(self.slider.value(), 0)
        self.slider.mouseReleaseEvent(self._mouse(QEvent.Type.MouseButtonRelease, x))
        self.assertFalse(self.slider.isSliderDown())
        self.assertFalse(self.slider._dragging_track)

    def test_drag_keeps_following_the_pointer(self) -> None:
        start = self.slider.width() * 0.2
        end = self.slider.width() * 0.8
        self.slider.mousePressEvent(self._mouse(QEvent.Type.MouseButtonPress, start))
        first = self.events[-1]
        self.slider.mouseMoveEvent(self._mouse(QEvent.Type.MouseMove, end))
        last = self.events[-1]
        self.slider.mouseReleaseEvent(self._mouse(QEvent.Type.MouseButtonRelease, end))
        self.assertGreater(last, first)
        self.assertEqual(self.slider.value(), last)

    def test_press_on_the_handle_is_left_to_the_base_slider(self) -> None:
        self.slider.setValue(500)
        _APP.processEvents()
        handle = self.slider._sub_control_rect(QStyle.SubControl.SC_SliderHandle)
        self.assertGreater(handle.width(), 0)
        self.slider.mousePressEvent(
            self._mouse(QEvent.Type.MouseButtonPress, float(handle.center().x()))
        )
        self.assertEqual(self.events, [])
        self.assertFalse(self.slider._dragging_track)
        self.slider.mouseReleaseEvent(
            self._mouse(QEvent.Type.MouseButtonRelease, float(handle.center().x()))
        )

    def test_none_events_are_ignored(self) -> None:
        self.slider.mousePressEvent(None)
        self.slider.mouseMoveEvent(None)
        self.slider.mouseReleaseEvent(None)
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
