"""열 상태 JSON 직렬화, 복원 규칙과 테이블 적용 결과를 검증"""

import json
import os
import unittest

from PyQt6.QtWidgets import QApplication

from src.core.messages import ColumnId
from src.gui.constants import PRESET_SPECS
from src.gui.table.column_layout_state import (
    COL_INDEX_TO_ID,
    NO_STORED_WIDTH,
    ColumnLayout,
    apply_column_layouts,
    apply_column_widths,
    capture_column_layouts,
    decode_column_layouts,
    encode_column_layouts,
)
from src.gui.table.column_presets import apply_preset_default
from src.gui.table.column_sizing import DEFAULT_COL_ORDER
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

    def test_encode_without_widths_records_the_placeholder(self) -> None:
        """폭을 빼고 직렬화하면 순서와 표시는 남고 폭은 자리 표시 값이 됨을 검증"""
        layouts = _sample_layouts()
        decoded = decode_column_layouts(
            encode_column_layouts(layouts, include_widths=False)
        )
        assert decoded is not None
        self.assertEqual(set(decoded), set(layouts))
        for cid, layout in layouts.items():
            self.assertEqual(decoded[cid].order, layout.order)
            self.assertEqual(decoded[cid].hidden, layout.hidden)
            self.assertEqual(decoded[cid].width, NO_STORED_WIDTH)

    def test_apply_without_widths_keeps_the_current_column_widths(self) -> None:
        """폭을 빼고 적용하면 저장된 순서, 표시만 바뀌고 지금 열 폭은 그대로임을 검증"""
        view = VideoTableView()
        view.setColumnWidth(VideoTableModel.COL_CODEC, 155)
        name_width = view.columnWidth(VideoTableModel.COL_NAME)
        view.filter_header.moveSection(
            view.filter_header.visualIndex(VideoTableModel.COL_ACTION), 0
        )
        layouts = {
            ColumnId.NAME: ColumnLayout(order=0, width=310, hidden=False),
            ColumnId.SAVED_SIZE: ColumnLayout(order=1, width=120, hidden=True),
        }
        apply_column_layouts(view, layouts, apply_widths=False)
        header = view.filter_header
        self.assertEqual(header.visualIndex(VideoTableModel.COL_NAME), 0)
        self.assertEqual(header.visualIndex(VideoTableModel.COL_SAVED_SIZE), 1)
        self.assertTrue(view.isColumnHidden(VideoTableModel.COL_SAVED_SIZE))
        self.assertEqual(view.columnWidth(VideoTableModel.COL_CODEC), 155)
        self.assertEqual(view.columnWidth(VideoTableModel.COL_NAME), name_width)
        # - 되돌린 폭은 사용자 조절 폭으로 간주해 내용 폭 재계산에서 줄이지 않음
        self.assertIn(VideoTableModel.COL_CODEC, view._user_resized_cols)

    def test_apply_widths_only_changes_column_widths(self) -> None:
        """폭만 적용하면 지금 열 순서와 표시는 그대로이고 저장된 폭만 반영됨을 검증"""
        view = VideoTableView()
        apply_preset_default(view, PRESET_SPECS)
        order = [
            view.filter_header.logicalIndex(index)
            for index in range(view.table_model.columnCount())
        ]
        layouts = {
            ColumnId.NAME: ColumnLayout(order=0, width=310, hidden=False),
            ColumnId.ACTION: ColumnLayout(order=1, width=120, hidden=False),
        }
        apply_column_widths(view, layouts)
        self.assertEqual(view.columnWidth(VideoTableModel.COL_NAME), 310)
        self.assertFalse(view.isColumnHidden(VideoTableModel.COL_CODEC))
        self.assertEqual(
            [
                view.filter_header.logicalIndex(index)
                for index in range(view.table_model.columnCount())
            ],
            order,
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

    def test_old_saved_state_without_the_quality_value_column_still_applies(self) -> None:
        """품질값 열을 추가하기 전에 저장한 상태를 적용해도 기본 위치, 표시, 폭으로 채워짐을 검증"""
        old_payload = {
            "version": 2,
            "columns": {
                "name": {"order": 0, "width": 300, "hidden": False},
                "action": {"order": 1, "width": 150, "hidden": False},
                "reason": {"order": 2, "width": 110, "hidden": False},
            },
        }
        decoded = decode_column_layouts(json.dumps(old_payload))
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertNotIn(ColumnId.QUALITY_VALUE, decoded)

        view = VideoTableView()
        apply_column_layouts(view, decoded)
        header = view.filter_header
        self.assertEqual(header.visualIndex(VideoTableModel.COL_NAME), 0)
        self.assertEqual(header.visualIndex(VideoTableModel.COL_ACTION), 1)
        self.assertEqual(header.visualIndex(VideoTableModel.COL_REASON), 2)
        self.assertFalse(view.isColumnHidden(VideoTableModel.COL_QUALITY_VALUE))
        self.assertGreater(view.columnWidth(VideoTableModel.COL_QUALITY_VALUE), 0)
        # - 저장되지 않은 열은 기본 순서대로 뒤에 붙으므로 품질값이 누락 열의 첫 자리를 차지함
        self.assertEqual(header.visualIndex(VideoTableModel.COL_QUALITY_VALUE), 3)
        # - 화질 등급도 저장되지 않은 열이라 품질값 다음 기본 순서 자리에 놓임
        self.assertEqual(
            header.visualIndex(VideoTableModel.COL_ZONE),
            DEFAULT_COL_ORDER.index(VideoTableModel.COL_ZONE),
        )


if __name__ == "__main__":
    unittest.main()
