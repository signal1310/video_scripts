"""행 묶기 프록시, 그룹 머리글 델리게이트, 묶기 조율자 검증"""

import json
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import Any
from unittest import mock

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QMainWindow, QStyleOptionViewItem

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.config import ClassifierConfig
from src.core.models import VideoEvaluation
from src.core.settings.state_codec import (
    AppState,
    parse_state_mapping,
    render_state_jsonc,
    strip_jsonc_comments,
)
from src.core.settings.ui_state import (
    GROUP_BY_COLUMN_PREFIX,
    GROUP_BY_FOLDER,
    GROUP_BY_NONE,
    MAX_COLLAPSED_GROUP_LABELS,
    UiState,
)
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.table.column_filters import ColumnDataType
from src.gui.table.frame_analysis_columns import ACTIVE_FRAME_KEEP_FULL_TEXT
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_columns import COL_DEFS
from src.gui.table.table_grouping import (
    GROUP_EXPANDED_ROLE,
    GROUP_HEADER_ROLE,
    GroupHeaderDelegate,
    VideoGroupProxyModel,
    cursor_index,
    filename_at_row,
    group_label,
    group_options,
    handle_group_click,
    nearest_video_row,
    snap_cursor_row,
    sync_group_spans,
    top_row_key,
)
from src.gui.table.table_grouping_coordinator import RowGroupingCoordinator
from src.gui.table.table_model import VideoTableModel
from src.gui.theme import ThemeManager
from src.gui.widgets.filter_bar import FilterTuningBar
from src.gui.window_layout import attach_table_display
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_ROOT_DIR: str = "C:/work"


def _make_chain(
    evals: list[VideoEvaluation],
) -> tuple[VideoTableModel, VideoSortFilterProxyModel, VideoGroupProxyModel]:
    """원본 표 모델, 필터 프록시, 그룹 프록시를 차례로 연결한 고리"""
    model = VideoTableModel(config=ClassifierConfig())
    model.set_data_source(evals, root_dir=_ROOT_DIR)
    proxy = VideoSortFilterProxyModel()
    proxy.setSourceModel(model)
    group = VideoGroupProxyModel()
    group.setSourceModel(proxy)
    return model, proxy, group


def _sample_evals() -> list[VideoEvaluation]:
    """폴더와 코덱이 다른 표본 평가 목록"""
    return [
        make_eval(video=make_video(filename="a/one.mp4", codec="h264")),
        make_eval(video=make_video(filename="a/two.mp4", codec="hevc")),
        make_eval(video=make_video(filename="b/three.mp4", codec="h264")),
        make_eval(video=make_video(filename="four.mkv", codec="h264")),
    ]


def _barred_result() -> FrameAnalysisResult:
    """화면 여백가 있는 활성 화면 결과. 전체 화면 유지 상태 확인에 씀"""
    return FrameAnalysisResult(
        active_region=ActiveRegion(734, 444, 593, 138, True),
        metric_used="vmaf",
        vmaf_model="vmaf_v0.6.1",
        tier_scores=(),
        candidate_tier=None,
        valid_segment_count=5,
        total_segment_count=5,
        unmeasurable=False,
        is_vr_protected=False,
    )


def _header_row(group: VideoGroupProxyModel, prefix: str) -> int:
    """그룹 머리글 행 중 표시 이름이 prefix로 시작하는 첫 행"""
    for row in range(group.rowCount()):
        if group.is_group_row(row):
            text = str(group.index(row, 0).data(Qt.ItemDataRole.DisplayRole))
            if text.startswith(prefix):
                return row
    raise AssertionError(f"그룹 머리글을 찾지 못함: {prefix}")


