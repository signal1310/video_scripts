"""메인 창 위젯 트리 조립, 단축키 바인딩 및 창 상태 영속화 모듈"""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QByteArray, QObject, Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMenuBar,
    QPlainTextEdit,
    QScrollBar,
    QTabBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.models import VideoEvaluation
from src.core.settings.ui_state import UiLayoutState, UiState
from src.core.workspace_paths import parse_root_dirs
from src.gui.constants import APP_WINDOW_TITLE
from src.gui.menus.menu_callbacks import MenuBarCallbacks
from src.gui.table.column_history import (
    COLUMN_FIT_HISTORY_LABEL,
    COLUMN_PRESET_HISTORY_LABEL,
    COLUMN_RESET_HISTORY_LABEL,
    COLUMN_VISIBILITY_HISTORY_LABEL,
    ColumnHistoryRecorder,
)
from src.gui.table.column_layout_state import (
    apply_column_layouts,
    capture_column_layouts,
    decode_column_layouts,
    encode_column_layouts,
)
from src.gui.table.column_sizing import (
    COLUMN_PRESETS,
    apply_compact_mode,
    auto_fit_visible_columns,
    detect_column_preset,
    hidden_columns_for_preset,
)
from src.gui.table.video_table_view import VideoTableView
from src.gui.widgets import (
    BentoDashboard,
    FilterTuningBar,
    FooterBar,
    HeaderBar,
)
from src.gui.widgets.collapsible_top_area import CollapsibleTopArea, TopAreaSection

# 빈 영역 클릭 시 테이블 선택을 유지해야 하는 대화형 위젯 타입
NON_CLEAR_CLICK_WIDGET_TYPES: tuple[type[QWidget], ...] = (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QMenu,
    QMenuBar,
    QPlainTextEdit,
    QScrollBar,
    QTabBar,
    QTextEdit,
)


@dataclass
class MainWindowWidgets:
    """메인 윈도우 중앙 레이아웃을 구성하는 핵심 위젯 그룹 참조 컨테이너"""

    header_bar: HeaderBar
    dashboard: BentoDashboard
    filter_bar: FilterTuningBar
    table_view: VideoTableView
    footer_bar: FooterBar
    drop_overlay: QFrame
    top_area: CollapsibleTopArea


@dataclass
class MainWindowCallbacks:
    """메인 윈도우 위젯, 단축키 시그널에 바인딩할 콜백 묶음"""

    root_dir_provider: Callable[[], str]
    on_browse_dir: Callable[[], None]
    on_open_explorer: Callable[[], None]
    on_analyze_fast: Callable[[], None]
    on_analyze_full: Callable[[], None]
    on_cancel: Callable[[], None]
    on_open_settings: Callable[[], None]
    on_filter_changed: Callable[[], None]
    on_exceptions_changed: Callable[[], None]
    on_append_exception: Callable[[str], None]
    on_append_exceptions: Callable[[list[str]], None]
    on_remove_exceptions: Callable[[list[str]], None]
    on_selection_changed: Callable[[int], None]
    on_shortcut_analyze: Callable[[], None]
    on_reset_filters: Callable[[], None]
    on_show_logs: Callable[[], None]
    on_export_csv: Callable[[], None]
    on_focus_search: Callable[[], None]
    on_manual_targets_requested: Callable[[list[VideoEvaluation], str | None], object]
    on_manual_targets_cleared: Callable[[], None]
    on_analyze_mode_changed: Callable[[str], None] | None = None
    on_analyze_frame: Callable[[], None] | None = None
    on_open_exception_editor: Callable[[], None] | None = None
    on_add_dir: Callable[[], None] | None = None
    on_toggle_recursive: Callable[[bool], None] | None = None
    can_start_drag: Callable[[], bool] | None = None


_TITLE_SEPARATOR: str = " — "
_TITLE_EXTRA_ROOTS_TEMPLATE: str = " 외 {count}개"
# - 재인코딩 진행 중 창 제목 앞에 붙이는 진행률 표기
ENCODE_TITLE_PREFIX_TEMPLATE: str = "[{percent}%] "
_ENCODE_TITLE_PREFIX_PATTERN = re.compile(r"^\[\d+%\] ")
_BASE_TITLE_PROPERTY: str = "_encodeBaseTitle"


