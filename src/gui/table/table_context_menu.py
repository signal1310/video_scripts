"""비디오 테이블 헤더 및 행 우클릭 컨텍스트 메뉴 조립, 실행 모듈"""

from collections.abc import Callable
from typing import Protocol

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QWidget

from src.core.models import VideoEvaluation
from src.gui.menus.manual_target_menu import (
    MANUAL_TARGET_MENU_TITLE,
    fill_manual_target_menu,
)
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.table.column_filter_popover import ColumnFilterPopover
from src.gui.table.filter_header_view import FilterHeaderView
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_command_actions import copy_ffmpeg_commands
from src.gui.table.table_model import VideoTableModel

_MENU_ICON_SIZE: int = 14
_MANUAL_TARGET_ICON: str = "monitor"
_VR_PROTECT_LABEL: str = "VR 영상으로 보호"
_CROP_EXCLUDE_LABEL: str = "검은 여백 잘라내기 제외"
_COMPARE_LABEL: str = "화질 비교..."
_COMPARE_ICON: str = "compare"
_VR_PROTECT_TIP: str = (
    "검은 띠 감지와 잘라내기에서만 보호하며 축소 후보는 정상 측정합니다."
)
_CROP_EXCLUDE_TIP: str = (
    "검은 여백이 감지되어도 이 영상은 인코딩할 때 검은 여백을 잘라내지 않습니다."
)
RowCompareOpener = Callable[[VideoEvaluation], None]
# - (대상 영상 목록, 검은 여백만 감지 여부)를 받는 화질 분석 실행 함수
RowFrameAnalysisOpener = Callable[[list[VideoEvaluation], bool], None]
_FRAME_ANALYSIS_LABEL: str = "선택 영상 화질 분석"
_FRAME_ANALYSIS_TIP: str = "선택한 영상의 활성 화면과 축소 후보를 화질 분석합니다."
_CROP_SCAN_LABEL: str = "선택 영상 검은 여백 감지"
_CROP_SCAN_TIP: str = "선택한 영상의 검은 여백을 빠르게 감지합니다."
_CROP_ICON: str = "crop"

_row_compare_opener: RowCompareOpener | None = None
_row_frame_analysis_opener: RowFrameAnalysisOpener | None = None


def set_row_compare_opener(opener: RowCompareOpener | None) -> None:
    """행 우클릭 메뉴의 화질 비교 항목이 부를 함수를 등록. 창이 끝날 때 None으로 지움"""
    global _row_compare_opener
    _row_compare_opener = opener


def set_row_frame_analysis_opener(opener: RowFrameAnalysisOpener | None) -> None:
    """행 우클릭 메뉴의 화질 분석 항목이 부를 함수를 등록. 창이 끝날 때 None으로 지움"""
    global _row_frame_analysis_opener
    _row_frame_analysis_opener = opener


class TableContextHost(Protocol):
    """테이블 컨텍스트 메뉴가 참조하는 TableView 공개 인터페이스"""

    filter_header: FilterHeaderView
    table_model: VideoTableModel
    proxy_model: VideoSortFilterProxyModel

    def isColumnHidden(self, column: int, /) -> bool:
        """지정 열 숨김 여부 반환"""
        ...

    def setColumnHidden(self, column: int, hide: bool, /) -> None:
        """지정 열 숨김 상태 전환"""
        ...

    def open_column_filter_popover(
        self, col: int, anchor_global: QPoint | None = None
    ) -> ColumnFilterPopover | None:
        """지정 열의 동적 필터 팝오버 표시"""
        ...

    def reset_table_sort(self) -> None:
        """테이블 기본 정렬 상태 복원"""
        ...

    def reset_columns(self) -> None:
        """테이블 기본 열 구성 복원"""
        ...

    def play_selected(self, evals: list[VideoEvaluation] | None = None) -> int:
        """대상 영상 기본 플레이어 재생"""
        ...

    def reveal_selected(self, target: VideoEvaluation | None = None) -> bool:
        """대상 영상 위치 탐색기 표시"""
        ...

    def copy_selected_names(self, evals: list[VideoEvaluation] | None = None) -> int:
        """대상 영상 파일명 클립보드 복사"""
        ...

    def copy_selected_paths(self, evals: list[VideoEvaluation] | None = None) -> int:
        """대상 영상 전체 경로 클립보드 복사"""
        ...

    def add_selected_to_exceptions(
        self, evals: list[VideoEvaluation] | None = None
    ) -> int:
        """대상 영상 예외 규칙 추가"""
        ...

    def remove_selected_from_exceptions(
        self, evals: list[VideoEvaluation] | None = None
    ) -> int:
        """대상 영상 예외 규칙 제거"""
        ...

    def set_manual_target_for(
        self, evals: list[VideoEvaluation] | None = None, value: str | None = None
    ) -> int:
        """대상 영상의 출력 해상도 직접 지정 변경 요청, None이면 자동 판정으로 복귀"""
        ...

    def viewport(self) -> QWidget | None:
        """테이블 뷰포트 위젯 반환"""
        ...


