"""비디오 평가 테이블 뷰, 빈 상태 안내 오버레이 및 컨텍스트 메뉴 위젯 모듈"""

import os
import subprocess
from collections.abc import Callable
from typing import ClassVar

from PyQt6.QtCore import QItemSelection, QModelIndex, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QClipboard, QFontMetrics, QKeyEvent, QResizeEvent, QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from src.core.file_manager import resolve_video_filepath
from src.core.models import VideoEvaluation
from src.gui.column_filters import ColumnDataType
from src.gui.delegates import ClipTextDelegate, IconTextDelegate, PillBadgeDelegate
from src.gui.proxy_model import VideoSortFilterProxyModel
from src.gui.table_model import VideoTableModel
from src.gui.widgets.column_filter_popover import ColumnFilterPopover
from src.gui.widgets.filter_header_view import FilterHeaderView
from src.gui.widgets.table_context_menu import (
    show_header_context_menu,
    show_row_context_menu,
)

_DEFAULT_ROW_HEIGHT: int = 34
_MIN_COLUMN_WIDTH: int = 36
_H_SCROLL_SINGLE_STEP_PX: int = 20
_EMPTY_ACTION_BUTTON_WIDTH: int = 150
_NAME_COL_DEFAULT_WIDTH: int = 160
_NAME_COL_MAX_WIDTH: int = 360
_NAME_COL_CELL_PADDING_PX: int = 24


