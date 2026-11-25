"""재인코딩 메뉴(재인코딩 창 열기, 재인코딩 설정, 데이터 정리 및 초기화) 항목 조립과 상태 적용 모듈"""

from dataclasses import dataclass

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QMenuBar

from src.core.encode.encode_start_state import StartState
from src.gui.menus.menu_base import MenuContext
from src.gui.menus.menu_callbacks import MenuBarCallbacks

ENCODE_MENU_TITLE: str = "재인코딩(&N)"
OPEN_WINDOW_TEXT: str = "재인코딩 창 열기"
START_DESCRIPTION: str = "재인코딩 창을 열고, 시작 전 브리핑을 확인한 뒤 재인코딩을 시작합니다."
SETTINGS_DESCRIPTION: str = "환경 설정의 재인코딩 탭을 엽니다."
DATA_CLEANUP_TEXT: str = "데이터 정리 및 초기화..."
DATA_CLEANUP_DESCRIPTION: str = "기록, 캐시, 임시 파일과 화면 배치, 설정을 한 창에서 골라 정리합니다."


@dataclass(frozen=True)
class EncodeMenu:
    """재인코딩 메뉴가 만든 메뉴 객체와 항목"""

    menu: QMenu
    act_start: QAction
    act_settings: QAction
    act_data_cleanup: QAction


def build_encode_menu(
    ctx: MenuContext,
    menu_bar: QMenuBar,
    callbacks: MenuBarCallbacks,
) -> EncodeMenu:
    """재인코딩 메뉴를 만들어 반환. 비활성 사유를 툴팁으로 보이도록 툴팁 표시를 켬

    - 창 열기는 시작 전 요약 탭 브리핑과 진행 중 창 전면 표시를 함께 맡는 단일 항목
    - 정리는 파일 메뉴와 같은 데이터 정리 창을 열어 이 메뉴에서도 바로 닿게 함
    """
    menu = menu_bar.addMenu(ENCODE_MENU_TITLE)
    assert menu is not None
    menu.setToolTipsVisible(True)
    act_start = ctx.create_action(
        OPEN_WINDOW_TEXT,
        callbacks.on_start_encode or (lambda: None),
        action_id="encode.start",
        icon_name="zap",
        status_tip=START_DESCRIPTION,
    )
    act_settings = ctx.create_action(
        "재인코딩 설정...",
        callbacks.on_open_encode_settings or (lambda: None),
        action_id="encode.open_settings",
        icon_name="settings",
        status_tip=SETTINGS_DESCRIPTION,
    )
    act_data_cleanup = ctx.create_action(
        DATA_CLEANUP_TEXT,
        callbacks.on_open_data_cleanup,
        action_id="encode.open_data_cleanup",
        icon_name="trash",
        status_tip=DATA_CLEANUP_DESCRIPTION,
    )
    menu.addAction(act_start)
    menu.addAction(act_settings)
    # - 설정과 되돌릴 수 없는 데이터 정리를 시각적으로 구분
    menu.addSeparator()
    menu.addAction(act_data_cleanup)
    return EncodeMenu(
        menu=menu,
        act_start=act_start,
        act_settings=act_settings,
        act_data_cleanup=act_data_cleanup,
    )


def apply_start_state(action: QAction, state: StartState) -> None:
    """시작 항목의 활성 상태와 사유를 반영. 사유는 툴팁과 상태 표시줄에 같은 문구로 보임"""
    action.setEnabled(state.enabled)
    text = state.reason or START_DESCRIPTION
    action.setToolTip(text)
    action.setStatusTip(text)