class VideoGroupProxyModelTests(unittest.TestCase):
    """묶기 없음 통과, 폴더와 열 묶기, 접기와 공개 동작 검증"""

    def setUp(self) -> None:
        self.evals = _sample_evals()
        self.model, self.proxy, self.group = _make_chain(self.evals)

    def test_passthrough_without_grouping(self) -> None:
        self.assertEqual(self.group.group_by, GROUP_BY_NONE)
        self.assertEqual(self.group.group_count, 0)
        self.assertEqual(self.group.rowCount(), 4)
        for row in range(4):
            self.assertFalse(self.group.is_group_row(row))
            self.assertEqual(
                self.group.index(row, 0).data(Qt.ItemDataRole.DisplayRole),
                self.proxy.index(row, 0).data(Qt.ItemDataRole.DisplayRole),
            )

    def test_folder_grouping_headers(self) -> None:
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.assertEqual(self.group.group_count, 3)
        self.assertEqual(self.group.rowCount(), 7)
        labels = [
            str(self.group.index(row, 0).data(Qt.ItemDataRole.DisplayRole))
            for row in range(self.group.rowCount())
            if self.group.is_group_row(row)
        ]
        self.assertEqual(len(labels), 3)
        self.assertIn("(2개 영상", labels[0])
        self.assertTrue(labels[0].startswith("a "))

    def test_column_grouping(self) -> None:
        self.group.set_group_by(f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}")
        self.assertEqual(self.group.group_count, 2)
        headers = [
            str(self.group.index(row, 0).data(Qt.ItemDataRole.DisplayRole))
            for row in range(self.group.rowCount())
            if self.group.is_group_row(row)
        ]
        self.assertTrue(any(head.startswith("h264 ") for head in headers))

    def test_group_header_uses_the_keep_full_text(self) -> None:
        """활성 화면 열로 묶으면 전체 화면 유지 행이 그 문구를 그룹 머리글로 씀을 검증"""
        model, _proxy, group = _make_chain(
            [make_eval(video=make_video(filename="a.mp4")), make_eval(video=make_video(filename="b.mp4"))]
        )
        model.set_frame_analysis_results(
            {
                "a.mp4": _barred_result(),
            }
        )
        model.set_crop_exclusions(["a.mp4"])
        group.set_group_by(f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_ACTIVE_FRAME}")
        labels = [
            str(group.index(row, 0).data(Qt.ItemDataRole.DisplayRole))
            for row in range(group.rowCount())
            if group.is_group_row(row)
        ]
        self.assertTrue(any(label.startswith(ACTIVE_FRAME_KEEP_FULL_TEXT) for label in labels))
        self.assertFalse(any(label.startswith("734x444") for label in labels))

    def test_group_header_follows_the_crop_setting(self) -> None:
        """잘라내기 설정을 끄면 활성 화면 열로 묶은 그룹 머리글도 감지 크기로 바뀌는지 검증"""
        model, _proxy, group = _make_chain(
            [make_eval(video=make_video(filename="a.mp4")), make_eval(video=make_video(filename="b.mp4"))]
        )
        model.set_frame_analysis_results({"a.mp4": _barred_result()})
        model.set_crop_exclusions(["a.mp4"])
        group.set_group_by(f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_ACTIVE_FRAME}")
        _header_row(group, ACTIVE_FRAME_KEEP_FULL_TEXT)
        config = ClassifierConfig()
        config.encode.crop_black_bars = False
        model.set_config(config)
        labels = [
            str(group.index(row, 0).data(Qt.ItemDataRole.DisplayRole))
            for row in range(group.rowCount())
            if group.is_group_row(row)
        ]
        self.assertTrue(any(label.startswith("734x444") for label in labels))
        self.assertFalse(any(label.startswith(ACTIVE_FRAME_KEEP_FULL_TEXT) for label in labels))

    def test_toggle_and_expand_collapse(self) -> None:
        self.group.set_group_by(GROUP_BY_FOLDER)
        total = self.group.rowCount()
        self.group.toggle_group(0)
        self.assertTrue(self.group.is_collapsed_row(0))
        self.assertFalse(self.group.index(0, 0).data(GROUP_EXPANDED_ROLE))
        self.assertLess(self.group.rowCount(), total)
        self.group.toggle_group(0)
        self.assertEqual(self.group.rowCount(), total)
        self.group.collapse_all_groups()
        self.assertEqual(self.group.rowCount(), self.group.group_count)
        self.group.expand_all_groups()
        self.assertEqual(self.group.rowCount(), total)
        self.group.toggle_group(-1)
        self.group.toggle_group(total + 5)

    def test_invalid_group_key_falls_back(self) -> None:
        self.group.set_group_by("bogus")
        self.assertEqual(self.group.group_by, GROUP_BY_NONE)
        self.group.set_group_by(f"{GROUP_BY_COLUMN_PREFIX}999")
        self.assertEqual(self.group.group_by, GROUP_BY_NONE)
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.assertEqual(self.group.group_by, GROUP_BY_FOLDER)

    def test_roles_flags_and_mapping(self) -> None:
        self.group.set_group_by(GROUP_BY_FOLDER)
        header = self.group.index(0, 0)
        self.assertTrue(header.data(GROUP_HEADER_ROLE))
        self.assertTrue(header.data(GROUP_EXPANDED_ROLE))
        self.assertEqual(header.flags(), Qt.ItemFlag.ItemIsEnabled)
        self.assertIsNotNone(header.data(Qt.ItemDataRole.ToolTipRole))
        self.assertTrue(self.group.is_group_index(header))
        self.assertFalse(self.group.is_group_index(QModelIndex()))
        self.assertFalse(self.group.mapToSource(header).isValid())
        self.assertFalse(self.group.mapFromSource(QModelIndex()).isValid())
        self.assertEqual(self.group.columnCount(), self.proxy.columnCount())
        self.assertEqual(self.group.columnCount(header), 0)
        self.assertEqual(self.group.rowCount(header), 0)
        self.assertFalse(self.group.index(0, 0, header).isValid())
        self.assertFalse(self.group.index(999, 0).isValid())
        self.assertFalse(self.group.index(0, 999).isValid())
        self.assertEqual(
            self.group.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole),
            self.proxy.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole),
        )
        video_row = self.group.index(1, 0)
        self.assertFalse(self.group.is_group_row(1))
        self.assertTrue(self.group.mapFromSource(self.proxy.index(0, 0)).isValid())
        self.assertEqual(self.group.mapToSource(video_row).column(), 0)

    def test_empty_model_paths(self) -> None:
        empty = VideoGroupProxyModel()
        self.assertEqual(empty.rowCount(), 0)
        self.assertEqual(empty.columnCount(), 0)
        self.assertIsNone(empty.headerData(0, Qt.Orientation.Horizontal))
        self.assertEqual(empty.flags(empty.index(0, 0)), Qt.ItemFlag.NoItemFlags)
        self.assertIsNone(empty.data(QModelIndex()))
        self.assertEqual(empty.mapToSource(QModelIndex()), QModelIndex())
        self.assertEqual(empty.mapFromSource(QModelIndex()), QModelIndex())
        empty.set_group_by(GROUP_BY_FOLDER)
        self.assertEqual(empty.group_count, 0)
        empty.collapse_all_groups()
        empty.expand_all_groups()
        empty.toggle_group(0)
        empty.sort(-1)

    def test_data_changed_keeps_layout_when_group_keys_unchanged(self) -> None:
        """그룹 키와 무관한 열 변경은 행 구성을 유지하고 머리글 합계만 다시 그림

        - 그룹 기준 열 값이 바뀌면 그룹 구성이 달라지므로 행 구성을 다시 만듦
        """
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.group.set_group_by(codec_key)
        resets: list[int] = []
        changed: list[int] = []
        self.group.modelReset.connect(lambda: resets.append(1))
        self.group.dataChanged.connect(lambda *_args: changed.append(1))
        header_before = str(self.group.index(0, 0).data(Qt.ItemDataRole.DisplayRole))
        self.assertIn("(3개 영상", header_before)

        # 1. 그룹 키와 무관한 열 값 변경: 행 구성은 그대로, 합계 문구만 갱신
        self.model.evaluations[0].expected_delta_mb = 10.0
        cell = self.model.index(0, VideoTableModel.COL_SAVED_SIZE)
        self.model.dataChanged.emit(cell, cell, [])
        self.assertEqual(resets, [])
        self.assertTrue(changed)
        header_after = str(self.group.index(0, 0).data(Qt.ItemDataRole.DisplayRole))
        self.assertIn("(3개 영상", header_after)
        self.assertNotEqual(header_after, header_before)

        # 2. 그룹 기준 열 값 변경: 그룹 구성이 달라져 행을 다시 만듦
        self.model.evaluations[0].video.codec = "hevc"
        cell = self.model.index(0, VideoTableModel.COL_CODEC)
        self.model.dataChanged.emit(cell, cell, [])
        self.assertEqual(len(resets), 1)

    def test_multi_row_change_repaints_only_changed_display_rows(self) -> None:
        """여러 행 변경이 표 전체가 아니라 바뀐 표시 행 구간만 다시 그리는지 검증

        - 그룹 머리글 행 사이에 끼어 있어도 바뀐 영상 행의 연속 구간만 알림
        """
        self.group.set_group_by(GROUP_BY_FOLDER)
        received: list[tuple[int, int]] = []
        self.group.dataChanged.connect(lambda tl, br, _r: received.append((tl.row(), br.row())))
        top = self.model.index(0, 0)
        bottom = self.model.index(1, self.model.columnCount() - 1)
        self.model.dataChanged.emit(top, bottom, [])
        self.assertEqual(received, [(1, 2)])
        self.assertLess(received[0][1], self.group.rowCount() - 1)

    def test_change_inside_a_collapsed_group_emits_nothing(self) -> None:
        """접힌 그룹 안의 행 변경은 표시 인덱스가 없어 아무 구간도 알리지 않는지 검증"""
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.group.toggle_group(0)
        received: list[tuple[int, int]] = []
        self.group.dataChanged.connect(lambda tl, br, _r: received.append((tl.row(), br.row())))
        top = self.model.index(0, 0)
        bottom = self.model.index(1, self.model.columnCount() - 1)
        self.model.dataChanged.emit(top, bottom, [])
        self.assertEqual(received, [])

    def test_non_contiguous_changed_rows_emit_several_spans(self) -> None:
        """사이에 그룹 머리글이 낀 변경 행은 연속 구간마다 따로 알리는지 검증"""
        self.group.set_group_by(GROUP_BY_FOLDER)
        received: list[tuple[int, int]] = []
        self.group.dataChanged.connect(lambda tl, br, _r: received.append((tl.row(), br.row())))
        top = self.model.index(1, 0)
        bottom = self.model.index(3, self.model.columnCount() - 1)
        self.model.dataChanged.emit(top, bottom, [])
        self.assertEqual(received, [(2, 2), (4, 4), (6, 6)])

    def test_single_row_change_keeps_the_single_cell_range(self) -> None:
        """한 행만 바뀌면 그 셀 범위만 다시 그리는지 검증"""
        self.group.set_group_by(GROUP_BY_FOLDER)
        received: list[tuple[int, int, int, int]] = []
        self.group.dataChanged.connect(lambda tl, br, _r: received.append((tl.row(), br.row(), tl.column(), tl.column())))
        column = VideoTableModel.COL_SAVED_SIZE
        cell = self.model.index(1, column)
        self.model.dataChanged.emit(cell, cell, [])
        display_row = self.group.mapFromSource(self.proxy.index(1, column)).row()
        self.assertIn((display_row, display_row, column, column), received)

    def test_passthrough_data_changed_forwards_range(self) -> None:
        """묶기가 없을 때 데이터 변경은 같은 표시 범위로 그대로 전달"""
        top = self.model.index(0, 0)
        bottom = self.model.index(1, VideoTableModel.COL_CODEC)
        received: list[tuple[int, int]] = []
        self.group.dataChanged.connect(
            lambda tl, br, _roles: received.append((tl.row(), br.row()))
        )
        self.model.dataChanged.emit(top, bottom, [])
        self.assertEqual(received, [(0, 1)])

    def test_data_changed_ignored_for_empty_or_invalid_source(self) -> None:
        """원본이 비었거나 범위를 벗어난 변경은 무시"""
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.model.set_data_source([], root_dir=_ROOT_DIR)
        stale = self.proxy.index(0, 0)
        self.model.dataChanged.emit(stale, stale, [])
        self.assertEqual(self.group.rowCount(), 0)

    def test_source_replacement_reconnects(self) -> None:
        other_model = VideoTableModel(config=ClassifierConfig())
        other_model.set_data_source(
            [make_eval(video=make_video(filename="z/x.mp4"))], root_dir=_ROOT_DIR
        )
        other_proxy = VideoSortFilterProxyModel()
        other_proxy.setSourceModel(other_model)
        self.group.setSourceModel(other_proxy)
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.assertEqual(self.group.group_count, 1)
        self.assertEqual(self.group.rowCount(), 2)
        self.group.setSourceModel(None)
        self.assertEqual(self.group.rowCount(), 0)

    def test_staged_row_arrival_applies_every_saved_collapsed_label(self) -> None:
        """원본 행이 나눠 도착해도 미리 넘긴 접힘 이름이 모두 반영되는지 검증

        - 첫 재구성에서 지금 없는 이름까지 소비하면 뒤에 도착한 그룹의 접힘이 사라짐
        """
        self.model.set_data_source([], root_dir=_ROOT_DIR)
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.group.set_collapsed_labels(["a", "b", "(최상위)"])
        self.assertEqual(self.group.collapsed_labels, [])
        self.model.set_data_source(self.evals[:2], root_dir=_ROOT_DIR)
        self.assertEqual(self.group.collapsed_labels, ["a"])
        self.model.set_data_source(self.evals, root_dir=_ROOT_DIR)
        self.assertEqual(self.group.collapsed_labels, ["(최상위)", "a", "b"])

    def test_saved_labels_replace_the_current_collapsed_set(self) -> None:
        """새로 넘긴 이름이 지금 접힘을 대체하고 아직 없는 이름은 예약으로 남는지 검증"""
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.group.toggle_group(0)
        self.assertEqual(self.group.collapsed_labels, ["a"])
        self.group.set_collapsed_labels(["b", "나중에 생길 그룹"])
        self.assertEqual(self.group.collapsed_labels, ["b"])
        self.model.set_data_source(
            [*self.evals, make_eval(video=make_video(filename="나중에 생길 그룹/x.mp4"))],
            root_dir=_ROOT_DIR,
        )
        self.assertEqual(self.group.collapsed_labels, ["b", "나중에 생길 그룹"])

    def test_toggle_keeps_the_reservation_of_a_group_hidden_by_search(self) -> None:
        """검색으로 행이 없는 그룹의 접힘 예약이 다른 그룹을 접어도 남는지 검증"""
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.proxy.set_search_text("three")
        self.group.set_collapsed_labels(["a"])
        self.assertEqual(self.group.saved_collapsed_labels, ["a"])

        self.group.toggle_group(0)

        self.assertEqual(self.group.saved_collapsed_labels, ["a", "b"])
        self.proxy.set_search_text("")
        self.assertEqual(self.group.collapsed_labels, ["a", "b"])

    def test_toggle_of_a_reserved_group_clears_its_reservation(self) -> None:
        """예약으로 남은 그룹을 사용자가 직접 펼치면 그 예약이 사라지는지 검증"""
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.proxy.set_search_text("three")
        self.group.set_collapsed_labels(["a"])
        self.proxy.set_search_text("")
        # - 검색을 풀면 예약이 적용되어 a 그룹 머리글이 접힌 채로 나타남
        header_row = _header_row(self.group, "a ")
        self.assertTrue(self.group.is_collapsed_row(header_row))

        self.group.toggle_group(header_row)

        self.assertEqual(self.group.collapsed_labels, [])
        self.assertEqual(self.group.saved_collapsed_labels, [])

    def test_filtering_and_sorting_keep_group_order(self) -> None:
        self.group.set_group_by(f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}")
        self.proxy.set_search_text("mp4")
        self.assertEqual(self.group.group_count, 2)
        self.proxy.set_search_text("")
        self.proxy.sort(VideoTableModel.COL_CODEC, Qt.SortOrder.AscendingOrder)
        self.assertTrue(self.group.group_count >= 1)
        self.assertTrue(self.model.sort_stack)


