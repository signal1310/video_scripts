"""분석 메뉴(분석 실행, 강제 재분석, 기본 분석 모드, 중단) 항목 조립 모듈"""

from dataclasses import dataclass

from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import QMenu, QMenuBar

from src.core.settings.ui_state import DEFAULT_ANALYZE_MODE, FRAME_ANALYZE_MODE
from src.gui.menus.menu_base import MenuContext
from src.gui.menus.menu_callbacks import MenuBarCallbacks

ANALYZE_MODE_LABELS: tuple[tuple[str, str, str], ...] = (
    (DEFAULT_ANALYZE_MODE, "빠른 분석 (고속)", "zap"),
    ("full", "정밀 분석 (키프레임 포함)", "analyze"),
    (FRAME_ANALYZE_MODE, "화질 분석 (축소 후보 포함)", "film"),
)
ANALYZE_MODE_COMMAND_IDS: dict[str, str] = {
    DEFAULT_ANALYZE_MODE: "analyze.mode_fast",
    "full": "analyze.mode_full",
    FRAME_ANALYZE_MODE: "analyze.mode_frame",
}


@dataclass(frozen=True)
class AnalysisMenu:
    """분석 메뉴가 만든 액션과 기본 분석 모드 라디오 그룹"""

    act_analyze_default: QAction
    act_analyze_fast: QAction
    act_analyze_full: QAction
    act_force_reanalyze: QAction
    act_frame_analysis_selected: QAction
    act_frame_analysis_all: QAction
    act_crop_scan_selected: QAction
    act_crop_scan_all: QAction
    act_cancel: QAction
    analyze_mode_menu: QMenu
    analyze_mode_group: QActionGroup
    analyze_mode_actions: dict[str, QAction]
    act_recursive: QAction


