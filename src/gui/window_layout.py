"""메인 창 위젯 트리 조립, 단축키 바인딩 및 창 상태 영속화 모듈"""

import os
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QByteArray, Qt
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

from src.gui.menu_bar import MenuBarCallbacks
from src.gui.widgets import (
    BentoDashboard,
    FilterTuningBar,
    FooterBar,
    HeaderBar,
    VideoTableView,
)

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
    on_unclassify_all: Callable[[], None]
    on_real_classify: Callable[[], None]
    on_show_logs: Callable[[], None]
    on_export_csv: Callable[[], None]
    on_focus_search: Callable[[], None]
    on_analyze_mode_changed: Callable[[str], None] | None = None
    on_open_exception_editor: Callable[[], None] | None = None


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
    header_bar.browse_clicked.connect(callbacks.on_browse_dir)
    header_bar.open_explorer_clicked.connect(callbacks.on_open_explorer)
    header_bar.analyze_fast_clicked.connect(callbacks.on_analyze_fast)
    header_bar.analyze_full_clicked.connect(callbacks.on_analyze_full)
    if callbacks.on_analyze_mode_changed is not None:
        header_bar.analyze_mode_changed.connect(callbacks.on_analyze_mode_changed)
    header_bar.cancel_clicked.connect(callbacks.on_cancel)
    header_bar.settings_clicked.connect(callbacks.on_open_settings)
    root_layout.addWidget(header_bar)

    dashboard = BentoDashboard(window)
    root_layout.addWidget(dashboard)

    filter_bar = FilterTuningBar(window)
    filter_bar.filter_changed.connect(callbacks.on_filter_changed)
    filter_bar.reset_filters_requested.connect(callbacks.on_reset_filters)
    filter_bar.exceptions_changed.connect(callbacks.on_exceptions_changed)
    if callbacks.on_open_exception_editor is not None:
        filter_bar.exception_editor_requested.connect(callbacks.on_open_exception_editor)
    root_layout.addWidget(filter_bar)

    table_view = VideoTableView(
        root_dir_provider=callbacks.root_dir_provider,
        exceptions_provider=filter_bar.get_exception_tokens,
        parent=window,
    )
    table_view.exception_keyword_added.connect(callbacks.on_append_exception)
    table_view.exception_keywords_added.connect(callbacks.on_append_exceptions)
    table_view.exception_keywords_removed.connect(callbacks.on_remove_exceptions)
    table_view.selection_changed.connect(callbacks.on_selection_changed)
    table_view.analyze_fast_requested.connect(callbacks.on_analyze_fast)
    table_view.analyze_full_requested.connect(callbacks.on_analyze_full)
    if callbacks.on_analyze_mode_changed is not None:
        table_view.analyze_mode_changed.connect(callbacks.on_analyze_mode_changed)
    table_view.reset_filters_requested.connect(callbacks.on_reset_filters)
    root_layout.addWidget(table_view, stretch=1)

    footer_bar = FooterBar(window)
    footer_bar.unclassify_all_clicked.connect(callbacks.on_unclassify_all)
    footer_bar.real_classify_clicked.connect(callbacks.on_real_classify)
    footer_bar.logs_clicked.connect(callbacks.on_show_logs)
    footer_bar.export_csv_clicked.connect(callbacks.on_export_csv)
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
        "여기에 폴더를 놓으면 현재 작업 폴더를 전환합니다.", drop_overlay
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
    )


_HEADER_STATE_VERSION: str = "v1"


def restore_window_ui_state(
    window: QMainWindow,
    window_geometry_b64: str,
    table_header_b64: str,
    table_view: VideoTableView,
) -> None:
    """저장된 Base64 인코딩 창 지오메트리 및 테이블 열 헤더 상태를 복원"""
    if window_geometry_b64:
        geom = QByteArray.fromBase64(window_geometry_b64.encode("ascii"))
        if not geom.isEmpty():
            window.restoreGeometry(geom)
    if table_header_b64:
        header = table_view.horizontalHeader()
        expected_prefix = (
            f"{_HEADER_STATE_VERSION}:{header.count()}:" if header is not None else ""
        )
        if header is not None and table_header_b64.startswith(expected_prefix):
            hdr_state = QByteArray.fromBase64(
                table_header_b64[len(expected_prefix) :].encode("ascii")
            )
            if not hdr_state.isEmpty() and header.restoreState(hdr_state):
                # 저장된 상태가 열 이동, 클릭, 정렬 화살표를 덮어쓰지 않도록 동작 정책 재적용
                header.setSectionsMovable(True)
                header.setSectionsClickable(True)
                table_view.reset_table_sort()
                table_view.sync_name_column_user_resize_from_current()
                table_view.keep_restored_column_widths()
                return
        table_view.reset_columns()