class VideoGroupProxyIncrementalTests(unittest.TestCase):
    """증분 갱신이 전체 재구성과 같은 결과를 내고 비용이 바뀐 행에만 비례하는지 검증"""

    def _snapshot(
        self, group: VideoGroupProxyModel, proxy: VideoSortFilterProxyModel
    ) -> tuple[int, list[str], int, list[int]]:
        headers = [
            str(group.index(row, 0).data(Qt.ItemDataRole.DisplayRole))
            for row in range(group.rowCount())
            if group.is_group_row(row)
        ]
        mapped = [
            group.mapFromSource(proxy.index(row, 0)).row() for row in range(proxy.rowCount())
        ]
        return group.group_count, headers, group.rowCount(), mapped

    def _fresh(self, proxy: VideoSortFilterProxyModel, group_by: str) -> VideoGroupProxyModel:
        fresh = VideoGroupProxyModel()
        fresh.setSourceModel(proxy)
        fresh.set_group_by(group_by)
        return fresh

    def _emit(self, row: int, column: int) -> None:
        cell = self.model.index(row, column)
        self.model.dataChanged.emit(cell, cell, [])

    def _assert_equivalent(self, group_by: str) -> None:
        self.group.set_group_by(group_by)
        # 1. 그룹이 그대로인 값 변경과 합계 변경
        self.model.evaluations[0].expected_delta_mb = 20.0
        self._emit(0, VideoTableModel.COL_SAVED_SIZE)
        self.model.evaluations[1].expected_delta_mb = 30.0
        self._emit(1, VideoTableModel.COL_SAVED_SIZE)
        # 2. 그룹을 옮기는 값 변경
        self.model.evaluations[2].video.codec = "vp9"
        self._emit(2, VideoTableModel.COL_CODEC)
        self.model.evaluations[3].video.filename = "c/four.mkv"
        self._emit(3, VideoTableModel.COL_NAME)

        fresh = self._fresh(self.proxy, group_by)
        self.assertEqual(self._snapshot(self.group, self.proxy), self._snapshot(fresh, self.proxy))
        self.assertGreater(self.group.group_count, 0)

    def test_incremental_equals_full_rebuild_for_folder(self) -> None:
        self.model, self.proxy, self.group = _make_chain(_sample_evals())
        self._assert_equivalent(GROUP_BY_FOLDER)

    def test_incremental_equals_full_rebuild_for_column(self) -> None:
        self.model, self.proxy, self.group = _make_chain(_sample_evals())
        self._assert_equivalent(f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}")

    def test_group_criteria_calls_follow_changed_rows(self) -> None:
        """키 열이 바뀐 행만 다시 계산하고 무관한 열 변경은 계산하지 않는지 검증"""
        evals = [
            make_eval(video=make_video(filename=f"folder{i % 7}/clip{i}.mp4", codec="h264"))
            for i in range(3000)
        ]
        model, _proxy, group = _make_chain(evals)
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        with mock.patch.object(
            VideoGroupProxyModel, "_group_criteria", autospec=True,
            side_effect=VideoGroupProxyModel._group_criteria,
        ) as spy:
            group.set_group_by(codec_key)
            spy.reset_mock()
            for row in range(200):
                cell = model.index(row, VideoTableModel.COL_CODEC)
                model.dataChanged.emit(cell, cell, [])
            self.assertEqual(spy.call_count, 200)
            spy.reset_mock()
            for row in range(200):
                cell = model.index(row, VideoTableModel.COL_SAVED_SIZE)
                model.dataChanged.emit(cell, cell, [])
            self.assertEqual(spy.call_count, 0)
        self.assertEqual(group.group_count, 1)

    def test_selection_kept_and_groups_rebuilt_only_on_rebuild(self) -> None:
        """그룹이 그대로면 선택을 보존하고, 재구성할 때만 groups_rebuilt를 내는지 검증"""
        from src.gui.table.video_table_view import VideoTableView

        view = VideoTableView()
        self.addCleanup(view.close)
        view.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        group = view.group_model
        group.set_group_by(f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}")
        view.set_current_row(1)
        current_row = view.currentIndex().row()
        rebuilt: list[int] = []
        group.groups_rebuilt.connect(lambda: rebuilt.append(1))

        def emit(row: int, column: int) -> None:
            cell = view.table_model.index(row, column)
            view.table_model.dataChanged.emit(cell, cell, [])

        view.table_model.evaluations[0].expected_delta_mb = 12.0
        emit(0, VideoTableModel.COL_SAVED_SIZE)
        self.assertEqual(rebuilt, [])
        self.assertEqual(view.currentIndex().row(), current_row)

        view.table_model.evaluations[0].video.codec = "vp9"
        emit(0, VideoTableModel.COL_CODEC)
        self.assertEqual(rebuilt, [1])


