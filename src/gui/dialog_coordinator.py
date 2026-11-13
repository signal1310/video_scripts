"""비모달 환경설정·로그 대화상자 수명주기 및 CSV 내보내기 흐름 조율 모듈"""

import os
from collections.abc import Callable
from datetime import datetime

from PyQt6.QtCore import QAbstractItemModel, Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QWidget

from src.core.app_state import AppState
from src.core.config import ClassifierConfig
from src.core.exceptions import format_user_error
from src.core.messages import LOG_TAG_EXPORT, format_log
from src.gui.constants import MAX_LOG_HISTORY
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
    ) -> None:
        self._parent = parent
        self._on_status = on_status
        self._on_settings_applied = on_settings_applied
        self._on_settings_reset = on_settings_reset
        self._settings_dialog: SettingsDialog | None = None
        self._log_dialog: LogDialog | None = None
        self._log_history: list[str] = []

    @property
    def settings_dialog(self) -> SettingsDialog | None:
        """열려 있거나 마지막으로 생성된 환경설정 대화상자 인스턴스 반환"""
        return self._settings_dialog

    @property
    def log_dialog(self) -> LogDialog | None:
        """열려 있거나 마지막으로 생성된 작업 로그 대화상자 인스턴스 반환"""
        return self._log_dialog

    @property
    def log_history(self) -> list[str]:
        """누적된 GUI 로그 이력 목록 반환"""
        return self._log_history

    def open_settings(
        self,
        current_config: ClassifierConfig,
        current_root_dir: str,
        app_state: AppState | None = None,
    ) -> SettingsDialog:
        """환경설정 다이얼로그를 열거나 이미 열려 있으면 전면으로 활성화"""
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return self._settings_dialog

        dlg = SettingsDialog(
            current_config=current_config,
            current_root_dir=current_root_dir,
            app_state=app_state,
            parent=self._parent,
        )
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.settings_applied.connect(self._on_settings_applied)
        if self._on_settings_reset is not None:
            dlg.settings_reset.connect(self._on_settings_reset)
        dlg.finished.connect(self._on_settings_dialog_finished)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        self._settings_dialog = dlg
        return dlg

    def _on_settings_dialog_finished(self) -> None:
        dlg = self._settings_dialog
        self._settings_dialog = None
        if dlg is not None:
            dlg.deleteLater()

    def sync_thresholds(self, cfg: ClassifierConfig) -> None:
        """열려 있는 환경설정 다이얼로그에 실시간 필터 바의 임계값 변경을 동기화"""
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.sync_thresholds(
                cfg.thresholds.over_bitrate_ratio,
                cfg.thresholds.keyframe_interval_sec,
                cfg.small_footage.size_mb,
            )

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
        """실시간 로그 보기 비모달 다이얼로그를 열거나 전면으로 활성화"""
        if self._log_dialog is not None and self._log_dialog.isVisible():
            self._log_dialog.set_logs(self._log_history)
            self._log_dialog.raise_()
            self._log_dialog.activateWindow()
            return self._log_dialog

        dlg = LogDialog(
            logs=self._log_history,
            on_clear=self.clear_logs,
            parent=self._parent,
        )
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.finished.connect(self._on_log_dialog_finished)
        dlg.show()
        self._log_dialog = dlg
        return dlg

    def _on_log_dialog_finished(self) -> None:
        dlg = self._log_dialog
        self._log_dialog = None
        if dlg is not None:
            dlg.deleteLater()

    def close_all(self) -> None:
        """열려 있는 보조 다이얼로그(설정, 로그)를 정리"""
        if self._settings_dialog is not None:
            self._settings_dialog.close()
        if self._log_dialog is not None:
            self._log_dialog.close()

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
            count = export_table_to_csv(filepath, proxy_model, VideoTableModel.HEADERS)
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
