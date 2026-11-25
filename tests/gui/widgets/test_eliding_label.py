"""말줄임 라벨의 폭 힌트, 최소 폭, 글자색 토큰과 그리기 준비 상태를 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QWidget

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.widgets.eliding_label import ElidingLabel

_APP = QApplication.instance() or QApplication([])


class ElidingLabelTests(unittest.TestCase):
    """말줄임 라벨의 문구, 폭 정책, 최소 폭, 글자색을 검증"""

    def test_full_text_is_kept_and_minimum_width_is_free(self) -> None:
        """전체 문구를 보관하고 최소 폭을 비워 좁은 자리에서 줄어들게 하는지 검증"""
        label = ElidingLabel()
        self.addCleanup(label.deleteLater)
        label.set_full_text("아주 긴 작업 폴더 이름")
        self.assertEqual(label.full_text, "아주 긴 작업 폴더 이름")
        self.assertEqual(label.minimumSizeHint().width(), 0)
        self.assertGreater(label.minimumSizeHint().height(), 0)

    def test_pinned_label_never_wider_than_its_text(self) -> None:
        """pin_width 라벨은 최대 폭을 문구 폭으로 묶어 남는 가로 공간을 쓰지 않는지 검증"""
        label = ElidingLabel(pin_width=True)
        self.addCleanup(label.deleteLater)
        label.set_full_text("clips")
        text_width = label.fontMetrics().horizontalAdvance("clips")
        self.assertLessEqual(label.maximumWidth(), text_width + 4)
        label.set_full_text("아주 긴 작업 폴더 이름")
        self.assertGreater(label.maximumWidth(), text_width)

    def test_unpinned_label_keeps_the_default_maximum(self) -> None:
        """기본 라벨은 최대 폭을 문구 폭으로 묶지 않는지 검증"""
        label = ElidingLabel()
        self.addCleanup(label.deleteLater)
        label.set_full_text("clips")
        self.assertGreater(label.maximumWidth(), 100000)

    def test_paint_before_and_after_text_and_color(self) -> None:
        """문구와 색이 없을 때와 있을 때 모두 그리기가 예외 없이 끝나는지 검증"""
        label = ElidingLabel()
        self.addCleanup(label.deleteLater)
        label.resize(60, 20)
        label.paintEvent(None)
        label.set_full_text("아주 긴 작업 폴더 이름")
        label.set_color(DARK_PALETTE.text_secondary)
        label.paintEvent(None)
        self.assertEqual(label.color, DARK_PALETTE.text_secondary)
        label.set_color(LIGHT_PALETTE.text_secondary)
        self.assertEqual(label.color, LIGHT_PALETTE.text_secondary)

    def test_paint_produces_visible_pixels_of_the_text_color(self) -> None:
        """말줄임 문구가 지정한 글자색 픽셀을 실제로 남기는지 검증"""
        host = QWidget()
        self.addCleanup(host.deleteLater)
        label = ElidingLabel(host)
        self.addCleanup(label.deleteLater)
        label.resize(QSize(140, 24))
        label.set_full_text("clips")
        label.set_color("#FF0000")
        image = label.grab().toImage()
        has_red = any(
            QColor(image.pixelColor(x, y)).red() > 120
            and QColor(image.pixelColor(x, y)).green() < 90
            for x in range(image.width())
            for y in range(image.height())
        )
        self.assertTrue(has_red)


if __name__ == "__main__":
    unittest.main(verbosity=2)
