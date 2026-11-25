"""표 행을 폴더나 범주형 열 값으로 묶어 접을 수 있는 그룹 헤더 행을 표시하는 모듈

- 그룹 프록시는 필터 프록시 위에 놓여 정렬과 필터가 만든 행 순서를 그대로 이어받음
- 묶기 키가 없으면 영상 행만 1:1로 통과시켜 평소 표시 비용을 늘리지 않음
- 그룹 헤더 행은 선택할 수 없어 선택 수, 미리 보기, 재생, 복사 경로가 영상 행만 다룸
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import (
    QAbstractProxyModel,
    QItemSelectionModel,
    QModelIndex,
    QPersistentModelIndex,
    QRectF,
    QSize,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QAbstractItemView, QStyle, QStyleOptionViewItem, QWidget

from src.core.formatting import format_size_mb
from src.core.models import VideoEvaluation
from src.core.settings.ui_state import (
    DEFAULT_GROUP_BY,
    GROUP_BY_COLUMN_PREFIX,
    GROUP_BY_FOLDER,
    GROUP_BY_NONE,
    is_valid_group_by,
    parse_group_by_column,
)
from src.gui.constants import FONT_TABLE_BADGE_PT, FONT_UI_FAMILY
from src.gui.styles.icons import get_icon
from src.gui.table.column_filters import ColumnDataType
from src.gui.table.delegates import NameAccentDelegate
from src.gui.table.table_columns import COL_DEF_MAP, COL_DEFS
from src.gui.table.table_model import VideoTableModel
from src.gui.theme import ThemeManager

__all__ = [
    "GroupHeaderDelegate",
    "RowGroupingCoordinator",
    "VideoGroupProxyModel",
    "attach_row_grouping",
    "cursor_index",
    "filename_at_row",
    "group_label",
    "group_options",
    "handle_group_click",
    "install_group_proxy",
    "move_current_video_row",
    "nearest_video_row",
    "snap_cursor_row",
    "sync_group_spans",
    "top_row_key",
]

DEFAULT_GROUP_KEY: str = DEFAULT_GROUP_BY
FOLDER_GROUP_LABEL: str = "폴더"
NONE_GROUP_LABEL: str = "없음"
GROUP_BUTTON_PREFIX: str = "그룹: "
EMPTY_GROUP_LABEL: str = "(값 없음)"
ROOT_GROUP_LABEL: str = "(최상위)"
GROUP_BY_OPTION_TIP: str = "영상을 폴더나 범주형 열 값으로 묶어 접을 수 있는 그룹 머리글을 표시합니다."
HEADER_COUNT_TEMPLATE: str = "{label} ({count}개 영상 / 예상 절약 {saved})"
HEADER_TOOLTIP_TEMPLATE: str = "이 그룹의 영상 {count}개, 예상 절약 {saved}입니다. 누르면 접거나 폅니다."
SAVED_DECIMALS: int = 1
HEADER_FONT_PT: int = FONT_TABLE_BADGE_PT
CHEVRON_SIZE_PX: int = 12
HEADER_PADDING_X_PX: int = 10
HEADER_ICON_GAP_PX: int = 6
HEADER_BORDER_PX: int = 1
GROUP_HEADER_ROLE: int = int(Qt.ItemDataRole.UserRole) + 30
GROUP_EXPANDED_ROLE: int = int(Qt.ItemDataRole.UserRole) + 31
# - 표시 행 구성 요소의 첫 값: 그룹 머리글 행이면 그룹 번호, 영상 행이면 소스 행 번호
_HEADER_ROW: int = -1
_PLAIN_ROW: int = -2
_EMPTY_INDEX: QModelIndex = QModelIndex()
_CATEGORICAL_TYPES: frozenset[ColumnDataType] = frozenset(
    {ColumnDataType.ENUM, ColumnDataType.CATEGORY}
)
# - PyQt6 열거자는 int()로 변환되지 않으므로 비교용 집합은 원소의 정숫값을 담음
_CursorAction = QAbstractItemView.CursorAction
_UPWARD_CURSOR_ACTIONS: frozenset[int] = frozenset(
    action.value for action in (_CursorAction.MoveUp, _CursorAction.MovePageUp, _CursorAction.MoveHome)
)
_DOWNWARD_CURSOR_ACTIONS: frozenset[int] = frozenset(
    action.value for action in (_CursorAction.MoveDown, _CursorAction.MovePageDown, _CursorAction.MoveEnd)
)
# - 표 뷰 타입은 이 모듈을 가져다 쓰는 쪽이므로 직접 참조하지 않아 순환 import를 피함


def group_label(group_by: str) -> str:
    """묶기 키의 표시 이름, 없음과 알 수 없는 키는 빈 문자열"""
    if group_by == GROUP_BY_FOLDER:
        return FOLDER_GROUP_LABEL
    column = parse_group_by_column(group_by)
    if column is None or column not in COL_DEF_MAP:
        return ""
    return COL_DEF_MAP[column].menu_title


def group_options() -> list[tuple[str, str]]:
    """행 묶기 메뉴에 넣을 (키, 이름) 목록, 폴더와 범주형 열 전체

    - 숨긴 열도 묶기 기준으로 쓸 수 있어 표시 여부와 무관하게 범주형 열을 모두 넣음
    - 폴더를 맨 앞에 두고 나머지는 `COL_DEFS` 열 순서를 따름
    """
    options: list[tuple[str, str]] = [(GROUP_BY_FOLDER, FOLDER_GROUP_LABEL)]
    for col_def in COL_DEFS:
        if col_def.spec.data_type not in _CATEGORICAL_TYPES:
            continue
        options.append((f"{GROUP_BY_COLUMN_PREFIX}{col_def.col_id}", col_def.menu_title))
    return options


def _folder_label(filename: str) -> str:
    """논리 파일명의 상위 폴더 상대 경로, 작업 폴더 바로 아래 파일은 최상위로 표시"""
    posix = filename.replace("\\", "/")
    parent = os.path.dirname(posix)
    return parent if parent else ROOT_GROUP_LABEL


@dataclass(slots=True)
class _Group:
    """그룹 머리글에 표시할 이름, 영상 수, 예상 절약 합계"""

    label: str
    count: int = 0
    saved_mb: float = 0.0


class VideoGroupProxyModel(QAbstractProxyModel):
    """필터 프록시 위에 그룹 머리글 행을 끼워 넣는 표 최상위 프록시

    - 묶기 키가 없으면 필터 프록시와 1:1로 대응해 평소 표시 순서를 그대로 유지
    - 묶기 키가 있으면 필터, 정렬 결과 순서대로 그룹을 만들고 접힌 그룹의 영상 행을 숨김
    """

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)
        self._group_by: str = DEFAULT_GROUP_KEY
        self._video_rows: list[int] = []
        self._rows: list[tuple[int, int]] = []
        self._groups: list[_Group] = []
        # - 소스 행마다 속한 그룹 이름, 데이터 변경이 그룹 구성을 바꿨는지 비교하는 기준
        self._group_keys: list[str] = []
        # - 소스 행마다 지금 합계에 넣은 예상 절약 값, 데이터 변경 시 차이만 더하고 빼기 위한 캐시
        self._row_saved: list[float] = []
        # - 소스 행과 그룹 머리글 행의 표시 행 위치, mapFromSource를 상수 시간에 처리
        self._display_rows: dict[int, int] = {}
        self._header_rows: dict[int, int] = {}
        self._collapsed: set[str] = set()

    # - 그룹 구성이 다시 만들어져 머리글 수가 바뀌면 필터 바 배지를 갱신하도록 알림
    groups_rebuilt = pyqtSignal()

    @property
    def group_by(self) -> str:
        """현재 행 묶기 키, 없음은 빈 문자열"""
        return self._group_by

    @property
    def group_count(self) -> int:
        """지금 만들어진 그룹 수, 묶기가 없으면 0"""
        return len(self._groups)

    def set_group_by(self, group_by: str) -> None:
        """행 묶기 키를 바꾸고 행 구성을 다시 만듦, 알 수 없는 키는 없음으로 되돌림"""
        target = group_by if is_valid_group_by(group_by) else GROUP_BY_NONE
        column = parse_group_by_column(target)
        if column is not None and column not in COL_DEF_MAP:
            target = GROUP_BY_NONE
        if target == self._group_by:
            return
        self._group_by = target
        self._collapsed.clear()
        self._rebuild()

    def setSourceModel(self, sourceModel: Any | None) -> None:
        """원본 프록시 설정 시 행 구성과 변경 전달을 연결"""
        prev = self.sourceModel()
        if prev is not None:
            self._disconnect_source(prev)
        super().setSourceModel(sourceModel)
        if sourceModel is not None:
            sourceModel.modelReset.connect(self._rebuild)
            sourceModel.layoutChanged.connect(self._rebuild)
            sourceModel.rowsInserted.connect(self._rebuild)
            sourceModel.rowsRemoved.connect(self._rebuild)
            sourceModel.dataChanged.connect(self._on_source_data_changed)
        self._rebuild()

    def _disconnect_source(self, source: Any) -> None:
        for signal, slot in (
            (source.modelReset, self._rebuild),
            (source.layoutChanged, self._rebuild),
            (source.rowsInserted, self._rebuild),
            (source.rowsRemoved, self._rebuild),
            (source.dataChanged, self._on_source_data_changed),
        ):
            try:
                signal.disconnect(slot)
            except TypeError:
                continue

    def _source_row_count(self) -> int:
        source = self.sourceModel()
        return source.rowCount() if source is not None else 0

    def _evaluation(self, source_row: int) -> VideoEvaluation | None:
        source = self.sourceModel()
        if source is None:
            return None
        ev = source.data(source.index(source_row, 0), VideoTableModel.EVAL_ROLE)
        return ev if isinstance(ev, VideoEvaluation) else None

    def _group_criteria(self, source_row: int) -> str:
        """소스 행이 속한 그룹의 표시 이름"""
        column = parse_group_by_column(self._group_by)
        if column is None:
            ev = self._evaluation(source_row)
            return ROOT_GROUP_LABEL if ev is None else _folder_label(ev.video.filename)
        source = self.sourceModel()
        if source is None:
            return EMPTY_GROUP_LABEL
        value = source.data(source.index(source_row, column), Qt.ItemDataRole.DisplayRole)
        if value is None or value == "":
            return EMPTY_GROUP_LABEL
        return str(value)

    def _build_rows(self) -> None:
        """필터, 정렬 결과 순서대로 그룹과 표시 행을 다시 계산"""
        self._groups = []
        self._video_rows = []
        self._group_keys = []
        self._row_saved = []
        count = self._source_row_count()
        if self._group_by == GROUP_BY_NONE:
            self._rows = [(_PLAIN_ROW, row) for row in range(count)]
            self._reindex()
            return
        group_index_by_label: dict[str, int] = {}
        for source_row in range(count):
            label = self._group_criteria(source_row)
            group_index = group_index_by_label.get(label)
            if group_index is None:
                group_index = len(self._groups)
                group_index_by_label[label] = group_index
                self._groups.append(_Group(label))
            group = self._groups[group_index]
            group.count += 1
            saved = self._saved_mb(source_row)
            group.saved_mb += saved
            self._row_saved.append(saved)
            self._video_rows.append(group_index)
            self._group_keys.append(label)
        self._rows = self._expanded_rows()
        self._reindex()

    def _saved_mb(self, source_row: int) -> float:
        """소스 행 영상의 예상 절약 값, 평가가 없으면 0"""
        ev = self._evaluation(source_row)
        return ev.preview_delta_mb if ev is not None else 0.0

    def _reindex(self) -> None:
        """표시 행 목록에서 소스 행과 그룹 머리글 행의 위치를 다시 색인"""
        self._display_rows = {}
        self._header_rows = {}
        for row, (kind, payload) in enumerate(self._rows):
            if kind == _HEADER_ROW:
                self._header_rows[payload] = row
            else:
                self._display_rows[payload] = row

    def _key_columns(self) -> frozenset[int]:
        """그룹 이름이 바뀔 수 있는 소스 열, 폴더 묶기는 파일명 열만 해당"""
        column = parse_group_by_column(self._group_by)
        if column is None:
            return frozenset({VideoTableModel.COL_NAME})
        return frozenset({column})

    def _expanded_rows(self) -> list[tuple[int, int]]:
        """그룹 머리글과 펼쳐진 그룹의 영상 행을 표시 순서로 나열"""
        rows: list[tuple[int, int]] = []
        for group_index, group in enumerate(self._groups):
            rows.append((_HEADER_ROW, group_index))
            if group.label in self._collapsed:
                continue
            rows.extend(
                (group_index, source_row)
                for source_row, owner in enumerate(self._video_rows)
                if owner == group_index
            )
        return rows

    def _rebuild(self, *_args: Any) -> None:
        """행 구성을 다시 만들고 모델 초기화를 알림"""
        self.beginResetModel()
        try:
            self._build_rows()
        finally:
            self.endResetModel()
        self.groups_rebuilt.emit()

    def _on_source_data_changed(
        self,
        top_left: QModelIndex | QPersistentModelIndex,
        bottom_right: QModelIndex | QPersistentModelIndex,
        roles: Sequence[int] | None = None,
    ) -> None:
        """원본 데이터 변경 시 그룹 구성이 그대로면 다시 그리기만 해 선택을 보존

        - 바뀐 행의 그룹 이름만 다시 확인하고, 하나라도 달라지면 행 구성을 다시 만듦
        - 그룹 이름이 그대로면 바뀐 행의 합계만 더하고 빼고 그 그룹 머리글만 다시 그림
        """
        if self._group_by == GROUP_BY_NONE:
            top = self.mapFromSource(QModelIndex(top_left))
            bottom = self.mapFromSource(QModelIndex(bottom_right))
            if not top.isValid() or not bottom.isValid():
                return
            self.dataChanged.emit(top, bottom, [] if roles is None else list(roles))
            return
        count = self._source_row_count()
        if count == 0:
            return
        top_row = min(max(top_left.row(), 0), count - 1)
        bottom_row = min(max(bottom_right.row(), top_row), count - 1)
        if self._keys_changed(top_row, bottom_row, top_left.column(), bottom_right.column()):
            self._rebuild()
            return
        self._emit_changed_cells(top_left, bottom_right, roles)
        self._apply_row_totals(top_row, bottom_row)

    def _emit_changed_cells(
        self,
        top_left: QModelIndex | QPersistentModelIndex,
        bottom_right: QModelIndex | QPersistentModelIndex,
        roles: Sequence[int] | None,
    ) -> None:
        """바뀐 원본 셀에 대응하는 표시 셀만 다시 그림

        - 한 행 변경은 그 셀만 다시 그림
        - 여러 행 변경은 표시 순서가 원본 순서와 달라지므로 표시 행 전체 범위로 다시 그림
        """
        role_list: list[int] = [] if roles is None else list(roles)
        if top_left.row() == bottom_right.row():
            top = self.mapFromSource(QModelIndex(top_left))
            bottom = self.mapFromSource(QModelIndex(bottom_right))
            if top.isValid() and bottom.isValid():
                self.dataChanged.emit(top, bottom, role_list)
            return
        last_row = self.rowCount() - 1
        if last_row < 0:
            return
        top = self.index(0, top_left.column())
        bottom = self.index(last_row, bottom_right.column())
        if top.isValid() and bottom.isValid():
            self.dataChanged.emit(top, bottom, role_list)

    def _keys_changed(
        self, top_row: int, bottom_row: int, left_column: int, right_column: int
    ) -> bool:
        """바뀐 행 범위의 그룹 이름이 하나라도 달라졌는지 확인"""
        key_columns = self._key_columns()
        if not any(left_column <= col <= right_column for col in key_columns):
            return False
        return any(
            self._group_criteria(row) != self._group_keys[row] for row in range(top_row, bottom_row + 1)
        )

    def _apply_row_totals(self, top_row: int, bottom_row: int) -> None:
        """바뀐 행의 예상 절약 차이를 그룹 합계에 반영하고 그 그룹 머리글만 다시 그림"""
        changed_groups: set[int] = set()
        for row in range(top_row, bottom_row + 1):
            saved = self._saved_mb(row)
            previous = self._row_saved[row]
            if saved == previous:
                continue
            self._row_saved[row] = saved
            group_index = self._video_rows[row]
            self._groups[group_index].saved_mb += saved - previous
            changed_groups.add(group_index)
        for group_index in changed_groups:
            row = self._header_rows.get(group_index)
            if row is not None:
                self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1), [])

    def _display_row_of_source(self, source_row: int) -> int | None:
        """소스 행의 표시 행 번호, 그룹 머리글이거나 접힌 행이면 None"""
        return self._display_rows.get(source_row)

    def is_group_row(self, row: int) -> bool:
        """표시 행이 그룹 머리글 행인지"""
        return 0 <= row < len(self._rows) and self._rows[row][0] == _HEADER_ROW

    def is_group_index(self, index: QModelIndex) -> bool:
        """모델 인덱스가 그룹 머리글 행인지"""
        return index.isValid() and self.is_group_row(index.row())

    def is_collapsed_row(self, row: int) -> bool:
        """표시 행이 접힌 그룹의 머리글인지"""
        if not self.is_group_row(row):
            return False
        return self._groups[self._rows[row][1]].label in self._collapsed

    def toggle_group(self, row: int) -> None:
        """그룹 머리글 행의 접힘 상태를 뒤집고 행 구성을 다시 만듦"""
        if not self.is_group_row(row):
            return
        label = self._groups[self._rows[row][1]].label
        if label in self._collapsed:
            self._collapsed.discard(label)
        else:
            self._collapsed.add(label)
        self.beginResetModel()
        try:
            self._rows = self._expanded_rows()
            self._reindex()
        finally:
            self.endResetModel()

    def expand_all_groups(self) -> None:
        """접힌 그룹을 모두 펼침"""
        if not self._collapsed:
            return
        self._collapsed.clear()
        self._rebuild()

    def collapse_all_groups(self) -> None:
        """모든 그룹을 접어 머리글 행만 남김"""
        if self._group_by == GROUP_BY_NONE:
            return
        labels = {group.label for group in self._groups}
        if labels == self._collapsed:
            return
        self._collapsed = labels
        self._rebuild()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        """표시 행 수, 그룹 머리글과 펼쳐진 영상 행을 포함"""
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        """원본 프록시와 같은 열 수"""
        if parent.isValid():
            return 0
        source = self.sourceModel()
        return source.columnCount() if source is not None else 0

    def index(
        self,
        row: int,
        column: int,
        parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX,
    ) -> QModelIndex:
        """단일 단계 목록의 표시 인덱스, 범위를 벗어나면 무효 인덱스"""
        if parent.isValid() or not (0 <= row < len(self._rows)) or not (0 <= column < self.columnCount()):
            return _EMPTY_INDEX
        return self.createIndex(row, column)

    def parent(self, index: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> QModelIndex:  # type: ignore[override]
        """표는 단일 단계 목록이므로 항상 무효 인덱스"""
        _ = index
        return _EMPTY_INDEX

    def mapToSource(self, proxyIndex: QModelIndex | QPersistentModelIndex) -> QModelIndex:
        """표시 인덱스에 대응하는 원본 프록시 인덱스, 그룹 머리글은 무효 인덱스"""
        row = proxyIndex.row()
        if not proxyIndex.isValid() or not (0 <= row < len(self._rows)):
            return _EMPTY_INDEX
        kind, payload = self._rows[row]
        if kind == _HEADER_ROW:
            return _EMPTY_INDEX
        source = self.sourceModel()
        if source is None:
            return _EMPTY_INDEX
        return source.index(payload, proxyIndex.column())

    def mapFromSource(self, sourceIndex: QModelIndex | QPersistentModelIndex) -> QModelIndex:
        """원본 프록시 인덱스에 대응하는 표시 인덱스, 접힌 그룹의 영상 행은 무효 인덱스"""
        if not sourceIndex.isValid():
            return _EMPTY_INDEX
        display_row = self._display_row_of_source(sourceIndex.row())
        if display_row is None:
            return _EMPTY_INDEX
        return self.index(display_row, sourceIndex.column())

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        """정렬은 필터 프록시가 원본 모델에 위임하므로 그대로 넘김"""
        source = self.sourceModel()
        if source is not None:
            source.sort(column, order)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        """그룹 머리글이면 합계 문구, 영상 행이면 원본 프록시 값을 전달"""
        row = index.row()
        if not index.isValid() or not (0 <= row < len(self._rows)):
            return None
        kind, payload = self._rows[row]
        if kind == _HEADER_ROW:
            return self._header_data(payload, role)
        source = self.sourceModel()
        if source is None:
            return None
        return source.data(source.index(payload, index.column()), role)

    def _header_data(self, group_index: int, role: int) -> Any:
        if not 0 <= group_index < len(self._groups):
            return None
        group = self._groups[group_index]
        saved = format_size_mb(group.saved_mb, SAVED_DECIMALS, unit="auto")
        if role == Qt.ItemDataRole.DisplayRole:
            return HEADER_COUNT_TEMPLATE.format(label=group.label, count=group.count, saved=saved)
        if role == Qt.ItemDataRole.ToolTipRole:
            return HEADER_TOOLTIP_TEMPLATE.format(count=group.count, saved=saved)
        if role == GROUP_HEADER_ROLE:
            return True
        if role == GROUP_EXPANDED_ROLE:
            return group.label not in self._collapsed
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        """그룹 머리글은 선택할 수 없게 하고 영상 행은 원본 플래그를 씀"""
        row = index.row()
        if not index.isValid() or not (0 <= row < len(self._rows)):
            return Qt.ItemFlag.NoItemFlags
        kind, payload = self._rows[row]
        if kind == _HEADER_ROW:
            _ = payload
            return Qt.ItemFlag.ItemIsEnabled
        source = self.sourceModel()
        if source is None:
            return Qt.ItemFlag.NoItemFlags
        return source.flags(source.index(payload, index.column()))

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        """머리글 값은 원본 프록시에 그대로 위임"""
        source = self.sourceModel()
        if source is None:
            return None
        return source.headerData(section, orientation, role)


class GroupHeaderDelegate(NameAccentDelegate):
    """그룹 머리글 행을 테마 색 접기 표시와 굵은 이름으로 그리는 델리게이트

    - 파일명 델리게이트를 상속해 영상 행의 인코딩 진행 띠와 자르기 동작을 그대로 유지
    - 머리글 행에는 선택 배경과 포커스 테두리를 쓰지 않음
    """

    def __init__(self, view: QWidget) -> None:
        super().__init__(view)
        self._font = QFont(FONT_UI_FAMILY, HEADER_FONT_PT, QFont.Weight.Bold)
        self._color_cache: dict[str, QColor] = {}

    def _color(self, hex_str: str) -> QColor:
        color = self._color_cache.get(hex_str)
        if color is None:
            color = QColor(hex_str)
            self._color_cache[hex_str] = color
        return color

    def _is_header(self, index: QModelIndex) -> bool:
        return bool(index.data(GROUP_HEADER_ROLE))

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """머리글이면 접기 표시와 이름을, 영상 행이면 파일명 델리게이트에 위임"""
        if painter is None:
            return
        if not self._is_header(index):
            super().paint(painter, option, index)
            return
        palette = ThemeManager().current_palette
        painter.save()
        painter.fillRect(option.rect, self._color(palette.subtitle_bg))
        painter.fillRect(
            option.rect.left(),
            option.rect.bottom(),
            option.rect.width(),
            HEADER_BORDER_PX,
            self._color(palette.border_subtle),
        )
        expanded = bool(index.data(GROUP_EXPANDED_ROLE))
        icon = get_icon(
            "chevron-down" if expanded else "chevron-right",
            palette=palette,
            size=CHEVRON_SIZE_PX,
        )
        rect = option.rect
        icon_x = rect.left() + HEADER_PADDING_X_PX
        icon.paint(
            painter,
            QRectF(
                float(icon_x),
                float(rect.center().y() - CHEVRON_SIZE_PX // 2),
                float(CHEVRON_SIZE_PX),
                float(CHEVRON_SIZE_PX),
            ).toRect(),
        )
        painter.setFont(self._font)
        painter.setPen(self._color(palette.subtitle_text))
        text = index.data(Qt.ItemDataRole.DisplayRole)
        painter.drawText(
            rect.adjusted(
                icon_x - rect.left() + CHEVRON_SIZE_PX + HEADER_ICON_GAP_PX,
                0,
                -HEADER_PADDING_X_PX,
                0,
            ),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "" if text is None else str(text),
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """머리글은 글꼴 높이에 위아래 여백을 더한 높이를 요청"""
        if not self._is_header(index):
            return super().sizeHint(option, index)
        return QSize(option.rect.width(), option.fontMetrics.height() + HEADER_PADDING_X_PX)

    def initStyleOption(self, option: QStyleOptionViewItem | None, index: QModelIndex) -> None:
        """머리글 행에서는 선택, 포커스 상태를 지워 배경 강조를 막음"""
        super().initStyleOption(option, index)
        if option is not None and self._is_header(index):
            option.state &= ~QStyle.StateFlag.State_Selected
            option.state &= ~QStyle.StateFlag.State_HasFocus


def sync_group_spans(view: Any, model: VideoGroupProxyModel) -> None:
    """그룹 머리글 행만 모든 열을 합쳐 한 줄로 그리도록 병합 상태를 다시 맞춤"""
    view.clearSpans()
    if model.group_by == GROUP_BY_NONE:
        return
    columns = model.columnCount()
    if columns <= 0:
        return
    for row in range(model.rowCount()):
        if model.is_group_row(row):
            view.setSpan(row, 0, 1, columns)


def handle_group_click(view: Any, index: QModelIndex) -> bool:
    """그룹 머리글 클릭이면 접힘을 뒤집고 처리 여부를 반환"""
    model = view.group_model
    if not model.is_group_index(index):
        return False
    model.toggle_group(index.row())
    return True


def filename_at_row(view: Any, row: int) -> str:
    """표시 행의 영상 파일명, 그룹 머리글 행이거나 범위를 벗어나면 빈 문자열"""
    model = view.group_model
    if not 0 <= row < model.rowCount() or model.is_group_row(row):
        return ""
    ev = view.get_evaluation_at(model.index(row, 0))
    return ev.video.filename if ev is not None else ""


def top_row_key(view: Any) -> tuple[str, int] | None:
    """뷰포트 맨 위 영상 행의 (파일명, 행 번호), 머리글만 보이면 다음 영상 행을 씀"""
    start = view.rowAt(0)
    if start < 0:
        return None
    for row in range(start, view.group_model.rowCount()):
        name = filename_at_row(view, row)
        if name:
            return name, row
    return None


def nearest_video_row(view: Any, filename: str, row: int) -> int:
    """복원할 행을 파일명으로 찾고, 없으면 저장한 행 번호에서 가장 가까운 영상 행을 씀"""
    model = view.group_model
    count = model.rowCount()
    if filename:
        match = next((r for r in range(count) if filename_at_row(view, r) == filename), None)
        if match is not None:
            return match
    target = min(max(row, 0), count - 1)
    for candidate in (range(target, count), range(target - 1, -1, -1)):
        for r in candidate:
            if not model.is_group_row(r):
                return r
    return target


def snap_cursor_row(view: Any, target: int, cursor_action: int) -> int:
    """키보드 이동 목표 행이 그룹 머리글이면 같은 방향의 다음 영상 행으로 넘김

    - 묶기가 없거나 그룹 머리글이 아니면 목표 행을 그대로 씀
    - 그 방향에 영상 행이 없으면 현재 영상 행에 머물러 머리글에 포커스가 가지 않게 함
    """
    model = view.group_model
    if model.group_by == GROUP_BY_NONE or not model.is_group_row(target):
        return target
    if cursor_action in _DOWNWARD_CURSOR_ACTIONS:
        step = 1
    elif cursor_action in _UPWARD_CURSOR_ACTIONS:
        step = -1
    else:
        return target
    row = target + step
    while 0 <= row < model.rowCount():
        if not model.is_group_row(row):
            return row
        row += step
    current = view.currentIndex().row()
    if current >= 0 and not model.is_group_row(current):
        return current
    return target


def cursor_index(view: Any, target: QModelIndex, cursor_action: int) -> QModelIndex:
    """키보드 이동 결과가 그룹 머리글이면 영상 행 인덱스로 바꿔 반환"""
    row = snap_cursor_row(view, target.row(), cursor_action)
    if row == target.row():
        return target
    return view.group_model.index(row, max(target.column(), 0))


def move_current_video_row(view: Any, step: int) -> None:
    """포커스 행을 step만큼 옮기고 그 행 하나만 선택, 영상 파일이 없는 행은 건너뜀"""
    sel_model = view.selectionModel()
    count = view.group_model.rowCount()
    if sel_model is None or count == 0:
        return
    current = view.currentIndex().row()
    row = (current if current >= 0 else (-1 if step > 0 else count)) + step
    while 0 <= row < count:
        if view.video_path_at_row(row) is not None:
            sel_model.setCurrentIndex(
                view.group_model.index(row, 0),
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            view.scrollTo(view.group_model.index(row, 0))
            return
        row += step


def install_group_proxy(view: Any) -> VideoGroupProxyModel:
    """표 뷰의 표시 모델을 그룹 프록시로 바꾸고 머리글 병합 동기화를 연결"""
    model = VideoGroupProxyModel(view)
    model.setSourceModel(view.proxy_model)
    view.setModel(model)
    model.modelReset.connect(lambda: sync_group_spans(view, model))
    sync_group_spans(view, model)
    return model


class RowGroupingCoordinator:
    """행 묶기 선택을 표, 필터 바 버튼, 저장 상태에 함께 반영"""

    def __init__(
        self,
        table_view: Any,
        filter_bar: Any,
        ui_state: Any,
        persist: Any,
    ) -> None:
        self._view = table_view
        self._filter_bar = filter_bar
        self._ui = ui_state
        self._persist = persist
        filter_bar.row_group_selected.connect(self.apply_group_by)
        filter_bar.expand_all_groups_requested.connect(self._view.group_model.expand_all_groups)
        filter_bar.collapse_all_groups_requested.connect(self._view.group_model.collapse_all_groups)
        # - 필터, 검색, 정렬, 데이터 변경으로 그룹 구성이 다시 만들어지면 버튼의 그룹 수를 갱신
        self._view.group_model.groups_rebuilt.connect(self._refresh_button)
        self.sync_from_state()

    @property
    def group_by(self) -> str:
        """지금 적용 중인 묶기 키"""
        return self._view.group_model.group_by

    def apply_group_by(self, group_by: str) -> None:
        """사용자가 고른 묶기 키를 표와 저장 상태에 적용"""
        self._view.group_model.set_group_by(group_by)
        self._ui.filters.group_by = self._view.group_model.group_by
        self.sync_options()
        self._persist()

    def sync_from_state(self) -> None:
        """저장된 묶기 키를 표에 적용, 숨김 열의 묶기도 그대로 유지"""
        self._view.group_model.set_group_by(self._ui.filters.group_by)
        self.sync_options()

    def sync_options(self) -> None:
        """묶기 메뉴 항목과 버튼 글자를 지금 상태에 맞춤"""
        self._filter_bar.set_row_group_options(group_options())
        self._refresh_button()

    def _refresh_button(self) -> None:
        """지금 묶기 키와 그룹 수만 버튼에 반영, 메뉴는 다음에 열 때 다시 만듦"""
        self._filter_bar.set_row_group(
            self._view.group_model.group_by,
            label=group_label(self._view.group_model.group_by),
            count=self._view.group_model.group_count,
        )


def attach_row_grouping(
    table_view: Any,
    filter_bar: Any,
    ui_state: Any,
    persist: Any,
) -> RowGroupingCoordinator:
    """필터 바의 행 묶기 드롭다운을 표 그룹 프록시에 연결"""
    return RowGroupingCoordinator(table_view, filter_bar, ui_state, persist)
