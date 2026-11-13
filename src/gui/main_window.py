import logging
import os
from datetime import datetime

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import (
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QKeyEvent,
    QKeySequence,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from src.classifier import VideoClassifier
from src.core.config import ClassifierConfig, get_root_dir, set_root_dir
from src.core.models import SummaryReport
from src.gui.constants import (
    MAX_LOG_HISTORY,
    WINDOW_DEFAULT_HEIGHT,
    WINDOW_DEFAULT_WIDTH,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
    WORKER_CANCEL_WAIT_MS,
    WORKER_TERMINATE_WAIT_MS,
    browse_directory,
    get_app_settings,
)
from src.gui.dialogs.log_dialog import LogDialog
from src.gui.dialogs.settings_dialog import SettingsDialog
from src.gui.exporters import export_table_to_csv
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.table_model import VideoTableModel
from src.gui.theme import ThemeManager
from src.gui.widgets import (
    BentoCard,
    BentoDashboard,
    FilterTuningBar,
    FooterBar,
    HeaderBar,
    VideoTableView,
    parse_exception_token,
)
from src.gui.worker import ClassifierWorker


class MainWindow(QMainWindow):
    """
    - 데스크톱 GUI 메인 윈도우.
    - HeaderBar, BentoDashboard, FilterTuningBar, VideoTableView, FooterBar 컴포넌트로 구성.
    """

    def __init__(
        self,
        classifier: VideoClassifier | None = None,
        initial_dir: str | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(
            "Video Classifier"
        )
        self.resize(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        self._theme_mgr = ThemeManager()
        self.setStyleSheet(build_stylesheet(self._theme_mgr.current_palette))
        self._theme_mgr.theme_changed.connect(self._on_theme_changed)

        self._classifier: VideoClassifier | None = classifier
        self._current_report: SummaryReport | None = None
        self._worker: ClassifierWorker | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._log_dialog: LogDialog | None = None
        self._log_history: list[str] = []
        self._selection_summary: tuple[int, float, float] = (0, 0.0, 0.0)
        self._ui_settings = get_app_settings()
        self.setAcceptDrops(True)

        resolved_dir = self._resolve_initial_dir(classifier, initial_dir)
        if self._classifier is None and resolved_dir and os.path.isdir(resolved_dir):
            self._classifier = VideoClassifier(root_dir=resolved_dir)
            self._classifier.pseudo_classify_mode(True)

        self._init_ui(resolved_dir)
        self._init_shortcuts()
        self._restore_ui_state()
        if self._classifier is not None:
            self.filter_bar.sync_from_config(self._classifier.config)
        else:
            try:
                self.filter_bar.sync_from_config(
                    ClassifierConfig.from_env(reload_dotenv=False)
                )
            except Exception:
                logging.getLogger(__name__).debug("초기 .env 설정 로드 생략", exc_info=True)

    @staticmethod
    def _resolve_initial_dir(
        classifier: VideoClassifier | None, initial_dir: str | None
    ) -> str:
        if classifier is not None:
            return classifier.root_dir
        if initial_dir and os.path.isdir(initial_dir):
            return initial_dir
        try:
            root = get_root_dir()
            if root and os.path.isdir(root):
                return root
        except Exception:
            logging.getLogger(__name__).debug(".rootdir 초기 경로 조회 생략", exc_info=True)
        return ""

    @property
    def classifier(self) -> VideoClassifier | None:
        return self._classifier

    @property
    def current_report(self) -> SummaryReport | None:
        return self._current_report

    def _init_ui(self, initial_dir: str) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(16, 14, 16, 14)
        root_layout.setSpacing(12)

        # 1. 상단 헤더 바
        self.header_bar = HeaderBar(initial_dir, self)
        self.header_bar.browse_clicked.connect(self._on_browse_dir)
        self.header_bar.open_explorer_clicked.connect(self._on_open_root_in_explorer)
        self.header_bar.scan_fast_clicked.connect(
            lambda: self.start_scan(include_keyframe=False)
        )
        self.header_bar.scan_full_clicked.connect(
            lambda: self.start_scan(include_keyframe=True)
        )
        self.header_bar.cancel_clicked.connect(self.cancel_active_task)
        self.header_bar.settings_clicked.connect(self._on_open_settings)
        root_layout.addWidget(self.header_bar)

        # 2. KPI 요약 대시보드
        self.dashboard = BentoDashboard(self)
        root_layout.addWidget(self.dashboard)

        # 3. 검색·필터 및 튜닝 바
        self.filter_bar = FilterTuningBar(self)
        self.filter_bar.filter_changed.connect(self._on_filter_changed)
        self.filter_bar.thresholds_changed.connect(self._on_threshold_changed)
        root_layout.addWidget(self.filter_bar)

        # 4. 데이터 테이블 뷰
        self.table_view = VideoTableView(
            root_dir_provider=lambda: (
                self._classifier.root_dir
                if self._classifier
                else self.path_edit.text().strip()
            ),
            exceptions_provider=self.filter_bar.get_exception_tokens,
            parent=self,
        )
        self.table_view.exception_keyword_added.connect(self._on_append_exception)
        self.table_view.exception_keywords_added.connect(self._on_append_exceptions)
        self.table_view.exception_keywords_removed.connect(self._on_remove_exceptions)
        self.table_view.selection_summary_changed.connect(
            self._on_selection_summary_changed
        )
        self.table_view.scan_requested.connect(self._on_shortcut_scan)
        self.table_view.reset_filters_requested.connect(self._on_reset_filters)
        self.table_model = self.table_view.table_model
        self.proxy_model = self.table_view.proxy_model
        root_layout.addWidget(self.table_view, stretch=1)

        # 5. 하단 진행률 및 액션 바
        self.footer_bar = FooterBar(self)
        self.footer_bar.unclassify_all_clicked.connect(
            lambda: self.run_unclassify(pseudo_only=False)
        )
        self.footer_bar.real_classify_clicked.connect(
            lambda: self.run_real_classify(confirm=True)
        )
        self.footer_bar.logs_clicked.connect(self.show_log_dialog)
        self.footer_bar.export_csv_clicked.connect(self.export_csv)
        root_layout.addWidget(self.footer_bar)

        # 하위 위젯 주요 컨트롤 별칭(Alias) 바인딩
        self.path_edit = self.header_bar.path_edit
        self.card_files = self.dashboard.card_files
        self.card_orig_size = self.dashboard.card_orig_size
        self.card_after_size = self.dashboard.card_after_size
        self.card_net_saved = self.dashboard.card_net_saved
        self.card_strategy = self.dashboard.card_strategy
        self.search_edit = self.filter_bar.search_edit
        self.combo_zone = self.filter_bar.combo_zone
        self.combo_target = self.filter_bar.combo_target
        self.lbl_filter_count = self.filter_bar.lbl_filter_count
        self.spin_over_ratio = self.filter_bar.spin_over_ratio
        self.spin_kf_limit = self.filter_bar.spin_kf_limit
        self.spin_small_mb = self.filter_bar.spin_small_mb
        self.edit_exceptions = self.filter_bar.edit_exceptions
        self.progress_bar = self.footer_bar.progress_bar
        self.lbl_status = self.footer_bar.lbl_status

    def _init_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._focus_search)
        QShortcut(QKeySequence("F5"), self).activated.connect(self._on_shortcut_scan)
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(
            self._on_open_settings
        )
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self.show_log_dialog)
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(self.export_csv)

    def _focus_search(self) -> None:
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def _on_shortcut_scan(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.start_scan(
            include_keyframe=(self.header_bar.current_scan_mode == "full")
        )

    def _restore_ui_state(self) -> None:
        geom = self._ui_settings.value("window_geometry")
        if isinstance(geom, QByteArray) and not geom.isEmpty():
            self.restoreGeometry(geom)
        hdr_state = self._ui_settings.value("table_header_state")
        header = self.table_view.horizontalHeader()
        if isinstance(hdr_state, QByteArray) and not hdr_state.isEmpty() and header is not None:
            header.restoreState(hdr_state)

    def _save_ui_state(self) -> None:
        self._ui_settings.setValue("window_geometry", self.saveGeometry())
        header = self.table_view.horizontalHeader()
        if header is not None:
            self._ui_settings.setValue("table_header_state", header.saveState())

    def _on_append_exception(self, keyword: str) -> None:
        self._on_append_exceptions([keyword])

    def _on_append_exceptions(self, keywords: list[str]) -> None:
        tokens = self.filter_bar.get_exception_tokens()
        existing_lower = {t.lower() for t in tokens}
        added = False
        for kw in keywords:
            clean = kw.strip()
            if clean and clean.lower() not in existing_lower:
                tokens.append(clean)
                existing_lower.add(clean.lower())
                added = True
        if added:
            self.filter_bar.set_exception_tokens(tokens)

    def _on_remove_exceptions(self, keywords: list[str]) -> None:
        remove_lower = {k.strip().lower() for k in keywords if k.strip()}
        if not remove_lower:
            return
        tokens = [
            t
            for t in self.filter_bar.get_exception_tokens()
            if t.lower() not in remove_lower
        ]
        self.filter_bar.set_exception_tokens(tokens)

    def _ensure_classifier(self) -> VideoClassifier | None:
        target_dir = self.path_edit.text().strip()
        if not target_dir or not os.path.isdir(target_dir):
            QMessageBox.warning(
                self, "경로 확인 필요", "유효한 작업 폴더 경로를 먼저 선택해 주세요."
            )
            return None

        if self._classifier is None:
            self._classifier = VideoClassifier(root_dir=target_dir)
            self._classifier.pseudo_classify_mode(True)
            self.filter_bar.apply_to_classifier(self._classifier)
        elif os.path.abspath(self._classifier.root_dir) != os.path.abspath(target_dir):
            self._classifier.set_root_dir(target_dir)
            self.filter_bar.apply_to_classifier(self._classifier)

        return self._classifier

    def _on_threshold_changed(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        if self._classifier is None:
            return
        self.filter_bar.apply_to_classifier(self._classifier)
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            cfg = self._classifier.config
            self._settings_dialog.sync_thresholds(
                cfg.over_bitrate_ratio,
                cfg.keyframe_threshold,
                cfg.small_footage_mb,
            )
        if self._classifier.has_cache:
            report = self._classifier.get_summary_report(ensure_keyframe=False)
            self._update_ui_with_report(report)

    def _on_theme_changed(self, palette: ThemePalette) -> None:
        self.setStyleSheet(build_stylesheet(palette))
        vp = self.table_view.viewport()
        if vp is not None:
            vp.update()

    def _on_filter_changed(self) -> None:
        self.proxy_model.set_search_text(self.search_edit.text())
        self.proxy_model.set_zone_filter(self.combo_zone.currentData() or "ALL")
        self.proxy_model.set_target_filter(self.combo_target.currentData() or "ALL")
        self._update_filter_count_label()

    def _on_reset_filters(self) -> None:
        self.search_edit.clear()
        self.combo_zone.setCurrentIndex(0)
        self.combo_target.setCurrentIndex(0)

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
            self.lbl_filter_count.setText(
                f"표시: {visible} / {total}개 · 선택 {sel_count}개 ({sel_orig:,.1f} MB, 절감 {sel_saved:+,.1f} MB)"
            )
        else:
            self.lbl_filter_count.setText(f"표시: {visible} / {total}개")
        self.table_view.update_empty_state(total_count=total, visible_count=visible)

    def _on_browse_dir(self) -> None:
        chosen = browse_directory(
            self, "비디오 분석 대상 작업 폴더 선택", self.path_edit.text()
        )
        if chosen:
            self.set_working_directory(chosen)

    def _clear_analysis_view(self) -> None:
        self._current_report = None
        self._selection_summary = (0, 0.0, 0.0)
        pseudo_mode = (
            self._classifier.is_pseudo_classify if self._classifier else True
        )
        root_dir = self._classifier.root_dir if self._classifier else ""
        self.table_model.set_data_source([], root_dir=root_dir, pseudo_mode=pseudo_mode)
        self._update_filter_count_label()
        self.dashboard.reset()

    def set_working_directory(self, directory: str) -> None:
        norm_dir = os.path.normpath(directory)
        prev_dir = self.path_edit.text().strip()
        dir_changed = (
            not prev_dir
            or os.path.abspath(prev_dir) != os.path.abspath(norm_dir)
        )
        self.path_edit.setText(norm_dir)
        try:
            set_root_dir(norm_dir)
        except Exception:
            logging.getLogger(__name__).warning(".rootdir 경로 저장 실패", exc_info=True)

        if self._classifier is None:
            self._classifier = VideoClassifier(root_dir=norm_dir)
            self._classifier.pseudo_classify_mode(True)
        else:
            self._classifier.set_root_dir(norm_dir)
        self.filter_bar.apply_to_classifier(self._classifier)
        if dir_changed:
            self._clear_analysis_view()

    def _on_open_settings(self) -> None:
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return

        curr_cfg = (
            self._classifier.config
            if self._classifier is not None
            else ClassifierConfig.from_env(reload_dotenv=True)
        )
        curr_root = (
            self._classifier.root_dir
            if self._classifier is not None
            else self.path_edit.text().strip()
        )
        self._settings_dialog = SettingsDialog(
            current_config=curr_cfg, current_root_dir=curr_root, parent=self
        )
        self._settings_dialog.setWindowModality(Qt.WindowModality.NonModal)
        self._settings_dialog.settings_applied.connect(self._on_settings_applied)
        self._settings_dialog.finished.connect(self._on_settings_dialog_finished)
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _on_settings_dialog_finished(self) -> None:
        dlg = self._settings_dialog
        self._settings_dialog = None
        if dlg is not None:
            dlg.deleteLater()

    def _on_settings_applied(self, new_cfg: ClassifierConfig, new_root: str) -> None:
        prev_root = (
            self._classifier.root_dir
            if self._classifier is not None
            else self.path_edit.text().strip()
        )
        root_changed = bool(
            new_root
            and os.path.isdir(new_root)
            and (
                not prev_root
                or os.path.abspath(prev_root) != os.path.abspath(new_root)
            )
        )

        if new_root and os.path.isdir(new_root):
            self.path_edit.setText(new_root)
            if self._classifier is not None:
                self._classifier.set_root_dir(new_root)

        if self._classifier is None and new_root and os.path.isdir(new_root):
            self._classifier = VideoClassifier(root_dir=new_root, config=new_cfg)
            self._classifier.pseudo_classify_mode(True)
        elif self._classifier is not None:
            self._classifier.set_config(new_cfg)

        self.filter_bar.sync_from_config(new_cfg)
        if self._classifier is not None:
            self.filter_bar.apply_to_classifier(self._classifier)

        if root_changed:
            self._clear_analysis_view()
        elif self._classifier is not None and self._classifier.has_cache:
            rep = self._classifier.get_summary_report(ensure_keyframe=False)
            self._update_ui_with_report(rep)

    def _on_open_root_in_explorer(self) -> None:
        target = self.path_edit.text().strip()
        if target and os.path.isdir(target) and hasattr(os, "startfile"):
            os.startfile(target)

    def _set_busy_state(self, busy: bool, status_msg: str = "") -> None:
        self.header_bar.set_busy(busy)
        self.filter_bar.set_busy(busy)
        self.table_view.set_empty_action_enabled(not busy)
        self.footer_bar.set_busy(busy, status_msg)

    def start_scan(
        self, include_keyframe: bool = True, force_rescan: bool = False
    ) -> None:
        clf = self._ensure_classifier()
        if clf is None:
            return
        self.filter_bar.apply_to_classifier(clf)
        task = (
            ClassifierWorker.TASK_SCAN_FULL
            if include_keyframe
            else ClassifierWorker.TASK_SCAN_FAST
        )
        label = "정밀 분석" if include_keyframe else "빠른 스캔"
        self._launch_worker(clf, task, force_rescan=force_rescan, label=label)

    def run_real_classify(self, confirm: bool = True) -> None:
        clf = self._ensure_classifier()
        if clf is None:
            return
        self.filter_bar.apply_to_classifier(clf)
        if confirm:
            details = ["인코딩 대상 영상들을 하위 폴더로 이동하시겠습니까?"]
            if self._current_report is not None:
                rep = self._current_report
                has_unmeasured_kf = any(
                    ev.video.keyframe_interval is None for ev in rep.evaluations
                )
                details.append("")
                details.append(
                    f"• 현재 기준 이동 예정: {rep.encoded_count:,}개 / 전체 {rep.total_count:,}개"
                )
                details.append(
                    f"• 예상 순수 절감 용량: {rep.net_saved_mb:,.1f} MB"
                )
                if has_unmeasured_kf:
                    details.append(
                        "• 키프레임 미측정 영상이 포함되어 있어 이동 전 정밀 키프레임 분석이 함께 진행됩니다."
                    )
            details.append("• 언제든지 [분류 원복] 버튼으로 되돌릴 수 있습니다.")

            ans = QMessageBox.question(
                self,
                "폴더 분류 이동 확인",
                "\n".join(details),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        self._launch_worker(
            clf, ClassifierWorker.TASK_CLASSIFY_REAL, label="폴더 분류 이동"
        )

    def run_unclassify(self, pseudo_only: bool = False) -> None:
        clf = self._ensure_classifier()
        if clf is None:
            return
        task = (
            ClassifierWorker.TASK_UNCLASSIFY_PSEUDO
            if pseudo_only
            else ClassifierWorker.TASK_UNCLASSIFY_ALL
        )
        label = "분류 초기화" if pseudo_only else "분류 원복"
        self._launch_worker(clf, task, label=label)

    def _launch_worker(
        self,
        clf: VideoClassifier,
        task_type: str,
        force_rescan: bool = False,
        label: str = "작업",
    ) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.progress_bar.setValue(0)
        self._set_busy_state(True, f"{label} 진행 중...")
        worker = ClassifierWorker(
            classifier=clf, task_type=task_type, force_rescan=force_rescan, parent=self
        )
        worker.progress_updated.connect(self._on_worker_progress)
        worker.log_emitted.connect(self._on_worker_log)
        worker.task_completed.connect(self._on_worker_completed)
        worker.task_cancelled.connect(self._on_worker_cancelled)
        worker.task_failed.connect(self._on_worker_failed)
        self._worker = worker
        worker.start()

    def cancel_active_task(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self.lbl_status.setText("작업 중단 요청 중...")

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is None:
            return
        if (
            event.key() == Qt.Key.Key_Escape
            and self._worker is not None
            and self._worker.isRunning()
        ):
            self.cancel_active_task()
            event.accept()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if event is None:
            return
        if self._worker is not None and self._worker.isRunning():
            event.ignore()
            return
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile() and os.path.isdir(url.toLocalFile()):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        if self._worker is not None and self._worker.isRunning():
            event.ignore()
            return
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                local_path = url.toLocalFile()
                if url.isLocalFile() and os.path.isdir(local_path):
                    self.set_working_directory(local_path)
                    event.acceptProposedAction()
                    return
        event.ignore()

    def closeEvent(self, event: QCloseEvent | None) -> None:
        if event is None:
            return
        self._save_ui_state()
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            if (
                hasattr(self._worker, "wait")
                and not self._worker.wait(WORKER_CANCEL_WAIT_MS)
            ):
                self._worker.terminate()
                self._worker.wait(WORKER_TERMINATE_WAIT_MS)
        if self._settings_dialog is not None:
            self._settings_dialog.close()
        if self._log_dialog is not None:
            self._log_dialog.close()
        super().closeEvent(event)

    def _append_log(self, line: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_history.append(f"[{ts}] {line}")
        if len(self._log_history) > MAX_LOG_HISTORY:
            self._log_history = self._log_history[-MAX_LOG_HISTORY:]
        if self._log_dialog is not None and self._log_dialog.isVisible():
            self._log_dialog.set_logs(self._log_history)

    def _clear_logs(self) -> None:
        self._log_history.clear()

    def show_log_dialog(self) -> None:
        if self._log_dialog is not None and self._log_dialog.isVisible():
            self._log_dialog.set_logs(self._log_history)
            self._log_dialog.raise_()
            self._log_dialog.activateWindow()
            return
        self._log_dialog = LogDialog(
            logs=self._log_history,
            on_clear=self._clear_logs,
            parent=self,
        )
        self._log_dialog.setWindowModality(Qt.WindowModality.NonModal)
        self._log_dialog.finished.connect(self._on_log_dialog_finished)
        self._log_dialog.show()

    def _on_log_dialog_finished(self) -> None:
        dlg = self._log_dialog
        self._log_dialog = None
        if dlg is not None:
            dlg.deleteLater()

    def export_report_to_csv(self, filepath: str) -> int:
        return export_table_to_csv(
            filepath, self.proxy_model, VideoTableModel.HEADERS
        )

    def export_csv(self) -> None:
        if self.proxy_model.rowCount() == 0:
            QMessageBox.information(
                self, "내보내기 안내", "내보낼 분석 결과 행이 없습니다."
            )
            return
        base_dir = self.path_edit.text().strip() or os.path.expanduser("~")
        default_path = os.path.join(base_dir, "video_analysis_report.csv")
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "분석 결과 CSV 내보내기",
            default_path,
            "CSV Files (*.csv)",
        )
        if not filepath:
            return
        try:
            count = self.export_report_to_csv(filepath)
            self.lbl_status.setText(f"CSV 내보내기 완료: {count}개 행 저장됨")
            self._append_log(f"[EXPORT] CSV 저장 완료 ({count}개 행): {filepath}")
        except Exception as exc:
            QMessageBox.critical(
                self, "내보내기 오류", f"CSV 저장 중 오류가 발생했습니다:\n{exc}"
            )

    def _on_worker_progress(self, current: int, total: int, filename: str) -> None:
        pct = round((current / max(total, 1)) * 100)
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(f"[{current}/{total}] {filename}")

    def _on_worker_log(self, message: str) -> None:
        clean_msg = message.strip()
        if clean_msg:
            self._append_log(clean_msg)
            if not clean_msg.startswith("[INFO]"):
                self.lbl_status.setText(clean_msg)

    def _on_worker_completed(
        self,
        task_type: str,
        report: SummaryReport | None,
        moved_count: int,
        warnings: list[str],
    ) -> None:
        self.progress_bar.setValue(100)
        self._set_busy_state(False)
        if report is not None:
            self._update_ui_with_report(report)

        if task_type == ClassifierWorker.TASK_CLASSIFY_PSEUDO:
            status_text = f"모의 분류 완료: {report.encoded_count if report else 0}개 대상"
        elif task_type == ClassifierWorker.TASK_CLASSIFY_REAL:
            status_text = (
                f"폴더 분류 완료: {report.encoded_count if report else 0}개 파일 이동됨"
            )
        elif task_type == ClassifierWorker.TASK_UNCLASSIFY_PSEUDO:
            status_text = "분류 표시 초기화 완료"
        elif task_type == ClassifierWorker.TASK_UNCLASSIFY_ALL:
            warn_suffix = f" (경고 {len(warnings)}건)" if warnings else ""
            status_text = (
                f"분류 원복 완료: {moved_count}개 파일 복원됨{warn_suffix}"
            )
            if warnings:
                for w in warnings:
                    self._append_log(f"[WARN] {w}")
                QMessageBox.warning(
                    self, "원복 충돌 안내", "\n".join(warnings)
                )
        else:
            status_text = (
                f"분석 완료: 총 {report.total_count if report else 0}개 영상"
            )
        self.lbl_status.setText(status_text)
        self._append_log(f"[DONE] {status_text}")

    def _on_worker_cancelled(self, reason: str) -> None:
        self.progress_bar.setValue(0)
        self._set_busy_state(False, "작업이 중단되었습니다.")
        self._append_log(f"[CANCEL] {reason}")

    def _on_worker_failed(self, error_msg: str) -> None:
        self.progress_bar.setValue(0)
        self._set_busy_state(False, f"오류 발생: {error_msg}")
        self._append_log(f"[ERROR] {error_msg}")
        QMessageBox.critical(self, "작업 오류", f"처리 중 오류가 발생했습니다:\n{error_msg}")

    def _update_ui_with_report(self, report: SummaryReport) -> None:
        self._current_report = report
        self._selection_summary = (0, 0.0, 0.0)
        pseudo_mode = (
            self._classifier.is_pseudo_classify if self._classifier else True
        )
        self.table_model.set_data_source(
            report.evaluations, root_dir=report.root_dir, pseudo_mode=pseudo_mode
        )
        self._update_filter_count_label()
        self.dashboard.update_from_report(report)


__all__ = ["BentoCard", "MainWindow", "parse_exception_token"]
