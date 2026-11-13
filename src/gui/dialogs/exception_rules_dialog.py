"""Windows 환경변수 편집기 스타일의 여러 줄 예외 규칙 편집 대화상자 모듈"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.exception_rules import validate_exception_token
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin, ThemeManager, build_stylesheet

_DIALOG_DEFAULT_WIDTH: int = 660
_DIALOG_DEFAULT_HEIGHT: int = 470
_DIALOG_MIN_WIDTH: int = 520
_DIALOG_MIN_HEIGHT: int = 380


class ExceptionRulesDialog(QDialog, ThemeAwareMixin):
    """예외 키워드·AST 조건식을 행 단위로 추가·수정·삭제·활성 토글하는 모달 편집기"""

    def __init__(
        self,
        active_tokens: list[str],
        disabled_tokens: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("예외 규칙 편집기")
        self.resize(_DIALOG_DEFAULT_WIDTH, _DIALOG_DEFAULT_HEIGHT)
        self.setMinimumSize(_DIALOG_MIN_WIDTH, _DIALOG_MIN_HEIGHT)
        self._syncing_item: bool = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        lbl_title = QLabel("예외 규칙 목록", self)
        lbl_title.setObjectName("GroupHeaderTitle")
        lbl_desc = QLabel(
            "체크박스로 개별 규칙을 켜거나 끌 수 있습니다. 항목을 더블클릭하면 바로 수정됩니다.",
            self,
        )
        lbl_desc.setObjectName("GroupHeaderSubtitle")
        layout.addWidget(lbl_title)
        layout.addWidget(lbl_desc)

        # 상단 새 규칙 추가 바
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.input_rule = QLineEdit(self)
        self.input_rule.setPlaceholderText(
            "예외 키워드(sample, 무손실) 또는 조건식(v.duration < 10) 입력..."
        )
        self.input_rule.returnPressed.connect(self._on_add_clicked)
        self.btn_add = QPushButton("추가", self)
        self.btn_add.setObjectName("PrimaryButton")
        self.btn_add.setAutoDefault(False)
        self.btn_add.clicked.connect(self._on_add_clicked)
        add_row.addWidget(self.input_rule, stretch=1)
        add_row.addWidget(self.btn_add)
        layout.addLayout(add_row)

        # 중앙 목록 + 우측 조작 버튼 열
        body_row = QHBoxLayout()
        body_row.setSpacing(10)

        self.rules_list = QListWidget(self)
        self.rules_list.setObjectName("ExceptionRuleList")
        self.rules_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.rules_list.setAlternatingRowColors(True)
        self.rules_list.currentRowChanged.connect(self._update_button_states)
        self.rules_list.itemChanged.connect(self._on_item_changed)
        body_row.addWidget(self.rules_list, stretch=1)

        side_col = QVBoxLayout()
        side_col.setSpacing(6)
        self.btn_edit = QPushButton("수정", self)
        self.btn_move_up = QPushButton("위로", self)
        self.btn_move_down = QPushButton("아래로", self)
        self.btn_delete = QPushButton("삭제", self)
        self.btn_enable_all = QPushButton("전체 활성", self)
        self.btn_disable_all = QPushButton("전체 비활성", self)

        for btn in (
            self.btn_edit,
            self.btn_move_up,
            self.btn_move_down,
            self.btn_delete,
            self.btn_enable_all,
            self.btn_disable_all,
        ):
            btn.setAutoDefault(False)
            btn.setMinimumWidth(96)
            side_col.addWidget(btn)
        side_col.insertStretch(4, 1)
        body_row.addLayout(side_col)
        layout.addLayout(body_row, stretch=1)

        self.btn_edit.clicked.connect(self._on_edit_selected)
        self.btn_move_up.clicked.connect(lambda: self.move_selected(-1))
        self.btn_move_down.clicked.connect(lambda: self.move_selected(1))
        self.btn_delete.clicked.connect(self.remove_selected_rule)
        self.btn_enable_all.clicked.connect(lambda: self.set_all_enabled(True))
        self.btn_disable_all.clicked.connect(lambda: self.set_all_enabled(False))

        self.lbl_error = QLabel("", self)
        self.lbl_error.setObjectName("ErrorHintLabel")
        self.lbl_error.setWordWrap(True)
        self.lbl_error.setVisible(False)
        layout.addWidget(self.lbl_error)

        lbl_hint = QLabel(
            "허용 속성(v.*): filename, width, height, rotate_type, fps, vid_kbps, aud_kbps, vid_size_MB, duration, codec, keyframe_interval, moved_dirname, aspect_ratio",
            self,
        )
        lbl_hint.setObjectName("CardSubtext")
        lbl_hint.setWordWrap(True)
        layout.addWidget(lbl_hint)

        footer = QHBoxLayout()
        footer.addStretch()
        self.btn_cancel = QPushButton("취소", self)
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_apply = QPushButton("적용", self)
        self.btn_apply.setObjectName("PrimaryButton")
        self.btn_apply.setAutoDefault(False)
        self.btn_apply.clicked.connect(self._on_apply_clicked)
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_apply)
        layout.addLayout(footer)

        for tok in active_tokens:
            self._append_item(tok, enabled=True)
        for tok in disabled_tokens or []:
            self._append_item(tok, enabled=False)

        self._validate_all_rules()
        self._update_button_states()

        self.apply_theme(ThemeManager().current_palette)
        self.init_theme_listener()

    def _create_item(self, token: str, *, enabled: bool) -> QListWidgetItem:
        item = QListWidgetItem(token)
        item.setFlags(
            item.flags()
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsEditable
        )
        item.setCheckState(
            Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
        )
        return item

    def _append_item(self, token: str, *, enabled: bool) -> bool:
        clean = token.strip()
        if not clean:
            return False
        lowered = clean.lower()
        for row in range(self.rules_list.count()):
            existing = self.rules_list.item(row)
            if existing is not None and existing.text().strip().lower() == lowered:
                return False
        self._syncing_item = True
        try:
            self.rules_list.addItem(self._create_item(clean, enabled=enabled))
        finally:
            self._syncing_item = False
        return True

    def add_rule(self, token: str, *, enabled: bool = True) -> bool:
        """새 예외 규칙을 검증 후 목록에 추가하고 성공 여부를 반환"""
        clean = token.strip()
        if not clean:
            self._show_error("추가할 예외 키워드나 조건식을 입력해 주세요.")
            return False
        _, err = validate_exception_token(clean)
        if err:
            self._show_error(f"예외 규칙 오류: {err}")
            return False
        if not self._append_item(clean, enabled=enabled):
            self._show_error(f"이미 동일한 예외 규칙이 등록되어 있습니다: '{clean}'")
            return False
        self.rules_list.setCurrentRow(self.rules_list.count() - 1)
        self._validate_all_rules()
        self._update_button_states()
        return True

    def remove_selected_rule(self) -> bool:
        """현재 선택된 예외 규칙 행을 목록에서 삭제"""
        row = self.rules_list.currentRow()
        if row < 0:
            return False
        taken = self.rules_list.takeItem(row)
        del taken
        self._validate_all_rules()
        self._update_button_states()
        return True

    def move_selected(self, delta: int) -> bool:
        """현재 선택된 예외 규칙의 순서를 위(-1) 또는 아래(+1)로 이동"""
        row = self.rules_list.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.rules_list.count():
            return False
        self._syncing_item = True
        try:
            item = self.rules_list.takeItem(row)
            if item is None:
                return False
            self.rules_list.insertItem(target, item)
            self.rules_list.setCurrentRow(target)
        finally:
            self._syncing_item = False
        self._update_button_states()
        return True

    def set_all_enabled(self, enabled: bool) -> None:
        """목록 내 모든 예외 규칙의 활성 체크 상태를 일괄 전환"""
        state = Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
        self._syncing_item = True
        try:
            for row in range(self.rules_list.count()):
                item = self.rules_list.item(row)
                if item is not None:
                    item.setCheckState(state)
        finally:
            self._syncing_item = False
        self._validate_all_rules()
        self._update_button_states()

    def get_tokens(self) -> tuple[list[str], list[str]]:
        """현재 목록의 (활성 규칙 리스트, 비활성 규칙 리스트) 쌍을 순서대로 반환"""
        active: list[str] = []
        disabled: list[str] = []
        for row in range(self.rules_list.count()):
            item = self.rules_list.item(row)
            if item is None:
                continue
            text = item.text().strip()
            if not text:
                continue
            if item.checkState() == Qt.CheckState.Checked:
                active.append(text)
            else:
                disabled.append(text)
        return active, disabled

    def _show_error(self, message: str) -> None:
        if message:
            self.lbl_error.setText(message)
            self.lbl_error.setVisible(True)
        else:
            self.lbl_error.clear()
            self.lbl_error.setVisible(False)

    def _validate_all_rules(self) -> bool:
        seen: set[str] = set()
        for row in range(self.rules_list.count()):
            item = self.rules_list.item(row)
            if item is None:
                continue
            text = item.text().strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                self._show_error(f"중복된 예외 규칙이 있습니다: '{text}'")
                self.btn_apply.setEnabled(False)
                return False
            seen.add(lowered)
            _, err = validate_exception_token(text)
            if err:
                self._show_error(f"예외 규칙 오류 ({text}): {err}")
                self.btn_apply.setEnabled(False)
                return False
        self._show_error("")
        self.btn_apply.setEnabled(True)
        return True

    def _update_button_states(self, *_args: object) -> None:
        row = self.rules_list.currentRow()
        count = self.rules_list.count()
        has_sel = 0 <= row < count
        self.btn_edit.setEnabled(has_sel)
        self.btn_delete.setEnabled(has_sel)
        self.btn_move_up.setEnabled(has_sel and row > 0)
        self.btn_move_down.setEnabled(has_sel and row < count - 1)
        self.btn_enable_all.setEnabled(count > 0)
        self.btn_disable_all.setEnabled(count > 0)

    def _on_add_clicked(self) -> None:
        if self.add_rule(self.input_rule.text(), enabled=True):
            self.input_rule.clear()
            self.input_rule.setFocus()

    def _on_edit_selected(self) -> None:
        item = self.rules_list.currentItem()
        if item is not None:
            self.rules_list.editItem(item)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._syncing_item:
            return
        cleaned = item.text().strip()
        if not cleaned:
            row = self.rules_list.row(item)
            if row >= 0:
                taken = self.rules_list.takeItem(row)
                del taken
        elif cleaned != item.text():
            self._syncing_item = True
            try:
                item.setText(cleaned)
            finally:
                self._syncing_item = False
        self._validate_all_rules()
        self._update_button_states()

    def _on_apply_clicked(self) -> None:
        if self._validate_all_rules():
            self.accept()

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 대화상자 스타일시트를 즉시 갱신"""
        self.setStyleSheet(build_stylesheet(palette))