class _GroupViewStub:
    """표 뷰 대신 묶기 도우미가 쓰는 최소 인터페이스"""

    def __init__(self, group_model: VideoGroupProxyModel, proxy: VideoSortFilterProxyModel) -> None:
        self.group_model = group_model
        self.proxy_model = proxy
        table_model = proxy.sourceModel()
        assert isinstance(table_model, VideoTableModel)
        self.table_model = table_model
        self.hidden: set[int] = set()
        self.spans: list[tuple[int, int, int, int]] = []

    def isColumnHidden(self, col: int) -> bool:
        return col in self.hidden

    def clearSpans(self) -> None:
        self.spans.clear()

    def setSpan(self, row: int, col: int, row_span: int, col_span: int) -> None:
        self.spans.append((row, col, row_span, col_span))

    def get_evaluation_at(self, index: QModelIndex) -> VideoEvaluation | None:
        filter_index = self.group_model.mapToSource(index)
        if not filter_index.isValid():
            return None
        src = self.proxy_model.mapToSource(filter_index)
        ev = self.table_model.data(src, VideoTableModel.EVAL_ROLE)
        return ev if isinstance(ev, VideoEvaluation) else None

    def rowAt(self, _y: int) -> int:
        return 0


class GroupHelperTests(unittest.TestCase):
    """묶기 키 이름, 메뉴 항목, 표 행 도우미 검증"""

    def setUp(self) -> None:
        self.model, self.proxy, group = _make_chain(_sample_evals())
        self.view = _GroupViewStub(group, self.proxy)

    def test_group_label(self) -> None:
        self.assertEqual(group_label(GROUP_BY_FOLDER), "폴더")
        self.assertEqual(group_label(GROUP_BY_NONE), "")
        self.assertEqual(group_label("bogus"), "")
        self.assertEqual(
            group_label(f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"),
            COL_DEFS[VideoTableModel.COL_CODEC].menu_title,
        )

    def test_group_options_include_hidden_columns(self) -> None:
        """숨긴 범주형 열도 묶기 메뉴에 남고 폴더가 맨 앞에 오는지 검증"""
        options = group_options()
        self.assertEqual(options[0], (GROUP_BY_FOLDER, "폴더"))
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.assertIn(codec_key, dict(options))
        self.view.hidden.add(VideoTableModel.COL_CODEC)
        options = group_options()
        self.assertIn(codec_key, dict(options))
        self.assertEqual(options[0], (GROUP_BY_FOLDER, "폴더"))
        self.assertEqual(
            [key for key, _label in options],
            [GROUP_BY_FOLDER]
            + [
                f"{GROUP_BY_COLUMN_PREFIX}{col_def.col_id}"
                for col_def in COL_DEFS
                if col_def.spec.data_type in (ColumnDataType.ENUM, ColumnDataType.CATEGORY)
            ],
        )

    def test_row_helpers(self) -> None:
        self.view.group_model.set_group_by(GROUP_BY_FOLDER)
        self.assertTrue(self.view.group_model.is_group_row(0))
        self.assertEqual(filename_at_row(self.view, 0), "")
        self.assertEqual(filename_at_row(self.view, 1), "a/one.mp4")
        self.assertEqual(filename_at_row(self.view, -1), "")
        self.assertTrue(handle_group_click(self.view, self.view.group_model.index(0, 0)))
        self.assertTrue(self.view.group_model.is_collapsed_row(0))
        self.assertFalse(handle_group_click(self.view, QModelIndex()))
        # - 접힌 첫 그룹 뒤에는 영상 행이 머리글 다음 칸에 옴
        self.assertEqual(filename_at_row(self.view, 2), "b/three.mp4")
        self.assertEqual(nearest_video_row(self.view, "b/three.mp4", 0), 2)
        self.assertEqual(nearest_video_row(self.view, "missing.mp4", 0), 2)
        self.assertEqual(nearest_video_row(self.view, "", 0), 2)
        self.assertIsNotNone(top_row_key(self.view))

    def test_sync_group_spans(self) -> None:
        sync_group_spans(self.view, self.view.group_model)
        self.assertEqual(self.view.spans, [])
        self.view.group_model.set_group_by(GROUP_BY_FOLDER)
        sync_group_spans(self.view, self.view.group_model)
        self.assertTrue(self.view.spans)
        self.assertEqual(len(self.view.spans), self.view.group_model.group_count)
        self.view.group_model.setSourceModel(None)
        sync_group_spans(self.view, self.view.group_model)
        self.assertEqual(self.view.spans, [])


class _SearchEditStub:
    """검색 입력 대역"""

    def __init__(self) -> None:
        self._text: str = ""

    def text(self) -> str:
        """현재 검색어"""
        return self._text

    def clear(self) -> None:
        """검색어 지우기"""
        self._text = ""


class _FilterBarStub:
    """필터 바 대신 묶기 조율자가 쓰는 최소 인터페이스"""

    class _Signal:
        def __init__(self) -> None:
            self._slots: list[Any] = []

        def connect(self, slot: Any) -> None:
            self._slots.append(slot)

        def emit(self, *args: Any) -> None:
            for slot in self._slots:
                slot(*args)

    def __init__(self) -> None:
        self.row_group_selected = self._Signal()
        self.row_group_menu_opening = self._Signal()
        self.expand_all_groups_requested = self._Signal()
        self.collapse_all_groups_requested = self._Signal()
        self.search_edit = _SearchEditStub()
        self.options: list[tuple[str, str]] = []
        self.counts: dict[str, int] = {}
        self.menu_open_count: int = 0
        self.button: tuple[str, str, int] = ("", "", 0)
        self.review_filter_cleared: bool = False

    def set_row_group_options(self, options: list[tuple[str, str]]) -> None:
        self.options = list(options)

    def set_row_group_counts(self, counts: dict[str, int]) -> None:
        self.counts = dict(counts)

    def open_menu(self) -> None:
        """메뉴를 연 것처럼 열기 알림을 보냄"""
        self.menu_open_count += 1
        self.row_group_menu_opening.emit()

    def set_row_group(self, group_by: str, *, label: str, count: int) -> None:
        self.button = (group_by, label, count)

    def clear_chip_filters(self) -> None:
        """두 필터 칩 해제 기록"""
        self.review_filter_cleared = True


class RowGroupingCoordinatorTests(unittest.TestCase):
    """실제 표 뷰에서 묶기 적용, 버튼 갱신, 숨김 열 해제 검증"""

    def setUp(self) -> None:
        from src.core.settings.ui_state import UiState
        from src.gui.table.video_table_view import VideoTableView

        self.view = VideoTableView()
        self.addCleanup(self.view.close)
        self.view.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        self.ui = UiState()
        self.bar = _FilterBarStub()
        self.persists: list[int] = []
        self.coordinator = RowGroupingCoordinator(
            self.view, self.bar, self.ui, lambda: self.persists.append(1)
        )

    def test_default_is_no_grouping(self) -> None:
        self.assertEqual(self.coordinator.group_by, GROUP_BY_NONE)
        self.assertEqual(self.bar.button, (GROUP_BY_NONE, "", 0))
        self.assertTrue(self.bar.options)

    def test_apply_group_updates_state_and_button(self) -> None:
        self.coordinator.apply_group_by(GROUP_BY_FOLDER)
        self.assertEqual(self.view.group_model.group_by, GROUP_BY_FOLDER)
        self.assertEqual(self.ui.filters.group_by, GROUP_BY_FOLDER)
        self.assertEqual(self.bar.button, (GROUP_BY_FOLDER, "폴더", 3))
        self.assertTrue(self.persists)
        self.assertGreater(self.view.group_model.rowCount(), 4)

    def test_group_counts_are_computed_once_per_menu_open(self) -> None:
        """그룹 수를 메뉴를 열 때 한 번만 세고, 다시 열기 전에는 재사용하는지 검증"""
        self.coordinator.sync_options()
        self.assertEqual(self.bar.counts, {})
        self.bar.open_menu()
        counts = dict(self.bar.counts)
        self.assertTrue(counts)
        self.assertEqual(counts[GROUP_BY_FOLDER], 3)
        # - 그룹 구성이 바뀌지 않는 동안은 다시 세지 않음
        self.bar.counts = {}
        self.bar.open_menu()
        self.assertEqual(self.bar.counts, {})
        # - 필터로 행이 줄어 그룹 구성이 바뀌면 다음에 열 때 새로 셈
        self.view.proxy_model.set_search_text("a/one.mp4")
        self.bar.open_menu()
        self.assertEqual(self.bar.counts[GROUP_BY_FOLDER], 1)

    def test_hidden_group_column_keeps_grouping(self) -> None:
        """묶기 기준 열을 숨겨도 묶기와 저장 상태를 그대로 두는지 검증"""
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.coordinator.apply_group_by(codec_key)
        self.assertEqual(self.view.group_model.group_by, codec_key)
        group_count = self.view.group_model.group_count
        self.view.setColumnHidden(VideoTableModel.COL_CODEC, True)
        self.coordinator.sync_options()
        self.assertEqual(self.view.group_model.group_by, codec_key)
        self.assertEqual(self.ui.filters.group_by, codec_key)
        self.assertEqual(self.view.group_model.group_count, group_count)
        self.assertEqual(self.bar.button[0], codec_key)

    def test_sync_from_state_restores_saved_group(self) -> None:
        self.ui.filters.group_by = GROUP_BY_FOLDER
        self.coordinator.sync_from_state()
        self.assertEqual(self.view.group_model.group_by, GROUP_BY_FOLDER)
        self.assertEqual(self.bar.button, (GROUP_BY_FOLDER, "폴더", 3))
        reason_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_REASON}"
        self.ui.filters.group_by = reason_key
        self.view.setColumnHidden(VideoTableModel.COL_REASON, True)
        self.coordinator.sync_from_state()
        self.assertEqual(self.view.group_model.group_by, reason_key)
        self.assertEqual(self.ui.filters.group_by, reason_key)
        self.assertGreater(self.view.group_model.group_count, 0)

    def test_expand_and_collapse_signals_reach_view(self) -> None:
        self.coordinator.apply_group_by(GROUP_BY_FOLDER)
        expanded_rows = self.view.group_model.rowCount()
        self.bar.collapse_all_groups_requested.emit()
        self.assertEqual(self.view.group_model.rowCount(), 3)
        self.bar.expand_all_groups_requested.emit()
        self.assertEqual(self.view.group_model.rowCount(), expanded_rows)


