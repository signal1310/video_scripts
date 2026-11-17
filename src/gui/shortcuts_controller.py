"""단축키 바인딩, 다단계 되돌리기와 다시 실행, 출력 해상도 간편 지정을 조율하는 GUI 컨트롤러 모듈"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from src.core.resolution_tiers import KEEP_RESOLUTION_KEY, RESOLUTION_TIER_BY_KEY, copy_manual_targets
from src.core.shortcuts import (
    DEFAULT_SHORTCUTS,
    PREVIEW_SHORTCUT_GROUP,
    REDO_SECONDARY_SHORTCUT,
    SHORTCUT_SPECS,
    find_conflict_action,
    normalize_shortcut,
    resolve_shortcuts,
    shifted_symbol_variant,
)
from src.core.undo_history import UndoableSnapshot, UndoHistory
from src.gui.column_layout_state import capture_column_state, restore_column_state
from src.gui.dialog_coordinator import DialogCoordinator
from src.gui.manual_target_menu import (
    MANUAL_TARGET_AUTO_LABEL,
    MANUAL_TARGET_KEEP_LABEL,
    build_manual_target_options,
    fill_manual_target_menu,
)
from src.gui.menu_bar import MainMenuController
from src.gui.session_controller import SessionCoordinator
from src.gui.widgets.collapsible_top_area import CollapsibleTopArea
from src.gui.widgets.filter_bar import FilterTuningBar
from src.gui.widgets.footer_bar import FooterBar
from src.gui.widgets.header_bar import HeaderBar
from src.gui.widgets.video_table_view import VideoTableView

_UNDO_ID: str = "edit.undo"
_REDO_ID: str = "edit.redo"
_PLAY_ID: str = "classify.play_selected"
# - 단일 키(Space)를 쓰는 표 전용 명령. 창 범위로 두면 포커스된 버튼, 체크박스의 Space 입력을 가로챔
_TABLE_SCOPED_ACTION_IDS: frozenset[str] = frozenset({"classify.preview_selected"})
_CLEAR_ALL_ID: str = "manual_target.clear_all"
_MANUAL_TARGET_PREFIX: str = "manual_target."
_NO_SELECTION_TEXT: str = "영상을 먼저 선택해 주세요."
_DISABLED_FALLBACK_TEXT: str = "선택한 영상에는 이 출력 해상도를 지정할 수 없습니다."
_MANUAL_TARGET_HISTORY_PREFIX: str = "출력 해상도 지정"
_SHORTCUTS_CHANGE_HISTORY_LABEL: str = "단축키 변경"
_SHORTCUTS_RESET_HISTORY_LABEL: str = "단축키 초기화"


def _now_ms() -> int:
    """연속 변경 병합 판정에 쓰는 단조 증가 시각(밀리초)"""
    return int(time.monotonic() * 1000)


def manual_target_label(value: str | None) -> str:
    """직접 지정 값의 표시 문구, 자동은 None으로 표현"""
    if value is None:
        return MANUAL_TARGET_AUTO_LABEL
    if value == KEEP_RESOLUTION_KEY:
        return MANUAL_TARGET_KEEP_LABEL
    tier = RESOLUTION_TIER_BY_KEY.get(value)
    return tier.display if tier is not None else value


def manual_target_history_label(value: str | None) -> str:
    """되돌리기 스택에 표시할 출력 해상도 지정 단계 라벨"""
    return f"{_MANUAL_TARGET_HISTORY_PREFIX} ({manual_target_label(value)})"


def shortcuts_history_label(overrides: Mapping[str, str]) -> str:
    """되돌리기 스택에 표시할 단축키 변경 단계 라벨, 사용자 지정이 모두 사라진 경우는 초기화로 표시"""
    return _SHORTCUTS_CHANGE_HISTORY_LABEL if overrides else _SHORTCUTS_RESET_HISTORY_LABEL


def _value_from_manual_action_id(action_id: str) -> str | None:
    """출력 해상도 명령 ID에서 직접 지정 값 복원, 자동 명령은 None"""
    suffix = action_id.removeprefix(_MANUAL_TARGET_PREFIX)
    if suffix == "auto":
        return None
    if suffix == "keep":
        return KEEP_RESOLUTION_KEY
    return suffix


def _is_text_input(widget: QWidget | None) -> bool:
    """편집 가능한 텍스트 입력창인지 판정, 스핀박스 내부 편집칸은 앱 되돌리기 대상으로 둠"""
    if not isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
        return False
    return not isinstance(widget.parentWidget(), QAbstractSpinBox)


@dataclass(frozen=True, slots=True)
class ShortcutHost:
    """컨트롤러가 조작하는 메인 창의 위젯, 세션, 동작 콜백 묶음"""

    window: QMainWindow
    menu: MainMenuController
    session: SessionCoordinator
    header_bar: HeaderBar
    top_area: CollapsibleTopArea
    filter_bar: FilterTuningBar
    footer_bar: FooterBar
    table_view: VideoTableView
    dialogs: DialogCoordinator
    is_busy: Callable[[], bool]
    refresh_after_restore: Callable[[], None]
    set_theme_mode: Callable[[str], None]
    clear_manual_targets: Callable[[], None]
    clear_column_filters: Callable[[], None]


class ShortcutsController:
    """54개 명령의 단축키 바인딩과 앱 히스토리 적용, 출력 해상도 즉시 지정 실행을 담당"""

    def __init__(self, host: ShortcutHost) -> None:
        self._host = host
        self._history = UndoHistory()
        self._is_applying_history: bool = False
        self._effective: dict[str, str] = resolve_shortcuts()
        self._hidden_actions: dict[str, QAction] = {}
        self._sync_history_menu()

    @property
    def effective_shortcuts(self) -> dict[str, str]:
        """현재 적용 중인 명령별 유효 단축키 사본 반환"""
        return dict(self._effective)

    def capture(self, *, columns: bool = False) -> UndoableSnapshot:
        """현재 설정, 예외 규칙, 직접 지정, 화면 설정, 선택 영상 상태를 되돌리기용 스냅샷으로 캡처

        - 열 상태는 columns가 참일 때만 담음, 자동 열 너비 변화가 다른 단계의 비교에 섞이지 않게 함
        """
        ui = self._host.session.state.ui
        return UndoableSnapshot(
            config=self._host.session.state.to_classifier_config(),
            exception_tokens=tuple(ui.exception_tokens),
            disabled_exception_tokens=tuple(ui.disabled_exception_tokens),
            manual_target_tiers=copy_manual_targets(ui.manual_target_tiers),
            selected_filenames=frozenset(self._host.table_view.get_selected_filenames()),
            shortcuts=dict(ui.shortcuts),
            theme_mode=ui.theme_mode,
            analyze_mode=ui.analyze_mode,
            recursive_scan=ui.recursive_scan,
            show_setting_hints=ui.show_setting_hints,
            search=ui.filters.search,
            column_layout=capture_column_state(self._host.table_view) if columns else "",
        )

    def record_change(
        self,
        label: str,
        before: UndoableSnapshot,
        *,
        coalesce_key: str | None = None,
    ) -> None:
        """변경 직후 상태를 캡처해 히스토리에 기록, 히스토리 적용 중의 위젯 신호는 무시

        - 변경 전 스냅샷이 열 상태를 담았으면 변경 후에도 열 상태를 함께 캡처
        """
        if self._is_applying_history:
            return
        after = self.capture(columns=bool(before.column_layout))
        self._history.record(
            label, before, after, coalesce_key=coalesce_key, timestamp_ms=_now_ms()
        )
        self._sync_history_menu()

    def clear_history(self) -> None:
        """작업 폴더 전환 등 이전 상태와 이어지지 않는 시점에 두 스택을 모두 비움"""
        self._history.clear()
        self._sync_history_menu()

    def apply_shortcuts(self, overrides: Mapping[str, str]) -> None:
        """사용자 오버라이드를 해석해 모든 명령의 단축키와 관련 툴팁, 메뉴 표시를 갱신"""
        effective = resolve_shortcuts(overrides)
        self._effective = effective
        for spec in SHORTCUT_SPECS:
            if spec.group_key == PREVIEW_SHORTCUT_GROUP:
                continue
            action = self._action_for(spec.action_id)
            owner, context = self._scope_for(spec.action_id)
            if action not in owner.actions():
                owner.addAction(action)
            action.setShortcutContext(context)
            action.setShortcuts(
                [QKeySequence(key) for key in self._keys_for(spec.action_id, effective)]
            )
        self._host.table_view.set_play_shortcut(effective[_PLAY_ID])
        self._apply_tooltips(effective)
        self._host.menu.set_shortcut_keys(effective)
        self._host.dialogs.sync_shortcuts(effective)
        self._host.dialogs.sync_history_shortcuts(
            self._keys_for(_UNDO_ID, effective), self._keys_for(_REDO_ID, effective)
        )

    def trigger_command_for_key(self, key_text: str) -> bool:
        """다른 창(미리 보기 등)이 받은 키와 같은 단축키의 앱 명령을 실행, 실행했으면 True

        - 활성 창이 이 창이 아니면 창 범위 단축키가 동작하지 않으므로 호출자가 이 경로로 전달
        - 실행할 수 없는 상태(비활성 명령)면 False를 반환해 키 입력이 그대로 처리되게 함
        """
        pressed = normalize_shortcut(key_text)
        if not pressed:
            return False
        for spec in SHORTCUT_SPECS:
            if spec.group_key == PREVIEW_SHORTCUT_GROUP:
                continue
            keys = {normalize_shortcut(key) for key in self._keys_for(spec.action_id, self._effective)}
            if pressed not in keys:
                continue
            action = self._action_for(spec.action_id)
            if not action.isEnabled():
                return False
            action.trigger()
            return True
        return False

    def trigger_undo(self) -> None:
        """텍스트 입력창이면 입력창 되돌리기, 아니면 앱 히스토리를 한 단계 되돌림"""
        if self._delegate_to_text_input(undo=True) or self._host.is_busy():
            return
        entry = self._history.undo()
        if entry is None:
            return
        self._apply_snapshot(entry.before, entry.after)
        self._announce("되돌리기", entry.label)

    def trigger_redo(self) -> None:
        """텍스트 입력창이면 입력창 다시 실행, 아니면 앱 히스토리를 한 단계 다시 실행"""
        if self._delegate_to_text_input(undo=False) or self._host.is_busy():
            return
        entry = self._history.redo()
        if entry is None:
            return
        self._apply_snapshot(entry.after, entry.before)
        self._announce("다시 실행", entry.label)

    def trigger_manual_target(self, value: str | None) -> bool:
        """선택 행에 출력 해상도 직접 지정을 즉시 적용, 비활성이면 사유를 상태 표시줄에 안내"""
        if self._host.is_busy():
            return False
        evals = self._host.table_view.get_selected_evaluations()
        if not evals:
            self._host.footer_bar.set_status_text(_NO_SELECTION_TEXT)
            return False
        option = next(
            (opt for opt in build_manual_target_options(evals) if opt.value == value),
            None,
        )
        if option is None:
            return False
        if not option.enabled:
            self._host.footer_bar.set_status_text(option.tooltip or _DISABLED_FALLBACK_TEXT)
            return False
        applied = self._host.table_view.set_manual_target_for(None, value)
        if applied > 0:
            self._host.footer_bar.set_status_text(
                f"{manual_target_label(value)} 출력 해상도를 {applied}개 영상에 지정했습니다."
            )
        return applied > 0

    def popup_manual_target_menu(self) -> QMenu | None:
        """선택 행 위치에 출력 해상도 지정 팝업 메뉴를 띄우고 메뉴 객체를 반환"""
        if self._host.is_busy():
            return None
        menu = QMenu(self._host.window)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        fill_manual_target_menu(
            menu,
            self._host.table_view.get_selected_evaluations(),
            self.trigger_manual_target,
            on_clear_all=self._host.clear_manual_targets,
            shortcuts=self._effective,
        )
        menu.popup(self._popup_anchor())
        return menu

    def _sync_history_menu(self) -> None:
        self._host.menu.update_undo_redo_state(
            can_undo=self._history.can_undo,
            undo_label=self._history.undo_label,
            can_redo=self._history.can_redo,
            redo_label=self._history.redo_label,
        )

    def _apply_snapshot(self, target: UndoableSnapshot, other: UndoableSnapshot) -> None:
        """대상 스냅샷에서 반대편 스냅샷과 달라진 항목만 상태와 열린 위젯에 반영

        - 같은 항목은 건드리지 않아 그 사이에 기록되지 않은 변경(예: 정렬, 다른 단계의 열 너비)이 덮이지 않음
        - 위젯 갱신 신호가 히스토리를 오염시키지 않도록 가드
        """
        self._is_applying_history = True
        try:
            refreshed = self._apply_data_and_view_state(target, other)
            self._apply_preferences(target, other)
            if refreshed:
                self._refresh_widgets(target)
            if target.column_layout != other.column_layout:
                restore_column_state(self._host.table_view, target.column_layout)
            self._host.session.persist()
        finally:
            self._is_applying_history = False
        self._sync_history_menu()

    def _apply_data_and_view_state(self, target: UndoableSnapshot, other: UndoableSnapshot) -> bool:
        """분류 결과 영향 항목과 분석 모드, 검색어를 상태에 반영하고 위젯 갱신이 필요한지 반환"""
        state = self._host.session.state
        refresh = False
        if not target.same_data(other):
            state.apply_classifier_config(target.config)
            state.ui.exception_tokens = list(target.exception_tokens)
            state.ui.disabled_exception_tokens = list(target.disabled_exception_tokens)
            state.ui.manual_target_tiers = copy_manual_targets(target.manual_target_tiers)
            classifier = self._host.session.classifier
            if classifier is not None:
                classifier.set_config(state.to_classifier_config())
            refresh = True
        if target.analyze_mode != other.analyze_mode:
            state.ui.analyze_mode = target.analyze_mode
            refresh = True
        if target.recursive_scan != other.recursive_scan:
            state.ui.recursive_scan = target.recursive_scan
            classifier = self._host.session.classifier
            if classifier is not None:
                classifier.set_recursive(target.recursive_scan)
            refresh = True
        if target.search != other.search:
            state.ui.filters.search = target.search
            refresh = True
        return refresh

    def _apply_preferences(self, target: UndoableSnapshot, other: UndoableSnapshot) -> None:
        """단축키, 테마, 쉬운 설명 표시 여부를 반영하고 열린 설정 창에 동기화"""
        host = self._host
        state = host.session.state
        preferences_changed = False
        if target.shortcuts != other.shortcuts:
            state.ui.shortcuts = dict(target.shortcuts)
            self.apply_shortcuts(state.ui.shortcuts)
            preferences_changed = True
        if target.theme_mode != other.theme_mode:
            host.set_theme_mode(target.theme_mode)
            preferences_changed = True
        if target.show_setting_hints != other.show_setting_hints:
            state.ui.show_setting_hints = target.show_setting_hints
            preferences_changed = True
        if preferences_changed:
            host.dialogs.sync_preferences(state.ui.shortcuts, state.ui.show_setting_hints)

    def _refresh_widgets(self, target: UndoableSnapshot) -> None:
        """상태 변경 뒤 화면, 분류기, 열린 설정 창을 현재 상태에 맞추고 선택 영상을 복원"""
        host = self._host
        config = host.session.state.to_classifier_config()
        host.refresh_after_restore()
        host.dialogs.sync_config(config)
        host.dialogs.sync_criteria(config)
        host.table_view.select_filenames(set(target.selected_filenames))

    def _delegate_to_text_input(self, *, undo: bool) -> bool:
        """포커스가 텍스트 입력창이면 해당 위젯의 되돌리기 또는 다시 실행을 수행하고 True 반환"""
        focus = QApplication.focusWidget()
        if not (isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit)) and _is_text_input(focus)):
            return False
        if undo:
            focus.undo()
        else:
            focus.redo()
        return True

    def _announce(self, prefix: str, label: str) -> None:
        self._host.footer_bar.set_status_text(f"{prefix}: {label}")

    def _scope_for(self, action_id: str) -> tuple[QWidget, Qt.ShortcutContext]:
        """명령을 붙일 위젯과 활성 범위, 표 전용 명령은 표에 포커스가 있을 때만 활성화해 다른 위젯의 키 입력을 가로채지 않게 함"""
        if action_id in _TABLE_SCOPED_ACTION_IDS:
            return self._host.table_view, Qt.ShortcutContext.WidgetWithChildrenShortcut
        return self._host.window, Qt.ShortcutContext.WindowShortcut

    def _keys_for(self, action_id: str, effective: Mapping[str, str]) -> list[str]:
        """명령 하나에 바인딩할 키 목록, Shift+숫자의 기호 표기를 함께 포함"""
        keys = self._primary_keys_for(action_id, effective)
        variants = [variant for key in keys if (variant := shifted_symbol_variant(key))]
        return [*keys, *variants]

    def _primary_keys_for(self, action_id: str, effective: Mapping[str, str]) -> list[str]:
        """다시 실행 보조 키와 Return 재생의 위젯 처리 규칙을 반영한 기본 키 목록"""
        key = effective.get(action_id, "")
        if (
            action_id == _REDO_ID
            and key == DEFAULT_SHORTCUTS[_REDO_ID]
            and find_conflict_action(effective, _REDO_ID, REDO_SECONDARY_SHORTCUT) is None
        ):
            return [key, REDO_SECONDARY_SHORTCUT]
        if action_id == _PLAY_ID and normalize_shortcut(key) == "Return":
            return []
        return [key] if key else []

    def _action_for(self, action_id: str) -> QAction:
        """메뉴에 있는 명령은 그 QAction을, 없는 명령은 창 소유의 숨김 QAction을 반환"""
        menu_action = self._host.menu.action_for(action_id)
        if menu_action is not None:
            return menu_action
        hidden = self._hidden_actions.get(action_id)
        if hidden is None:
            hidden = QAction(self._host.window)
            hidden.triggered.connect(self._hidden_handler(action_id))
            self._hidden_actions[action_id] = hidden
        return hidden

    def _hidden_handler(self, action_id: str) -> Callable[[], None]:
        """메뉴에 없는 명령의 실행 동작 생성"""
        host = self._host
        if action_id == "file.edit_path":
            return lambda: host.header_bar.begin_path_edit()
        if action_id == "edit.clear_column_filters":
            return host.clear_column_filters
        if action_id == _CLEAR_ALL_ID:
            return host.clear_manual_targets
        if action_id == "manual_target.open_menu":
            return self._open_manual_target_popup
        value = _value_from_manual_action_id(action_id)
        return lambda: self._run_manual_target(value)

    def _open_manual_target_popup(self) -> None:
        self.popup_manual_target_menu()

    def _run_manual_target(self, value: str | None) -> None:
        self.trigger_manual_target(value)

    def _popup_anchor(self) -> QPoint:
        """선택한 첫 행의 아래 왼쪽, 선택이 없으면 테이블 상단 중앙을 전역 좌표로 반환"""
        table = self._host.table_view
        viewport = table.viewport()
        if viewport is None:
            return table.mapToGlobal(QPoint(0, 0))
        selection = table.selectionModel()
        if selection is not None and (rows := selection.selectedRows()):
            return viewport.mapToGlobal(table.visualRect(rows[0]).bottomLeft())
        return viewport.mapToGlobal(QPoint(viewport.width() // 2, 0))

    def _apply_tooltips(self, effective: Mapping[str, str]) -> None:
        host = self._host
        host.header_bar.update_shortcut_tooltips(
            cancel_key=effective["analyze.cancel"],
            collapse_key=effective["view.toggle_top_area"],
            edit_path_key=effective["file.edit_path"],
            add_dir_key=effective["file.add_dir"],
            recursive_key=effective["analyze.toggle_recursive"],
        )
        host.top_area.update_shortcut_tooltip(effective["view.toggle_top_area"])
        host.filter_bar.set_exception_shortcut(effective["edit.exception_editor"])
        host.footer_bar.update_shortcut_tooltips(
            logs_key=effective["view.show_logs"],
            classify_key=effective["classify.real_classify"],
            unclassify_key=effective["classify.unclassify_all"],
        )