def add_menu_action(
    menu: QMenu,
    text: str,
    slot: Callable[[], object],
    *,
    icon_name: str = "",
    palette: ThemePalette | None = None,
    enabled: bool = True,
) -> QAction:
    """아이콘, 활성 상태가 적용된 QAction을 생성해 메뉴에 추가"""
    act = (
        QAction(get_icon(icon_name, palette=palette, size=_MENU_ICON_SIZE), text, menu)
        if icon_name and palette is not None
        else QAction(text, menu)
    )
    act.setEnabled(enabled)
    act.triggered.connect(lambda _checked=False: slot())
    menu.addAction(act)
    return act


def show_header_context_menu(view: TableContextHost, pos: QPoint) -> None:
    """테이블 수평 헤더 우클릭 시 열 필터, 열 표시 토글, 정렬 초기화 메뉴 표시"""
    header = view.filter_header
    p = get_active_palette()
    parent_widget = view if isinstance(view, QWidget) else None
    menu = QMenu(parent_widget)
    clicked_col = header.logicalIndexAt(pos)
    if 0 <= clicked_col < len(VideoTableModel.HEADERS):
        col_title = VideoTableModel.MENU_HEADERS[clicked_col]
        add_menu_action(
            menu,
            f"'{col_title}' 열 필터...",
            lambda c=clicked_col: view.open_column_filter_popover(c),
            icon_name="filter",
            palette=p,
        )
        if view.proxy_model.get_column_filter(clicked_col) is not None:
            add_menu_action(
                menu,
                f"'{col_title}' 열 필터 해제",
                lambda c=clicked_col: view.proxy_model.clear_column_filter(c),
                icon_name="x",
                palette=p,
            )
        if view.proxy_model.active_column_filter_count() > 0:
            add_menu_action(
                menu,
                "모든 열 필터 초기화",
                view.proxy_model.clear_all_column_filters,
                icon_name="rotate-ccw",
                palette=p,
            )
        menu.addSeparator()

    visible_count = sum(
        1 for c in range(view.table_model.columnCount()) if not view.isColumnHidden(c)
    )
    for col_idx, title in enumerate(VideoTableModel.MENU_HEADERS):
        act = QAction(title, menu)
        act.setCheckable(True)
        is_visible = not view.isColumnHidden(col_idx)
        act.setChecked(is_visible)
        if col_idx == VideoTableModel.COL_NAME or (is_visible and visible_count <= 1):
            act.setEnabled(False)
        act.toggled.connect(lambda checked, c=col_idx: view.setColumnHidden(c, not checked))
        menu.addAction(act)
    menu.addSeparator()
    add_menu_action(
        menu,
        "기본 정렬로 복원",
        view.reset_table_sort,
        icon_name="rotate-ccw",
        palette=p,
        enabled=bool(view.proxy_model.sort_stack) or header.isSortIndicatorShown(),
    )
    add_menu_action(menu, "기본 열 너비, 표시, 순서 복원", view.reset_columns)
    # 메뉴가 닫힐 때까지 열 구성 변경을 한 번의 편집으로 묶어 되돌리기 기록에 알림
    header.layout_edit_started.emit()
    menu.exec(header.mapToGlobal(pos))
    header.layout_edit_finished.emit()
    menu.deleteLater()


def _open_row_compare(ev: VideoEvaluation) -> None:
    """등록된 화질 비교 열기 함수로 우클릭한 영상 하나를 넘김"""
    if _row_compare_opener is not None:
        _row_compare_opener(ev)


def _open_row_frame_analysis(evals: list[VideoEvaluation], *, crop_only: bool) -> None:
    """등록된 화질 분석 실행 함수로 우클릭한 영상을 넘김. 등록 전이면 아무것도 하지 않음"""
    if _row_frame_analysis_opener is not None:
        _row_frame_analysis_opener(evals, crop_only)


