"""메뉴바 액션에 연결할 메인 창 콜백 계약 데이터클래스 모듈"""

from collections.abc import Callable
from dataclasses import dataclass

from src.core.models import VideoEvaluation


@dataclass
class MenuBarCallbacks:
    """전체 기능 메뉴바 액션에 바인딩할 메인 창 콜백 모음"""

    on_browse_dir: Callable[[], None]
    on_select_recent_dir: Callable[[str], None]
    on_clear_recent_dirs: Callable[[], None]
    on_open_explorer: Callable[[], None]
    on_export_csv: Callable[[], None]
    on_close_window: Callable[[], None]
    on_focus_search: Callable[[], None]
    on_reset_filters: Callable[[], None]
    on_open_exception_editor: Callable[[], None]
    on_open_tuner: Callable[[], None]
    on_open_settings: Callable[[], None]
    on_set_theme_mode: Callable[[str], None]
    on_reset_columns: Callable[[], None]
    on_show_logs: Callable[[], None]
    on_shortcut_analyze: Callable[[], None]
    on_analyze_fast: Callable[[], None]
    on_analyze_full: Callable[[], None]
    on_force_reanalyze: Callable[[], None]
    on_cancel: Callable[[], None]
    on_play_selected: Callable[[], None]
    on_reveal_selected: Callable[[], None]
    on_copy_selected_names: Callable[[], None]
    on_copy_selected_paths: Callable[[], None]
    on_add_selected_exception: Callable[[], None]
    on_remove_selected_exception: Callable[[], None]
    on_real_classify: Callable[[], None]
    on_unclassify_all: Callable[[], None]
    on_show_help: Callable[[], None]
    on_clear_analysis_cache: Callable[[], None]
    on_set_manual_target: Callable[[str | None], object]
    on_clear_manual_targets: Callable[[], None]
    get_selected_evaluations: Callable[[], list[VideoEvaluation]]
    on_reset_sort: Callable[[], None] | None = None
    get_column_visibility: Callable[[], list[tuple[int, str, bool, bool]]] | None = None
    on_set_column_visible: Callable[[int, bool], None] | None = None
    on_set_analyze_mode: Callable[[str], None] | None = None
    on_open_presets_dir: Callable[[], None] | None = None
