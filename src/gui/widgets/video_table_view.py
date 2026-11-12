import os
import subprocess
from typing import Callable, Optional
from PyQt6.QtCore import QModelIndex, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QClipboard
from PyQt6.QtWidgets import (
    QApplication,
    QHeaderView,
    QMenu,
    QTableView,
    QWidget,
)

from src.core.file_manager import resolve_video_filepath
from src.core.models import VideoEvaluation
from src.gui.delegates import PillBadgeDelegate
from src.gui.models import VideoSortFilterProxyModel, VideoTableModel
from src.gui.styles.icons import get_active_palette, get_icon


class VideoTableView(QTableView):
    """
    - 비디오 평가 목록을 표시하는 테이블 뷰 위젯.
    - 열 너비 설정, 뱃지 델리게이트, 재생 및 컨텍스트 메뉴 액션을 제공.
    """

    exception_keyword_added = pyqtSignal(str)

    DEFAULT_COL_WIDTHS = {
        VideoTableModel.COL_STATUS: 140,
        VideoTableModel.COL_NAME: 280,
        VideoTableModel.COL_RES: 95,
        VideoTableModel.COL_CODEC: 68,
        VideoTableModel.COL_FPS: 65,
        VideoTableModel.COL_DURATION: 115,
        VideoTableModel.COL_SIZE: 90,
        VideoTableModel.COL_BITRATE: 90,
        VideoTableModel.COL_OPT_BITRATE: 90,
        VideoTableModel.COL_RATIO: 85,
        VideoTableModel.COL_ZONE: 85,
        VideoTableModel.COL_KEYFRAME: 85,
        VideoTableModel.COL_AFTER_SIZE: 95,
        VideoTableModel.COL_SAVED_SIZE: 115,
        VideoTableModel.COL_ACTION: 235,
    }

    def __init__(
        self,
        root_dir_provider: Optional[Callable[[], str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._root_dir_provider = root_dir_provider

        self.table_model = VideoTableModel(self)
        self.proxy_model = VideoSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.table_model)
        self.setModel(self.proxy_model)

        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.setSortingEnabled(True)
        self.setShowGrid(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(34)
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
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(60)

        for col_idx, width in self.DEFAULT_COL_WIDTHS.items():
            self.setColumnWidth(col_idx, width)

        self.sortByColumn(
            VideoTableModel.COL_SAVED_SIZE, Qt.SortOrder.DescendingOrder
        )

    def get_evaluation_at(self, proxy_index: QModelIndex) -> Optional[VideoEvaluation]:
        if not proxy_index.isValid():
            return None
        src_idx = self.proxy_model.mapToSource(proxy_index)
        return self.table_model.data(src_idx, VideoTableModel.EVAL_ROLE)

    def _resolve_root_dir(self) -> str:
        return self._root_dir_provider() if self._root_dir_provider else ""

    def _on_double_clicked(self, proxy_index: QModelIndex) -> None:
        ev = self.get_evaluation_at(proxy_index)
        root_dir = self._resolve_root_dir()
        if ev is None or not root_dir:
            return
        filepath = resolve_video_filepath(root_dir, ev.video)
        if os.path.isfile(filepath) and hasattr(os, "startfile"):
            os.startfile(filepath)

    def _on_context_menu(self, pos: QPoint) -> None:
        proxy_idx = self.indexAt(pos)
        ev = self.get_evaluation_at(proxy_idx)
        root_dir = self._resolve_root_dir()
        if ev is None or not root_dir:
            return

        p = get_active_palette()
        menu = QMenu(self)
        act_play = QAction(get_icon("play", palette=p, size=14, pad_right=7), "기본 플레이어로 재생", self)
        act_reveal = QAction(get_icon("folder", palette=p, size=14, pad_right=7), "탐색기에서 위치 열기", self)
        act_copy_name = QAction(get_icon("copy", palette=p, size=14, pad_right=7), "파일명 클립보드 복사", self)
        act_exclude = QAction(get_icon("ban", palette=p, size=14, pad_right=7), "이 파일을 예외 키워드에 추가 (원본 보존)", self)

        filepath = resolve_video_filepath(root_dir, ev.video)

        def _play() -> None:
            if os.path.isfile(filepath) and hasattr(os, "startfile"):
                os.startfile(filepath)

        def _reveal() -> None:
            if os.path.isfile(filepath):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(filepath)])

        def _copy_name() -> None:
            cb: Optional[QClipboard] = QApplication.clipboard()
            if cb is not None:
                cb.setText(ev.video.filename)

        def _add_to_exception() -> None:
            stem = os.path.splitext(ev.video.filename)[0]
            self.exception_keyword_added.emit(stem)

        act_play.triggered.connect(_play)
        act_reveal.triggered.connect(_reveal)
        act_copy_name.triggered.connect(_copy_name)
        act_exclude.triggered.connect(_add_to_exception)

        menu.addAction(act_play)
        menu.addAction(act_reveal)
        menu.addSeparator()
        menu.addAction(act_copy_name)
        menu.addAction(act_exclude)
        menu.exec(self.viewport().mapToGlobal(pos))
