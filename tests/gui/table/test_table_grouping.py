"""행 묶기 프록시, 그룹 머리글 델리게이트, 묶기 조율자 검증"""

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import Any

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QMainWindow, QStyleOptionViewItem

from src.core.config import ClassifierConfig
from src.core.models import VideoEvaluation
from src.core.settings.state_codec import AppState, parse_state_mapping, render_state_jsonc
from src.core.settings.ui_state import (
    GROUP_BY_COLUMN_PREFIX,
    GROUP_BY_FOLDER,
    GROUP_BY_NONE,
    UiState,
)
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_columns import COL_DEFS
from src.gui.table.table_grouping import (
    GROUP_EXPANDED_ROLE,
    GROUP_HEADER_ROLE,
    GroupHeaderDelegate,
    RowGroupingCoordinator,
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

    def test_filtering_and_sorting_keep_group_order(self) -> None:
        self.group.set_group_by(f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}")
        self.proxy.set_search_text("mp4")
        self.assertEqual(self.group.group_count, 2)
        self.proxy.set_search_text("")
        self.proxy.sort(VideoTableModel.COL_CODEC, Qt.SortOrder.AscendingOrder)
        self.assertTrue(self.group.group_count >= 1)
        self.assertTrue(self.model.sort_stack)


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

    def test_group_options_skip_hidden_columns(self) -> None:
        options = dict(group_options(self.view))
        self.assertEqual(options[GROUP_BY_FOLDER], "폴더")
        self.assertIn(f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}", options)
        self.view.hidden.add(VideoTableModel.COL_CODEC)
        options = dict(group_options(self.view))
        self.assertNotIn(f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}", options)

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
        self.expand_all_groups_requested = self._Signal()
        self.collapse_all_groups_requested = self._Signal()
        self.search_edit = _SearchEditStub()
        self.options: list[tuple[str, str]] = []
        self.button: tuple[str, str, int] = ("", "", 0)

    def set_row_group_options(self, options: list[tuple[str, str]]) -> None:
        self.options = list(options)

    def set_row_group(self, group_by: str, *, label: str, count: int) -> None:
        self.button = (group_by, label, count)


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

    def test_hidden_group_column_falls_back(self) -> None:
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.coordinator.apply_group_by(codec_key)
        self.assertEqual(self.view.group_model.group_by, codec_key)
        self.view.setColumnHidden(VideoTableModel.COL_CODEC, True)
        self.coordinator.sync_options()
        self.assertEqual(self.view.group_model.group_by, GROUP_BY_NONE)
        self.assertEqual(self.ui.filters.group_by, GROUP_BY_NONE)
        self.assertEqual(self.bar.button[0], GROUP_BY_NONE)

    def test_sync_from_state_restores_saved_group(self) -> None:
        self.ui.filters.group_by = GROUP_BY_FOLDER
        self.coordinator.sync_from_state()
        self.assertEqual(self.view.group_model.group_by, GROUP_BY_FOLDER)
        self.assertEqual(self.bar.button, (GROUP_BY_FOLDER, "폴더", 3))
        self.ui.filters.group_by = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_REASON}"
        self.view.setColumnHidden(VideoTableModel.COL_REASON, True)
        self.coordinator.sync_from_state()
        self.assertEqual(self.view.group_model.group_by, GROUP_BY_NONE)

    def test_expand_and_collapse_signals_reach_view(self) -> None:
        self.coordinator.apply_group_by(GROUP_BY_FOLDER)
        expanded_rows = self.view.group_model.rowCount()
        self.bar.collapse_all_groups_requested.emit()
        self.assertEqual(self.view.group_model.rowCount(), 3)
        self.bar.expand_all_groups_requested.emit()
        self.assertEqual(self.view.group_model.rowCount(), expanded_rows)


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
    """숨김 열 변경이 행 묶기를 해제하고 메뉴, 버튼, 저장 상태를 함께 맞추는지 검증"""

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

    def test_sync_preset_resets_grouping_for_hidden_column(self) -> None:
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.coordinator.apply_group_by(codec_key)
        self.assertEqual(
            self.bar.btn_row_group.text(),
            f"그룹: {group_label(codec_key)} ({self.view.group_model.group_count})",
        )
        assert self.bar._collapse_groups_action is not None
        self.assertTrue(self.bar._collapse_groups_action.isEnabled())

        self.view.setColumnHidden(VideoTableModel.COL_CODEC, True)
        self.display.sync_preset()

        self.assertEqual(self.view.group_model.group_by, GROUP_BY_NONE)
        self.assertEqual(self.ui.filters.group_by, GROUP_BY_NONE)
        self.assertEqual(self.bar.btn_row_group.text(), "그룹: 없음")
        self.assertNotIn(codec_key, self.bar._row_group_actions)
        assert self.bar._expand_groups_action is not None
        self.assertFalse(self.bar._expand_groups_action.isEnabled())
        assert self.bar._collapse_groups_action is not None
        self.assertFalse(self.bar._collapse_groups_action.isEnabled())

    def test_header_layout_edit_resets_grouping_for_hidden_column(self) -> None:
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.coordinator.apply_group_by(codec_key)
        self.view.setColumnHidden(VideoTableModel.COL_CODEC, True)
        self.view.filter_header.layout_edit_finished.emit()
        self.assertEqual(self.view.group_model.group_by, GROUP_BY_NONE)
        self.assertEqual(self.ui.filters.group_by, GROUP_BY_NONE)
        self.assertEqual(self.bar.btn_row_group.text(), "그룹: 없음")

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
        controller._reset_sort_group()
        self.assertEqual(view.group_model.group_by, GROUP_BY_NONE)
        self.assertEqual(ui.filters.group_by, GROUP_BY_NONE)
        self.assertEqual(bar.button[0], GROUP_BY_NONE)
        self.assertFalse(controller._sort_group_changed())
