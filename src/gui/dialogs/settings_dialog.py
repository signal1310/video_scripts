"""통합 설정(data/*) 조회, 검증, 저장 대화상자 모듈"""

import logging
import os
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from src.core.analysis.system_probe import detect_recommended_encode_speed
from src.core.app_state import (
    AppState,
    load_app_state,
    record_recent_roots,
    reset_app_state,
    resolve_effective_root_dir,
    save_app_state,
)
from src.core.config import (
    ClassifierConfig,
    SavingsConfig,
    ThresholdsConfig,
    merge_tuner_criteria,
)
from src.core.ui_state import UiState
from src.core.workspace_paths import (
    are_all_valid_dirs,
    normalize_root_spec,
    primary_root_dir,
)
from src.gui.constants import browse_directory
from src.gui.dialogs.criteria_tuner_panel import CriteriaTunerPanel
from src.gui.dialogs.setting_controls import (
    apply_inline_reset_button_icon,
    build_scroll_tab_shell,
)
from src.gui.dialogs.setting_row import SettingRow
from src.gui.dialogs.settings_field_bindings import CONFIG_FIELD_BINDINGS, ConfigFieldBinding
from src.gui.dialogs.settings_sections import SettingsFormFields, build_settings_form
from src.gui.dialogs.shortcuts_panel import ShortcutsPanel
from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.theme import ThemeAwareMixin, ThemeManager
from src.gui.widgets.collapsible_section import CollapsibleSection
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox
from src.gui.widgets.settings_group_card import SettingsGroupCard

_DIALOG_OUTER_MARGINS: tuple[int, int, int, int] = (12, 10, 12, 12)
_DIALOG_OUTER_SPACING: int = 8
_DIALOG_DEFAULT_WIDTH: int = 500
_DIALOG_DEFAULT_HEIGHT: int = 600
_DIALOG_MIN_WIDTH: int = 420
_DIALOG_MIN_HEIGHT: int = 380

