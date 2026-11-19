"""평면 진행 막대의 트랙, 단색 채움, 무한 진행, 테마 전환 렌더링을 픽셀 단위로 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE, ThemePalette
from src.gui.widgets.progress_fill_bar import FillProgressBar
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])

_BAR_WIDTH: int = 200
_BAR_HEIGHT: int = 18
# 모서리 둥근 부분과 중앙 글자 영역을 피해 샘플링
_SAMPLE_Y: int = _BAR_HEIGHT // 2 + 4
_LEFT_X: int = 20
_RIGHT_X: int = _BAR_WIDTH - 20
_CHANNEL_TOLERANCE: int = 8


def _same_rgb(actual: QColor, expected_hex: str) -> bool:
    expected = QColor(expected_hex)
    return (
        abs(actual.red() - expected.red()) <= _CHANNEL_TOLERANCE
        and abs(actual.green() - expected.green()) <= _CHANNEL_TOLERANCE
        and abs(actual.blue() - expected.blue()) <= _CHANNEL_TOLERANCE
    )


class FillProgressBarTests(GuiTestCase):
    """팔레트 토큰으로 그린 트랙과 채움의 픽셀 색이 기대값과 맞는지 검증"""

    def _bar(self, palette: ThemePalette, value: int, span: tuple[int, int] = (0, 100)) -> FillProgressBar:
        bar = FillProgressBar()
        bar.set_palette(palette)
        bar.setRange(*span)
        bar.setValue(value)
        bar.setTextVisible(False)
        bar.resize(_BAR_WIDTH, _BAR_HEIGHT)
        return bar

    def _pixel(self, palette: ThemePalette, value: int, x: int) -> QColor:
        return self._bar(palette, value).grab().toImage().pixelColor(x, _SAMPLE_Y)

    def test_unfilled_region_shows_track_color(self) -> None:
        """값이 0이거나 채움 끝 바깥이면 트랙 배경 색으로 그려짐을 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette.name):
                self.assertTrue(_same_rgb(self._pixel(palette, 0, _RIGHT_X), palette.progress_track_bg))
                self.assertTrue(_same_rgb(self._pixel(palette, 50, _RIGHT_X), palette.progress_track_bg))

    def test_filled_region_uses_flat_fill_color(self) -> None:
        """채움 안쪽은 팔레트의 단색 채움 값으로 그려지고 값에 따라 색이 변하지 않음을 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette.name):
                self.assertTrue(_same_rgb(self._pixel(palette, 50, _LEFT_X), palette.progress_fill))
                self.assertTrue(_same_rgb(self._pixel(palette, 50, _LEFT_X + 60), palette.progress_fill))
                self.assertTrue(_same_rgb(self._pixel(palette, 100, _RIGHT_X), palette.progress_fill))

    def test_indeterminate_shows_fixed_partial_segment(self) -> None:
        """범위가 0~0이면 왼쪽 일부만 채우고 나머지는 트랙 색으로 그려짐을 검증"""
        bar = self._bar(DARK_PALETTE, 0, span=(0, 0))
        image = bar.grab().toImage()
        self.assertTrue(_same_rgb(image.pixelColor(_LEFT_X, _SAMPLE_Y), DARK_PALETTE.progress_fill))
        self.assertTrue(_same_rgb(image.pixelColor(_RIGHT_X, _SAMPLE_Y), DARK_PALETTE.progress_track_bg))

    def test_set_palette_switches_colors(self) -> None:
        """테마 전환 시 같은 위젯의 트랙 색이 새 팔레트 값으로 바뀜을 검증"""
        bar = self._bar(DARK_PALETTE, 0)
        dark_pixel = bar.grab().toImage().pixelColor(_RIGHT_X, _SAMPLE_Y)
        bar.set_palette(LIGHT_PALETTE)
        light_pixel = bar.grab().toImage().pixelColor(_RIGHT_X, _SAMPLE_Y)
        self.assertTrue(_same_rgb(dark_pixel, DARK_PALETTE.progress_track_bg))
        self.assertTrue(_same_rgb(light_pixel, LIGHT_PALETTE.progress_track_bg))


if __name__ == "__main__":
    unittest.main()
