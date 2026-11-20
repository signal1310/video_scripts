"""재인코딩 카드의 그래프 범례가 항목마다 그래프와 같은 표본 모양을 그리고, 폭이 좁으면 줄을 바꾸며, 항목이 없으면 숨는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel

from src.gui.dialogs.encode_graph_legend import TEXT_OBJECT_NAME, GraphLegend
from src.gui.styles.palettes import LIGHT_PALETTE
from src.gui.widgets.bitrate_graph import LegendEntry

_APP = QApplication.instance() or QApplication([])
_ENTRIES: tuple[LegendEntry, ...] = (
    LegendEntry("원본 18409kbps", "#888888", Qt.PenStyle.SolidLine, 1.0),
    LegendEntry("최대 32717kbps", "#EA580C", Qt.PenStyle.DashLine, 1.5),
    LegendEntry("최고 32717kbps", "#475569", Qt.PenStyle.DotLine, 1.0),
)


class GraphLegendTests(unittest.TestCase):
    """범례 항목은 입력한 순서로 문구와 표본을 갖고, 빈 목록이면 숨김"""

    def test_each_entry_gets_a_text_and_a_swatch_and_empty_input_hides_the_legend(self) -> None:
        """항목 수만큼 문구 라벨이 생기고, 항목이 없으면 범례 자체를 숨김"""
        legend = GraphLegend()
        legend.set_entries(())
        self.assertTrue(legend.isHidden())
        self.assertEqual(legend.entries(), ())
        legend.set_entries(_ENTRIES)
        self.assertFalse(legend.isHidden())
        self.assertEqual(legend.entries(), _ENTRIES)
        labels = [label.text() for label in legend.findChildren(QLabel)]
        self.assertEqual(labels, [entry.text for entry in _ENTRIES])

    def test_swatches_paint_without_error_at_the_entry_style(self) -> None:
        """표본을 실제로 그려 보며 오류가 없는지 확인(화면은 띄우지 않고 offscreen 그리기)"""
        legend = GraphLegend()
        legend.apply_palette(LIGHT_PALETTE)
        legend.set_entries(_ENTRIES)
        legend.resize(400, 40)
        legend.show()
        self.assertIsNotNone(legend.grab())
        legend.hide()

    def test_palette_colors_the_legend_text_from_the_text_token(self) -> None:
        """설명 글자는 팔레트의 흐린 글자 토큰으로 칠함"""
        legend = GraphLegend()
        legend.apply_palette(LIGHT_PALETTE)
        self.assertIn(f"QLabel#{TEXT_OBJECT_NAME}", legend.styleSheet())
        self.assertIn(LIGHT_PALETTE.text_muted, legend.styleSheet())

    def test_narrow_width_wraps_the_entries_onto_more_lines(self) -> None:
        """폭이 좁으면 항목이 줄을 바꿔 높이가 늘어남"""
        legend = GraphLegend()
        legend.set_entries(_ENTRIES)
        self.assertGreater(legend.heightForWidth(120), legend.heightForWidth(800))


if __name__ == "__main__":
    unittest.main(verbosity=2)
