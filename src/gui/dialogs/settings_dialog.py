"""환경설정(.env·.rootdir) 조회·검증·저장 대화상자 모듈"""

import logging
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.core.config import (
    DEFAULT_CONFIGS,
    ClassifierConfig,
    get_root_dir,
    save_env_values,
    set_root_dir,
)
from src.gui.constants import browse_directory
from src.gui.dialogs.setting_row import SettingRow
from src.gui.dialogs.settings_sections import build_settings_form
from src.gui.styles.icons import get_icon
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

    def __init__(
        self,
        current_config: ClassifierConfig | None = None,
        current_root_dir: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("환경설정")
        self.resize(630, 680)
        self.setMinimumSize(560, 440)

        self._is_loading: bool = True
        self._cfg = current_config or ClassifierConfig.from_env(reload_dotenv=True)
        self._initial_root_dir = current_root_dir or self._load_current_root_dir()
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
        content_layout.addStretch(1)
        self.scroll_area.setWidget(content_widget)

        self._rows: dict[str, SettingRow] = form.rows
        self._cards: list[SettingsGroupCard] = form.cards
        self.combo_theme = form.combo_theme
        self.edit_root_dir = form.edit_root_dir
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
        main_layout.setContentsMargins(22, 20, 22, 18)
        main_layout.setSpacing(14)

        # 1. 상단 타이틀 및 설명
        header_box = QVBoxLayout()
        header_box.setSpacing(4)
        lbl_title = QLabel("환경설정")
        lbl_title.setObjectName("AppTitle")
        lbl_desc = QLabel(
            "설정을 변경하면 즉시 자동 저장되어 분석 및 분류에 반영됩니다."
        )
        lbl_desc.setObjectName("SettingDesc")
        lbl_desc.setWordWrap(True)
        header_box.addWidget(lbl_title)
        header_box.addWidget(lbl_desc)
        main_layout.addLayout(header_box)

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
            self.edit_root_dir.setText(os.path.normpath(chosen))
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
        self._is_loading = True
        try:
            self.spin_over_ratio.setValue(over_ratio)
            self.spin_kf_threshold.setValue(kf_threshold)
            self.spin_small_size.setValue(small_mb)
        finally:
            self._is_loading = False

    def _auto_save(self) -> None:
        if getattr(self, "_is_loading", False):
            return

        updates: dict[str, str] = {
            key: row.get_env_value()
            for key, row in self._rows.items()
            if key in DEFAULT_CONFIGS
        }

        try:
            save_env_values(updates, reload_dotenv=True)
        except Exception:
            logging.getLogger(__name__).warning(".env 설정 저장 실패", exc_info=True)

        root_dir_val = self.edit_root_dir.text().strip()
        if root_dir_val != self._last_saved_root_dir:
            try:
                set_root_dir(root_dir_val)
                self._last_saved_root_dir = root_dir_val
            except Exception:
                logging.getLogger(__name__).warning(".rootdir 설정 저장 실패", exc_info=True)

        chosen_theme = self.combo_theme.currentData()
        if chosen_theme and chosen_theme != ThemeManager().mode:
            ThemeManager().set_mode(chosen_theme)

        new_config = ClassifierConfig.from_env(reload_dotenv=True)
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
