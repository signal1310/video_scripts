"""통합 설정(data/config.json)·.rootdir 조회·검증·저장 대화상자 모듈"""

import logging
import os
from dataclasses import replace

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.core.app_state import (
    AppState,
    load_app_state,
    record_recent_dir,
    reset_app_state,
    save_app_state,
)
from src.core.config import (
    DEFAULT_CONFIGS,
    ClassifierConfig,
    get_root_dir,
)
from src.gui.constants import browse_directory
from src.gui.dialogs.setting_row import SettingRow
from src.gui.dialogs.settings_sections import build_reset_card, build_settings_form
from src.gui.styles.icons import get_icon, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.theme import ThemeAwareMixin, ThemeManager
from src.gui.widgets.collapsible_section import CollapsibleSection
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox
from src.gui.widgets.settings_group_card import SettingsGroupCard


class SettingsDialog(QDialog, ThemeAwareMixin):
    """.env 및 .rootdir 설정을 조회·수정하는 환경설정 대화상자

    그룹 카드(SettingsGroupCard)와 접이식(Accordion) 고급 설정을 단일 스크롤 뷰로 배치
    """

    settings_applied = pyqtSignal(ClassifierConfig, str)
    settings_reset = pyqtSignal(object)

    scroll_area: QScrollArea
    section_advanced: CollapsibleSection
    combo_theme: QComboBox
    edit_root_dir: QLineEdit
    btn_browse_root: QPushButton
    edit_dir_std: QLineEdit
    edit_dir_low: QLineEdit
    spin_over_ratio: QDoubleSpinBox
    spin_low_ratio: QDoubleSpinBox
    spin_kf_threshold: QDoubleSpinBox
    spin_small_size: QDoubleSpinBox
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
    spin_small_min_saved: QDoubleSpinBox
    spin_small_cum_ratio: QDoubleSpinBox
    spin_small_cum_mb: QDoubleSpinBox
    spin_kf_overhead: QDoubleSpinBox
    spin_cui_maxlen: CustomIntegerSpinBox

    btn_reset_defaults: QPushButton

    def __init__(
        self,
        current_config: ClassifierConfig | None = None,
        current_root_dir: str | None = None,
        app_state: AppState | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("환경설정")
        self.resize(660, 680)
        self.setMinimumSize(580, 440)

        self._is_loading: bool = True
        base_state = (
            replace(app_state)
            if app_state is not None
            else load_app_state()
        )
        self._cfg = current_config or base_state.config
        self._initial_root_dir = (
            current_root_dir or base_state.root_dir or self._load_current_root_dir()
        )
        self._app_state = replace(
            base_state,
            config=self._cfg,
            root_dir=self._initial_root_dir,
            recent_dirs=list(base_state.recent_dirs),
            exception_tokens=list(base_state.exception_tokens),
        )
        self._last_saved_root_dir: str = self._initial_root_dir

        self.scroll_area = QScrollArea(self)
        content_widget, content_layout = self._init_ui_shell()
        self.section_advanced = CollapsibleSection(
            title="고급 설정",
            subtitle="(코덱 압축 가중치, 해상도 스케일링, 세부 파라미터)",
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
        card_reset, self.btn_reset_defaults = build_reset_card(
            self._cards,
            self._on_reset_defaults_clicked,
        )
        content_layout.addWidget(card_reset)
        content_layout.addStretch(1)
        self.scroll_area.setWidget(content_widget)
        self.combo_theme = form.combo_theme
        self.edit_root_dir = form.edit_root_dir
        self.edit_root_dir.textChanged.connect(self.edit_root_dir.setToolTip)
        self.btn_browse_root = form.btn_browse_root
        self.edit_dir_std = form.edit_dir_std
        self.edit_dir_low = form.edit_dir_low
        self.spin_over_ratio = form.spin_over_ratio
        self.spin_low_ratio = form.spin_low_ratio
        self.spin_kf_threshold = form.spin_kf_threshold
        self.spin_small_size = form.spin_small_size
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
        self.spin_small_min_saved = form.spin_small_min_saved
        self.spin_small_cum_ratio = form.spin_small_cum_ratio
        self.spin_small_cum_mb = form.spin_small_cum_mb
        self.spin_kf_overhead = form.spin_kf_overhead
        self.spin_cui_maxlen = form.spin_cui_maxlen

        self._load_values()
        self._connect_row_signals()
        self._is_loading = False
        self.init_theme_listener()

    @staticmethod
    def _load_current_root_dir() -> str:
        try:
            return get_root_dir()
        except Exception:
            return ""

    def _init_ui_shell(self) -> tuple[QWidget, QVBoxLayout]:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(22, 16, 22, 18)
        main_layout.setSpacing(12)

        # 1. 상단 자동 저장 안내 (창 제목 표시줄과의 중복 방지를 위해 별도 제목 생략)
        lbl_desc = QLabel(
            "변경한 설정은 즉시 저장되어 분석 및 분류에 반영됩니다."
        )
        lbl_desc.setObjectName("SettingDesc")
        lbl_desc.setWordWrap(True)
        main_layout.addWidget(lbl_desc)

        # 2. 단일 스크롤 영역
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(2, 4, 8, 12)
        content_layout.setSpacing(14)

        main_layout.addWidget(self.scroll_area, stretch=1)
        return content_widget, content_layout

    def _load_values(self) -> None:
        idx = self.combo_theme.findData(ThemeManager().mode)
        if idx >= 0:
            self.combo_theme.setCurrentIndex(idx)

        self.edit_root_dir.setText(self._initial_root_dir)
        self.edit_root_dir.setCursorPosition(0)
        self.edit_root_dir.setToolTip(self._initial_root_dir)
        self.edit_dir_std.setText(self._cfg.dir_standard_cq22)
        self.edit_dir_low.setText(self._cfg.dir_low_quality_keyframe)

        self.spin_over_ratio.setValue(self._cfg.over_bitrate_ratio)
        self.spin_low_ratio.setValue(self._cfg.low_bitrate_ratio)
        self.spin_kf_threshold.setValue(self._cfg.keyframe_threshold)
        self.spin_small_size.setValue(self._cfg.small_footage_mb)

        self.spin_w_av1.setValue(
            self._cfg.codec_weights.get("av1", float(DEFAULT_CONFIGS["CODEC_WEIGHT_AV1"]))
        )
        self.spin_w_hevc.setValue(
            self._cfg.codec_weights.get(
                "hevc", float(DEFAULT_CONFIGS["CODEC_WEIGHT_HEVC_VP9"])
            )
        )
        self.spin_w_h264.setValue(
            self._cfg.codec_weights.get("h264", float(DEFAULT_CONFIGS["CODEC_WEIGHT_H264"]))
        )
        self.spin_w_legacy.setValue(
            self._cfg.codec_weights.get(
                "mpeg4", float(DEFAULT_CONFIGS["CODEC_WEIGHT_LEGACY"])
            )
        )

        self.spin_base_w.setValue(self._cfg.base_width)
        self.spin_base_h.setValue(self._cfg.base_height)
        self.spin_base_bitrate.setValue(self._cfg.base_target_bitrate)
        self.spin_base_fps.setValue(self._cfg.base_fps)
        self.spin_area_exp.setValue(self._cfg.area_scaling_exponent)
        self.spin_fps_exp.setValue(self._cfg.fps_scaling_exponent)

        self.spin_small_min_saved.setValue(self._cfg.small_footage_min_saved_mb)
        self.spin_small_cum_ratio.setValue(self._cfg.small_footage_cumulative_ratio)
        self.spin_small_cum_mb.setValue(self._cfg.small_footage_cumulative_mb)
        self.spin_kf_overhead.setValue(self._cfg.keyframe_1s_overhead_rate)
        self.spin_cui_maxlen.setValue(self._cfg.tabulate_filename_maxlen)

    def _on_browse_root_dir(self) -> None:
        curr = self.edit_root_dir.text().strip()
        start = curr if os.path.isdir(curr) else os.path.expanduser("~")
        chosen = browse_directory(self, "기본 작업 폴더 선택", start)
        if chosen:
            normalized = os.path.normpath(chosen)
            self.edit_root_dir.setText(normalized)
            self.edit_root_dir.setCursorPosition(0)
            self._auto_save()

    def _connect_row_signals(self) -> None:
        for row in self._rows.values():
            row.value_changed.connect(self._on_setting_changed)

    def _on_setting_changed(self) -> None:
        if getattr(self, "_is_loading", False):
            return
        self._auto_save()

    def sync_thresholds(
        self, over_ratio: float, kf_threshold: float, small_mb: float
    ) -> None:
        """메인 윈도우의 FilterTuningBar에서 변경된 임계값을 열려 있는 설정 창에 동기화"""
        self._cfg = replace(
            self._cfg,
            over_bitrate_ratio=over_ratio,
            keyframe_threshold=kf_threshold,
            small_footage_mb=small_mb,
        )
        self._app_state.config = self._cfg
        self._is_loading = True
        try:
            self.spin_over_ratio.setValue(over_ratio)
            self.spin_kf_threshold.setValue(kf_threshold)
            self.spin_small_size.setValue(small_mb)
        finally:
            self._is_loading = False

    def _on_reset_defaults_clicked(self) -> None:
        answer = QMessageBox.question(
            self,
            "기본값 복원",
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
            if ThemeManager().mode != fresh.theme_mode:
                ThemeManager().set_mode(fresh.theme_mode)
            self._cfg = fresh.config
            self._app_state = replace(
                fresh,
                recent_dirs=list(fresh.recent_dirs),
                exception_tokens=list(fresh.exception_tokens),
            )
            self._initial_root_dir = fresh.root_dir
            self._last_saved_root_dir = fresh.root_dir
            self._load_values()
        finally:
            self._is_loading = False
        self.settings_reset.emit(fresh)

    def _auto_save(self) -> None:
        if getattr(self, "_is_loading", False):
            return

        values: dict[str, str] = {
            key: row.get_config_value()
            for key, row in self._rows.items()
            if key in DEFAULT_CONFIGS
        }
        new_config = ClassifierConfig.from_mapping(values)

        root_dir_val = self.edit_root_dir.text().strip()
        root_dir_changed = root_dir_val != self._last_saved_root_dir
        if root_dir_changed:
            self._app_state.recent_dirs = record_recent_dir(
                self._app_state.recent_dirs, root_dir_val
            )

        chosen_theme = self.combo_theme.currentData()
        if chosen_theme and chosen_theme != ThemeManager().mode:
            ThemeManager().set_mode(chosen_theme)

        self._cfg = new_config
        self._app_state.config = new_config
        self._app_state.root_dir = root_dir_val
        self._app_state.theme_mode = ThemeManager().mode
        try:
            save_app_state(self._app_state, sync_rootdir=root_dir_changed)
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
            set_button_icon(
                self.btn_reset_defaults,
                "rotate-ccw",
                palette=palette,
                size=14,
                pad_right=6,
            )