def build_analysis_menu(
    ctx: MenuContext,
    menu_bar: QMenuBar,
    callbacks: MenuBarCallbacks,
) -> AnalysisMenu:
    """분석 메뉴와 기본 분석 모드 서브메뉴를 만들어 반환"""
    analyze_menu = menu_bar.addMenu("분석(&A)")
    assert analyze_menu is not None
    act_analyze_default = ctx.create_action(
        "현재 모드로 분석",
        callbacks.on_shortcut_analyze,
        action_id="analyze.run_default",
        icon_name="analyze",
        status_tip="현재 선택된 분석 모드(빠른 분석, 정밀 분석, 화질 분석)를 실행합니다.",
    )
    act_analyze_fast = ctx.create_action(
        "빠른 분석 (고속)",
        callbacks.on_analyze_fast,
        action_id="analyze.run_fast",
        icon_name="zap",
        status_tip="키프레임 측정을 생략하고 해상도, 비트레이트를 빠르게 분석합니다.",
    )
    act_analyze_full = ctx.create_action(
        "정밀 분석 (키프레임 포함)",
        callbacks.on_analyze_full,
        action_id="analyze.run_full",
        icon_name="analyze",
        status_tip="키프레임 간격 실측을 포함한 전체 정밀 분석을 실행합니다.",
    )
    act_force_reanalyze = ctx.create_action(
        "강제 재분석 (캐시 무시 정밀 분석)",
        callbacks.on_force_reanalyze,
        action_id="analyze.force_reanalyze",
        icon_name="refresh-cw",
        status_tip="기존 캐시를 비우고 작업 폴더 전체를 다시 정밀 분석합니다.",
    )
    act_cancel = ctx.create_action(
        "진행 중인 작업 중단",
        callbacks.on_cancel,
        action_id="analyze.cancel",
        icon_name="x-circle",
        status_tip="실행 중인 백그라운드 분석 작업을 중단합니다.",
    )
    act_cancel.setEnabled(False)
    act_frame_analysis_selected = ctx.create_action(
        "선택 영상 화질 분석",
        callbacks.on_frame_analysis_selected or (lambda: None),
        action_id="analyze.frame_selected",
        icon_name="film",
        status_tip="선택한 영상의 활성 화면과 축소 후보를 화질 분석합니다.",
    )
    act_frame_analysis_all = ctx.create_action(
        "전체 영상 화질 분석",
        callbacks.on_frame_analysis_all or (lambda: None),
        action_id="analyze.frame_all",
        icon_name="analyze",
        status_tip="모든 영상의 활성 화면과 축소 후보를 화질 분석합니다.",
    )
    act_crop_scan_selected = ctx.create_action(
        "선택 영상 여백 감지",
        lambda: None,
        action_id="analyze.crop_selected",
        icon_name="crop",
        status_tip="선택한 영상의 화면 여백을 빠르게 감지합니다.",
    )
    act_crop_scan_all = ctx.create_action(
        "전체 영상 여백 감지",
        lambda: None,
        action_id="analyze.crop_all",
        icon_name="crop",
        status_tip="모든 영상의 화면 여백을 빠르게 감지합니다.",
    )
    analyze_menu.addAction(act_analyze_default)
    analyze_menu.addAction(act_analyze_fast)
    analyze_menu.addAction(act_analyze_full)
    analyze_menu.addAction(act_force_reanalyze)
    analyze_menu.addSeparator()
    analyze_menu.addAction(act_frame_analysis_selected)
    analyze_menu.addAction(act_frame_analysis_all)
    analyze_menu.addAction(act_crop_scan_selected)
    analyze_menu.addAction(act_crop_scan_all)
    analyze_menu.addSeparator()

    analyze_mode_menu = analyze_menu.addMenu("기본 분석 모드")
    assert analyze_mode_menu is not None
    ctx.register_icon(analyze_mode_menu, "sliders")
    analyze_mode_group = QActionGroup(ctx.window)
    analyze_mode_group.setExclusive(True)
    analyze_mode_actions: dict[str, QAction] = {}
    for mode_key, mode_label, icon_key in ANALYZE_MODE_LABELS:
        act_mode = QAction(mode_label, ctx.window)
        act_mode.setCheckable(True)
        act_mode.triggered.connect(
            lambda _checked=False, m=mode_key: (
                callbacks.on_set_analyze_mode(m)
                if callbacks.on_set_analyze_mode is not None
                else None
            )
        )
        analyze_mode_group.addAction(act_mode)
        analyze_mode_menu.addAction(act_mode)
        analyze_mode_actions[mode_key] = act_mode
        ctx.actions[ANALYZE_MODE_COMMAND_IDS[mode_key]] = act_mode
        ctx.register_icon(act_mode, icon_key)

    act_recursive = ctx.create_action(
        "하위 폴더 포함",
        lambda: (
            callbacks.on_toggle_recursive(act_recursive.isChecked())
            if callbacks.on_toggle_recursive is not None
            else None
        ),
        action_id="analyze.toggle_recursive",
        icon_name="folder-tree",
        status_tip="작업 폴더 아래의 하위 폴더 영상까지 함께 분석합니다.",
    )
    act_recursive.setCheckable(True)
    analyze_menu.addAction(act_recursive)

    analyze_menu.addSeparator()
    analyze_menu.addAction(act_cancel)
    return AnalysisMenu(
        act_analyze_default=act_analyze_default,
        act_analyze_fast=act_analyze_fast,
        act_analyze_full=act_analyze_full,
        act_force_reanalyze=act_force_reanalyze,
        act_frame_analysis_selected=act_frame_analysis_selected,
        act_frame_analysis_all=act_frame_analysis_all,
        act_crop_scan_selected=act_crop_scan_selected,
        act_crop_scan_all=act_crop_scan_all,
        act_cancel=act_cancel,
        analyze_mode_menu=analyze_mode_menu,
        analyze_mode_group=analyze_mode_group,
        analyze_mode_actions=analyze_mode_actions,
        act_recursive=act_recursive,
    )
