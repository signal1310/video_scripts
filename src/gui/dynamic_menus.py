"""메뉴를 열거나 상태가 바뀔 때 항목을 다시 채우는 최근 작업 폴더, 표시할 열 서브메뉴 구성 모듈"""

import os
from collections.abc import Callable

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QWidget

from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import ThemePalette

MENU_ICON_SIZE: int = 14
RECENT_EMPTY_TEXT: str = "(최근 작업 폴더 없음)"
_COLUMNS_EMPTY_TEXT: str = "(열 정보 없음)"
_CURRENT_DIR_PREFIX: str = "✓ "


def fill_columns_menu(
    menu: QMenu,
    parent: QWidget,
    columns: list[tuple[int, str, bool, bool]] | None,
    on_set_visible: Callable[[int, bool], None] | None,
) -> None:
    """표시할 열 서브메뉴를 현재 열 표시 상태로 다시 구성하며, 보이는 열이 하나뿐이면 그 열은 끌 수 없음"""
    menu.clear()
    if columns is None:
        empty_act = QAction(_COLUMNS_EMPTY_TEXT, parent)
        empty_act.setEnabled(False)
        menu.addAction(empty_act)
        return

    visible_count = sum(1 for _col, _title, is_visible, _locked in columns if is_visible)
    for col_idx, title, is_visible, is_locked in columns:
        act = QAction(title, menu)
        act.setCheckable(True)
        act.setChecked(is_visible)
        if is_locked or (is_visible and visible_count <= 1):
            act.setEnabled(False)
        if on_set_visible is not None:
            act.toggled.connect(
                lambda checked, c=col_idx, cb=on_set_visible: cb(c, checked)
            )
        menu.addAction(act)


def fill_recent_dirs_menu(
    menu: QMenu,
    recent_dirs: list[str],
    active_dir: str,
    *,
    palette: ThemePalette,
    busy: bool,
    on_select: Callable[[str], None],
    clear_action: QAction,
) -> None:
    """최근 작업 폴더 서브메뉴를 최신 목록으로 다시 구성하고, 현재 폴더와 작업 중 항목은 비활성화"""
    menu.clear()
    norm_active = (
        os.path.normcase(os.path.abspath(active_dir.strip()))
        if active_dir.strip()
        else ""
    )
    if not recent_dirs:
        empty_act = QAction(RECENT_EMPTY_TEXT, menu)
        empty_act.setEnabled(False)
        menu.addAction(empty_act)
        return

    folder_icon = get_icon("folder", palette=palette, size=MENU_ICON_SIZE)
    for idx, path in enumerate(recent_dirs, start=1):
        norm_item = os.path.normpath(path)
        is_current = bool(
            norm_active
            and os.path.normcase(os.path.abspath(norm_item)) == norm_active
        )
        prefix = _CURRENT_DIR_PREFIX if is_current else f"{idx}. "
        act = QAction(folder_icon, f"{prefix}{norm_item}", menu)
        act.setEnabled(not is_current and not busy)
        act.triggered.connect(
            lambda _checked=False, p=norm_item: on_select(p)
        )
        menu.addAction(act)

    menu.addSeparator()
    clear_action.setEnabled(not busy)
    menu.addAction(clear_action)
