"""열 편집 되돌리기 라벨 판정과 명령 기록 단위 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.settings.undo_history import UndoableSnapshot
from src.gui.table.column_history import (
    COLUMN_RESET_HISTORY_LABEL,
    COLUMN_VISIBILITY_HISTORY_LABEL,
    ColumnHistoryRecorder,
    describe_column_change,
)
from src.gui.table.column_layout_state import capture_column_state
from src.gui.table.table_model import VideoTableModel
from src.gui.table.video_table_view import VideoTableView

_APP = QApplication.instance() or QApplication([])


def _snapshot(layout: str) -> UndoableSnapshot:
    """열 상태 문자열만 채운 되돌리기 스냅숏"""
    return UndoableSnapshot(
        config=ClassifierConfig(),
        exception_tokens=(),
        disabled_exception_tokens=(),
        manual_target_tiers={},
        column_layout=layout,
    )


class DescribeColumnChangeTests(unittest.TestCase):
    """전후 열 상태로 되돌리기 라벨을 고르는 규칙 검증"""

    def setUp(self) -> None:
        self.view = VideoTableView()
        self.addCleanup(self.view.close)

    def test_hidden_change_wins_over_width_change(self) -> None:
        """표시 변경과 폭 변경이 함께 있으면 열 표시 라벨로 판정"""
        before = capture_column_state(self.view)
        self.view.setColumnHidden(VideoTableModel.COL_CODEC, True)
        self.view.setColumnWidth(VideoTableModel.COL_NAME, 400)
        self.assertEqual(
            describe_column_change(before, capture_column_state(self.view)),
            COLUMN_VISIBILITY_HISTORY_LABEL,
        )

    def test_invalid_state_falls_back_to_the_combined_label(self) -> None:
        """해독할 수 없는 열 상태는 너비, 표시, 순서를 함께 표시하는 라벨로 판정"""
        self.assertEqual(
            describe_column_change("깨진 상태", "깨진 상태"),
            "열 너비, 표시, 순서 변경",
        )

    def test_reset_label_reuses_the_shared_menu_text(self) -> None:
        """초기화 명령 라벨이 메뉴 항목과 같은 문구를 쓰는지 검증"""
        self.assertEqual(COLUMN_RESET_HISTORY_LABEL, "기본 열 너비, 표시, 순서 복원")


class ColumnHistoryRecorderTests(unittest.TestCase):
    """명령 실행과 헤더 편집 구간을 되돌리기 기록으로 넘기는지 검증"""

    def setUp(self) -> None:
        self.view = VideoTableView()
        self.addCleanup(self.view.close)
        self.recorded: list[tuple[str, UndoableSnapshot]] = []
        self.recorder = ColumnHistoryRecorder(
            self.view,
            lambda: _snapshot(capture_column_state(self.view)),
            lambda label, before: self.recorded.append((label, before)),
        )

    def test_run_command_records_the_given_label(self) -> None:
        """메뉴 명령은 실행 뒤 지정한 라벨과 편집 전 상태로 한 번 기록"""
        before_state = capture_column_state(self.view)
        calls: list[str] = []
        self.recorder.run_command(COLUMN_RESET_HISTORY_LABEL, lambda: calls.append("run"))
        self.assertEqual(calls, ["run"])
        self.assertEqual(len(self.recorded), 1)
        label, before = self.recorded[0]
        self.assertEqual(label, COLUMN_RESET_HISTORY_LABEL)
        self.assertEqual(before.column_layout, before_state)

    def test_layout_edit_records_the_derived_label(self) -> None:
        """헤더 편집 구간은 전후 상태 비교로 고른 라벨로 기록"""
        header = self.view.filter_header
        header.layout_edit_started.emit()
        self.view.setColumnHidden(VideoTableModel.COL_CODEC, True)
        header.layout_edit_finished.emit()
        self.assertEqual(len(self.recorded), 1)
        self.assertEqual(self.recorded[0][0], COLUMN_VISIBILITY_HISTORY_LABEL)

    def test_finish_without_start_records_nothing(self) -> None:
        """시작 없이 끝 신호만 오면 기록하지 않음"""
        self.view.filter_header.layout_edit_finished.emit()
        self.assertEqual(self.recorded, [])

    def test_nested_start_keeps_the_earliest_state(self) -> None:
        """편집이 겹치면 가장 이른 시점을 편집 전 상태로 유지"""
        header = self.view.filter_header
        earliest = capture_column_state(self.view)
        header.layout_edit_started.emit()
        self.view.setColumnWidth(VideoTableModel.COL_NAME, 300)
        header.layout_edit_started.emit()
        header.layout_edit_finished.emit()
        self.assertEqual(len(self.recorded), 1)
        self.assertEqual(self.recorded[0][1].column_layout, earliest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