def format_window_title(folder_path: str) -> str:
    """앱 이름과 작업 폴더를 구분자로 잇는 창 제목 반환

    - 여러 작업 폴더면 주 작업 폴더 뒤에 개수를 붙임
    """
    roots = parse_root_dirs(folder_path)
    if not roots:
        return APP_WINDOW_TITLE
    title = f"{APP_WINDOW_TITLE}{_TITLE_SEPARATOR}{roots[0]}"
    if len(roots) > 1:
        title += _TITLE_EXTRA_ROOTS_TEMPLATE.format(count=len(roots) - 1)
    return title


def strip_encode_title_prefix(title: str) -> str:
    """창 제목 앞의 재인코딩 진행률 표기를 뗀 제목"""
    return _ENCODE_TITLE_PREFIX_PATTERN.sub("", title)


def set_window_base_title(window: QWidget, base_title: str) -> None:
    """기본 제목을 기억하고 적용하되, 진행률 표기가 붙어 있으면 그 표기를 유지한 채 바꿈"""
    window.setProperty(_BASE_TITLE_PROPERTY, base_title)
    prefix = _ENCODE_TITLE_PREFIX_PATTERN.match(window.windowTitle())
    window.setWindowTitle(base_title if prefix is None else f"{prefix.group(0)}{base_title}")


def apply_encode_title_prefix(window: QWidget, percent: int | None) -> None:
    """창 제목 앞에 재인코딩 진행률 `[N%] `를 붙이고, None이면 떼어 기본 제목으로 되돌림"""
    stored = window.property(_BASE_TITLE_PROPERTY)
    base = (
        stored
        if isinstance(stored, str) and stored
        else strip_encode_title_prefix(window.windowTitle())
    )
    if percent is None:
        window.setWindowTitle(base)
        return
    window.setWindowTitle(ENCODE_TITLE_PREFIX_TEMPLATE.format(percent=percent) + base)


def build_main_window_widgets(
    window: QMainWindow,
    initial_dir: str,
    callbacks: MainWindowCallbacks,
) -> MainWindowWidgets:
    """HeaderBar, BentoDashboard, FilterTuningBar, VideoTableView, FooterBar를 조립하여 중앙 위젯에 배치"""
    central = QWidget(window)
    window.setCentralWidget(central)
    root_layout = QVBoxLayout(central)
    root_layout.setContentsMargins(16, 14, 16, 14)
    root_layout.setSpacing(12)

    header_bar = HeaderBar(initial_dir, window)
    set_window_base_title(window, format_window_title(initial_dir))
    header_bar.committed_path_changed.connect(
        lambda path: set_window_base_title(window, format_window_title(path))
    )
    header_bar.browse_clicked.connect(callbacks.on_browse_dir)
    if callbacks.on_add_dir is not None:
        header_bar.add_dir_clicked.connect(callbacks.on_add_dir)
    if callbacks.on_toggle_recursive is not None:
        header_bar.recursive_toggled.connect(callbacks.on_toggle_recursive)
    header_bar.open_explorer_clicked.connect(callbacks.on_open_explorer)
    header_bar.analyze_fast_clicked.connect(callbacks.on_analyze_fast)
    header_bar.analyze_full_clicked.connect(callbacks.on_analyze_full)
    if callbacks.on_analyze_frame is not None:
        header_bar.analyze_frame_clicked.connect(callbacks.on_analyze_frame)
    if callbacks.on_analyze_mode_changed is not None:
        header_bar.analyze_mode_changed.connect(callbacks.on_analyze_mode_changed)
    header_bar.cancel_clicked.connect(callbacks.on_cancel)
    dashboard = BentoDashboard(window)

    filter_bar = FilterTuningBar(window)
    filter_bar.filter_changed.connect(callbacks.on_filter_changed)
    filter_bar.reset_filters_requested.connect(callbacks.on_reset_filters)
    filter_bar.exceptions_changed.connect(callbacks.on_exceptions_changed)
    if callbacks.on_open_exception_editor is not None:
        filter_bar.exception_editor_requested.connect(callbacks.on_open_exception_editor)
    top_area = CollapsibleTopArea(
        [
            TopAreaSection("작업 폴더", header_bar),
            TopAreaSection("분석 요약", dashboard),
            TopAreaSection("필터", filter_bar),
        ],
        window,
    )
    header_bar.collapse_toggle_clicked.connect(top_area.toggle)
    top_area.collapsed_changed.connect(
        lambda collapsed: header_bar.set_collapse_button_visible(not collapsed)
    )
    root_layout.addWidget(top_area)

    table_view = VideoTableView(
        root_dir_provider=callbacks.root_dir_provider,
        exceptions_provider=filter_bar.get_exception_tokens,
        can_start_drag_provider=callbacks.can_start_drag,
        parent=window,
    )
    table_view.exception_keyword_added.connect(callbacks.on_append_exception)
    table_view.exception_keywords_added.connect(callbacks.on_append_exceptions)
    table_view.exception_keywords_removed.connect(callbacks.on_remove_exceptions)
    table_view.manual_target_requested.connect(callbacks.on_manual_targets_requested)
    table_view.selection_changed.connect(callbacks.on_selection_changed)
    table_view.analyze_fast_requested.connect(callbacks.on_analyze_fast)
    table_view.analyze_full_requested.connect(callbacks.on_analyze_full)
    if callbacks.on_analyze_frame is not None:
        table_view.empty_overlay.analyze_frame_requested.connect(callbacks.on_analyze_frame)
    if callbacks.on_analyze_mode_changed is not None:
        table_view.analyze_mode_changed.connect(callbacks.on_analyze_mode_changed)
    table_view.reset_filters_requested.connect(callbacks.on_reset_filters)
    root_layout.addWidget(table_view, stretch=1)

    footer_bar = FooterBar(window)
    footer_bar.logs_clicked.connect(callbacks.on_show_logs)
    root_layout.addWidget(footer_bar)

    drop_overlay = QFrame(central)
    drop_overlay.setObjectName("DropOverlay")
    drop_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    drop_overlay.hide()
    drop_layout = QVBoxLayout(drop_overlay)
    drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    drop_layout.setSpacing(6)
    lbl_drop_title = QLabel("작업 폴더 경로 전환", drop_overlay)
    lbl_drop_title.setObjectName("DropOverlayTitle")
    lbl_drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl_drop_desc = QLabel(
        "여기에 폴더를 놓으면 작업 폴더를 전환합니다.\nShift 또는 Ctrl을 누른 채 놓으면 작업 폴더 목록에 추가합니다.",
        drop_overlay,
    )
    lbl_drop_desc.setObjectName("DropOverlayDesc")
    lbl_drop_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
    drop_layout.addWidget(lbl_drop_title)
    drop_layout.addWidget(lbl_drop_desc)

    return MainWindowWidgets(
        header_bar=header_bar,
        dashboard=dashboard,
        filter_bar=filter_bar,
        table_view=table_view,
        footer_bar=footer_bar,
        drop_overlay=drop_overlay,
        top_area=top_area,
    )


