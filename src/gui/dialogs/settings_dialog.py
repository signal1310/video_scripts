"""통합 설정(data/*) 조회·검증·저장 대화상자 모듈"""

import logging
import os
from dataclasses import dataclass, replace
from typing import Any

from PyQt6.QtCore import pyqtSignal
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

from src.core.app_state import (
    AppState,
    load_app_state,
    record_recent_dir,
    reset_app_state,
    resolve_effective_root_dir,
    save_app_state,
)
from src.core.config import (
    DEFAULT_DIR_LOW_QUALITY_KEYFRAME,
    DEFAULT_DIR_STANDARD_CQ22,
    ClassifierConfig,
    EfficiencyConfig,
    ThresholdsConfig,
    merge_tuner_criteria,
)
from src.core.system_probe import detect_recommended_encode_speed
from src.gui.constants import browse_directory
from src.gui.dialogs.criteria_tuner_panel import CriteriaTunerPanel
from src.gui.dialogs.setting_controls import (
    apply_inline_reset_button_icon,
    build_scroll_tab_shell,
)
from src.gui.dialogs.setting_row import SettingRow
from src.gui.dialogs.settings_sections import build_settings_form
from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.theme import ThemeAwareMixin, ThemeManager
from src.gui.widgets.collapsible_section import CollapsibleSection
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox
from src.gui.widgets.settings_group_card import SettingsGroupCard

FLOAT_SETTING_ROUND_DIGITS: int = 4
_DIALOG_OUTER_MARGINS: tuple[int, int, int, int] = (12, 10, 12, 12)
_DIALOG_OUTER_SPACING: int = 8
_DIALOG_DEFAULT_WIDTH: int = 460
_DIALOG_DEFAULT_HEIGHT: int = 540
_DIALOG_MIN_WIDTH: int = 420
_DIALOG_MIN_HEIGHT: int = 380


@dataclass(frozen=True, slots=True)
class _ConfigFieldBinding:
    """설정 대화상자 위젯과 ClassifierConfig 필드 간의 양방향 매핑 명세"""

    widget_attr: str
    section_attr: str | None
    field_name: str
    default_text: str = ""
    round_digits: int | None = None


