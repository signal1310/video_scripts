"""Video Classifier Studio 데스크톱 GUI 메인 윈도우 모듈"""

import logging
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QCloseEvent,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QKeyEvent,
    QResizeEvent,
)
from PyQt6.QtWidgets import QDialog, QMainWindow, QMessageBox, QWidget

from src.classifier import VideoClassifier
from src.core import app_state as app_state_mod
from src.core.app_state import (
    AppState,
    record_recent_dir,
    save_app_state,
)
from src.core.config import (
    DEFAULT_FILTER_TARGET,
    DEFAULT_FILTER_ZONE,
    ClassifierConfig,
)
from src.core.exception_rules import (
    exclude_exception_tokens,
    merge_exception_tokens,
)
from src.core.models import SummaryReport
from src.core.sorting import natural_sort_key
from src.gui.constants import (
    WINDOW_DEFAULT_HEIGHT,
    WINDOW_DEFAULT_WIDTH,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
    browse_directory,
)
from src.gui.dialog_coordinator import DialogCoordinator
from src.gui.dialogs.exception_rules_dialog import ExceptionRulesDialog
from src.gui.exporters import export_table_to_csv
from src.gui.logging_bridge import GuiLogBridge
from src.gui.menu_bar import MainMenuController, MenuBarCallbacks
from src.gui.session_controller import (
    apply_settings_to_classifier,
    bind_classifier_directory,
    load_initial_state,
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
    export_window_ui_state,
    restore_window_ui_state,
)
from src.gui.worker import ClassifierWorker


