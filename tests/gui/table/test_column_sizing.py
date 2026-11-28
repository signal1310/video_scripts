"""열 폭 측정이 표본 행 밖에만 나오는 긴 사유까지 덮고, 빈 표의 파일명 열 기본 폭을 지키는지 검증"""

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QApplication, QHeaderView, QTableView

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.config import ClassifierConfig
from src.core.evaluation.evaluator import VideoEvaluator
from src.core.models import ActiveFrame, VideoEvaluation
from src.core.smart_action import SMART_ACTION_LABELS, SmartAction
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
    ICON_CELL_EXTRA_PX,
    MIN_COL_WIDTHS,
    NAME_COL_DEFAULT_WIDTH,
    NAME_COL_MAX_WIDTH,
    NORMAL_ROW_HEIGHT_PX,
    PILL_CELL_EXTRA_PX,
    TextWidthCache,
    apply_compact_mode,
    auto_fit_visible_columns,
    measure_column_content_widths,
    measure_name_content_width,
)
from src.gui.table.delegates import (
    badge_font,
    pill_badge_cell_width,
    quiet_dot_cell_width,
    quiet_dot_font,
    quiet_dot_text_limit,
)
from src.gui.table.frame_analysis_columns import ACTIVE_FRAME_KEEP_FULL_TEXT
from src.gui.table.table_columns import ENCODE_STATE_LABELS
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


class ActiveFrameColumnWidthTests(unittest.TestCase):
    """활성 화면 열 폭이 연필 아이콘 붙은 크기와 전체 화면 유지 알약을 자르지 않는지 검증"""

    def _model_with_states(self) -> VideoTableModel:
        """수동 크기 행과 전체 화면 유지 행, 자동 크기 행을 담은 모델"""
        model = VideoTableModel()
        model.set_data_source(_evaluations(3), root_dir="C:/work")
        model.set_frame_analysis_results(
            {
                "clip0000.mp4": FrameAnalysisResult(
                    active_region=ActiveRegion(1280, 720, 0, 0, False),
                    metric_used="vmaf",
                    vmaf_model="vmaf_v0.6.1",
                    tier_scores=(),
                    candidate_tier=None,
                    valid_segment_count=5,
                    total_segment_count=5,
                    unmeasurable=False,
                    is_vr_protected=False,
                ),
                "clip0001.mp4": FrameAnalysisResult(
                    active_region=ActiveRegion(734, 444, 593, 138, True),
                    metric_used="vmaf",
                    vmaf_model="vmaf_v0.6.1",
                    tier_scores=(),
                    candidate_tier=None,
                    valid_segment_count=5,
                    total_segment_count=5,
                    unmeasurable=False,
                    is_vr_protected=False,
                ),
            }
        )
        model.set_crop_exclusions(["clip0001.mp4"])
        model.set_manual_active_frames({"clip0000.mp4": ActiveFrame(132, 240, 0, 0)})
        return model

    def test_keep_full_pill_and_iconed_size_fit_the_measured_width(self) -> None:
        """알약 문구와 연필 붙은 크기가 아이콘, 알약 여백을 더한 폭 안에 들어가는지 검증"""
        model = self._model_with_states()
        col = VideoTableModel.COL_ACTIVE_FRAME
        widths = measure_column_content_widths(model, QFont(), TextWidthCache())
        bold = QFont()
        bold.setBold(True)
        pill_width = QFontMetrics(bold).horizontalAdvance(ACTIVE_FRAME_KEEP_FULL_TEXT)
        expected = max(
            pill_width, QFontMetrics(bold).horizontalAdvance("132x240")
        ) + CELL_PADDING_PX + ICON_CELL_EXTRA_PX + PILL_CELL_EXTRA_PX
        self.assertGreaterEqual(widths[col], expected)
        self.assertEqual(model.index(1, col).data(Qt.ItemDataRole.DisplayRole), ACTIVE_FRAME_KEEP_FULL_TEXT)
        self.assertEqual(model.index(0, col).data(Qt.ItemDataRole.DisplayRole), "132x240")