def restore_window_ui_state(
    window: QMainWindow,
    layout: UiLayoutState,
    table_view: VideoTableView,
) -> None:
    """저장된 창 지오메트리(Base64), 열 상태(JSON), 상단 영역 접힘 상태, 표 맨 위 위치를 복원하고 형식이 맞지 않으면 기본값 적용"""
    top_area = window.findChild(CollapsibleTopArea)
    if top_area is not None:
        top_area.set_collapsed(layout.top_area_collapsed)
    if layout.window_geometry:
        geom = QByteArray.fromBase64(layout.window_geometry.encode("ascii"))
        if not geom.isEmpty():
            window.restoreGeometry(geom)
    table_view.request_top_restore(layout.table_top_filename, layout.table_top_row)
    if layout.table_header_state:
        layouts = decode_column_layouts(layout.table_header_state)
        if layouts is not None:
            apply_column_layouts(table_view, layouts, reset_sort=False)
            return
        table_view.reset_columns()


def export_window_ui_state(
    window: QMainWindow,
    table_view: VideoTableView,
) -> tuple[str, str]:
    """현재 창 지오메트리(Base64)와 열 상태(JSON 문자열)를 직렬화"""
    geom_b64 = window.saveGeometry().toBase64().data().decode("ascii")
    layouts = capture_column_layouts(table_view)
    return geom_b64, encode_column_layouts(layouts)


def export_top_area_collapsed(window: QMainWindow) -> bool:
    """상단 영역의 현재 접힘 상태를 반환. 상단 영역이 없으면 펼친 상태로 간주"""
    top_area = window.findChild(CollapsibleTopArea)
    return top_area is not None and top_area.collapsed


def extract_dropped_dirs(event: QDragEnterEvent | QDropEvent | None) -> list[str]:
    """드래그/드롭 이벤트 MIME URL에서 유효 로컬 디렉터리 경로 목록을 순서대로 추출"""
    if event is None:
        return []
    mime = event.mimeData()
    if mime is None or not mime.hasUrls():
        return []
    paths: list[str] = []
    for url in mime.urls():
        local_path = url.toLocalFile()
        if url.isLocalFile() and os.path.isdir(local_path):
            paths.append(local_path)
    return paths