class CollapsedGroupPersistenceTests(unittest.TestCase):
    """접힌 그룹 상태가 저장, 복원, 묶기 전환, 폴더 전환을 거치며 올바르게 유지되는지 검증"""

    def setUp(self) -> None:
        from src.gui.table.video_table_view import VideoTableView

        self.view = VideoTableView()
        self.addCleanup(self.view.close)
        self.view.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        self.ui = UiState()
        self.bar = _FilterBarStub()
        self.persists: list[int] = []
        self.coordinator = RowGroupingCoordinator(
            self.view, self.bar, self.ui, lambda: self.persists.append(1)
        )

    def test_collapsing_a_group_stores_its_label(self) -> None:
        self.coordinator.apply_group_by(GROUP_BY_FOLDER)
        self.view.group_model.toggle_group(0)
        self.assertEqual(self.ui.filters.collapsed_groups, {GROUP_BY_FOLDER: ["a"]})
        self.assertTrue(self.persists)

    def test_collapsed_group_restores_in_a_fresh_window(self) -> None:
        """한 창에서 접은 그룹이 같은 상태로 새로 만든 창에서도 접힌 채로 복원되는지 검증"""
        from src.gui.table.video_table_view import VideoTableView

        self.coordinator.apply_group_by(GROUP_BY_FOLDER)
        self.view.group_model.toggle_group(0)
        self.assertEqual(self.view.group_model.collapsed_labels, ["a"])

        rebuilt = VideoTableView()
        self.addCleanup(rebuilt.close)
        RowGroupingCoordinator(rebuilt, _FilterBarStub(), self.ui, lambda: None)
        rebuilt.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        model = rebuilt.group_model
        self.assertEqual(model.group_by, GROUP_BY_FOLDER)
        self.assertEqual(model.collapsed_labels, ["a"])
        self.assertTrue(model.is_collapsed_row(0))

    def test_collapsed_restore_applies_to_the_first_rebuild_only(self) -> None:
        """데이터가 없는 창에서 예약한 접힘이 첫 그룹 구성 때 한 번만 적용되는지 검증"""
        from src.gui.table.video_table_view import VideoTableView

        self.ui.filters.group_by = GROUP_BY_FOLDER
        self.ui.filters.collapsed_groups = {GROUP_BY_FOLDER: ["b"]}
        fresh = VideoTableView()
        self.addCleanup(fresh.close)
        RowGroupingCoordinator(fresh, _FilterBarStub(), self.ui, lambda: None)
        fresh.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        self.assertEqual(fresh.group_model.collapsed_labels, ["b"])
        fresh.group_model.expand_all_groups()
        fresh.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        self.assertEqual(fresh.group_model.collapsed_labels, [])

    def test_pending_restore_survives_an_empty_rebuild(self) -> None:
        """행이 없는 재구성이 있어도 예약한 접힘이 데이터 도착 뒤에 적용되는지 검증"""
        self.ui.filters.group_by = GROUP_BY_FOLDER
        self.ui.filters.collapsed_groups = {GROUP_BY_FOLDER: ["a"]}
        self.view.table_model.set_data_source([], root_dir=_ROOT_DIR)
        self.coordinator.sync_from_state()
        self.view.proxy_model.invalidateFilter()
        self.assertEqual(self.view.group_model.collapsed_labels, [])
        self.view.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        self.assertEqual(self.view.group_model.collapsed_labels, ["a"])

    def test_pending_restore_survives_a_rebuild_with_part_of_the_groups(self) -> None:
        """그룹이 일부만 있는 재구성을 거쳐도 저장한 접힘이 모두 적용되는지 검증

        - 시작 복원은 행이 일부만 도착한 재구성을 여러 번 거쳐, 먼저 온 그룹만 접히고
          나머지 이름은 다음 재구성에서 적용되어야 함
        """
        self.ui.filters.group_by = GROUP_BY_FOLDER
        self.ui.filters.collapsed_groups = {GROUP_BY_FOLDER: ["a", "b", "(최상위)"]}
        self.view.table_model.set_data_source([], root_dir=_ROOT_DIR)
        self.coordinator.sync_from_state()
        self.view.table_model.set_data_source(_sample_evals()[:2], root_dir=_ROOT_DIR)
        self.assertEqual(self.view.group_model.collapsed_labels, ["a"])
        self.view.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        self.assertEqual(
            self.view.group_model.collapsed_labels, ["(최상위)", "a", "b"]
        )
        # - 모든 이름을 적용한 뒤에는 예약이 남지 않아 다음 재구성이 접힘을 바꾸지 않음
        self.view.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        self.assertEqual(
            self.view.group_model.collapsed_labels, ["(최상위)", "a", "b"]
        )

    def test_folder_change_drops_pending_restore_names(self) -> None:
        """폴더가 바뀌면 아직 적용하지 못한 복원 예약도 함께 버리는지 검증"""
        self.ui.filters.group_by = GROUP_BY_FOLDER
        self.ui.filters.collapsed_groups = {GROUP_BY_FOLDER: ["a", "b"]}
        self.view.table_model.set_data_source([], root_dir=_ROOT_DIR)
        self.coordinator.sync_from_state()
        self.view.table_model.set_data_source(_sample_evals()[:2], root_dir=_ROOT_DIR)
        self.assertEqual(self.view.group_model.collapsed_labels, ["a"])
        self.view.table_model.set_data_source(_sample_evals(), root_dir="C:/other")
        self.assertEqual(self.view.group_model.collapsed_labels, [])
        # - 이전 폴더의 예약이 남아 있으면 새 폴더의 같은 이름 그룹이 저절로 접힘
        self.view.table_model.set_data_source(_sample_evals(), root_dir="C:/other")
        self.assertEqual(self.view.group_model.collapsed_labels, [])

    def test_user_toggle_keeps_other_pending_restore_names(self) -> None:
        """사용자가 한 그룹을 직접 접거나 펴도 다른 그룹의 복원 예약은 남는지 검증"""
        self.ui.filters.group_by = GROUP_BY_FOLDER
        self.ui.filters.collapsed_groups = {GROUP_BY_FOLDER: ["a", "b"]}
        self.view.table_model.set_data_source([], root_dir=_ROOT_DIR)
        self.coordinator.sync_from_state()
        self.view.table_model.set_data_source(_sample_evals()[:2], root_dir=_ROOT_DIR)
        self.assertEqual(self.view.group_model.collapsed_labels, ["a"])

        self.view.group_model.toggle_group(0)

        self.view.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        self.assertEqual(self.view.group_model.collapsed_labels, ["b"])

    def test_switching_group_by_does_not_apply_other_labels(self) -> None:
        """폴더 묶기에서 접은 이름이 열 묶기 그룹에 적용되지 않는지 검증"""
        self.coordinator.apply_group_by(GROUP_BY_FOLDER)
        self.view.group_model.toggle_group(0)
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.coordinator.apply_group_by(codec_key)
        self.assertEqual(self.view.group_model.group_by, codec_key)
        self.assertEqual(self.view.group_model.collapsed_labels, [])
        self.assertEqual(self.ui.filters.collapsed_groups, {GROUP_BY_FOLDER: ["a"]})
        # - 폴더 묶기로 돌아오면 그 묶기에서 접었던 그룹이 다시 접힘
        self.coordinator.apply_group_by(GROUP_BY_FOLDER)
        self.assertEqual(self.view.group_model.collapsed_labels, ["a"])

    def test_unknown_labels_are_ignored(self) -> None:
        """저장된 이름 중 지금 없는 그룹은 무시하고 나머지만 접는지 검증"""
        self.coordinator.apply_group_by(GROUP_BY_FOLDER)
        self.ui.filters.collapsed_groups = {GROUP_BY_FOLDER: ["없는 그룹", "b"]}
        self.coordinator.sync_from_state()
        self.assertEqual(self.view.group_model.collapsed_labels, ["b"])

    def test_folder_change_resets_collapsed_state(self) -> None:
        """작업 폴더가 바뀌면 접힘과 저장값을 모두 버리는지 검증"""
        self.coordinator.apply_group_by(GROUP_BY_FOLDER)
        self.view.group_model.toggle_group(0)
        self.view.table_model.set_data_source(_sample_evals(), root_dir="C:/other")
        self.assertEqual(self.view.group_model.collapsed_labels, [])
        self.assertEqual(self.ui.filters.collapsed_groups, {})
        self.assertEqual(self.view.group_model.group_by, GROUP_BY_FOLDER)

    def test_same_folder_reload_keeps_collapsed_state(self) -> None:
        """같은 작업 폴더의 재평가 결과 반영은 접힘을 건드리지 않는지 검증"""
        self.coordinator.apply_group_by(GROUP_BY_FOLDER)
        self.view.group_model.toggle_group(0)
        self.view.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        self.assertEqual(self.view.group_model.collapsed_labels, ["a"])


