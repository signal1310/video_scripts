"""선택 영상 조작(미리 보기 설정, 복사), 예외 규칙, 출력 해상도 지정 항목을 편집 메뉴 끝에 조립하는 모듈"""

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu

from src.gui.menus.manual_target_menu import MANUAL_TARGET_MENU_TITLE
from src.gui.menus.menu_base import MenuContext
from src.gui.menus.menu_callbacks import MenuBarCallbacks


@dataclass(frozen=True)
class SelectionActions:
    """선택 영상 조작 항목이 만든 액션과 출력 해상도 지정 서브메뉴(재생, 미리 보기, 위치 열기는 파일 메뉴에 있음)"""

    act_preview_settings: QAction
    act_copy_names: QAction
    act_copy_paths: QAction
    act_add_exception: QAction
    act_remove_exception: QAction
    manual_target_menu: QMenu


def build_selection_actions(
    ctx: MenuContext,
    menu: QMenu,
    callbacks: MenuBarCallbacks,
    on_manual_target_about_to_show: Callable[[], None],
) -> SelectionActions:
    """선택 영상 조작 항목을 주어진 메뉴 끝에 추가하며, 출력 해상도 지정 서브메뉴는 열릴 때마다 호출자가 채움"""
    act_preview_settings = ctx.create_action(
        "미리 보기 설정...",
        callbacks.on_open_preview_settings or (lambda: None),
        action_id="classify.preview_settings",
        icon_name="sliders",
        status_tip="미리 보기 방식, 구간 이동 간격, 정보 표시를 설정합니다.",
    )
    act_copy_names = ctx.create_action(
        "선택 파일명 복사",
        callbacks.on_copy_selected_names,
        action_id="classify.copy_names",
        icon_name="copy",
        status_tip="선택한 행의 파일명을 클립보드에 복사합니다.",
    )
    act_copy_paths = ctx.create_action(
        "선택 전체 경로 복사",
        callbacks.on_copy_selected_paths,
        action_id="classify.copy_paths",
        icon_name="copy",
        status_tip="선택한 행의 전체 절대 경로를 클립보드에 복사합니다.",
    )
    act_add_exception = ctx.create_action(
        "선택 항목 예외 규칙에 추가",
        callbacks.on_add_selected_exception,
        action_id="classify.add_exception",
        icon_name="ban",
        status_tip="선택한 파일의 이름 키워드를 예외 규칙에 추가합니다.",
    )
    act_remove_exception = ctx.create_action(
        "선택 항목 예외 규칙에서 제거",
        callbacks.on_remove_selected_exception,
        action_id="classify.remove_exception",
        icon_name="rotate-ccw",
        status_tip="선택한 파일의 이름 키워드를 예외 규칙에서 제거합니다.",
    )
    menu.addSeparator()
    menu.addAction(act_preview_settings)
    menu.addAction(act_copy_names)
    menu.addAction(act_copy_paths)
    menu.addAction(act_add_exception)
    menu.addAction(act_remove_exception)
    manual_target_menu = menu.addMenu(MANUAL_TARGET_MENU_TITLE)
    assert manual_target_menu is not None
    manual_target_menu.aboutToShow.connect(on_manual_target_about_to_show)
    ctx.register_icon(manual_target_menu, "monitor")
    return SelectionActions(
        act_preview_settings=act_preview_settings,
        act_copy_names=act_copy_names,
        act_copy_paths=act_copy_paths,
        act_add_exception=act_add_exception,
        act_remove_exception=act_remove_exception,
        manual_target_menu=manual_target_menu,
    )