def extract_dropped_dir(event: QDragEnterEvent | QDropEvent | None) -> str | None:
    """드래그/드롭 이벤트 MIME URL에서 첫 번째 유효 로컬 디렉터리 경로 추출"""
    dirs = extract_dropped_dirs(event)
    return dirs[0] if dirs else None


def sync_drop_overlay_geometry(window: QMainWindow, drop_overlay: QFrame) -> None:
    """중앙 위젯 크기에 맞춰 드롭 안내 오버레이 영역 동기화"""
    central = window.centralWidget()
    if central is not None:
        drop_overlay.setGeometry(central.rect())


def set_drop_overlay_visible(
    window: QMainWindow, drop_overlay: QFrame, visible: bool
) -> None:
    """드롭 안내 오버레이 표시 여부 및 z-order 갱신"""
    if visible:
        sync_drop_overlay_geometry(window, drop_overlay)
        drop_overlay.raise_()
        drop_overlay.show()
    else:
        drop_overlay.hide()


def is_blank_area_click(
    window: QMainWindow, target: QObject | None, table_view: VideoTableView
) -> bool:
    """창 안에서 대화형 위젯이나 표가 아닌 빈 영역을 눌렀는지 판별"""
    if not window.isVisible() or not isinstance(target, QWidget):
        return False
    if target.window() is not window:
        return False
    curr: QWidget | None = target
    while curr is not None:
        if curr is table_view:
            return False
        if isinstance(curr, NON_CLEAR_CLICK_WIDGET_TYPES):
            return False
        if curr is window:
            break
        curr = curr.parentWidget()
    return True


def handle_drag_enter(
    window: QMainWindow,
    drop_overlay: QFrame,
    is_busy: bool,
    event: QDragEnterEvent | None,
) -> None:
    """유효한 로컬 디렉터리 드래그 진입 시 드롭 허용 및 오버레이 표시"""
    if event is None:
        return
    if event.source() is not None:
        set_drop_overlay_visible(window, drop_overlay, False)
        event.ignore()
        return
    if not is_busy and extract_dropped_dir(event) is not None:
        set_drop_overlay_visible(window, drop_overlay, True)
        event.acceptProposedAction()
        return
    set_drop_overlay_visible(window, drop_overlay, False)
    event.ignore()


def handle_drop_dirs_event(
    window: QMainWindow,
    drop_overlay: QFrame,
    is_busy: bool,
    event: QDropEvent | None,
) -> tuple[list[str], bool]:
    """드롭된 폴더 목록과 추가 모드 여부 반환

    - 추가 모드는 드롭 순간 Shift 또는 Ctrl이 눌려 있을 때 True
    - 작업 중이거나 유효한 폴더가 없으면 빈 목록 반환
    """
    set_drop_overlay_visible(window, drop_overlay, False)
    if event is None:
        return [], False
    dirs = extract_dropped_dirs(event)
    if is_busy or not dirs:
        event.ignore()
        return [], False
    event.acceptProposedAction()
    additive = bool(
        event.modifiers()
        & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)
    )
    return dirs, additive


def _as_void_slot(fn: Callable[[], object]) -> Callable[[], None]:
    def _call() -> None:
        fn()

    return _call


