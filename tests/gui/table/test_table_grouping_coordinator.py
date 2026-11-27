"""행 묶기 조율자가 필터 바와 그룹 프록시, 저장 상태를 함께 맞추는지 검증"""

import os
import unittest
from typing import Any

from src.core.settings.ui_state import GROUP_BY_FOLDER, GROUP_BY_NONE, UiState
from src.gui.table.table_grouping_coordinator import (
    RowGroupingCoordinator,
    attach_row_grouping,
)
from src.gui.table.video_table_view import VideoTableView
from tests._factories import make_eval, make_video

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT_DIR = "C:/work"


class _Signal:
    def __init__(self) -> None:
        self._slots: list[Any] = []

    def connect(self, slot: Any) -> None:
        self._slots.append(slot)

    def emit(self, *args: Any) -> None:
        for slot in self._slots:
            slot(*args)


class _BarStub:
    """조율자가 쓰는 필터 바 인터페이스만 흉내 내는 대역"""

    def __init__(self) -> None:
        self.row_group_selected = _Signal()
        self.row_group_menu_opening = _Signal()
        self.expand_all_groups_requested = _Signal()
        self.collapse_all_groups_requested = _Signal()
        self.options: list[tuple[str, str]] = []
        self.counts: dict[str, int] = {}
        self.button: tuple[str, str, int] = ("", "", 0)

    def set_row_group_options(self, options: list[tuple[str, str]]) -> None:
        self.options = list(options)

    def set_row_group_counts(self, counts: dict[str, int]) -> None:
        self.counts = dict(counts)

    def set_row_group(self, group_by: str, *, label: str, count: int) -> None:
        self.button = (group_by, label, count)


def _evals() -> list[Any]:
    return [
        make_eval(video=make_video(filename="a/one.mp4", codec="h264")),
        make_eval(video=make_video(filename="b/two.mp4", codec="hevc")),
    ]


class RowGroupingCoordinatorTests(unittest.TestCase):
    """실제 표 뷰에서 묶기 적용과 버튼, 메뉴 항목, 저장 상태 동기화 검증"""

    def setUp(self) -> None:
        self.view = VideoTableView()
        self.addCleanup(self.view.close)
        self.view.table_model.set_data_source(_evals(), root_dir=_ROOT_DIR)
        self.ui = UiState()
        self.bar = _BarStub()
        self.persists: list[int] = []
        self.coordinator = RowGroupingCoordinator(
            self.view, self.bar, self.ui, lambda: self.persists.append(1)
        )

    def test_default_is_no_grouping_with_menu_options(self) -> None:
        self.assertEqual(self.coordinator.group_by, GROUP_BY_NONE)
        self.assertEqual(self.bar.button, (GROUP_BY_NONE, "", 0))
        self.assertTrue(self.bar.options)

    def test_apply_group_updates_state_button_and_persists(self) -> None:
        self.coordinator.apply_group_by(GROUP_BY_FOLDER)
        self.assertEqual(self.view.group_model.group_by, GROUP_BY_FOLDER)
        self.assertEqual(self.ui.filters.group_by, GROUP_BY_FOLDER)
        self.assertEqual(self.bar.button, (GROUP_BY_FOLDER, "폴더", 2))
        self.assertTrue(self.persists)

    def test_menu_opening_recomputes_counts_only_after_a_rebuild(self) -> None:
        self.bar.row_group_menu_opening.emit()
        self.assertEqual(self.bar.counts[GROUP_BY_FOLDER], 2)
        self.bar.counts = {}
        self.bar.row_group_menu_opening.emit()
        self.assertEqual(self.bar.counts, {})

    def test_attach_returns_a_wired_coordinator(self) -> None:
        coordinator = attach_row_grouping(self.view, _BarStub(), UiState(), lambda: None)
        self.assertEqual(coordinator.group_by, self.view.group_model.group_by)


if __name__ == "__main__":
    unittest.main(verbosity=2)
