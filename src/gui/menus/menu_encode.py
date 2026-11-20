"""재인코딩 메뉴(재인코딩 시작, 재인코딩 설정) 항목 조립과 시작 항목 상태 적용 모듈"""

from dataclasses import dataclass

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QMenuBar

from src.core.encode.encode_start_state import StartState
from src.gui.menus.menu_base import MenuContext
from src.gui.menus.menu_callbacks import MenuBarCallbacks

ENCODE_MENU_TITLE: str = "재인코딩(&N)"
START_DESCRIPTION: str = "현재 판정 결과로 재인코딩 시작 확인 창을 엽니다."
SETTINGS_DESCRIPTION: str = "환경 설정의 재인코딩 탭을 엽니다."
PROGRESS_TEXT: str = "재인코딩 진행 창 열기"
PROGRESS_DESCRIPTION: str = "재인코딩 진행 창을 엽니다."
PROGRESS_DISABLED_TEXT: str = "재인코딩을 시작한 뒤 열 수 있습니다."


@dataclass(frozen=True)
class EncodeMenu:
    """재인코딩 메뉴가 만든 메뉴 객체와 항목"""

    menu: QMenu
    act_start: QAction
    act_progress: QAction
    act_settings: QAction


def build_encode_menu(
    ctx: MenuContext,
    menu_bar: QMenuBar,
    callbacks: MenuBarCallbacks,
) -> EncodeMenu:
    """재인코딩 메뉴를 만들어 반환. 비활성 사유를 툴팁으로 보이도록 툴팁 표시를 켬"""
    menu = menu_bar.addMenu(ENCODE_MENU_TITLE)
    assert menu is not None
    menu.setToolTipsVisible(True)
    act_start = ctx.create_action(
        "재인코딩 시작...",
        callbacks.on_start_encode or (lambda: None),
        action_id="encode.start",
        icon_name="zap",
        status_tip=START_DESCRIPTION,
    )
    act_progress = ctx.create_action(
        PROGRESS_TEXT,
        callbacks.on_open_encode_progress or (lambda: None),
        action_id="encode.open_progress",
        icon_name="eye",
        status_tip=PROGRESS_DESCRIPTION,
    )
    act_settings = ctx.create_action(
        "재인코딩 설정...",
        callbacks.on_open_encode_settings or (lambda: None),
        action_id="encode.open_settings",
        icon_name="settings",
        status_tip=SETTINGS_DESCRIPTION,
    )
    menu.addAction(act_start)
    menu.addAction(act_progress)
    menu.addAction(act_settings)
    return EncodeMenu(menu=menu, act_start=act_start, act_progress=act_progress, act_settings=act_settings)


def apply_progress_state(action: QAction, has_batch: bool) -> None:
    """진행 창 항목은 배치를 시작한 뒤에만 활성. 비활성 사유는 툴팁과 상태 표시줄에 같은 문구로 보임"""
    action.setEnabled(has_batch)
    text = PROGRESS_DESCRIPTION if has_batch else PROGRESS_DISABLED_TEXT
    action.setToolTip(text)
    action.setStatusTip(text)


def apply_start_state(action: QAction, state: StartState) -> None:
    """시작 항목의 활성 상태와 사유를 반영. 사유는 툴팁과 상태 표시줄에 같은 문구로 보임"""
    action.setEnabled(state.enabled)
    text = state.reason or START_DESCRIPTION
    action.setToolTip(text)
    action.setStatusTip(text)
