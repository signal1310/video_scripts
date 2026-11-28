"""메뉴 빌더가 공유하는 액션 생성, 아이콘 등록, 명령 ID 레지스트리 모듈"""

from collections.abc import Callable

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMainWindow, QMenu, QMenuBar


def enable_menu_tooltips(menu: QMenu) -> None:
    """메뉴와 하위 메뉴의 툴팁 표시를 켜, 상태 표시줄이 없는 창에서도 항목 설명이 호버 툴팁으로 보이게 함"""
    menu.setToolTipsVisible(True)
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None:
            enable_menu_tooltips(submenu)


def enable_menu_bar_tooltips(menu_bar: QMenuBar) -> None:
    """메뉴바의 모든 메뉴와 하위 메뉴에 툴팁 표시를 적용"""
    for action in menu_bar.actions():
        menu = action.menu()
        if menu is not None:
            enable_menu_tooltips(menu)


def make_action(
    parent: QMainWindow,
    text: str,
    slot: Callable[[], None],
    *,
    status_tip: str = "",
) -> QAction:
    """텍스트, 상태 안내, 실행 슬롯이 연결된 창 소유 액션 생성"""
    act = QAction(text, parent)
    if status_tip:
        act.setToolTip(status_tip)
        act.setStatusTip(status_tip)
    act.triggered.connect(lambda _checked=False: slot())
    return act


class MenuContext:
    """메뉴별 빌더가 공유하는 창 참조, 아이콘 등록 목록, 단축키 명령 ID별 단일 QAction 레지스트리"""

    def __init__(self, window: QMainWindow) -> None:
        self.window = window
        self.icon_targets: list[tuple[QAction | QMenu, str]] = []
        # - 단축키 명령 ID별 단일 QAction 레지스트리, 컨트롤러가 같은 객체에 단축키를 바인딩해 중복 바인딩을 막음
        self.actions: dict[str, QAction] = {}

    def register_icon(self, target: QAction | QMenu, icon_name: str) -> None:
        """테마 전환 시 색을 다시 입힐 아이콘 대상을 등록, 아이콘 이름이 비면 무시"""
        if icon_name:
            self.icon_targets.append((target, icon_name))

    def create_action(
        self,
        text: str,
        slot: Callable[[], None],
        *,
        action_id: str,
        icon_name: str = "",
        status_tip: str = "",
    ) -> QAction:
        """아이콘과 명령 ID 레지스트리에 등록되는 창 소유 액션 생성"""
        act = make_action(
            self.window,
            text,
            slot,
            status_tip=status_tip,
        )
        self.register_icon(act, icon_name)
        self.actions[action_id] = act
        return act
