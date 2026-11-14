"""Video Classifier Studio 데스크톱 GUI 메인 윈도우 모듈"""

import os
from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QCloseEvent,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QKeyEvent,
    QResizeEvent,
)
from PyQt6.QtWidgets import QMainWindow, QWidget

from src.classifier import VideoClassifier
from src.core.app_state import AppState
from src.core.config import ClassifierConfig
from src.core.models import SummaryReport
from src.gui.constants import (
    WINDOW_DEFAULT_HEIGHT,
    WINDOW_DEFAULT_WIDTH,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
    browse_directory,
)
from src.gui.dialog_coordinator import DialogCoordinator
from src.gui.exporters import export_table_to_csv
from src.gui.logging_bridge import GuiLogBridge
from src.gui.menu_bar import MainMenuController
from src.gui.session_controller import (
    SessionCoordinator,
    evaluate_action_availability,
    format_filter_count_text,
    order_report_evaluations,
    update_filter_exceptions,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.table_model import VideoTableModel
from src.gui.task_runner import TaskRunner
from src.gui.theme import ThemeManager
from src.gui.window_layout import (
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


class MainWindow(QMainWindow):
    """데스크톱 GUI 메인 윈도우

    HeaderBar·BentoDashboard·FilterTuningBar·VideoTableView·FooterBar 조합 및
    세션(SessionCoordinator)·작업 실행(TaskRunner)·보조 대화상자(DialogCoordinator) 연결
    """

    def __init__(
        self,
        classifier: VideoClassifier | None = None,
        initial_dir: str | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Video Classifier")
        self.resize(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.setAcceptDrops(True)

        self._theme_mgr = ThemeManager()
        self._current_report: SummaryReport | None = None
        self._worker: ClassifierWorker | None = None
        self._selection_summary: tuple[int, float, float] = (0, 0.0, 0.0)
        self._session = SessionCoordinator(classifier, initial_dir)
        self._session.apply_initial_theme(
            self._theme_mgr, has_external_classifier=(classifier is not None)
        )

        self.setStyleSheet(build_stylesheet(self._theme_mgr.current_palette))
        self._theme_mgr.theme_changed.connect(self._on_theme_changed)

        self._dialogs = DialogCoordinator(
            parent=self,
            on_status=self.set_status_text,
            on_settings_applied=self.apply_settings,
            on_settings_reset=self._on_settings_reset,
        )
        self._log_bridge = GuiLogBridge(on_log=self.append_log)
        self._log_bridge.attach()
        self._tasks = TaskRunner(self)

        win_cb = self._build_window_callbacks()
        widgets = build_main_window_widgets(
            window=self, initial_dir=self._session.initial_dir, callbacks=win_cb
        )
        self.header_bar = widgets.header_bar
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
            on_force_rescan=lambda: self._run_mode_scan(force_rescan=True),
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
            on_show_help=self._dialogs.show_help_dialog,
        )
        self._menu = MainMenuController(self, menu_cb)
        restore_window_ui_state(
            self,
            self._app_state.ui.layout.window_geometry,
            self._app_state.ui.layout.table_header_state,
            self.table_view,
        )
        self._sync_widgets_from_session()
        self._update_action_buttons()

    def _build_window_callbacks(self) -> MainWindowCallbacks:
        return MainWindowCallbacks(
            root_dir_provider=self._current_root_dir,
            on_browse_dir=self._on_browse_dir,
            on_open_explorer=self._on_open_root_in_explorer,
            on_scan_fast=lambda: self.start_scan(include_keyframe=False),
            on_scan_full=lambda: self.start_scan(include_keyframe=True),
            on_cancel=self.cancel_active_task,
            on_open_settings=self.open_settings,
            on_filter_changed=self._on_filter_changed,
            on_threshold_changed=self._on_threshold_changed,
            on_append_exception=lambda kw: update_filter_exceptions(
                self.filter_bar, [kw], add=True
            ),
            on_append_exceptions=lambda kws: update_filter_exceptions(
                self.filter_bar, kws, add=True
            ),
            on_remove_exceptions=lambda kws: update_filter_exceptions(
                self.filter_bar, kws, add=False
            ),
            on_selection_summary_changed=self._on_selection_summary_changed,
            on_shortcut_scan=lambda: self._run_mode_scan(force_rescan=False),
            on_reset_filters=self._on_reset_filters,
            on_unclassify_all=self.run_unclassify,
            on_real_classify=lambda: self.run_real_classify(confirm=True),
            on_show_logs=self.show_log_dialog,
            on_export_csv=self.export_csv,
            on_focus_search=self._focus_search,
            on_scan_mode_changed=self._on_scan_mode_changed,
            on_open_exception_editor=self._on_open_exception_editor,
        )

    @property
    def _app_state(self) -> AppState:
        return self._session.state

    @property
    def classifier(self) -> VideoClassifier | None:
        """현재 바인딩된 VideoClassifier 인스턴스 반환"""
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
            self._menu,
            self._theme_mgr.mode,
            apply_proxy_cb=self._apply_proxy_filters_from_ui,
        )

    def _reset_selection_summary(self) -> None:
        self._selection_summary = (0, 0.0, 0.0)
        self._menu.update_selection_state(0)

    def _focus_search(self) -> None:
        self.filter_bar.search_edit.setFocus()
        self.filter_bar.search_edit.selectAll()

    def _run_mode_scan(self, *, force_rescan: bool) -> None:
        if not self._tasks.is_running():
            self.start_scan(
                include_keyframe=(self.header_bar.current_scan_mode == "full"),
                force_rescan=force_rescan,
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
            self._dialogs.warn_missing_recent_dir(path)
            self._session.remove_missing_recent_dir(
                path, self._menu, self.header_bar.path_edit.text()
            )
            return
        self.set_working_directory(path)

    def _on_clear_recent_dirs(self) -> None:
        self._session.clear_recent_dirs(self._menu, self.header_bar.path_edit.text())
        self.set_status_text("최근 작업 폴더 목록을 비웠습니다.")

    def _on_open_exception_editor(self) -> None:
        if self._tasks.is_running():
            return
        prev_tokens = self.filter_bar.get_exception_tokens()
        result = self._dialogs.open_exception_editor(
            prev_tokens, self._app_state.ui.disabled_exception_tokens
        )
        if result is not None:
            self._session.apply_exception_editor_result(
                prev_tokens, result, self.filter_bar
            )

    def ensure_classifier(self) -> VideoClassifier | None:
        """현재 경로 기준으로 VideoClassifier 인스턴스를 준비하고 반환"""
        return self._session.ensure_classifier(
            self,
            self.header_bar.path_edit.text().strip(),
            self.filter_bar,
            self.table_model,
        )

    def apply_filters_to_classifier(self, clf: VideoClassifier) -> None:
        """필터 바의 임계값·예외 규칙을 분류기 인스턴스에 동기화"""
        self.filter_bar.apply_to_classifier(clf)

    def _apply_proxy_filters_from_ui(self) -> None:
        self.proxy_model.set_search_text(self.filter_bar.search_edit.text())
        self._update_filter_count_label()

    def _re_evaluate_from_cache(self) -> None:
        clf = self._session.classifier
        if clf is not None and clf.has_cache:
            self.apply_report(clf.get_summary_report(ensure_keyframe=False))

    def _on_scan_mode_changed(self, mode: str) -> None:
        self._app_state.ui.scan_mode = mode
        self._session.persist()

    def _on_threshold_changed(self) -> None:
        if not self._tasks.is_running():
            self._session.apply_thresholds(
                self.filter_bar, self.table_model, self._dialogs
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
            self._menu.sync_theme_mode(self._theme_mgr.mode)
            self._menu.refresh_icons(palette)
        if hasattr(self, "_session"):
            self._app_state.ui.theme_mode = self._theme_mgr.mode
            self._session.persist()

    def _on_column_filters_updated(self) -> None:
        self.filter_bar.set_active_column_filter_count(
            self.proxy_model.active_column_filter_count()
        )
        self._update_filter_count_label()

    def _on_filter_changed(self) -> None:
        self._apply_proxy_filters_from_ui()
        self._session.sync_filters_to_state(self.filter_bar)

    def _on_reset_filters(self) -> None:
        self.filter_bar.search_edit.clear()
        self.proxy_model.clear_all_column_filters()

    def _on_selection_summary_changed(
        self, count: int, orig_mb: float, saved_mb: float
    ) -> None:
        self._selection_summary = (count, orig_mb, saved_mb)
        self._menu.update_selection_state(count)
        self._update_filter_count_label()

    def _update_filter_count_label(self) -> None:
        visible, total = self.proxy_model.rowCount(), self.table_model.rowCount()
        self.filter_bar.lbl_filter_count.setText(
            format_filter_count_text(visible, total, self._selection_summary)
        )
        self.table_view.update_empty_state(total_count=total, visible_count=visible)

    def _on_browse_dir(self) -> None:
        if chosen := browse_directory(
            self, "작업 폴더 선택", self.header_bar.path_edit.text()
        ):
            self.set_working_directory(chosen)

    def _clear_analysis_view(self) -> None:
        self._current_report = None
        self._reset_selection_summary()
        self.proxy_model.clear_all_column_filters()
        self.table_model.set_data_source([], root_dir=self._current_root_dir())
        self._update_filter_count_label()
        self.dashboard.reset()
        self._update_action_buttons()

    def set_working_directory(self, directory: str) -> None:
        """작업 폴더를 전환하고 필요 시 이전 분석 뷰를 초기화"""
        dir_changed = self._session.switch_directory(
            directory, self.header_bar, self.filter_bar, self.table_model, self._menu
        )
        if dir_changed:
            self._clear_analysis_view()
        else:
            self._update_action_buttons()

    def open_settings(self) -> None:
        """비모달 환경설정 대화상자를 열거나 기존 창을 활성화"""
        clf = self._session.classifier
        curr_cfg = (
            clf.config if clf is not None else self._app_state.to_classifier_config()
        )
        self._dialogs.open_settings(
            curr_cfg, self._current_root_dir(), app_state=self._app_state
        )

    def apply_settings(self, new_cfg: ClassifierConfig, new_root: str) -> None:
        """변경된 설정과 작업 폴더 경로를 세션·분류기·화면 상태에 반영"""
        root_changed = self._session.apply_settings(
            new_cfg,
            new_root,
            self.header_bar,
            self.filter_bar,
            self.table_model,
            self._menu,
            self._theme_mgr.mode,
        )
        if root_changed:
            self._clear_analysis_view()
        elif self._session.classifier is not None and self._session.classifier.has_cache:
            self._re_evaluate_from_cache()
        else:
            self._update_action_buttons()

    def _on_settings_reset(self, fresh_state: AppState) -> None:
        self._session.state = fresh_state.copy()
        self._sync_widgets_from_session()
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
            self._current_report, self._current_root_dir()
        )
        self.footer_bar.set_action_availability(
            can_classify=can_classify, can_unclassify=can_unclassify
        )
        self._menu.update_action_availability(
            can_classify=can_classify, can_unclassify=can_unclassify
        )

    def set_busy_state(self, busy: bool, status_msg: str = "") -> None:
        """작업 실행 여부에 맞춰 헤더·필터·테이블·하단 상태 바 잠금 전환"""
        self.header_bar.set_busy(busy)
        self.filter_bar.set_busy(busy)
        self.table_view.set_empty_action_enabled(not busy)
        self.footer_bar.set_busy(busy, status_msg)
        self._menu.set_busy(busy)
        if not busy:
            self._update_action_buttons()

    def set_progress(self, value: int) -> None:
        """하단 진행률 표시줄 값 설정"""
        self.footer_bar.progress_bar.setValue(value)

    def set_status_text(self, text: str) -> None:
        """하단 상태 표시줄 텍스트 설정"""
        self.footer_bar.lbl_status.setText(text)

    def append_log(self, line: str) -> None:
        """대화상자 코디네이터 로그 버퍼에 한 줄 추가"""
        self._dialogs.append_log(line)

    def apply_report(self, report: SummaryReport) -> None:
        """요약 리포트를 대시보드·테이블 모델·필터 상태에 일괄 반영"""
        self._current_report = report
        self._reset_selection_summary()
        clf = self._session.classifier
        self.table_model.set_data_source(
            order_report_evaluations(report),
            root_dir=report.root_dir,
            config=clf.config if clf is not None else None,
        )
        self._update_filter_count_label()
        self.dashboard.update_from_report(report)
        self._update_action_buttons()

    def start_scan(
        self, include_keyframe: bool = True, force_rescan: bool = False
    ) -> None:
        """백그라운드 비디오 메타데이터 스캔 시작"""
        self._tasks.start_scan(
            include_keyframe=include_keyframe, force_rescan=force_rescan
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

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """ESC 키 입력 시 실행 중인 백그라운드 작업 중단"""
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
        """창 종료 시 UI 상태 저장 및 워커·로그 브리지·보조 창 정리"""
        if event is None:
            return
        self._save_ui_state()
        self._tasks.shutdown()
        self._log_bridge.detach()
        self._dialogs.close_all()
        super().closeEvent(event)

    def show_log_dialog(self) -> None:
        """비모달 작업 로그 대화상자 표시"""
        self._dialogs.show_log_dialog()

    def export_report_to_csv(self, filepath: str) -> int:
        """현재 필터/정렬된 테이블 모델 데이터를 CSV 파일로 내보내기"""
        return export_table_to_csv(filepath, self.proxy_model, VideoTableModel.HEADERS)

    def export_csv(self) -> None:
        """CSV 파일 저장 경로 선택 대화상자 실행 및 내보내기 수행"""
        self._dialogs.export_csv(self.proxy_model, self.header_bar.path_edit.text())
