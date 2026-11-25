"""비디오 평가 테이블 뷰, 열 폭 관리 및 컨텍스트 메뉴 위젯 모듈"""

from collections.abc import Callable, Iterable
from typing import ClassVar

from PyQt6.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QPoint,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QKeyEvent,
    QMouseEvent,
    QResizeEvent,
    QShowEvent,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QTableView,
    QWidget,
)

from src.core.models import VideoEvaluation
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.table.column_filter_popover import ColumnFilterPopover
from src.gui.table.column_filters import ColumnDataType
from src.gui.table.column_sizing import (
    DEFAULT_COL_ORDER as _DEFAULT_COL_ORDER,
)
from src.gui.table.column_sizing import (
    DEFAULT_HIDDEN_COLS as _DEFAULT_HIDDEN_COLS,
)
from src.gui.table.column_sizing import (
    MIN_COL_WIDTHS as _MIN_COL_WIDTHS,
)
from src.gui.table.column_sizing import (
    TextWidthCache,
    measure_column_content_widths,
    measure_name_content_width,
)
from src.gui.table.filter_header_view import FilterHeaderView
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.reanalysis_blur_delegate import install_table_delegates
from src.gui.table.table_actions import (
    confirm_bulk_play,
    copy_lines_to_clipboard,
    count_missing_video_files,
    existing_video_paths,
    launch_video_file,
    missing_notice,
    reveal_video_file,
    row_file_state,
    split_exception_stems,
)
from src.gui.table.table_context_menu import (
    show_header_context_menu,
    show_row_context_menu,
)
from src.gui.table.table_drag import (
    collect_draggable_file_paths,
    start_table_file_drag,
)
from src.gui.table.table_empty_overlay import TableEmptyOverlay
from src.gui.table.table_grouping import (
    GroupHeaderDelegate,
    cursor_index,
    handle_group_click,
    install_group_proxy,
    move_current_video_row,
    nearest_video_row,
    top_row_key,
)
from src.gui.table.table_model import VideoTableModel
from src.gui.table.table_overlay_layout import TableOverlays
from src.gui.widgets.edge_fade_overlay import HorizontalEdgeFadeOverlay

_DEFAULT_ROW_HEIGHT: int = 34
_MIN_COLUMN_WIDTH: int = 36
_H_SCROLL_SINGLE_STEP_PX: int = 20


