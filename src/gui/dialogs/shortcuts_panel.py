"""설정 대화상자 단축키 탭의 키 캡처 버튼, 명령 행, 기능 그룹 카드 배치와 중복 확인 모듈"""

from collections.abc import Callable, Mapping

from PyQt6.QtCore import QEvent, QKeyCombination, QObject, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFocusEvent, QIcon, QKeyEvent, QKeySequence, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.shortcuts import (
    DEFAULT_SHORTCUTS,
    SHORTCUT_GROUPS,
    SHORTCUT_SPEC_BY_ID,
    SHORTCUT_SPECS,
    ShortcutGroup,
    ShortcutSpec,
    diff_shortcut_overrides,
    find_conflict_action,
    normalize_shortcut,
    resolve_shortcuts,
)
from src.gui.dialogs.setting_controls import (
    RESET_BUTTON_SIZE,
    RESET_ICON_SIZE,
    apply_inline_reset_button_icon,
    apply_row_reset_icon,
    build_row_reset_button,
    build_scroll_tab_shell,
)
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.gui.widgets.settings_group_card import SettingsGroupCard

_MODIFIER_KEYS: tuple[Qt.Key, ...] = (
    Qt.Key.Key_Control,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Alt,
    Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr,
)
_EMPTY_ICON_NAME: str = "keyboard"
_EMPTY_ICON_SIZE: int = 14
_CAPTURE_PROMPT_TEXT: str = "키 입력..."
_CAPTURE_TOOLTIP: str = "칸을 누른 뒤 키를 입력하면 단축키가 지정됩니다."
_CLEAR_ICON_NAME: str = "x"
_CLEAR_ACCESSIBLE_NAME: str = "단축키 해제"
_RESET_ROW_TOOLTIP: str = "기본 단축키로 되돌립니다."
_CLEAR_ROW_TOOLTIP: str = "단축키를 해제합니다."
_PANEL_DESC_TEXT: str = "변경 즉시 저장, 적용됩니다. 칸을 누르고 키를 입력해 지정합니다."
_RESET_ALL_BUTTON_TEXT: str = "단축키 초기화"
_RESET_ALL_TOOLTIP: str = "모든 명령의 단축키를 기본값으로 되돌립니다."
_SEARCH_PLACEHOLDER: str = "명령 이름, 기능 그룹, 단축키 검색..."
_NO_RESULT_TEXT: str = "일치하는 명령이 없습니다."
_CONFLICT_TITLE: str = "단축키 중복 확인"
_RESET_ALL_TITLE: str = "단축키 초기화"
_RESET_ALL_QUESTION: str = (
    "모든 명령의 단축키를 기본값으로 되돌릴까요?\n"
    "(직접 지정한 단축키는 모두 사라집니다.)"
)


def key_event_to_shortcut_str(event: QKeyEvent) -> str | None:
    """키 입력을 QKeySequence 표준 문자열로 변환하며, 수식키 단독 입력은 None 반환

    - 단독 Enter는 Return으로 바꿔 기본값과 같은 표기를 쓰도록 함
    """
    key = event.key()
    if key in _MODIFIER_KEYS:
        return None
    qt_key = Qt.Key.Key_Return if key == Qt.Key.Key_Enter else Qt.Key(key)
    combination = QKeyCombination(event.modifiers(), qt_key)
    text = QKeySequence(combination).toString(QKeySequence.SequenceFormat.PortableText)
    return normalize_shortcut(text) if text else None


