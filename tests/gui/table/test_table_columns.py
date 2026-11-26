"""테이블 열 스키마의 인코딩 상태 열 정의와 열 식별자 대응을 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.encode.encode_batch_book import STATUS_PENDING, STATUS_REPLACE_PENDING
from src.core.messages import (
    COLUMN_LABELS,
    ColumnId,
    format_action_tooltip,
)
from src.core.smart_action import SmartAction
from src.gui.table.column_filters import ColumnDataType
from src.gui.table.column_layout_state import COL_INDEX_TO_ID
from src.gui.table.column_presets import (
    PRESET_DEFAULT,
    PRESET_QUALITY,
    PRESET_SPECS,
    hidden_columns_for_preset,
)
from src.gui.table.column_sizing import DEFAULT_COL_ORDER, DEFAULT_HIDDEN_COLS
from src.gui.table.numeric_buckets import NumericBucketKind
from src.gui.table.table_cell_tooltips import quality_value_cell_tip
from src.gui.table.table_column_formats import (
    format_quality_value_display,
    keyframe_icon_name,
)
from src.gui.table.table_column_styles import FONT_MONO_BOLD
from src.gui.table.table_columns import COL_DEF_MAP, ENCODE_STATE_LABELS
from src.gui.table.table_model import VideoTableModel
from tests._factories import make_eval

_APP = QApplication.instance() or QApplication([])


class EncodeStateColumnTests(unittest.TestCase):
    """인코딩 상태 열의 머리글, 필터 선택지, 툴팁과 위치를 검증"""

    def test_state_column_is_defined_after_the_reason_column_in_the_default_order(self) -> None:
        """상태 열이 판정 사유 열 바로 뒤에 놓이도록 열 번호와 머리글이 맞는지 검증"""
        self.assertEqual(VideoTableModel.COL_ENCODE_STATE, 25)
        self.assertIn(VideoTableModel.COL_ENCODE_STATE, COL_DEF_MAP)
        self.assertEqual(COL_INDEX_TO_ID[VideoTableModel.COL_ENCODE_STATE], ColumnId.ENCODE_STATE)

    def test_header_comes_from_the_shared_label_table(self) -> None:
        """머리글 짧은 이름과 풀네임이 열 이름 기준 정의에서 오는지 검증"""
        col_def = COL_DEF_MAP[VideoTableModel.COL_ENCODE_STATE]
        self.assertEqual(col_def.header, COLUMN_LABELS[ColumnId.ENCODE_STATE].short)
        self.assertEqual(col_def.menu_title, COLUMN_LABELS[ColumnId.ENCODE_STATE].full)

    def test_filter_choices_are_the_approved_status_labels(self) -> None:
        """열 필터 선택지가 승인된 상태 표기 목록이고 일시중지 표기를 포함하는지 검증"""
        spec = VideoTableModel.get_column_spec(VideoTableModel.COL_ENCODE_STATE)
        self.assertIs(spec.data_type, ColumnDataType.ENUM)
        self.assertEqual(spec.preset_values, ENCODE_STATE_LABELS)
        self.assertEqual(ENCODE_STATE_LABELS[0], STATUS_PENDING)
        self.assertEqual(ENCODE_STATE_LABELS[-1], STATUS_REPLACE_PENDING)
        self.assertIn("진행 중 (일시중지)", ENCODE_STATE_LABELS)

    def test_filter_value_is_empty_because_the_state_is_batch_dependent(self) -> None:
        """상태 값이 배치 진행에 따라 달라서 행 값만으로 필터 값을 만들지 않는지 검증"""
        col_def = COL_DEF_MAP[VideoTableModel.COL_ENCODE_STATE]
        self.assertIsNone(col_def.filter_value(make_eval()))

    def test_tooltip_describes_the_column_in_polite_korean(self) -> None:
        """머리글 툴팁이 승인된 문장이고 존댓말로 끝나는지 검증"""
        tip = COL_DEF_MAP[VideoTableModel.COL_ENCODE_STATE].tooltip_factory(VideoTableModel().config)
        self.assertEqual(tip, "인코딩 배치에서 이 영상의 상태입니다. 배치 대상이 아니면 비어 있습니다.")

    def test_header_keeps_the_full_state_name(self) -> None:
        """좁은 폭에서도 인코딩 상태 이름을 줄이지 않고 그대로 보이는지 검증"""
        self.assertEqual(COLUMN_LABELS[ColumnId.ENCODE_STATE].short, "인코딩 상태")
        self.assertEqual(COL_DEF_MAP[VideoTableModel.COL_ENCODE_STATE].header, "인코딩 상태")

    def test_keyframe_column_declares_the_warning_icon_getter(self) -> None:
        """키프레임 열이 상태 문자열 대신 아이콘 게터로 경고를 표시하는지 검증"""
        self.assertIs(COL_DEF_MAP[VideoTableModel.COL_KEYFRAME].icon_getter, keyframe_icon_name)

    def test_emphasis_columns_use_bold_monospace(self) -> None:
        """활성 화면과 출력 해상도 열이 굵은 모노 글꼴을 쓰는지 검증"""
        for col in (VideoTableModel.COL_OUTPUT_RES, VideoTableModel.COL_ACTIVE_FRAME):
            with self.subTest(col=col):
                self.assertEqual(COL_DEF_MAP[col].font_kind, FONT_MONO_BOLD)


class FfmpegArgsColumnTests(unittest.TestCase):
    """FFmpeg 인자 열의 위치, 기본 숨김, 머리글과 툴팁을 검증"""

    def test_column_is_appended_last_and_hidden_by_default(self) -> None:
        """인자 열이 마지막 열이고 기본 숨김인지 검증"""
        self.assertEqual(VideoTableModel.COL_FFMPEG_ARGS, 26)
        self.assertEqual(len(COL_DEF_MAP), 28)
        self.assertEqual(COL_INDEX_TO_ID[VideoTableModel.COL_FFMPEG_ARGS], ColumnId.FFMPEG_ARGS)
        self.assertIn(VideoTableModel.COL_FFMPEG_ARGS, DEFAULT_HIDDEN_COLS)
        self.assertEqual(
            DEFAULT_COL_ORDER[-1], VideoTableModel.COL_FFMPEG_ARGS
        )


class QualityValueColumnTests(unittest.TestCase):
    """품질값 열의 위치, 기본 표시, 수치 스펙, 표시 문구와 툴팁을 검증"""

    def test_column_is_visible_right_after_the_action_column(self) -> None:
        """품질값 열이 권장 처리 바로 뒤에 기본 표시로 놓이는지 검증"""
        self.assertEqual(VideoTableModel.COL_QUALITY_VALUE, 27)
        self.assertEqual(
            COL_INDEX_TO_ID[VideoTableModel.COL_QUALITY_VALUE], ColumnId.QUALITY_VALUE
        )
        order = DEFAULT_COL_ORDER.index(VideoTableModel.COL_QUALITY_VALUE)
        self.assertEqual(DEFAULT_COL_ORDER[order - 1], VideoTableModel.COL_ACTION)
        self.assertNotIn(VideoTableModel.COL_QUALITY_VALUE, DEFAULT_HIDDEN_COLS)

    def test_column_is_numeric_without_buckets(self) -> None:
        """품질값 열이 수치 범위 필터를 쓰되 구간 버킷은 두지 않는지 검증"""
        spec = VideoTableModel.get_column_spec(VideoTableModel.COL_QUALITY_VALUE)
        self.assertIs(spec.data_type, ColumnDataType.NUMBER)
        self.assertIs(spec.bucket_kind, NumericBucketKind.NONE)
        self.assertEqual(spec.decimals, 1)
        self.assertTrue(spec.nullable_label)

    def test_display_and_filter_values_split_kind_and_number(self) -> None:
        """인코딩 대상은 품질값 숫자, 보존 행은 표시만 -이고 필터 값은 비어 있는지 검증"""
        target = make_eval()
        kept = make_eval(smart_action=SmartAction.PASS_OPTIMAL)
        self.assertEqual(
            COL_DEF_MAP[VideoTableModel.COL_QUALITY_VALUE].filter_value(target), 22.0
        )
        self.assertIsNone(COL_DEF_MAP[VideoTableModel.COL_QUALITY_VALUE].filter_value(kept))
        self.assertEqual(format_quality_value_display(target, VideoTableModel().config), "22.0")
        self.assertEqual(format_quality_value_display(kept, VideoTableModel().config), "-")
        self.assertEqual(target.action_label, "품질 인코딩")

    def test_tooltips_explain_the_value_without_a_number_in_the_kind(self) -> None:
        """머리글 툴팁이 값의 뜻과 변동 가능성을 안내하고 권장 처리 툴팁에 숫자가 없는지 검증"""
        cfg = VideoTableModel().config
        header_tip = COL_DEF_MAP[VideoTableModel.COL_QUALITY_VALUE].tooltip_factory(cfg)
        self.assertIn("계획", header_tip)
        self.assertIn("정밀 결정", header_tip)
        self.assertTrue(header_tip.endswith("표시합니다."))
        self.assertNotIn("품질 22.0 인코딩", format_action_tooltip(cfg))
        self.assertIn("품질값 열", format_action_tooltip(cfg))

    def test_cell_tooltip_states_the_planned_value_or_the_kept_reason(self) -> None:
        """품질값 셀 툴팁이 계획 값과 보존 안내를 구분하는지 검증"""
        self.assertIn("22.0", quality_value_cell_tip(make_eval()))
        kept_tip = quality_value_cell_tip(make_eval(smart_action=SmartAction.PASS_OPTIMAL))
        self.assertIn("품질값이 없습니다", kept_tip)

    def test_header_comes_from_the_shared_label_table(self) -> None:
        """머리글이 좁은 이름과 풀네임 기준 정의를 따르는지 검증"""
        col_def = COL_DEF_MAP[VideoTableModel.COL_FFMPEG_ARGS]
        self.assertEqual(col_def.header, COLUMN_LABELS[ColumnId.FFMPEG_ARGS].short)
        self.assertEqual(col_def.menu_title, COLUMN_LABELS[ColumnId.FFMPEG_ARGS].full)
        self.assertEqual(COLUMN_LABELS[ColumnId.FFMPEG_ARGS].short, "인자")

    def test_no_preset_shows_the_column(self) -> None:
        """프리셋 어디에도 인자 열을 넣지 않아 사용자가 직접 켜야 함을 검증"""
        for preset_key in (PRESET_DEFAULT, PRESET_QUALITY, PRESET_SPECS):
            with self.subTest(preset=preset_key):
                self.assertIn(
                    VideoTableModel.COL_FFMPEG_ARGS, hidden_columns_for_preset(preset_key)
                )

    def test_tooltip_is_polite_and_mentions_the_command(self) -> None:
        """머리글 툴팁이 명령줄 안내를 담고 존댓말로 끝나는지 검증"""
        tip = COL_DEF_MAP[VideoTableModel.COL_FFMPEG_ARGS].tooltip_factory(
            VideoTableModel().config
        )
        self.assertIn("명령줄", tip)
        self.assertTrue(tip.endswith("."))


if __name__ == "__main__":
    unittest.main(verbosity=2)
