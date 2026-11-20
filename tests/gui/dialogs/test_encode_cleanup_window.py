"""재인코딩 임시 파일 정리 창과 확인 대화상자 검증: 묶음 표시, 체크 가능 여부, 요약, 옮기기 확인 한 번, 더블클릭 위치 열기

- 옮기기 결과는 대역 콜백으로 받고, 위치 열기는 탐색기를 건드리지 않도록 대역으로 바꿈
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QTreeWidgetItem

from src.core.encode.encode_leftovers import BlockReason, LeftoverGroup, LeftoverVerdict
from src.core.encode.encode_progress_values import bytes_to_mb, size_text
from src.gui.dialogs import encode_cleanup_window as window_module
from src.gui.dialogs.encode_cleanup_window import (
    BLOCK_LABELS,
    BLOCK_TEXTS,
    EMPTY_TEXT,
    REASON_COLUMN,
    CleanupWindow,
    ask_part_trash,
    ask_permanent,
    ask_trash,
)
from src.service.encode.encode_leftovers import CleanupOutcome, LeftoverItem

_APP = QApplication.instance() or QApplication([])
ROOT: str = os.path.join(os.sep, "work")
MTIME_NS: int = 1_600_000_000_000_000_000


def _item(name: str, verdict: LeftoverVerdict, size: int = 2 * 1024 * 1024) -> LeftoverItem:
    return LeftoverItem(os.path.join(ROOT, name), ROOT, size, MTIME_NS, verdict)


def _rows(window: CleanupWindow) -> list[QTreeWidgetItem]:
    rows: list[QTreeWidgetItem] = []
    for index in range(window._tree.topLevelItemCount()):
        group = window._tree.topLevelItem(index)
        assert group is not None
        for child in range(group.childCount()):
            row = group.child(child)
            assert row is not None
            rows.append(row)
    return rows


def _row_named(window: CleanupWindow, name: str) -> QTreeWidgetItem:
    matches = [row for row in _rows(window) if row.text(0) == name]
    assert len(matches) == 1
    return matches[0]


class CleanupWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.recorded = _item("recorded.mp4.part", LeftoverVerdict(LeftoverGroup.RECORDED, None))
        self.unknown = _item("stray.part", LeftoverVerdict(LeftoverGroup.UNKNOWN, None))
        self.blocked = _item(
            "replace.mp4.part", LeftoverVerdict(LeftoverGroup.BLOCKED, BlockReason.REPLACE)
        )
        self.calls: list[list[LeftoverItem]] = []

    def _window(self, items: list[LeftoverItem], outcomes: list[CleanupOutcome] | None = None) -> CleanupWindow:
        def on_trash(selected: list[LeftoverItem]) -> list[CleanupOutcome]:
            self.calls.append(selected)
            if outcomes is not None:
                return outcomes
            return [CleanupOutcome(item.path, True) for item in selected]

        window = CleanupWindow(items, on_trash)
        self.addCleanup(window.deleteLater)
        return window

    def test_groups_show_selectable_rows_with_checkboxes_and_blocked_rows_with_reasons(self) -> None:
        """묶음마다 머리글을 두고, 고를 수 있는 파일만 체크 상자를 주며, 고를 수 없는 파일은 이유를 툴팁으로 보임"""
        window = self._window([self.recorded, self.unknown, self.blocked])
        self.assertEqual(window._tree.topLevelItemCount(), 3)
        recorded_row = _row_named(window, "recorded.mp4.part")
        self.assertTrue(recorded_row.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        blocked_row = _row_named(window, "replace.mp4.part")
        self.assertFalse(blocked_row.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertEqual(blocked_row.toolTip(0), BLOCK_TEXTS[BlockReason.REPLACE])

    def test_blocked_rows_show_the_reason_in_their_own_column_and_selectable_rows_leave_it_empty(self) -> None:
        """고를 수 없는 행은 이유 칸에 짧은 이유를 바로 보이고, 고를 수 있는 행은 그 칸을 비움"""
        window = self._window([self.recorded, self.blocked])
        self.assertEqual(_row_named(window, "replace.mp4.part").text(REASON_COLUMN), BLOCK_LABELS[BlockReason.REPLACE])
        self.assertEqual(_row_named(window, "recorded.mp4.part").text(REASON_COLUMN), "")

    def test_empty_list_shows_the_empty_notice_row(self) -> None:
        """정리할 파일이 없는 창은 빈 상태 문구를 보임"""
        window = self._window([])
        top = window._tree.topLevelItem(0)
        assert top is not None
        self.assertEqual(top.text(0), EMPTY_TEXT)

    def test_summary_counts_only_checked_rows_and_their_size(self) -> None:
        """요약은 체크된 파일 수와 용량 합계를 보여 주고, 체크를 바꾸면 갱신됨"""
        window = self._window([self.recorded, self.unknown])
        self.assertEqual([item.path for item in window.checked_items()], [self.recorded.path])
        self.assertIn("선택 1개", window._summary.text())
        _row_named(window, "stray.part").setCheckState(0, Qt.CheckState.Checked)
        self.assertEqual(len(window.checked_items()), 2)
        self.assertIn("선택 2개", window._summary.text())
        self.assertIn(size_text(bytes_to_mb(self.recorded.size + self.unknown.size)), window._summary.text())

    def test_recorded_and_own_result_files_start_checked_and_unknown_files_do_not(self) -> None:
        """기록된 파일과 결과로 보이는 파일은 처음부터 체크되고, 출처를 알 수 없는 파일은 해제 상태로 둠"""
        own = _item("own.mp4.part", LeftoverVerdict(LeftoverGroup.OWN_RESULT, None))
        window = self._window([self.recorded, self.unknown, own])
        self.assertEqual(
            sorted(item.path for item in window.checked_items()), sorted([self.recorded.path, own.path])
        )

    def test_trash_button_asks_once_then_removes_moved_rows_from_the_list(self) -> None:
        """옮기기 버튼은 확인 창을 한 번 띄우고, 옮긴 파일만 목록에서 빼며 건너뛴 파일은 남김"""
        outcomes = [CleanupOutcome(self.recorded.path, True), CleanupOutcome(self.unknown.path, False, "사유")]
        window = self._window([self.recorded, self.unknown], outcomes=outcomes)
        _row_named(window, "recorded.mp4.part").setCheckState(0, Qt.CheckState.Checked)
        _row_named(window, "stray.part").setCheckState(0, Qt.CheckState.Checked)
        trash_button = _button(window, "휴지통으로 이동")
        with mock.patch.object(window_module, "ask_trash", return_value=True) as ask, mock.patch.object(
            window_module.QMessageBox, "information"
        ) as notice:
            trash_button.click()
        ask.assert_called_once()
        self.assertEqual(len(self.calls[0]), 2)
        names = [row.text(0) for row in _rows(window)]
        self.assertNotIn("recorded.mp4.part", names)
        self.assertIn("stray.part", names)
        notice.assert_called_once()

    def test_declined_trash_does_not_call_the_callback(self) -> None:
        """확인 창에서 취소하면 옮기기 콜백을 부르지 않음"""
        window = self._window([self.recorded])
        _row_named(window, "recorded.mp4.part").setCheckState(0, Qt.CheckState.Checked)
        with mock.patch.object(window_module, "ask_trash", return_value=False):
            _button(window, "휴지통으로 이동").click()
        self.assertEqual(self.calls, [])

    def test_toolbar_reveal_opens_the_checked_rows_together(self) -> None:
        """툴바의 위치 열기는 체크된 파일을 한 번의 위치 열기 요청으로 넘김"""
        window = self._window([self.recorded])
        _row_named(window, "recorded.mp4.part").setCheckState(0, Qt.CheckState.Checked)
        toolbar = [
            b for b in window.findChildren(QPushButton) if b.accessibleName() == "위치 열기" and b.parent() is window
        ]
        self.assertEqual(len(toolbar), 1)
        with mock.patch.object(window_module, "reveal_paths", return_value=0) as reveal:
            toolbar[0].click()
        reveal.assert_called_once_with([self.recorded.path])

    def test_double_click_reveals_the_row_file_in_explorer(self) -> None:
        """행을 더블클릭하면 그 파일의 위치를 탐색기에서 선택 표시"""
        window = self._window([self.recorded])
        row = _row_named(window, "recorded.mp4.part")
        with mock.patch.object(window_module, "reveal_paths", return_value=0) as reveal:
            window._tree.itemDoubleClicked.emit(row, 0)
        reveal.assert_called_once_with([self.recorded.path])


class ConfirmDialogTest(unittest.TestCase):
    def test_closing_the_confirm_without_a_choice_means_cancel(self) -> None:
        """확인 창을 선택 없이 닫으면 세 확인 모두 거짓. 기본 포커스와 Esc는 취소"""
        with mock.patch.object(QMessageBox, "exec", return_value=0), mock.patch.object(
            QMessageBox, "clickedButton", return_value=None
        ):
            self.assertFalse(ask_trash(None, 2, 10))
            self.assertFalse(ask_permanent(None))
            self.assertFalse(ask_part_trash(None, "a.mp4.part"))


def _button(window: CleanupWindow, accessible_name: str) -> QPushButton:
    buttons = [b for b in window.findChildren(QPushButton) if b.accessibleName() == accessible_name]
    assert len(buttons) == 1
    return buttons[0]


if __name__ == "__main__":
    unittest.main(verbosity=2)
