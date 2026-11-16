"""보기 메뉴(테마, 표시할 열, 정렬, 로그, 상단 영역) 항목 조립 모듈"""

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import QMenu, QMenuBar

from src.gui.menu_base import MenuContext
from src.gui.menu_callbacks import MenuBarCallbacks
from src.gui.theme import ThemeManager
from src.gui.widgets.collapsible_top_area import toggle_top_area

THEME_ICON_NAMES: dict[str, str] = {
    ThemeManager.MODE_SYSTEM: "monitor",
    ThemeManager.MODE_DARK: "moon",
    ThemeManager.MODE_LIGHT: "sun",
}
THEME_COMMAND_IDS: dict[str, str] = {
    ThemeManager.MODE_SYSTEM: "view.theme_system",
    ThemeManager.MODE_DARK: "view.theme_dark",
    ThemeManager.MODE_LIGHT: "view.theme_light",
}


@dataclass(frozen=True)
class ViewMenu:
    """보기 메뉴가 만든 액션, 테마 라디오 그룹, 표시할 열 서브메뉴"""

    theme_menu: QMenu
    theme_group: QActionGroup
    theme_actions: dict[str, QAction]
    columns_menu: QMenu
    act_reset_sort: QAction
    act_reset_columns: QAction
    act_show_logs: QAction
    act_toggle_top_area: QAction


def build_view_menu(
    ctx: MenuContext,
    menu_bar: QMenuBar,
    callbacks: MenuBarCallbacks,
    on_columns_about_to_show: Callable[[], None],
) -> ViewMenu:
    """보기 메뉴를 만들어 반환하며, 표시할 열 서브메뉴는 열릴 때마다 호출자가 채움"""
    window = ctx.window
    view_menu = menu_bar.addMenu("보기(&V)")
    assert view_menu is not None
    theme_menu = view_menu.addMenu("테마")
    assert theme_menu is not None
    theme_group = QActionGroup(window)
    theme_group.setExclusive(True)
    theme_actions: dict[str, QAction] = {}
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
        theme_group.addAction(act)
        theme_menu.addAction(act)
        theme_actions[mode] = act
        ctx.actions[THEME_COMMAND_IDS[mode]] = act
        ctx.register_icon(act, THEME_ICON_NAMES[mode])

    columns_menu = view_menu.addMenu("표시할 열")
    assert columns_menu is not None
    ctx.register_icon(columns_menu, "eye")
    columns_menu.aboutToShow.connect(on_columns_about_to_show)

    act_reset_sort = ctx.create_action(
        "기본 정렬로 복원",
        callbacks.on_reset_sort or (lambda: None),
        action_id="view.reset_sort",
        icon_name="rotate-ccw",
        status_tip="테이블 정렬 상태를 기본 파일명 자연 정렬로 초기화합니다.",
    )
    act_reset_columns = ctx.create_action(
        "기본 열 너비, 표시 복원",
        callbacks.on_reset_columns,
        action_id="view.reset_columns",
        icon_name="rotate-ccw",
        status_tip="숨겨진 열을 다시 표시하고 기본 열 너비로 복원합니다.",
    )
    act_show_logs = ctx.create_action(
        "작업 로그 보기",
        callbacks.on_show_logs,
        action_id="view.show_logs",
        icon_name="file-text",
        status_tip="분석, 분류 작업 로그 뷰어 대화상자를 엽니다.",
    )
    act_toggle_top_area = ctx.create_action(
        "상단 영역 접기/펼치기",
        lambda: toggle_top_area(window),
        action_id="view.toggle_top_area",
        status_tip="작업 폴더, 분석 요약, 필터 영역을 접거나 펼칩니다.",
    )
    view_menu.addAction(act_toggle_top_area)
    view_menu.addSeparator()
    view_menu.addAction(act_reset_sort)
    view_menu.addAction(act_reset_columns)
    view_menu.addSeparator()
    view_menu.addAction(act_show_logs)
    return ViewMenu(
        theme_menu=theme_menu,
        theme_group=theme_group,
        theme_actions=theme_actions,
        columns_menu=columns_menu,
        act_reset_sort=act_reset_sort,
        act_reset_columns=act_reset_columns,
        act_show_logs=act_show_logs,
        act_toggle_top_area=act_toggle_top_area,
    )
