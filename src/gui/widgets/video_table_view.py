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
from src.gui.delegates import PillBadgeDelegate
from src.gui.proxy_model import VideoSortFilterProxyModel
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.table_model import VideoTableModel


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

        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.setSortingEnabled(True)
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

        header = self.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            header.setStretchLastSection(False)
            header.setMinimumSectionSize(55)
            header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            header.customContextMenuRequested.connect(self._on_header_context_menu)

        self.reset_columns()

        self.sortByColumn(
            VideoTableModel.COL_SAVED_SIZE, Qt.SortOrder.DescendingOrder
        )

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
                f"전체 {total_count:,}개 영상 중 현재 검색어·등급·대상 필터와 일치하는 항목이 없습니다."
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
        """모든 테이블 열 표시 복원 및 기본 너비 재설정"""
        for col_idx, width in self.DEFAULT_COL_WIDTHS.items():
            self.setColumnHidden(col_idx, False)
            self.setColumnWidth(col_idx, width)

    def _on_header_context_menu(self, pos: QPoint) -> None:
        header = self.horizontalHeader()
        menu = QMenu(self)
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
        act_reset = QAction("기본 열 너비·표시 복원", menu)
        act_reset.triggered.connect(self.reset_columns)
        menu.addAction(act_reset)
        if header is not None:
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

    def _play_evaluation(self, ev: VideoEvaluation) -> None:
        root_dir = self._resolve_root_dir()
        if not root_dir:
            return
        filepath = resolve_video_filepath(root_dir, ev.video)
        if os.path.isfile(filepath) and hasattr(os, "startfile"):
            os.startfile(filepath)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """Enter 키 입력 시 선택된 첫 번째 영상 파일 기본 플레이어 실행"""
        if event is None:
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            evals = self.get_selected_evaluations()
            if evals:
                self._play_evaluation(evals[0])
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

        p = get_active_palette()
        menu = QMenu(self)
        act_play = QAction(
            get_icon("play", palette=p, size=14, pad_right=7),
            "기본 플레이어로 재생",
            self,
        )
        act_reveal = QAction(
            get_icon("folder", palette=p, size=14, pad_right=7),
            "탐색기에서 위치 열기",
            self,
        )
        copy_label = (
            "파일명 클립보드 복사"
            if len(target_evals) == 1
            else f"선택한 {len(target_evals)}개 파일명 클립보드 복사"
        )
        act_copy_name = QAction(
            get_icon("copy", palette=p, size=14, pad_right=7),
            copy_label,
            self,
        )

        filepath = resolve_video_filepath(root_dir, clicked_ev.video)

        def _play() -> None:
            self._play_evaluation(clicked_ev)

        def _reveal() -> None:
            if os.path.isfile(filepath):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(filepath)])

        def _copy_name() -> None:
            cb: QClipboard | None = QApplication.clipboard()
            if cb is not None:
                cb.setText("\n".join(e.video.filename for e in target_evals))

        act_play.triggered.connect(_play)
        act_reveal.triggered.connect(_reveal)
        act_copy_name.triggered.connect(_copy_name)

        menu.addAction(act_play)
        menu.addAction(act_reveal)
        menu.addSeparator()
        menu.addAction(act_copy_name)

        stems = [os.path.splitext(e.video.filename)[0] for e in target_evals]
        curr_tokens: set[str] = (
            {t.lower() for t in self._exceptions_provider()}
            if self._exceptions_provider
            else set()
        )
        to_add = [s for s in stems if s.lower() not in curr_tokens]
        to_remove = [s for s in stems if s.lower() in curr_tokens]

        if to_add:
            add_label = (
                "이 파일을 예외 키워드에 추가 (원본 보존)"
                if len(to_add) == 1
                else f"선택한 {len(to_add)}개 파일을 예외 키워드에 추가"
            )
            act_exclude = QAction(
                get_icon("ban", palette=p, size=14, pad_right=7),
                add_label,
                self,
            )

            def _add_to_exception() -> None:
                if len(to_add) == 1:
                    self.exception_keyword_added.emit(to_add[0])
                else:
                    self.exception_keywords_added.emit(to_add)

            act_exclude.triggered.connect(_add_to_exception)
            menu.addAction(act_exclude)

        if to_remove:
            rem_label = (
                "이 파일을 예외 키워드에서 제거"
                if len(to_remove) == 1
                else f"선택한 {len(to_remove)}개 파일을 예외 키워드에서 제거"
            )
            act_include = QAction(
                get_icon("rotate-ccw", palette=p, size=14, pad_right=7),
                rem_label,
                self,
            )
            act_include.triggered.connect(
                lambda: self.exception_keywords_removed.emit(to_remove)
            )
            menu.addAction(act_include)

        vp = self.viewport()
        if vp is not None:
            menu.exec(vp.mapToGlobal(pos))
