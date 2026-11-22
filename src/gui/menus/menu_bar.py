"""메인 창 상단 전체 기능 메뉴바(QMenuBar) 조립 및 상태 동기화 모듈"""

from __future__ import annotations

from collections.abc import Mapping

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMainWindow, QMenuBar

from src.core.encode.encode_start_state import StartState
from src.core.settings.ui_state import DEFAULT_ANALYZE_MODE
from src.gui.menus.dynamic_menus import (
    MENU_ICON_SIZE,
    RECENT_EMPTY_TEXT,
    fill_columns_menu,
    fill_recent_dirs_menu,
)
from src.gui.menus.manual_target_menu import fill_manual_target_menu
from src.gui.menus.menu_analysis import build_analysis_menu
from src.gui.menus.menu_base import MenuContext
from src.gui.menus.menu_callbacks import MenuBarCallbacks
from src.gui.menus.menu_edit import build_edit_menu
from src.gui.menus.menu_encode import (
    apply_cleanup_state,
    apply_clear_discards_state,
    apply_progress_state,
    apply_start_state,
    build_encode_menu,
)
from src.gui.menus.menu_file import build_file_menu
from src.gui.menus.menu_selection import build_selection_actions
from src.gui.menus.menu_view import THEME_ICON_NAMES, build_view_menu
from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeManager

ENCODE_STATE_POLL_MS: int = 200
FRAME_ANALYSIS_RUNNING_TEXT: str = "화질 분석이 진행 중입니다."
FRAME_ANALYSIS_BUSY_TEXT: str = "분석이 끝난 뒤 시작할 수 있습니다."
FRAME_ANALYSIS_NO_LIST_TEXT: str = "화질 분석할 영상이 없습니다."
FRAME_ANALYSIS_NO_SELECTION_TEXT: str = "선택한 영상이 없습니다."
FRAME_ANALYSIS_START_TIP: str = "화질 분석을 실행합니다."


def _set_frame_action(action: QAction, enabled: bool, reason: str, description: str) -> None:
    """화질 분석 항목의 활성 상태와 사유(비활성) 또는 설명(활성) 툴팁 설정"""
    action.setEnabled(enabled)
    text = description if enabled else reason
    action.setToolTip(text)
    action.setStatusTip(text)


