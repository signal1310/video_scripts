"""비모달 환경설정, 로그 대화상자 수명주기 및 CSV 내보내기 흐름 조율 모듈"""

import os
from collections.abc import Callable
from datetime import datetime

from PyQt6.QtCore import QAbstractItemModel, Qt
from PyQt6.QtWidgets import QDialog, QFileDialog, QMessageBox, QWidget

from src.core.app_state import AppState
from src.core.config import ClassifierConfig, EfficiencyConfig, ThresholdsConfig
from src.core.exceptions import format_user_error
from src.core.messages import LOG_TAG_EXPORT, format_log
from src.gui.constants import MAX_LOG_HISTORY
from src.gui.dialogs.exception_rules_dialog import ExceptionRulesDialog
from src.gui.dialogs.help_dialog import HelpDialog
from src.gui.dialogs.log_dialog import LogDialog
from src.gui.dialogs.settings_dialog import SettingsDialog
from src.gui.exporters import export_table_to_csv
from src.gui.table_model import VideoTableModel


class DialogCoordinator:
    """비모달 환경설정/로그 다이얼로그 수명주기, 로그 버퍼, CSV 내보내기 흐름을 조율"""

    def __init__(
        self,
        parent: QWidget,
        on_status: Callable[[str], None],
        on_settings_applied: Callable[[ClassifierConfig, str], None],
        on_settings_reset: Callable[[AppState], None] | None = None,
        on_criteria_changed: Callable[[ThresholdsConfig, EfficiencyConfig], None]
        | None = None,
        on_settings_tab_changed: Callable[[int], None] | None = None,
        on_help_tab_changed: Callable[[int], None] | None = None,
        on_setting_hints_changed: Callable[[bool], None] | None = None,
    ) -> None:
        self._parent = parent
        self._on_status = on_status
        self._on_settings_applied = on_settings_applied
        self._on_settings_reset = on_settings_reset
        self._on_criteria_changed = on_criteria_changed
        self._on_settings_tab_changed = on_settings_tab_changed
        self._on_help_tab_changed = on_help_tab_changed
        self._on_setting_hints_changed = on_setting_hints_changed
        self._on_exceptions_applied: Callable[[list[str], list[str]], None] | None = (
            None
        )
        self._settings_dialog: SettingsDialog | None = None
        self._log_dialog: LogDialog | None = None
        self._help_dialog: HelpDialog | None = None
        self._exception_dialog: ExceptionRulesDialog | None = None
        self._log_history: list[str] = []

    def _prepare_independent_dialog(self, dlg: QDialog) -> None:
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.setWindowFlags(
            (dlg.windowFlags() & ~Qt.WindowType.Dialog) | Qt.WindowType.Window
        )
        if self._parent.isVisible():
            center = self._parent.frameGeometry().center()
            dlg.move(
                center.x() - dlg.width() // 2,
                center.y() - dlg.height() // 2,
            )

    @staticmethod
    def _present_independent_dialog(dlg: QDialog) -> None:
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    @property
    def settings_dialog(self) -> SettingsDialog | None:
        """열려 있거나 마지막으로 생성된 통합 설정 대화상자 인스턴스 반환"""
        return self._settings_dialog

    @property
    def log_dialog(self) -> LogDialog | None:
        """열려 있거나 마지막으로 생성된 작업 로그 대화상자 인스턴스 반환"""
        return self._log_dialog

    @property
    def help_dialog(self) -> HelpDialog | None:
        """열려 있거나 마지막으로 생성된 사용 가이드 대화상자 인스턴스 반환"""
        return self._help_dialog

    @property
    def exception_dialog(self) -> ExceptionRulesDialog | None:
        """열려 있거나 마지막으로 생성된 예외 규칙 편집기 대화상자 인스턴스 반환"""
        return self._exception_dialog

    @property
    def log_history(self) -> list[str]:
        """누적된 GUI 로그 이력 목록 반환"""
        return self._log_history

    def open_settings(
        self,
        current_config: ClassifierConfig,
        current_root_dir: str,
        app_state: AppState | None = None,
        *,
        active_tab: int | None = None,
        busy: bool = False,
    ) -> SettingsDialog:
        """통합 설정 대화상자를 지정 탭(미지정 시 마지막 탭)으로 열거나 활성화"""
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.sync_root_dir(current_root_dir)
            if active_tab is not None:
                self._settings_dialog.set_active_tab(active_tab)
            self._settings_dialog.set_busy(busy)
            self._present_independent_dialog(self._settings_dialog)
            return self._settings_dialog

        dlg = SettingsDialog(
            current_config=current_config,
            current_root_dir=current_root_dir,
            app_state=app_state,
            active_tab=active_tab,
            busy=busy,
            parent=None,
        )
        dlg.settings_applied.connect(self._on_settings_applied)
        if self._on_settings_reset is not None:
            dlg.settings_reset.connect(self._on_settings_reset)
        if self._on_criteria_changed is not None:
            dlg.criteria_changed.connect(self._on_criteria_changed)
        if self._on_settings_tab_changed is not None:
            dlg.tab_changed.connect(self._on_settings_tab_changed)
        if self._on_setting_hints_changed is not None:
            dlg.setting_hints_changed.connect(self._on_setting_hints_changed)
        dlg.finished.connect(self._on_settings_dialog_finished)
        self._prepare_independent_dialog(dlg)
        self._settings_dialog = dlg
        self._present_independent_dialog(dlg)
        return dlg

    def _on_settings_dialog_finished(self) -> None:
        dlg = self._settings_dialog
        self._settings_dialog = None
        if dlg is not None:
            dlg.deleteLater()

    def open_tuner(
        self,
        current_config: ClassifierConfig,
        current_root_dir: str,
        app_state: AppState | None = None,
        *,
        busy: bool = False,
    ) -> SettingsDialog:
        """통합 설정 대화상자를 판별 기준 탭으로 열거나 전환"""
        return self.open_settings(
            current_config=current_config,
            current_root_dir=current_root_dir,
            app_state=app_state,
            active_tab=SettingsDialog.TAB_CRITERIA,
            busy=busy,
        )

    def set_tuner_busy(self, busy: bool) -> None:
        """백그라운드 작업 상태에 맞춰 열려 있는 판별 기준, 예외 규칙 창 잠금을 동기화"""
        if self._settings_dialog is not None:
            self._settings_dialog.set_busy(busy)
        if self._exception_dialog is not None:
            self._exception_dialog.set_busy(busy)

    def sync_root_dir(self, root_dir: str) -> None:
        """열려 있는 통합 설정 대화상자에 최신 작업 폴더 경로를 동기화"""
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.sync_root_dir(root_dir)

    def sync_criteria(
        self, cfg: ClassifierConfig, *, sync_tuner: bool = True
    ) -> None:
        """열려 있는 통합 설정 및 사용 가이드 대화상자에 최신 임계값, 효율 기준을 동기화"""
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.sync_criteria(
                cfg.thresholds, cfg.efficiency, sync_tuner=sync_tuner
            )
        if self._help_dialog is not None and self._help_dialog.isVisible():
            self._help_dialog.update_config(cfg)

    def append_log(self, line: str) -> None:
        """타임스탬프와 함께 로그 이력에 한 줄을 추가하고 열려 있는 로그 창을 갱신"""
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_history.append(f"[{ts}] {line}")
        if len(self._log_history) > MAX_LOG_HISTORY:
            self._log_history = self._log_history[-MAX_LOG_HISTORY:]
        if self._log_dialog is not None and self._log_dialog.isVisible():
            self._log_dialog.set_logs(self._log_history)

    def clear_logs(self) -> None:
        """메모리에 보관된 실행 로그 이력을 초기화"""
        self._log_history.clear()

    def show_log_dialog(self) -> LogDialog:
        """실시간 로그 보기 독립 비모달 창을 열거나 전면으로 활성화"""
        if self._log_dialog is not None and self._log_dialog.isVisible():
            self._log_dialog.set_logs(self._log_history)
            self._present_independent_dialog(self._log_dialog)
            return self._log_dialog

        dlg = LogDialog(
            logs=self._log_history,
            on_clear=self.clear_logs,
            parent=None,
        )
        dlg.finished.connect(self._on_log_dialog_finished)
        self._prepare_independent_dialog(dlg)
        self._log_dialog = dlg
        self._present_independent_dialog(dlg)
        return dlg

    def _on_log_dialog_finished(self) -> None:
        dlg = self._log_dialog
        self._log_dialog = None
        if dlg is not None:
            dlg.deleteLater()

    def close_all(self) -> None:
        """열려 있는 보조 독립 창(통합 설정, 로그, 사용 가이드, 예외 규칙)을 정리"""
        for dlg in (
            self._settings_dialog,
            self._log_dialog,
            self._help_dialog,
            self._exception_dialog,
        ):
            if dlg is not None:
                dlg.close()

    def export_csv(self, proxy_model: QAbstractItemModel, base_dir: str) -> None:
        """현재 필터링된 테이블 결과를 파일 저장 대화상자를 통해 CSV로 내보냄"""
        if proxy_model.rowCount() == 0:
            QMessageBox.information(
                self._parent, "내보내기 안내", "내보낼 분석 결과 행이 없습니다."
            )
            return
        start_dir = base_dir.strip() or os.path.expanduser("~")
        default_path = os.path.join(start_dir, "video_analysis_report.csv")
        filepath, _ = QFileDialog.getSaveFileName(
            self._parent,
            "분석 결과 CSV 내보내기",
            default_path,
            "CSV Files (*.csv)",
        )
        if not filepath:
            return
        try:
            count = export_table_to_csv(
                filepath, proxy_model, headers=VideoTableModel.MENU_HEADERS
            )
            self._on_status(f"CSV 내보내기 완료: {count}개 행 저장됨")
            self.append_log(
                format_log(LOG_TAG_EXPORT, f"CSV 저장 완료 ({count}개 행): {filepath}")
            )
        except Exception as exc:
            QMessageBox.critical(
                self._parent,
                "내보내기 오류",
                f"CSV 저장 중 오류가 발생했습니다.\n\n{format_user_error(exc)}",
            )

    def open_exception_editor(
        self,
        active_tokens: list[str],
        disabled_tokens: list[str],
        *,
        on_applied: Callable[[list[str], list[str]], None] | None = None,
        busy: bool = False,
    ) -> ExceptionRulesDialog:
        """예외 규칙 목록 편집기 독립 비모달 창을 열거나 전면으로 활성화"""
        self._on_exceptions_applied = on_applied
        if self._exception_dialog is not None and self._exception_dialog.isVisible():
            self._exception_dialog.set_busy(busy)
            self._present_independent_dialog(self._exception_dialog)
            return self._exception_dialog

        dlg = ExceptionRulesDialog(
            active_tokens=active_tokens,
            disabled_tokens=disabled_tokens,
            parent=None,
        )
        dlg.set_busy(busy)
        dlg.rules_applied.connect(self._handle_exception_rules_applied)
        dlg.finished.connect(self._on_exception_dialog_finished)
        self._prepare_independent_dialog(dlg)
        self._exception_dialog = dlg
        self._present_independent_dialog(dlg)
        return dlg

    def _handle_exception_rules_applied(
        self, active_tokens: list[str], disabled_tokens: list[str]
    ) -> None:
        if self._on_exceptions_applied is not None:
            self._on_exceptions_applied(active_tokens, disabled_tokens)

    def _on_exception_dialog_finished(self) -> None:
        dlg = self._exception_dialog
        self._exception_dialog = None
        if dlg is not None:
            dlg.deleteLater()

    def show_help_dialog(
        self,
        config: ClassifierConfig | None = None,
        *,
        active_tab: int | None = None,
    ) -> HelpDialog:
        """사용 가이드, 고급 활용 독립 비모달 창 표시"""
        if self._help_dialog is not None and self._help_dialog.isVisible():
            if config is not None:
                self._help_dialog.update_config(config)
            if active_tab is not None:
                self._help_dialog.set_active_tab(active_tab)
            self._present_independent_dialog(self._help_dialog)
            return self._help_dialog

        initial_tab = (
            HelpDialog.TAB_GUIDE
            if active_tab is None
            else HelpDialog.normalize_tab_index(active_tab)
        )
        dlg = HelpDialog(
            config=config,
            active_tab=initial_tab,
            parent=None,
        )
        if self._on_help_tab_changed is not None:
            dlg.tab_changed.connect(self._on_help_tab_changed)
        dlg.finished.connect(self._on_help_dialog_finished)
        self._prepare_independent_dialog(dlg)
        self._help_dialog = dlg
        self._present_independent_dialog(dlg)
        return dlg

    def _on_help_dialog_finished(self) -> None:
        dlg = self._help_dialog
        self._help_dialog = None
        if dlg is not None:
            dlg.deleteLater()

    def warn_missing_recent_dir(self, path: str) -> None:
        """존재하지 않는 최근 작업 폴더 제거 안내 경고창 표시"""
        QMessageBox.warning(
            self._parent,
            "폴더를 찾을 수 없음",
            f"선택한 최근 작업 폴더가 존재하지 않아 목록에서 제거합니다:\n{path}",
        )