class ShortcutCaptureButton(QPushButton):
    """클릭 후 키를 누르면 단축키를 받는 칸, 포커스 이탈이나 외부 클릭 시 캡처를 취소"""

    shortcut_captured = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ShortcutCaptureButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(_EMPTY_ICON_SIZE, _EMPTY_ICON_SIZE))
        self.setToolTip(_CAPTURE_TOOLTIP)
        self._key: str = ""
        self._capturing: bool = False
        self._palette: ThemePalette | None = None
        # 마우스는 mousePressEvent가 시작하므로, 스페이스 같은 키보드 활성화도 같은 입력 대기로 연결
        self.clicked.connect(self.begin_capture)
        self._refresh_display()

    def apply_palette(self, palette: ThemePalette) -> None:
        """테마 팔레트를 지정해 빈 칸 플레이스홀더 아이콘 색상을 갱신"""
        self._palette = palette
        self._refresh_display()

    def shortcut_text(self) -> str:
        """현재 할당된 단축키 문자열, 미지정이면 빈 문자열"""
        return self._key

    def is_capturing(self) -> bool:
        """키 입력 대기 상태인지 반환"""
        return self._capturing

    def set_key(self, key: str) -> None:
        """표시할 단축키를 바꾸며, 대기 중이었다면 캡처를 취소"""
        self._end_capture()
        self._key = key
        self._refresh_display()

    def begin_capture(self) -> None:
        """키 입력 대기 상태로 전환하고 외부 마우스 클릭 감시를 시작"""
        if self._capturing:
            return
        self._capturing = True
        self._refresh_display()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def cancel_capture(self) -> None:
        """대기 상태를 해제하고 기존 단축키 표시로 되돌림"""
        if self._capturing:
            self._end_capture()

    def mousePressEvent(self, e: QMouseEvent | None) -> None:
        """칸을 누르면 키 입력 대기 상태로 전환"""
        self.begin_capture()
        super().mousePressEvent(e)

    def keyPressEvent(self, e: QKeyEvent | None) -> None:
        """대기 중에는 수식키 단독 입력을 무시하고, 그 밖의 키는 단축키로 받아 신호로 전달"""
        if e is None or not self._capturing:
            super().keyPressEvent(e)
            return
        e.accept()
        text = key_event_to_shortcut_str(e)
        if text is None:
            return
        self._end_capture()
        self.shortcut_captured.emit(text)

    def focusOutEvent(self, e: QFocusEvent | None) -> None:
        """포커스를 잃으면 키 입력 대기를 취소"""
        self.cancel_capture()
        super().focusOutEvent(e)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """대기 중 이 칸 밖을 누르면 캡처를 취소하고 이벤트는 그대로 통과"""
        if (
            self._capturing
            and a0 is not self
            and isinstance(a1, QMouseEvent)
            and a1.type() == QEvent.Type.MouseButtonPress
        ):
            self.cancel_capture()
        return super().eventFilter(a0, a1)

    def _end_capture(self) -> None:
        if not self._capturing:
            return
        self._capturing = False
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._refresh_display()

    def _refresh_display(self) -> None:
        is_empty = not self._key and not self._capturing
        if self._capturing:
            self.setText(_CAPTURE_PROMPT_TEXT)
        else:
            self.setText(self._key)
        self.setIcon(self._placeholder_icon() if is_empty else QIcon())
        _set_state_property(self, "capturing", self._capturing)

    def _placeholder_icon(self) -> QIcon:
        """미지정 상태를 나타내는 흐린 키보드 아이콘, 팔레트 미지정 시 활성 팔레트 사용"""
        palette = self._palette or get_active_palette()
        return get_icon(_EMPTY_ICON_NAME, color=palette.text_muted, size=_EMPTY_ICON_SIZE)


def _retain_slot_when_hidden(widget: QWidget) -> None:
    """숨김 상태에서도 가로 자리를 유지해 버튼 표시 전환 시 행 안의 칸 위치가 밀리지 않도록 설정"""
    policy = widget.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    widget.setSizePolicy(policy)


def apply_clear_icon(button: QPushButton, palette: ThemePalette) -> None:
    """해제 버튼의 아이콘을 현재 팔레트의 보조 글자색으로 갱신"""
    button.setIcon(get_icon(_CLEAR_ICON_NAME, color=palette.text_secondary, size=RESET_ICON_SIZE))


def _set_state_property(widget: QWidget, name: str, value: bool) -> None:
    """QSS 속성 선택자가 상태 변경을 반영하도록 동적 속성을 설정하고 스타일을 다시 적용"""
    widget.setProperty(name, "true" if value else "false")
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


