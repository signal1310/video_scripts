"""비디오 평가 테이블 뷰, 빈 상태 안내 오버레이 및 컨텍스트 메뉴 위젯 모듈"""

import os
import subprocess
from collections.abc import Callable
from typing import ClassVar

from PyQt6.QtCore import QItemSelection, QModelIndex, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QClipboard, QKeyEvent, QResizeEvent
from PyQt6.QtWidgets import (
    QApplication,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from src.core.file_manager import resolve_video_filepath
from src.core.models import VideoEvaluation
from src.gui.column_filters import ColumnDataType
from src.gui.delegates import PillBadgeDelegate
from src.gui.proxy_model import VideoSortFilterProxyModel
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.table_model import VideoTableModel
from src.gui.widgets.column_filter_popover import ColumnFilterPopover
from src.gui.widgets.filter_header_view import FilterHeaderView


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
        VideoTableModel.COL_STATUS: 135,
        VideoTableModel.COL_NAME: 245,
        VideoTableModel.COL_RES: 88,
        VideoTableModel.COL_CODEC: 62,
        VideoTableModel.COL_FPS: 60,
        VideoTableModel.COL_DURATION: 74,
        VideoTableModel.COL_SIZE: 82,
        VideoTableModel.COL_BITRATE: 78,
        VideoTableModel.COL_OPT_BITRATE: 82,
        VideoTableModel.COL_RATIO: 76,
        VideoTableModel.COL_ZONE: 78,
        VideoTableModel.COL_KEYFRAME: 78,
        VideoTableModel.COL_AFTER_SIZE: 82,
        VideoTableModel.COL_SAVED_SIZE: 96,
        VideoTableModel.COL_ACTION: 205,
    }

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

        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.setShowGrid(True)
        vheader = self.verticalHeader()
        if vheader is not None:
            vheader.setVisible(False)
            vheader.setDefaultSectionSize(34)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.doubleClicked.connect(self._on_double_clicked)

        self.badge_delegate = PillBadgeDelegate(self)
        for col in (
            VideoTableModel.COL_STATUS,
            VideoTableModel.COL_ZONE,
            VideoTableModel.COL_ACTION,
        ):
            self.setItemDelegateForColumn(col, self.badge_delegate)

        self.filter_header = FilterHeaderView(Qt.Orientation.Horizontal, self)
        self.setHorizontalHeader(self.filter_header)
        # 커스텀 수평 헤더 교체 이후 정렬을 활성화해야 sectionsClickable()이 적용됨
        self.setSortingEnabled(True)
        self.filter_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.filter_header.setSectionsMovable(True)
        self.filter_header.setStretchLastSection(False)
        self.filter_header.setMinimumSectionSize(55)
        self.filter_header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.filter_header.customContextMenuRequested.connect(
            self._on_header_context_menu
        )
        self.filter_header.filter_clicked.connect(self.open_column_filter_popover)
        self.filter_header.sectionClicked.connect(self._on_header_section_clicked)
        self.filter_header.sortIndicatorChanged.connect(
            self._on_sort_indicator_changed
        )
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
        self._btn_empty_action.setFixedWidth(150)
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
                f"전체 {total_count:,}개 영상 중 현재 검색어·등급·대상 및 열 필터 조건과 일치하는 항목이 없습니다."
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
        """뷰포트 크기 변경 시 빈 상태 오버레이 크기 동기화"""
        super().resizeEvent(event)
        if hasattr(self, "_empty_overlay") and self._empty_overlay.isVisible():
            vp = self.viewport()
            if vp is not None:
                self._empty_overlay.setGeometry(vp.rect())

    def reset_columns(self) -> None:
        """모든 테이블 열 순서·표시 복원 및 기본 너비 재설정"""
        header = self.filter_header
        for logical_idx in range(len(self.DEFAULT_COL_WIDTHS)):
            current_visual = header.visualIndex(logical_idx)
            if current_visual != logical_idx:
                header.moveSection(current_visual, logical_idx)
        for col_idx, width in self.DEFAULT_COL_WIDTHS.items():
            self.setColumnHidden(col_idx, False)
            self.setColumnWidth(col_idx, width)

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
        value_counts = (
            self.table_model.collect_column_value_counts(col)
            if spec.data_type in (ColumnDataType.ENUM, ColumnDataType.CATEGORY)
            else []
        )
        numeric_bounds = (
            self.table_model.collect_numeric_bounds(col)
            if spec.data_type is ColumnDataType.NUMBER
            else (None, None, 0)
        )
        bucket_counts = (
            self.table_model.collect_numeric_bucket_counts(col)
            if spec.data_type is ColumnDataType.NUMBER
            else []
        )
        popover = ColumnFilterPopover(
            col=col,
            title=VideoTableModel.HEADERS[col],
            spec=spec,
            current_rule=self.proxy_model.get_column_filter(col),
            value_counts=value_counts,
            numeric_bounds=numeric_bounds,
            number_bucket_counts=bucket_counts,
            toggle_rect_global=self.filter_header.filter_button_global_rect(col),
            parent=self,
        )
        popover.filter_changed.connect(self.proxy_model.set_column_filter)
        self._active_popover = popover

        pos = anchor_global or self.filter_header.section_anchor_global(col)
        pos = QPoint(pos.x() - 6, pos.y() - 2)
        popover.adjustSize()
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            x = min(pos.x(), avail.right() - popover.width() - 4)
            y = min(pos.y(), avail.bottom() - popover.height() - 4)
            pos = QPoint(max(avail.left() + 4, x), max(avail.top() + 4, y))
        popover.move(pos)
        popover.show()
        return popover

    def _on_header_context_menu(self, pos: QPoint) -> None:
        header = self.filter_header
        p = get_active_palette()
        menu = QMenu(self)
        clicked_col = header.logicalIndexAt(pos)
        if 0 <= clicked_col < len(VideoTableModel.HEADERS):
            col_title = VideoTableModel.HEADERS[clicked_col]
            act_filter = QAction(
                get_icon("filter", palette=p, size=14),
                f"'{col_title}' 열 필터...",
                menu,
            )
            act_filter.triggered.connect(
                lambda _c=False, c=clicked_col: self.open_column_filter_popover(c)
            )
            menu.addAction(act_filter)
            if self.proxy_model.get_column_filter(clicked_col) is not None:
                act_clear_col = QAction(
                    get_icon("x", palette=p, size=14),
                    f"'{col_title}' 열 필터 해제",
                    menu,
                )
                act_clear_col.triggered.connect(
                    lambda _c=False, c=clicked_col: self.proxy_model.clear_column_filter(
                        c
                    )
                )
                menu.addAction(act_clear_col)
            if self.proxy_model.active_column_filter_count() > 0:
                act_clear_all = QAction(
                    get_icon("rotate-ccw", palette=p, size=14),
                    "모든 열 필터 초기화",
                    menu,
                )
                act_clear_all.triggered.connect(
                    self.proxy_model.clear_all_column_filters
                )
                menu.addAction(act_clear_all)
            menu.addSeparator()

        visible_cols = [
            c
            for c in range(self.table_model.columnCount())
            if not self.isColumnHidden(c)
        ]
        for col_idx, title in enumerate(VideoTableModel.HEADERS):
            act = QAction(title, menu)
            act.setCheckable(True)
            is_visible = not self.isColumnHidden(col_idx)
            act.setChecked(is_visible)
            if col_idx == VideoTableModel.COL_NAME or (
                is_visible and len(visible_cols) <= 1
            ):
                act.setEnabled(False)
            def _on_col_toggled(checked: bool, c: int = col_idx) -> None:
                self.setColumnHidden(c, not checked)

            act.toggled.connect(_on_col_toggled)
            menu.addAction(act)
        menu.addSeparator()
        act_reset_sort = QAction(
            get_icon("rotate-ccw", palette=p, size=14),
            "기본 정렬로 복원",
            menu,
        )
        act_reset_sort.setEnabled(
            bool(self.proxy_model.sort_stack)
            or self.filter_header.isSortIndicatorShown()
        )
        act_reset_sort.triggered.connect(self.reset_table_sort)
        menu.addAction(act_reset_sort)
        act_reset = QAction("기본 열 너비·표시·순서 복원", menu)
        act_reset.triggered.connect(self.reset_columns)
        menu.addAction(act_reset)
        menu.exec(header.mapToGlobal(pos))

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

    def _play_evaluation(self, ev: VideoEvaluation) -> None:
        root_dir = self._resolve_root_dir()
        if root_dir:
            self._launch_file(root_dir, ev)

    def _resolve_target_evals(
        self, evals: list[VideoEvaluation] | None
    ) -> list[VideoEvaluation]:
        return evals if evals is not None else self.get_selected_evaluations()

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
        root_dir = self._resolve_root_dir()
        if not root_dir:
            return 0
        launched = 0
        for ev in self._resolve_target_evals(evals):
            if self._launch_file(root_dir, ev):
                launched += 1
        return launched

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
        root_dir = self._resolve_root_dir()
        if not root_dir:
            return 0
        target = self._resolve_target_evals(evals)
        paths = [
            os.path.normpath(resolve_video_filepath(root_dir, e.video))
            for e in target
        ]
        return self._copy_lines_to_clipboard(paths)

    def add_selected_to_exceptions(
        self, evals: list[VideoEvaluation] | None = None
    ) -> int:
        """선택된 영상 중 미등록 파일명 줄기를 예외 키워드 추가 시그널로 전달"""
        target = self._resolve_target_evals(evals)
        to_add, _ = self._split_exception_stems(target)
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
        target = self._resolve_target_evals(evals)
        _, to_remove = self._split_exception_stems(target)
        if not to_remove:
            return 0
        self.exception_keywords_removed.emit(to_remove)
        return len(to_remove)

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
        if ev is not None:
            self._play_evaluation(ev)

    def _on_context_menu(self, pos: QPoint) -> None:
        proxy_idx = self.indexAt(pos)
        clicked_ev = self.get_evaluation_at(proxy_idx)
        root_dir = self._resolve_root_dir()
        if clicked_ev is None or not root_dir:
            return

        selected_evals = self.get_selected_evaluations()
        if not any(
            e.video.filename == clicked_ev.video.filename for e in selected_evals
        ):
            target_evals = [clicked_ev]
        else:
            target_evals = selected_evals

        count = len(target_evals)
        p = get_active_palette()
        menu = QMenu(self)
        play_label = (
            "기본 플레이어로 재생"
            if count == 1
            else f"선택한 {count}개 영상 재생"
        )
        act_play = QAction(
            get_icon("play", palette=p, size=14),
            play_label,
            self,
        )
        act_reveal = QAction(
            get_icon("folder", palette=p, size=14),
            "탐색기에서 위치 열기",
            self,
        )
        copy_name_label = (
            "파일명 클립보드 복사"
            if count == 1
            else f"선택한 {count}개 파일명 클립보드 복사"
        )
        act_copy_name = QAction(
            get_icon("copy", palette=p, size=14),
            copy_name_label,
            self,
        )
        copy_path_label = (
            "전체 경로 클립보드 복사"
            if count == 1
            else f"선택한 {count}개 전체 경로 클립보드 복사"
        )
        act_copy_path = QAction(
            get_icon("copy", palette=p, size=14),
            copy_path_label,
            self,
        )

        act_play.triggered.connect(lambda: self.play_selected(target_evals))
        act_reveal.triggered.connect(lambda: self.reveal_selected(clicked_ev))
        act_copy_name.triggered.connect(
            lambda: self.copy_selected_names(target_evals)
        )
        act_copy_path.triggered.connect(
            lambda: self.copy_selected_paths(target_evals)
        )

        menu.addAction(act_play)
        menu.addAction(act_reveal)
        menu.addSeparator()
        menu.addAction(act_copy_name)
        menu.addAction(act_copy_path)

        to_add, to_remove = self._split_exception_stems(target_evals)

        if to_add:
            add_label = (
                "이 파일을 예외 키워드에 추가 (원본 보존)"
                if len(to_add) == 1
                else f"선택한 {len(to_add)}개 파일을 예외 키워드에 추가"
            )
            act_exclude = QAction(
                get_icon("ban", palette=p, size=14),
                add_label,
                self,
            )
            act_exclude.triggered.connect(
                lambda: self.add_selected_to_exceptions(target_evals)
            )
            menu.addAction(act_exclude)

        if to_remove:
            rem_label = (
                "이 파일을 예외 키워드에서 제거"
                if len(to_remove) == 1
                else f"선택한 {len(to_remove)}개 파일을 예외 키워드에서 제거"
            )
            act_include = QAction(
                get_icon("rotate-ccw", palette=p, size=14),
                rem_label,
                self,
            )
            act_include.triggered.connect(
                lambda: self.remove_selected_from_exceptions(target_evals)
            )
            menu.addAction(act_include)

        vp = self.viewport()
        if vp is not None:
            menu.exec(vp.mapToGlobal(pos))
