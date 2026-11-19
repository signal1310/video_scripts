"""설정 대화상자 단축키 탭의 기능 그룹 카드 배치, 검색 필터, 중복 확인 모듈"""

from collections.abc import Mapping

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from src.core.settings.shortcuts import (
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
from src.gui.settings.setting_controls import (
    apply_inline_reset_button_icon,
    apply_row_reset_icon,
    build_scroll_tab_shell,
)
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.settings.shortcut_row import (
    ShortcutCaptureButton,
    ShortcutRow,
    apply_clear_icon,
    key_event_to_shortcut_str,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin

__all__ = ["ShortcutCaptureButton", "ShortcutsPanel", "key_event_to_shortcut_str"]

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
        self._rows: dict[str, ShortcutRow] = {}
        self._groups: list[tuple[ShortcutGroup, SettingsGroupCard, list[ShortcutRow]]] = []
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
            rows: list[ShortcutRow] = []
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

    def _make_row(self, spec: ShortcutSpec) -> ShortcutRow:
        action_id = spec.action_id
        row = ShortcutRow(
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
