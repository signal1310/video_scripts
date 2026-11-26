"""열 폭 측정이 표본 행 밖에만 나오는 긴 사유까지 덮고, 빈 표의 파일명 열 기본 폭을 지키는지 검증"""

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QApplication, QHeaderView, QTableView

from src.core.config import ClassifierConfig
from src.core.evaluation.evaluator import VideoEvaluator
from src.core.models import VideoEvaluation
from src.core.smart_action import SmartAction
from src.gui.constants import (
    COLUMN_PRESETS,
    COMPACT_PROPERTY,
    PRESET_CUSTOM,
    PRESET_DEFAULT,
    PRESET_QUALITY,
    PRESET_SPECS,
    preset_label,
)
from src.gui.table.column_presets import (
    PRESET_ALL,
    detect_column_preset,
    hidden_columns_for_preset,
)
from src.gui.table.column_sizing import (
    CELL_PADDING_PX,
    COMPACT_ROW_HEIGHT_PX,
    CONTENT_SAMPLE_ROWS,
    DEFAULT_HIDDEN_COLS,
    NAME_COL_DEFAULT_WIDTH,
    NAME_COL_MAX_WIDTH,
    NORMAL_ROW_HEIGHT_PX,
    TextWidthCache,
    apply_compact_mode,
    auto_fit_visible_columns,
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


class ColumnPresetTests(unittest.TestCase):
    """열 프리셋 4종의 숨김 열 계산, 라벨, 현재 상태 감지를 검증"""

    def test_presets_cover_four_korean_labels(self) -> None:
        self.assertEqual(COLUMN_PRESETS, {
            PRESET_DEFAULT: "기본",
            PRESET_QUALITY: "화질, 해상도",
            PRESET_SPECS: "기술 사양",
            PRESET_ALL: "전체",
        })
        self.assertEqual(preset_label(PRESET_SPECS), "기술 사양")
        self.assertEqual(preset_label(PRESET_CUSTOM), "사용자 지정")
        self.assertEqual(preset_label("unknown"), "사용자 지정")

    def test_hidden_columns_hide_everything_outside_the_preset(self) -> None:
        self.assertEqual(hidden_columns_for_preset(PRESET_ALL), frozenset())
        self.assertEqual(hidden_columns_for_preset(PRESET_DEFAULT), DEFAULT_HIDDEN_COLS)
        self.assertEqual(hidden_columns_for_preset("unknown"), DEFAULT_HIDDEN_COLS)
        quality_hidden = hidden_columns_for_preset(PRESET_QUALITY)
        specs_hidden = hidden_columns_for_preset(PRESET_SPECS)
        self.assertIn(VideoTableModel.COL_CODEC, quality_hidden)
        self.assertNotIn(VideoTableModel.COL_CODEC, specs_hidden)
        self.assertIn(VideoTableModel.COL_ACTION, specs_hidden)
        self.assertNotIn(VideoTableModel.COL_ACTION, quality_hidden)
        for hidden in (quality_hidden, specs_hidden):
            self.assertNotIn(VideoTableModel.COL_NAME, hidden)

    def test_detect_returns_the_matching_preset_or_custom(self) -> None:
        for preset_key in COLUMN_PRESETS:
            self.assertEqual(detect_column_preset(hidden_columns_for_preset(preset_key)), preset_key)
        custom_hidden = DEFAULT_HIDDEN_COLS - {VideoTableModel.COL_ROTATE}
        self.assertEqual(detect_column_preset(custom_hidden), PRESET_CUSTOM)


class _TableViewFixture:
    """내용이 있는 모델을 붙인 QTableView 생성 도우미"""

    @staticmethod
    def build(count: int = 3) -> QTableView:
        view = QTableView()
        model = VideoTableModel()
        model.set_data_source(_evaluations(count), root_dir="C:/work")
        view.setModel(model)
        header = view.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        return view


class AutoFitVisibleColumnsTests(unittest.TestCase):
    """모든 열 크기 맞춤이 보이는 열만 실측 폭으로 조정하는지 검증"""

    def test_visible_columns_get_measured_widths_and_hidden_columns_are_skipped(self) -> None:
        view = _TableViewFixture.build()
        model = view.model()
        assert isinstance(model, VideoTableModel)
        view.setColumnHidden(VideoTableModel.COL_ROTATE, True)
        applied = auto_fit_visible_columns(model, view)
        self.assertNotIn(VideoTableModel.COL_ROTATE, applied)
        self.assertIn(VideoTableModel.COL_ACTION, applied)
        header = view.horizontalHeader()
        assert header is not None
        self.assertEqual(header.sectionSize(VideoTableModel.COL_ACTION), applied[VideoTableModel.COL_ACTION])

    def test_name_column_is_clamped_to_the_max_width(self) -> None:
        view = _TableViewFixture.build()
        model = view.model()
        assert isinstance(model, VideoTableModel)
        applied = auto_fit_visible_columns(model, view)
        self.assertLessEqual(applied[VideoTableModel.COL_NAME], NAME_COL_MAX_WIDTH)
        self.assertGreaterEqual(applied[VideoTableModel.COL_NAME], NAME_COL_DEFAULT_WIDTH)

    def test_compact_mode_reduces_content_widths(self) -> None:
        view = _TableViewFixture.build()
        model = view.model()
        assert isinstance(model, VideoTableModel)
        normal = auto_fit_visible_columns(model, view)
        compact = auto_fit_visible_columns(model, view, compact=True)
        self.assertLessEqual(compact[VideoTableModel.COL_ACTION], normal[VideoTableModel.COL_ACTION])


class CompactModeTests(unittest.TestCase):
    """좁게 보기가 QSS 속성과 행 높이를 함께 바꾸는지 검증"""

    def test_toggle_sets_property_on_view_and_header_and_changes_row_height(self) -> None:
        view = _TableViewFixture.build()
        apply_compact_mode(view, True)
        header = view.horizontalHeader()
        assert header is not None
        self.assertEqual(view.property(COMPACT_PROPERTY), "true")
        self.assertEqual(header.property(COMPACT_PROPERTY), "true")
        vheader = view.verticalHeader()
        assert vheader is not None
        self.assertEqual(vheader.defaultSectionSize(), COMPACT_ROW_HEIGHT_PX)
        apply_compact_mode(view, False)
        self.assertEqual(view.property(COMPACT_PROPERTY), "false")
        self.assertEqual(header.property(COMPACT_PROPERTY), "false")
        self.assertEqual(vheader.defaultSectionSize(), NORMAL_ROW_HEIGHT_PX)


if __name__ == "__main__":
    unittest.main(verbosity=2)
