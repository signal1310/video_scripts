"""분류 메뉴(선택 영상 조작, 예외 규칙, 출력 해상도 지정, 분류 이동과 복원) 항목 조립 모듈"""

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QMenuBar

from src.gui.manual_target_menu import MANUAL_TARGET_MENU_TITLE
from src.gui.menu_base import MenuContext
from src.gui.menu_callbacks import MenuBarCallbacks


@dataclass(frozen=True)
class ClassifyMenu:
    """분류 메뉴가 만든 액션과 출력 해상도 지정 서브메뉴"""

    act_play_selected: QAction
    act_reveal_selected: QAction
    act_copy_names: QAction
    act_copy_paths: QAction
    act_add_exception: QAction
    act_remove_exception: QAction
    act_real_classify: QAction
    act_unclassify_all: QAction
    manual_target_menu: QMenu


def build_classify_menu(
    ctx: MenuContext,
    menu_bar: QMenuBar,
    callbacks: MenuBarCallbacks,
    on_manual_target_about_to_show: Callable[[], None],
) -> ClassifyMenu:
    """분류 메뉴를 만들어 반환하며, 출력 해상도 지정 서브메뉴는 열릴 때마다 호출자가 채움"""
    classify_menu = menu_bar.addMenu("분류(&C)")
    assert classify_menu is not None
    act_play_selected = ctx.create_action(
        "선택 영상 재생",
        callbacks.on_play_selected,
        action_id="classify.play_selected",
        icon_name="play",
        status_tip="선택된 비디오 파일을 기본 미디어 플레이어로 재생합니다.",
    )
    act_reveal_selected = ctx.create_action(
        "탐색기에서 위치 열기",
        callbacks.on_reveal_selected,
        action_id="classify.reveal_selected",
        icon_name="folder",
        status_tip="선택한 비디오 파일의 위치를 파일 탐색기에서 엽니다.",
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
    act_real_classify = ctx.create_action(
        "분류 폴더로 이동...",
        callbacks.on_real_classify,
        action_id="classify.real_classify",
        icon_name="folder",
        status_tip="분류 대상 영상을 목적 하위 폴더로 실제 이동합니다.",
    )
    act_unclassify_all = ctx.create_action(
        "작업 폴더로 복원",
        callbacks.on_unclassify_all,
        action_id="classify.unclassify_all",
        icon_name="rotate-ccw",
        status_tip="하위 폴더로 이동된 영상을 원래 작업 폴더로 복원합니다.",
    )
    classify_menu.addAction(act_play_selected)
    classify_menu.addAction(act_reveal_selected)
    classify_menu.addAction(act_copy_names)
    classify_menu.addAction(act_copy_paths)
    classify_menu.addAction(act_add_exception)
    classify_menu.addAction(act_remove_exception)
    manual_target_menu = classify_menu.addMenu(MANUAL_TARGET_MENU_TITLE)
    assert manual_target_menu is not None
    manual_target_menu.aboutToShow.connect(on_manual_target_about_to_show)
    ctx.register_icon(manual_target_menu, "monitor")
    classify_menu.addSeparator()
    classify_menu.addAction(act_real_classify)
    classify_menu.addAction(act_unclassify_all)
    return ClassifyMenu(
        act_play_selected=act_play_selected,
        act_reveal_selected=act_reveal_selected,
        act_copy_names=act_copy_names,
        act_copy_paths=act_copy_paths,
        act_add_exception=act_add_exception,
        act_remove_exception=act_remove_exception,
        act_real_classify=act_real_classify,
        act_unclassify_all=act_unclassify_all,
        manual_target_menu=manual_target_menu,
    )
