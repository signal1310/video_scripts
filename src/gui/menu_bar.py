"""메인 창 상단 전체 기능 메뉴바(QMenuBar) 조립 및 상태 동기화 모듈"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtGui import QAction, QActionGroup, QKeySequence
from PyQt6.QtWidgets import QMainWindow, QMenu, QMenuBar

from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeManager

_MENU_ICON_SIZE: int = 14
_THEME_ICON_NAMES: dict[str, str] = {
    ThemeManager.MODE_SYSTEM: "monitor",
    ThemeManager.MODE_DARK: "moon",
    ThemeManager.MODE_LIGHT: "sun",
}


@dataclass
class MenuBarCallbacks:
    """전체 기능 메뉴바 액션에 바인딩할 메인 창 콜백 모음"""

    on_browse_dir: Callable[[], None]
    on_select_recent_dir: Callable[[str], None]
    on_clear_recent_dirs: Callable[[], None]
    on_open_explorer: Callable[[], None]
    on_export_csv: Callable[[], None]
    on_close_window: Callable[[], None]
    on_focus_search: Callable[[], None]
    on_reset_filters: Callable[[], None]
    on_open_exception_editor: Callable[[], None]
    on_open_settings: Callable[[], None]
    on_set_theme_mode: Callable[[str], None]
    on_reset_columns: Callable[[], None]
    on_show_logs: Callable[[], None]
    on_shortcut_scan: Callable[[], None]
    on_scan_fast: Callable[[], None]
    on_scan_full: Callable[[], None]
    on_force_rescan: Callable[[], None]
    on_cancel: Callable[[], None]
    on_play_selected: Callable[[], None]
    on_reveal_selected: Callable[[], None]
    on_copy_selected_names: Callable[[], None]
    on_copy_selected_paths: Callable[[], None]
    on_add_selected_exception: Callable[[], None]
    on_remove_selected_exception: Callable[[], None]
    on_real_classify: Callable[[], None]
    on_unclassify_all: Callable[[], None]
    on_show_help: Callable[[], None]


def _make_action(
    parent: QMainWindow,
    text: str,
    slot: Callable[[], None],
    *,
    shortcut: str = "",
    status_tip: str = "",
) -> QAction:
    act = QAction(text, parent)
    if shortcut:
        act.setShortcut(QKeySequence(shortcut))
    if status_tip:
        act.setToolTip(status_tip)
        act.setStatusTip(status_tip)
    act.triggered.connect(slot)
    return act


class MainMenuController:
    """파일·편집·보기·분석·분류·도움말 메뉴바 및 상태 연동 컨트롤러"""

    def __init__(
        self,
        window: QMainWindow,
        callbacks: MenuBarCallbacks,
    ) -> None:
        self._window = window
        self._callbacks = callbacks
        self._palette: ThemePalette = ThemeManager().current_palette
        self._icon_targets: list[tuple[QAction | QMenu, str]] = []
        menu_bar: QMenuBar | None = window.menuBar()
        assert menu_bar is not None
        self.menu_bar: QMenuBar = menu_bar

        # 1. 파일 메뉴
        file_menu = menu_bar.addMenu("파일(&F)")
        assert file_menu is not None
        self.act_open_dir = self._create_action(
            "작업 폴더 열기...",
            callbacks.on_browse_dir,
            icon_name="folder-open",
            shortcut="Ctrl+O",
            status_tip="분석할 작업 비디오 폴더를 선택합니다.",
        )
        file_menu.addAction(self.act_open_dir)

        recent_menu = file_menu.addMenu("최근 작업 폴더")
        assert recent_menu is not None
        self.recent_menu: QMenu = recent_menu
        self._register_icon(self.recent_menu, "folder")

        self.act_open_explorer = self._create_action(
            "탐색기에서 작업 폴더 열기",
            callbacks.on_open_explorer,
            icon_name="folder",
            status_tip="현재 작업 폴더를 파일 탐색기에서 엽니다.",
        )
        file_menu.addAction(self.act_open_explorer)
        file_menu.addSeparator()

        self.act_export_csv = self._create_action(
            "CSV 내보내기...",
            callbacks.on_export_csv,
            icon_name="download",
            shortcut="Ctrl+E",
            status_tip="현재 필터·정렬된 테이블 결과를 CSV 파일로 저장합니다.",
        )
        file_menu.addAction(self.act_export_csv)
        file_menu.addSeparator()

        self.act_exit = self._create_action(
            "종료",
            callbacks.on_close_window,
            icon_name="x",
            shortcut="Ctrl+Q",
            status_tip="애플리케이션을 종료합니다.",
        )
        file_menu.addAction(self.act_exit)

        # 2. 편집 메뉴
        edit_menu = menu_bar.addMenu("편집(&E)")
        assert edit_menu is not None
        self.act_focus_search = self._create_action(
            "검색 포커스",
            callbacks.on_focus_search,
            icon_name="search",
            shortcut="Ctrl+F",
            status_tip="파일명·코덱 검색 입력창으로 포커스를 이동합니다.",
        )
        self.act_reset_filters = self._create_action(
            "필터 초기화",
            callbacks.on_reset_filters,
            icon_name="rotate-ccw",
            status_tip="검색어·화질·표시 대상 필터를 기본값으로 초기화합니다.",
        )
        self.act_exception_editor = self._create_action(
            "예외 규칙 편집기...",
            callbacks.on_open_exception_editor,
            icon_name="sliders",
            shortcut="Ctrl+Shift+E",
            status_tip="여러 줄 예외 규칙 목록과 활성 여부를 편집합니다.",
        )
        self.act_open_settings = self._create_action(
            "환경설정...",
            callbacks.on_open_settings,
            icon_name="settings",
            shortcut="Ctrl+,",
            status_tip="분석 기준, 폴더명, 테마 등 세부 환경설정을 엽니다.",
        )
        edit_menu.addAction(self.act_focus_search)
        edit_menu.addAction(self.act_reset_filters)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_exception_editor)
        edit_menu.addAction(self.act_open_settings)

        # 3. 보기 메뉴
        view_menu = menu_bar.addMenu("보기(&V)")
        assert view_menu is not None
        theme_menu = view_menu.addMenu("테마")
        assert theme_menu is not None
        self.theme_menu: QMenu = theme_menu
        self.theme_group = QActionGroup(window)
        self.theme_group.setExclusive(True)
        self._theme_actions: dict[str, QAction] = {}
        for mode, label in (
            (ThemeManager.MODE_SYSTEM, "시스템 설정 따라가기"),
            (ThemeManager.MODE_DARK, "다크 테마"),
            (ThemeManager.MODE_LIGHT, "라이트 테마"),
        ):
            act = QAction(label, window)
            act.setCheckable(True)
            act.triggered.connect(
                lambda _checked=False, m=mode: callbacks.on_set_theme_mode(m)
            )
            self.theme_group.addAction(act)
            theme_menu.addAction(act)
            self._theme_actions[mode] = act
            self._register_icon(act, _THEME_ICON_NAMES[mode])

        self.act_reset_columns = self._create_action(
            "기본 열 너비·표시 복원",
            callbacks.on_reset_columns,
            icon_name="rotate-ccw",
            status_tip="숨겨진 열을 다시 표시하고 기본 열 너비로 복원합니다.",
        )
        self.act_show_logs = self._create_action(
            "작업 로그 보기",
            callbacks.on_show_logs,
            icon_name="file-text",
            shortcut="Ctrl+L",
            status_tip="분석·분류 작업 로그 뷰어 대화상자를 엽니다.",
        )
        view_menu.addAction(self.act_reset_columns)
        view_menu.addSeparator()
        view_menu.addAction(self.act_show_logs)

        # 4. 분석 메뉴
        analyze_menu = menu_bar.addMenu("분석(&A)")
        assert analyze_menu is not None
        self.act_scan_default = self._create_action(
            "현재 모드로 스캔",
            callbacks.on_shortcut_scan,
            icon_name="scan",
            shortcut="F5",
            status_tip="현재 선택된 스캔 모드(빠른 스캔 또는 정밀 분석)를 실행합니다.",
        )
        self.act_scan_fast = self._create_action(
            "빠른 스캔 (고속)",
            callbacks.on_scan_fast,
            icon_name="zap",
            status_tip="키프레임 측정을 생략하고 해상도·비트레이트를 빠르게 분석합니다.",
        )
        self.act_scan_full = self._create_action(
            "정밀 분석 (키프레임 포함)",
            callbacks.on_scan_full,
            icon_name="scan",
            status_tip="키프레임 간격 실측을 포함한 전체 정밀 분석을 실행합니다.",
        )
        self.act_force_rescan = self._create_action(
            "강제 재스캔 (캐시 무시 정밀 분석)",
            callbacks.on_force_rescan,
            icon_name="refresh-cw",
            shortcut="Shift+F5",
            status_tip="기존 캐시를 비우고 작업 폴더 전체를 다시 정밀 분석합니다.",
        )
        self.act_cancel = self._create_action(
            "진행 중인 작업 중단 (Esc)",
            callbacks.on_cancel,
            icon_name="x-circle",
            status_tip="실행 중인 백그라운드 분석·분류 작업을 중단합니다.",
        )
        self.act_cancel.setEnabled(False)
        analyze_menu.addAction(self.act_scan_default)
        analyze_menu.addAction(self.act_scan_fast)
        analyze_menu.addAction(self.act_scan_full)
        analyze_menu.addAction(self.act_force_rescan)
        analyze_menu.addSeparator()
        analyze_menu.addAction(self.act_cancel)

        # 5. 분류 메뉴
        classify_menu = menu_bar.addMenu("분류(&C)")
        assert classify_menu is not None
        self.act_play_selected = self._create_action(
            "선택 영상 재생",
            callbacks.on_play_selected,
            icon_name="play",
            status_tip="선택된 비디오 파일을 기본 미디어 플레이어로 재생합니다.",
        )
        self.act_reveal_selected = self._create_action(
            "탐색기에서 선택 파일 보기",
            callbacks.on_reveal_selected,
            icon_name="folder",
            status_tip="선택한 비디오 파일의 위치를 파일 탐색기에서 엽니다.",
        )
        self.act_copy_names = self._create_action(
            "선택 파일명 복사",
            callbacks.on_copy_selected_names,
            icon_name="copy",
            status_tip="선택한 행의 파일명을 클립보드에 복사합니다.",
        )
        self.act_copy_paths = self._create_action(
            "선택 전체 경로 복사",
            callbacks.on_copy_selected_paths,
            icon_name="copy",
            shortcut="Ctrl+Shift+C",
            status_tip="선택한 행의 전체 절대 경로를 클립보드에 복사합니다.",
        )
        self.act_add_exception = self._create_action(
            "선택 항목 예외 규칙에 추가",
            callbacks.on_add_selected_exception,
            icon_name="ban",
            status_tip="선택한 파일의 이름 키워드를 예외 규칙에 추가합니다.",
        )
        self.act_remove_exception = self._create_action(
            "선택 항목 예외 규칙에서 제거",
            callbacks.on_remove_selected_exception,
            icon_name="rotate-ccw",
            status_tip="선택한 파일의 이름 키워드를 예외 규칙에서 제거합니다.",
        )
        self.act_real_classify = self._create_action(
            "폴더 분류 이동...",
            callbacks.on_real_classify,
            icon_name="folder",
            status_tip="분류 대상 영상을 목적 하위 폴더로 실제 이동합니다.",
        )
        self.act_unclassify_all = self._create_action(
            "분류 복원",
            callbacks.on_unclassify_all,
            icon_name="rotate-ccw",
            status_tip="하위 폴더로 이동된 영상을 원래 작업 폴더로 복원합니다.",
        )
        classify_menu.addAction(self.act_play_selected)
        classify_menu.addAction(self.act_reveal_selected)
        classify_menu.addAction(self.act_copy_names)
        classify_menu.addAction(self.act_copy_paths)
        classify_menu.addAction(self.act_add_exception)
        classify_menu.addAction(self.act_remove_exception)
        classify_menu.addSeparator()
        classify_menu.addAction(self.act_real_classify)
        classify_menu.addAction(self.act_unclassify_all)

        # 6. 도움말 메뉴
        help_menu = menu_bar.addMenu("도움말(&H)")
        assert help_menu is not None
        self.act_help = self._create_action(
            "단축키 · 예외 조건식 안내",
            callbacks.on_show_help,
            icon_name="help-circle",
            shortcut="F1",
            status_tip="지원 단축키 및 예외 조건식(v.*) 사용법을 확인합니다.",
        )
        help_menu.addAction(self.act_help)

        self._busy: bool = False
        self._has_selection: bool = False
        self._can_classify: bool = False
        self._can_unclassify: bool = False
        self.refresh_icons(self._palette)
        self.sync_theme_mode(ThemeManager().mode)
        self.update_recent_dirs([], "")
        self.update_selection_state(0)
        self.update_action_availability(can_classify=False, can_unclassify=False)

    def _register_icon(self, target: QAction | QMenu, icon_name: str) -> None:
        if icon_name:
            self._icon_targets.append((target, icon_name))

    def _create_action(
        self,
        text: str,
        slot: Callable[[], None],
        *,
        icon_name: str = "",
        shortcut: str = "",
        status_tip: str = "",
    ) -> QAction:
        act = _make_action(
            self._window,
            text,
            slot,
            shortcut=shortcut,
            status_tip=status_tip,
        )
        self._register_icon(act, icon_name)
        return act

    def refresh_icons(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 메뉴 액션·서브메뉴 아이콘 색상을 일괄 갱신"""
        self._palette = palette
        for target, icon_name in self._icon_targets:
            target.setIcon(
                get_icon(icon_name, palette=palette, size=_MENU_ICON_SIZE)
            )
        theme_icon = _THEME_ICON_NAMES.get(ThemeManager().mode, "monitor")
        self.theme_menu.setIcon(
            get_icon(theme_icon, palette=palette, size=_MENU_ICON_SIZE)
        )
        for act in self.recent_menu.actions():
            if not act.isSeparator() and act.text() != "(최근 작업 폴더 없음)":
                icon_key = "x" if act.text() == "최근 목록 비우기" else "folder"
                act.setIcon(
                    get_icon(icon_key, palette=palette, size=_MENU_ICON_SIZE)
                )

    def sync_theme_mode(self, mode: str) -> None:
        """활성 테마 모드에 맞춰 보기 메뉴 라디오 체크 및 서브메뉴 아이콘을 동기화"""
        act = self._theme_actions.get(mode)
        if act is not None:
            act.setChecked(True)
        theme_icon = _THEME_ICON_NAMES.get(mode, "monitor")
        self.theme_menu.setIcon(
            get_icon(theme_icon, palette=self._palette, size=_MENU_ICON_SIZE)
        )

    def update_recent_dirs(self, recent_dirs: list[str], active_dir: str) -> None:
        """최근 작업 폴더 서브메뉴 항목을 최신 목록으로 재구성"""
        self.recent_menu.clear()
        norm_active = (
            os.path.normcase(os.path.abspath(active_dir.strip()))
            if active_dir.strip()
            else ""
        )
        if not recent_dirs:
            empty_act = QAction("(최근 작업 폴더 없음)", self._window)
            empty_act.setEnabled(False)
            self.recent_menu.addAction(empty_act)
            return

        folder_icon = get_icon("folder", palette=self._palette, size=_MENU_ICON_SIZE)
        for idx, path in enumerate(recent_dirs, start=1):
            norm_item = os.path.normpath(path)
            is_current = bool(
                norm_active
                and os.path.normcase(os.path.abspath(norm_item)) == norm_active
            )
            prefix = "✓ " if is_current else f"{idx}. "
            act = QAction(folder_icon, f"{prefix}{norm_item}", self._window)
            act.setEnabled(not is_current and not self._busy)
            act.triggered.connect(
                lambda _checked=False, p=norm_item: self._callbacks.on_select_recent_dir(
                    p
                )
            )
            self.recent_menu.addAction(act)

        self.recent_menu.addSeparator()
        act_clear = QAction(
            get_icon("x", palette=self._palette, size=_MENU_ICON_SIZE),
            "최근 목록 비우기",
            self._window,
        )
        act_clear.setEnabled(not self._busy)
        act_clear.triggered.connect(self._callbacks.on_clear_recent_dirs)
        self.recent_menu.addAction(act_clear)

    def _refresh_enabled_states(self) -> None:
        idle = not self._busy
        for act in (
            self.act_open_dir,
            self.act_exception_editor,
            self.act_scan_default,
            self.act_scan_fast,
            self.act_scan_full,
            self.act_force_rescan,
            self.act_add_exception,
            self.act_remove_exception,
        ):
            act.setEnabled(idle)

        self.recent_menu.setEnabled(idle)
        self.act_cancel.setEnabled(self._busy)
        self.act_real_classify.setEnabled(idle and self._can_classify)
        self.act_unclassify_all.setEnabled(idle and self._can_unclassify)

        sel_ok = self._has_selection
        for act in (
            self.act_play_selected,
            self.act_reveal_selected,
            self.act_copy_names,
            self.act_copy_paths,
        ):
            act.setEnabled(sel_ok)
        self.act_add_exception.setEnabled(idle and sel_ok)
        self.act_remove_exception.setEnabled(idle and sel_ok)

    def set_busy(self, busy: bool) -> None:
        """백그라운드 작업 실행 여부에 맞춰 메뉴 액션을 잠금 또는 해제"""
        self._busy = busy
        self._refresh_enabled_states()

    def update_selection_state(self, selected_count: int) -> None:
        """테이블 선택 행 개수에 따라 선택 행 액션 활성화 상태를 갱신"""
        self._has_selection = selected_count > 0
        self._refresh_enabled_states()

    def update_action_availability(
        self, *, can_classify: bool, can_unclassify: bool
    ) -> None:
        """분류 이동·분류 복원 가능 여부에 따라 메뉴 액션 활성 상태를 갱신"""
        self._can_classify = can_classify
        self._can_unclassify = can_unclassify
        self._refresh_enabled_states()
