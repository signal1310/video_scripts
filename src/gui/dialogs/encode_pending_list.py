"""재인코딩 시작 전 대기열 목록: 대상을 미리 확인하고 체크를 풀어 이번 배치에서 제외

- 시작 전에만 표시하고, 체크를 푼 영상은 조정자가 배치 대상에서 뺌
- 화면 스레드에서만 쓴다
"""

import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.gui.styles.palettes import ThemePalette

PENDING_TITLE: str = "시작 전 대기열"
PENDING_HINT: str = "체크를 해제한 영상은 이번 재인코딩에서 제외합니다."
PENDING_TIP: str = "이번 재인코딩 대상입니다. 체크를 해제하면 이번 배치에서 제외합니다."
LIST_RADIUS_PX: int = 8


class PendingQueueList(QWidget):
    """시작 전 대기열 목록. 항목 체크 상태가 바뀌면 exclusion_changed를 보냄"""

    exclusion_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._paths: list[str] = []
        self._palette: ThemePalette | None = None
        self._title = QLabel(PENDING_TITLE, self)
        self._title.setObjectName("GroupHeaderTitle")
        self._title.setToolTip(PENDING_HINT)
        self._list = QListWidget(self)
        self._list.setObjectName("EncodePendingList")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setToolTip(PENDING_HINT)
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.setVisible(False)
        self._title.setVisible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._title)
        layout.addWidget(self._list)

    def set_paths(self, paths: list[str]) -> None:
        """대기열을 새로 채움. 모든 항목을 체크한 상태로 두고 안내 줄은 숨김"""
        self._paths = list(paths)
        self._list.blockSignals(True)
        self._list.clear()
        for path in self._paths:
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(Qt.CheckState.Checked)
            item.setToolTip(PENDING_TIP)
            self._list.addItem(item)
        self._list.blockSignals(False)
        visible = bool(paths)
        self._list.setVisible(visible)
        self._title.setVisible(visible)
        self.exclusion_changed.emit()

    def count(self) -> int:
        """대기열 항목 수"""
        return len(self._paths)

    def names(self) -> list[str]:
        """대기열 파일 이름 목록"""
        names: list[str] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None:
                names.append(item.text())
        return names

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

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """체크 상태가 바뀌면 제외 목록이 달라졌음을 알림"""
        if item.data(Qt.ItemDataRole.UserRole) is not None:
            self.exclusion_changed.emit()
