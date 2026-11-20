"""테이블 열 스키마의 재인코딩 상태 열 정의와 열 식별자 대응을 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.encode.encode_batch_book import STATUS_PENDING, STATUS_REPLACE_PENDING
from src.core.messages import COLUMN_LABELS, ColumnId
from src.gui.table.column_filters import ColumnDataType
from src.gui.table.column_layout_state import COL_INDEX_TO_ID
from src.gui.table.table_columns import COL_DEF_MAP, ENCODE_STATE_LABELS
from src.gui.table.table_model import VideoTableModel
from tests._factories import make_eval

_APP = QApplication.instance() or QApplication([])


class EncodeStateColumnTests(unittest.TestCase):
    """재인코딩 상태 열의 머리글, 필터 선택지, 툴팁과 위치를 검증"""

    def test_state_column_is_defined_after_the_reason_column_in_the_default_order(self) -> None:
        """상태 열이 판정 사유 열 바로 뒤에 놓이도록 열 번호와 머리글이 맞는지 검증"""
        self.assertEqual(VideoTableModel.COL_ENCODE_STATE, 22)
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
        self.assertEqual(tip, "재인코딩 배치에서 이 영상의 상태입니다. 배치 대상이 아니면 비어 있습니다.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