_CONFIG_FIELD_BINDINGS: tuple[_ConfigFieldBinding, ...] = (
    _ConfigFieldBinding(
        "edit_dir_std", None, "dir_standard_cq22", DEFAULT_DIR_STANDARD_CQ22
    ),
    _ConfigFieldBinding(
        "edit_dir_low", None, "dir_low_quality_keyframe", DEFAULT_DIR_LOW_QUALITY_KEYFRAME
    ),
    _ConfigFieldBinding("combo_encode_codec", "efficiency", "encode_codec"),
    _ConfigFieldBinding(
        "spin_encode_speed",
        "efficiency",
        "encode_speed_factor",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    _ConfigFieldBinding(
        "spin_kf_overhead",
        "thresholds",
        "keyframe_1s_overhead_rate",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    _ConfigFieldBinding(
        "spin_w_av1",
        "codec_weights",
        "av1",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    _ConfigFieldBinding(
        "spin_w_hevc",
        "codec_weights",
        "hevc_vp9",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    _ConfigFieldBinding(
        "spin_w_h264",
        "codec_weights",
        "h264",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    _ConfigFieldBinding(
        "spin_w_legacy",
        "codec_weights",
        "legacy",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    _ConfigFieldBinding("spin_base_w", "bitrate_model", "base_width"),
    _ConfigFieldBinding("spin_base_h", "bitrate_model", "base_height"),
    _ConfigFieldBinding("spin_base_bitrate", "bitrate_model", "base_target_kbps"),
    _ConfigFieldBinding(
        "spin_base_fps",
        "bitrate_model",
        "base_fps",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    _ConfigFieldBinding(
        "spin_area_exp",
        "bitrate_model",
        "area_scaling_exponent",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    _ConfigFieldBinding(
        "spin_fps_exp",
        "bitrate_model",
        "fps_scaling_exponent",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    _ConfigFieldBinding("spin_cui_maxlen", "cui", "filename_maxlen"),
    _ConfigFieldBinding("spin_fps_decimals", "display", "fps_decimals"),
    _ConfigFieldBinding("combo_fps_trim_whole", "display", "fps_trim_whole"),
    _ConfigFieldBinding("spin_duration_decimals", "display", "duration_decimals"),
    _ConfigFieldBinding("spin_ratio_decimals", "display", "ratio_decimals"),
    _ConfigFieldBinding("spin_keyframe_decimals", "display", "keyframe_decimals"),
    _ConfigFieldBinding("spin_size_decimals", "display", "size_decimals"),
    _ConfigFieldBinding("spin_after_size_decimals", "display", "after_size_decimals"),
    _ConfigFieldBinding("spin_saved_size_decimals", "display", "saved_size_decimals"),
    _ConfigFieldBinding("combo_size_unit", "display", "size_unit"),
)


class SettingsDialog(QDialog, ThemeAwareMixin):
    """환경·세부 설정과 판별 기준 실시간 조정을 2탭으로 통합한 대화상자

    1탭(환경 · 세부 설정)은 그룹 카드와 접이식 고급 설정을, 2탭(화질 · 효율 기준)은 실시간 슬라이더를 배치
    """

    TAB_ENVIRONMENT: int = 0
    TAB_CRITERIA: int = 1

    settings_applied = pyqtSignal(ClassifierConfig, str)
    settings_reset = pyqtSignal(object)
    criteria_changed = pyqtSignal(object, object)
    tab_changed = pyqtSignal(int)
    setting_hints_changed = pyqtSignal(bool)

    tab_widget: QTabWidget
    chk_show_hints: QCheckBox
    criteria_panel: CriteriaTunerPanel
    scroll_area: QScrollArea
    section_advanced: CollapsibleSection
    combo_theme: QComboBox
    edit_root_dir: QLineEdit
    btn_browse_root: QPushButton
    edit_dir_std: QLineEdit
    edit_dir_low: QLineEdit
    combo_encode_codec: QComboBox
    spin_encode_speed: QDoubleSpinBox
    btn_detect_encode_speed: QPushButton
    spin_w_av1: QDoubleSpinBox
    spin_w_hevc: QDoubleSpinBox
    spin_w_h264: QDoubleSpinBox
    spin_w_legacy: QDoubleSpinBox
    spin_base_w: CustomIntegerSpinBox
    spin_base_h: CustomIntegerSpinBox
    spin_base_bitrate: CustomIntegerSpinBox
    spin_base_fps: QDoubleSpinBox
    spin_area_exp: QDoubleSpinBox
    spin_fps_exp: QDoubleSpinBox
    spin_kf_overhead: QDoubleSpinBox
    spin_cui_maxlen: CustomIntegerSpinBox
    spin_fps_decimals: CustomIntegerSpinBox
    combo_fps_trim_whole: QComboBox
    spin_duration_decimals: CustomIntegerSpinBox
    spin_ratio_decimals: CustomIntegerSpinBox
    spin_keyframe_decimals: CustomIntegerSpinBox
    spin_size_decimals: CustomIntegerSpinBox
    spin_after_size_decimals: CustomIntegerSpinBox
    spin_saved_size_decimals: CustomIntegerSpinBox
    combo_size_unit: QComboBox

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
        self.setWindowTitle("설정 · 판별 기준")
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

        hint_bar = QFrame(self)
        hint_bar.setObjectName("SettingsHintBar")
        hint_layout = QHBoxLayout(hint_bar)
        hint_layout.setContentsMargins(4, 2, 4, 2)
        hint_layout.setSpacing(8)
        lbl_purpose = QLabel("화질 유지 · 최소 용량 자동 선별 기준을 조정합니다.")
        lbl_purpose.setObjectName("SettingDesc")
        self.chk_show_hints = QCheckBox("쉬운 설명 보기")
        self.chk_show_hints.setToolTip(
            "각 설정의 의도·조정 방향·실시간 기준 예시를 항목 아래에 함께 표시합니다. "
            "(끄면 항목 위에 마우스를 올렸을 때 툴팁으로만 확인합니다.)"
        )
        self.chk_show_hints.toggled.connect(self._on_show_hints_toggled)
        hint_layout.addWidget(lbl_purpose, stretch=1)
        hint_layout.addWidget(self.chk_show_hints)
        main_layout.addWidget(hint_bar)

        self.tab_widget = QTabWidget(self)
        self.tab_widget.setObjectName("SettingsTabs")
        self.tab_widget.setDocumentMode(True)
        tab_bar = self.tab_widget.tabBar()
        if tab_bar is not None:
            tab_bar.setDrawBase(False)

        env_tab = QWidget(self.tab_widget)
        self.scroll_area, _content_widget, content_layout, _lbl_env_desc, self.btn_reset_defaults = (
            build_scroll_tab_shell(
                env_tab,
                desc_text="변경 즉시 저장·적용됩니다. 마우스 오버 시 세부 설명을 봅니다.",
                reset_button_text="전체 초기화",
                reset_tooltip=(
                    "모든 분석 설정, 필터, 예외 규칙 및 화면 테마를 기본값으로 되돌립니다. "
                    "(작업 폴더 경로는 유지됩니다.)"
                ),
                on_reset=self._on_reset_defaults_clicked,
            )
        )
        self.section_advanced = CollapsibleSection(
            title="고급 설정",
            subtitle="(코덱 · 스케일링 · 표 서식)",
            is_expanded=False,
        )
        form = build_settings_form(
            general_layout=content_layout,
            advanced_section=self.section_advanced,
            on_browse_root=self._on_browse_root_dir,
        )
        content_layout.addWidget(self.section_advanced)

        self._rows: dict[str, SettingRow] = form.rows
        self._cards: list[SettingsGroupCard] = form.cards
        content_layout.addStretch(1)
        self.tab_widget.addTab(env_tab, "환경 · 세부 설정")

        show_hints = bool(self._app_state.ui.show_setting_hints)
        self.criteria_panel = CriteriaTunerPanel(
            thresholds=self._cfg.thresholds,
            efficiency=self._cfg.efficiency,
            parent=self.tab_widget,
            bitrate_model=self._cfg.bitrate_model,
            show_hints=show_hints,
        )
        self.criteria_panel.criteria_changed.connect(self._on_criteria_panel_changed)
        self.criteria_panel.set_busy(busy)
        self.tab_widget.addTab(self.criteria_panel, "화질 · 효율 기준")
        main_layout.addWidget(self.tab_widget, stretch=1)
        initial_tab = (
            active_tab
            if active_tab is not None
            else self._app_state.ui.layout.settings_tab
        )
        self.tab_widget.currentChanged.connect(self._on_tab_widget_changed)
        self.set_active_tab(initial_tab)
        self.combo_theme = form.combo_theme
        self.edit_root_dir = form.edit_root_dir
        self.edit_root_dir.textChanged.connect(self._on_root_dir_text_changed)
        self.btn_browse_root = form.btn_browse_root
        self.edit_dir_std = form.edit_dir_std
        self.edit_dir_low = form.edit_dir_low
        self.combo_encode_codec = form.combo_encode_codec
        self.spin_encode_speed = form.spin_encode_speed
        self.btn_detect_encode_speed = form.btn_detect_encode_speed
        self.btn_detect_encode_speed.clicked.connect(
            self._on_detect_encode_speed_clicked
        )
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
        self.spin_kf_overhead = form.spin_kf_overhead
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

        self._load_values()
        self._connect_row_signals()
        self._is_loading = False
        self.init_theme_listener()

    @classmethod
    def normalize_tab_index(cls, tab_index: int) -> int:
        """설정 대화상자 탭 인덱스를 유효한 TAB_ENVIRONMENT/TAB_CRITERIA 상수로 정규화"""
        return cls.TAB_CRITERIA if tab_index == cls.TAB_CRITERIA else cls.TAB_ENVIRONMENT

    def _on_tab_widget_changed(self, tab_index: int) -> None:
        target = self.normalize_tab_index(tab_index)
        self._app_state.ui.layout.settings_tab = target
        if not getattr(self, "_is_loading", False):
            self.tab_changed.emit(target)

    def set_active_tab(self, tab_index: int) -> None:
        """활성 탭(화질 · 효율 기준 또는 환경 · 세부 설정) 전환"""
        target = self.normalize_tab_index(tab_index)
        self._app_state.ui.layout.settings_tab = target
        self.tab_widget.setCurrentIndex(target)

    def set_busy(self, busy: bool) -> None:
        """백그라운드 스캔·분류 중 판별 기준 탭 조작 잠금 동기화"""
        self.criteria_panel.set_busy(busy)

    def _on_criteria_panel_changed(
        self, thresholds: ThresholdsConfig, efficiency: EfficiencyConfig
    ) -> None:
        self.sync_criteria(thresholds, efficiency, sync_tuner=False)
        self.criteria_changed.emit(thresholds, efficiency)

    def _apply_hints_visibility(self, visible: bool) -> None:
        self.criteria_panel.set_hints_visible(visible)
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

        self.edit_root_dir.setText(self._initial_root_dir)
        self.edit_root_dir.setCursorPosition(0)
        self.edit_root_dir.setToolTip(self._initial_root_dir)

        for binding in _CONFIG_FIELD_BINDINGS:
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
        normalized_input = text.strip()
        if (
            (not normalized_input or os.path.isdir(normalized_input))
            and normalized_input != self._last_saved_root_dir
        ):
            self._auto_save()

    def _on_browse_root_dir(self) -> None:
        curr = self.edit_root_dir.text().strip()
        start = curr if os.path.isdir(curr) else os.path.expanduser("~")
        chosen = browse_directory(self, "기본 작업 폴더 선택", start)
        if chosen:
            normalized = os.path.normpath(chosen)
            self.edit_root_dir.setText(normalized)
            self.edit_root_dir.setCursorPosition(0)
            if normalized != self._last_saved_root_dir:
                self._auto_save()

    def sync_root_dir(self, root_dir: str) -> None:
        """외부에서 변경된 작업 폴더 경로를 열려 있는 설정 UI에 반영"""
        stripped = root_dir.strip()
        normalized = os.path.normpath(stripped) if stripped else ""
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
        if sender is self._rows.get("efficiency.encode_codec"):
            # 목표 코덱 변경 시 추천 배속을 먼저 선반영해 2회 연속 저장·재평가를 방지
            self._is_loading = True
            try:
                self._apply_recommended_encode_speed(show_popup=False)
            finally:
                self._is_loading = False
        self._auto_save()

    def sync_criteria(
        self,
        thresholds: ThresholdsConfig,
        efficiency: EfficiencyConfig,
        *,
        sync_tuner: bool = True,
    ) -> None:
        """외부 또는 튜너 탭에서 변경된 임계값·효율 기준을 내부 상태 및 튜너 UI에 동기화"""
        self._cfg = merge_tuner_criteria(self._cfg, thresholds, efficiency)
        self._app_state.apply_classifier_config(self._cfg)
        if sync_tuner:
            self.criteria_panel.sync_from_config(
                self._cfg.thresholds, self._cfg.efficiency
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
            self.criteria_panel.set_bitrate_model(self._cfg.bitrate_model)
            self.criteria_panel.sync_from_config(
                self._cfg.thresholds, self._cfg.efficiency
            )
        finally:
            self._is_loading = False
        self.settings_reset.emit(fresh)

    def _read_binding_value(self, binding: _ConfigFieldBinding) -> Any:
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
        for binding in _CONFIG_FIELD_BINDINGS:
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
            self.criteria_panel.current_efficiency(),
        )

        root_dir_val = self.edit_root_dir.text().strip()
        root_dir_changed = root_dir_val != self._last_saved_root_dir
        if root_dir_changed:
            self._app_state.paths.recent_dirs = record_recent_dir(
                self._app_state.paths.recent_dirs, root_dir_val
            )

        chosen_theme = self.combo_theme.currentData()
        if chosen_theme and chosen_theme != ThemeManager().mode:
            ThemeManager().set_mode(chosen_theme)

        self._cfg = new_config
        self.criteria_panel.set_bitrate_model(new_config.bitrate_model)
        self.criteria_panel.sync_from_config(
            new_config.thresholds, new_config.efficiency
        )
        self._app_state.apply_classifier_config(new_config)
        self._app_state.ui.theme_mode = ThemeManager().mode
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