class GroupCursorNavigationTests(unittest.TestCase):
    """방향키 이동과 행 이동이 그룹 머리글 행을 건너뛰고 영상 행에 머무는지 검증"""

    def setUp(self) -> None:
        from src.gui.table.video_table_view import VideoTableView

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root_dir = tmp.name
        for name in ("a/one.mp4", "a/two.mp4", "b/three.mp4", "four.mkv"):
            path = os.path.join(self.root_dir, name.replace("/", os.sep))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(b"0")
        self.view = VideoTableView(root_dir_provider=lambda: self.root_dir)
        self.addCleanup(self.view.close)
        self.view.table_model.set_data_source(_sample_evals(), root_dir=self.root_dir)
        self.view.resize(900, 500)
        self.view.show()
        _APP.processEvents()
        self.view.group_model.set_group_by(GROUP_BY_FOLDER)
        self.model = self.view.group_model
        # - 폴더 묶기 행 구성: 머리글 a, 영상 2, 머리글 b, 영상 1, 머리글 (최상위), 영상 1
        self.assertEqual(
            [self.model.is_group_row(row) for row in range(7)],
            [True, False, False, True, False, True, False],
        )

    def test_snap_cursor_row_skips_headers(self) -> None:
        actions = QAbstractItemView.CursorAction
        self.assertEqual(snap_cursor_row(self.view, 0, actions.MoveDown.value), 1)
        self.assertEqual(snap_cursor_row(self.view, 3, actions.MoveDown.value), 4)
        self.assertEqual(snap_cursor_row(self.view, 3, actions.MovePageDown.value), 4)
        self.assertEqual(snap_cursor_row(self.view, 5, actions.MoveUp.value), 4)
        self.assertEqual(snap_cursor_row(self.view, 5, actions.MoveHome.value), 4)
        self.assertEqual(snap_cursor_row(self.view, 5, actions.MoveDown.value), 6)
        # - 위쪽에 영상 행이 없으면 포커스가 없는 한 머리글에 머물지 않고 목표 행을 그대로 씀
        self.assertEqual(snap_cursor_row(self.view, 0, actions.MoveUp.value), 0)
        self.assertEqual(snap_cursor_row(self.view, 0, actions.MoveLeft.value), 0)
        # - 영상 행이면 방향과 무관하게 목표 행 유지
        self.assertEqual(snap_cursor_row(self.view, 2, actions.MoveDown.value), 2)
        self.assertEqual(snap_cursor_row(self.view, 2, actions.MoveUp.value), 2)
        self.model.set_group_by(GROUP_BY_NONE)
        self.assertEqual(snap_cursor_row(self.view, 0, actions.MoveDown.value), 0)

    def test_snap_cursor_row_keeps_current_video_row_at_edge(self) -> None:
        down = QAbstractItemView.CursorAction.MoveDown.value
        # - 마지막 그룹만 접으면 그 아래에 영상 행이 없어 머리글 이동이 막힘
        self.model.toggle_group(5)
        self.assertTrue(self.model.is_group_row(5))
        self.assertEqual(self.model.rowCount(), 6)
        self.view.set_current_row(4)
        self.assertEqual(snap_cursor_row(self.view, 5, down), 4)
        # - 현재 행도 머리글이면 목표 행을 그대로 돌려줌
        self.view.set_current_row(0)
        self.assertEqual(snap_cursor_row(self.view, 5, down), 5)

    def test_cursor_index_maps_header_to_video_row(self) -> None:
        down = QAbstractItemView.CursorAction.MoveDown.value
        result = cursor_index(self.view, self.model.index(0, 0), down)
        self.assertEqual(result.row(), 1)
        self.assertEqual(result.column(), 0)
        same = cursor_index(self.view, self.model.index(1, 0), down)
        self.assertEqual(same.row(), 1)

    def test_move_cursor_skips_headers_in_both_directions(self) -> None:
        actions = QAbstractItemView.CursorAction
        self.view.set_current_row(2)
        result = self.view.moveCursor(actions.MoveDown, Qt.KeyboardModifier.NoModifier)
        self.assertEqual(result.row(), 4)
        self.assertFalse(self.model.is_group_row(result.row()))
        self.view.set_current_row(4)
        result = self.view.moveCursor(actions.MoveUp, Qt.KeyboardModifier.NoModifier)
        self.assertEqual(result.row(), 2)
        self.assertFalse(self.model.is_group_row(result.row()))

    def test_move_current_row_lands_on_video_rows(self) -> None:
        self.view.set_current_row(0)
        self.view.move_current_row(1)
        self.assertEqual(self.view.currentIndex().row(), 1)
        self.assertEqual(self.view.get_selected_evaluations()[0].video.filename, "a/one.mp4")
        self.view.move_current_row(1)
        self.assertEqual(self.view.currentIndex().row(), 2)
        self.view.move_current_row(1)
        self.assertEqual(self.view.currentIndex().row(), 4)
        self.view.move_current_row(-1)
        self.assertEqual(self.view.currentIndex().row(), 2)
        self.assertFalse(self.model.is_group_row(self.view.currentIndex().row()))

    def test_filename_and_top_row_helpers_skip_headers(self) -> None:
        self.assertEqual(filename_at_row(self.view, 0), "")
        self.assertEqual(filename_at_row(self.view, 1), "a/one.mp4")
        key = top_row_key(self.view)
        assert key is not None
        self.assertEqual(key[0], "a/one.mp4")
        self.assertEqual(nearest_video_row(self.view, "", 0), 1)
        self.assertEqual(nearest_video_row(self.view, "b/three.mp4", 0), 4)


