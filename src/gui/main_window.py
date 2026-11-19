"""Video Classifier Studio 데스크톱 GUI 메인 윈도우 모듈"""

import os
from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import (
    QCloseEvent,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QKeyEvent,
    QMouseEvent,
    QResizeEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QWidget,
)

from src.core.config import ClassifierConfig, SavingsConfig, ThresholdsConfig
from src.core.eta import format_estimate_label
from src.core.models import SummaryReport
from src.core.settings.app_state import AppState
from src.core.settings.shortcuts import resolve_shortcuts
from src.core.undo_history import UndoableSnapshot
from src.core.workspace_paths import format_root_dirs, parse_root_dirs, primary_root_dir
from src.gui.constants import WINDOW_DEFAULT_HEIGHT, WINDOW_DEFAULT_WIDTH, browse_directory, call_later
from src.gui.controllers.dialog_coordinator import DialogCoordinator
from src.gui.controllers.external_resync import attach_external_resync
from src.gui.controllers.manual_target_controller import ManualTargetController, ManualTargetHost
from src.gui.controllers.report_evaluator import ReportEvaluator
from src.gui.controllers.restore_flow import RestoreFlow
from src.gui.controllers.session_controller import (
    SessionCoordinator,
    apply_report_preserving_selection,
    order_report_evaluations,
    update_filter_exceptions,
)
from src.gui.controllers.shortcuts_controller import (
    ShortcutHost,
    ShortcutsController,
    shortcuts_history_label,
)
from src.gui.controllers.task_runner import TaskRunner
from src.gui.controllers.worker import ClassifierWorker
from src.gui.dialogs.help_dialog import HelpDialog
from src.gui.dialogs.log_dialog import LogTab
from src.gui.dialogs.settings_dialog import SettingsDialog
from src.gui.footer_summary import (
    build_footer_summary_spec,
    summarize_rows,
)
from src.gui.logging_bridge import GuiLogBridge
from src.gui.menus.menu_bar import MainMenuController
from src.gui.menus.menu_callbacks import MenuBarCallbacks
from src.gui.preview.preview_controller import PreviewController
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.table.column_history import (
    COLUMN_RESET_HISTORY_LABEL,
    COLUMN_VISIBILITY_HISTORY_LABEL,
    ColumnHistoryRecorder,
)
from src.gui.table_model import VideoTableModel
from src.gui.theme import ThemeManager
from src.gui.window_layout import (
    MainWindowCallbacks,
    build_main_window_widgets,
    build_menu_callbacks,
    handle_drag_enter,
    handle_drop_dirs_event,
    is_blank_area_click,
    restore_window_ui_state,
    set_drop_overlay_visible,
    sync_drop_overlay_geometry,
)
from src.service.session import ClassifierSession

_SETTINGS_PREWARM_DELAY_MS: int = 1000


