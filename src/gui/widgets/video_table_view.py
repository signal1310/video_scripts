"""비디오 평가 테이블 뷰, 빈 상태 안내 오버레이 및 컨텍스트 메뉴 위젯 모듈"""

import os
from collections.abc import Callable
from typing import ClassVar

from PyQt6.QtCore import (
    QEvent,
    QItemSelection,
    QModelIndex,
    QObject,
    QPoint,
    Qt,
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
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from src.core.file_manager import resolve_video_filepath
from src.core.models import VideoEvaluation
from src.gui.column_filters import ColumnDataType
from src.gui.column_sizing import (
    DEFAULT_COL_ORDER as _DEFAULT_COL_ORDER,
)
from src.gui.column_sizing import (
    DEFAULT_HIDDEN_COLS as _DEFAULT_HIDDEN_COLS,
)
from src.gui.column_sizing import (
    MIN_COL_WIDTHS as _MIN_COL_WIDTHS,
)
from src.gui.column_sizing import (
    measure_column_content_widths,
    measure_name_content_width,
)
from src.gui.delegates import ClipTextDelegate, IconTextDelegate, PillBadgeDelegate
from src.gui.proxy_model import VideoSortFilterProxyModel
from src.gui.table_model import VideoTableModel
from src.gui.widgets.column_filter_popover import ColumnFilterPopover
from src.gui.widgets.edge_fade_overlay import (
    HorizontalEdgeFadeOverlay,
    sync_edge_overlay_from_scrollbar,
)
from src.gui.widgets.filter_header_view import FilterHeaderView
from src.gui.widgets.header_bar import SplitScanButton
from src.gui.widgets.table_actions import (
    copy_lines_to_clipboard,
    launch_video_file,
    reveal_video_file,
    split_exception_stems,
)
from src.gui.widgets.table_context_menu import (
    show_header_context_menu,
    show_row_context_menu,
)

_DEFAULT_ROW_HEIGHT: int = 34
_MIN_COLUMN_WIDTH: int = 36
_H_SCROLL_SINGLE_STEP_PX: int = 20
_EMPTY_ACTION_BUTTON_WIDTH: int = 150
_EMPTY_GUIDE_MAX_WIDTH: int = 520
_EMPTY_STEP_BADGE_WIDTH: int = 84


class VideoTableView(QTableView):
    """비디오 평가 목록 표시용 테이블 뷰 위젯

    열 너비 설정, 뱃지 델리게이트, 다중 선택, Empty State 안내 및 컨텍스트 메뉴 제공
    """

    exception_keyword_added = pyqtSignal(str)
    exception_keywords_added = pyqtSignal(list)
    exception_keywords_removed = pyqtSignal(list)
    selection_changed = pyqtSignal(int)
    scan_fast_requested = pyqtSignal()
    scan_full_requested = pyqtSignal()
    scan_mode_changed = pyqtSignal(str)
    reset_filters_requested = pyqtSignal()

    BULK_PLAY_CONFIRM_THRESHOLD: ClassVar[int] = 10

    # 열 최소 폭과 기본 열 순서, 숨김 열은 column_sizing 모듈의 정책 데이터를 참조
    MIN_COL_WIDTHS: ClassVar[dict[int, int]] = _MIN_COL_WIDTHS
    DEFAULT_COL_ORDER: ClassVar[tuple[int, ...]] = _DEFAULT_COL_ORDER
    DEFAULT_HIDDEN_COLS: ClassVar[frozenset[int]] = _DEFAULT_HIDDEN_COLS

    def __init__(
        self,
        root_dir_provider: Callable[[], str] | None = None,
        exceptions_provider: Callable[[], list[str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._root_dir_provider = root_dir_provider
        self._exceptions_provider = exceptions_provider

        self.table_model = VideoTableModel(self)
        self.proxy_model = VideoSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.table_model)
        self.setModel(self.proxy_model)
        self._active_popover: ColumnFilterPopover | None = None
        self.vp_fade_overlay: HorizontalEdgeFadeOverlay | None = None
        self.header_fade_overlay: HorizontalEdgeFadeOverlay | None = None
        self._user_resized_name_col: bool = False
        self._user_resized_cols: set[int] = set()
        self._content_widths: dict[int, int] = {}
        self._programmatic_col_resize: bool = False
        self._in_fit_name_col: bool = False
        self._name_content_width: int | None = None

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

        self.name_delegate = ClipTextDelegate(self)
        self.setItemDelegateForColumn(VideoTableModel.COL_NAME, self.name_delegate)
        self.badge_delegate = PillBadgeDelegate(self)
        for col in (
            VideoTableModel.COL_STATUS,
            VideoTableModel.COL_ZONE,
            VideoTableModel.COL_ACTION,
            VideoTableModel.COL_REASON,
        ):
            self.setItemDelegateForColumn(col, self.badge_delegate)
        self.icon_text_delegate = IconTextDelegate(self)
        self.setItemDelegateForColumn(
            VideoTableModel.COL_ROTATE, self.icon_text_delegate
        )

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
        self.proxy_model.column_filters_changed.connect(
            self._sync_column_filter_indicators
        )

        self.reset_columns()
        self.reset_table_sort()

        sel_model = self.selectionModel()
        if sel_model is not None:
            sel_model.selectionChanged.connect(self._on_selection_changed)

        self._init_empty_overlay()
        self._init_edge_fade_overlays()
        self.update_empty_state(total_count=0, visible_count=0)

    def _init_edge_fade_overlays(self) -> None:
        vp = self.viewport()
        self.vp_fade_overlay = (
            HorizontalEdgeFadeOverlay(vp, lambda p: p.bg_card) if vp is not None else None
        )
        header_vp = self.filter_header.viewport()
        self.header_fade_overlay = (
            HorizontalEdgeFadeOverlay(header_vp, lambda p: p.header_bg)
            if header_vp is not None
            else None
        )
        if header_vp is not None:
            header_vp.installEventFilter(self)
        h_bar = self.horizontalScrollBar()
        if h_bar is not None:
            h_bar.valueChanged.connect(self._sync_edge_fade_overlays)
            h_bar.rangeChanged.connect(self._sync_edge_fade_overlays)

    def _sync_edge_fade_overlays(self) -> None:
        if not hasattr(self, "empty_overlay"):
            return
        h_bar = self.horizontalScrollBar()
        show_vp_fade = not self.empty_overlay.isVisible()
        sync_edge_overlay_from_scrollbar(self.vp_fade_overlay, h_bar, enabled=show_vp_fade)
        sync_edge_overlay_from_scrollbar(self.header_fade_overlay, h_bar, enabled=True)

    def _init_empty_overlay(self) -> None:
        vp = self.viewport()
        # 뷰포트 자식으로 두어야 휠, 트랙패드 가로 스크롤 이벤트가 뷰포트로 자연스럽게 전파됨
        self.empty_overlay = QWidget(vp)
        self.empty_overlay.setObjectName("TableEmptyOverlay")
        if vp is not None:
            vp.installEventFilter(self)

        outer_layout = QVBoxLayout(self.empty_overlay)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.empty_scroll = QScrollArea(self.empty_overlay)
        self.empty_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.empty_scroll.setWidgetResizable(True)
        self.empty_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.empty_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll_vp = self.empty_scroll.viewport()
        if scroll_vp is not None:
            scroll_vp.setObjectName("TableEmptyOverlay")

        empty_content = QWidget(self.empty_scroll)
        empty_content.setObjectName("TableEmptyOverlay")
        overlay_layout = QVBoxLayout(empty_content)
        overlay_layout.setContentsMargins(20, 12, 20, 12)
        overlay_layout.setSpacing(6)
        overlay_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._lbl_empty_title = QLabel("", empty_content)
        self._lbl_empty_title.setObjectName("EmptyStateTitle")
        self._lbl_empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_empty_title.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

        self._lbl_empty_desc = QLabel("", empty_content)
        self._lbl_empty_desc.setObjectName("EmptyStateDesc")
        self._lbl_empty_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_empty_desc.setWordWrap(True)
        self._lbl_empty_desc.setMaximumWidth(_EMPTY_GUIDE_MAX_WIDTH)
        self._lbl_empty_desc.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )

        self.guide_card = QFrame(empty_content)
        self.guide_card.setObjectName("EmptyGuideCard")
        self.guide_card.setMaximumWidth(_EMPTY_GUIDE_MAX_WIDTH)
        self.guide_card.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        guide_layout = QVBoxLayout(self.guide_card)
        guide_layout.setContentsMargins(14, 10, 14, 10)
        guide_layout.setSpacing(5)
        steps = (
            ("1. 폴더 분석", "작업 폴더를 고르고 [빠른 분석] 또는 [정밀 분석]을 실행합니다."),
            ("2. 기준 비교", "과화질(여유 용량 큰 파일)과 키프레임 간격 초과만 선별해 예상 절약을 미리 계산합니다."),
            ("3. 안전 분류", "[실제 분류 실행]으로 인코딩할 파일만 하위 폴더로 옮깁니다. (원본 덮어쓰기 없음)"),
        )
        for badge_text, step_desc in steps:
            step_row = QHBoxLayout()
            step_row.setContentsMargins(0, 0, 0, 0)
            step_row.setSpacing(8)
            badge = QLabel(badge_text, self.guide_card)
            badge.setObjectName("EmptyStepBadge")
            badge.setFixedWidth(_EMPTY_STEP_BADGE_WIDTH)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            desc = QLabel(step_desc, self.guide_card)
            desc.setObjectName("EmptyStepDesc")
            desc.setWordWrap(True)
            step_row.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)
            step_row.addWidget(desc, stretch=1)
            guide_layout.addLayout(step_row)

        self.btn_empty_scan = SplitScanButton(empty_content)
        self.btn_empty_scan.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.btn_empty_scan.scan_fast_requested.connect(self.scan_fast_requested.emit)
        self.btn_empty_scan.scan_full_requested.connect(self.scan_full_requested.emit)
        self.btn_empty_scan.mode_changed.connect(self.scan_mode_changed.emit)

        self.btn_empty_action = QPushButton("필터 초기화", empty_content)
        self.btn_empty_action.setObjectName("PrimaryButton")
        self.btn_empty_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_empty_action.setFixedWidth(_EMPTY_ACTION_BUTTON_WIDTH)
        self.btn_empty_action.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.empty_mode: str = "no_data"
        self.btn_empty_action.clicked.connect(self.reset_filters_requested.emit)

        overlay_layout.addWidget(self._lbl_empty_title)
        overlay_layout.addWidget(self._lbl_empty_desc)
        overlay_layout.addSpacing(4)
        overlay_layout.addWidget(self.guide_card, alignment=Qt.AlignmentFlag.AlignCenter)
        overlay_layout.addSpacing(4)
        overlay_layout.addWidget(
            self.btn_empty_scan, alignment=Qt.AlignmentFlag.AlignCenter
        )
        overlay_layout.addWidget(
            self.btn_empty_action, alignment=Qt.AlignmentFlag.AlignCenter
        )

        self.empty_scroll.setWidget(empty_content)
        outer_layout.addWidget(self.empty_scroll)

    def update_empty_state(self, total_count: int, visible_count: int) -> None:
        """전체, 가시 행 수에 맞춰 빈 데이터 또는 필터 결과 없음 오버레이 전환"""
        if visible_count > 0:
            self.empty_overlay.hide()
            self._sync_edge_fade_overlays()
            return
        if total_count == 0:
            self.empty_mode = "no_data"
            self._lbl_empty_title.setText("화질 유지, 용량 절약 대상 자동 선별")
            self._lbl_empty_desc.setText(
                "눈으로 구분하기 힘든 과도한 비트레이트 영상과 탐색이 느린 영상만 골라 "
                "인코딩 작업 폴더로 분류합니다."
            )
            self.guide_card.show()
            self.btn_empty_scan.show()
            self.btn_empty_action.hide()
        else:
            self.empty_mode = "filtered_empty"
            self._lbl_empty_title.setText("필터 조건에 맞는 영상이 없습니다")
            self._lbl_empty_desc.setText(
                f"전체 {total_count:,}개 영상 중 현재 검색어 및 열 필터 조건과 일치하는 항목이 없습니다."
            )
            self.guide_card.hide()
            self.btn_empty_scan.hide()
            self.btn_empty_action.setText("필터 초기화")
            self.btn_empty_action.show()
        self._sync_empty_overlay_geometry()
        self.empty_overlay.raise_()
        self.empty_overlay.show()
        self._sync_edge_fade_overlays()

    def _sync_empty_overlay_geometry(self) -> None:
        if not hasattr(self, "empty_overlay"):
            return
        vp = self.viewport()
        if vp is not None:
            self.empty_overlay.setGeometry(vp.rect())
        for overlay, target_vp in (
            (self.vp_fade_overlay, vp),
            (self.header_fade_overlay, self.filter_header.viewport()),
        ):
            if overlay is not None and target_vp is not None:
                overlay.setGeometry(target_vp.rect())
                overlay.raise_()
        self._sync_edge_fade_overlays()

    def set_empty_scan_mode(self, mode: str) -> None:
        """빈 데이터 안내 스플릿 버튼의 스캔 모드를 동기화"""
        self.btn_empty_scan.set_mode(mode, emit_signal=False)

    def set_empty_action_enabled(self, enabled: bool) -> None:
        """빈 상태 오버레이 내 유도 액션 버튼 활성화 여부 설정"""
        self.btn_empty_scan.setEnabled(enabled)
        self.btn_empty_action.setEnabled(enabled)

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

    def _on_model_reset(self) -> None:
        self._name_content_width = None
        self._content_widths = measure_column_content_widths(self.table_model, self.font())
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
                self.table_model, self.font()
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
        if self._active_popover is not None:
            self._active_popover.close()

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

    def get_selected_evaluations(self) -> list[VideoEvaluation]:
        """현재 선택된 모든 행의 VideoEvaluation 객체 목록 반환"""
        sel_model = self.selectionModel()
        if sel_model is None:
            return []
        result: list[VideoEvaluation] = []
        for idx in sel_model.selectedRows():
            ev = self.get_evaluation_at(idx)
            if ev is not None:
                result.append(ev)
        return result

    def _on_selection_changed(
        self, _selected: QItemSelection, _deselected: QItemSelection
    ) -> None:
        self.selection_changed.emit(len(self.get_selected_evaluations()))

    def get_evaluation_at(self, proxy_index: QModelIndex) -> VideoEvaluation | None:
        """프록시 모델 인덱스에 해당하는 원본 VideoEvaluation 객체 반환"""
        if not proxy_index.isValid():
            return None
        src_idx = self.proxy_model.mapToSource(proxy_index)
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

    def _confirm_bulk_play(self, count: int) -> bool:
        if count <= self.BULK_PLAY_CONFIRM_THRESHOLD:
            return True
        answer = QMessageBox.question(
            self,
            "영상 일괄 재생",
            f"선택한 {count:,}개 영상을 모두 재생합니다. 계속하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def play_selected(self, evals: list[VideoEvaluation] | None = None) -> int:
        """선택된 영상 파일들을 기본 플레이어로 일괄 실행하고 실행 건수 반환(대량은 확인 후)"""
        root_dir, target = self._resolve_root_and_targets(evals)
        if not root_dir or not self._confirm_bulk_play(len(target)):
            return 0
        return sum(1 for ev in target if launch_video_file(root_dir, ev))

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
        return reveal_video_file(root_dir, target)

    def copy_selected_names(self, evals: list[VideoEvaluation] | None = None) -> int:
        """선택된 영상들의 파일명을 줄바꿈 구분으로 클립보드에 복사"""
        target = self._resolve_target_evals(evals)
        return copy_lines_to_clipboard([e.video.filename for e in target])

    def copy_selected_paths(self, evals: list[VideoEvaluation] | None = None) -> int:
        """선택된 영상들의 전체 절대 경로를 줄바꿈 구분으로 클립보드에 복사"""
        root_dir, target = self._resolve_root_and_targets(evals)
        if not root_dir:
            return 0
        paths = [
            os.path.normpath(resolve_video_filepath(root_dir, e.video))
            for e in target
        ]
        return copy_lines_to_clipboard(paths)

    def add_selected_to_exceptions(
        self, evals: list[VideoEvaluation] | None = None
    ) -> int:
        """선택된 영상 중 미등록 파일명 줄기를 예외 키워드 추가 시그널로 전달"""
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
        """선택된 영상 중 이미 등록된 파일명 줄기를 예외 키워드 제거 시그널로 전달"""
        _, to_remove = self._split_exception_stems(self._resolve_target_evals(evals))
        if not to_remove:
            return 0
        self.exception_keywords_removed.emit(to_remove)
        return len(to_remove)

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

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        """행이 없는 빈 영역 좌클릭 시 현재 선택을 해제"""
        if (
            event is not None
            and event.button() == Qt.MouseButton.LeftButton
            and not self.indexAt(event.pos()).isValid()
        ):
            self.clearSelection()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """Enter 키 입력 시 선택된 영상 파일들을 기본 플레이어로 실행"""
        if event is None:
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            evals = self.get_selected_evaluations()
            if evals:
                self.play_selected(evals)
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_double_clicked(self, proxy_index: QModelIndex) -> None:
        ev = self.get_evaluation_at(proxy_index)
        root_dir = self._resolve_root_dir()
        if ev is not None and root_dir:
            launch_video_file(root_dir, ev)

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