class GroupCountRefreshTests(unittest.TestCase):
    """필터와 데이터 변경 뒤 그룹 머리글 수와 버튼 배지가 실제 영상 수를 따르는지 검증"""

    def setUp(self) -> None:
        from src.gui.table.video_table_view import VideoTableView

        self.view = VideoTableView()
        self.addCleanup(self.view.close)
        self.view.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        self.ui = UiState()
        self.bar = _FilterBarStub()
        self.coordinator = RowGroupingCoordinator(self.view, self.bar, self.ui, lambda: None)

    def _header_labels(self) -> list[str]:
        model = self.view.group_model
        return [
            str(model.index(row, 0).data(Qt.ItemDataRole.DisplayRole))
            for row in range(model.rowCount())
            if model.is_group_row(row)
        ]

    def test_group_count_follows_filtering(self) -> None:
        self.coordinator.apply_group_by(GROUP_BY_FOLDER)
        self.assertEqual(self.bar.button, (GROUP_BY_FOLDER, "폴더", 3))
        self.view.proxy_model.set_search_text("mp4")
        self.assertEqual(self.view.group_model.group_count, 2)
        self.assertEqual(self.bar.button, (GROUP_BY_FOLDER, "폴더", 2))
        labels = self._header_labels()
        self.assertEqual(len(labels), 2)
        self.assertTrue(any("(2개 영상" in label for label in labels))
        self.assertTrue(any("(1개 영상" in label for label in labels))
        self.view.proxy_model.set_search_text("")
        self.assertEqual(self.view.group_model.group_count, 3)
        self.assertEqual(self.bar.button[2], 3)
        self.assertIn("(2개 영상", self._header_labels()[0])

    def test_header_counts_after_group_column_change(self) -> None:
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.coordinator.apply_group_by(codec_key)
        self.assertEqual(self.bar.button[2], 2)
        self.view.table_model.evaluations[0].video.codec = "hevc"
        cell = self.view.table_model.index(0, VideoTableModel.COL_CODEC)
        self.view.table_model.dataChanged.emit(cell, cell, [])
        self.assertEqual(self.view.group_model.group_count, 2)
        self.assertEqual(self.bar.button[2], 2)
        labels = self._header_labels()
        self.assertEqual({label.split(" (")[0] for label in labels}, {"h264", "hevc"})
        self.assertTrue(all("(2개 영상" in label for label in labels))


class RowGroupingPresetSyncTests(unittest.TestCase):
    """숨김 열 변경이 행 묶기를 유지하고 메뉴, 버튼, 저장 상태를 함께 맞추는지 검증"""

    def setUp(self) -> None:
        from src.gui.table.video_table_view import VideoTableView

        self.window = QMainWindow()
        self.addCleanup(self.window.close)
        self.view = VideoTableView()
        self.addCleanup(self.view.close)
        self.view.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        self.bar = FilterTuningBar()
        self.addCleanup(self.bar.close)
        self.ui = UiState()
        self.persists: list[int] = []
        self.display = attach_table_display(
            self.window, self.view, self.bar, self.ui, lambda: self.persists.append(1)
        )
        coordinator = self.display.row_grouping
        assert coordinator is not None
        self.coordinator = coordinator

    def test_sync_preset_keeps_grouping_for_hidden_column(self) -> None:
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.coordinator.apply_group_by(codec_key)
        group_count = self.view.group_model.group_count
        self.assertEqual(
            self.bar.btn_row_group.text(),
            f"그룹: {group_label(codec_key)} ({group_count})",
        )

        self.view.setColumnHidden(VideoTableModel.COL_CODEC, True)
        self.display.sync_preset()

        self.assertEqual(self.view.group_model.group_by, codec_key)
        self.assertEqual(self.ui.filters.group_by, codec_key)
        self.assertEqual(self.view.group_model.group_count, group_count)
        self.assertEqual(
            self.bar.btn_row_group.text(),
            f"그룹: {group_label(codec_key)} ({group_count})",
        )
        self.assertIn(codec_key, self.bar._row_group_actions)
        assert self.bar._expand_groups_action is not None
        self.assertTrue(self.bar._expand_groups_action.isEnabled())
        assert self.bar._collapse_groups_action is not None
        self.assertTrue(self.bar._collapse_groups_action.isEnabled())

    def test_header_layout_edit_keeps_grouping_for_hidden_column(self) -> None:
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.coordinator.apply_group_by(codec_key)
        self.view.setColumnHidden(VideoTableModel.COL_CODEC, True)
        self.view.filter_header.layout_edit_finished.emit()
        self.assertEqual(self.view.group_model.group_by, codec_key)
        self.assertEqual(self.ui.filters.group_by, codec_key)
        self.assertIn(codec_key, self.bar._row_group_actions)

    def test_visible_group_column_survives_preset_sync(self) -> None:
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.coordinator.apply_group_by(codec_key)
        self.display.sync_preset()
        self.assertEqual(self.view.group_model.group_by, codec_key)
        self.assertEqual(self.ui.filters.group_by, codec_key)
        assert self.bar._collapse_groups_action is not None
        self.assertTrue(self.bar._collapse_groups_action.isEnabled())