class TableDisplayCoordinator:
    """열 프리셋, 모든 열 크기 맞춤, 좁게 보기를 표와 필터 바, 보기 메뉴, 저장 상태에 함께 반영

    - 표 위젯은 열 폭과 숨김만 담당하고, 되돌리기 기록과 저장은 이 조율자가 맡음
    """

    def __init__(
        self,
        window: QMainWindow,
        table_view: VideoTableView,
        filter_bar: FilterTuningBar,
        ui_state: UiState,
        persist: Callable[[], None],
    ) -> None:
        self._window = window
        self._table_view = table_view
        self._filter_bar = filter_bar
        self._ui = ui_state
        self._persist = persist
        self.sync_from_state()

    def sync_from_state(self) -> None:
        """저장된 좁게 보기 설정을 표와 버튼, 메뉴에 적용하고 현재 숨김 열로 프리셋 라벨을 맞춤"""
        apply_compact_mode(self._table_view, self._ui.compact_table)
        self._filter_bar.set_compact(self._ui.compact_table)
        self.sync_preset()

    def sync_preset(self) -> None:
        """현재 숨김 열에서 프리셋을 다시 감지해 필터 바 라벨과 보기 메뉴 체크를 맞춤

        - 되돌리기, 헤더 우클릭 열 표시 변경, 화질 분석 자동 표시처럼 표 밖에서 숨김이 바뀐 경우 호출
        """
        hidden = self._hidden_columns()
        preset = detect_column_preset(hidden)
        self._filter_bar.set_column_preset(preset)
        self._sync_menu(preset)

    def apply_preset(self, preset_key: str) -> None:
        """열 프리셋의 숨김 열 집합을 표에 적용하고 라벨, 메뉴, 저장 상태를 갱신"""
        hidden = hidden_columns_for_preset(preset_key)

        def _apply() -> None:
            for col in range(self._table_view.table_model.columnCount()):
                self._table_view.setColumnHidden(col, col in hidden)

        self._run_column_command(COLUMN_PRESET_HISTORY_LABEL, _apply)
        self.sync_preset()
        self._persist_display_state()

    def fit_all(self) -> None:
        """보이는 모든 열 폭을 실제 표시 내용과 열 제목에 한 번 맞추고 되돌리기 기록에 남김"""
        compact = self._filter_bar.is_compact()

        def _fit() -> None:
            auto_fit_visible_columns(
                self._table_view.table_model, self._table_view, compact=compact
            )

        self._run_column_command(COLUMN_FIT_HISTORY_LABEL, _fit)
        self._persist_display_state()

    def set_compact(self, enabled: bool) -> None:
        """좁게 보기를 표와 버튼, 메뉴에 반영하고 설정에 저장"""
        apply_compact_mode(self._table_view, enabled)
        self._filter_bar.set_compact(enabled)
        self._ui.compact_table = bool(enabled)
        self.sync_preset()
        self._persist_display_state()

    def set_column_visible(self, col: int, visible: bool) -> None:
        """보기 메뉴에서 바꾼 열 표시를 되돌리기 기록과 함께 적용하고 프리셋 라벨을 다시 계산"""
        self._run_column_command(
            COLUMN_VISIBILITY_HISTORY_LABEL,
            lambda: self._table_view.setColumnHidden(col, not visible),
        )
        self.sync_preset()
        self._persist_display_state()

    def reset_columns(self) -> None:
        """열 순서, 표시, 폭을 기본값으로 되돌리고 프리셋 라벨을 갱신"""
        self._run_column_command(COLUMN_RESET_HISTORY_LABEL, self._table_view.reset_columns)
        self.sync_preset()
        self._persist_display_state()

    def _run_column_command(self, label: str, action: Callable[[], None]) -> None:
        history = getattr(self._window, "column_history", None)
        if isinstance(history, ColumnHistoryRecorder):
            history.run_command(label, action)
            return
        action()

    def _persist_display_state(self) -> None:
        """열 배치를 저장 상태에 먼저 반영한 뒤 설정을 저장해 이전 배치가 남지 않게 함"""
        self._ui.layout.table_header_state = encode_column_layouts(
            capture_column_layouts(self._table_view)
        )
        self._persist()

    def _sync_menu(self, preset: str) -> None:
        menu = getattr(self._window, "menu", None)
        sync = getattr(menu, "sync_table_display", None)
        if callable(sync):
            sync(preset, self._filter_bar.is_compact())

    def _hidden_columns(self) -> set[int]:
        return {
            col
            for col in range(self._table_view.table_model.columnCount())
            if self._table_view.isColumnHidden(col)
        }


def attach_table_display(
    window: QMainWindow,
    table_view: VideoTableView,
    filter_bar: FilterTuningBar,
    ui_state: UiState,
    persist: Callable[[], None],
) -> TableDisplayCoordinator:
    """필터 바의 열 프리셋, 크기 맞춤, 좁게 보기 컨트롤을 표시 조율자에 연결하고 반환"""
    coordinator = TableDisplayCoordinator(window, table_view, filter_bar, ui_state, persist)
    filter_bar.column_preset_selected.connect(coordinator.apply_preset)
    filter_bar.auto_fit_requested.connect(coordinator.fit_all)
    filter_bar.compact_toggled.connect(coordinator.set_compact)
    # - 헤더 우클릭 메뉴의 열 표시 변경은 표에서 직접 일어나므로 편집 종료 신호로 프리셋 라벨을 다시 맞춤
    table_view.filter_header.layout_edit_finished.connect(coordinator.sync_preset)
    menu = getattr(window, "menu", None)
    preset_menu = getattr(menu, "preset_menu", None)
    if isinstance(preset_menu, QMenu):
        preset_menu.aboutToShow.connect(coordinator.sync_preset)
    return coordinator


