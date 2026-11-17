"""비모달 환경설정, 작업 기록 창 수명주기와 로그 버퍼 조율 모듈"""

from collections.abc import Callable, Mapping

from PyQt6.QtCore import QAbstractItemModel, Qt
from PyQt6.QtWidgets import QDialog, QMessageBox, QWidget

from src.core.app_state import AppState
from src.core.config import ClassifierConfig, SavingsConfig, ThresholdsConfig
from src.core.ui_state import UiState
from src.gui.constants import MAX_LOG_HISTORY
from src.gui.dialogs.exception_rules_dialog import ExceptionRulesDialog
from src.gui.dialogs.help_dialog import HelpDialog
from src.gui.dialogs.log_dialog import LogDialog, LogTab
from src.gui.dialogs.settings_dialog import SettingsDialog
from src.gui.log_file import open_log_file


class DialogCoordinator:
    """비모달 환경설정, 작업 기록 창의 수명주기와 로그 버퍼를 조율"""

    def __init__(
        self,
        parent: QWidget,
        on_status: Callable[[str], None],
        on_settings_applied: Callable[[ClassifierConfig, str], None],
        on_settings_reset: Callable[[AppState], None] | None = None,
        on_criteria_changed: Callable[[ThresholdsConfig, SavingsConfig], None]
        | None = None,
        on_settings_tab_changed: Callable[[int], None] | None = None,
        on_help_tab_changed: Callable[[int], None] | None = None,
        on_setting_hints_changed: Callable[[bool], None] | None = None,
        on_shortcuts_changed: Callable[[dict[str, str]], None] | None = None,
        on_settings_reset_confirmed: Callable[[], None] | None = None,
        on_undo: Callable[[], None] | None = None,
        on_redo: Callable[[], None] | None = None,
        on_preview_backend_changed: Callable[[str], None] | None = None,
    ) -> None:
        self._parent = parent
        self._on_status = on_status
        self._on_settings_applied = on_settings_applied
        self._on_settings_reset = on_settings_reset
        self._on_criteria_changed = on_criteria_changed
        self._on_settings_tab_changed = on_settings_tab_changed
        self._on_help_tab_changed = on_help_tab_changed
        self._on_setting_hints_changed = on_setting_hints_changed
        self._on_shortcuts_changed = on_shortcuts_changed
        self._on_settings_reset_confirmed = on_settings_reset_confirmed
        self._on_undo = on_undo
        self._on_redo = on_redo
        self._on_preview_backend_changed = on_preview_backend_changed
        self._history_keys: tuple[list[str], list[str]] = ([], [])
        self._on_exceptions_applied: Callable[[list[str], list[str]], None] | None = (
            None
        )
        self._settings_dialog: SettingsDialog | None = None
        self._log_dialog: LogDialog | None = None
        self._help_dialog: HelpDialog | None = None
        self._exception_dialog: ExceptionRulesDialog | None = None
        self._log_history: list[str] = []
        self._busy: bool = False

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
        """열려 있거나 마지막으로 생성된 작업 기록 창 인스턴스 반환"""
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
        """통합 설정 대화상자를 지정 탭(미지정 시 마지막 탭)으로 열거나 활성화, 숨겨 둔 창은 재사용"""
        if self._settings_dialog is not None:
            self._settings_dialog.reload_state(
                current_config,
                current_root_dir,
                app_state,
                active_tab=active_tab,
                busy=busy,
            )
            self._settings_dialog.set_history_shortcuts(*self._history_keys)
            self._present_independent_dialog(self._settings_dialog)
            return self._settings_dialog

        dlg = self._create_settings_dialog(
            current_config, current_root_dir, app_state, active_tab=active_tab, busy=busy
        )
        self._prepare_independent_dialog(dlg)
        self._settings_dialog = dlg
        self._present_independent_dialog(dlg)
        return dlg

    def prewarm_settings_dialog(
        self,
        current_config: ClassifierConfig,
        current_root_dir: str,
        app_state: AppState | None = None,
    ) -> None:
        """통합 설정 대화상자를 보이지 않게 미리 만들어 첫 열기 지연을 줄임, 이미 있으면 아무 것도 하지 않음"""
        if self._settings_dialog is not None:
            return
        dlg = self._create_settings_dialog(
            current_config, current_root_dir, app_state, active_tab=None, busy=False
        )
        self._prepare_independent_dialog(dlg)
        self._settings_dialog = dlg

    def _create_settings_dialog(
        self,
        current_config: ClassifierConfig,
        current_root_dir: str,
        app_state: AppState | None,
        *,
        active_tab: int | None,
        busy: bool,
    ) -> SettingsDialog:
        """통합 설정 대화상자를 만들고 호출자 콜백을 연결, 표시와 위치 지정은 하지 않음"""
        dlg = SettingsDialog(
            current_config=current_config,
            current_root_dir=current_root_dir,
            app_state=app_state,
            active_tab=active_tab,
            busy=busy,
            parent=None,
        )
        dlg.settings_applied.connect(self._on_settings_applied)
        if self._on_preview_backend_changed is not None:
            dlg.preview_backend_changed.connect(self._on_preview_backend_changed)
        if self._on_settings_reset_confirmed is not None:
            dlg.settings_reset_confirmed.connect(self._on_settings_reset_confirmed)
        if self._on_settings_reset is not None:
            dlg.settings_reset.connect(self._on_settings_reset)
        if self._on_criteria_changed is not None:
            dlg.criteria_changed.connect(self._on_criteria_changed)
        if self._on_settings_tab_changed is not None:
            dlg.tab_changed.connect(self._on_settings_tab_changed)
        if self._on_setting_hints_changed is not None:
            dlg.setting_hints_changed.connect(self._on_setting_hints_changed)
        if self._on_shortcuts_changed is not None:
            dlg.shortcuts_changed.connect(self._on_shortcuts_changed)
        if self._on_undo is not None:
            dlg.undo_requested.connect(self._on_undo)
        if self._on_redo is not None:
            dlg.redo_requested.connect(self._on_redo)
        dlg.set_history_shortcuts(*self._history_keys)
        return dlg

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

    def sync_config(self, cfg: ClassifierConfig) -> None:
        """되돌리기, 다시 실행 결과를 열려 있는 통합 설정 대화상자의 환경 설정 입력값에 동기화"""
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.sync_config(cfg)

    def sync_history_shortcuts(self, undo_keys: list[str], redo_keys: list[str]) -> None:
        """되돌리기, 다시 실행 유효 단축키를 보관하고 열려 있는 통합 설정 대화상자에 반영"""
        self._history_keys = (list(undo_keys), list(redo_keys))
        if self._settings_dialog is not None:
            self._settings_dialog.set_history_shortcuts(*self._history_keys)

    def sync_preferences(self, shortcuts: Mapping[str, str], show_hints: bool) -> None:
        """되돌리기, 다시 실행 결과의 단축키, 테마, 쉬운 설명 표시 여부를 열려 있는 통합 설정 대화상자에 동기화"""
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.sync_preferences(shortcuts, show_hints)

    def sync_preview_settings(self, ui: UiState) -> None:
        """열려 있는 통합 설정 대화상자에 미리 보기 설정을 알려 저장이 옛 값을 쓰지 않게 함"""
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.sync_preview_settings(ui)

    def sync_criteria(
        self, cfg: ClassifierConfig, *, sync_tuner: bool = True
    ) -> None:
        """열려 있는 통합 설정 및 사용 가이드 대화상자에 최신 임계값, 효율 기준을 동기화"""
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.sync_criteria(
                cfg.thresholds, cfg.savings, sync_tuner=sync_tuner
            )
        if self._help_dialog is not None and self._help_dialog.isVisible():
            self._help_dialog.update_config(cfg)

    def append_log(self, line: str) -> None:
        """포맷된 로그 한 줄을 이력에 추가하고 열려 있는 작업 기록 창을 갱신"""
        self._log_history.append(line)
        if len(self._log_history) > MAX_LOG_HISTORY:
            self._log_history = self._log_history[-MAX_LOG_HISTORY:]
        if self._log_dialog is not None and self._log_dialog.isVisible():
            self._log_dialog.set_logs(self._log_history)

    def clear_logs(self) -> None:
        """메모리에 보관된 실행 로그 이력을 초기화"""
        self._log_history.clear()

    def set_busy(self, busy: bool) -> None:
        """분석, 분류 작업 진행 여부를 기록하고 열려 있는 작업 기록 창의 저장 동작에 반영"""
        self._busy = busy
        if self._log_dialog is not None:
            self._log_dialog.export_panel.set_busy(busy)

    def show_log_dialog(
        self,
        tab: LogTab = LogTab.LOG,
        *,
        export_model: QAbstractItemModel,
        base_dir_provider: Callable[[], str],
    ) -> LogDialog:
        """작업 기록 창을 지정 탭으로 열거나 전면으로 활성화, 숨겨 둔 창은 재사용"""
        if self._log_dialog is not None:
            dlg = self._log_dialog
            dlg.set_logs(self._log_history)
        else:
            dlg = LogDialog(
                logs=self._log_history,
                on_clear=self.clear_logs,
                on_open_log_file=lambda: open_log_file(self._parent),
                on_status=self._on_status,
                busy=self._busy,
                parent=None,
            )
            self._prepare_independent_dialog(dlg)
            self._log_dialog = dlg
        dlg.export_panel.bind_source(export_model, base_dir_provider)
        dlg.select_tab(tab)
        self._present_independent_dialog(dlg)
        return dlg

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

    def open_exception_editor(
        self,
        active_tokens: list[str],
        disabled_tokens: list[str],
        *,
        on_applied: Callable[[list[str], list[str]], None] | None = None,
        busy: bool = False,
    ) -> ExceptionRulesDialog:
        """예외 규칙 목록 편집기 독립 비모달 창을 열거나 전면으로 활성화, 숨겨 둔 창은 목록을 갱신해 재사용"""
        self._on_exceptions_applied = on_applied
        if self._exception_dialog is not None:
            self._exception_dialog.sync_tokens(active_tokens, disabled_tokens)
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
        self._prepare_independent_dialog(dlg)
        self._exception_dialog = dlg
        self._present_independent_dialog(dlg)
        return dlg

    def _handle_exception_rules_applied(
        self, active_tokens: list[str], disabled_tokens: list[str]
    ) -> None:
        if self._on_exceptions_applied is not None:
            self._on_exceptions_applied(active_tokens, disabled_tokens)

    def show_help_dialog(
        self,
        config: ClassifierConfig | None = None,
        *,
        active_tab: int | None = None,
        shortcuts: Mapping[str, str] | None = None,
    ) -> HelpDialog:
        """사용 가이드, 고급 활용 독립 비모달 창 표시, 숨겨 둔 창은 설정과 단축키를 갱신해 재사용"""
        if self._help_dialog is not None:
            if config is not None:
                self._help_dialog.update_config(config)
            if shortcuts is not None:
                self._help_dialog.update_shortcuts(shortcuts)
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
            shortcuts=shortcuts,
        )
        if self._on_help_tab_changed is not None:
            dlg.tab_changed.connect(self._on_help_tab_changed)
        self._prepare_independent_dialog(dlg)
        self._help_dialog = dlg
        self._present_independent_dialog(dlg)
        return dlg

    def sync_shortcuts(self, shortcuts: Mapping[str, str]) -> None:
        """단축키 변경을 열려 있는 사용 가이드 창의 본문과 단축키 표에 즉시 반영"""
        if self._help_dialog is not None and self._help_dialog.isVisible():
            self._help_dialog.update_shortcuts(shortcuts)

    def warn_missing_recent_dir(self, path: str) -> None:
        """존재하지 않는 최근 작업 폴더 제거 안내 경고창 표시"""
        QMessageBox.warning(
            self._parent,
            "폴더를 찾을 수 없음",
            f"선택한 최근 작업 폴더가 존재하지 않아 목록에서 제거합니다:\n{path}",
        )
