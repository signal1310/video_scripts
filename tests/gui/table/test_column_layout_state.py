"""열 상태 JSON 직렬화, 복원 규칙과 테이블 적용 결과를 검증"""

import json
import os
import unittest

from PyQt6.QtWidgets import QApplication

from src.core.messages import ColumnId
from src.gui.table.column_layout_state import (
    COL_INDEX_TO_ID,
    ColumnLayout,
    apply_column_layouts,
    capture_column_layouts,
    decode_column_layouts,
    encode_column_layouts,
)
from src.gui.table.table_model import VideoTableModel
from src.gui.table.video_table_view import VideoTableView
from tests._gui_helpers import GuiTestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def _sample_layouts() -> dict[ColumnId, ColumnLayout]:
    """기본 순서와 숨김 상태를 따르는 전체 열 상태 샘플"""
    return {
        COL_INDEX_TO_ID[col]: ColumnLayout(
            order=order,
            width=60 + order,
            hidden=col in VideoTableView.DEFAULT_HIDDEN_COLS,
        )
        for order, col in enumerate(VideoTableView.DEFAULT_COL_ORDER)
    }


class ColumnLayoutCodecTests(unittest.TestCase):
    def test_mapping_covers_every_table_column_once(self) -> None:
        """열 인덱스와 ColumnId 대응이 테이블의 모든 열을 중복 없이 덮는지 검증"""
        self.assertEqual(len(COL_INDEX_TO_ID), len(VideoTableModel.HEADERS))
        self.assertEqual(len(set(COL_INDEX_TO_ID.values())), len(COL_INDEX_TO_ID))
        self.assertEqual(set(COL_INDEX_TO_ID), set(range(len(VideoTableModel.HEADERS))))

    def test_round_trip_keeps_every_field(self) -> None:
        """직렬화 후 역직렬화하면 순서, 폭, 숨김 상태가 그대로 복원됨을 검증"""
        layouts = _sample_layouts()
        self.assertEqual(decode_column_layouts(encode_column_layouts(layouts)), layouts)

    def test_unknown_column_key_is_ignored(self) -> None:
        """현재 버전에 없는 열 키는 무시하고 나머지 열 설정은 보존함을 검증"""
        payload = json.loads(encode_column_layouts(_sample_layouts()))
        payload["columns"]["future_column"] = {"order": 0, "width": 10, "hidden": False}
        decoded = decode_column_layouts(json.dumps(payload))
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded, _sample_layouts())

    def test_missing_column_key_is_allowed(self) -> None:
        """저장되지 않은 열은 빠진 채로 복원해 이후 적용 단계에서 기본값을 쓰게 함을 검증"""
        layouts = _sample_layouts()
        del layouts[ColumnId.ACTION]
        decoded = decode_column_layouts(encode_column_layouts(layouts))
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertNotIn(ColumnId.ACTION, decoded)

    def test_saved_state_without_new_column_still_decodes(self) -> None:
        """새 열을 추가하기 전에 저장한 상태도 나머지 열 설정을 그대로 복원함을 검증"""
        layouts = _sample_layouts()
        del layouts[ColumnId.FFMPEG_ARGS]
        decoded = decode_column_layouts(encode_column_layouts(layouts))
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertNotIn(ColumnId.FFMPEG_ARGS, decoded)
        self.assertEqual(decoded[ColumnId.NAME], layouts[ColumnId.NAME])

    def test_corrupt_entry_rejects_whole_state(self) -> None:
        """값 형식이 틀린 열 항목이 하나라도 있으면 전체 상태를 무효로 처리함을 검증"""
        payload = json.loads(encode_column_layouts(_sample_layouts()))
        payload["columns"]["name"]["width"] = "wide"
        self.assertIsNone(decode_column_layouts(json.dumps(payload)))

    def test_invalid_or_legacy_text_returns_none(self) -> None:
        """Base64 헤더 상태, 이전 버전 문자열, 깨진 JSON은 복원하지 않음을 검증"""
        for text in ("CCCDDD==", "v1:15:AAAA", '{"version": 1, "columns": {}}', "{", "[]"):
            with self.subTest(text=text):
                self.assertIsNone(decode_column_layouts(text))


class ColumnLayoutApplyTests(GuiTestCase):
    def test_partial_state_applies_saved_columns_and_defaults_the_rest(self) -> None:
        """일부 열만 저장된 상태를 적용하면 저장된 열은 그대로, 나머지는 기본값으로 적용됨을 검증"""
        view = VideoTableView()
        layouts = {
            ColumnId.NAME: ColumnLayout(order=0, width=310, hidden=False),
            ColumnId.SAVED_SIZE: ColumnLayout(order=1, width=120, hidden=True),
        }
        apply_column_layouts(view, layouts)
        header = view.filter_header
        self.assertEqual(header.visualIndex(VideoTableModel.COL_NAME), 0)
        self.assertEqual(header.visualIndex(VideoTableModel.COL_SAVED_SIZE), 1)
        self.assertEqual(view.columnWidth(VideoTableModel.COL_NAME), 310)
        self.assertTrue(view.isColumnHidden(VideoTableModel.COL_SAVED_SIZE))
        self.assertEqual(
            view.isColumnHidden(VideoTableModel.COL_ROTATE),
            VideoTableModel.COL_ROTATE in VideoTableView.DEFAULT_HIDDEN_COLS,
        )

    def test_capture_apply_round_trip_restores_table_state(self) -> None:
        """한 테이블의 열 상태를 저장해 다른 테이블에 적용하면 같은 상태가 됨을 검증"""
        src = VideoTableView()
        src.filter_header.moveSection(
            src.filter_header.visualIndex(VideoTableModel.COL_ACTION), 0
        )
        src.setColumnHidden(VideoTableModel.COL_CODEC, True)
        src.setColumnWidth(VideoTableModel.COL_ACTION, 150)
        encoded = encode_column_layouts(capture_column_layouts(src))

        dst = VideoTableView()
        decoded = decode_column_layouts(encoded)
        self.assertIsNotNone(decoded)
        assert decoded is not None
        apply_column_layouts(dst, decoded)
        self.assertEqual(capture_column_layouts(dst), capture_column_layouts(src))


if __name__ == "__main__":
    unittest.main()