def build_menu_callbacks(
    win_cb: MainWindowCallbacks,
    *,
    on_select_recent_dir: Callable[[str], None],
    on_clear_recent_dirs: Callable[[], None],
    on_close_window: Callable[[], object],
    on_set_theme_mode: Callable[[str], None],
    on_reset_columns: Callable[[], None],
    on_force_reanalyze: Callable[[], None],
    on_play_selected: Callable[[], None],
    on_preview_selected: Callable[[], None],
    on_reveal_selected: Callable[[], object],
    on_copy_selected_names: Callable[[], None],
    on_copy_selected_paths: Callable[[], None],
    on_add_selected_exception: Callable[[], None],
    on_remove_selected_exception: Callable[[], None],
    on_show_help: Callable[[], None],
    on_clear_analysis_cache: Callable[[], None],
    on_open_tuner: Callable[[], None],
    on_open_settings: Callable[[], None] | None = None,
    on_reset_sort: Callable[[], None] | None = None,
    get_column_visibility: Callable[[], list[tuple[int, str, bool, bool]]] | None = None,
    on_set_column_visible: Callable[[int, bool], None] | None = None,
    on_set_analyze_mode: Callable[[str], None] | None = None,
    on_set_manual_target: Callable[[str | None], object],
    on_clear_manual_targets: Callable[[], None],
    get_selected_evaluations: Callable[[], list[VideoEvaluation]],
    on_open_preview_settings: Callable[[], None] | None = None,
    on_select_column_preset: Callable[[str], None] | None = None,
    on_fit_all_columns: Callable[[], None] | None = None,
    on_set_compact_table: Callable[[bool], None] | None = None,
) -> MenuBarCallbacks:
    """공유 MainWindowCallbacks를 재사용해 MenuBarCallbacks 조립"""
    return MenuBarCallbacks(
        on_browse_dir=win_cb.on_browse_dir,
        on_select_recent_dir=on_select_recent_dir,
        on_clear_recent_dirs=on_clear_recent_dirs,
        on_open_explorer=win_cb.on_open_explorer,
        on_export_csv=win_cb.on_export_csv,
        on_close_window=_as_void_slot(on_close_window),
        on_focus_search=win_cb.on_focus_search,
        on_reset_filters=win_cb.on_reset_filters,
        on_open_exception_editor=win_cb.on_open_exception_editor or (lambda: None),
        on_add_dir=win_cb.on_add_dir,
        on_toggle_recursive=win_cb.on_toggle_recursive,
        on_open_tuner=on_open_tuner,
        on_open_settings=on_open_settings or win_cb.on_open_settings,
        on_set_theme_mode=on_set_theme_mode,
        on_reset_columns=on_reset_columns,
        on_show_logs=win_cb.on_show_logs,
        on_shortcut_analyze=win_cb.on_shortcut_analyze,
        on_analyze_fast=win_cb.on_analyze_fast,
        on_analyze_full=win_cb.on_analyze_full,
        on_force_reanalyze=on_force_reanalyze,
        on_cancel=win_cb.on_cancel,
        on_play_selected=on_play_selected,
        on_preview_selected=on_preview_selected,
        on_reveal_selected=_as_void_slot(on_reveal_selected),
        on_copy_selected_names=on_copy_selected_names,
        on_copy_selected_paths=on_copy_selected_paths,
        on_add_selected_exception=on_add_selected_exception,
        on_remove_selected_exception=on_remove_selected_exception,
        on_show_help=on_show_help,
        on_clear_analysis_cache=on_clear_analysis_cache,
        on_reset_sort=on_reset_sort,
        get_column_visibility=get_column_visibility,
        on_set_column_visible=on_set_column_visible,
        on_set_analyze_mode=on_set_analyze_mode,
        on_set_manual_target=on_set_manual_target,
        on_clear_manual_targets=on_clear_manual_targets,
        get_selected_evaluations=get_selected_evaluations,
        on_open_preview_settings=on_open_preview_settings,
        on_select_column_preset=on_select_column_preset,
        on_fit_all_columns=on_fit_all_columns,
        on_set_compact_table=on_set_compact_table,
        column_presets=dict(COLUMN_PRESETS),
    )
