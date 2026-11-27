"""미리 보기 무대 위젯이 영상 위젯을 영상비로 가운데 놓고 바깥을 고른 색으로 칠하는지 검증"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QWidget

from src.gui.preview.stage import PreviewStage
from src.gui.styles.palettes import DARK_PALETTE
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


class PreviewStageTests(GuiTestCase):
    """무대가 영상 위젯을 영상비로 놓고 바깥을 고른 색으로 칠하는지"""

    def _stage(self, size: QSize | None = None) -> PreviewStage:
        stage = PreviewStage(QWidget(), lambda: (200, 100))
        self.addCleanup(stage.deleteLater)
        stage.apply_stage_style(DARK_PALETTE, "gray", False)
        stage.resize(size if size is not None else QSize(400, 400))
        stage.show()
        _APP.processEvents()
        return stage

    def test_video_is_placed_with_the_frame_aspect_in_the_middle(self) -> None:
        stage = self._stage()
        self.assertEqual(stage.video_geometry(), stage.rect().adjusted(0, 100, 0, -100))
        self.assertEqual(stage._video.geometry(), stage.video_geometry())

    def test_missing_frame_size_fills_the_stage(self) -> None:
        stage = PreviewStage(QWidget(), lambda: None)
        self.addCleanup(stage.deleteLater)
        stage.resize(320, 240)
        self.assertEqual(stage.video_geometry(), stage.rect())

    def test_pixel_outside_the_frame_equals_the_chosen_color(self) -> None:
        stage = self._stage()
        image = stage.grab().toImage()
        self.assertEqual(image.pixelColor(5, 5).name().upper(), DARK_PALETTE.media_stage_gray.upper())

    def test_each_background_color_is_painted_outside_the_frame(self) -> None:
        stage = self._stage()
        for key, token in (
            ("black", DARK_PALETTE.media_bg),
            ("gray", DARK_PALETTE.media_stage_gray),
            ("white", DARK_PALETTE.media_stage_white),
        ):
            stage.apply_stage_style(DARK_PALETTE, key, False)
            _APP.processEvents()
            image = stage.grab().toImage()
            self.assertEqual(image.pixelColor(5, 5).name().upper(), QColor(token).name().upper(), key)

    def test_outline_marks_the_frame_edge_without_covering_video_pixels(self) -> None:
        stage = self._stage()
        stage.apply_stage_style(DARK_PALETTE, "black", True)
        _APP.processEvents()
        image = stage.grab().toImage()
        outline = QColor(DARK_PALETTE.media_stage_outline)
        frame_top = stage.video_geometry().top()
        row = frame_top - 1
        hits = [
            image.pixelColor(x, row)
            for x in range(stage.video_geometry().left(), stage.video_geometry().right())
        ]
        self.assertTrue(any(color.name().upper() == outline.name().upper() for color in hits))
        self.assertEqual(image.pixelColor(5, 5).name().upper(), QColor(DARK_PALETTE.media_bg).name().upper())
