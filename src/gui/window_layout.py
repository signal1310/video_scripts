"""메인 창 위젯 트리 조립, 단축키 바인딩 및 창 상태 영속화 모듈"""

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QFrame, QLabel, QMainWindow, QVBoxLayout, QWidget

from src.gui.widgets import (
    BentoDashboard,
    FilterTuningBar,
    FooterBar,
    HeaderBar,
    VideoTableView,
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
    """메인 윈도우 위젯·단축키 시그널에 바인딩할 콜백 묶음"""

    root_dir_provider: Callable[[], str]
    on_browse_dir: Callable[[], None]
    on_open_explorer: Callable[[], None]
    on_scan_fast: Callable[[], None]
    on_scan_full: Callable[[], None]
    on_cancel: Callable[[], None]
    on_open_settings: Callable[[], None]
    on_filter_changed: Callable[[], None]
    on_threshold_changed: Callable[[], None]
    on_append_exception: Callable[[str], None]
    on_append_exceptions: Callable[[list[str]], None]
    on_remove_exceptions: Callable[[list[str]], None]
    on_selection_summary_changed: Callable[[int, float, float], None]
    on_shortcut_scan: Callable[[], None]
    on_reset_filters: Callable[[], None]
    on_unclassify_all: Callable[[], None]
    on_real_classify: Callable[[], None]
    on_show_logs: Callable[[], None]
    on_export_csv: Callable[[], None]
    on_focus_search: Callable[[], None]
    on_scan_mode_changed: Callable[[str], None] | None = None


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
    header_bar.scan_fast_clicked.connect(callbacks.on_scan_fast)
    header_bar.scan_full_clicked.connect(callbacks.on_scan_full)
    if callbacks.on_scan_mode_changed is not None:
        header_bar.scan_mode_changed.connect(callbacks.on_scan_mode_changed)
    header_bar.cancel_clicked.connect(callbacks.on_cancel)
    header_bar.settings_clicked.connect(callbacks.on_open_settings)
    root_layout.addWidget(header_bar)

    dashboard = BentoDashboard(window)
    root_layout.addWidget(dashboard)

    filter_bar = FilterTuningBar(window)
    filter_bar.filter_changed.connect(callbacks.on_filter_changed)
    filter_bar.thresholds_changed.connect(callbacks.on_threshold_changed)
    root_layout.addWidget(filter_bar)

    table_view = VideoTableView(
        root_dir_provider=callbacks.root_dir_provider,
        exceptions_provider=filter_bar.get_exception_tokens,
        parent=window,
    )
    table_view.exception_keyword_added.connect(callbacks.on_append_exception)
    table_view.exception_keywords_added.connect(callbacks.on_append_exceptions)
    table_view.exception_keywords_removed.connect(callbacks.on_remove_exceptions)
    table_view.selection_summary_changed.connect(callbacks.on_selection_summary_changed)
    table_view.scan_requested.connect(callbacks.on_shortcut_scan)
    table_view.reset_filters_requested.connect(callbacks.on_reset_filters)
    root_layout.addWidget(table_view, stretch=1)

    footer_bar = FooterBar(window)
    footer_bar.unclassify_all_clicked.connect(callbacks.on_unclassify_all)
    footer_bar.real_classify_clicked.connect(callbacks.on_real_classify)
    footer_bar.logs_clicked.connect(callbacks.on_show_logs)
    footer_bar.export_csv_clicked.connect(callbacks.on_export_csv)
    root_layout.addWidget(footer_bar)

    QShortcut(QKeySequence("Ctrl+F"), window).activated.connect(callbacks.on_focus_search)
    QShortcut(QKeySequence("F5"), window).activated.connect(callbacks.on_shortcut_scan)
    QShortcut(QKeySequence("Ctrl+,"), window).activated.connect(callbacks.on_open_settings)
    QShortcut(QKeySequence("Ctrl+L"), window).activated.connect(callbacks.on_show_logs)
    QShortcut(QKeySequence("Ctrl+E"), window).activated.connect(callbacks.on_export_csv)

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
        hdr_state = QByteArray.fromBase64(table_header_b64.encode("ascii"))
        header = table_view.horizontalHeader()
        if not hdr_state.isEmpty() and header is not None:
            header.restoreState(hdr_state)


def export_window_ui_state(
    window: QMainWindow,
    table_view: VideoTableView,
) -> tuple[str, str]:
    """현재 창 지오메트리 및 테이블 열 헤더 상태를 Base64 문자열 쌍으로 직렬화"""
    geom_b64 = window.saveGeometry().toBase64().data().decode("ascii")
    header = table_view.horizontalHeader()
    hdr_b64 = (
        header.saveState().toBase64().data().decode("ascii")
        if header is not None
        else ""
    )
    return geom_b64, hdr_b64