class BadgeColumnWidthTests(unittest.TestCase):
    """배지로 그리는 열 폭이 글자 폭이 아니라 배지가 필요로 하는 폭을 덮는지 검증"""

    def _model(self) -> VideoTableModel:
        model = VideoTableModel()
        model.set_data_source(_evaluations(3), root_dir="C:/work")
        return model

    def test_zone_width_fits_the_widest_zone_badge(self) -> None:
        """화질 등급 열 폭이 점 배지 글꼴로 잰 가장 넓은 등급 표기를 점과 여백까지 담는지 검증"""
        model = self._model()
        col = VideoTableModel.COL_ZONE
        widths = measure_column_content_widths(model, QFont(), TextWidthCache())
        metrics = QFontMetrics(quiet_dot_font())
        widest = max(
            str(model.index(row, col).data(Qt.ItemDataRole.DisplayRole) or "")
            for row in range(model.rowCount())
        )
        self.assertTrue(widest)
        needed = quiet_dot_cell_width(metrics.horizontalAdvance(widest))
        self.assertGreaterEqual(widths[col], needed)
        self.assertGreaterEqual(quiet_dot_text_limit(widths[col]), metrics.horizontalAdvance(widest))

    def test_action_width_fits_the_widest_action_badge(self) -> None:
        """권장 처리 열 폭이 알약 배지 글꼴로 잰 가장 넓은 처리 표기를 알약 여백까지 담는지 검증"""
        model = self._model()
        col = VideoTableModel.COL_ACTION
        widths = measure_column_content_widths(model, QFont(), TextWidthCache())
        metrics = QFontMetrics(badge_font())
        widest = max(SMART_ACTION_LABELS, key=metrics.horizontalAdvance)
        self.assertGreaterEqual(widths[col], pill_badge_cell_width(metrics.horizontalAdvance(widest)))

    def test_auto_fit_applies_the_badge_width_to_badge_columns(self) -> None:
        """모든 열 너비 맞춤이 화질 등급, 권장 처리, 인코딩 상태 열에 배지 폭을 적용하는지 검증"""
        view = _TableViewFixture.build()
        model = view.model()
        assert isinstance(model, VideoTableModel)
        applied = auto_fit_visible_columns(model, view)
        dot_metrics = QFontMetrics(quiet_dot_font())
        zone_widest = max(
            str(model.index(row, VideoTableModel.COL_ZONE).data(Qt.ItemDataRole.DisplayRole) or "")
            for row in range(model.rowCount())
        )
        self.assertGreaterEqual(
            applied[VideoTableModel.COL_ZONE],
            quiet_dot_cell_width(dot_metrics.horizontalAdvance(zone_widest)),
        )
        pill_metrics = QFontMetrics(badge_font())
        action_widest = max(SMART_ACTION_LABELS, key=pill_metrics.horizontalAdvance)
        self.assertGreaterEqual(
            applied[VideoTableModel.COL_ACTION],
            pill_badge_cell_width(pill_metrics.horizontalAdvance(action_widest)),
        )
        state_widest = max(ENCODE_STATE_LABELS, key=pill_metrics.horizontalAdvance)
        self.assertGreaterEqual(
            applied[VideoTableModel.COL_ENCODE_STATE],
            pill_badge_cell_width(pill_metrics.horizontalAdvance(state_widest)),
        )


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
    """모든 열 너비 맞춤이 보이는 열만 실측 폭으로 조정하는지 검증"""

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

    def test_header_text_width_is_not_counted(self) -> None:
        """열 제목 폭이 셀 값 폭보다 넓어도 제목 폭을 세지 않고 값 폭에 맞추는지 검증"""
        view = _TableViewFixture.build()
        model = view.model()
        assert isinstance(model, VideoTableModel)
        col = VideoTableModel.COL_TARGET_RATIO
        header_text = str(model.headerData(col, Qt.Orientation.Horizontal, int(Qt.ItemDataRole.DisplayRole)) or "")
        self.assertTrue(header_text)
        bold = QFont()
        bold.setBold(True)
        header_width = QFontMetrics(bold).horizontalAdvance(header_text)
        applied = auto_fit_visible_columns(model, view)
        self.assertLess(applied[col], header_width)


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


class EveryColumnHasMinimumWidthTests(unittest.TestCase):
    """셀 값만으로 맞춰도 헤더가 잘리지 않도록 이름 열 밖의 모든 열에 최소 폭이 있는지 검증"""

    def test_every_model_column_has_a_minimum_width(self) -> None:
        model = VideoTableModel()
        missing = [col for col in range(model.columnCount()) if col not in MIN_COL_WIDTHS]
        self.assertEqual(missing, [])