class MainWindow(QMainWindow):
    """데스크톱 GUI 메인 윈도우

    HeaderBar, BentoDashboard, FilterTuningBar, VideoTableView, FooterBar 조합 및
    세션(SessionCoordinator), 작업 실행(TaskRunner), 보조 대화상자(DialogCoordinator) 연결
    """

    def __init__(
        self,
        classifier: ClassifierSession | None = None,
        initial_dir: str | None = None,
    ) -> None:
        super().__init__()
        self.resize(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)
        self.setAcceptDrops(True)

        self._theme_mgr = ThemeManager()
        self._current_report: SummaryReport | None = None
        self._pre_reset_state: UndoableSnapshot | None = None
        self._worker: ClassifierWorker | None = None
        self._session = SessionCoordinator(classifier, initial_dir)
        self._session.apply_initial_theme(
            self._theme_mgr, has_external_classifier=(classifier is not None)
        )

        self.setStyleSheet(build_stylesheet(self._theme_mgr.current_palette))
        self._theme_mgr.theme_changed.connect(self._on_theme_changed)
        self._theme_signal_connected: bool = True

        self.dialogs = DialogCoordinator(
            parent=self,
            on_status=self.set_status_text,
            on_settings_applied=self.apply_settings,
            on_settings_reset=self._on_settings_reset,
            on_criteria_changed=self._on_criteria_changed,
            on_settings_tab_changed=self._on_settings_tab_changed,
            on_help_tab_changed=self._on_help_tab_changed,
            on_setting_hints_changed=self._on_setting_hints_changed,
            on_shortcuts_changed=self._on_shortcuts_changed,
            on_settings_reset_confirmed=self._on_settings_reset_confirmed,
            on_undo=lambda: self.shortcuts.trigger_undo(),
            on_redo=lambda: self.shortcuts.trigger_redo(),
            on_preview_backend_changed=lambda backend: self._preview.set_backend(backend),
        )
        self._log_bridge = GuiLogBridge(on_log=self.dialogs.append_log)
        self._log_bridge.attach()
        self._tasks = TaskRunner(self)

        win_cb = self._build_window_callbacks()
        widgets = build_main_window_widgets(
            window=self, initial_dir=self._session.initial_dir, callbacks=win_cb
        )
        self.header_bar = widgets.header_bar
        self.header_bar.path_submitted.connect(self.set_working_directory)
        self.header_bar.path_rejected.connect(self.set_status_text)
        self.header_bar.split_analyze.set_estimate_provider(self._analysis_estimate_text)
        self.dashboard, self.filter_bar = widgets.dashboard, widgets.filter_bar
        self.table_view = widgets.table_view
        self._external_resync = attach_external_resync(
            self.table_view,
            self._session,
            self._tasks,
            self.set_status_text,
            lambda kf: self.start_analyze(include_keyframe=kf, force_reanalyze=False),
            self._re_evaluate_async,
        )
        self.top_area = widgets.top_area
        self.footer_bar = widgets.footer_bar
        self._drop_overlay = widgets.drop_overlay
        self.table_model = self.table_view.table_model
        self.proxy_model = self.table_view.proxy_model
        self._evaluator = ReportEvaluator(self)
        self._restore_flow = RestoreFlow(
            self._session, self._tasks, self, self.set_busy_state, self.set_status_text, self.set_progress_pending,
            self.apply_filters_to_classifier, self._re_evaluate_async,
        )
        self.manual_targets = ManualTargetController(self._build_manual_target_host())
        self.filter_bar.clear_column_filters_requested.connect(
            self.proxy_model.clear_all_column_filters
        )
        self.proxy_model.column_filters_changed.connect(self._on_column_filters_updated)
        self.proxy_model.sort_keys_changed.connect(lambda: self._session.sync_sort_keys(self.proxy_model))

        self._preview = PreviewController(
            self,
            self.table_view,
            lambda: self._session.state.ui,
            lambda: self._session.state.to_classifier_config(),
            self._on_preview_settings_changed,
            self._on_preview_audio_changed,
            self._on_preview_rate_changed,
            lambda: resolve_shortcuts(self._session.state.ui.shortcuts),
            lambda key_text: self.shortcuts.trigger_command_for_key(key_text),
            self.set_status_text,
        )
        self.table_view.current_video_changed.connect(self._preview.on_table_current_changed)
        # - 정보 표시가 표 값을 그대로 보여 주므로 선택 수, 평가 결과, 임시 출력 해상도, 표시 설정이 바뀌면 다시 읽음
        self.table_view.selection_changed.connect(lambda _count: self._preview.refresh_video_info())
        self.table_view.table_model.modelReset.connect(self._preview.refresh_video_info)
        self.table_view.table_model.dataChanged.connect(
            lambda _top, _bottom, _roles=None: self._preview.refresh_video_info()
        )
        self.table_view.table_model.config_changed.connect(self._preview.refresh_video_info)
        # - 첫 창 표시 뒤 이벤트 루프에서 QuickLook 상주 실행, 기본 방식이 내장이면 아무 동작 없음
        call_later(self, 0, self._preview.warm_up)
        self.menu = MainMenuController(self, self._build_menu_callbacks(win_cb))
        self.table_view.selection_history_changed.connect(self.menu.update_selection_history_state)
        self.shortcuts = ShortcutsController(self._build_shortcut_host())
        self.shortcuts.apply_shortcuts(self._app_state.ui.shortcuts)
        self.column_history = ColumnHistoryRecorder(
            self.table_view,
            capture_state=lambda: self.shortcuts.capture(columns=True),
            record_change=lambda label, before: self.shortcuts.record_change(label, before),
        )
        restore_window_ui_state(self, self._app_state.ui.layout, self.table_view)
        self._session.restore_pending_selection(self._current_root_dir(), self.table_view)
        self._sync_widgets_from_session()
        self._app_filter_installed: bool = False
        if (app := QApplication.instance()) is not None:
            app.installEventFilter(self)
            self._app_filter_installed = True
        # - 창이 그려진 뒤 복원해야 진행 표시와 완료 반영이 보이므로 이벤트 루프 진입 후 실행
        call_later(self, 0, self._restore_flow.start)
        # - 표 전용 명령(Space 미리 보기)은 표에 포커스가 있어야 동작하므로 시작 직후 표에 포커스를 둠
        call_later(self, 0, lambda: self.table_view.setFocus(Qt.FocusReason.OtherFocusReason))
        # - 시작 직후의 복원과 첫 화면 표시가 끝난 뒤 생성해 시작 지연에 섞이지 않게 함
        call_later(self, _SETTINGS_PREWARM_DELAY_MS, self._prewarm_settings_dialog)

    def _prewarm_settings_dialog(self) -> None:
        """설정 대화상자를 숨긴 상태로 미리 만들어 첫 열기 때의 생성 지연을 없앰"""
        self.dialogs.prewarm_settings_dialog(
            self._effective_config(),
            self._current_root_dir(),
            app_state=self._app_state,
        )

    def _build_window_callbacks(self) -> MainWindowCallbacks:
        return MainWindowCallbacks(
            root_dir_provider=self._current_root_dir,
            on_browse_dir=self._on_browse_dir,
            on_add_dir=self._on_add_dir,
            on_toggle_recursive=self._on_recursive_toggled,
            on_open_explorer=self._on_open_root_in_explorer,
            on_analyze_fast=lambda: self.start_analyze(include_keyframe=False),
            on_analyze_full=lambda: self.start_analyze(include_keyframe=True),
            on_cancel=self.cancel_active_task,
            on_open_settings=self.open_settings,
            on_filter_changed=self._on_filter_changed,
            on_exceptions_changed=self._on_exceptions_changed,
            on_append_exception=lambda kw: update_filter_exceptions(
                self.filter_bar, [kw], add=True
            ),
            on_append_exceptions=lambda kws: update_filter_exceptions(
                self.filter_bar, kws, add=True
            ),
            on_remove_exceptions=lambda kws: update_filter_exceptions(
                self.filter_bar, kws, add=False
            ),
            on_selection_changed=self._on_selection_changed,
            on_shortcut_analyze=lambda: self._run_mode_analyze(force_reanalyze=False),
            on_reset_filters=self._on_reset_filters,
            on_show_logs=self.show_log_dialog,
            on_export_csv=self.export_csv,
            on_focus_search=self._focus_search,
            on_analyze_mode_changed=self._on_analyze_mode_changed,
            on_open_exception_editor=self._on_open_exception_editor,
            on_manual_targets_requested=lambda evals, value: self.manual_targets.request(evals, value),
            on_manual_targets_cleared=lambda: self.manual_targets.clear(),
            can_start_drag=lambda: not self._tasks.is_running(),
        )

    def _build_manual_target_host(self) -> ManualTargetHost:
        # - 되돌리기 기록은 shortcuts 생성 전에 이 컨트롤러가 만들어지므로 지연 호출로 연결
        return ManualTargetHost(
            parent=self,
            session=self._session,
            table_view=self.table_view,
            is_busy=self._tasks.is_running,
            re_evaluate=self._re_evaluate_async,
            capture_state=lambda: self.shortcuts.capture(),
            record_change=lambda label, before: self.shortcuts.record_change(label, before),
            notify=lambda text: self._preview.notify(text),
        )

    def _build_shortcut_host(self) -> ShortcutHost:
        return ShortcutHost(
            window=self,
            menu=self.menu,
            session=self._session,
            header_bar=self.header_bar,
            top_area=self.top_area,
            filter_bar=self.filter_bar,
            footer_bar=self.footer_bar,
            table_view=self.table_view,
            dialogs=self.dialogs,
            is_busy=self._tasks.is_running,
            refresh_after_restore=self._refresh_after_history_restore,
            set_theme_mode=self._theme_mgr.set_mode,
            clear_manual_targets=self.manual_targets.clear,
            clear_column_filters=self.proxy_model.clear_all_column_filters,
            notify=lambda text: self._preview.notify(text),
        )

    def _refresh_after_history_restore(self) -> None:
        """되돌리기, 다시 실행으로 상태가 바뀐 뒤 위젯, 분류기, 테이블, 버튼을 현재 상태에 맞춤"""
        self._sync_widgets_from_session()
        self._session.sync_classifier_and_table(self.filter_bar, self.table_model)
        self._re_evaluate_async()

    def _build_menu_callbacks(self, win_cb: MainWindowCallbacks) -> MenuBarCallbacks:
        tv = self.table_view
        callbacks = build_menu_callbacks(
            win_cb,
            on_select_recent_dir=self._on_select_recent_dir,
            on_clear_recent_dirs=self._on_clear_recent_dirs,
            on_close_window=self.close,
            on_set_theme_mode=self._theme_mgr.set_mode,
            on_reset_columns=lambda: self.column_history.run_command(COLUMN_RESET_HISTORY_LABEL, tv.reset_columns),
            on_force_reanalyze=lambda: self._run_mode_analyze(force_reanalyze=True),
            on_play_selected=self._table_slot(tv.play_selected, "선택한 {}개 영상을 재생했습니다."),
            on_preview_selected=self._preview.preview_selected,
            on_open_preview_settings=self._preview.show_settings,
            on_reveal_selected=tv.reveal_selected,
            on_copy_selected_names=self._table_slot(tv.copy_selected_names, "선택한 {}개 파일명을 클립보드에 복사했습니다."),
            on_copy_selected_paths=self._table_slot(tv.copy_selected_paths, "선택한 {}개 전체 경로를 클립보드에 복사했습니다."),
            on_add_selected_exception=self._table_slot(
                tv.add_selected_to_exceptions, "선택한 {}개 항목을 예외 규칙에 추가했습니다.", require_idle=True
            ),
            on_remove_selected_exception=self._table_slot(
                tv.remove_selected_from_exceptions, "선택한 {}개 항목을 예외 규칙에서 제거했습니다.", require_idle=True
            ),
            on_show_help=self._open_help, on_clear_analysis_cache=self._tasks.clear_analysis_cache,
            on_set_manual_target=lambda value: tv.set_manual_target_for(None, value),
            on_clear_manual_targets=self.manual_targets.clear,
            get_selected_evaluations=tv.get_selected_evaluations,
            on_open_tuner=self.open_tuner,
            on_open_settings=lambda: self.open_settings(active_tab=SettingsDialog.TAB_ENVIRONMENT),
            on_reset_sort=tv.reset_table_sort, get_column_visibility=self._get_column_visibility,
            on_set_column_visible=lambda col, vis: self.column_history.run_command(
                COLUMN_VISIBILITY_HISTORY_LABEL, lambda: tv.setColumnHidden(col, not vis)
            ),
            on_set_analyze_mode=lambda mode: self.header_bar.split_analyze.set_mode(mode, emit_signal=True),
        )
        callbacks.on_undo = lambda: self.shortcuts.trigger_undo()
        callbacks.on_redo = lambda: self.shortcuts.trigger_redo()
        callbacks.on_restore_selection = self._table_slot(
            tv.restore_previous_selection, "이전 선택 {}개 영상을 복원했습니다.", require_idle=True
        )
        callbacks.on_redo_selection = self._table_slot(
            tv.redo_selection_restore,
            "선택 복원을 되돌려 {}개 영상을 선택했습니다.",
            require_idle=True,
            zero_status="복원했던 선택을 해제 상태로 되돌렸습니다.",
        )
        callbacks.on_open_shortcuts = lambda: self.open_settings(active_tab=SettingsDialog.TAB_SHORTCUTS)
        return callbacks

    @property
    def _app_state(self) -> AppState:
        return self._session.state

    @property
    def classifier(self) -> ClassifierSession | None:
        """현재 바인딩된 분류 세션 인스턴스 반환"""
        return self._session.classifier

    @property
    def current_report(self) -> SummaryReport | None:
        """가장 최근 UI에 적용된 요약 리포트 반환"""
        return self._current_report

    @property
    def active_worker(self) -> ClassifierWorker | None:
        """현재 실행 중인 백그라운드 워커 반환"""
        return self._worker

    @active_worker.setter
    def active_worker(self, worker: ClassifierWorker | None) -> None:
        self._worker = worker

    @property
    def task_runner(self) -> TaskRunner:
        """비동기 작업 실행 컨트롤러 반환"""
        return self._tasks

    def as_widget(self) -> QWidget:
        """호스트 위젯 자신(self) 반환"""
        return self

    def _current_root_dir(self) -> str:
        return self._session.current_root_dir(self.header_bar.path_edit.text())

    def _sync_widgets_from_session(self) -> None:
        self._session.sync_to_widgets(
            self.header_bar,
            self.filter_bar,
            self.table_model,
            self.menu,
            self._theme_mgr.mode,
            apply_proxy_cb=self._apply_proxy_filters_from_ui,
        )
        self._session.restore_table_state(self.proxy_model, self.table_view)
        self.table_view.set_empty_analyze_mode(self._app_state.ui.analyze_mode)

    def _focus_search(self) -> None:
        self.filter_bar.search_edit.setFocus()
        self.filter_bar.search_edit.selectAll()

    def _run_mode_analyze(self, *, force_reanalyze: bool) -> None:
        if not self._tasks.is_running():
            self.start_analyze(
                include_keyframe=(self.header_bar.current_analyze_mode == "full"),
                force_reanalyze=force_reanalyze,
            )

    def _table_slot(
        self,
        action: Callable[[], int | None],
        status_template: str,
        *,
        require_idle: bool = False,
        zero_status: str = "",
    ) -> Callable[[], None]:
        def _invoke() -> None:
            if require_idle and self._tasks.is_running():
                return
            if (count := action()) is None:
                return
            if count > 0:
                self.set_status_text(status_template.format(count))
            elif zero_status:
                self.set_status_text(zero_status)

        return _invoke

    def _on_preview_audio_changed(self, volume_percent: int, muted: bool) -> None:
        """미리 보기 창의 음량과 음소거 변경을 세션 저장값과 열린 설정 대화상자 사본에 함께 반영"""
        self._session.set_preview_audio(volume_percent, muted)
        self.dialogs.sync_preview_settings(self._session.state.ui)

    def _on_preview_rate_changed(self, rate: float) -> None:
        """미리 보기 창의 배속 변경을 세션 저장값과 열린 설정 대화상자 사본에 함께 반영"""
        self._session.set_preview_playback_rate(rate)
        self.dialogs.sync_preview_settings(self._session.state.ui)

    def _on_preview_settings_changed(self) -> None:
        """미리 보기 설정 변경을 지연 저장으로 예약하고 열린 설정 대화상자 사본에 반영"""
        self._session.schedule_preview_persist()
        self.dialogs.sync_preview_settings(self._session.state.ui)

    def _on_select_recent_dir(self, path: str) -> None:
        if self._tasks.is_running():
            return
        if not os.path.isdir(path):
            self.dialogs.warn_missing_recent_dir(path)
            self._session.remove_missing_recent_dir(
                path, self.menu, self.header_bar.path_edit.text()
            )
            return
        self.set_working_directory(path)

    def _on_clear_recent_dirs(self) -> None:
        self._session.clear_recent_dirs(self.menu, self.header_bar.path_edit.text())
        self.set_status_text("최근 작업 폴더 목록을 비웠습니다.")

    def _on_open_exception_editor(self) -> None:
        if self._tasks.is_running():
            return
        self.dialogs.open_exception_editor(
            self.filter_bar.get_exception_tokens(),
            self._app_state.ui.disabled_exception_tokens,
            on_applied=self._on_exception_rules_applied,
            busy=self._tasks.is_running(),
        )

    def _on_exception_rules_applied(
        self, active_tokens: list[str], disabled_tokens: list[str]
    ) -> None:
        if self._tasks.is_running():
            return
        before = self.shortcuts.capture()
        self._session.apply_exception_editor_result(
            self.filter_bar.get_exception_tokens(),
            (active_tokens, disabled_tokens),
            self.filter_bar,
        )
        self.shortcuts.record_change("예외 규칙 편집", before)

    def ensure_classifier(self) -> ClassifierSession | None:
        """현재 경로 기준으로 분류 세션 인스턴스를 준비하고 반환"""
        return self._session.ensure_classifier(
            self,
            self.header_bar.path_edit.text().strip(),
            self.filter_bar,
            self.table_model,
        )

    def _analysis_estimate_text(self, include_keyframe: bool) -> str:
        """분석 모드 팝업에 표시할 예상 문구, 분류 세션이 없으면 빈 문구"""
        clf = self._session.classifier
        if clf is None:
            return ""
        estimate = clf.estimate_analysis(include_keyframe)
        return format_estimate_label(estimate.pending_count, estimate.expected_sec)

    def apply_filters_to_classifier(self, clf: ClassifierSession) -> None:
        """필터 바의 임계값, 예외 규칙을 분류기 인스턴스에 동기화"""
        clf.set_exception_tokens(self.filter_bar.get_exception_tokens())

    def _effective_config(self) -> ClassifierConfig:
        clf = self._session.classifier
        return clf.config if clf is not None else self._app_state.to_classifier_config()

    def _refresh_filter_summary(self) -> None:
        self.filter_bar.set_any_filter_active(self.proxy_model.is_any_filter_active())
        self._update_filter_count_label()

    def _apply_proxy_filters_from_ui(self) -> None:
        self.proxy_model.set_search_text(self.filter_bar.search_edit.text())
        self._refresh_filter_summary()

    def _re_evaluate_async(self, on_done: Callable[[], None] | None = None) -> None:
        """캐시 재평가를 백그라운드에서 수행해 설정 변경과 미리 보기 재생 중에도 GUI 입력이 멈추지 않게 하고, 결과 적용 뒤 on_done 호출"""
        self._session.submit_cached_reevaluation(
            self._evaluator, self.apply_report, on_done or (lambda: None)
        )

    def _on_analyze_mode_changed(self, mode: str) -> None:
        self._app_state.ui.analyze_mode = mode
        self.header_bar.set_analyze_mode(mode)
        self.table_view.set_empty_analyze_mode(mode)
        if hasattr(self, "menu"):
            self.menu.sync_analyze_mode(mode)
        self._session.persist()

    def _get_column_visibility(self) -> list[tuple[int, str, bool, bool]]:
        return [
            (col_idx, VideoTableModel.MENU_HEADERS[col_idx], not self.table_view.isColumnHidden(col_idx), col_idx == VideoTableModel.COL_NAME)
            for col_idx in range(self.table_model.columnCount())
        ]

    def _on_help_tab_changed(self, tab_index: int) -> None:
        normalized = HelpDialog.normalize_tab_index(tab_index)
        if self._app_state.ui.layout.help_tab == normalized:
            return
        self._app_state.ui.layout.help_tab = normalized
        self._session.persist()

    def _open_help(self) -> None:
        self.dialogs.show_help_dialog(
            self._effective_config(),
            active_tab=self._app_state.ui.layout.help_tab,
            shortcuts=self.shortcuts.effective_shortcuts,
        )

    def _on_exceptions_changed(self) -> None:
        if not self._tasks.is_running():
            before = self.shortcuts.capture()
            self._session.apply_exceptions(self.filter_bar, self.table_model)
            self._re_evaluate_async()
            self.shortcuts.record_change("예외 규칙 변경", before)

    def _on_criteria_changed(
        self, thresholds: ThresholdsConfig, savings: SavingsConfig
    ) -> None:
        if not self._tasks.is_running():
            before = self.shortcuts.capture()
            self._session.apply_criteria(
                thresholds,
                savings,
                self.filter_bar,
                self.table_model,
                self.dialogs,
            )
            self._re_evaluate_async()
            self.shortcuts.record_change("판별 기준 조정", before, coalesce_key="criteria")

    def _on_theme_changed(self, palette: ThemePalette) -> None:
        self.setStyleSheet(build_stylesheet(palette))
        for vp in (
            self.table_view.viewport(),
            self.table_view.filter_header.viewport(),
        ):
            if vp is not None:
                vp.update()
        if hasattr(self, "menu"):
            self.menu.sync_theme_mode(self._theme_mgr.mode)
            self.menu.refresh_icons(palette)
        if hasattr(self, "_session"):
            self._app_state.ui.theme_mode = self._theme_mgr.mode
            self._session.persist()

    def _on_column_filters_updated(self) -> None:
        self.filter_bar.set_active_column_filter_count(
            self.proxy_model.active_column_filter_count()
        )
        self._refresh_filter_summary()
        self._session.sync_column_filters(self.proxy_model)

    def _on_filter_changed(self) -> None:
        self._apply_proxy_filters_from_ui()
        self._session.sync_filters_to_state(self.filter_bar)

    def _on_reset_filters(self) -> None:
        self.filter_bar.search_edit.clear()
        self.proxy_model.clear_all_column_filters()
        self._refresh_filter_summary()

    def _on_selection_changed(self, count: int) -> None:
        self.menu.update_selection_state(count)
        self._update_filter_count_label()
        self._session.schedule_selection_persist(self._current_report, self.table_view)

    def _update_filter_count_label(self) -> None:
        total = self.table_model.rowCount()
        visible_evals = self.proxy_model.iter_visible_evaluations()
        selected_evals = self.table_view.get_selected_evaluations()
        spec = build_footer_summary_spec(
            total_count=total,
            visible=summarize_rows(visible_evals),
            selected=summarize_rows(selected_evals) if selected_evals else None,
        )
        self.footer_bar.set_summary_spec(spec)
        self.table_view.update_empty_state(
            total_count=total, visible_count=len(visible_evals)
        )

    def _on_browse_dir(self) -> None:
        current = self.header_bar.committed_path
        start = primary_root_dir(current)
        chosen = browse_directory(self, "작업 폴더 선택", start)
        if not chosen:
            return
        root_count = len(parse_root_dirs(current))
        if root_count > 1 and not self._confirm_replace_roots(root_count):
            return
        self.set_working_directory(chosen)

    def _confirm_replace_roots(self, root_count: int) -> bool:
        """여러 작업 폴더를 선택한 폴더 하나로 바꾸기 전에 사용자 확인을 받음"""
        answer = QMessageBox.question(
            self,
            "작업 폴더 선택",
            f"작업 폴더 {root_count}개를 선택한 폴더 하나로 바꿀까요?\n"
            "추가한 작업 폴더는 목록에서 빠집니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_add_dir(self) -> None:
        if chosen := browse_directory(self, "작업 폴더 추가", ""):
            self.add_working_directory(chosen)

    def add_working_directory(self, directory: str) -> None:
        """현재 작업 폴더를 유지한 채 폴더를 추가하고 바뀌었으면 분석 뷰를 초기화"""
        if self._session.add_directory(
            directory, self.header_bar, self.filter_bar, self.table_model, self.menu
        ):
            self.dialogs.sync_root_dir(self._current_root_dir())
            self._clear_analysis_view()
            self._restore_flow.start()

    def _on_recursive_toggled(self, enabled: bool) -> None:
        if self._session.set_recursive_scan(enabled, self.header_bar, self.menu):
            self._clear_analysis_view()
            self._restore_flow.start()

    def _clear_analysis_view(self) -> None:
        self._session.save_current_selection(self._current_report, self.table_view)
        self.shortcuts.clear_history()
        self._current_report = None
        self._external_resync.reset()
        self.table_view.clear_selection_history()
        self.menu.update_selection_state(0)
        self.proxy_model.clear_all_column_filters()
        self.table_model.set_data_source([], root_dir=self._current_root_dir())
        self._refresh_filter_summary()
        self.dashboard.reset()
        self._session.restore_pending_selection(self._current_root_dir(), self.table_view)

    def set_working_directory(self, directory: str) -> None:
        """작업 폴더를 전환하고 필요 시 이전 분석 뷰를 초기화"""
        dir_changed = self._session.switch_directory(
            directory, self.header_bar, self.filter_bar, self.table_model, self.menu
        )
        self.dialogs.sync_root_dir(self._current_root_dir())
        if dir_changed:
            self._clear_analysis_view()
            self._restore_flow.start()

    def _on_settings_tab_changed(self, tab_index: int) -> None:
        normalized = SettingsDialog.normalize_tab_index(tab_index)
        if self._app_state.ui.layout.settings_tab == normalized:
            return
        self._app_state.ui.layout.settings_tab = normalized
        self._session.persist()

    def _on_shortcuts_changed(self, overrides: dict[str, str]) -> None:
        """설정 대화상자 단축키 탭의 변경을 세션 상태에 저장하고 모든 명령에 즉시 반영"""
        before = self.shortcuts.capture()
        self._app_state.ui.shortcuts = dict(overrides)
        self.shortcuts.apply_shortcuts(overrides)
        self._session.persist()
        self.shortcuts.record_change(shortcuts_history_label(overrides), before)

    def _on_setting_hints_changed(self, visible: bool) -> None:
        self._app_state.ui.show_setting_hints = bool(visible)

    def open_tuner(self) -> None:
        """통합 설정 대화상자를 판별 기준 탭으로 열거나 전환"""
        self._on_settings_tab_changed(SettingsDialog.TAB_CRITERIA)
        self.dialogs.open_tuner(
            self._effective_config(),
            self._current_root_dir(),
            app_state=self._app_state,
            busy=self._tasks.is_running(),
        )

    def open_settings(self, *, active_tab: int | None = None) -> None:
        """통합 설정 대화상자를 지정 탭(미지정 시 마지막으로 연 탭)으로 열거나 전환"""
        if active_tab is not None:
            self._on_settings_tab_changed(active_tab)
        self.dialogs.open_settings(
            self._effective_config(),
            self._current_root_dir(),
            app_state=self._app_state,
            active_tab=active_tab,
            busy=self._tasks.is_running(),
        )

    def apply_settings(self, new_cfg: ClassifierConfig, new_root: str) -> None:
        """변경된 설정과 작업 폴더 경로를 세션, 분류기, 화면 상태에 반영"""
        if self._tasks.is_running():
            return
        before = self.shortcuts.capture()
        root_changed = self._session.apply_settings(
            new_cfg,
            new_root,
            self.header_bar,
            self.filter_bar,
            self.table_model,
            self.menu,
            self._theme_mgr.mode,
        )
        if root_changed:
            self._clear_analysis_view()
        elif self._session.classifier is not None and self._session.classifier.has_cache:
            self._re_evaluate_async()
        self.dialogs.sync_criteria(new_cfg)
        if not root_changed:
            self.shortcuts.record_change("환경 설정 변경", before, coalesce_key="settings")

    def _on_settings_reset_confirmed(self) -> None:
        # - 설정 창이 테마를 먼저 바꾸므로 초기화 전 상태는 확인 직후에 보관
        self._pre_reset_state = self.shortcuts.capture()

    def _on_settings_reset(self, fresh_state: AppState) -> None:
        before, self._pre_reset_state = self._pre_reset_state, None
        if self._tasks.is_running():
            return
        if before is None:
            before = self.shortcuts.capture()
        self._session.state = fresh_state.copy()
        self._session.attach_manual_targets()
        # - 초기화된 직접 지정 맵을 전용 DB 테이블에도 반영
        self._session.persist()
        self._sync_widgets_from_session()
        self.dialogs.sync_criteria(self._app_state.to_classifier_config())
        if self._session.classifier is not None:
            self._session.classifier.set_config(self._app_state.to_classifier_config())
            self._session.classifier.set_recursive(self._app_state.ui.recursive_scan)
            self._session.classifier.set_exception_tokens([])
        self._re_evaluate_async()
        self.shortcuts.apply_shortcuts(self._app_state.ui.shortcuts)
        self.shortcuts.record_change("전체 초기화", before)

    def _on_open_root_in_explorer(self) -> None:
        target = primary_root_dir(self.header_bar.path_edit.text())
        if target and os.path.isdir(target) and hasattr(os, "startfile"):
            os.startfile(target)

    def set_busy_state(self, busy: bool, status_msg: str = "") -> None:
        """작업 실행 여부에 맞춰 헤더, 필터, 테이블, 하단 상태 바 잠금 전환"""
        self.header_bar.set_busy(busy)
        self.filter_bar.set_busy(busy)
        self.dialogs.set_tuner_busy(busy)
        self.dialogs.set_busy(busy)
        self.table_view.set_empty_action_enabled(not busy)
        self.footer_bar.set_busy(busy, status_msg)
        self.menu.set_busy(busy)

    def set_progress(self, value: int, detail: str = "", tooltip: str = "") -> None:
        """하단 진행률 표시줄 값, 막대 안 문구, 툴팁 설정"""
        self.footer_bar.set_progress(value, detail, tooltip)

    def set_progress_pending(self) -> None:
        """하단 진행률 표시줄을 무한 진행 상태로 전환"""
        self.footer_bar.set_progress_pending()

    def set_status_text(self, text: str, tooltip: str = "") -> None:
        """하단 상태 표시줄 텍스트와 툴팁 설정"""
        self.footer_bar.set_status_text(text, tooltip)

    def apply_report(self, report: SummaryReport) -> None:
        """요약 리포트를 대시보드, 테이블 모델, 필터 상태에 일괄 반영하고 같은 폴더 선택을 유지"""
        previous = self._current_report
        self._current_report = report
        apply_report_preserving_selection(
            self._session,
            previous,
            report,
            self.table_view,
            self.table_model,
            order_report_evaluations(report),
        )
        self.menu.update_selection_state(len(self.table_view.get_selected_evaluations()))
        self._update_filter_count_label()
        self.dashboard.update_from_report(report)

    def start_analyze(
        self, include_keyframe: bool = True, force_reanalyze: bool = False
    ) -> None:
        """백그라운드 비디오 메타데이터 스캔 시작"""
        if self._tasks.is_running():
            return
        if not self._current_root_dir():
            chosen = browse_directory(self, "작업 폴더 선택", "")
            if not chosen:
                return
            self.set_working_directory(chosen)
            if not self._current_root_dir():
                return
        self._tasks.start_analyze(
            include_keyframe=include_keyframe, force_reanalyze=force_reanalyze
        )

    def cancel_active_task(self) -> None:
        """진행 중인 백그라운드 작업과 이전 분석 결과 판정 중단 요청"""
        self._tasks.cancel()
        self._restore_flow.cancel()

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """빈 영역 클릭 시 경로 입력 포커스를 해제하고, 외부 드래그 뒤 창 복귀 시 파일 변경을 동기화"""
        if a1 is not None and a1.type() == QEvent.Type.WindowActivate and a0 is self:
            self._external_resync.request(immediate=False)
        elif (
            isinstance(a1, QMouseEvent)
            and a1.type() == QEvent.Type.MouseButtonPress
            and a1.button() == Qt.MouseButton.LeftButton
            and is_blank_area_click(self, a0, self.table_view)
        ):
            self.header_bar.release_path_focus()
        return super().eventFilter(a0, a1)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """ESC 키 입력 시 실행 중인 백그라운드 작업을 즉시 중단"""
        if event is None:
            return
        if event.key() == Qt.Key.Key_Escape and self._tasks.is_running():
            self.cancel_active_task()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        """창 크기 변경 시 드롭 안내 오버레이 영역 동기화"""
        super().resizeEvent(event)
        if hasattr(self, "_drop_overlay") and self._drop_overlay.isVisible():
            sync_drop_overlay_geometry(self, self._drop_overlay)

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """유효한 로컬 디렉터리 드래그 진입 시 드롭 허용 및 안내 오버레이 표시"""
        handle_drag_enter(self, self._drop_overlay, self._tasks.is_running(), event)

    def dragLeaveEvent(self, event: QDragLeaveEvent | None) -> None:
        """드래그 포인터가 창 영역을 벗어나면 드롭 안내 오버레이 숨김"""
        set_drop_overlay_visible(self, self._drop_overlay, False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent | None) -> None:
        """드롭된 로컬 디렉터리로 작업 폴더를 전환하고, Shift 또는 Ctrl이면 현재 목록에 추가"""
        dirs, additive = handle_drop_dirs_event(
            self, self._drop_overlay, self._tasks.is_running(), event
        )
        if not dirs:
            return
        if additive:
            self.add_working_directory(format_root_dirs(dirs))
        else:
            self.set_working_directory(format_root_dirs(dirs))

    def _save_ui_state(self) -> None:
        self._session.save_window_layout(self, self.table_view, report=self._current_report)

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """창 종료 시 UI 상태 저장 및 워커, 로그 브리지, 보조 창 정리"""
        if event is None:
            return
        if self._app_filter_installed:
            if (app := QApplication.instance()) is not None:
                app.removeEventFilter(self)
            self._app_filter_installed = False
        if getattr(self, "_theme_signal_connected", False):
            self._theme_mgr.theme_changed.disconnect(self._on_theme_changed)
            self._theme_signal_connected = False
        self._save_ui_state()
        self._tasks.shutdown()
        self._log_bridge.detach()
        self.dialogs.close_all()
        super().closeEvent(event)

    def show_log_dialog(self, tab: LogTab = LogTab.LOG) -> None:
        """비모달 작업 기록 창을 지정 탭으로 표시"""
        self.dialogs.show_log_dialog(
            tab,
            export_model=self.proxy_model,
            base_dir_provider=lambda: self.header_bar.path_edit.text(),
        )

    def export_csv(self) -> None:
        """작업 기록 창의 CSV 내보내기 탭을 표시"""
        self.show_log_dialog(LogTab.EXPORT)
