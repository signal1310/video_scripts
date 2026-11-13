import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QKeyEvent
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QWidget

from src.classifier import VideoClassifier
from src.core.config import ClassifierConfig
from src.core.exception_rules import (
    exclude_exception_tokens,
    merge_exception_tokens,
)
from src.core.models import SummaryReport
from src.gui.constants import (
    WINDOW_DEFAULT_HEIGHT,
    WINDOW_DEFAULT_WIDTH,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
    browse_directory,
    get_app_settings,
)
from src.gui.dialog_coordinator import DialogCoordinator
from src.gui.exporters import export_table_to_csv
from src.gui.session_controller import (
    apply_settings_to_classifier,
    bind_classifier_directory,
    load_initial_config,
    resolve_initial_directory,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.table_model import VideoTableModel
from src.gui.task_runner import TaskRunner
from src.gui.theme import ThemeManager
from src.gui.window_layout import (
    MainWindowCallbacks,
    build_main_window_widgets,
    restore_window_ui_state,
    save_window_ui_state,
)
from src.gui.worker import ClassifierWorker


class MainWindow(QMainWindow):
    """
    - 데스크톱 GUI 메인 윈도우.
    - HeaderBar, BentoDashboard, FilterTuningBar, VideoTableView, FooterBar 컴포넌트를 조합하고
      작업 실행(TaskRunner) 및 보조 대화상자(DialogCoordinator)와 연결.
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

        self._theme_mgr = ThemeManager()
        self.setStyleSheet(build_stylesheet(self._theme_mgr.current_palette))
        self._theme_mgr.theme_changed.connect(self._on_theme_changed)

        self._classifier: VideoClassifier | None = classifier
        self._current_report: SummaryReport | None = None
        self._worker: ClassifierWorker | None = None
        self._selection_summary: tuple[int, float, float] = (0, 0.0, 0.0)
        self._ui_settings = get_app_settings()
        self.setAcceptDrops(True)

        resolved_dir = resolve_initial_directory(classifier, initial_dir)
        if self._classifier is None and resolved_dir and os.path.isdir(resolved_dir):
            self._classifier = VideoClassifier(root_dir=resolved_dir)
            self._classifier.pseudo_classify_mode(True)

        self._dialogs = DialogCoordinator(
            parent=self,
            on_status=self.set_status_text,
            on_settings_applied=self._on_settings_applied,
        )
        self._tasks = TaskRunner(self)

        widgets = build_main_window_widgets(
            window=self,
            initial_dir=resolved_dir,
            callbacks=MainWindowCallbacks(
                root_dir_provider=lambda: (
                    self._classifier.root_dir
                    if self._classifier
                    else self.header_bar.path_edit.text().strip()
                ),
                on_browse_dir=self._on_browse_dir,
                on_open_explorer=self._on_open_root_in_explorer,
                on_scan_fast=lambda: self.start_scan(include_keyframe=False),
                on_scan_full=lambda: self.start_scan(include_keyframe=True),
                on_cancel=self.cancel_active_task,
                on_open_settings=self._on_open_settings,
                on_filter_changed=self._on_filter_changed,
                on_threshold_changed=self._on_threshold_changed,
                on_append_exception=self._on_append_exception,
                on_append_exceptions=self._on_append_exceptions,
                on_remove_exceptions=self._on_remove_exceptions,
                on_selection_summary_changed=self._on_selection_summary_changed,
                on_shortcut_scan=self._on_shortcut_scan,
                on_reset_filters=self._on_reset_filters,
                on_unclassify_all=lambda: self.run_unclassify(pseudo_only=False),
                on_real_classify=lambda: self.run_real_classify(confirm=True),
                on_show_logs=self.show_log_dialog,
                on_export_csv=self.export_csv,
                on_focus_search=self._focus_search,
            ),
        )
        self.header_bar = widgets.header_bar
        self.dashboard = widgets.dashboard
        self.filter_bar = widgets.filter_bar
        self.table_view = widgets.table_view
        self.footer_bar = widgets.footer_bar
        self.table_model = self.table_view.table_model
        self.proxy_model = self.table_view.proxy_model

        restore_window_ui_state(self, self._ui_settings, self.table_view)
        init_cfg = load_initial_config(self._classifier)
        if init_cfg is not None:
            self.filter_bar.sync_from_config(init_cfg)

    @property
    def classifier(self) -> VideoClassifier | None:
        return self._classifier

    @property
    def current_report(self) -> SummaryReport | None:
        return self._current_report

    @property
    def active_worker(self) -> ClassifierWorker | None:
        return self._worker

    @active_worker.setter
    def active_worker(self, worker: ClassifierWorker | None) -> None:
        self._worker = worker

    def as_widget(self) -> QWidget:
        return self

    def _focus_search(self) -> None:
        self.filter_bar.search_edit.setFocus()
        self.filter_bar.search_edit.selectAll()

    def _on_shortcut_scan(self) -> None:
        if self._tasks.is_running():
            return
        self.start_scan(include_keyframe=(self.header_bar.current_scan_mode == "full"))

    def _save_ui_state(self) -> None:
        save_window_ui_state(self, self._ui_settings, self.table_view)

    def _on_append_exception(self, keyword: str) -> None:
        self._on_append_exceptions([keyword])

    def _on_append_exceptions(self, keywords: list[str]) -> None:
        merged, changed = merge_exception_tokens(
            self.filter_bar.get_exception_tokens(), keywords
        )
        if changed:
            self.filter_bar.set_exception_tokens(merged)

    def _on_remove_exceptions(self, keywords: list[str]) -> None:
        filtered = exclude_exception_tokens(
            self.filter_bar.get_exception_tokens(), keywords
        )
        if len(filtered) != len(self.filter_bar.get_exception_tokens()):
            self.filter_bar.set_exception_tokens(filtered)

    def ensure_classifier(self) -> VideoClassifier | None:
        """현재 경로 기준으로 VideoClassifier 인스턴스를 준비하고 반환."""
        target_dir = self.header_bar.path_edit.text().strip()
        if not target_dir or not os.path.isdir(target_dir):
            QMessageBox.warning(
                self, "경로 확인 필요", "유효한 작업 폴더 경로를 먼저 선택해 주세요."
            )
            return None

        was_none = self._classifier is None
        self._classifier, changed = bind_classifier_directory(
            self._classifier, target_dir
        )
        if was_none or changed:
            self.filter_bar.apply_to_classifier(self._classifier)
        return self._classifier

    def apply_filters_to_classifier(self, clf: VideoClassifier) -> None:
        self.filter_bar.apply_to_classifier(clf)

    def _on_threshold_changed(self) -> None:
        if self._tasks.is_running() or self._classifier is None:
            return
        self.filter_bar.apply_to_classifier(self._classifier)
        self._dialogs.sync_thresholds(self._classifier.config)
        if self._classifier.has_cache:
            report = self._classifier.get_summary_report(ensure_keyframe=False)
            self._update_ui_with_report(report)

    def _on_theme_changed(self, palette: ThemePalette) -> None:
        self.setStyleSheet(build_stylesheet(palette))
        vp = self.table_view.viewport()
        if vp is not None:
            vp.update()

    def _on_filter_changed(self) -> None:
        self.proxy_model.set_search_text(self.filter_bar.search_edit.text())
        self.proxy_model.set_zone_filter(
            self.filter_bar.combo_zone.currentData() or "ALL"
        )
        self.proxy_model.set_target_filter(
            self.filter_bar.combo_target.currentData() or "ALL"
        )
        self._update_filter_count_label()

    def _on_reset_filters(self) -> None:
        self.filter_bar.search_edit.clear()
        self.filter_bar.combo_zone.setCurrentIndex(0)
        self.filter_bar.combo_target.setCurrentIndex(0)

    def _on_selection_summary_changed(
        self, count: int, orig_mb: float, saved_mb: float
    ) -> None:
        self._selection_summary = (count, orig_mb, saved_mb)
        self._update_filter_count_label()

    def _update_filter_count_label(self) -> None:
        visible = self.proxy_model.rowCount()
        total = self.table_model.rowCount()
        sel_count, sel_orig, sel_saved = self._selection_summary
        if sel_count > 0:
            self.filter_bar.lbl_filter_count.setText(
                f"표시: {visible} / {total}개 · 선택 {sel_count}개 ({sel_orig:,.1f} MB, 절감 {sel_saved:+,.1f} MB)"
            )
        else:
            self.filter_bar.lbl_filter_count.setText(f"표시: {visible} / {total}개")
        self.table_view.update_empty_state(total_count=total, visible_count=visible)

    def _on_browse_dir(self) -> None:
        chosen = browse_directory(
            self, "비디오 분석 대상 작업 폴더 선택", self.header_bar.path_edit.text()
        )
        if chosen:
            self.set_working_directory(chosen)

    def _clear_analysis_view(self) -> None:
        self._current_report = None
        self._selection_summary = (0, 0.0, 0.0)
        pseudo_mode = self._classifier.is_pseudo_classify if self._classifier else True
        root_dir = self._classifier.root_dir if self._classifier else ""
        self.table_model.set_data_source([], root_dir=root_dir, pseudo_mode=pseudo_mode)
        self._update_filter_count_label()
        self.dashboard.reset()

    def set_working_directory(self, directory: str) -> None:
        """작업 루트 폴더를 전환하고 필요 시 이전 분석 뷰를 초기화."""
        norm_dir = os.path.normpath(directory)
        prev_dir = self.header_bar.path_edit.text().strip()
        dir_changed = not prev_dir or os.path.abspath(prev_dir) != os.path.abspath(norm_dir)
        self.header_bar.path_edit.setText(norm_dir)
        self._classifier, _ = bind_classifier_directory(
            self._classifier, norm_dir, persist_rootdir=True
        )
        self.filter_bar.apply_to_classifier(self._classifier)
        if dir_changed:
            self._clear_analysis_view()

    def _on_open_settings(self) -> None:
        curr_cfg = (
            self._classifier.config
            if self._classifier is not None
            else ClassifierConfig.from_env(reload_dotenv=True)
        )
        curr_root = (
            self._classifier.root_dir
            if self._classifier is not None
            else self.header_bar.path_edit.text().strip()
        )
        self._dialogs.open_settings(curr_cfg, curr_root)

    def _on_settings_applied(self, new_cfg: ClassifierConfig, new_root: str) -> None:
        self._classifier, root_changed, valid_new_root = apply_settings_to_classifier(
            self._classifier, self.header_bar.path_edit.text(), new_cfg, new_root
        )
        if valid_new_root is not None:
            self.header_bar.path_edit.setText(valid_new_root)

        self.filter_bar.sync_from_config(new_cfg)
        if self._classifier is not None:
            self.filter_bar.apply_to_classifier(self._classifier)

        if root_changed:
            self._clear_analysis_view()
        elif self._classifier is not None and self._classifier.has_cache:
            self._update_ui_with_report(
                self._classifier.get_summary_report(ensure_keyframe=False)
            )

    def _on_open_root_in_explorer(self) -> None:
        target = self.header_bar.path_edit.text().strip()
        if target and os.path.isdir(target) and hasattr(os, "startfile"):
            os.startfile(target)

    def set_busy_state(self, busy: bool, status_msg: str = "") -> None:
        self.header_bar.set_busy(busy)
        self.filter_bar.set_busy(busy)
        self.table_view.set_empty_action_enabled(not busy)
        self.footer_bar.set_busy(busy, status_msg)

    def set_progress(self, value: int) -> None:
        self.footer_bar.progress_bar.setValue(value)

    def set_status_text(self, text: str) -> None:
        self.footer_bar.lbl_status.setText(text)

    def append_log(self, line: str) -> None:
        self._dialogs.append_log(line)

    def apply_report(self, report: SummaryReport) -> None:
        self._update_ui_with_report(report)

    def start_scan(
        self, include_keyframe: bool = True, force_rescan: bool = False
    ) -> None:
        self._tasks.start_scan(
            include_keyframe=include_keyframe, force_rescan=force_rescan
        )

    def run_real_classify(self, confirm: bool = True) -> None:
        self._tasks.run_real_classify(confirm=confirm)

    def run_unclassify(self, pseudo_only: bool = False) -> None:
        self._tasks.run_unclassify(pseudo_only=pseudo_only)

    def cancel_active_task(self) -> None:
        self._tasks.cancel()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is None:
            return
        if event.key() == Qt.Key.Key_Escape and self._tasks.is_running():
            self.cancel_active_task()
            event.accept()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _extract_dropped_dir(
        event: QDragEnterEvent | QDropEvent | None,
    ) -> str | None:
        if event is None:
            return None
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                local_path = url.toLocalFile()
                if url.isLocalFile() and os.path.isdir(local_path):
                    return local_path
        return None

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if event is None:
            return
        if not self._tasks.is_running() and self._extract_dropped_dir(event) is not None:
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        if not self._tasks.is_running():
            local_path = self._extract_dropped_dir(event)
            if local_path is not None:
                self.set_working_directory(local_path)
                event.acceptProposedAction()
                return
        event.ignore()

    def closeEvent(self, event: QCloseEvent | None) -> None:
        if event is None:
            return
        self._save_ui_state()
        self._tasks.shutdown()
        self._dialogs.close_all()
        super().closeEvent(event)

    def show_log_dialog(self) -> None:
        self._dialogs.show_log_dialog()

    def export_report_to_csv(self, filepath: str) -> int:
        return export_table_to_csv(
            filepath, self.proxy_model, VideoTableModel.HEADERS
        )

    def export_csv(self) -> None:
        self._dialogs.export_csv(self.proxy_model, self.header_bar.path_edit.text())

    def _on_worker_progress(self, current: int, total: int, filename: str) -> None:
        self._tasks.handle_progress(current, total, filename)

    def _on_worker_log(self, message: str) -> None:
        self._tasks.handle_log(message)

    def _on_worker_completed(
        self,
        task_type: str,
        report: SummaryReport | None,
        moved_count: int,
        warnings: list[str],
    ) -> None:
        self._tasks.handle_completed(task_type, report, moved_count, warnings)

    def _on_worker_cancelled(self, reason: str) -> None:
        self._tasks.handle_cancelled(reason)

    def _on_worker_failed(self, error_msg: str) -> None:
        self._tasks.handle_failed(error_msg)

    def _update_ui_with_report(self, report: SummaryReport) -> None:
        self._current_report = report
        self._selection_summary = (0, 0.0, 0.0)
        pseudo_mode = self._classifier.is_pseudo_classify if self._classifier else True
        self.table_model.set_data_source(
            report.evaluations, root_dir=report.root_dir, pseudo_mode=pseudo_mode
        )
        self._update_filter_count_label()
        self.dashboard.update_from_report(report)