class SettingsDialog(QDialog, ThemeAwareMixin):
    """환경 설정, 판별 기준 실시간 조정, 단축키 사용자 지정을 3탭으로 통합한 대화상자

    1탭(환경 설정)은 그룹 카드와 접이식 고급 설정을, 2탭(판별 기준)은 실시간 슬라이더를,
    3탭(단축키)은 명령별 단축키 칸을 배치
    """

    TAB_ENVIRONMENT: int = 0
    TAB_CRITERIA: int = 1
    TAB_SHORTCUTS: int = 2

    settings_applied = pyqtSignal(ClassifierConfig, str)
    preview_backend_changed = pyqtSignal(str)
    settings_reset_confirmed = pyqtSignal()
    settings_reset = pyqtSignal(object)
    criteria_changed = pyqtSignal(object, object)
    tab_changed = pyqtSignal(int)
    setting_hints_changed = pyqtSignal(bool)
    shortcuts_changed = pyqtSignal(dict)
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()

    tab_widget: QTabWidget
    chk_show_hints: QCheckBox
    criteria_panel: CriteriaTunerPanel
    scroll_area: QScrollArea
    section_advanced: CollapsibleSection

    btn_reset_defaults: QPushButton

    def __init__(
        self,
        current_config: ClassifierConfig | None = None,
        current_root_dir: str | None = None,
        app_state: AppState | None = None,
        *,
        active_tab: int | None = None,
        busy: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("환경 설정")
        self.resize(_DIALOG_DEFAULT_WIDTH, _DIALOG_DEFAULT_HEIGHT)
        self.setMinimumSize(_DIALOG_MIN_WIDTH, _DIALOG_MIN_HEIGHT)

        self._is_loading: bool = True
        base_state = (
            app_state.copy()
            if app_state is not None
            else load_app_state()
        )
        self._cfg: ClassifierConfig = (
            current_config.copy()
            if current_config is not None
            else base_state.to_classifier_config()
        )
        self._initial_root_dir = (
            current_root_dir
            or resolve_effective_root_dir()
        )
        self._app_state = base_state
        self._app_state.apply_classifier_config(self._cfg)
        self._last_saved_root_dir: str = self._initial_root_dir

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(*_DIALOG_OUTER_MARGINS)
        main_layout.setSpacing(_DIALOG_OUTER_SPACING)
        hint_bar, self.chk_show_hints = self._build_hint_bar()
        main_layout.addWidget(hint_bar)

        self.tab_widget = self._build_tab_widget()
        main_layout.addWidget(self.tab_widget, stretch=1)
        form, self.scroll_area, self.section_advanced, self.btn_reset_defaults = self._build_environment_tab()
        self._rows: dict[str, SettingRow] = form.rows
        self._cards: list[SettingsGroupCard] = form.cards
        self._bind_form(form)
        self._build_criteria_tab(busy)
        self._build_shortcuts_tab()
        self._build_history_actions()
        self._disable_button_auto_default()
        initial_tab = (
            active_tab
            if active_tab is not None
            else self._app_state.ui.layout.settings_tab
        )
        self.tab_widget.currentChanged.connect(self._on_tab_widget_changed)
        self.set_active_tab(initial_tab)

        self._load_values()
        self._connect_row_signals()
        self._is_loading = False
        self.init_theme_listener()

    def _disable_button_auto_default(self) -> None:
        """입력칸의 Enter가 다이얼로그 기본 버튼(전체 초기화 등)을 누르지 않도록 버튼의 자동 기본 설정 해제"""
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def _build_hint_bar(self) -> tuple[QFrame, QCheckBox]:
        hint_bar = QFrame(self)
        hint_bar.setObjectName("SettingsHintBar")
        hint_layout = QHBoxLayout(hint_bar)
        hint_layout.setContentsMargins(4, 2, 4, 2)
        hint_layout.setSpacing(8)
        lbl_purpose = QLabel("화질 유지, 최소 용량 자동 선별 기준을 조정합니다.")
        lbl_purpose.setObjectName("SettingDesc")
        chk_show_hints = QCheckBox("쉬운 설명 보기")
        chk_show_hints.setToolTip(
            "각 설정의 의도, 조정 방향, 실시간 기준 예시, 단축키 명령의 설명을 항목 아래에 함께 표시합니다. "
            "(끄면 항목 위에 마우스를 올렸을 때 툴팁으로만 확인합니다.)"
        )
        chk_show_hints.toggled.connect(self._on_show_hints_toggled)
        hint_layout.addWidget(lbl_purpose, stretch=1)
        hint_layout.addWidget(chk_show_hints)
        return hint_bar, chk_show_hints

    def _build_tab_widget(self) -> QTabWidget:
        tab_widget = QTabWidget(self)
        tab_widget.setObjectName("SettingsTabs")
        tab_widget.setDocumentMode(True)
        tab_bar = tab_widget.tabBar()
        if tab_bar is not None:
            tab_bar.setDrawBase(False)
        return tab_widget

    def _build_environment_tab(self) -> tuple[SettingsFormFields, QScrollArea, CollapsibleSection, QPushButton]:
        env_tab = QWidget(self.tab_widget)
        scroll_area, _content_widget, content_layout, _lbl_env_desc, btn_reset_defaults = (
            build_scroll_tab_shell(
                env_tab,
                desc_text="변경 즉시 저장, 적용됩니다. 마우스 오버 시 세부 설명을 봅니다.",
                reset_button_text="전체 초기화",
                reset_tooltip=(
                    "모든 분석 설정, 필터, 예외 규칙 및 화면 테마를 기본값으로 되돌립니다. "
                    "(작업 폴더 경로는 유지됩니다.)"
                ),
                on_reset=self._on_reset_defaults_clicked,
            )
        )
        section_advanced = CollapsibleSection(
            title="고급 설정",
            subtitle="(코덱, 스케일링, 표 서식)",
            is_expanded=False,
        )
        form = build_settings_form(
            general_layout=content_layout,
            advanced_section=section_advanced,
            on_browse_root=self._on_browse_root_dir,
        )
        content_layout.addWidget(section_advanced)

        content_layout.addStretch(1)
        self.tab_widget.addTab(env_tab, "환경 설정")
        return form, scroll_area, section_advanced, btn_reset_defaults

    def _build_criteria_tab(self, busy: bool) -> None:
        show_hints = bool(self._app_state.ui.show_setting_hints)
        self.criteria_panel = CriteriaTunerPanel(
            thresholds=self._cfg.thresholds,
            savings=self._cfg.savings,
            parent=self.tab_widget,
            config=self._cfg,
            show_hints=show_hints,
        )
        self.criteria_panel.criteria_changed.connect(self._on_criteria_panel_changed)
        self.criteria_panel.set_busy(busy)
        self.tab_widget.addTab(self.criteria_panel, "판별 기준")

    def _build_shortcuts_tab(self) -> None:
        self.shortcuts_panel = ShortcutsPanel(
            overrides=self._app_state.ui.shortcuts,
            show_hints=bool(self._app_state.ui.show_setting_hints),
            parent=self.tab_widget,
        )
        self.shortcuts_panel.shortcuts_changed.connect(self._on_shortcuts_panel_changed)
        self.tab_widget.addTab(self.shortcuts_panel, "단축키")

    def _build_history_actions(self) -> None:
        """설정 창이 활성일 때도 메인 창의 되돌리기, 다시 실행 단축키가 동작하도록 창 소유 동작을 등록"""
        self._undo_action = QAction(self)
        self._undo_action.triggered.connect(lambda _checked=False: self.undo_requested.emit())
        self._redo_action = QAction(self)
        self._redo_action.triggered.connect(lambda _checked=False: self.redo_requested.emit())
        self.addAction(self._undo_action)
        self.addAction(self._redo_action)

    def set_history_shortcuts(self, undo_keys: list[str], redo_keys: list[str]) -> None:
        """되돌리기, 다시 실행에 바인딩할 유효 단축키 목록을 반영"""
        self._undo_action.setShortcuts([QKeySequence(key) for key in undo_keys])
        self._redo_action.setShortcuts([QKeySequence(key) for key in redo_keys])

    def _on_shortcuts_panel_changed(self, overrides: dict[str, str]) -> None:
        # - 자동 저장은 대화상자가 가진 상태 사본을 기록하므로 단축키 변경을 사본에도 반영해 옛 값이 되돌아가지 않게 함
        self._app_state.ui.shortcuts = dict(overrides)
        self.shortcuts_changed.emit(dict(overrides))

    def _bind_form(self, form: SettingsFormFields) -> None:
        self.combo_theme = form.combo_theme
        self.combo_preview_backend = form.combo_preview_backend
        self.edit_root_dir = form.edit_root_dir
        self.edit_root_dir.textChanged.connect(self._on_root_dir_text_changed)
        self.btn_browse_root = form.btn_browse_root
        self.combo_encode_codec = form.combo_encode_codec
        self.combo_force_legacy_compat = form.combo_force_legacy_compat
        self.combo_max_resolution = form.combo_max_resolution
        self.combo_auto_downscale_by_bitrate = form.combo_auto_downscale_by_bitrate
        self.combo_savings_basis = form.combo_savings_basis
        self.spin_encode_speed = form.spin_encode_speed
        self.btn_detect_encode_speed = form.btn_detect_encode_speed
        self.btn_detect_encode_speed.clicked.connect(self._on_detect_encode_speed_clicked)
        self.spin_w_av1 = form.spin_w_av1
        self.spin_w_hevc = form.spin_w_hevc
        self.spin_w_h264 = form.spin_w_h264
        self.spin_w_legacy = form.spin_w_legacy
        self.spin_base_w = form.spin_base_w
        self.spin_base_h = form.spin_base_h
        self.spin_base_bitrate = form.spin_base_bitrate
        self.spin_base_fps = form.spin_base_fps
        self.spin_area_exp = form.spin_area_exp
        self.spin_fps_exp = form.spin_fps_exp
        self.spin_baseline_cq = form.spin_baseline_cq
        self.spin_kf_increase = form.spin_kf_increase
        self.spin_cui_maxlen = form.spin_cui_maxlen
        self.spin_fps_decimals = form.spin_fps_decimals
        self.combo_fps_trim_whole = form.combo_fps_trim_whole
        self.spin_duration_decimals = form.spin_duration_decimals
        self.spin_ratio_decimals = form.spin_ratio_decimals
        self.spin_keyframe_decimals = form.spin_keyframe_decimals
        self.spin_size_decimals = form.spin_size_decimals
        self.spin_after_size_decimals = form.spin_after_size_decimals
        self.spin_saved_size_decimals = form.spin_saved_size_decimals
        self.combo_size_unit = form.combo_size_unit
        self.combo_filename_align = form.combo_filename_align

    @classmethod
    def normalize_tab_index(cls, tab_index: int) -> int:
        """설정 대화상자 탭 인덱스를 유효한 TAB_ENVIRONMENT, TAB_CRITERIA, TAB_SHORTCUTS 상수로 정규화"""
        if tab_index in (cls.TAB_CRITERIA, cls.TAB_SHORTCUTS):
            return tab_index
        return cls.TAB_ENVIRONMENT

    def _on_tab_widget_changed(self, tab_index: int) -> None:
        target = self.normalize_tab_index(tab_index)
        self._app_state.ui.layout.settings_tab = target
        if not getattr(self, "_is_loading", False):
            self.tab_changed.emit(target)

    def set_active_tab(self, tab_index: int) -> None:
        """활성 탭(판별 기준 또는 환경 설정) 전환"""
        target = self.normalize_tab_index(tab_index)
        self._app_state.ui.layout.settings_tab = target
        self.tab_widget.setCurrentIndex(target)

    def set_busy(self, busy: bool) -> None:
        """백그라운드 스캔, 분석 중 판별 기준 탭 조작 잠금 동기화"""
        self.criteria_panel.set_busy(busy)

    def _on_criteria_panel_changed(
        self, thresholds: ThresholdsConfig, savings: SavingsConfig
    ) -> None:
        self.sync_criteria(thresholds, savings, sync_tuner=False)
        self.criteria_changed.emit(thresholds, savings)

    def _apply_hints_visibility(self, visible: bool) -> None:
        self.criteria_panel.set_hints_visible(visible)
        self.shortcuts_panel.set_hints_visible(visible)
        for row in self._rows.values():
            row.set_hint_visible(visible)

    def _on_show_hints_toggled(self, checked: bool) -> None:
        self._apply_hints_visibility(checked)
        self._app_state.ui.show_setting_hints = bool(checked)
        if getattr(self, "_is_loading", False):
            return
        try:
            save_app_state(self._app_state)
        except Exception:
            logging.getLogger(__name__).warning("통합 설정 파일 저장 실패", exc_info=True)
        self.setting_hints_changed.emit(bool(checked))

    def _load_values(self) -> None:
        show_hints = bool(self._app_state.ui.show_setting_hints)
        self.chk_show_hints.setChecked(show_hints)
        self._apply_hints_visibility(show_hints)

        idx = self.combo_theme.findData(ThemeManager().mode)
        if idx >= 0:
            self.combo_theme.setCurrentIndex(idx)
        idx = self.combo_preview_backend.findData(self._app_state.ui.preview_backend)
        if idx >= 0:
            self.combo_preview_backend.setCurrentIndex(idx)

        self.edit_root_dir.setText(self._initial_root_dir)
        self.edit_root_dir.setCursorPosition(0)
        self.edit_root_dir.setToolTip(self._initial_root_dir)

        for binding in CONFIG_FIELD_BINDINGS:
            target = (
                self._cfg
                if binding.section_attr is None
                else getattr(self._cfg, binding.section_attr)
            )
            val = getattr(target, binding.field_name)
            widget = getattr(self, binding.widget_attr)
            if isinstance(widget, QLineEdit):
                widget.setText(str(val))
            elif isinstance(widget, (QDoubleSpinBox, CustomIntegerSpinBox)):
                widget.setValue(val)
            elif isinstance(widget, QComboBox):
                combo_idx = widget.findData(val)
                if combo_idx >= 0:
                    widget.setCurrentIndex(combo_idx)

    def _apply_recommended_encode_speed(self, *, show_popup: bool) -> None:
        codec = str(self.combo_encode_codec.currentData() or "")
        rec = detect_recommended_encode_speed(codec)
        self.btn_detect_encode_speed.setToolTip(rec.detail_summary)
        self.spin_encode_speed.setValue(rec.recommended_speed_factor)
        if show_popup:
            QToolTip.showText(
                self.btn_detect_encode_speed.mapToGlobal(
                    self.btn_detect_encode_speed.rect().bottomLeft()
                ),
                rec.detail_summary,
                self.btn_detect_encode_speed,
            )

    def _on_detect_encode_speed_clicked(self) -> None:
        self._apply_recommended_encode_speed(show_popup=True)

    def _on_root_dir_text_changed(self, text: str) -> None:
        self.edit_root_dir.setToolTip(text)
        if getattr(self, "_is_loading", False):
            return
        normalized_input = normalize_root_spec(text)
        if (
            (not normalized_input or are_all_valid_dirs(normalized_input))
            and normalized_input != self._last_saved_root_dir
        ):
            self._auto_save()

    def _on_browse_root_dir(self) -> None:
        curr = normalize_root_spec(self.edit_root_dir.text())
        start = primary_root_dir(curr) if are_all_valid_dirs(curr) else os.path.expanduser("~")
        chosen = browse_directory(self, "작업 폴더 선택", start)
        if chosen:
            normalized = os.path.normpath(chosen)
            self.edit_root_dir.setText(normalized)
            self.edit_root_dir.setCursorPosition(0)
            if normalized != self._last_saved_root_dir:
                self._auto_save()

    def sync_root_dir(self, root_dir: str) -> None:
        """외부에서 변경된 작업 폴더 경로를 열려 있는 설정 UI에 반영"""
        normalized = normalize_root_spec(root_dir)
        if (
            normalized == self._last_saved_root_dir
            and self.edit_root_dir.text().strip() == normalized
        ):
            return
        prev_loading = self._is_loading
        self._is_loading = True
        try:
            self._initial_root_dir = normalized
            self._last_saved_root_dir = normalized
            self.edit_root_dir.setText(normalized)
            self.edit_root_dir.setCursorPosition(0)
            self.edit_root_dir.setToolTip(normalized)
        finally:
            self._is_loading = prev_loading

    def _connect_row_signals(self) -> None:
        for row in self._rows.values():
            row.value_changed.connect(self._on_setting_changed)

    def _on_setting_changed(self) -> None:
        if getattr(self, "_is_loading", False):
            return
        sender = self.sender()
        if (
            sender is self._rows.get("root_dir")
            and self.edit_root_dir.text().strip() == self._last_saved_root_dir
        ):
            return
        if sender is self._rows.get("encode.encode_codec"):
            # 목표 코덱 변경 시 추천 배속을 먼저 선반영해 2회 연속 저장, 재평가를 방지
            self._is_loading = True
            try:
                self._apply_recommended_encode_speed(show_popup=False)
            finally:
                self._is_loading = False
        self._auto_save()

    def sync_preview_settings(self, ui: UiState) -> None:
        """미리 보기에서 바뀐 설정(방식, 구간 이동 간격, 음량, 음소거, 정보 표시)을 내부 앱 상태 사본에 반영

        - 설정 저장이 이 사본 전체를 기록하므로 옛 값으로 덮어쓰지 않게 하기 위함
        """
        mine = self._app_state.ui
        mine.preview_backend = ui.preview_backend
        mine.preview_seek_seconds = ui.preview_seek_seconds
        mine.preview_volume = ui.preview_volume
        mine.preview_muted = ui.preview_muted
        mine.preview_playback_rate = ui.preview_playback_rate
        mine.preview_fullscreen = ui.preview_fullscreen
        idx = self.combo_preview_backend.findData(ui.preview_backend)
        if idx >= 0:
            self.combo_preview_backend.setCurrentIndex(idx)
        mine.preview_info = replace(
            ui.preview_info,
            summary_items=list(ui.preview_info.summary_items),
            detail_items=list(ui.preview_info.detail_items),
        )

    def sync_criteria(
        self,
        thresholds: ThresholdsConfig,
        savings: SavingsConfig,
        *,
        sync_tuner: bool = True,
    ) -> None:
        """외부 또는 튜너 탭에서 변경된 임계값, 효율 기준을 내부 상태 및 튜너 UI에 동기화"""
        self._cfg = merge_tuner_criteria(self._cfg, thresholds, savings)
        self._app_state.apply_classifier_config(self._cfg)
        if sync_tuner:
            self.criteria_panel.sync_from_config(
                self._cfg.thresholds, self._cfg.savings
            )

    def sync_config(self, cfg: ClassifierConfig) -> None:
        """되돌리기, 다시 실행으로 바뀐 환경 설정 값을 열린 입력 위젯에 반영"""
        prev_loading = self._is_loading
        self._is_loading = True
        try:
            self._cfg = cfg
            self._load_values()
        finally:
            self._is_loading = prev_loading

    def sync_preferences(self, shortcuts: Mapping[str, str], show_hints: bool) -> None:
        """되돌리기, 다시 실행으로 바뀐 단축키, 테마, 쉬운 설명 표시 여부를 열린 입력 위젯에 반영"""
        prev_loading = self._is_loading
        self._is_loading = True
        try:
            self._app_state.ui.shortcuts = dict(shortcuts)
            self.shortcuts_panel.set_overrides(shortcuts)
            self.chk_show_hints.setChecked(show_hints)
            idx = self.combo_theme.findData(ThemeManager().mode)
            if idx >= 0:
                self.combo_theme.setCurrentIndex(idx)
        finally:
            self._is_loading = prev_loading

    def reload_state(
        self,
        current_config: ClassifierConfig,
        current_root_dir: str,
        app_state: AppState | None,
        *,
        active_tab: int | None,
        busy: bool,
    ) -> None:
        """숨겨 둔 대화상자를 다시 열 때 호출자의 현재 설정, 앱 상태, 작업 폴더로 내부 사본과 입력값을 갱신

        - 생성 시점의 앱 상태 사본을 계속 쓰면 자동 저장이 최신 값을 옛 값으로 덮어쓰므로 항상 교체
        """
        self._app_state = app_state.copy() if app_state is not None else load_app_state()
        self.sync_config(current_config.copy())
        self._app_state.apply_classifier_config(self._cfg)
        self.sync_criteria(self._cfg.thresholds, self._cfg.savings)
        self.sync_root_dir(current_root_dir or resolve_effective_root_dir())
        self.sync_preferences(
            self._app_state.ui.shortcuts, bool(self._app_state.ui.show_setting_hints)
        )
        self.set_busy(busy)
        self.set_active_tab(
            active_tab if active_tab is not None else self._app_state.ui.layout.settings_tab
        )

    def _on_reset_defaults_clicked(self) -> None:
        answer = QMessageBox.question(
            self,
            "전체 초기화",
            "모든 분석 설정, 필터, 예외 규칙 및 화면 테마를 기본값으로 복원할까요?\n"
            "(현재 선택된 작업 폴더 경로는 유지됩니다.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # - 테마가 아래에서 먼저 바뀌므로 호출자가 초기화 전 상태를 캡처할 수 있게 변경 전에 알림
        self.settings_reset_confirmed.emit()
        fresh = reset_app_state(
            preserve_root_dir=True,
            preserve_recent_dirs=True,
        )
        self._is_loading = True
        try:
            if ThemeManager().mode != fresh.ui.theme_mode:
                ThemeManager().set_mode(fresh.ui.theme_mode)
            self._cfg = fresh.to_classifier_config()
            self._app_state = fresh.copy()
            self._initial_root_dir = resolve_effective_root_dir() or self._last_saved_root_dir
            self._last_saved_root_dir = self._initial_root_dir
            self._load_values()
            self.criteria_panel.set_config(self._cfg)
            self.criteria_panel.sync_from_config(
                self._cfg.thresholds, self._cfg.savings
            )
            self.shortcuts_panel.set_overrides(self._app_state.ui.shortcuts)
        finally:
            self._is_loading = False
        self.settings_reset.emit(fresh)

    def _read_binding_value(self, binding: ConfigFieldBinding) -> Any:
        widget = getattr(self, binding.widget_attr)
        if isinstance(widget, QLineEdit):
            return widget.text().strip() or binding.default_text
        if isinstance(widget, QDoubleSpinBox):
            raw = widget.value()
            return (
                round(raw, binding.round_digits)
                if binding.round_digits is not None
                else raw
            )
        if isinstance(widget, CustomIntegerSpinBox):
            return widget.value()
        if isinstance(widget, QComboBox):
            return widget.currentData()
        raise TypeError(f"지원하지 않는 설정 위젯 유형: {binding.widget_attr}")

    def _auto_save(self) -> None:
        if getattr(self, "_is_loading", False):
            return

        root_updates: dict[str, Any] = {}
        section_updates: dict[str, dict[str, Any]] = {}
        for binding in CONFIG_FIELD_BINDINGS:
            parsed = self._read_binding_value(binding)
            if binding.section_attr is None:
                root_updates[binding.field_name] = parsed
            else:
                section_updates.setdefault(binding.section_attr, {})[
                    binding.field_name
                ] = parsed

        section_objs: dict[str, Any] = {
            section_attr: replace(getattr(self._cfg, section_attr), **fields_map)
            for section_attr, fields_map in section_updates.items()
        }
        draft_cfg = replace(self._cfg, **root_updates, **section_objs)
        new_config = merge_tuner_criteria(
            draft_cfg,
            self.criteria_panel.current_thresholds(),
            self.criteria_panel.current_savings(),
        )

        root_dir_val = normalize_root_spec(self.edit_root_dir.text())
        root_dir_changed = root_dir_val != self._last_saved_root_dir
        if root_dir_changed:
            self._app_state.paths.recent_dirs = record_recent_roots(
                self._app_state.paths.recent_dirs, root_dir_val
            )

        chosen_theme = self.combo_theme.currentData()
        if chosen_theme and chosen_theme != ThemeManager().mode:
            ThemeManager().set_mode(chosen_theme)

        self._cfg = new_config
        self.criteria_panel.set_config(new_config)
        self.criteria_panel.sync_from_config(
            new_config.thresholds, new_config.savings
        )
        self._app_state.apply_classifier_config(new_config)
        self._app_state.ui.theme_mode = ThemeManager().mode
        chosen_backend = str(self.combo_preview_backend.currentData())
        if chosen_backend != self._app_state.ui.preview_backend:
            self._app_state.ui.preview_backend = chosen_backend
            self.preview_backend_changed.emit(chosen_backend)
        try:
            save_app_state(self._app_state, root_dir=root_dir_val if root_dir_changed else None)
            if root_dir_changed:
                self._last_saved_root_dir = root_dir_val
        except Exception:
            logging.getLogger(__name__).warning("통합 설정 파일 저장 실패", exc_info=True)

        self.settings_applied.emit(new_config, root_dir_val)

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 다이얼로그 스타일시트 및 아이콘 색상을 갱신"""
        self.setStyleSheet(build_stylesheet(palette))
        if hasattr(self, "combo_theme"):
            self.combo_theme.setItemIcon(
                0, get_icon("monitor", palette=palette, size=14, pad_right=6)
            )
            self.combo_theme.setItemIcon(
                1, get_icon("moon", palette=palette, size=14, pad_right=6)
            )
            self.combo_theme.setItemIcon(
                2, get_icon("sun", palette=palette, size=14, pad_right=6)
            )
        if hasattr(self, "btn_browse_root"):
            self.btn_browse_root.setIcon(get_icon("folder", palette=palette, size=14))
        if hasattr(self, "btn_reset_defaults"):
            apply_inline_reset_button_icon(self.btn_reset_defaults, palette)
