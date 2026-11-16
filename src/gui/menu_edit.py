"""편집 메뉴(되돌리기, 검색, 예외 규칙, 판별 기준, 환경 설정, 단축키 설정) 항목 조립 모듈"""

from dataclasses import dataclass

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenuBar

from src.gui.menu_base import MenuContext
from src.gui.menu_callbacks import MenuBarCallbacks


@dataclass(frozen=True)
class EditMenu:
    """편집 메뉴가 만든 액션"""

    act_focus_search: QAction
    act_reset_filters: QAction
    act_exception_editor: QAction
    act_open_tuner: QAction
    act_open_settings: QAction
    act_undo: QAction
    act_redo: QAction
    act_open_shortcuts: QAction


def build_edit_menu(
    ctx: MenuContext,
    menu_bar: QMenuBar,
    callbacks: MenuBarCallbacks,
) -> EditMenu:
    """편집 메뉴를 만들어 반환"""
    edit_menu = menu_bar.addMenu("편집(&E)")
    assert edit_menu is not None
    act_focus_search = ctx.create_action(
        "검색 포커스",
        callbacks.on_focus_search,
        action_id="edit.focus_search",
        icon_name="search",
        status_tip="파일명, 코덱 검색 입력창으로 포커스를 이동합니다.",
    )
    act_reset_filters = ctx.create_action(
        "필터 초기화",
        callbacks.on_reset_filters,
        action_id="edit.reset_filters",
        icon_name="rotate-ccw",
        status_tip="검색어 및 모든 열 필터를 기본값으로 초기화합니다.",
    )
    act_exception_editor = ctx.create_action(
        "예외 규칙 편집기...",
        callbacks.on_open_exception_editor,
        action_id="edit.exception_editor",
        icon_name="sliders",
        status_tip="여러 줄 예외 규칙 목록과 활성 여부를 편집합니다.",
    )
    act_open_tuner = ctx.create_action(
        "판별 기준 조정...",
        callbacks.on_open_tuner,
        action_id="edit.open_tuner",
        icon_name="sliders",
        status_tip="과화질, 저화질, 키프레임 및 절약, 분당 절약 기준을 실시간으로 조정합니다.",
    )
    act_open_settings = ctx.create_action(
        "환경 설정...",
        callbacks.on_open_settings,
        action_id="edit.open_settings",
        icon_name="settings",
        status_tip="분석 기준, 폴더명, 테마 등 세부 환경 설정을 엽니다.",
    )
    act_undo = ctx.create_action(
        "되돌리기",
        callbacks.on_undo or (lambda: None),
        action_id="edit.undo",
        icon_name="rotate-ccw",
        status_tip="직전에 변경한 출력 해상도 지정, 예외 규칙, 판별 기준 및 설정을 이전 상태로 되돌립니다.",
    )
    act_redo = ctx.create_action(
        "다시 실행",
        callbacks.on_redo or (lambda: None),
        action_id="edit.redo",
        icon_name="refresh-cw",
        status_tip="되돌린 출력 해상도 지정, 예외 규칙, 판별 기준 및 설정 변경을 다시 적용합니다.",
    )
    edit_menu.addAction(act_undo)
    edit_menu.addAction(act_redo)
    edit_menu.addSeparator()
    edit_menu.addAction(act_focus_search)
    edit_menu.addAction(act_reset_filters)
    edit_menu.addSeparator()
    edit_menu.addAction(act_exception_editor)
    edit_menu.addAction(act_open_tuner)
    act_open_shortcuts = ctx.create_action(
        "단축키 설정...",
        callbacks.on_open_shortcuts or (lambda: None),
        action_id="edit.open_shortcuts",
        icon_name="sliders",
        status_tip="단축키 사용자 지정 탭을 엽니다.",
    )
    edit_menu.addAction(act_open_settings)
    edit_menu.addSeparator()
    edit_menu.addAction(act_open_shortcuts)
    return EditMenu(
        act_focus_search=act_focus_search,
        act_reset_filters=act_reset_filters,
        act_exception_editor=act_exception_editor,
        act_open_tuner=act_open_tuner,
        act_open_settings=act_open_settings,
        act_undo=act_undo,
        act_redo=act_redo,
        act_open_shortcuts=act_open_shortcuts,
    )
