"""파일 메뉴(작업 폴더, 최근 폴더, 내보내기, 캐시, 종료) 항목 조립 모듈"""

from dataclasses import dataclass

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QMenuBar

from src.gui.menu_base import MenuContext
from src.gui.menu_callbacks import MenuBarCallbacks


@dataclass(frozen=True)
class FileMenu:
    """파일 메뉴가 만든 액션과 최근 작업 폴더 서브메뉴"""

    act_open_dir: QAction
    act_add_dir: QAction
    recent_menu: QMenu
    act_clear_recent_dirs: QAction
    act_open_explorer: QAction
    act_export_csv: QAction
    act_clear_analysis_cache: QAction
    act_exit: QAction


def build_file_menu(
    ctx: MenuContext,
    menu_bar: QMenuBar,
    callbacks: MenuBarCallbacks,
) -> FileMenu:
    """파일 메뉴와 최근 작업 폴더 서브메뉴를 만들어 반환"""
    file_menu = menu_bar.addMenu("파일(&F)")
    assert file_menu is not None
    act_open_dir = ctx.create_action(
        "작업 폴더 열기...",
        callbacks.on_browse_dir,
        action_id="file.open_dir",
        icon_name="folder-open",
        status_tip="분석할 작업 비디오 폴더를 선택합니다.",
    )
    file_menu.addAction(act_open_dir)
    act_add_dir = ctx.create_action(
        "작업 폴더 추가...",
        callbacks.on_add_dir or (lambda: None),
        action_id="file.add_dir",
        icon_name="folder-plus",
        status_tip="현재 작업 폴더를 유지한 채 다른 작업 폴더를 추가합니다.",
    )
    file_menu.addAction(act_add_dir)

    recent_menu = file_menu.addMenu("최근 작업 폴더")
    assert recent_menu is not None
    ctx.register_icon(recent_menu, "folder")
    # - 목록 재구성 시 clear()로 지워지지 않도록 창 소유의 영구 액션으로 보관
    act_clear_recent_dirs = ctx.create_action(
        "최근 목록 비우기",
        callbacks.on_clear_recent_dirs,
        action_id="file.clear_recent",
        icon_name="x",
    )

    act_open_explorer = ctx.create_action(
        "탐색기에서 작업 폴더 열기",
        callbacks.on_open_explorer,
        action_id="file.open_explorer",
        icon_name="folder",
        status_tip="현재 작업 폴더를 파일 탐색기에서 엽니다.",
    )
    file_menu.addAction(act_open_explorer)
    file_menu.addSeparator()

    act_export_csv = ctx.create_action(
        "CSV 내보내기...",
        callbacks.on_export_csv,
        action_id="file.export_csv",
        icon_name="download",
        status_tip="현재 필터, 정렬된 테이블 결과를 CSV 파일로 저장합니다.",
    )
    file_menu.addAction(act_export_csv)
    file_menu.addSeparator()

    act_clear_analysis_cache = ctx.create_action(
        "분석 캐시 비우기...",
        callbacks.on_clear_analysis_cache,
        action_id="file.clear_cache",
        icon_name="x-circle",
        status_tip="저장된 분석 결과를 모두 삭제합니다. 다음 분석부터 영상을 다시 측정합니다.",
    )
    file_menu.addAction(act_clear_analysis_cache)
    file_menu.addSeparator()

    act_exit = ctx.create_action(
        "종료",
        callbacks.on_close_window,
        action_id="file.exit",
        icon_name="x",
        status_tip="애플리케이션을 종료합니다.",
    )
    file_menu.addAction(act_exit)
    return FileMenu(
        act_open_dir=act_open_dir,
        act_add_dir=act_add_dir,
        recent_menu=recent_menu,
        act_clear_recent_dirs=act_clear_recent_dirs,
        act_open_explorer=act_open_explorer,
        act_export_csv=act_export_csv,
        act_clear_analysis_cache=act_clear_analysis_cache,
        act_exit=act_exit,
    )