class GroupHeaderDelegateTests(unittest.TestCase):
    """그룹 머리글 델리게이트가 두 테마에서 배경과 글자를 그리는지 검증"""

    def setUp(self) -> None:
        from src.gui.table.video_table_view import VideoTableView

        self.view = VideoTableView()
        self.addCleanup(self.view.close)
        self.view.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        self.view.group_model.set_group_by(GROUP_BY_FOLDER)
        delegate = self.view.itemDelegateForColumn(VideoTableModel.COL_NAME)
        self.assertIsInstance(delegate, GroupHeaderDelegate)
        self.delegate = delegate

    def _paint(self, index: QModelIndex) -> QPixmap:
        pixmap = QPixmap(320, 24)
        painter = QPainter(pixmap)
        option = QStyleOptionViewItem()
        option.rect = pixmap.rect()
        assert isinstance(self.delegate, GroupHeaderDelegate)
        self.delegate.paint(painter, option, index)
        painter.end()
        return pixmap

    def test_paints_header_in_both_themes(self) -> None:
        manager = ThemeManager()
        self.addCleanup(manager.set_mode, manager.mode)
        header = self.view.group_model.index(0, 0)
        for mode, palette in (
            (ThemeManager.MODE_DARK, DARK_PALETTE),
            (ThemeManager.MODE_LIGHT, LIGHT_PALETTE),
        ):
            manager.set_mode(mode)
            self.assertIs(manager.current_palette, palette)
            self.assertFalse(self._paint(header).isNull())

    def test_paints_collapsed_header_and_data_row(self) -> None:
        self.view.group_model.toggle_group(0)
        self.assertTrue(self.view.group_model.is_collapsed_row(0))
        self.assertFalse(self._paint(self.view.group_model.index(0, 0)).isNull())
        # - 접힌 뒤에는 바로 다음 행이 다음 그룹 머리글이고 그 뒤가 영상 행
        self.assertTrue(self.view.group_model.is_group_row(1))
        self.assertFalse(self.view.group_model.is_group_row(2))
        self.assertFalse(self._paint(self.view.group_model.index(2, 0)).isNull())

    def test_init_style_option_drops_selection(self) -> None:
        from PyQt6.QtWidgets import QStyle

        option = QStyleOptionViewItem()
        option.state |= QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus
        header = self.view.group_model.index(0, 0)
        assert isinstance(self.delegate, GroupHeaderDelegate)
        self.delegate.initStyleOption(option, header)
        self.assertFalse(option.state & QStyle.StateFlag.State_Selected)
        self.assertFalse(option.state & QStyle.StateFlag.State_HasFocus)

    def test_size_hint_and_none_painter(self) -> None:
        option = QStyleOptionViewItem()
        option.rect = QPixmap(320, 24).rect()
        header = self.view.group_model.index(0, 0)
        assert isinstance(self.delegate, GroupHeaderDelegate)
        self.assertGreater(self.delegate.sizeHint(option, header).height(), 0)
        self.delegate.paint(None, option, header)


class GroupPersistenceTests(unittest.TestCase):
    """행 묶기 키의 저장, 복원, 잘못된 값 대체 검증"""

    def test_round_trip(self) -> None:
        state = AppState()
        state.ui.filters.group_by = GROUP_BY_FOLDER
        text = render_state_jsonc(state)
        self.assertIn('"group_by": "folder"', text)
        restored = parse_state_mapping(state.to_dict())
        self.assertEqual(restored.ui.filters.group_by, GROUP_BY_FOLDER)

    def test_copy_keeps_group_key(self) -> None:
        state = AppState()
        state.ui.filters.group_by = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.assertEqual(state.copy().ui.filters.group_by, state.ui.filters.group_by)

    def test_invalid_saved_values_fall_back(self) -> None:
        for raw in ("bogus", "column:x", "column:-1", 5, None, ["folder"]):
            with self.subTest(raw=raw):
                restored = parse_state_mapping({"ui": {"filters": {"group_by": raw}}})
                self.assertEqual(restored.ui.filters.group_by, GROUP_BY_NONE)
        restored = parse_state_mapping(
            {"ui": {"filters": {"group_by": f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"}}}
        )
        self.assertEqual(
            restored.ui.filters.group_by,
            f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}",
        )

    def test_missing_key_uses_default(self) -> None:
        restored = parse_state_mapping({"ui": {"filters": {}}})
        self.assertEqual(restored.ui.filters.group_by, GROUP_BY_NONE)

    def test_collapsed_groups_round_trip_per_group_key(self) -> None:
        """접힌 그룹 이름이 묶기 키별로 저장과 복원을 거쳐도 그대로 유지됨을 검증"""
        state = AppState()
        state.ui.filters.group_by = GROUP_BY_FOLDER
        state.ui.filters.collapsed_groups = {GROUP_BY_FOLDER: ["a", "(최상위)"]}
        self.assertEqual(state.copy().ui.filters.collapsed_groups, state.ui.filters.collapsed_groups)
        restored = parse_state_mapping(
            json.loads(strip_jsonc_comments(render_state_jsonc(state)))
        )
        self.assertEqual(
            restored.ui.filters.collapsed_groups, {GROUP_BY_FOLDER: ["a", "(최상위)"]}
        )

    def test_collapsed_groups_drop_unknown_keys_and_cap_labels(self) -> None:
        """알 수 없는 묶기 키, 빈 이름, 중복을 버리고 전체 상한까지만 남김을 검증"""
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        raw = {
            "ui": {
                "filters": {
                    "collapsed_groups": {
                        "bogus": ["x"],
                        "": ["x"],
                        codec_key: ["hevc", "hevc", "", 7, " h264 "],
                        GROUP_BY_FOLDER: "not-a-list",
                    }
                }
            }
        }
        filters = parse_state_mapping(raw).ui.filters
        self.assertEqual(filters.collapsed_groups, {codec_key: ["hevc", "h264"]})
        many = [f"group-{index}" for index in range(MAX_COLLAPSED_GROUP_LABELS + 20)]
        capped = parse_state_mapping(
            {"ui": {"filters": {"collapsed_groups": {GROUP_BY_FOLDER: many}}}}
        ).ui.filters.collapsed_groups
        self.assertEqual(len(capped[GROUP_BY_FOLDER]), MAX_COLLAPSED_GROUP_LABELS)
        self.assertEqual(capped[GROUP_BY_FOLDER][0], "group-0")
        missing = parse_state_mapping({"ui": {"filters": {}}}).ui.filters.collapsed_groups
        self.assertEqual(missing, {})


class DataCleanupGroupResetTests(unittest.TestCase):
    """데이터 정리가 행 묶기를 감지하고 없음으로 되돌리는지 검증"""

    def test_group_detected_and_reset(self) -> None:
        from src.core.settings.ui_state import UiState
        from src.gui.controllers.data_cleanup_controller import DataCleanupController
        from src.gui.table.video_table_view import VideoTableView

        view = VideoTableView()
        self.addCleanup(view.close)
        view.table_model.set_data_source(_sample_evals(), root_dir=_ROOT_DIR)
        ui = UiState()
        bar = _FilterBarStub()
        RowGroupingCoordinator(view, bar, ui, lambda: None)

        class _Deps:
            session = type("S", (), {"state": type("St", (), {"ui": ui})()})()
            table_view = view
            proxy_model = view.proxy_model
            filter_bar = bar

        controller = DataCleanupController.__new__(DataCleanupController)
        controller._deps_cache = _Deps()  # type: ignore[assignment]
        self.assertFalse(controller._sort_group_changed())
        view.group_model.set_group_by(GROUP_BY_FOLDER)
        self.assertTrue(controller._sort_group_changed())
        # - 여백 확인 필요 칩을 켜면 필터 변경으로 세고, 초기화는 칩과 보기 필터를 함께 끔
        view.proxy_model.set_review_only(True)
        self.assertTrue(controller._sort_group_changed())
        controller._reset_sort_group()
        self.assertFalse(view.proxy_model.review_only)
        self.assertTrue(bar.review_filter_cleared)
        self.assertEqual(view.group_model.group_by, GROUP_BY_NONE)
        self.assertEqual(ui.filters.group_by, GROUP_BY_NONE)
        self.assertEqual(bar.button[0], GROUP_BY_NONE)
        self.assertFalse(controller._sort_group_changed())
