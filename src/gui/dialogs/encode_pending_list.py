"""인코딩 시작 전 대기열 목록: 대상을 미리 확인하고 체크를 풀어 이번 배치에서 제외

- 시작 전에만 표시하고, 체크를 푼 영상은 조정자가 배치 대상에서 뺌
- 영상 행은 작업 폴더 기준 상대 경로, 메인 표와 같은 상세 툴팁, 더블클릭 재생, 스페이스바 미리 보기,
  화질 비교 단축키를 제공
- 화면 스레드에서만 쓴다
"""

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import Qt, pyqtBoundSignal, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from src.gui.dialogs.encode_impact_tree import SummaryMedia
from src.gui.dialogs.encode_progress_panels import compare_shortcut_pressed
from src.gui.styles.palettes import ThemePalette
from src.gui.table.table_actions import missing_notice

PENDING_TITLE: str = "시작 전 대기열"
PENDING_HINT: str = "체크를 해제한 영상은 이번 인코딩에서 제외합니다."
PENDING_TIP: str = "이번 인코딩 대상입니다. 체크를 해제하면 이번 배치에서 제외합니다."
MISSING_TITLE: str = "인코딩"
LIST_RADIUS_PX: int = 8


@dataclass(frozen=True, slots=True)
class PendingRow:
    """시작 전 대기열 한 줄. path는 제외 판정에 쓰는 절대 경로, label은 작업 폴더 기준 상대 경로"""

    path: str
    label: str
    tooltip: str


class _PendingListWidget(QListWidget):
    """대기열 목록. 스페이스바는 미리 보기, 화질 비교 단축키는 화질 비교, 더블클릭은 재생 요청으로 넘김"""

    open_requested = pyqtSignal(str)
    preview_requested = pyqtSignal(str)
    compare_requested = pyqtSignal(str)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is not None:
            if compare_shortcut_pressed(event):
                self._emit_current(self.compare_requested)
                event.accept()
                return
            plain_space = not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if event.key() == Qt.Key.Key_Space and plain_space:
                self._emit_current(self.preview_requested)
                event.accept()
                return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:
        if event is not None:
            self._emit_current(self.open_requested)
        super().mouseDoubleClickEvent(event)

    def _emit_current(self, signal: pyqtBoundSignal) -> None:
        """현재 행의 절대 경로로 요청을 보냄. 행이 없으면 아무것도 하지 않음"""
        item = self.currentItem()
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(path, str):
            signal.emit(path)


class PendingQueueList(QWidget):
    """시작 전 대기열 목록. 항목 체크 상태가 바뀌면 exclusion_changed를 보냄"""

    exclusion_changed = pyqtSignal()

    def __init__(self, media: SummaryMedia, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._media: SummaryMedia = media
        self._paths: list[str] = []
        self._palette: ThemePalette | None = None
        self._title = QLabel(PENDING_TITLE, self)
        self._title.setObjectName("GroupHeaderTitle")
        self._title.setToolTip(PENDING_HINT)
        self._list = _PendingListWidget(self)
        self._list.setObjectName("EncodePendingList")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setToolTip(PENDING_HINT)
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.open_requested.connect(lambda path: self._play(path, media.open_file))
        self._list.preview_requested.connect(lambda path: self._play(path, media.preview_file))
        self._list.compare_requested.connect(media.compare_files)
        self._list.setVisible(False)
        self._title.setVisible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._title)
        layout.addWidget(self._list)

    def set_rows(self, rows: Sequence[PendingRow], *, notify: bool = True) -> None:
        """대기열을 새로 채움. 모두 체크한 상태로 두고, 행이 없으면 목록과 안내 줄을 숨김"""
        self._paths = [row.path for row in rows]
        self._list.blockSignals(True)
        self._list.clear()
        for row in rows:
            item = QListWidgetItem(row.label)
            item.setData(Qt.ItemDataRole.UserRole, row.path)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(Qt.CheckState.Checked)
            item.setToolTip(row.tooltip)
            self._list.addItem(item)
        self._list.blockSignals(False)
        visible = bool(rows)
        self._list.setVisible(visible)
        self._title.setVisible(visible)
        if notify:
            self.exclusion_changed.emit()

    def count(self) -> int:
        """대기열 항목 수"""
        return len(self._paths)

    def names(self) -> list[str]:
        """대기열 표시 이름 목록(작업 폴더 기준 상대 경로)"""
        names: list[str] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None:
                names.append(item.text())
        return names

    def current_path(self) -> str | None:
        """선택한 행의 절대 경로. 선택한 행이 없으면 None"""
        item = self._list.currentItem()
        if item is None:
            return None
        path = item.data(Qt.ItemDataRole.UserRole)
        return path if isinstance(path, str) else None

    def excluded_paths(self) -> frozenset[str]:
        """체크가 해제된 영상의 절대 경로"""
        excluded: set[str] = set()
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is None:
                continue
            path = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(path, str) and item.checkState() == Qt.CheckState.Unchecked:
                excluded.add(path)
        return frozenset(excluded)

    def clear(self) -> None:
        """배치가 시작되면 대기열을 비우고 숨김"""
        self._paths = []
        self._list.blockSignals(True)
        self._list.clear()
        self._list.blockSignals(False)
        self._list.setVisible(False)
        self._title.setVisible(False)

    def apply_palette(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 목록 색을 칠함"""
        self._palette = palette
        self._list.setStyleSheet(
            f"QListWidget#EncodePendingList {{ background: {palette.bg_card};"
            f" border: 1px solid {palette.border_subtle}; border-radius: {LIST_RADIUS_PX}px;"
            f" color: {palette.text_primary}; padding: 4px; outline: none; }}"
            f"QListWidget#EncodePendingList::item {{ padding: 4px 6px; border-radius: 4px; }}"
            f"QListWidget#EncodePendingList::item:selected {{ background: {palette.bg_selected};"
            f" color: {palette.text_primary}; }}"
        )

    def _play(self, path: str, action: Callable[[str], None]) -> None:
        """파일이 있으면 열기나 미리 보기로 넘기고, 없으면 메인 표와 같은 안내를 보여 줌"""
        if not os.path.isfile(path):
            notice = missing_notice(1, 1)
            if notice is not None:
                QMessageBox.warning(self, MISSING_TITLE, notice)
            return
        action(path)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """체크 상태가 바뀌면 제외 목록이 달라졌음을 알림"""
        if item.data(Qt.ItemDataRole.UserRole) is not None:
            self.exclusion_changed.emit()