def show_row_context_menu(
    view: TableContextHost,
    pos: QPoint,
    clicked_ev: VideoEvaluation,
    targets: list[VideoEvaluation],
    to_add: list[str],
    to_remove: list[str],
) -> None:
    """테이블 행 우클릭 시 재생, 화질 비교, 화질 분석, 탐색기 열기, 복사, 예외 규칙 토글, 출력 해상도 지정 메뉴 표시"""
    count = len(targets)
    p = get_active_palette()
    parent_widget = view if isinstance(view, QWidget) else None
    menu = QMenu(parent_widget)
    add_menu_action(
        menu,
        "선택 영상 재생" if count == 1 else f"선택한 {count}개 영상 재생",
        lambda: view.play_selected(targets),
        icon_name="play",
        palette=p,
    )
    add_menu_action(
        menu,
        _COMPARE_LABEL,
        lambda: _open_row_compare(clicked_ev),
        icon_name=_COMPARE_ICON,
        palette=p,
    )
    add_menu_action(
        menu,
        _FRAME_ANALYSIS_LABEL,
        lambda: _open_row_frame_analysis(targets, crop_only=False),
        icon_name="film",
        palette=p,
    )
    add_menu_action(
        menu,
        _CROP_SCAN_LABEL,
        lambda: _open_row_frame_analysis(targets, crop_only=True),
        icon_name=_CROP_ICON,
        palette=p,
    )
    add_menu_action(
        menu,
        "탐색기에서 위치 열기",
        lambda: view.reveal_selected(clicked_ev),
        icon_name="folder",
        palette=p,
    )
    menu.addSeparator()
    add_menu_action(
        menu,
        "선택 파일명 복사" if count == 1 else f"선택한 {count}개 파일명 복사",
        lambda: view.copy_selected_names(targets),
        icon_name="copy",
        palette=p,
    )
    add_menu_action(
        menu,
        "선택 전체 경로 복사"
        if count == 1
        else f"선택한 {count}개 전체 경로 복사",
        lambda: view.copy_selected_paths(targets),
        icon_name="copy",
        palette=p,
    )
    copy_commands = add_menu_action(
        menu,
        "FFmpeg 명령 복사" if count == 1 else f"선택한 {count}개 FFmpeg 명령 복사",
        lambda: copy_ffmpeg_commands(view.table_model, targets, parent_widget),
        icon_name="copy",
        palette=p,
    )
    # - 활성 여부는 메모리에 있는 선택 상태만으로 정함. 예정 명령 계산은 파일 존재 확인을 하므로
    #   수천 행을 고른 상태에서 메뉴를 열 때 화면 스레드가 멈추지 않도록 복사 실행 때로 미룸
    # - 복사 경로에는 워커 스레드 배관이 없고 표 모델이 인자 열에서 이미 계산해 둔 계획을 재사용하므로
    #   복사할 때 화면 스레드에서 계산하며, 캐시가 빈 행만 그때 확인함
    copy_commands.setEnabled(count > 0)

    if to_add:
        add_n = len(to_add)
        add_menu_action(
            menu,
            "선택 항목 예외 규칙에 추가"
            if add_n == 1
            else f"선택한 {add_n}개 항목을 예외 규칙에 추가",
            lambda: view.add_selected_to_exceptions(targets),
            icon_name="ban",
            palette=p,
        )
    if to_remove:
        rem_n = len(to_remove)
        add_menu_action(
            menu,
            "선택 항목 예외 규칙에서 제거"
            if rem_n == 1
            else f"선택한 {rem_n}개 항목을 예외 규칙에서 제거",
            lambda: view.remove_selected_from_exceptions(targets),
            icon_name="rotate-ccw",
            palette=p,
        )

    menu.addSeparator()
    manual_target_menu = menu.addMenu(
        get_icon(_MANUAL_TARGET_ICON, palette=p, size=_MENU_ICON_SIZE), MANUAL_TARGET_MENU_TITLE
    )
    assert manual_target_menu is not None
    fill_manual_target_menu(
        manual_target_menu,
        targets,
        lambda value: view.set_manual_target_for(targets, value),
        on_apply_candidates=lambda: view.table_model.apply_frame_analysis_candidates(targets),
    )
    menu.addSeparator()
    vr_act = QAction(_VR_PROTECT_LABEL, menu)
    vr_act.setCheckable(True)
    vr_act.setChecked(bool(targets) and all(view.table_model.is_manual_vr(ev.video.filename) for ev in targets))
    vr_act.setToolTip(_VR_PROTECT_TIP)
    vr_act.setStatusTip(_VR_PROTECT_TIP)
    vr_act.toggled.connect(lambda checked: view.table_model.toggle_manual_vr(targets, checked))
    menu.addAction(vr_act)
    crop_act = QAction(_CROP_EXCLUDE_LABEL, menu)
    crop_act.setCheckable(True)
    crop_act.setChecked(
        bool(targets) and all(view.table_model.is_crop_excluded(ev.video.filename) for ev in targets)
    )
    crop_act.setToolTip(_CROP_EXCLUDE_TIP)
    crop_act.setStatusTip(_CROP_EXCLUDE_TIP)
    crop_act.toggled.connect(lambda checked: view.table_model.toggle_crop_exclusion(targets, checked))
    menu.addAction(crop_act)

    vp = view.viewport()
    if vp is not None:
        menu.exec(vp.mapToGlobal(pos))
    # - 메뉴는 표 뷰를 부모로 가지므로 실행이 끝나면 지워 표 뷰 아래 자식이 쌓이지 않게 함
    menu.deleteLater()
