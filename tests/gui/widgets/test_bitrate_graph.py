"""순간 비트레이트 그래프가 점, 일시 정지 구간, 최대 비트레이트 기준선을 그리고 마우스 위치의 툴팁을 주는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from src.core.encode.encode_progress_view import SERIES_WINDOW_SEC, SeriesPoint
from src.gui.widgets.bitrate_graph import GRAPH_HEIGHT_PX, MARGIN_LEFT_PX, MARGIN_RIGHT_PX, BitrateGraph

_APP = QApplication.instance() or QApplication([])
_NOW_SEC: float = 200.0
_POINTS: tuple[SeriesPoint, ...] = (
    SeriesPoint(110.0, 2000.0),
    SeriesPoint(120.0, 2400.0),
    SeriesPoint(130.0, None),
    SeriesPoint(160.0, 2600.0),
)


def _move_to(graph: BitrateGraph, ratio: float) -> None:
    """그래프 그림 영역의 가로 비율 위치로 마우스 이동 이벤트를 보냄"""
    plot_width = graph.width() - MARGIN_LEFT_PX - MARGIN_RIGHT_PX
    x = MARGIN_LEFT_PX + plot_width * ratio
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(x, 10.0),
        QPointF(x, 10.0),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    graph.mouseMoveEvent(event)


class BitrateGraphTests(unittest.TestCase):
    """그래프가 설정한 점을 그리고 툴팁과 강조를 주는지 검증"""

    def setUp(self) -> None:
        self.graph = BitrateGraph()
        self.graph.resize(480, GRAPH_HEIGHT_PX)

    def test_fixed_height_and_paint_with_values_and_pause_span(self) -> None:
        """고정 높이이고, 값과 일시 정지 구간, 기준선이 있어도 그림이 그려지는지 검증"""
        self.graph.set_series(_POINTS, 3094.0, _NOW_SEC)
        self.assertEqual(self.graph.height(), GRAPH_HEIGHT_PX)
        image = self.graph.grab().toImage()
        self.assertEqual(image.width(), 480)

    def test_paint_without_points_or_max_line_still_works(self) -> None:
        """점과 최대 비트레이트가 없어도 그림이 그려지는지 검증"""
        self.graph.set_series((), None, _NOW_SEC)
        self.assertFalse(self.graph.grab().isNull())

    def test_pause_point_without_following_value_extends_to_now(self) -> None:
        """마지막이 일시 정지 지점이면 현재 시각까지 구간을 채우고, 값 뒤의 구간은 값 점에서 끝나는지 검증"""
        self.graph.set_series((SeriesPoint(150.0, None),), 3094.0, _NOW_SEC)
        self.assertFalse(self.graph.grab().isNull())
        self.graph.set_series((SeriesPoint(100.0, 2000.0), SeriesPoint(120.0, None), SeriesPoint(140.0, 2000.0)), None, _NOW_SEC)
        self.assertFalse(self.graph.grab().isNull())

    def test_hover_shows_the_nearest_point_label(self) -> None:
        """마우스가 값 점 근처에 있으면 그 점의 시각과 비트레이트가 툴팁으로 나오는지 검증"""
        self.graph.set_series(_POINTS, 3094.0, _NOW_SEC)
        start = _NOW_SEC - SERIES_WINDOW_SEC
        _move_to(self.graph, (120.0 - start) / SERIES_WINDOW_SEC)
        self.assertEqual(self.graph.toolTip(), "02:00, 2400kbps")
        self.graph.grab()

    def test_hover_on_pause_point_names_the_paused_span(self) -> None:
        """마우스가 일시 정지 지점 근처에 있으면 구간 이름이 툴팁으로 나오는지 검증"""
        self.graph.set_series(_POINTS, 3094.0, _NOW_SEC)
        start = _NOW_SEC - SERIES_WINDOW_SEC
        _move_to(self.graph, (130.0 - start) / SERIES_WINDOW_SEC)
        self.assertEqual(self.graph.toolTip(), "02:10, 일시 정지 구간")

    def test_hover_without_points_has_no_tooltip(self) -> None:
        """점이 없으면 마우스 위치에도 툴팁이 나오지 않는지 검증"""
        self.graph.set_series((), None, _NOW_SEC)
        _move_to(self.graph, 0.5)
        self.assertEqual(self.graph.toolTip(), "")

    def test_leaving_the_graph_clears_the_hover_and_tooltip(self) -> None:
        """마우스가 그래프를 벗어나면 툴팁과 강조가 지워지는지 검증"""
        self.graph.set_series(_POINTS, 3094.0, _NOW_SEC)
        _move_to(self.graph, 0.5)
        self.graph.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertEqual(self.graph.toolTip(), "")
        self.graph.grab()


if __name__ == "__main__":
    unittest.main(verbosity=2)
