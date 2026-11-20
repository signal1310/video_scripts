"""열 폭 측정이 표본 행 밖에만 나오는 긴 사유까지 덮고, 빈 표의 파일명 열 기본 폭을 지키는지 검증"""

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.evaluation.evaluator import VideoEvaluator
from src.core.models import VideoEvaluation
from src.core.smart_action import SmartAction
from src.gui.table.column_sizing import (
    CELL_PADDING_PX,
    CONTENT_SAMPLE_ROWS,
    NAME_COL_DEFAULT_WIDTH,
    TextWidthCache,
    measure_column_content_widths,
    measure_name_content_width,
)
from src.gui.table.table_model import VideoTableModel
from tests._factories import make_video

_APP = QApplication.instance() or QApplication([])
# 표본 간격이 2가 되는 행 수. 짝수 행만 표본이므로 1번 행은 표본 밖
_SAMPLED_ROW_COUNT: int = 2 * CONTENT_SAMPLE_ROWS
_UNSAMPLED_ROW: int = 1


def _evaluations(count: int) -> list[VideoEvaluation]:
    videos = [make_video(filename=f"clip{i:04d}.mp4") for i in range(count)]
    return VideoEvaluator.evaluate_all("C:/work", videos, ClassifierConfig()).evaluations


class ReasonColumnWidthTests(unittest.TestCase):
    """사유 열 폭이 표본 행에 나온 값만이 아니라 가능한 모든 사유의 폭을 덮는지 검증"""

    def test_width_covers_longest_reason_shown_only_outside_the_sample(self) -> None:
        """표본 밖 행에만 있는 가장 긴 사유가 열 폭 안에 들어가는지 검증"""
        evaluations = _evaluations(_SAMPLED_ROW_COUNT)
        bold = QFont()
        bold.setBold(True)
        metrics = QFontMetrics(bold)
        longest = max(SmartAction, key=lambda action: metrics.horizontalAdvance(action.reason_label))
        evaluations[_UNSAMPLED_ROW] = replace(evaluations[_UNSAMPLED_ROW], smart_action=longest)
        model = VideoTableModel()
        model.set_data_source(evaluations, root_dir="C:/work")
        reason_col = VideoTableModel.COL_REASON
        sampled_texts = {
            model.index(row, reason_col).data(Qt.ItemDataRole.DisplayRole)
            for row in range(0, _SAMPLED_ROW_COUNT, 2)
        }
        self.assertNotIn(longest.reason_label, sampled_texts)
        widths = measure_column_content_widths(model, QFont(), TextWidthCache())
        self.assertGreaterEqual(
            widths[reason_col],
            metrics.horizontalAdvance(longest.reason_label) + CELL_PADDING_PX,
        )


class NameColumnWidthTests(unittest.TestCase):
    """파일명 열 폭이 표시할 행이 없을 때 기본 폭을 유지하는지 검증"""

    def test_empty_table_keeps_the_default_name_width(self) -> None:
        """행이 없는 모델의 파일명 열 폭이 기본 폭인지 검증"""
        model = VideoTableModel()
        self.assertEqual(measure_name_content_width(model, QFont(), TextWidthCache()), NAME_COL_DEFAULT_WIDTH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