class MainMenuController:
    """파일, 편집, 보기, 분석, 재인코딩, 도움말 메뉴바 및 상태 연동 컨트롤러

    메뉴별 액션 조립은 각 빌더가 맡고, 이 클래스는 조립 순서와 아이콘, 활성 상태, 동적 서브메뉴 동기화를 담당
    """

    def __init__(
        self,
        window: QMainWindow,
        callbacks: MenuBarCallbacks,
    ) -> None:
        self._window = window
        self._callbacks = callbacks
        self._ctx = MenuContext(window)
        self._palette: ThemePalette = ThemeManager().current_palette
        self._shortcut_keys: dict[str, str] = {}
        # - 인코더 시험이 끝나지 않은 동안 시작 항목 상태를 주기적으로 다시 계산
        self._encode_poll: QTimer = QTimer(window)
        self._encode_poll.setSingleShot(True)
        self._encode_poll.setInterval(ENCODE_STATE_POLL_MS)
        self._encode_poll.timeout.connect(self.refresh_encode_state)
        menu_bar: QMenuBar | None = window.menuBar()
        assert menu_bar is not None
        self.menu_bar: QMenuBar = menu_bar

        file_menu = build_file_menu(self._ctx, menu_bar, callbacks)
        edit_menu = build_edit_menu(self._ctx, menu_bar, callbacks)
        view_menu = build_view_menu(self._ctx, menu_bar, callbacks, self._populate_columns_menu)
        analysis_menu = build_analysis_menu(self._ctx, menu_bar, callbacks)
        encode_menu = build_encode_menu(self._ctx, menu_bar, callbacks)
        encode_menu.menu.aboutToShow.connect(self.refresh_encode_state)
        selection_actions = build_selection_actions(
            self._ctx, edit_menu.menu, callbacks, self._refresh_manual_target_menu
        )
        self._build_help_menu(menu_bar, callbacks)
        self.act_open_dir = file_menu.act_open_dir
        self.act_add_dir = file_menu.act_add_dir
        self.recent_menu = file_menu.recent_menu
        self.act_clear_recent_dirs = file_menu.act_clear_recent_dirs
        self.act_open_explorer = file_menu.act_open_explorer
        self.act_export_csv = file_menu.act_export_csv
        self.act_clear_analysis_cache = file_menu.act_clear_analysis_cache
        self.act_exit = file_menu.act_exit
        self.act_focus_search = edit_menu.act_focus_search
        self.act_reset_filters = edit_menu.act_reset_filters
        self.act_exception_editor = edit_menu.act_exception_editor
        self.act_open_tuner = edit_menu.act_open_tuner
        self.act_open_settings = edit_menu.act_open_settings
        self.act_undo = edit_menu.act_undo
        self.act_redo = edit_menu.act_redo
        self.act_restore_selection = edit_menu.act_restore_selection
        self.act_redo_selection = edit_menu.act_redo_selection
        self.act_open_shortcuts = edit_menu.act_open_shortcuts
        self.theme_menu = view_menu.theme_menu
        self.theme_group = view_menu.theme_group
        self._theme_actions = view_menu.theme_actions
        self.columns_menu = view_menu.columns_menu
        self.act_reset_sort = view_menu.act_reset_sort
        self.act_reset_columns = view_menu.act_reset_columns
        self.act_show_logs = view_menu.act_show_logs
        self.act_toggle_top_area = view_menu.act_toggle_top_area
        self.act_analyze_default = analysis_menu.act_analyze_default
        self.act_analyze_fast = analysis_menu.act_analyze_fast
        self.act_analyze_full = analysis_menu.act_analyze_full
        self.act_force_reanalyze = analysis_menu.act_force_reanalyze
        self.act_frame_selected = analysis_menu.act_frame_analysis_selected
        self.act_frame_all = analysis_menu.act_frame_analysis_all
        self.act_cancel = analysis_menu.act_cancel
        self.analyze_mode_menu = analysis_menu.analyze_mode_menu
        self.act_recursive = analysis_menu.act_recursive
        self.analyze_mode_group = analysis_menu.analyze_mode_group
        self._analyze_mode_actions = analysis_menu.analyze_mode_actions
        self.act_play_selected = file_menu.act_play_selected
        self.act_preview_selected = file_menu.act_preview_selected
        self.act_reveal_selected = file_menu.act_reveal_selected
        self.act_start_encode = encode_menu.act_start
        self.act_open_encode_progress = encode_menu.act_progress
        self.act_open_encode_settings = encode_menu.act_settings
        self.act_encode_cleanup = encode_menu.act_cleanup
        self.act_encode_clear_discards = encode_menu.act_clear_discards
        self.act_copy_names = selection_actions.act_copy_names
        self.act_copy_paths = selection_actions.act_copy_paths
        self.act_add_exception = selection_actions.act_add_exception
        self.act_remove_exception = selection_actions.act_remove_exception
        self.manual_target_menu = selection_actions.manual_target_menu

        self._busy: bool = False
        self._has_selection: bool = False
        self._frame_has_list: bool = False
        self._frame_running: bool = False
        self._can_undo: bool = False
        self._can_redo: bool = False
        self._can_restore_selection: bool = False
        self._can_redo_selection: bool = False
        self.refresh_icons(self._palette)
        self.sync_theme_mode(ThemeManager().mode)
        self.sync_analyze_mode(DEFAULT_ANALYZE_MODE)
        self._populate_columns_menu()
        self.update_recent_dirs([], "")
        self.update_selection_state(0)
        # - 창에 붙은 타이머로 지연 확인: 전역 singleShot은 창이 사라진 뒤에도 실행되어 슬롯 예외로 프로세스가 중단됨
        self._encode_poll.start(0)

    def _build_help_menu(
        self, menu_bar: QMenuBar, callbacks: MenuBarCallbacks
    ) -> None:
        help_menu = menu_bar.addMenu("도움말(&H)")
        assert help_menu is not None
        self.act_help = self._ctx.create_action(
            "사용 가이드...",
            callbacks.on_show_help,
            action_id="help.open_guide",
            icon_name="help-circle",
            status_tip="프로그램 안내, 판정 원리, 예외 조건식 및 단축키 가이드를 엽니다.",
        )
        help_menu.addAction(self.act_help)

    def refresh_icons(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 메뉴 액션, 서브메뉴 아이콘 색상을 일괄 갱신"""
        self._palette = palette
        for target, icon_name in self._ctx.icon_targets:
            target.setIcon(
                get_icon(icon_name, palette=palette, size=MENU_ICON_SIZE)
            )
        theme_icon = THEME_ICON_NAMES.get(ThemeManager().mode, "monitor")
        self.theme_menu.setIcon(
            get_icon(theme_icon, palette=palette, size=MENU_ICON_SIZE)
        )
        for act in self.recent_menu.actions():
            if not act.isSeparator() and act.text() != RECENT_EMPTY_TEXT:
                icon_key = "x" if act.text() == "최근 목록 비우기" else "folder"
                act.setIcon(
                    get_icon(icon_key, palette=palette, size=MENU_ICON_SIZE)
                )

    def sync_theme_mode(self, mode: str) -> None:
        """활성 테마 모드에 맞춰 보기 메뉴 라디오 체크 및 서브메뉴 아이콘을 동기화"""
        act = self._theme_actions.get(mode)
        if act is not None:
            act.setChecked(True)
        theme_icon = THEME_ICON_NAMES.get(mode, "monitor")
        self.theme_menu.setIcon(
            get_icon(theme_icon, palette=self._palette, size=MENU_ICON_SIZE)
        )

    def sync_analyze_mode(self, mode: str) -> None:
        """기본 스캔 모드에 맞춰 분석 메뉴 라디오 체크 상태를 동기화"""
        target = "full" if mode == "full" else DEFAULT_ANALYZE_MODE
        act = self._analyze_mode_actions.get(target)
        if act is not None:
            act.setChecked(True)

    def sync_recursive(self, enabled: bool) -> None:
        """하위 폴더 포함 메뉴 항목의 체크 상태를 설정 값과 맞춤"""
        self.act_recursive.setChecked(bool(enabled))

    def _populate_columns_menu(self) -> None:
        getter = self._callbacks.get_column_visibility
        fill_columns_menu(
            self.columns_menu,
            self._window,
            getter() if getter is not None else None,
            self._callbacks.on_set_column_visible,
        )

    def update_recent_dirs(self, recent_dirs: list[str], active_dir: str) -> None:
        """최근 작업 폴더 서브메뉴 항목을 최신 목록으로 재구성"""
        fill_recent_dirs_menu(
            self.recent_menu,
            recent_dirs,
            active_dir,
            palette=self._palette,
            busy=self._busy,
            on_select=self._callbacks.on_select_recent_dir,
            clear_action=self.act_clear_recent_dirs,
        )

    def _refresh_enabled_states(self) -> None:
        idle = not self._busy
        for act in (
            self.act_open_dir,
            self.act_add_dir,
            self.act_clear_analysis_cache,
            self.act_exception_editor,
            self.act_analyze_default,
            self.act_analyze_fast,
            self.act_analyze_full,
            self.act_force_reanalyze,
            self.act_recursive,
            self.act_add_exception,
            self.act_remove_exception,
        ):
            act.setEnabled(idle)

        self.recent_menu.setEnabled(idle)
        self.analyze_mode_menu.setEnabled(idle)
        self.act_cancel.setEnabled(self._busy)

        sel_ok = self._has_selection
        for act in (
            self.act_play_selected,
            self.act_preview_selected,
            self.act_reveal_selected,
            self.act_copy_names,
            self.act_copy_paths,
        ):
            act.setEnabled(sel_ok)
        self.act_add_exception.setEnabled(idle and sel_ok)
        self.act_remove_exception.setEnabled(idle and sel_ok)
        self.manual_target_menu.setEnabled(idle)
        self.act_undo.setEnabled(idle and self._can_undo)
        self.act_redo.setEnabled(idle and self._can_redo)
        self.act_restore_selection.setEnabled(idle and self._can_restore_selection)
        self.act_redo_selection.setEnabled(idle and self._can_redo_selection)
        self._apply_frame_analysis_state()

    def _apply_frame_analysis_state(self) -> None:
        """화질 분석 메뉴 항목의 활성 여부와 비활성 사유를 반영"""
        idle = not self._busy and not self._frame_running
        if self._frame_running:
            block_reason = FRAME_ANALYSIS_RUNNING_TEXT
        elif self._busy:
            block_reason = FRAME_ANALYSIS_BUSY_TEXT
        else:
            block_reason = ""
        selected_block = block_reason or (
            FRAME_ANALYSIS_NO_LIST_TEXT if not self._frame_has_list else FRAME_ANALYSIS_NO_SELECTION_TEXT
        )
        _set_frame_action(
            self.act_frame_selected,
            idle and self._frame_has_list and self._has_selection,
            selected_block,
            FRAME_ANALYSIS_START_TIP,
        )
        _set_frame_action(
            self.act_frame_all,
            idle and self._frame_has_list,
            block_reason or FRAME_ANALYSIS_NO_LIST_TEXT,
            FRAME_ANALYSIS_START_TIP,
        )

    def sync_frame_analysis_state(
        self, *, has_list: bool, has_selection: bool, running: bool
    ) -> None:
        """화질 분석 메뉴 항목을 현재 대상, 선택, 실행 상태에 맞춤"""
        self._frame_has_list = has_list
        self._has_selection = has_selection
        self._frame_running = running
        self._apply_frame_analysis_state()

    def _refresh_manual_target_menu(self) -> None:
        """하위 메뉴를 열 때 현재 선택 행 기준으로 직접 지정 항목의 활성, 체크 상태를 다시 계산"""
        fill_manual_target_menu(
            self.manual_target_menu,
            self._callbacks.get_selected_evaluations(),
            self._callbacks.on_set_manual_target,
            on_clear_all=self._callbacks.on_clear_manual_targets,
            shortcuts=self._shortcut_keys,
        )

    def set_busy(self, busy: bool) -> None:
        """백그라운드 작업 실행 여부에 맞춰 메뉴 액션을 잠금 또는 해제"""
        self._busy = busy
        self._refresh_enabled_states()
        self.refresh_encode_state()

    def refresh_encode_state(self) -> None:
        """재인코딩 시작 항목의 활성 상태와 사유를 다시 계산하고, 인코더 시험 중이면 잠시 뒤 다시 계산"""
        has_batch = self._callbacks.has_encode_batch
        if has_batch is not None:
            apply_progress_state(self.act_open_encode_progress, has_batch())
        block_reason = self._callbacks.get_encode_cleanup_block
        if block_reason is not None:
            apply_cleanup_state(self.act_encode_cleanup, block_reason())
        clear_block = self._callbacks.get_encode_clear_discards_block
        if clear_block is not None:
            apply_clear_discards_state(self.act_encode_clear_discards, clear_block())
        getter = self._callbacks.get_encode_state
        if getter is None:
            return
        state: StartState = getter()
        apply_start_state(self.act_start_encode, state)
        if state.pending:
            self._encode_poll.start()

    def update_selection_state(self, selected_count: int) -> None:
        """테이블 선택 행 개수에 따라 선택 행 액션 활성화 상태를 갱신"""
        self._has_selection = selected_count > 0
        self._refresh_enabled_states()

    def update_undo_redo_state(
        self,
        *,
        can_undo: bool,
        undo_label: str | None,
        can_redo: bool,
        redo_label: str | None,
    ) -> None:
        """되돌리기, 다시 실행 가능 여부와 다음 단계 라벨을 메뉴 항목 문구와 활성 상태에 반영"""
        self._can_undo = can_undo
        self._can_redo = can_redo
        self.act_undo.setText(f"되돌리기 ({undo_label})" if undo_label else "되돌리기")
        self.act_redo.setText(f"다시 실행 ({redo_label})" if redo_label else "다시 실행")
        self._refresh_enabled_states()

    def update_selection_history_state(self, can_restore: bool, can_redo: bool) -> None:
        """이전 다중 선택 복원 및 선택 복원 되돌리기 가능 여부에 따라 메뉴 항목 활성 상태를 갱신"""
        self._can_restore_selection = bool(can_restore)
        self._can_redo_selection = bool(can_redo)
        self._refresh_enabled_states()

    def set_shortcut_keys(self, keys: Mapping[str, str]) -> None:
        """하위 메뉴 표시용 단축키 문자열 사전을 갱신, 실제 바인딩은 컨트롤러가 담당"""
        self._shortcut_keys = dict(keys)

    def action_for(self, action_id: str) -> QAction | None:
        """단축키 명령 ID에 해당하는 메뉴 액션 반환, 메뉴에 없는 명령은 None"""
        return self._ctx.actions.get(action_id)