class MainWindow(QMainWindow):
    """데스크톱 GUI 메인 윈도우

    HeaderBar·BentoDashboard·FilterTuningBar·VideoTableView·FooterBar 조합 및
    작업 실행(TaskRunner)·보조 대화상자(DialogCoordinator) 연결
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
        self._is_syncing_state: bool = False
        self._classifier: VideoClassifier | None = classifier
        self._current_report: SummaryReport | None = None
        self._worker: ClassifierWorker | None = None
        self._selection_summary: tuple[int, float, float] = (0, 0.0, 0.0)
        self._app_state: AppState = load_initial_state(classifier)

        if (
            classifier is None
            and os.path.isfile(app_state_mod.get_state_path())
            and self._app_state.ui.theme_mode != self._theme_mgr.mode
        ):
            self._theme_mgr.set_mode(self._app_state.ui.theme_mode)

        self.setStyleSheet(build_stylesheet(self._theme_mgr.current_palette))
        self._theme_mgr.theme_changed.connect(self._on_theme_changed)
        self.setAcceptDrops(True)

        self._dialogs = DialogCoordinator(
            parent=self,
            on_status=self.set_status_text,
            on_settings_applied=self._on_settings_applied,
            on_settings_reset=self._on_settings_reset,
        )
        self._log_bridge = GuiLogBridge(on_log=self.append_log)
        self._log_bridge.attach()

        resolved_dir = resolve_initial_directory(
            classifier, initial_dir, fallback_root=self._app_state.paths.root_dir
        )
        if self._classifier is None and resolved_dir and os.path.isdir(resolved_dir):
            self._classifier = VideoClassifier(
                root_dir=resolved_dir,
                config=self._app_state.to_classifier_config(),
            )
            if self._app_state.ui.exception_tokens:
                self._classifier.set_exception_tokens(
                    self._app_state.ui.exception_tokens
                )
            self._classifier.pseudo_classify_mode(True)

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
                on_unclassify_all=self.run_unclassify,
                on_real_classify=lambda: self.run_real_classify(confirm=True),
                on_show_logs=self.show_log_dialog,
                on_export_csv=self.export_csv,
                on_focus_search=self._focus_search,
                on_scan_mode_changed=self._on_scan_mode_changed,
                on_open_exception_editor=self._on_open_exception_editor,
            ),
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

        self._menu = MainMenuController(
            self,
            MenuBarCallbacks(
                on_browse_dir=self._on_browse_dir,
                on_select_recent_dir=self._on_select_recent_dir,
                on_clear_recent_dirs=self._on_clear_recent_dirs,
                on_open_explorer=self._on_open_root_in_explorer,
                on_export_csv=self.export_csv,
                on_close_window=self._request_close,
                on_focus_search=self._focus_search,
                on_reset_filters=self._on_reset_filters,
                on_open_exception_editor=self._on_open_exception_editor,
                on_open_settings=self._on_open_settings,
                on_set_theme_mode=self._on_set_theme_mode,
                on_reset_columns=self.table_view.reset_columns,
                on_show_logs=self.show_log_dialog,
                on_shortcut_scan=self._on_shortcut_scan,
                on_scan_fast=lambda: self.start_scan(include_keyframe=False),
                on_scan_full=lambda: self.start_scan(include_keyframe=True),
                on_force_rescan=self._on_force_rescan,
                on_cancel=self.cancel_active_task,
                on_play_selected=self._on_menu_play_selected,
                on_reveal_selected=self._on_menu_reveal_selected,
                on_copy_selected_names=self._on_menu_copy_names,
                on_copy_selected_paths=self._on_menu_copy_paths,
                on_add_selected_exception=self._on_menu_add_exception,
                on_remove_selected_exception=self._on_menu_remove_exception,
                on_real_classify=lambda: self.run_real_classify(confirm=True),
                on_unclassify_all=self.run_unclassify,
                on_show_help=self._on_show_help,
            ),
        )

        restore_window_ui_state(
            self,
            self._app_state.ui.layout.window_geometry,
            self._app_state.ui.layout.table_header_state,
            self.table_view,
        )
        self._apply_state_to_widgets(self._app_state)
        self._update_action_buttons()

    @property
    def classifier(self) -> VideoClassifier | None:
        """현재 바인딩된 VideoClassifier 인스턴스 반환"""
        return self._classifier

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

    def as_widget(self) -> QWidget:
        """호스트 위젯 자신(self) 반환"""
        return self

    def _focus_search(self) -> None:
        self.filter_bar.search_edit.setFocus()
        self.filter_bar.search_edit.selectAll()

    def _on_shortcut_scan(self) -> None:
        if self._tasks.is_running():
            return
        self.start_scan(include_keyframe=(self.header_bar.current_scan_mode == "full"))

    def _request_close(self) -> None:
        self.close()

    def _on_force_rescan(self) -> None:
        if self._tasks.is_running():
            return
        self.start_scan(
            include_keyframe=(self.header_bar.current_scan_mode == "full"),
            force_rescan=True,
        )

    def _on_set_theme_mode(self, mode: str) -> None:
        self._theme_mgr.set_mode(mode)

    def _on_select_recent_dir(self, path: str) -> None:
        if self._tasks.is_running():
            return
        if not os.path.isdir(path):
            QMessageBox.warning(
                self,
                "폴더를 찾을 수 없음",
                f"선택한 최근 작업 폴더가 존재하지 않아 목록에서 제거합니다:\n{path}",
            )
            norm_target = os.path.normcase(os.path.abspath(path))
            self._app_state.paths.recent_dirs = [
                d
                for d in self._app_state.paths.recent_dirs
                if os.path.normcase(os.path.abspath(d)) != norm_target
            ]
            self._persist_app_state()
            self._menu.update_recent_dirs(
                self._app_state.paths.recent_dirs,
                self.header_bar.path_edit.text().strip(),
            )
            return
        self.set_working_directory(path)

    def _on_clear_recent_dirs(self) -> None:
        self._app_state.paths.recent_dirs.clear()
        self._persist_app_state()
        self._menu.update_recent_dirs([], self.header_bar.path_edit.text().strip())
        self.set_status_text("최근 작업 폴더 목록을 비웠습니다.")

    def _on_open_exception_editor(self) -> None:
        if self._tasks.is_running():
            return
        dlg = ExceptionRulesDialog(
            active_tokens=self.filter_bar.get_exception_tokens(),
            disabled_tokens=self._app_state.ui.disabled_exception_tokens,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            active_tokens, disabled_tokens = dlg.get_tokens()
            self._app_state.ui.disabled_exception_tokens = list(disabled_tokens)
            self.filter_bar.set_disabled_exception_count(len(disabled_tokens))
            prev_tokens = self.filter_bar.get_exception_tokens()
            self.filter_bar.set_exception_tokens(active_tokens)
            if active_tokens == prev_tokens:
                self._persist_app_state()

    def _on_menu_play_selected(self) -> None:
        launched = self.table_view.play_selected()
        if launched > 0:
            self.set_status_text(f"선택한 {launched}개 영상을 재생했습니다.")

    def _on_menu_reveal_selected(self) -> None:
        self.table_view.reveal_selected()

    def _on_menu_copy_names(self) -> None:
        count = self.table_view.copy_selected_names()
        if count > 0:
            self.set_status_text(f"선택한 {count}개 파일명을 클립보드에 복사했습니다.")

    def _on_menu_copy_paths(self) -> None:
        count = self.table_view.copy_selected_paths()
        if count > 0:
            self.set_status_text(
                f"선택한 {count}개 전체 경로를 클립보드에 복사했습니다."
            )

    def _on_menu_add_exception(self) -> None:
        if self._tasks.is_running():
            return
        added = self.table_view.add_selected_to_exceptions()
        if added > 0:
            self.set_status_text(
                f"선택한 {added}개 항목을 예외 키워드에 추가했습니다."
            )

    def _on_menu_remove_exception(self) -> None:
        if self._tasks.is_running():
            return
        removed = self.table_view.remove_selected_from_exceptions()
        if removed > 0:
            self.set_status_text(
                f"선택한 {removed}개 항목을 예외 키워드에서 제거했습니다."
            )

    def _on_show_help(self) -> None:
        QMessageBox.information(
            self,
            "단축키 및 사용 안내",
            "\n".join(
                [
                    "• Ctrl+O : 작업 폴더 열기",
                    "• Ctrl+E : 테이블 CSV 내보내기",
                    "• Ctrl+Q : 애플리케이션 종료",
                    "• Ctrl+F : 파일명 검색창 포커스",
                    "• Ctrl+Shift+E : 예외 규칙 목록 편집기 열기",
                    "• Ctrl+, : 환경설정 열기",
                    "• Ctrl+L : 작업 로그 창 열기",
                    "• F5 / Shift+F5 : 현재 모드 스캔 / 캐시 무시 강제 재스캔",
                    "• Ctrl+Shift+C : 선택한 행 전체 경로 클립보드 복사",
                    "• Enter / Esc : 선택 영상 재생 / 실행 중인 작업 중단",
                    "• F1 : 단축키 · 예외 조건식 안내 보기",
                ]
            ),
        )

    def _save_ui_state(self) -> None:
        geom_b64, hdr_b64 = export_window_ui_state(self, self.table_view)
        self._app_state.ui.layout.window_geometry = geom_b64
        self._app_state.ui.layout.table_header_state = hdr_b64
        self._persist_app_state(sync_rootdir=False)

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
        """현재 경로 기준으로 VideoClassifier 인스턴스를 준비하고 반환"""
        target_dir = self.header_bar.path_edit.text().strip()
        if not target_dir or not os.path.isdir(target_dir):
            QMessageBox.warning(
                self, "경로 확인 필요", "유효한 작업 폴더를 먼저 선택해 주세요."
            )
            return None

        was_none = self._classifier is None
        self._classifier, changed = bind_classifier_directory(
            self._classifier,
            target_dir,
            config=self._app_state.to_classifier_config(),
            exception_tokens=self._app_state.ui.exception_tokens,
        )
        if was_none or changed:
            self.filter_bar.apply_to_classifier(self._classifier)
            self.table_model.set_config(self._classifier.config)
        return self._classifier

    def apply_filters_to_classifier(self, clf: VideoClassifier) -> None:
        """필터 바의 임계값·예외 규칙을 분류기 인스턴스에 동기화"""
        self.filter_bar.apply_to_classifier(clf)

    def _persist_app_state(self, *, sync_rootdir: bool = False) -> None:
        if getattr(self, "_is_syncing_state", False):
            return
        try:
            save_app_state(
                self._app_state,
                sync_rootdir=sync_rootdir,
            )
        except Exception:
            logging.getLogger(__name__).warning("통합 설정 파일 저장 실패", exc_info=True)

    def _apply_proxy_filters_from_ui(self) -> None:
        self.proxy_model.set_search_text(self.filter_bar.search_edit.text())
        self.proxy_model.set_zone_filter(
            self.filter_bar.combo_zone.currentData() or DEFAULT_FILTER_ZONE
        )
        self.proxy_model.set_target_filter(
            self.filter_bar.combo_target.currentData() or DEFAULT_FILTER_TARGET
        )
        self._update_filter_count_label()

    def _apply_state_to_widgets(self, state: AppState) -> None:
        self._is_syncing_state = True
        try:
            ui = state.ui
            flt = ui.filters
            cfg = state.to_classifier_config()
            self.header_bar.set_scan_mode(ui.scan_mode)
            self.filter_bar.sync_from_config(cfg)
            self.filter_bar.sync_filter_state(
                search=flt.search,
                zone=flt.zone,
                target=flt.target,
                exception_tokens=ui.exception_tokens,
            )
            self.filter_bar.set_disabled_exception_count(
                len(ui.disabled_exception_tokens)
            )
            self.table_model.set_config(cfg)
            self._apply_proxy_filters_from_ui()
            self._menu.sync_theme_mode(self._theme_mgr.mode)
            self._menu.update_recent_dirs(
                state.paths.recent_dirs,
                self.header_bar.path_edit.text().strip(),
            )
        finally:
            self._is_syncing_state = False

    def _re_evaluate_from_cache(self) -> None:
        if self._classifier is not None and self._classifier.has_cache:
            report = self._classifier.get_summary_report(ensure_keyframe=False)
            self._update_ui_with_report(report)

    def _on_scan_mode_changed(self, mode: str) -> None:
        self._app_state.ui.scan_mode = mode
        self._persist_app_state()

    def _on_threshold_changed(self) -> None:
        if self._tasks.is_running():
            return
        updated_cfg = self.filter_bar.apply_to_config(
            self._app_state.to_classifier_config()
        )
        tokens = self.filter_bar.get_exception_tokens()
        self._app_state.ui.exception_tokens = list(tokens)
        active_lower = {t.lower() for t in tokens}
        self._app_state.ui.disabled_exception_tokens = [
            t
            for t in self._app_state.ui.disabled_exception_tokens
            if t.lower() not in active_lower
        ]
        self.filter_bar.set_disabled_exception_count(
            len(self._app_state.ui.disabled_exception_tokens)
        )

        if self._classifier is not None:
            self.filter_bar.apply_to_classifier(self._classifier)
            effective_cfg = self._classifier.config
        else:
            effective_cfg = updated_cfg

        self._app_state.apply_classifier_config(effective_cfg)
        self.table_model.set_config(effective_cfg)
        self._dialogs.sync_thresholds(effective_cfg)
        self._persist_app_state()
        self._re_evaluate_from_cache()

    def _on_theme_changed(self, palette: ThemePalette) -> None:
        self.setStyleSheet(build_stylesheet(palette))
        vp = self.table_view.viewport()
        if vp is not None:
            vp.update()
        hdr_vp = self.table_view.filter_header.viewport()
        if hdr_vp is not None:
            hdr_vp.update()
        if hasattr(self, "_menu"):
            self._menu.sync_theme_mode(self._theme_mgr.mode)
            self._menu.refresh_icons(palette)
        if hasattr(self, "_app_state"):
            self._app_state.ui.theme_mode = self._theme_mgr.mode
            self._persist_app_state()

    def _on_column_filters_updated(self) -> None:
        self.filter_bar.set_active_column_filter_count(
            self.proxy_model.active_column_filter_count()
        )
        self._update_filter_count_label()

    def _on_filter_changed(self) -> None:
        self._apply_proxy_filters_from_ui()
        flt = self._app_state.ui.filters
        flt.search = self.filter_bar.search_edit.text()
        flt.zone = str(self.filter_bar.combo_zone.currentData() or DEFAULT_FILTER_ZONE)
        flt.target = str(
            self.filter_bar.combo_target.currentData() or DEFAULT_FILTER_TARGET
        )
        self._persist_app_state()

    def _on_reset_filters(self) -> None:
        self.filter_bar.search_edit.clear()
        self.filter_bar.combo_zone.setCurrentIndex(0)
        self.filter_bar.combo_target.setCurrentIndex(0)
        self.proxy_model.clear_all_column_filters()

    def _on_selection_summary_changed(
        self, count: int, orig_mb: float, saved_mb: float
    ) -> None:
        self._selection_summary = (count, orig_mb, saved_mb)
        self._menu.update_selection_state(count)
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
            self, "작업 폴더 선택", self.header_bar.path_edit.text()
        )
        if chosen:
            self.set_working_directory(chosen)

    def _clear_analysis_view(self) -> None:
        self._current_report = None
        self._selection_summary = (0, 0.0, 0.0)
        self._menu.update_selection_state(0)
        self.proxy_model.clear_all_column_filters()
        root_dir = self._classifier.root_dir if self._classifier else ""
        self.table_model.set_data_source([], root_dir=root_dir)
        self._update_filter_count_label()
        self.dashboard.reset()
        self._update_action_buttons()

    def set_working_directory(self, directory: str) -> None:
        """작업 폴더를 전환하고 필요 시 이전 분석 뷰를 초기화"""
        norm_dir = os.path.normpath(directory)
        prev_dir = self.header_bar.path_edit.text().strip()
        dir_changed = not prev_dir or os.path.abspath(prev_dir) != os.path.abspath(norm_dir)
        self.header_bar.path_edit.setText(norm_dir)
        self._classifier, _ = bind_classifier_directory(
            self._classifier,
            norm_dir,
            persist_rootdir=False,
            config=self._app_state.to_classifier_config(),
            exception_tokens=self._app_state.ui.exception_tokens,
        )
        self.filter_bar.apply_to_classifier(self._classifier)
        self.table_model.set_config(self._classifier.config)
        self._app_state.paths.root_dir = self._classifier.root_dir
        self._app_state.paths.recent_dirs = record_recent_dir(
            self._app_state.paths.recent_dirs, self._classifier.root_dir
        )
        self._persist_app_state(sync_rootdir=True)
        self._menu.update_recent_dirs(
            self._app_state.paths.recent_dirs, self._classifier.root_dir
        )
        if dir_changed:
            self._clear_analysis_view()
        else:
            self._update_action_buttons()

    def _on_open_settings(self) -> None:
        curr_cfg = (
            self._classifier.config
            if self._classifier is not None
            else self._app_state.to_classifier_config()
        )
        curr_root = (
            self._classifier.root_dir
            if self._classifier is not None
            else self.header_bar.path_edit.text().strip()
        )
        self._dialogs.open_settings(curr_cfg, curr_root, app_state=self._app_state)

    def _on_settings_applied(self, new_cfg: ClassifierConfig, new_root: str) -> None:
        self._classifier, root_changed, valid_new_root = apply_settings_to_classifier(
            self._classifier,
            self.header_bar.path_edit.text(),
            new_cfg,
            new_root,
            exception_tokens=self._app_state.ui.exception_tokens,
        )
        if valid_new_root is not None:
            self.header_bar.path_edit.setText(valid_new_root)
            self._app_state.paths.root_dir = valid_new_root
            self._app_state.paths.recent_dirs = record_recent_dir(
                self._app_state.paths.recent_dirs, valid_new_root
            )
        else:
            self._app_state.paths.root_dir = new_root

        self._app_state.apply_classifier_config(new_cfg)
        self._app_state.ui.theme_mode = self._theme_mgr.mode
        self._menu.sync_theme_mode(self._theme_mgr.mode)
        self._menu.update_recent_dirs(
            self._app_state.paths.recent_dirs,
            self.header_bar.path_edit.text().strip(),
        )
        self.filter_bar.sync_from_config(new_cfg)
        if self._classifier is not None:
            self.filter_bar.apply_to_classifier(self._classifier)
            self.table_model.set_config(self._classifier.config)
        else:
            self.table_model.set_config(new_cfg)

        if root_changed:
            self._clear_analysis_view()
        elif self._classifier is not None and self._classifier.has_cache:
            self._re_evaluate_from_cache()
        else:
            self._update_action_buttons()

    def _on_settings_reset(self, fresh_state: AppState) -> None:
        self._app_state = fresh_state.copy()
        self._apply_state_to_widgets(self._app_state)
        if self._classifier is not None:
            self._classifier.set_config(self._app_state.to_classifier_config())
            self._classifier.set_exception_tokens([])
        self._re_evaluate_from_cache()
        self._update_action_buttons()

    def _on_open_root_in_explorer(self) -> None:
        target = self.header_bar.path_edit.text().strip()
        if target and os.path.isdir(target) and hasattr(os, "startfile"):
            os.startfile(target)

    def _has_unclassify_candidates(self) -> bool:
        if self._current_report is not None and any(
            ev.video.moved_dirname is not None
            for ev in self._current_report.evaluations
        ):
            return True
        target = (
            self._classifier.root_dir
            if self._classifier is not None
            else self.header_bar.path_edit.text().strip()
        )
        if not target or not os.path.isdir(target):
            return False
        try:
            with os.scandir(target) as it:
                return any(entry.is_dir() for entry in it)
        except OSError:
            return False

    def _update_action_buttons(self) -> None:
        can_classify = self._current_report is not None and any(
            ev.target_dirname is not None and ev.video.moved_dirname is None
            for ev in self._current_report.evaluations
        )
        can_unclassify = self._has_unclassify_candidates()
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
        self._update_ui_with_report(report)

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

    def _sync_drop_overlay_geometry(self) -> None:
        central = self.centralWidget()
        if central is not None:
            self._drop_overlay.setGeometry(central.rect())

    def _set_drop_overlay_visible(self, visible: bool) -> None:
        if visible:
            self._sync_drop_overlay_geometry()
            self._drop_overlay.raise_()
            self._drop_overlay.show()
        else:
            self._drop_overlay.hide()

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        """창 크기 변경 시 드롭 안내 오버레이 영역 동기화"""
        super().resizeEvent(event)
        if hasattr(self, "_drop_overlay") and self._drop_overlay.isVisible():
            self._sync_drop_overlay_geometry()

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """유효한 로컬 디렉터리 드래그 진입 시 드롭 허용 및 안내 오버레이 표시"""
        if event is None:
            return
        if not self._tasks.is_running() and self._extract_dropped_dir(event) is not None:
            self._set_drop_overlay_visible(True)
            event.acceptProposedAction()
            return
        self._set_drop_overlay_visible(False)
        event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent | None) -> None:
        """드래그 포인터가 창 영역을 벗어나면 드롭 안내 오버레이 숨김"""
        self._set_drop_overlay_visible(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent | None) -> None:
        """드롭된 로컬 디렉터리로 작업 폴더 전환 및 안내 오버레이 숨김"""
        self._set_drop_overlay_visible(False)
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
        return export_table_to_csv(
            filepath, self.proxy_model, VideoTableModel.HEADERS
        )

    def export_csv(self) -> None:
        """CSV 파일 저장 경로 선택 대화상자 실행 및 내보내기 수행"""
        self._dialogs.export_csv(self.proxy_model, self.header_bar.path_edit.text())

    def _on_worker_progress(self, current: int, total: int, filename: str) -> None:
        self._tasks.handle_progress(current, total, filename)

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
        self._menu.update_selection_state(0)
        cfg = self._classifier.config if self._classifier else None
        ordered_evaluations = sorted(
            report.evaluations,
            key=lambda ev: natural_sort_key(ev.video.filename),
        )
        self.table_model.set_data_source(
            ordered_evaluations,
            root_dir=report.root_dir,
            config=cfg,
        )
        self._update_filter_count_label()
        self.dashboard.update_from_report(report)
        self._update_action_buttons()