def export_window_ui_state(
    window: QMainWindow,
    table_view: VideoTableView,
) -> tuple[str, str]:
    """현재 창 지오메트리 및 테이블 열 헤더 상태를 Base64 문자열 쌍으로 직렬화"""
    geom_b64 = window.saveGeometry().toBase64().data().decode("ascii")
    header = table_view.horizontalHeader()
    if header is None:
        return geom_b64, ""
    raw_b64 = header.saveState().toBase64().data().decode("ascii")
    return geom_b64, f"{_HEADER_STATE_VERSION}:{header.count()}:{raw_b64}"


def extract_dropped_dir(event: QDragEnterEvent | QDropEvent | None) -> str | None:
    """드래그/드롭 이벤트 MIME URL에서 첫 번째 유효 로컬 디렉터리 경로 추출"""
    if event is None:
        return None
    mime = event.mimeData()
    if mime is not None and mime.hasUrls():
        for url in mime.urls():
            local_path = url.toLocalFile()
            if url.isLocalFile() and os.path.isdir(local_path):
                return local_path
    return None


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


def handle_drag_enter(
    window: QMainWindow,
    drop_overlay: QFrame,
    is_busy: bool,
    event: QDragEnterEvent | None,
) -> None:
    """유효한 로컬 디렉터리 드래그 진입 시 드롭 허용 및 오버레이 표시"""
    if event is None:
        return
    if not is_busy and extract_dropped_dir(event) is not None:
        set_drop_overlay_visible(window, drop_overlay, True)
        event.acceptProposedAction()
        return
    set_drop_overlay_visible(window, drop_overlay, False)
    event.ignore()


def handle_drop_event(
    window: QMainWindow,
    drop_overlay: QFrame,
    is_busy: bool,
    event: QDropEvent | None,
) -> str | None:
    """드롭 이벤트 처리 후 유효 로컬 디렉터리 경로 반환(미허용 시 None)"""
    set_drop_overlay_visible(window, drop_overlay, False)
    if event is None:
        return None
    if not is_busy:
        local_path = extract_dropped_dir(event)
        if local_path is not None:
            event.acceptProposedAction()
            return local_path
    event.ignore()
    return None


def _as_void_slot(fn: Callable[[], object]) -> Callable[[], None]:
    def _call() -> None:
        fn()

    return _call


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
    on_reveal_selected: Callable[[], object],
    on_copy_selected_names: Callable[[], None],
    on_copy_selected_paths: Callable[[], None],
    on_add_selected_exception: Callable[[], None],
    on_remove_selected_exception: Callable[[], None],
    on_show_help: Callable[[], None],
    on_open_tuner: Callable[[], None],
    on_open_settings: Callable[[], None] | None = None,
    on_reset_sort: Callable[[], None] | None = None,
    get_column_visibility: Callable[[], list[tuple[int, str, bool, bool]]] | None = None,
    on_set_column_visible: Callable[[int, bool], None] | None = None,
    on_set_analyze_mode: Callable[[str], None] | None = None,
    on_open_presets_dir: Callable[[], None] | None = None,
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
        on_reveal_selected=_as_void_slot(on_reveal_selected),
        on_copy_selected_names=on_copy_selected_names,
        on_copy_selected_paths=on_copy_selected_paths,
        on_add_selected_exception=on_add_selected_exception,
        on_remove_selected_exception=on_remove_selected_exception,
        on_real_classify=win_cb.on_real_classify,
        on_unclassify_all=win_cb.on_unclassify_all,
        on_show_help=on_show_help,
        on_reset_sort=on_reset_sort,
        get_column_visibility=get_column_visibility,
        on_set_column_visible=on_set_column_visible,
        on_set_analyze_mode=on_set_analyze_mode,
        on_open_presets_dir=on_open_presets_dir,
    )
