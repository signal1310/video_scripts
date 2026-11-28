"""부분 분석 결과 반영이 표 선택을 지우지 않는지 검증하는 GUI 테스트"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import Any

from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtWidgets import QApplication

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.settings.ui_state import GROUP_BY_FOLDER, GROUP_BY_NONE
from src.gui.table.table_grouping import VideoGroupProxyModel
from src.gui.table.table_model import VideoTableModel
from src.gui.table.video_table_view import VideoTableView
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_ROOT_DIR: str = "C:/work"


def _evals() -> list[Any]:
    return [
        make_eval(video=make_video(filename=f"a/clip{i}.mp4", codec="h264"))
        for i in range(6)
    ]


def _result(area: int) -> FrameAnalysisResult:
    side = int(area ** 0.5)
    return FrameAnalysisResult(
        active_region=ActiveRegion(side, side, 0, 0, False),
        metric_used="vmaf",
        vmaf_model="vmaf_v0.6.1",
        tier_scores=(),
        candidate_tier=None,
        valid_segment_count=5,
        total_segment_count=5,
        unmeasurable=False,
        is_vr_protected=False,
    )


class RebuildSelectionTests(unittest.TestCase):
    """그룹 프록시 재구성과 뷰 선택 유지 검증"""

    def setUp(self) -> None:
        self.view = VideoTableView()
        self.view.resize(900, 500)
        self.view.show()
        self.addCleanup(self.view.close)
        self.view.table_model.set_data_source(_evals(), root_dir=_ROOT_DIR)
        self.view.table_model.set_sort_stack(
            [(VideoTableModel.COL_ACTIVE_FRAME, Qt.SortOrder.AscendingOrder)]
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

    def _selected_filename(self) -> str | None:
        names = self.view.get_selected_filenames()
        return next(iter(names)) if names else None

    def _group_model(self, group_by: str) -> VideoGroupProxyModel:
        self.view.group_model.set_group_by(group_by)
        return self.view.group_model

    def test_resort_keeps_selection_without_reset(self) -> None:
        """재정렬이 모델 초기화 없이 지나가고 같은 영상이 선택, 포커스로 남는지 검증"""
        for group_by in (GROUP_BY_NONE, GROUP_BY_FOLDER):
            with self.subTest(group_by=group_by):
                self._group_model(group_by)
                self._select_row_of("a/clip3.mp4")
                resets: list[int] = []
                self.view.group_model.modelReset.connect(
                    lambda checked=False, sink=resets: sink.append(1)
                )
                # - 화질 분석 부분 결과가 정렬 열 값을 바꿔 프록시가 재정렬하는 흐름
                self.view.table_model.update_frame_analysis_results(
                    {"a/clip0.mp4": _result(100), "a/clip5.mp4": _result(400)}
                )
                self.assertEqual(resets, [])
                self.assertEqual(self._selected_filename(), "a/clip3.mp4")
                current = self.view.get_evaluation_at(self.view.currentIndex())
                assert current is not None
                self.assertEqual(current.video.filename, "a/clip3.mp4")

    def test_inserted_rows_keep_selection(self) -> None:
        """새 행이 도착해 초기화되어도 기존 선택이 파일명으로 복원되는지 검증"""
        self._group_model(GROUP_BY_NONE)
        self._select_row_of("a/clip2.mp4")
        self.view.table_model.set_data_source(
            [*_evals(), make_eval(video=make_video(filename="a/clip_new.mp4"))],
            root_dir=_ROOT_DIR,
        )
        self.assertEqual(self._selected_filename(), "a/clip2.mp4")

    def test_resort_inside_filter_batch_keeps_selection_without_reset(self) -> None:
        """검색 필터 재평가 묶음 안에서 재정렬돼도 초기화 없이 선택이 유지되는지 검증"""
        self._group_model(GROUP_BY_FOLDER)
        self._select_row_of("a/clip3.mp4")
        resets: list[int] = []
        self.view.group_model.modelReset.connect(
            lambda checked=False, sink=resets: sink.append(1)
        )
        proxy = self.view.proxy_model
        proxy.filter_batch_started.emit()
        try:
            # - 검색 재평가 동안 도착한 부분 결과가 정렬 열 값을 바꿔 재정렬하는 흐름
            self.view.table_model.update_frame_analysis_results(
                {"a/clip0.mp4": _result(400), "a/clip5.mp4": _result(100)}
            )
        finally:
            proxy.filter_batch_finished.emit()
        self.assertEqual(resets, [])
        self.assertEqual(self._selected_filename(), "a/clip3.mp4")
        current = self.view.get_evaluation_at(self.view.currentIndex())
        assert current is not None
        self.assertEqual(current.video.filename, "a/clip3.mp4")

    def test_explicit_clear_still_clears(self) -> None:
        """빈 영역 클릭 같은 명시적 해제는 재구성 후에도 유지되는지 검증"""
        self._group_model(GROUP_BY_NONE)
        self._select_row_of("a/clip2.mp4")
        self.view.clearSelection()
        self.view.table_model.update_frame_analysis_results(
            {"a/clip0.mp4": _result(100)}
        )
        self.assertEqual(self._selected_filename(), None)


if __name__ == "__main__":
    unittest.main()
