"""행 초기화 전후 선택 이어 주기와 예약 복원 도우미 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import Any

from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtWidgets import QApplication

from src.core.models import VideoEvaluation
from src.gui.table.table_selection_keeper import PendingSelectionRestore, SelectionKeeper
from src.gui.table.video_table_view import VideoTableView
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])


class _FakeSignal:
    """신호 연결만 기록하는 최소 흉내"""

    def __init__(self) -> None:
        self.slots: list[Any] = []

    def connect(self, slot: Any) -> None:
        self.slots.append(slot)

    def emit(self, *args: Any) -> None:
        for slot in list(self.slots):
            slot(*args)


class _FakeGroupModel:
    """그룹 프록시의 신호와 행 조회를 흉내"""

    def __init__(self, view: "_FakeView") -> None:
        self._view = view
        self.modelAboutToBeReset = _FakeSignal()
        self.modelReset = _FakeSignal()

    def rowCount(self) -> int:
        return len(self._view.rows)

    def is_group_row(self, row: int) -> bool:
        _ = row
        return False

    def index(self, row: int, column: int) -> tuple[int, int]:
        return (row, column)


class _FakeTableModel:
    """작업 폴더 전환을 알리는 최소 원본 모델 흉내"""

    def __init__(self) -> None:
        self.root_changed = _FakeSignal()


class _FakeView:
    """선택 이어 주기 도우미가 쓰는 뷰 인터페이스 최소 모형"""

    def __init__(self, rows: list[VideoEvaluation] | None = None) -> None:
        self.rows = list(rows or [])
        self.selected: set[str] = set()
        self.pending: set[str] | None = None
        self.focused: str = ""
        self.sync_depth = 0
        self.selected_rows: list[int] = []
        self.current_row: int | None = None
        self.group_model = _FakeGroupModel(self)
        self.table_model = _FakeTableModel()

    def _row_of(self, filename: str) -> int | None:
        for row, ev in enumerate(self.rows):
            if ev.video.filename == filename:
                return row
        return None

    def get_selected_filenames(self) -> set[str]:
        return set(self.selected)

    def get_evaluation_at(self, index: tuple[int, int] | None) -> VideoEvaluation | None:
        if index is None or not 0 <= index[0] < len(self.rows):
            return None
        return self.rows[index[0]]

    def currentIndex(self) -> tuple[int, int] | None:
        row = self._row_of(self.focused)
        return None if row is None else (row, 0)

    def selectionModel(self) -> "_FakeView":
        return self

    def set_selection_sync_guard(self, active: bool) -> None:
        self.sync_depth += 1 if active else -1

    def is_selection_syncing(self) -> bool:
        return self.sync_depth > 0

    def has_pending_selection(self) -> bool:
        return self.pending is not None

    def select_filenames(self, filenames: set[str]) -> None:
        self.selected = set(filenames)

    def clearSelection(self) -> None:
        self.selected_rows = []

    def select(self, index: tuple[int, int], _flags: Any) -> None:
        self.selected_rows.append(index[0])

    def setCurrentIndex(self, index: tuple[int, int], _flags: Any) -> None:
        self.current_row = index[0]


def _eval(filename: str) -> VideoEvaluation:
    """지정 파일명의 최소 평가 결과"""
    return make_eval(video=make_video(filename=filename, codec="h264"))


class SelectionKeeperTests(unittest.TestCase):
    """캡처, 해제, 복원 동작 검증"""

    def setUp(self) -> None:
        self.view = _FakeView([_eval("a.mp4"), _eval("b.mp4")])
        self.keeper = SelectionKeeper(self.view)

    def test_connects_signals_on_init(self) -> None:
        self.assertEqual(len(self.view.group_model.modelAboutToBeReset.slots), 1)
        self.assertEqual(len(self.view.group_model.modelReset.slots), 1)
        self.assertEqual(len(self.view.table_model.root_changed.slots), 1)

    def test_capture_records_selection_and_current(self) -> None:
        self.view.selected = {"a.mp4", "b.mp4"}
        self.view.focused = "a.mp4"
        self.keeper.capture()
        self.assertEqual(self.keeper.filenames, {"a.mp4", "b.mp4"})
        self.assertEqual(self.keeper.current, "a.mp4")

    def test_capture_skips_when_pending_restore(self) -> None:
        self.view.pending = {"x.mp4"}
        self.view.selected = {"a.mp4"}
        self.keeper.capture()
        self.assertIsNone(self.keeper.filenames)
        self.assertEqual(self.keeper.current, "")

    def test_capture_clears_stale_drop_flag_when_pending_restore(self) -> None:
        """예약 복원이 있는 초기화는 앞선 해제 표시를 다음 복원으로 넘기지 않는지 검증"""
        self.keeper.drop()
        self.view.pending = {"x.mp4"}
        self.keeper.capture()
        self.assertFalse(self.keeper.dropping)

    def test_drop_clears_and_sets_flag(self) -> None:
        self.view.selected = {"a.mp4"}
        self.keeper.capture()
        self.keeper.drop()
        self.assertTrue(self.keeper.dropping)
        self.assertIsNone(self.keeper.filenames)
        self.assertEqual(self.keeper.current, "")

    def test_restore_reselects_filenames_and_current(self) -> None:
        self.view.selected = {"a.mp4"}
        self.view.focused = "b.mp4"
        self.keeper.capture()
        self.view.selected = set()
        self.keeper.restore()
        self.assertEqual(self.view.selected_rows, [0])
        self.assertEqual(self.view.current_row, 1)
        self.assertEqual(self.view.sync_depth, 0)
        self.assertIsNone(self.keeper.filenames)
        self.assertEqual(self.keeper.current, "")

    def test_restore_skipped_after_drop(self) -> None:
        self.view.selected = {"a.mp4"}
        self.keeper.capture()
        self.keeper.drop()
        self.keeper.restore()
        self.assertEqual(self.view.selected_rows, [])
        self.assertFalse(self.keeper.dropping)

    def test_restore_skipped_when_pending_restore(self) -> None:
        self.view.selected = {"a.mp4"}
        self.keeper.capture()
        self.view.pending = {"x.mp4"}
        self.keeper.restore()
        self.assertEqual(self.view.selected_rows, [])

    def test_restore_skipped_when_nothing_captured(self) -> None:
        self.keeper.restore()
        self.assertEqual(self.view.selected_rows, [])

    def test_restore_without_selection_model(self) -> None:
        """선택 모델이 없으면 복원을 건너뛰는지 검증"""
        self.view.selected = {"a.mp4"}
        self.keeper.capture()
        self.view.selectionModel = lambda: None  # type: ignore[method-assign]
        self.keeper.restore()
        self.assertEqual(self.view.sync_depth, 0)

    def test_rows_of_filenames_lists_video_rows(self) -> None:
        self.assertEqual(self.keeper.rows_of_filenames(), {"a.mp4": 0, "b.mp4": 1})

    def test_select_filenames_replaces_selection(self) -> None:
        self.keeper.select_filenames({"b.mp4"})
        self.assertEqual(self.view.selected_rows, [1])
        self.assertEqual(self.view.sync_depth, 0)

    def test_select_filenames_without_selection_model(self) -> None:
        """선택 모델이 없으면 아무 것도 하지 않는지 검증"""
        self.view.selectionModel = lambda: None  # type: ignore[method-assign]
        self.keeper.select_filenames({"a.mp4"})
        self.assertEqual(self.view.selected_rows, [])


class PendingSelectionRestoreTests(unittest.TestCase):
    """예약 복원 요청, 소비, 적용 검증"""

    def setUp(self) -> None:
        self.view = _FakeView([_eval("a.mp4"), _eval("b.mp4")])
        self.pending = PendingSelectionRestore(self.view)

    def test_request_ignores_empty_names(self) -> None:
        self.pending.request(["", "a.mp4"])
        self.assertTrue(self.pending.has_pending())
        self.assertEqual(self.pending.take(), {"a.mp4"})
        self.assertFalse(self.pending.has_pending())

    def test_request_without_valid_names_clears_pending(self) -> None:
        """유효한 파일명이 없으면 예약을 남기지 않는지 검증"""
        self.pending.request(["", ""])
        self.assertFalse(self.pending.has_pending())
        self.assertIsNone(self.pending.take())

    def test_apply_selects_reserved_names(self) -> None:
        self.pending.request({"b.mp4"})
        self.pending.apply()
        self.assertEqual(self.view.selected, {"b.mp4"})
        self.assertFalse(self.pending.has_pending())

    def test_apply_skipped_while_selection_syncing(self) -> None:
        self.pending.request({"b.mp4"})
        self.view.set_selection_sync_guard(True)
        self.pending.apply()
        self.assertTrue(self.pending.has_pending())
        self.assertEqual(self.view.selected, set())

    def test_apply_skipped_without_rows(self) -> None:
        self.pending.request({"b.mp4"})
        self.view.rows = []
        self.pending.apply()
        self.assertTrue(self.pending.has_pending())


class CurrentVideoSignalTests(unittest.TestCase):
    """선택 복원 중 미리 보기 알림을 내지 않는지 검증"""

    def setUp(self) -> None:
        self.view = VideoTableView()
        self.addCleanup(self.view.close)
        self.view.table_model.set_data_source(
            [_eval("a.mp4"), _eval("b.mp4")], root_dir="C:/work"
        )

    def _select_row_of(self, filename: str) -> None:
        for row in range(self.view.group_model.rowCount()):
            index = self.view.group_model.index(row, 0)
            ev = self.view.get_evaluation_at(index)
            if ev is not None and ev.video.filename == filename:
                sel_model = self.view.selectionModel()
                assert sel_model is not None
                sel_model.setCurrentIndex(
                    index,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                return
        raise AssertionError(filename)

    def test_restore_does_not_emit_current_video_changed(self) -> None:
        """행 초기화 뒤 선택 복원이 미리 보기 알림을 내지 않는지 검증"""
        self._select_row_of("b.mp4")
        emitted: list[int] = []
        self.view.current_video_changed.connect(lambda: emitted.append(1))
        keeper = self.view._selection_keeper
        keeper.capture()
        keeper.restore()
        self.assertEqual(emitted, [])
        self.assertEqual(self.view.get_selected_filenames(), {"b.mp4"})

    def test_sync_guard_suppresses_current_video_changed(self) -> None:
        """선택 동기화 보호 중의 포커스 이동은 미리 보기 알림을 내지 않는지 검증"""
        self._select_row_of("a.mp4")
        emitted: list[int] = []
        self.view.current_video_changed.connect(lambda: emitted.append(1))
        sel_model = self.view.selectionModel()
        assert sel_model is not None
        self.view.set_selection_sync_guard(True)
        try:
            sel_model.setCurrentIndex(
                self.view.group_model.index(1, 0),
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        finally:
            self.view.set_selection_sync_guard(False)
        self.assertEqual(emitted, [])

    def test_current_change_outside_sync_emits_signal(self) -> None:
        """보호 밖의 포커스 이동은 그대로 미리 보기 알림을 내는지 검증"""
        emitted: list[int] = []
        self.view.current_video_changed.connect(lambda: emitted.append(1))
        self._select_row_of("a.mp4")
        self.assertEqual(emitted, [1])

    def test_pending_restore_wrappers_delegate(self) -> None:
        """뷰의 예약 복원 메서드가 도우미로 그대로 이어지는지 검증"""
        self.assertFalse(self.view.has_pending_selection())
        self.view.request_selection_restore(["a.mp4", ""])
        self.assertTrue(self.view.has_pending_selection())
        self.assertEqual(self.view.take_pending_selection(), {"a.mp4"})
        self.assertFalse(self.view.has_pending_selection())

    def test_select_filenames_selects_matching_rows(self) -> None:
        """뷰의 파일명 선택이 도우미를 거쳐 같은 행을 선택하는지 검증"""
        self.assertFalse(self.view.is_selection_syncing())
        self.view.select_filenames({"b.mp4"})
        self.assertEqual(self.view.get_selected_filenames(), {"b.mp4"})
        self.assertFalse(self.view.is_selection_syncing())


if __name__ == "__main__":
    unittest.main()