class _ShortcutRow(QFrame):
    """명령 하나의 이름, 단축키 칸, 해제 버튼, 기본값 복원 버튼으로 구성된 행"""

    def __init__(
        self,
        spec: ShortcutSpec,
        *,
        on_captured: Callable[[str], None],
        on_clear: Callable[[], None],
        on_reset: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsCard")
        self.spec = spec

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 4, 8, 4)
        outer.setSpacing(2)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        outer.addLayout(layout)

        self.lbl_name = QLabel(spec.label, self)
        self.lbl_name.setToolTip(spec.description)
        # 줄바꿈을 허용해야 좁은 창에서 명령 이름이 행의 최소 폭을 밀어내지 않음
        self.lbl_name.setWordWrap(True)
        layout.addWidget(self.lbl_name, stretch=1)

        self.capture = ShortcutCaptureButton(self)
        self.capture.shortcut_captured.connect(on_captured)
        layout.addWidget(self.capture)

        self.btn_clear = QPushButton(self)
        # 기본값 복원 버튼과 같은 아이콘 버튼 서식을 공유
        self.btn_clear.setObjectName("ResetButton")
        self.btn_clear.setFixedSize(RESET_BUTTON_SIZE, RESET_BUTTON_SIZE)
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setToolTip(_CLEAR_ROW_TOOLTIP)
        self.btn_clear.setAccessibleName(_CLEAR_ACCESSIBLE_NAME)
        apply_clear_icon(self.btn_clear, get_active_palette())
        self.btn_clear.clicked.connect(lambda _checked=False: on_clear())
        _retain_slot_when_hidden(self.btn_clear)
        layout.addWidget(self.btn_clear)

        self.btn_reset = build_row_reset_button(
            _RESET_ROW_TOOLTIP,
            on_reset,
            self,
        )
        layout.addWidget(self.btn_reset)

        self.lbl_hint = QLabel(spec.description, self)
        self.lbl_hint.setObjectName("SettingDesc")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setVisible(False)
        outer.addWidget(self.lbl_hint)

    def set_hint_visible(self, visible: bool) -> None:
        """명령 설명 라벨을 이름 아래에 표시하거나 숨김"""
        self.lbl_hint.setVisible(visible)

    def refresh(self, key: str, default_key: str) -> None:
        """현재 유효 단축키와 기본값을 칸에 반영하고, 해당 없는 해제, 복원 버튼은 숨김"""
        self.capture.set_key(key)
        self.btn_clear.setVisible(bool(key))
        self.btn_reset.setVisible(key != default_key)

    def search_text(self, group_title: str) -> str:
        """필터 검색 대상 문자열, 기능 그룹 이름과 명령 이름과 현재 단축키를 포함"""
        return f"{group_title} {self.spec.label} {self.capture.shortcut_text()}"