class VideoTableView(QTableView):
    """비디오 평가 목록 표시용 테이블 뷰 위젯

    열 너비 설정, 뱃지 델리게이트, 다중 선택, Empty State 안내 및 컨텍스트 메뉴 제공
    """

    exception_keyword_added = pyqtSignal(str)
    exception_keywords_added = pyqtSignal(list)
    exception_keywords_removed = pyqtSignal(list)
    selection_summary_changed = pyqtSignal(int, float, float)
    scan_requested = pyqtSignal()
    reset_filters_requested = pyqtSignal()

    DEFAULT_COL_WIDTHS: ClassVar[dict[int, int]] = {
        VideoTableModel.COL_NAME: _NAME_COL_DEFAULT_WIDTH,
        VideoTableModel.COL_STATUS: 76,
        VideoTableModel.COL_WIDTH: 46,
        VideoTableModel.COL_HEIGHT: 46,
        VideoTableModel.COL_ROTATE: 50,
        VideoTableModel.COL_CODEC: 52,
        VideoTableModel.COL_FPS: 42,
        VideoTableModel.COL_DURATION: 54,
        VideoTableModel.COL_BITRATE: 52,
        VideoTableModel.COL_OPT_BITRATE: 58,
        VideoTableModel.COL_RATIO: 52,
        VideoTableModel.COL_ZONE: 58,
        VideoTableModel.COL_KEYFRAME: 64,
        VideoTableModel.COL_SIZE: 76,
        VideoTableModel.COL_AFTER_SIZE: 80,
        VideoTableModel.COL_SAVED_SIZE: 84,
        VideoTableModel.COL_ACTION: 88,
        VideoTableModel.COL_REASON: 80,
    }
    DEFAULT_HIDDEN_COLS: ClassVar[frozenset[int]] = frozenset(
        {
            VideoTableModel.COL_ROTATE,
            VideoTableModel.COL_OPT_BITRATE,
            VideoTableModel.COL_RATIO,
            VideoTableModel.COL_AFTER_SIZE,
        }
    )

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
        self._user_resized_name_col: bool = False
        self._programmatic_col_resize: bool = False
        self._in_fit_name_col: bool = False

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
        self.update_empty_state(total_count=0, visible_count=0)

    def _init_empty_overlay(self) -> None:
        self._empty_overlay = QWidget(self.viewport())
        self._empty_overlay.setObjectName("TableEmptyOverlay")
        overlay_layout = QVBoxLayout(self._empty_overlay)
        overlay_layout.setContentsMargins(24, 24, 24, 24)
        overlay_layout.setSpacing(8)
        overlay_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._lbl_empty_title = QLabel("", self._empty_overlay)
        self._lbl_empty_title.setObjectName("EmptyStateTitle")
        self._lbl_empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._lbl_empty_desc = QLabel("", self._empty_overlay)
        self._lbl_empty_desc.setObjectName("EmptyStateDesc")
        self._lbl_empty_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_empty_desc.setWordWrap(True)

        self._btn_empty_action = QPushButton("", self._empty_overlay)
        self._btn_empty_action.setObjectName("PrimaryButton")
        self._btn_empty_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_empty_action.setFixedWidth(_EMPTY_ACTION_BUTTON_WIDTH)
        self._empty_mode: str = "no_data"
        self._btn_empty_action.clicked.connect(self._on_empty_action_clicked)

        overlay_layout.addWidget(self._lbl_empty_title)
        overlay_layout.addWidget(self._lbl_empty_desc)
        overlay_layout.addSpacing(4)
        overlay_layout.addWidget(
            self._btn_empty_action, alignment=Qt.AlignmentFlag.AlignCenter
        )

    def _on_empty_action_clicked(self) -> None:
        if self._empty_mode == "filtered_empty":
            self.reset_filters_requested.emit()
        else:
            self.scan_requested.emit()

    def update_empty_state(self, total_count: int, visible_count: int) -> None:
        """전체·가시 행 수에 맞춰 빈 데이터 또는 필터 결과 없음 오버레이 전환"""
        if visible_count > 0:
            self._empty_overlay.hide()
            return
        if total_count == 0:
            self._empty_mode = "no_data"
            self._lbl_empty_title.setText("분석된 영상이 없습니다")
            self._lbl_empty_desc.setText(
                "상단에서 작업 폴더를 선택하고 [빠른 스캔] 또는 [정밀 분석]을 실행해 주세요."
            )
            self._btn_empty_action.setText("분석 실행")
        else:
            self._empty_mode = "filtered_empty"
            self._lbl_empty_title.setText("필터 조건에 맞는 영상이 없습니다")
            self._lbl_empty_desc.setText(
                f"전체 {total_count:,}개 영상 중 현재 검색어 및 열 필터 조건과 일치하는 항목이 없습니다."
            )
            self._btn_empty_action.setText("필터 초기화")
        vp = self.viewport()
        if vp is not None:
            self._empty_overlay.setGeometry(vp.rect())
        self._empty_overlay.show()

    def set_empty_action_enabled(self, enabled: bool) -> None:
        """빈 상태 오버레이 내 유도 액션 버튼 활성화 여부 설정"""
        self._btn_empty_action.setEnabled(enabled)

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        """뷰포트 크기 변경 시 빈 상태 오버레이와 이름 열 여백 채우기 너비 동기화"""
        super().resizeEvent(event)
        if hasattr(self, "_empty_overlay") and self._empty_overlay.isVisible():
            vp = self.viewport()
            if vp is not None:
                self._empty_overlay.setGeometry(vp.rect())
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
            self.fit_name_column_to_contents()

    def _on_model_reset(self) -> None:
        self.fit_name_column_to_contents()

    def _measure_name_content_width(self) -> int:
        target_w = _NAME_COL_DEFAULT_WIDTH
        row_count = self.table_model.rowCount()
        if row_count <= 0:
            return target_w
        metrics = QFontMetrics(self.font())
        for row in range(row_count):
            raw_name = self.table_model.index(row, VideoTableModel.COL_NAME).data(
                Qt.ItemDataRole.DisplayRole
            )
            if not raw_name:
                continue
            measured = (
                metrics.horizontalAdvance(str(raw_name)) + _NAME_COL_CELL_PADDING_PX
            )
            if measured >= _NAME_COL_MAX_WIDTH:
                return _NAME_COL_MAX_WIDTH
            if measured > target_w:
                target_w = measured
        return target_w

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
        """모든 테이블 열 순서·표시 복원 및 기본 너비 재설정"""
        self._user_resized_name_col = False
        header = self.filter_header
        self._programmatic_col_resize = True
        try:
            for logical_idx in range(len(self.DEFAULT_COL_WIDTHS)):
                current_visual = header.visualIndex(logical_idx)
                if current_visual != logical_idx:
                    header.moveSection(current_visual, logical_idx)
            for col_idx, width in self.DEFAULT_COL_WIDTHS.items():
                self.setColumnWidth(col_idx, width)
                self.setColumnHidden(col_idx, col_idx in self.DEFAULT_HIDDEN_COLS)
        finally:
            self._programmatic_col_resize = False
        self.fit_name_column_to_contents(force=True)

    def sync_name_column_user_resize_from_current(self) -> None:
        """저장된 헤더 상태 복원 직후 이름 열의 수동 조절 여부를 현재 너비 기준으로 동기화"""
        default_w = self.DEFAULT_COL_WIDTHS[VideoTableModel.COL_NAME]
        self._user_resized_name_col = (
            self.columnWidth(VideoTableModel.COL_NAME) != default_w
        )

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
            title=VideoTableModel.HEADERS[col],
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
        evals = self.get_selected_evaluations()
        total_orig = sum(ev.video.vid_size_MB for ev in evals)
        total_saved = sum(ev.expected_delta_mb for ev in evals)
        self.selection_summary_changed.emit(len(evals), total_orig, total_saved)

    def get_evaluation_at(self, proxy_index: QModelIndex) -> VideoEvaluation | None:
        """프록시 모델 인덱스에 해당하는 원본 VideoEvaluation 객체 반환"""
        if not proxy_index.isValid():
            return None
        src_idx = self.proxy_model.mapToSource(proxy_index)
        return self.table_model.data(src_idx, VideoTableModel.EVAL_ROLE)

    def _resolve_root_dir(self) -> str:
        return self._root_dir_provider() if self._root_dir_provider else ""

    def _launch_file(self, root_dir: str, ev: VideoEvaluation) -> bool:
        filepath = resolve_video_filepath(root_dir, ev.video)
        if os.path.isfile(filepath) and hasattr(os, "startfile"):
            os.startfile(filepath)
            return True
        return False

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

    def _copy_lines_to_clipboard(self, lines: list[str]) -> int:
        if not lines:
            return 0
        cb: QClipboard | None = QApplication.clipboard()
        if cb is None:
            return 0
        cb.setText("\n".join(lines))
        return len(lines)

    def _split_exception_stems(
        self, evals: list[VideoEvaluation]
    ) -> tuple[list[str], list[str]]:
        stems = [os.path.splitext(e.video.filename)[0] for e in evals]
        curr_tokens: set[str] = (
            {t.lower() for t in self._exceptions_provider()}
            if self._exceptions_provider
            else set()
        )
        to_add = [s for s in stems if s.lower() not in curr_tokens]
        to_remove = [s for s in stems if s.lower() in curr_tokens]
        return to_add, to_remove

    def play_selected(self, evals: list[VideoEvaluation] | None = None) -> int:
        """선택된 영상 파일들을 기본 플레이어로 일괄 실행하고 실행 건수 반환"""
        root_dir, target = self._resolve_root_and_targets(evals)
        if not root_dir:
            return 0
        return sum(1 for ev in target if self._launch_file(root_dir, ev))

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
        filepath = resolve_video_filepath(root_dir, target.video)
        if not os.path.isfile(filepath):
            return False
        subprocess.Popen(["explorer", "/select,", os.path.normpath(filepath)])
        return True

    def copy_selected_names(self, evals: list[VideoEvaluation] | None = None) -> int:
        """선택된 영상들의 파일명을 줄바꿈 구분으로 클립보드에 복사"""
        target = self._resolve_target_evals(evals)
        return self._copy_lines_to_clipboard([e.video.filename for e in target])

    def copy_selected_paths(self, evals: list[VideoEvaluation] | None = None) -> int:
        """선택된 영상들의 전체 절대 경로를 줄바꿈 구분으로 클립보드에 복사"""
        root_dir, target = self._resolve_root_and_targets(evals)
        if not root_dir:
            return 0
        paths = [
            os.path.normpath(resolve_video_filepath(root_dir, e.video))
            for e in target
        ]
        return self._copy_lines_to_clipboard(paths)

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

    def updateGeometries(self) -> None:
        """테이블 지오메트리 재계산 후에도 미세 픽셀 가로 스크롤 단위를 유지"""
        super().updateGeometries()
        self._apply_horizontal_scroll_step()

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
            self._launch_file(root_dir, ev)

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
