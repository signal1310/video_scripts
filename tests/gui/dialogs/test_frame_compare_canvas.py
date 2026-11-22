"""화질 비교 캔버스 검증: 맞춤 배율, 3모드 라벨, 분할선 드래그, 이동, 휠 확대와 즉시 전환 표시

- 오프스크린에서 위젯을 띄우고 마우스, 휠 사건을 직접 보냄
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import QApplication

from src.gui.dialogs.frame_compare_canvas import (
    MODE_SIDE,
    MODE_SPLIT,
    MODE_TOGGLE,
    FrameCompareCanvas,
)
from src.gui.styles.palettes import LIGHT_PALETTE
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _image(width: int = 200, height: int = 100, color: str = "#FF0000") -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


def _mouse(event_type: QEvent.Type, x: float, y: float, button: Qt.MouseButton) -> QMouseEvent:
    point = QPointF(x, y)
    buttons = Qt.MouseButton.LeftButton if event_type == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton
    return QMouseEvent(
        event_type, point, QPointF(x, y), button, buttons, Qt.KeyboardModifier.NoModifier
    )


def _wheel(x: float, y: float, delta: int) -> QWheelEvent:
    point = QPointF(x, y)
    return QWheelEvent(
        point,
        QPointF(x, y),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def _render(canvas: FrameCompareCanvas) -> QImage:
    """캔버스를 같은 크기의 이미지에 그려 픽셀을 확인"""
    target = QImage(canvas.size(), QImage.Format.Format_RGB32)
    target.fill(QColor("#000000"))
    canvas.render(target)
    return target


class CanvasTests(GuiTestCase):
    """캔버스 그리기 상태, 드래그와 확대 조작"""

    def setUp(self) -> None:
        self.canvas = FrameCompareCanvas()
        self.addCleanup(self.canvas.deleteLater)
        self.canvas.resize(400, 200)
        self.canvas.show()
        _APP.processEvents()

    def test_fit_scale_and_percent_follow_the_view_size(self) -> None:
        self.canvas.set_images(_image(), _image(color="#0000FF"))
        self.assertAlmostEqual(self.canvas.scale(), 2.0)
        self.assertEqual(self.canvas.percent_text(), "200%")
        self.canvas.grab()

    def test_modes_switch_corner_labels_and_center_badge(self) -> None:
        self.canvas.set_images(_image(), _image())
        self.canvas.set_labels("원본", "1080p 축소 비교", "축소 풀이")
        self.canvas.set_mode(MODE_SIDE)
        self.assertEqual(self.canvas.mode(), MODE_SIDE)
        self.assertFalse(self.canvas._left_label.isHidden())
        self.assertFalse(self.canvas._right_label.isHidden())
        self.assertTrue(self.canvas._center_badge.isHidden())
        self.assertEqual(self.canvas._right_label.toolTip(), "축소 풀이")
        self.canvas.set_mode(MODE_TOGGLE)
        self.assertTrue(self.canvas._left_label.isHidden())
        self.assertFalse(self.canvas._center_badge.isHidden())
        self.canvas.grab()
        self.canvas.set_mode(MODE_SPLIT)
        self.assertFalse(self.canvas._left_label.isHidden())

    def test_split_ratio_is_clamped(self) -> None:
        self.canvas.set_split_ratio(0.0)
        self.assertAlmostEqual(self.canvas.split_ratio(), 0.05)
        self.canvas.set_split_ratio(1.0)
        self.assertAlmostEqual(self.canvas.split_ratio(), 0.95)
        self.canvas.set_split_ratio(0.4)
        self.assertAlmostEqual(self.canvas.split_ratio(), 0.4)

    def test_dragging_the_divider_moves_only_the_split(self) -> None:
        self.canvas.set_images(_image(), _image())
        divider_x = self.canvas.split_ratio() * self.canvas.width()
        QApplication.sendEvent(
            self.canvas, _mouse(QEvent.Type.MouseButtonPress, divider_x, 100, Qt.MouseButton.LeftButton)
        )
        QApplication.sendEvent(self.canvas, _mouse(QEvent.Type.MouseMove, 10, 100, Qt.MouseButton.NoButton))
        QApplication.sendEvent(
            self.canvas, _mouse(QEvent.Type.MouseButtonRelease, 10, 100, Qt.MouseButton.NoButton)
        )
        self.assertAlmostEqual(self.canvas.split_ratio(), 0.05)
        self.assertIsNone(self.canvas._dragging)

    def test_pan_moves_the_image_when_zoomed_in_and_clamps(self) -> None:
        self.canvas.set_images(_image(), _image())
        self.canvas.apply_zoom_preset("400")
        self.assertAlmostEqual(self.canvas.scale(), 4.0)
        QApplication.sendEvent(
            self.canvas, _mouse(QEvent.Type.MouseButtonPress, 200, 100, Qt.MouseButton.LeftButton)
        )
        QApplication.sendEvent(self.canvas, _mouse(QEvent.Type.MouseMove, 260, 140, Qt.MouseButton.NoButton))
        QApplication.sendEvent(
            self.canvas, _mouse(QEvent.Type.MouseButtonRelease, 260, 140, Qt.MouseButton.NoButton)
        )
        self.assertLessEqual(self.canvas._transform.offset_x, 0)
        self.assertLessEqual(self.canvas._transform.offset_y, 0)

    def test_toggle_mode_peeks_at_the_original_while_pressed(self) -> None:
        sides: list[bool] = []
        self.canvas.side_changed.connect(sides.append)
        self.canvas.set_images(_image(), _image())
        self.canvas.set_mode(MODE_TOGGLE)
        QApplication.sendEvent(
            self.canvas, _mouse(QEvent.Type.MouseButtonPress, 200, 100, Qt.MouseButton.LeftButton)
        )
        self.assertTrue(self.canvas.showing_original())
        self.canvas.grab()
        QApplication.sendEvent(
            self.canvas, _mouse(QEvent.Type.MouseButtonRelease, 200, 100, Qt.MouseButton.NoButton)
        )
        self.assertFalse(self.canvas.showing_original())
        self.assertEqual(sides, [True, False])
        self.canvas.set_original_pinned(True)
        self.assertTrue(self.canvas.original_pinned())
        self.assertTrue(self.canvas.showing_original())

    def test_wheel_zooms_around_the_cursor_and_side_mode_maps_the_right_pane(self) -> None:
        scales: list[float] = []
        self.canvas.scale_changed.connect(scales.append)
        self.canvas.set_images(_image(), _image())
        self.canvas.set_mode(MODE_SIDE)
        before = self.canvas.scale()
        QApplication.sendEvent(self.canvas, _wheel(350, 100, 120))
        self.assertGreater(self.canvas.scale(), before)
        self.assertTrue(scales)
        half = (self.canvas.width() - 1) / 2
        self.assertAlmostEqual(self.canvas._local_view_point(QPointF(350, 100))[0], 350 - half - 1)

    def test_zoom_presets_and_steps_stay_within_limits(self) -> None:
        self.canvas.set_images(_image(), _image())
        self.canvas.apply_zoom_preset("1")
        self.assertAlmostEqual(self.canvas.scale(), 1.0)
        self.canvas.zoom_step(1)
        self.assertAlmostEqual(self.canvas.scale(), 1.25)
        self.canvas.zoom_step(-1)
        self.assertAlmostEqual(self.canvas.scale(), 1.0)
        self.canvas.apply_zoom_preset("fit")
        self.assertAlmostEqual(self.canvas.scale(), 2.0)

    def test_resize_keeps_fit_mode_and_clamps_manual_zoom(self) -> None:
        self.canvas.set_images(_image(), _image())
        self.canvas.resize(800, 400)
        _APP.processEvents()
        self.assertAlmostEqual(self.canvas.scale(), 4.0)
        self.canvas.apply_zoom_preset("400")
        self.canvas.resize(100, 100)
        _APP.processEvents()
        self.assertGreaterEqual(self.canvas._transform.offset_x, -self.canvas.scale() * 200)

    def test_palette_and_empty_canvas_operations_are_safe(self) -> None:
        self.canvas.apply_palette(LIGHT_PALETTE)
        self.assertIn(LIGHT_PALETTE.media_overlay_bg, self.canvas._left_label.styleSheet())
        self.canvas.fit()
        self.canvas.zoom_step(1)
        self.canvas.apply_zoom_preset("400")
        QApplication.sendEvent(self.canvas, _wheel(10, 10, 120))
        self.canvas.set_peek(True)
        self.assertTrue(self.canvas.showing_original())
        self.canvas.set_peek(False)
        self.canvas.grab()

    def test_compared_image_fills_the_original_rect_in_all_modes(self) -> None:
        self.canvas.set_images(_image(160, 90, "#00FF00"), _image(80, 45, "#FF0000"))
        self.assertAlmostEqual(self.canvas.scale(), 400 / 160 if 400 / 160 < 200 / 90 else 200 / 90)
        split = _render(self.canvas)
        self.assertGreater(split.pixelColor(355, 189).red(), 200)
        self.canvas.set_mode(MODE_SIDE)
        self.canvas.fit()
        side = _render(self.canvas)
        self.assertGreater(side.pixelColor(387, 150).red(), 200)
        self.canvas.set_mode(MODE_TOGGLE)
        toggle = _render(self.canvas)
        self.assertGreater(toggle.pixelColor(187, 150).red(), 200)

    def test_same_size_frames_keep_zoom_and_pan_until_reset(self) -> None:
        self.canvas.set_images(_image(160, 90), _image(160, 90, "#0000FF"))
        self.canvas.apply_zoom_preset("400")
        QApplication.sendEvent(
            self.canvas, _mouse(QEvent.Type.MouseButtonPress, 200, 100, Qt.MouseButton.LeftButton)
        )
        QApplication.sendEvent(self.canvas, _mouse(QEvent.Type.MouseMove, 250, 130, Qt.MouseButton.NoButton))
        QApplication.sendEvent(
            self.canvas, _mouse(QEvent.Type.MouseButtonRelease, 250, 130, Qt.MouseButton.NoButton)
        )
        before = (self.canvas.scale(), self.canvas._transform.offset_x, self.canvas._transform.offset_y)
        self.canvas.set_images(_image(160, 90), _image(160, 90, "#0000FF"))
        self.assertEqual(
            (self.canvas.scale(), self.canvas._transform.offset_x, self.canvas._transform.offset_y), before
        )
        self.canvas.set_images(_image(80, 45), _image(80, 45, "#0000FF"))
        self.assertTrue(self.canvas._fit_mode)
        self.assertAlmostEqual(self.canvas.scale(), 200 / 45)
        self.canvas.apply_zoom_preset("1")
        self.canvas.set_images(_image(80, 45), _image(80, 45, "#0000FF"), reset_view=True)
        self.assertTrue(self.canvas._fit_mode)
        self.assertAlmostEqual(self.canvas.scale(), 200 / 45)

    def test_side_mode_paints_both_panes(self) -> None:
        self.canvas.set_images(_image(), _image(color="#0000FF"))
        self.canvas.set_mode(MODE_SIDE)
        self.canvas.grab()


if __name__ == "__main__":
    unittest.main(verbosity=2)