class ShortcutsPanel(QWidget, ThemeAwareMixin):
    """명령별 단축키를 기능 그룹 카드로 보여 주고 지정, 해제, 기본값 복원을 처리하는 설정 탭 위젯"""

    shortcuts_changed = pyqtSignal(dict)

    def __init__(
        self,
        overrides: Mapping[str, str] | None = None,
        *,
        show_hints: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ShortcutsPanel")
        self._effective: dict[str, str] = resolve_shortcuts(overrides)
        self._rows: dict[str, _ShortcutRow] = {}
        self._groups: list[tuple[ShortcutGroup, SettingsGroupCard, list[_ShortcutRow]]] = []
        self._build()
        self.set_hints_visible(show_hints)
        self._refresh_all()
        self.init_theme_listener()

    def set_hints_visible(self, visible: bool) -> None:
        """모든 명령 행의 설명 라벨 표시 여부를 전환"""
        for row in self._rows.values():
            row.set_hint_visible(visible)

    def current_overrides(self) -> dict[str, str]:
        """기본값과 다른 단축키만 담은 최소 오버라이드 사전 반환"""
        return diff_shortcut_overrides(self._effective)

    def capture_button(self, action_id: str) -> ShortcutCaptureButton:
        """명령의 단축키 입력 칸"""
        return self._rows[action_id].capture

    def clear_button(self, action_id: str) -> QPushButton:
        """명령의 해제 버튼"""
        return self._rows[action_id].btn_clear

    def reset_button(self, action_id: str) -> QPushButton:
        """명령의 기본값 복원 버튼"""
        return self._rows[action_id].btn_reset

    def is_row_visible(self, action_id: str) -> bool:
        """검색 필터 적용 후 명령 행이 보이는지 반환"""
        return not self._rows[action_id].isHidden()

    def is_group_visible(self, group_key: str) -> bool:
        """검색 필터 적용 후 기능 그룹 카드가 보이는지 반환"""
        for group, card, _rows in self._groups:
            if group.key == group_key:
                return not card.isHidden()
        return False

    def set_overrides(self, overrides: Mapping[str, str]) -> None:
        """외부에서 저장값을 다시 읽어 표시를 맞춤, 신호는 보내지 않음"""
        self._effective = resolve_shortcuts(overrides)
        self._refresh_all()

    def set_shortcut_for(
        self,
        action_id: str,
        new_key: str,
        *,
        confirm_conflict: bool = True,
    ) -> bool:
        """명령에 단축키를 지정하고, 중복이면 확인 후 기존 명령의 단축키를 해제

        - 확인에서 거절하면 아무 것도 바꾸지 않고 False 반환
        """
        target = SHORTCUT_SPEC_BY_ID[action_id]
        norm = normalize_shortcut(new_key)
        if self._effective.get(action_id, "") == norm:
            return True
        conflict = find_conflict_action(self._effective, action_id, norm)
        if conflict is not None:
            if confirm_conflict and not self._confirm_conflict(norm, conflict, target):
                return False
            self._effective[conflict.action_id] = ""
        self._effective[action_id] = norm
        self._commit()
        return True

    def reset_action(self, action_id: str) -> bool:
        """명령 하나를 기본 단축키로 되돌리며, 다른 명령과 겹치면 중복 확인을 거침"""
        return self.set_shortcut_for(action_id, SHORTCUT_SPEC_BY_ID[action_id].default_key)

    def reset_all_shortcuts(self, *, confirm: bool = True) -> bool:
        """모든 명령의 단축키를 기본값으로 되돌림, 확인에서 거절하면 False 반환"""
        if confirm and not self._confirm_reset_all():
            return False
        self._effective = dict(DEFAULT_SHORTCUTS)
        self._commit()
        return True

    def apply_theme(self, palette: ThemePalette) -> None:
        """테마 팔레트에 맞춰 복원 아이콘 색상을 갱신"""
        apply_inline_reset_button_icon(self.btn_reset_all, palette)
        for row in self._rows.values():
            apply_row_reset_icon(row.btn_reset, palette)
            apply_clear_icon(row.btn_clear, palette)
            row.capture.apply_palette(palette)

    def _build(self) -> None:
        _scroll, _body, body_layout, _lbl_desc, self.btn_reset_all = build_scroll_tab_shell(
            self,
            desc_text=_PANEL_DESC_TEXT,
            reset_button_text=_RESET_ALL_BUTTON_TEXT,
            reset_tooltip=_RESET_ALL_TOOLTIP,
            on_reset=self._on_reset_all_clicked,
        )
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText(_SEARCH_PLACEHOLDER)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _text: self._apply_filter())
        body_layout.addWidget(self.search_edit)

        for group in SHORTCUT_GROUPS:
            card = SettingsGroupCard(group.icon_name, group.title, parent=self)
            rows: list[_ShortcutRow] = []
            for spec in SHORTCUT_SPECS:
                if spec.group_key != group.key:
                    continue
                row = self._make_row(spec)
                card.add_row(row)
                rows.append(row)
            body_layout.addWidget(card)
            self._groups.append((group, card, rows))

        self.lbl_no_result = QLabel(_NO_RESULT_TEXT, self)
        self.lbl_no_result.setObjectName("SettingDesc")
        self.lbl_no_result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_no_result.setVisible(False)
        body_layout.addWidget(self.lbl_no_result)
        body_layout.addStretch(1)

    def _make_row(self, spec: ShortcutSpec) -> _ShortcutRow:
        action_id = spec.action_id
        row = _ShortcutRow(
            spec,
            on_captured=lambda key: self._on_key_captured(action_id, key),
            on_clear=lambda: self._on_clear_clicked(action_id),
            on_reset=lambda: self._on_reset_row_clicked(action_id),
            parent=self,
        )
        self._rows[action_id] = row
        return row

    def _on_key_captured(self, action_id: str, key: str) -> None:
        """키 입력 결과를 지정하고, 거절되면 칸을 이전 단축키 표시로 되돌림"""
        if not self.set_shortcut_for(action_id, key):
            self._refresh_all()

    def _on_clear_clicked(self, action_id: str) -> None:
        self.set_shortcut_for(action_id, "")

    def _on_reset_row_clicked(self, action_id: str) -> None:
        self.reset_action(action_id)

    def _on_reset_all_clicked(self) -> None:
        self.reset_all_shortcuts()

    def _commit(self) -> None:
        self._refresh_all()
        self.shortcuts_changed.emit(self.current_overrides())

    def _refresh_all(self) -> None:
        for action_id, row in self._rows.items():
            row.refresh(
                self._effective.get(action_id, ""),
                SHORTCUT_SPEC_BY_ID[action_id].default_key,
            )
        self.btn_reset_all.setEnabled(self.current_overrides() != {})
        self._apply_filter()

    def _apply_filter(self) -> None:
        """검색어가 명령 이름, 기능 그룹, 단축키 문자열 중 하나와 맞는 행만 남기고 빈 그룹 카드는 숨김"""
        query = self.search_edit.text().strip().lower()
        total_visible = 0
        for group, card, rows in self._groups:
            visible_count = 0
            for row in rows:
                matched = not query or query in row.search_text(group.title).lower()
                row.setVisible(matched)
                visible_count += int(matched)
            card.setVisible(visible_count > 0)
            total_visible += visible_count
        self.lbl_no_result.setVisible(total_visible == 0)

    def _confirm_conflict(
        self,
        key: str,
        conflict: ShortcutSpec,
        target: ShortcutSpec,
    ) -> bool:
        answer = QMessageBox.question(
            self,
            _CONFLICT_TITLE,
            f"'{key}' 단축키는 이미 '{conflict.label}' 명령에 지정되어 있습니다.\n"
            f"기존 단축키를 해제하고 '{target.label}'에 지정할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _confirm_reset_all(self) -> bool:
        answer = QMessageBox.question(
            self,
            _RESET_ALL_TITLE,
            _RESET_ALL_QUESTION,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes
