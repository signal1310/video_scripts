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

from src.core.app_state import AppState
from src.core.config import ClassifierConfig, SavingsConfig, ThresholdsConfig
from src.core.models import SummaryReport
from src.core.paths import get_app_base_dir
from src.gui.constants import (
    WINDOW_DEFAULT_HEIGHT,
    WINDOW_DEFAULT_WIDTH,
    browse_directory,
)
from src.gui.dialog_coordinator import DialogCoordinator
from src.gui.dialogs.help_dialog import HelpDialog
from src.gui.dialogs.settings_dialog import SettingsDialog
from src.gui.exporters import export_table_to_csv
from src.gui.footer_summary import (
    build_footer_summary_spec,
    compute_table_range_summary,
)
from src.gui.logging_bridge import GuiLogBridge
from src.gui.menu_bar import MainMenuController
from src.gui.session_controller import (
    SessionCoordinator,
    evaluate_action_availability,
    order_report_evaluations,
    update_filter_exceptions,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.table_model import VideoTableModel
from src.gui.task_runner import TaskRunner
from src.gui.theme import ThemeManager
from src.gui.window_layout import (
    NON_CLEAR_CLICK_WIDGET_TYPES,
    MainWindowCallbacks,
    build_main_window_widgets,
    build_menu_callbacks,
    handle_drag_enter,
    handle_drop_event,
    restore_window_ui_state,
    set_drop_overlay_visible,
    sync_drop_overlay_geometry,
)
from src.gui.worker import ClassifierWorker
from src.service.session import ClassifierSession


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
        self.setWindowTitle("Video Classifier")
        self.resize(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)
        self.setAcceptDrops(True)

        self._theme_mgr = ThemeManager()
        self._current_report: SummaryReport | None = None
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
        )
        self._log_bridge = GuiLogBridge(on_log=self.append_log)
        self._log_bridge.attach()
        self._tasks = TaskRunner(self)

        win_cb = self._build_window_callbacks()
        widgets = build_main_window_widgets(
            window=self, initial_dir=self._session.initial_dir, callbacks=win_cb
        )
        self.header_bar = widgets.header_bar
        self.header_bar.path_submitted.connect(self.set_working_directory)
        self.header_bar.path_rejected.connect(self.set_status_text)
        self.dashboard = widgets.dashboard
        self.filter_bar = widgets.filter_bar
        self.table_view = widgets.table_view
        self.footer_bar = widgets.footer_bar
        self._drop_overlay = widgets.drop_overlay
        self.table_model = self.table_view.table_model
        self.proxy_model = self.table_view.proxy_model
        self.filter_bar.clear_column_filters_requested.connect(
            self.proxy_model.clear_all_column_filters
        )
        self.proxy_model.column_filters_changed.connect(self._on_column_filters_updated)

        tv = self.table_view
        menu_cb = build_menu_callbacks(
            win_cb,
            on_select_recent_dir=self._on_select_recent_dir,
            on_clear_recent_dirs=self._on_clear_recent_dirs,
            on_close_window=self.close,
            on_set_theme_mode=self._theme_mgr.set_mode,
            on_reset_columns=tv.reset_columns,
            on_force_reanalyze=lambda: self._run_mode_analyze(force_reanalyze=True),
            on_play_selected=self._table_slot(
                tv.play_selected, "선택한 {}개 영상을 재생했습니다."
            ),
            on_reveal_selected=tv.reveal_selected,
            on_copy_selected_names=self._table_slot(
                tv.copy_selected_names, "선택한 {}개 파일명을 클립보드에 복사했습니다."
            ),
            on_copy_selected_paths=self._table_slot(
                tv.copy_selected_paths,
                "선택한 {}개 전체 경로를 클립보드에 복사했습니다.",
            ),
            on_add_selected_exception=self._table_slot(
                tv.add_selected_to_exceptions,
                "선택한 {}개 항목을 예외 키워드에 추가했습니다.",
                require_idle=True,
            ),
            on_remove_selected_exception=self._table_slot(
                tv.remove_selected_from_exceptions,
                "선택한 {}개 항목을 예외 키워드에서 제거했습니다.",
                require_idle=True,
            ),
            on_show_help=self._open_help,
            on_open_tuner=self.open_tuner,
            on_open_settings=lambda: self.open_settings(
                active_tab=SettingsDialog.TAB_ENVIRONMENT
            ),
            on_reset_sort=tv.reset_table_sort,
            get_column_visibility=self._get_column_visibility,
            on_set_column_visible=lambda col, vis: tv.setColumnHidden(col, not vis),
            on_set_analyze_mode=self._on_set_analyze_mode_from_menu,
            on_open_presets_dir=self._on_open_presets_dir,
        )
        self.menu = MainMenuController(self, menu_cb)
        restore_window_ui_state(
            self,
            self._app_state.ui.layout.window_geometry,
            self._app_state.ui.layout.table_header_state,
            self.table_view,
        )
        self._sync_widgets_from_session()
        self._update_action_buttons()
        self._app_filter_installed: bool = False
        if (app := QApplication.instance()) is not None:
            app.installEventFilter(self)
            self._app_filter_installed = True

    def _build_window_callbacks(self) -> MainWindowCallbacks:
        return MainWindowCallbacks(
            root_dir_provider=self._current_root_dir,
            on_browse_dir=self._on_browse_dir,
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
            on_unclassify_all=self.run_unclassify,
            on_real_classify=lambda: self.run_real_classify(confirm=True),
            on_show_logs=self.show_log_dialog,
            on_export_csv=self.export_csv,
            on_focus_search=self._focus_search,
            on_analyze_mode_changed=self._on_analyze_mode_changed,
            on_open_exception_editor=self._on_open_exception_editor,
        )

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
        action: Callable[[], int],
        status_template: str,
        *,
        require_idle: bool = False,
    ) -> Callable[[], None]:
        def _invoke() -> None:
            if require_idle and self._tasks.is_running():
                return
            if (count := action()) > 0:
                self.set_status_text(status_template.format(count))

        return _invoke

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
        self._session.apply_exception_editor_result(
            self.filter_bar.get_exception_tokens(),
            (active_tokens, disabled_tokens),
            self.filter_bar,
        )

    def ensure_classifier(self) -> ClassifierSession | None:
        """현재 경로 기준으로 분류 세션 인스턴스를 준비하고 반환"""
        return self._session.ensure_classifier(
            self,
            self.header_bar.path_edit.text().strip(),
            self.filter_bar,
            self.table_model,
        )

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

    def _re_evaluate_from_cache(self) -> None:
        clf = self._session.classifier
        if clf is not None and (report := clf.get_cached_report()) is not None:
            self.apply_report(report)

    def _on_analyze_mode_changed(self, mode: str) -> None:
        self._app_state.ui.analyze_mode = mode
        self.header_bar.set_analyze_mode(mode)
        self.table_view.set_empty_analyze_mode(mode)
        if hasattr(self, "_menu"):
            self.menu.sync_analyze_mode(mode)
        self._session.persist()

    def _on_set_analyze_mode_from_menu(self, mode: str) -> None:
        self.header_bar.split_analyze.set_mode(mode, emit_signal=True)

    def _get_column_visibility(self) -> list[tuple[int, str, bool, bool]]:
        return [
            (
                col_idx,
                VideoTableModel.MENU_HEADERS[col_idx],
                not self.table_view.isColumnHidden(col_idx),
                col_idx == VideoTableModel.COL_NAME,
            )
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
        )

    def _on_open_presets_dir(self) -> None:
        presets_dir = os.path.join(get_app_base_dir(), "shana_presets")
        if os.path.isdir(presets_dir) and hasattr(os, "startfile"):
            os.startfile(presets_dir)
            return
        QMessageBox.information(
            self,
            "프리셋 폴더 안내",
            f"샤나인코더 프리셋 폴더를 찾을 수 없습니다.\n경로: {presets_dir}",
        )

    def _on_exceptions_changed(self) -> None:
        if not self._tasks.is_running():
            self._session.apply_exceptions(self.filter_bar, self.table_model)
            self._re_evaluate_from_cache()

    def _on_criteria_changed(
        self, thresholds: ThresholdsConfig, savings: SavingsConfig
    ) -> None:
        if not self._tasks.is_running():
            self._session.apply_criteria(
                thresholds,
                savings,
                self.filter_bar,
                self.table_model,
                self.dialogs,
            )
            self._re_evaluate_from_cache()

    def _on_theme_changed(self, palette: ThemePalette) -> None:
        self.setStyleSheet(build_stylesheet(palette))
        for vp in (
            self.table_view.viewport(),
            self.table_view.filter_header.viewport(),
        ):
            if vp is not None:
                vp.update()
        if hasattr(self, "_menu"):
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

    def _update_filter_count_label(self) -> None:
        total = self.table_model.rowCount()
        visible_evals = self.proxy_model.iter_visible_evaluations()
        selected_evals = self.table_view.get_selected_evaluations()
        summary = compute_table_range_summary(
            visible_evals=visible_evals,
            selected_evals=selected_evals,
            total_count=total,
        )
        self.footer_bar.set_summary_spec(build_footer_summary_spec(summary))
        self.table_view.update_empty_state(
            total_count=total, visible_count=len(visible_evals)
        )

    def _on_browse_dir(self) -> None:
        if chosen := browse_directory(
            self, "작업 폴더 선택", self.header_bar.path_edit.text()
        ):
            self.set_working_directory(chosen)

    def _clear_analysis_view(self) -> None:
        self._current_report = None
        self.menu.update_selection_state(0)
        self.proxy_model.clear_all_column_filters()
        self.table_model.set_data_source([], root_dir=self._current_root_dir())
        self._refresh_filter_summary()
        self.dashboard.reset()
        self._update_action_buttons()

    def set_working_directory(self, directory: str) -> None:
        """작업 폴더를 전환하고 필요 시 이전 분석 뷰를 초기화"""
        dir_changed = self._session.switch_directory(
            directory, self.header_bar, self.filter_bar, self.table_model, self.menu
        )
        self.dialogs.sync_root_dir(self._current_root_dir())
        if dir_changed:
            self._clear_analysis_view()
        else:
            self._update_action_buttons()

    def _on_settings_tab_changed(self, tab_index: int) -> None:
        normalized = SettingsDialog.normalize_tab_index(tab_index)
        if self._app_state.ui.layout.settings_tab == normalized:
            return
        self._app_state.ui.layout.settings_tab = normalized
        self._session.persist()

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
            self._re_evaluate_from_cache()
        else:
            self._update_action_buttons()
        self.dialogs.sync_criteria(new_cfg)

    def _on_settings_reset(self, fresh_state: AppState) -> None:
        if self._tasks.is_running():
            return
        self._session.state = fresh_state.copy()
        self._sync_widgets_from_session()
        self.dialogs.sync_criteria(self._app_state.to_classifier_config())
        if self._session.classifier is not None:
            self._session.classifier.set_config(self._app_state.to_classifier_config())
            self._session.classifier.set_exception_tokens([])
        self._re_evaluate_from_cache()
        self._update_action_buttons()

    def _on_open_root_in_explorer(self) -> None:
        target = self.header_bar.path_edit.text().strip()
        if target and os.path.isdir(target) and hasattr(os, "startfile"):
            os.startfile(target)

    def _update_action_buttons(self) -> None:
        can_classify, can_unclassify = evaluate_action_availability(
            self._current_report, self._current_root_dir(), self._app_state.to_classifier_config()
        )
        self.footer_bar.set_action_availability(
            can_classify=can_classify, can_unclassify=can_unclassify
        )
        self.menu.update_action_availability(
            can_classify=can_classify, can_unclassify=can_unclassify
        )

    def set_busy_state(self, busy: bool, status_msg: str = "") -> None:
        """작업 실행 여부에 맞춰 헤더, 필터, 테이블, 하단 상태 바 잠금 전환"""
        self.header_bar.set_busy(busy)
        self.filter_bar.set_busy(busy)
        self.dialogs.set_tuner_busy(busy)
        self.table_view.set_empty_action_enabled(not busy)
        self.footer_bar.set_busy(busy, status_msg)
        self.menu.set_busy(busy)
        if not busy:
            self._update_action_buttons()

    def set_progress(self, value: int) -> None:
        """하단 진행률 표시줄 값 설정"""
        self.footer_bar.progress_bar.setValue(value)

    def set_status_text(self, text: str) -> None:
        """하단 상태 표시줄 텍스트 설정"""
        self.footer_bar.set_status_text(text)

    def append_log(self, line: str) -> None:
        """대화상자 코디네이터 로그 버퍼에 한 줄 추가"""
        self.dialogs.append_log(line)

    def apply_report(self, report: SummaryReport) -> None:
        """요약 리포트를 대시보드, 테이블 모델, 필터 상태에 일괄 반영"""
        self._current_report = report
        self.menu.update_selection_state(0)
        clf = self._session.classifier
        self.table_model.set_data_source(
            order_report_evaluations(report),
            root_dir=report.root_dir,
            config=clf.config if clf is not None else None,
        )
        self._update_filter_count_label()
        self.dashboard.update_from_report(report)
        self._update_action_buttons()

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

    def run_real_classify(self, confirm: bool = True) -> None:
        """인코딩 대상 영상의 실제 하위 폴더 이동 실행"""
        self._tasks.run_real_classify(confirm=confirm)

    def run_unclassify(self) -> None:
        """분류된 영상 파일의 작업 폴더 복원 실행"""
        self._tasks.run_unclassify()

    def cancel_active_task(self) -> None:
        """진행 중인 백그라운드 작업 중단 요청"""
        self._tasks.cancel()

    def _should_clear_table_selection_on_click(self, target: QObject | None) -> bool:
        if not self.isVisible() or not isinstance(target, QWidget):
            return False
        if target.window() is not self:
            return False
        sel_model = self.table_view.selectionModel()
        if sel_model is None or not sel_model.hasSelection():
            return False
        curr: QWidget | None = target
        while curr is not None:
            if curr is self.table_view:
                return False
            if isinstance(curr, NON_CLEAR_CLICK_WIDGET_TYPES):
                return False
            if curr is self:
                break
            curr = curr.parentWidget()
        return True

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """테이블 외부의 비대화형 영역 클릭 시 현재 행 선택을 해제"""
        if (
            isinstance(a1, QMouseEvent)
            and a1.type() == QEvent.Type.MouseButtonPress
            and a1.button() == Qt.MouseButton.LeftButton
            and self._should_clear_table_selection_on_click(a0)
        ):
            self.table_view.clearSelection()
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
        """드롭된 로컬 디렉터리로 작업 폴더 전환 및 안내 오버레이 숨김"""
        if (
            local_path := handle_drop_event(
                self, self._drop_overlay, self._tasks.is_running(), event
            )
        ) is not None:
            self.set_working_directory(local_path)

    def _save_ui_state(self) -> None:
        self._session.save_window_layout(self, self.table_view)

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

    def show_log_dialog(self) -> None:
        """비모달 작업 로그 대화상자 표시"""
        self.dialogs.show_log_dialog()

    def export_report_to_csv(self, filepath: str) -> int:
        """현재 필터/정렬된 테이블 모델 데이터를 CSV 파일로 내보내기"""
        return export_table_to_csv(filepath, self.proxy_model)

    def export_csv(self) -> None:
        """CSV 파일 저장 경로 선택 대화상자 실행 및 내보내기 수행"""
        self.dialogs.export_csv(self.proxy_model, self.header_bar.path_edit.text())
