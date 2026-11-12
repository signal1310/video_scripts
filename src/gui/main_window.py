import os
from typing import List, Optional
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
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
from src.gui.dialogs.settings_dialog import SettingsDialog
from src.gui.theme import ThemeManager, ThemePalette, build_stylesheet
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
        classifier: Optional[VideoClassifier] = None,
        initial_dir: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(
            "Video Classifier"
        )
        self.resize(1540, 880)
        self.setMinimumSize(1180, 680)

        self._theme_mgr = ThemeManager()
        self.setStyleSheet(build_stylesheet(self._theme_mgr.current_palette))
        self._theme_mgr.theme_changed.connect(self._on_theme_changed)

        self._classifier: Optional[VideoClassifier] = classifier
        self._current_report: Optional[SummaryReport] = None
        self._worker: Optional[ClassifierWorker] = None
        self._settings_dialog: Optional[SettingsDialog] = None

        resolved_dir = self._resolve_initial_dir(classifier, initial_dir)
        if self._classifier is None and resolved_dir and os.path.isdir(resolved_dir):
            self._classifier = VideoClassifier(root_dir=resolved_dir)
            self._classifier.pseudo_classify_mode(True)

        self._init_ui(resolved_dir)
        if self._classifier is not None:
            self.filter_bar.sync_from_config(self._classifier.config)

    @staticmethod
    def _resolve_initial_dir(
        classifier: Optional[VideoClassifier], initial_dir: Optional[str]
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
            pass
        return ""

    @property
    def classifier(self) -> Optional[VideoClassifier]:
        return self._classifier

    @property
    def current_report(self) -> Optional[SummaryReport]:
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
            parent=self,
        )
        self.table_view.exception_keyword_added.connect(self._on_append_exception)
        self.table_model = self.table_view.table_model
        self.proxy_model = self.table_view.proxy_model
        root_layout.addWidget(self.table_view, stretch=1)

        # 5. 하단 진행률 및 액션 바
        self.footer_bar = FooterBar(self)
        self.footer_bar.pseudo_classify_clicked.connect(self.run_pseudo_classify)
        self.footer_bar.unmark_pseudo_clicked.connect(
            lambda: self.run_unclassify(pseudo_only=True)
        )
        self.footer_bar.unclassify_all_clicked.connect(
            lambda: self.run_unclassify(pseudo_only=False)
        )
        self.footer_bar.real_classify_clicked.connect(
            lambda: self.run_real_classify(confirm=True)
        )
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

    def _on_append_exception(self, keyword: str) -> None:
        curr = self.edit_exceptions.text().strip()
        self.edit_exceptions.setText(f"{curr}, {keyword}" if curr else keyword)

    def _ensure_classifier(self) -> Optional[VideoClassifier]:
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
        if self._classifier is None:
            return
        self.filter_bar.apply_to_classifier(self._classifier)
        if self._classifier.service.cache is not None:
            report = self._classifier.get_summary_report(ensure_keyframe=False)
            self._update_ui_with_report(report)

    def _on_theme_changed(self, palette: ThemePalette) -> None:
        self.setStyleSheet(build_stylesheet(palette))
        self.table_view.viewport().update()

    def _on_filter_changed(self) -> None:
        self.proxy_model.set_search_text(self.search_edit.text())
        self.proxy_model.set_zone_filter(self.combo_zone.currentData() or "ALL")
        self.proxy_model.set_target_filter(self.combo_target.currentData() or "ALL")
        self._update_filter_count_label()

    def _update_filter_count_label(self) -> None:
        visible = self.proxy_model.rowCount()
        total = self.table_model.rowCount()
        self.lbl_filter_count.setText(f"표시: {visible} / {total}개")

    def _on_browse_dir(self) -> None:
        curr = self.path_edit.text().strip()
        start_dir = curr if os.path.isdir(curr) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, "비디오 분석 대상 작업 폴더 선택", start_dir
        )
        if chosen:
            self.set_working_directory(chosen)

    def set_working_directory(self, directory: str) -> None:
        norm_dir = os.path.normpath(directory)
        self.path_edit.setText(norm_dir)
        try:
            set_root_dir(norm_dir)
        except Exception:
            pass

        if self._classifier is None:
            self._classifier = VideoClassifier(root_dir=norm_dir)
            self._classifier.pseudo_classify_mode(True)
        else:
            self._classifier.set_root_dir(norm_dir)
        self.filter_bar.apply_to_classifier(self._classifier)

    def _on_open_settings(self) -> None:
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return

        curr_cfg = (
            self._classifier.service.config
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
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _on_settings_applied(self, new_cfg: ClassifierConfig, new_root: str) -> None:
        if new_root and os.path.isdir(new_root):
            self.path_edit.setText(new_root)
            if self._classifier is not None:
                self._classifier.set_root_dir(new_root)

        if self._classifier is None and new_root and os.path.isdir(new_root):
            self._classifier = VideoClassifier(root_dir=new_root, config=new_cfg)
            self._classifier.pseudo_classify_mode(True)
        elif self._classifier is not None:
            self._classifier.service.config = new_cfg

        self.filter_bar.sync_from_config(new_cfg)

        if (
            self._classifier is not None
            and self._classifier.service.cache is not None
        ):
            rep = self._classifier.get_summary_report(ensure_keyframe=False)
            self._update_ui_with_report(rep)

    def _on_open_root_in_explorer(self) -> None:
        target = self.path_edit.text().strip()
        if target and os.path.isdir(target) and hasattr(os, "startfile"):
            os.startfile(target)

    def _set_busy_state(self, busy: bool, status_msg: str = "") -> None:
        self.header_bar.set_busy(busy)
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

    def run_pseudo_classify(self) -> None:
        clf = self._ensure_classifier()
        if clf is None:
            return
        self.filter_bar.apply_to_classifier(clf)
        self._launch_worker(
            clf,
            ClassifierWorker.TASK_CLASSIFY_PSEUDO,
            label="모의 분류",
        )

    def run_real_classify(self, confirm: bool = True) -> None:
        clf = self._ensure_classifier()
        if clf is None:
            return
        self.filter_bar.apply_to_classifier(clf)
        if confirm:
            ans = QMessageBox.question(
                self,
                "폴더 분류 이동 확인",
                "인코딩 대상 영상들을 하위 폴더로 이동하시겠습니까?\n\n"
                "• 언제든지 [분류 원복] 버튼으로 되돌릴 수 있습니다.",
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

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self._worker is not None and self._worker.isRunning():
                self.cancel_active_task()
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_worker_progress(self, current: int, total: int, filename: str) -> None:
        pct = int(round((current / max(total, 1)) * 100))
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(f"[{current}/{total}] {filename}")

    def _on_worker_log(self, message: str) -> None:
        clean_msg = message.strip()
        if clean_msg and not clean_msg.startswith("[INFO]"):
            self.lbl_status.setText(clean_msg)

    def _on_worker_completed(
        self,
        task_type: str,
        report: Optional[SummaryReport],
        moved_count: int,
        warnings: List[str],
    ) -> None:
        self.progress_bar.setValue(100)
        self._set_busy_state(False)
        if report is not None:
            self._update_ui_with_report(report)

        if task_type == ClassifierWorker.TASK_CLASSIFY_PSEUDO:
            self.lbl_status.setText(
                f"모의 분류 완료: {report.encoded_count if report else 0}개 대상"
            )
        elif task_type == ClassifierWorker.TASK_CLASSIFY_REAL:
            self.lbl_status.setText(
                f"폴더 분류 완료: {report.encoded_count if report else 0}개 파일 이동됨"
            )
        elif task_type == ClassifierWorker.TASK_UNCLASSIFY_PSEUDO:
            self.lbl_status.setText("분류 표시 초기화 완료")
        elif task_type == ClassifierWorker.TASK_UNCLASSIFY_ALL:
            warn_suffix = f" (경고 {len(warnings)}건)" if warnings else ""
            self.lbl_status.setText(
                f"분류 원복 완료: {moved_count}개 파일 복원됨{warn_suffix}"
            )
            if warnings:
                QMessageBox.warning(
                    self, "원복 충돌 안내", "\n".join(warnings)
                )
        else:
            self.lbl_status.setText(
                f"분석 완료: 총 {report.total_count if report else 0}개 영상"
            )

    def _on_worker_cancelled(self, reason: str) -> None:
        self.progress_bar.setValue(0)
        self._set_busy_state(False, "작업이 중단되었습니다.")

    def _on_worker_failed(self, error_msg: str) -> None:
        self.progress_bar.setValue(0)
        self._set_busy_state(False, f"오류 발생: {error_msg}")
        QMessageBox.critical(self, "작업 오류", f"처리 중 오류가 발생했습니다:\n{error_msg}")

    def _update_ui_with_report(self, report: SummaryReport) -> None:
        self._current_report = report
        pseudo_mode = (
            self._classifier.service.pseudo_classify if self._classifier else True
        )
        self.table_model.set_data_source(
            report.evaluations, root_dir=report.root_dir, pseudo_mode=pseudo_mode
        )
        self._update_filter_count_label()
        self.dashboard.update_from_report(report)


__all__ = ["MainWindow", "BentoCard", "parse_exception_token"]
