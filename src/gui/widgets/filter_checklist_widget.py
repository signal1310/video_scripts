"""팝오버 내 검색 가능한 다중 체크리스트 위젯 모듈"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_VALUE_LIST_MIN_HEIGHT: int = 88
_VALUE_ITEM_HEIGHT_HINT: int = 28
_LIST_VERTICAL_PADDING: int = 12


class FilterChecklistWidget(QWidget):
    """검색창, 전체 선택/해제 링크, 선택 개수 라벨을 포함한 체크리스트 위젯"""

    selection_changed = pyqtSignal()

    def __init__(
        self,
        items: list[tuple[str, str, int]],
        selected: frozenset[str] | None,
        *,
        include_search: bool,
        max_list_height: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._total = len(items)
        self._syncing = False
        self.preferred_content_width: int = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.edit_search: QLineEdit | None = None
        if include_search:
            search = QLineEdit(self)
            search.setPlaceholderText("항목 검색...")
            search.setClearButtonEnabled(True)
            search.textChanged.connect(self._filter_rows)
            self.edit_search = search
            layout.addWidget(search)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.btn_select_all = self._create_link_button("모두 선택", True)
        self.btn_deselect_all = self._create_link_button("모두 해제", False)
        self.lbl_selected_count = QLabel("", self)
        self.lbl_selected_count.setObjectName("SectionLabel")
        self.lbl_selected_count.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        actions.addWidget(self.btn_select_all)
        actions.addWidget(self.btn_deselect_all)
        actions.addStretch(1)
        actions.addWidget(self.lbl_selected_count)
        layout.addLayout(actions)

        lst = QListWidget(self)
        lst.setObjectName("ColumnFilterValueList")
        lst.setAlternatingRowColors(True)
        lst.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list_values = lst

        self.preferred_content_width = self._populate_items(items, selected)
        est_height = max(
            _VALUE_LIST_MIN_HEIGHT,
            min(max_list_height, len(items) * _VALUE_ITEM_HEIGHT_HINT + _LIST_VERTICAL_PADDING),
        )
        lst.setFixedHeight(est_height)
        lst.itemChanged.connect(self._on_item_changed)
        layout.addWidget(lst)
        self._update_count_label()

    @property
    def total_count(self) -> int:
        """전체 고유 항목 수 반환"""
        return self._total

    def _create_link_button(self, text: str, checked_target: bool) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setObjectName("PopoverLinkButton")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(
            lambda: self.apply_checked_state(
                checked_target, visible_only=True, emit_signal=True
            )
        )
        return btn

    def _populate_items(
        self,
        items: list[tuple[str, str, int]],
        selected: frozenset[str] | None,
    ) -> int:
        max_text_px = 0
        fm = self.list_values.fontMetrics()
        checkable_flags = (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        self._syncing = True
        try:
            if not items:
                empty_item = QListWidgetItem("(표시할 항목 없음)")
                empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list_values.addItem(empty_item)
                self.btn_select_all.setEnabled(False)
                self.btn_deselect_all.setEnabled(False)
            else:
                for raw_key, display_label, count in items:
                    display_text = f"{display_label} ({count})"
                    max_text_px = max(max_text_px, fm.horizontalAdvance(display_text))
                    item = QListWidgetItem(display_text)
                    item.setToolTip(display_text)
                    item.setData(Qt.ItemDataRole.UserRole, raw_key)
                    item.setFlags(checkable_flags)
                    is_checked = selected is None or raw_key in selected
                    item.setCheckState(
                        Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked
                    )
                    self.list_values.addItem(item)
        finally:
            self._syncing = False
        return max_text_px

    def _filter_rows(self, text: str) -> None:
        query = text.strip().lower()
        for row in range(self.list_values.count()):
            item = self.list_values.item(row)
            if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
                continue
            raw_val = item.data(Qt.ItemDataRole.UserRole)
            matched = query in str(raw_val).lower() or query in item.text().lower()
            item.setHidden(bool(query) and not matched)

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        if self._syncing:
            return
        self._update_count_label()
        self.selection_changed.emit()

    def _update_count_label(self) -> None:
        self.lbl_selected_count.setText(
            f"{len(self.selected_values())} / {self._total} 선택"
        )

    def set_controls_enabled(self, enabled: bool) -> None:
        """빈 목록 여부를 유지하면서 체크리스트와 전체 선택 버튼 활성 상태를 전환"""
        if self._total <= 0:
            return
        self.list_values.setEnabled(enabled)
        self.btn_select_all.setEnabled(enabled)
        self.btn_deselect_all.setEnabled(enabled)

    def apply_checked_state(
        self, checked: bool, *, visible_only: bool, emit_signal: bool
    ) -> None:
        """항목들의 체크 상태를 일괄 변경하고 필요 시 selection_changed 방출"""
        if self._total == 0:
            return
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._syncing = True
        try:
            for row in range(self.list_values.count()):
                item = self.list_values.item(row)
                if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
                    continue
                if visible_only and item.isHidden():
                    continue
                item.setCheckState(state)
        finally:
            self._syncing = False
        self._update_count_label()
        if emit_signal:
            self.selection_changed.emit()

    def selected_values(self) -> set[str]:
        """체크리스트에서 현재 체크된 고유 값 집합 반환"""
        if self._total == 0:
            return set()
        selected: set[str] = set()
        for row in range(self.list_values.count()):
            item = self.list_values.item(row)
            if item is None:
                continue
            raw_val = item.data(Qt.ItemDataRole.UserRole)
            if raw_val is not None and item.checkState() == Qt.CheckState.Checked:
                selected.add(str(raw_val))
        return selected