class VideoTableView(QTableView):
    """비디오 평가 목록 표시용 테이블 뷰 위젯

    열 너비 설정, 뱃지 델리게이트, 다중 선택, Empty State 안내 및 컨텍스트 메뉴 제공
    """

    exception_keyword_added = pyqtSignal(str)
    exception_keywords_added = pyqtSignal(list)
    exception_keywords_removed = pyqtSignal(list)
    # - (대상 영상 목록, 직접 지정 값 또는 None(자동))
    manual_target_requested = pyqtSignal(list, object)
    selection_changed = pyqtSignal(int)
    current_video_changed = pyqtSignal()
    analyze_fast_requested = pyqtSignal()
    analyze_full_requested = pyqtSignal()
    analyze_mode_changed = pyqtSignal(str)
    reset_filters_requested = pyqtSignal()
    external_drag_finished = pyqtSignal(list)
    file_notice = pyqtSignal(str)
    selection_history_changed = pyqtSignal(bool, bool)

    BULK_PLAY_CONFIRM_THRESHOLD: ClassVar[int] = 10
    MAX_SELECTION_HISTORY: ClassVar[int] = 20

    # 열 최소 폭과 기본 열 순서, 숨김 열은 column_sizing 모듈의 정책 데이터를 참조
    MIN_COL_WIDTHS: ClassVar[dict[int, int]] = _MIN_COL_WIDTHS
    DEFAULT_COL_ORDER: ClassVar[tuple[int, ...]] = _DEFAULT_COL_ORDER
    DEFAULT_HIDDEN_COLS: ClassVar[frozenset[int]] = _DEFAULT_HIDDEN_COLS

    def __init__(
        self,
        root_dir_provider: Callable[[], str] | None = None,
        exceptions_provider: Callable[[], list[str]] | None = None,
        can_start_drag_provider: Callable[[], bool] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._root_dir_provider = root_dir_provider
        self._exceptions_provider = exceptions_provider
        self._can_start_drag_provider = can_start_drag_provider
        self._drag_start_pos: QPoint | None = None
        self._pending_single_select_index: QModelIndex | None = None
        self._selection_history: list[frozenset[str]] = []
        self._selection_redo_history: list[frozenset[str]] = []
        self._last_recorded_selection: frozenset[str] = frozenset()
        self._selection_sync_depth: int = 0
        self._pending_top: tuple[str, int] | None = None
        self._pending_selection: set[str] | None = None
        # - 기본 Return 재생은 이 위젯이 직접 처리하고, 다른 키로 바뀌면 QAction 단축키가 맡음
        self._play_shortcut: str = DEFAULT_SHORTCUTS["classify.play_selected"]

        self.table_model = VideoTableModel(self)
        self.proxy_model = VideoSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.table_model)
        self.group_model = install_group_proxy(self)
        self._active_popover: ColumnFilterPopover | None = None
        self._user_resized_name_col: bool = False
        self._user_resized_cols: set[int] = set()
        self._content_widths: dict[int, int] = {}
        self._programmatic_col_resize: bool = False
        self._in_fit_name_col: bool = False
        self._name_content_width: int | None = None
        self._text_widths = TextWidthCache()
        # - 재설정 직후 표를 먼저 그리고 열 폭은 이벤트 루프에서 한 번만 계산하도록 지연
        self._width_timer = QTimer(self)
        self._width_timer.setSingleShot(True)
        self._width_timer.setInterval(0)
        self._width_timer.timeout.connect(self._refresh_content_widths)

        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.setShowGrid(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._apply_horizontal_scroll_step()
        vheader = self.verticalHeader()
        if vheader is not None:
            vheader.setVisible(False)
            vheader.setDefaultSectionSize(_DEFAULT_ROW_HEIGHT)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.doubleClicked.connect(self._on_double_clicked)

        self.name_delegate = GroupHeaderDelegate(self)
        self.setItemDelegateForColumn(VideoTableModel.COL_NAME, self.name_delegate)
        self.badge_delegate, self.icon_text_delegate, self._hover_tracker = install_table_delegates(self)

        self.filter_header = FilterHeaderView(Qt.Orientation.Horizontal, self)
        self.setHorizontalHeader(self.filter_header)
        # 커스텀 수평 헤더 교체 이후 정렬을 활성화해야 sectionsClickable()이 적용됨
        self.setSortingEnabled(True)
        self.filter_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.filter_header.setSectionsMovable(True)
        self.filter_header.setStretchLastSection(False)
        self.filter_header.setMinimumSectionSize(_MIN_COLUMN_WIDTH)
        self.filter_header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.filter_header.customContextMenuRequested.connect(
            self._on_header_context_menu
        )
        self.filter_header.filter_clicked.connect(self.open_column_filter_popover)
        self.filter_header.sectionClicked.connect(self._on_header_section_clicked)
        self.filter_header.sortIndicatorChanged.connect(
            self._on_sort_indicator_changed
        )
        self.filter_header.sectionResized.connect(self._on_header_section_resized)
        self.table_model.modelReset.connect(self._on_model_reset)
        self.table_model.config_changed.connect(self._on_config_changed)
        # - 시작 직후에는 행이 없다가 분석 결과가 들어오므로 행이 생긴 뒤에 맨 위 복원을 적용
        self.proxy_model.modelReset.connect(self._apply_pending_top_restore)
        self.proxy_model.layoutChanged.connect(self._apply_pending_top_restore)
        self.proxy_model.modelReset.connect(self._apply_pending_selection_restore)
        self.proxy_model.layoutChanged.connect(self._apply_pending_selection_restore)
        self.proxy_model.column_filters_changed.connect(
            self._sync_column_filter_indicators
        )

        self.reset_columns()
        self.reset_table_sort()

        sel_model = self.selectionModel()
        if sel_model is not None:
            sel_model.selectionChanged.connect(self._on_selection_changed)

        self._init_overlays()
        self.update_empty_state(total_count=0, visible_count=0)

    def _sync_edge_fade_overlays(self) -> None:
        if hasattr(self, "_overlays"):
            self._overlays.sync_edge_fades()

    def _init_overlays(self) -> None:
        self._overlays = TableOverlays(self, self.filter_header.viewport())
        vp = self.viewport()
        if vp is not None:
            vp.installEventFilter(self)
        header_vp = self.filter_header.viewport()
        if header_vp is not None:
            header_vp.installEventFilter(self)
        empty = self._overlays.empty
        empty.analyze_fast_requested.connect(self.analyze_fast_requested.emit)
        empty.analyze_full_requested.connect(self.analyze_full_requested.emit)
        empty.analyze_mode_changed.connect(self.analyze_mode_changed.emit)
        empty.reset_filters_requested.connect(self.reset_filters_requested.emit)

    @property
    def empty_overlay(self) -> TableEmptyOverlay:
        """빈 상태 오버레이(생성 전 접근 시 AttributeError)"""
        return self._overlays.empty

    @property
    def vp_fade_overlay(self) -> HorizontalEdgeFadeOverlay | None:
        """뷰포트 가로 끝 페이드 오버레이"""
        return self._overlays.vp_fade

    @property
    def header_fade_overlay(self) -> HorizontalEdgeFadeOverlay | None:
        """헤더 뷰포트 가로 끝 페이드 오버레이"""
        return self._overlays.header_fade

    def update_empty_state(self, total_count: int, visible_count: int) -> None:
        """전체, 가시 행 수에 맞춰 빈 데이터 또는 필터 결과 없음 오버레이 전환"""
        if visible_count > 0:
            self.empty_overlay.hide()
            self._sync_edge_fade_overlays()
            return
        self.empty_overlay.apply_state(total_count)
        self._sync_empty_overlay_geometry()
        self.empty_overlay.raise_()
        self.empty_overlay.show()
        self._sync_edge_fade_overlays()

    def _sync_empty_overlay_geometry(self) -> None:
        if hasattr(self, "_overlays"):
            self._overlays.sync_geometry()

    def set_empty_analyze_mode(self, mode: str) -> None:
        """빈 데이터 안내 스플릿 버튼의 스캔 모드를 동기화"""
        self.empty_overlay.set_analyze_mode(mode)

    def set_empty_action_enabled(self, enabled: bool) -> None:
        """빈 상태 오버레이 내 유도 액션 버튼 활성화 여부 설정"""
        self.empty_overlay.set_actions_enabled(enabled)

    def showEvent(self, event: QShowEvent | None) -> None:
        """최초 표시 시 뷰포트 실제 크기에 맞춰 빈 상태 오버레이 영역을 동기화"""
        super().showEvent(event)
        self._sync_empty_overlay_geometry()

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """뷰포트 이동, 리사이즈, 표시 시 빈 상태 오버레이와 경계 페이드, 이름 열 너비를 동기화"""
        if a1 is not None and a1.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Move,
            QEvent.Type.Show,
        ):
            if a0 is self.viewport():
                self._sync_empty_overlay_geometry()
                if a1.type() == QEvent.Type.Resize:
                    self.fit_name_column_to_contents()
            elif a0 is self.filter_header.viewport():
                self._sync_empty_overlay_geometry()
        return super().eventFilter(a0, a1)

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        """뷰포트 크기 변경 시 빈 상태 오버레이와 이름 열 여백 채우기 너비 동기화"""
        super().resizeEvent(event)
        self._sync_empty_overlay_geometry()
        self.fit_name_column_to_contents()

    def _set_col_width_programmatic(self, col_idx: int, width: int) -> None:
        self._programmatic_col_resize = True
        try:
            self.setColumnWidth(col_idx, width)
        finally:
            self._programmatic_col_resize = False

    def _on_header_section_resized(
        self, logical_col: int, _old_w: int, _new_w: int
    ) -> None:
        if self._programmatic_col_resize:
            return
        if logical_col == VideoTableModel.COL_NAME:
            self._user_resized_name_col = True
        else:
            self._user_resized_cols.add(logical_col)
            self.fit_name_column_to_contents()

    def _on_config_changed(self) -> None:
        """설정 변경 뒤 보이는 행만 다시 그리도록 전체 뷰를 갱신(전 행 dataChanged 대체)"""
        self.update()
        vp = self.viewport()
        if vp is not None:
            vp.update()

    def _on_model_reset(self) -> None:
        if self.table_model.name_list_changed:
            self._name_content_width = None
        self._width_timer.start()

    def _refresh_content_widths(self) -> None:
        self._content_widths = measure_column_content_widths(
            self.table_model, self.font(), self._text_widths
        )
        self._apply_column_widths()
        self.fit_name_column_to_contents()

    def _apply_column_widths(self) -> None:
        """열별 최소 폭과 내용 폭 중 큰 값을 적용하고 사용자가 조절한 열은 현재 폭 이상을 유지"""
        for col in range(self.table_model.columnCount()):
            if col == VideoTableModel.COL_NAME:
                continue
            floor = max(self.MIN_COL_WIDTHS.get(col, 0), self._content_widths.get(col, 0))
            target = max(floor, self.columnWidth(col)) if col in self._user_resized_cols else floor
            if self.columnWidth(col) != target:
                self._set_col_width_programmatic(col, target)

    def keep_restored_column_widths(self) -> None:
        """저장된 헤더 상태로 복원한 열 폭을 사용자 조절 폭으로 간주해 데이터 로드 시 보존"""
        self._user_resized_cols = set(range(self.table_model.columnCount())) - {VideoTableModel.COL_NAME}

    def _measure_name_content_width(self) -> int:
        # 행 수에 비례하는 측정이라 데이터 로드 시에만 갱신하고 리사이즈에서는 캐시를 재사용
        if self._name_content_width is None:
            self._name_content_width = measure_name_content_width(
                self.table_model, self.font(), self._text_widths
            )
        return self._name_content_width

    def _calc_viewport_fill_name_width(self) -> int:
        vp = self.viewport()
        if vp is None or not self.isVisible():
            return 0
        vp_w = vp.width()
        if vp_w <= 0:
            return 0
        other_w = sum(
            self.columnWidth(c)
            for c in range(self.table_model.columnCount())
            if c != VideoTableModel.COL_NAME and not self.isColumnHidden(c)
        )
        return max(vp_w - other_w, 0)

    def fit_name_column_to_contents(self, *, force: bool = False) -> int:
        """파일명 길이와 창 뷰포트 남는 너비에 맞춰 이름 열 너비를 자동 조정"""
        if self._in_fit_name_col or (self._user_resized_name_col and not force):
            return self.columnWidth(VideoTableModel.COL_NAME)

        self._in_fit_name_col = True
        try:
            content_w = self._measure_name_content_width()
            fill_w = self._calc_viewport_fill_name_width()
            target_w = max(content_w, fill_w)
            if self.columnWidth(VideoTableModel.COL_NAME) != target_w:
                self._set_col_width_programmatic(VideoTableModel.COL_NAME, target_w)
            return target_w
        finally:
            self._in_fit_name_col = False

    def reset_columns(self) -> None:
        """열 순서, 표시 여부를 기본값으로 되돌리고 열 폭을 내용 기준으로 재설정"""
        self._user_resized_name_col = False
        self._user_resized_cols.clear()
        header = self.filter_header
        self._programmatic_col_resize = True
        try:
            for target_visual, logical_idx in enumerate(self.DEFAULT_COL_ORDER):
                current_visual = header.visualIndex(logical_idx)
                if current_visual != target_visual:
                    header.moveSection(current_visual, target_visual)
            for col_idx in range(self.table_model.columnCount()):
                self.setColumnHidden(col_idx, col_idx in self.DEFAULT_HIDDEN_COLS)
        finally:
            self._programmatic_col_resize = False
        self._apply_column_widths()
        self.fit_name_column_to_contents(force=True)

    def sync_name_column_user_resize_from_current(self) -> None:
        """저장된 헤더 상태 복원 직후 이름 열의 창 너비 자동 채움 상태를 복구"""
        # 이전 해상도, DPI에서 자동 확장된 이름 열 너비를 수동 고정으로 오인하면 오른쪽 여백이 생김
        self._user_resized_name_col = False

    def reset_table_sort(self) -> None:
        """정렬 화살표와 다중 정렬 스택을 초기화해 기본(파일명 자연 정렬) 순서로 복원"""
        self.filter_header.setSortIndicatorShown(False)
        self.filter_header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.proxy_model.sort(-1)

    def _on_header_section_clicked(self, col: int) -> None:
        # 정렬 화살표가 꺼진 초기 상태에서는 첫 클릭을 오름차순으로 활성화해 엑셀식 2단 토글 진입
        if col >= 0 and not self.filter_header.isSortIndicatorShown():
            self.filter_header.setSortIndicatorShown(True)
            self.filter_header.setSortIndicator(col, Qt.SortOrder.AscendingOrder)

    def _on_sort_indicator_changed(self, col: int, _order: Qt.SortOrder) -> None:
        if col >= 0 and not self.filter_header.isSortIndicatorShown():
            self.filter_header.setSortIndicatorShown(True)

    def _sync_column_filter_indicators(self) -> None:
        active = set(self.proxy_model.get_active_column_filters())
        self.filter_header.set_active_filter_columns(active)
        self.table_model.set_filter_summaries(
            self.proxy_model.get_column_filter_summaries()
        )

    def open_column_filter_popover(
        self, col: int, anchor_global: QPoint | None = None
    ) -> ColumnFilterPopover | None:
        """지정 열의 데이터 타입에 맞는 동적 필터 팝오버를 열어 표시"""
        if not (0 <= col < len(VideoTableModel.HEADERS)):
            return None
        # - 이전 팝오버는 닫고 지워 열 때마다 표 뷰 아래 자식이 쌓이지 않게 함(최대 한 개 유지)
        if self._active_popover is not None:
            self._active_popover.close()
            self._active_popover.deleteLater()
            self._active_popover = None

        spec = VideoTableModel.get_column_spec(col)
        is_cat = spec.data_type in (ColumnDataType.ENUM, ColumnDataType.CATEGORY)
        is_num = spec.data_type is ColumnDataType.NUMBER
        popover = ColumnFilterPopover(
            col=col,
            title=VideoTableModel.MENU_HEADERS[col],
            spec=spec,
            current_rule=self.proxy_model.get_column_filter(col),
            value_counts=self.table_model.collect_column_value_counts(col) if is_cat else [],
            numeric_bounds=(
                self.table_model.collect_numeric_bounds(col)
                if is_num
                else (None, None, 0)
            ),
            number_bucket_counts=(
                self.table_model.collect_numeric_bucket_counts(col) if is_num else []
            ),
            toggle_rect_global=self.filter_header.filter_button_global_rect(col),
            parent=self,
        )
        popover.filter_changed.connect(self.proxy_model.set_column_filter)
        self._active_popover = popover
        popover.popup_at(anchor_global or self.filter_header.section_anchor_global(col))
        return popover

    def _on_header_context_menu(self, pos: QPoint) -> None:
        show_header_context_menu(self, pos)

    def _selected_proxy_rows(self) -> list[int]:
        """선택 영역의 표시 행 번호를 오름차순으로 반환, 그룹 머리글 행은 제외

        - 행 선택 모드에서는 선택 범위의 행 번호가 selectedRows와 같으며, 열 수만큼 인덱스를 만들지 않음
        """
        sel_model = self.selectionModel()
        if sel_model is None:
            return []
        rows: set[int] = set()
        for selection_range in sel_model.selection():
            rows.update(range(selection_range.top(), selection_range.bottom() + 1))
        return sorted(row for row in rows if not self.group_model.is_group_row(row))

    def get_selected_evaluations(self) -> list[VideoEvaluation]:
        """현재 선택된 모든 행의 VideoEvaluation 객체 목록 반환"""
        result: list[VideoEvaluation] = []
        for row in self._selected_proxy_rows():
            ev = self.get_evaluation_at(self.group_model.index(row, 0))
            if ev is not None:
                result.append(ev)
        return result

    def _on_selection_changed(
        self, _selected: QItemSelection, _deselected: QItemSelection
    ) -> None:
        if self._selection_sync_depth == 0:
            curr = frozenset(self.get_selected_filenames())
            prev = self._last_recorded_selection
            if curr != prev:
                history_dirty = bool(self._selection_redo_history)
                self._selection_redo_history.clear()
                if len(prev) >= 2 and not prev.issubset(curr):
                    before_len = len(self._selection_history)
                    self._push_selection_entry(self._selection_history, prev)
                    history_dirty = history_dirty or len(self._selection_history) != before_len
                if history_dirty:
                    self._emit_selection_history_state()
                self._last_recorded_selection = curr
        self.selection_changed.emit(len(self._selected_proxy_rows()))

    def currentChanged(self, current: QModelIndex, previous: QModelIndex) -> None:
        """포커스 행이 클릭, 방향키, 프로그램 호출로 바뀌면 미리 보기 같은 구독자에게 알림"""
        super().currentChanged(current, previous)
        self.current_video_changed.emit()

    def get_evaluation_at(self, proxy_index: QModelIndex) -> VideoEvaluation | None:
        """표시 인덱스에 해당하는 원본 VideoEvaluation 객체 반환, 그룹 머리글 행이면 None"""
        if not proxy_index.isValid():
            return None
        src_idx = self.proxy_model.mapToSource(self.group_model.mapToSource(proxy_index))
        return self.table_model.data(src_idx, VideoTableModel.EVAL_ROLE)

    def _resolve_root_dir(self) -> str:
        return self._root_dir_provider() if self._root_dir_provider else ""

    def _resolve_target_evals(
        self, evals: list[VideoEvaluation] | None
    ) -> list[VideoEvaluation]:
        return evals if evals is not None else self.get_selected_evaluations()

    def _resolve_root_and_targets(
        self, evals: list[VideoEvaluation] | None
    ) -> tuple[str, list[VideoEvaluation]]:
        root_dir = self._resolve_root_dir()
        if not root_dir:
            return "", []
        return root_dir, self._resolve_target_evals(evals)

    def _split_exception_stems(
        self, evals: list[VideoEvaluation]
    ) -> tuple[list[str], list[str]]:
        tokens = self._exceptions_provider() if self._exceptions_provider else []
        return split_exception_stems(evals, tokens)

    def play_selected(self, evals: list[VideoEvaluation] | None = None) -> int:
        """선택된 영상 파일들을 기본 플레이어로 일괄 실행하고 실행 건수 반환(대량은 확인 후)"""
        root_dir, target = self._resolve_root_and_targets(evals)
        if not root_dir or not confirm_bulk_play(self, len(target), self.BULK_PLAY_CONFIRM_THRESHOLD):
            return 0
        self._notify_missing(root_dir, target)
        return sum(1 for ev in target if launch_video_file(root_dir, ev))

    def _notify_missing(self, root_dir: str, evals: list[VideoEvaluation]) -> bool:
        """대상 중 영상 파일이 없는 항목이 있으면 안내를 내보내고 안내 여부를 반환"""
        notice = missing_notice(count_missing_video_files(root_dir, evals), len(evals))
        if notice is not None:
            self.file_notice.emit(notice)
        return notice is not None

    def notify_missing_selected(self) -> bool:
        """선택 항목이 모두 영상 파일이 없으면 안내를 내보내고 안내 여부를 반환"""
        return self._notify_missing(self._resolve_root_dir(), self.get_selected_evaluations())

    def notify_current_missing(self) -> None:
        """포커스 행의 영상 파일이 없으면 안내"""
        ev = self.get_evaluation_at(self.currentIndex())
        if ev is not None:
            self._notify_missing(self._resolve_root_dir(), [ev])

    def video_path_at_row(self, row: int) -> str | None:
        """표시 순서의 행 번호에 해당하는 영상 파일 절대 경로, 행이 없거나 파일이 없으면 None"""
        if row < 0 or row >= self.group_model.rowCount():
            return None
        ev = self.get_evaluation_at(self.group_model.index(row, 0))
        root_dir = self._resolve_root_dir()
        if ev is None or not root_dir:
            return None
        return row_file_state(root_dir, ev)[1]

    def topmost_selected_video_row(self) -> int | None:
        """선택된 행 중 가장 위에 있고 영상 파일이 있는 행 번호, 없으면 None"""
        for row in self._selected_proxy_rows():
            if self.video_path_at_row(row) is not None:
                return row
        return None

    def current_video_path(self) -> str | None:
        """현재 포커스 행의 영상 파일 경로, 없으면 None"""
        return self.video_path_at_row(self.currentIndex().row())

    def set_current_row(self, row: int) -> None:
        """선택 범위는 그대로 두고 포커스 행만 옮김"""
        sel_model = self.selectionModel()
        if sel_model is None or not (0 <= row < self.group_model.rowCount()):
            return
        sel_model.setCurrentIndex(
            self.group_model.index(row, 0), QItemSelectionModel.SelectionFlag.NoUpdate
        )

    def move_current_row(self, step: int) -> None:
        """포커스 행을 step만큼 옮기고 그 행 하나만 선택, 파일이 없는 행은 건너뜀"""
        move_current_video_row(self, step)

    def moveCursor(
        self, cursor_action: QAbstractItemView.CursorAction, modifiers: Qt.KeyboardModifier
    ) -> QModelIndex:
        """키보드 이동 결과가 그룹 머리글 행이면 같은 방향의 영상 행으로 넘김"""
        target = super().moveCursor(cursor_action, modifiers)
        return cursor_index(self, target, cursor_action.value)

    def reveal_selected(self, target: VideoEvaluation | None = None) -> bool:
        """선택된 첫 번째 영상(또는 지정 항목)의 위치를 탐색기에서 열어 표시"""
        root_dir = self._resolve_root_dir()
        if not root_dir:
            return False
        if target is None:
            selected = self.get_selected_evaluations()
            if not selected:
                return False
            target = selected[0]
        revealed = reveal_video_file(root_dir, target)
        if not revealed:
            self._notify_missing(root_dir, [target])
        return revealed

    def copy_selected_names(self, evals: list[VideoEvaluation] | None = None) -> int:
        """선택된 영상들의 파일명을 줄바꿈 구분으로 클립보드에 복사"""
        target = self._resolve_target_evals(evals)
        return copy_lines_to_clipboard([e.video.filename for e in target])

    def copy_selected_paths(self, evals: list[VideoEvaluation] | None = None) -> int:
        """선택된 영상들의 전체 절대 경로를 줄바꿈 구분으로 클립보드에 복사"""
        root_dir, target = self._resolve_root_and_targets(evals)
        if not root_dir:
            return 0
        self._notify_missing(root_dir, target)
        return copy_lines_to_clipboard(existing_video_paths(root_dir, target))

    def add_selected_to_exceptions(
        self, evals: list[VideoEvaluation] | None = None
    ) -> int:
        """선택된 영상 중 미등록 파일명 줄기를 예외 규칙 추가 시그널로 전달"""
        to_add, _ = self._split_exception_stems(self._resolve_target_evals(evals))
        if not to_add:
            return 0
        if len(to_add) == 1:
            self.exception_keyword_added.emit(to_add[0])
        else:
            self.exception_keywords_added.emit(to_add)
        return len(to_add)

    def remove_selected_from_exceptions(
        self, evals: list[VideoEvaluation] | None = None
    ) -> int:
        """선택된 영상 중 이미 등록된 파일명 줄기를 예외 규칙 제거 시그널로 전달"""
        _, to_remove = self._split_exception_stems(self._resolve_target_evals(evals))
        if not to_remove:
            return 0
        self.exception_keywords_removed.emit(to_remove)
        return len(to_remove)

    def get_selected_filenames(self) -> set[str]:
        """현재 선택된 행들의 파일명 집합 반환"""
        return {ev.video.filename for ev in self.get_selected_evaluations()}

    @property
    def can_restore_selection(self) -> bool:
        """복원 가능한 직전 다중 선택 기록이 있는지 반환"""
        return bool(self._selection_history)

    @property
    def can_redo_selection(self) -> bool:
        """되돌릴 수 있는 선택 복원 다시 실행 기록이 있는지 반환"""
        return bool(self._selection_redo_history)

    def _emit_selection_history_state(self) -> None:
        self.selection_history_changed.emit(
            bool(self._selection_history), bool(self._selection_redo_history)
        )

    def _push_selection_entry(self, stack: list[frozenset[str]], entry: frozenset[str]) -> None:
        if not stack or stack[-1] != entry:
            stack.append(entry)
            if len(stack) > self.MAX_SELECTION_HISTORY:
                del stack[0]

    def set_selection_sync_guard(self, active: bool) -> None:
        """재평가, 선택 복원 같은 내부 선택 동기화 동안 선택 히스토리가 오염되지 않게 보호"""
        if active:
            self._selection_sync_depth += 1
            return
        if self._selection_sync_depth <= 0:
            return
        self._selection_sync_depth -= 1
        if self._selection_sync_depth == 0:
            self._last_recorded_selection = frozenset(self.get_selected_filenames())
            self.selection_changed.emit(len(self._selected_proxy_rows()))

    def clear_selection_history(self) -> None:
        """작업 폴더 전환 등 기존 선택 문맥이 끊길 때 선택 복원 스택을 비움"""
        had_history = bool(self._selection_history or self._selection_redo_history)
        self._selection_history.clear()
        self._selection_redo_history.clear()
        self._last_recorded_selection = frozenset(self.get_selected_filenames())
        if had_history:
            self._emit_selection_history_state()

    def request_selection_restore(self, filenames: Iterable[str]) -> None:
        """다음에 행이 채워질 때 저장해 둔 선택 파일명 집합을 복원하도록 예약"""
        names = {name for name in filenames if name}
        self._pending_selection = names or None

    def take_pending_selection(self) -> set[str] | None:
        """예약된 시작, 폴더 전환 선택 복원 목록을 꺼내 반환하고 비움"""
        pending = self._pending_selection
        self._pending_selection = None
        return pending

    def _apply_pending_selection_restore(self) -> None:
        if self._pending_selection is None or self._selection_sync_depth > 0 or self.group_model.rowCount() == 0:
            return
        if pending := self.take_pending_selection():
            self.select_filenames(pending)

    def select_filenames(self, filenames: set[str]) -> None:
        """파일명이 일치하는 행만 선택 상태로 복원(재평가로 모델이 초기화된 뒤 선택 유지용)"""
        sel_model = self.selectionModel()
        if sel_model is None:
            return
        self.set_selection_sync_guard(True)
        try:
            sel_model.clearSelection()
            for row in range(self.group_model.rowCount()):
                idx = self.group_model.index(row, 0)
                ev = self.get_evaluation_at(idx)
                if ev is not None and ev.video.filename in filenames:
                    sel_model.select(
                        idx,
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                    )
        finally:
            self.set_selection_sync_guard(False)

    def _step_selection_history(self, *, forward: bool) -> int | None:
        src = self._selection_redo_history if forward else self._selection_history
        dst = self._selection_history if forward else self._selection_redo_history
        curr = frozenset(self.get_selected_filenames())
        available = {
            ev.video.filename
            for row in range(self.group_model.rowCount())
            if (ev := self.get_evaluation_at(self.group_model.index(row, 0))) is not None
        }
        while src:
            candidate = src.pop()
            if candidate:
                target = candidate & available
                if not target or target == curr:
                    continue
            elif not forward or not curr:
                continue
            else:
                target = set()
            self._push_selection_entry(dst, curr)
            self.select_filenames(set(target))
            self._emit_selection_history_state()
            return len(target)
        self._emit_selection_history_state()
        return None

    def restore_previous_selection(self) -> int:
        """실수로 해제되거나 바뀐 직전 다중 선택을 복원하고 복원된 행 수를 반환"""
        return restored if (restored := self._step_selection_history(forward=False)) is not None else 0

    def redo_selection_restore(self) -> int | None:
        """복원했던 이전 선택을 취소해 직전 선택으로 되돌리고 선택된 행 수를 반환(없으면 None)"""
        return self._step_selection_history(forward=True)

    def top_visible_row_key(self) -> tuple[str, int] | None:
        """뷰포트 맨 위 영상 행의 (파일명, 행 번호), 표가 비었으면 None"""
        return top_row_key(self)

    def request_top_restore(self, filename: str, row: int) -> None:
        """다음에 행이 채워질 때 저장한 파일을 맨 위로 옮김, 파일이 없으면 저장한 행 번호 근처로 이동"""
        self._pending_top = (filename, row)

    def _apply_pending_top_restore(self) -> None:
        if self._pending_top is None:
            return
        count = self.group_model.rowCount()
        if count == 0:
            return
        filename, row = self._pending_top
        self._pending_top = None
        target = nearest_video_row(self, filename, row)
        # - 행이 방금 채워져 스크롤 범위와 행 위치가 아직 갱신되지 않았을 수 있어 레이아웃을 먼저 맞춤
        self.updateGeometries()
        self.scrollTo(
            self.group_model.index(target, 0), QAbstractItemView.ScrollHint.PositionAtTop
        )

    def set_manual_target_for(
        self, evals: list[VideoEvaluation] | None = None, value: str | None = None
    ) -> int:
        """대상 영상의 출력 해상도 직접 지정 변경을 시그널로 전달하고 대상 수 반환"""
        target = self._resolve_target_evals(evals)
        if not target:
            return 0
        self.manual_target_requested.emit(target, value)
        return len(target)

    def _apply_horizontal_scroll_step(self) -> None:
        hbar = self.horizontalScrollBar()
        if hbar is not None:
            hbar.setSingleStep(_H_SCROLL_SINGLE_STEP_PX)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        """가로, 세로 스크롤 시 뷰포트 이동분을 상쇄해 빈 상태 오버레이를 중앙 영역에 고정"""
        super().scrollContentsBy(dx, dy)
        if hasattr(self, "empty_overlay") and self.empty_overlay.isVisible():
            self._sync_empty_overlay_geometry()

    def updateGeometries(self) -> None:
        """테이블 지오메트리 재계산 후 미세 픽셀 스크롤 단위와 빈 상태 오버레이 위치를 동기화"""
        super().updateGeometries()
        self._apply_horizontal_scroll_step()
        self._sync_empty_overlay_geometry()

    def wheelEvent(self, event: QWheelEvent | None) -> None:
        """트랙패드 가로 픽셀 델타는 1:1 픽셀 이동으로, 일반 휠은 미세 스텝 픽셀 스크롤로 처리"""
        if event is None:
            return
        px = event.pixelDelta()
        if px.x() != 0 and abs(px.x()) > abs(px.y()):
            hbar = self.horizontalScrollBar()
            if hbar is not None and hbar.maximum() > hbar.minimum():
                hbar.setValue(hbar.value() - px.x())
                event.accept()
                return
        super().wheelEvent(event)

    def start_external_drag(self) -> list[str]:
        """선택된 영상들의 실존 파일 경로로 외부 드래그를 시작하고 전달한 경로 목록 반환"""
        if self._can_start_drag_provider is not None and not self._can_start_drag_provider():
            return []
        root_dir = self._resolve_root_dir()
        if not root_dir:
            return []
        selected = self.get_selected_evaluations()
        paths = collect_draggable_file_paths(root_dir, selected)
        self._notify_missing(root_dir, selected)
        if not paths:
            return []
        start_table_file_drag(self, paths)
        self.external_drag_finished.emit(paths)
        return paths

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        """빈 영역 좌클릭 시 선택 해제, 행 좌클릭 시 외부 드래그 후보 기록"""
        self._drag_start_pos = None
        self._pending_single_select_index = None
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            idx = self.indexAt(event.pos())
            if not idx.isValid():
                self.clearSelection()
                event.accept()
                return
            if handle_group_click(self, idx):
                # - 그룹 머리글 행은 영상 행이 아니라 선택, 드래그 후보로 쓰지 않음
                event.accept()
                return
            sel_model = self.selectionModel()
            if event.modifiers() == Qt.KeyboardModifier.NoModifier and sel_model is not None:
                if sel_model.isSelected(idx):
                    self._drag_start_pos = event.pos()
                    self._pending_single_select_index = idx
                else:
                    sel_model.setCurrentIndex(
                        idx,
                        QItemSelectionModel.SelectionFlag.ClearAndSelect
                        | QItemSelectionModel.SelectionFlag.Rows,
                    )
                    self._drag_start_pos = event.pos()
                self.setFocus()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        """외부 드래그 후보 상태에서 시작 거리 이상 끌면 외부 파일 드래그 시작"""
        if (
            event is not None
            and self._drag_start_pos is not None
            and (event.buttons() & Qt.MouseButton.LeftButton)
        ):
            if (event.pos() - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance():
                self._drag_start_pos = None
                self._pending_single_select_index = None
                self.start_external_drag()
                self.setState(QAbstractItemView.State.NoState)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        """선택된 행을 끌지 않고 바로 놓았을 때 해당 행 단일 선택으로 전환"""
        pending_idx = self._pending_single_select_index
        was_drag_candidate = self._drag_start_pos is not None
        self._drag_start_pos = None
        self._pending_single_select_index = None
        if (
            event is not None
            and event.button() == Qt.MouseButton.LeftButton
            and pending_idx is not None
            and pending_idx.isValid()
        ):
            sel_model = self.selectionModel()
            if sel_model is not None:
                sel_model.setCurrentIndex(
                    pending_idx,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
            self.setState(QAbstractItemView.State.NoState)
            event.accept()
            return
        if event is not None and event.button() == Qt.MouseButton.LeftButton and was_drag_candidate:
            self.setState(QAbstractItemView.State.NoState)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def set_play_shortcut(self, key: str) -> None:
        """선택 영상 재생 단축키 변경, Return이면 이 위젯이 키 입력으로 처리하고 빈 값이면 재생 키를 두지 않음"""
        self._play_shortcut = key

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """Enter 키 입력 시 선택된 영상 파일들을 기본 플레이어로 실행"""
        if event is None:
            return
        if self._play_shortcut == DEFAULT_SHORTCUTS["classify.play_selected"] and event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            evals = self.get_selected_evaluations()
            if evals:
                self.play_selected(evals)
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_double_clicked(self, proxy_index: QModelIndex) -> None:
        ev = self.get_evaluation_at(proxy_index)
        root_dir = self._resolve_root_dir()
        if ev is not None and root_dir and not launch_video_file(root_dir, ev):
            self._notify_missing(root_dir, [ev])

    def _on_context_menu(self, pos: QPoint) -> None:
        clicked_ev = self.get_evaluation_at(self.indexAt(pos))
        if clicked_ev is None or not self._resolve_root_dir():
            return
        selected_evals = self.get_selected_evaluations()
        in_selection = any(
            e.video.filename == clicked_ev.video.filename for e in selected_evals
        )
        targets = selected_evals if in_selection else [clicked_ev]
        to_add, to_remove = self._split_exception_stems(targets)
        show_row_context_menu(self, pos, clicked_ev, targets, to_add, to_remove)
