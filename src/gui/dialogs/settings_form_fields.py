"""환경설정 다이얼로그의 입력 위젯, 그룹 카드, 설정 행 맵을 묶는 폼 필드 컨테이너 모듈"""

from dataclasses import dataclass

from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QPushButton

from src.gui.dialogs.setting_row import SettingRow
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox
from src.gui.widgets.settings_group_card import SettingsGroupCard


@dataclass
class SettingsFormFields:
    """환경설정 다이얼로그의 입력 위젯과 그룹 카드, 설정 행 맵을 보관하는 컨테이너"""

    rows: dict[str, SettingRow]
    cards: list[SettingsGroupCard]
    combo_theme: QComboBox
    combo_preview_backend: QComboBox
    combo_preview_seek: QComboBox
    edit_root_dir: QLineEdit
    btn_browse_root: QPushButton
    edit_dir_std: QLineEdit
    edit_dir_low: QLineEdit
    edit_dir_downscale: QLineEdit
    combo_encode_codec: QComboBox
    spin_encode_speed: QDoubleSpinBox
    btn_detect_encode_speed: QPushButton
    combo_savings_basis: QComboBox
    combo_force_legacy_compat: QComboBox
    combo_max_resolution: QComboBox
    combo_auto_downscale_by_bitrate: QComboBox
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
    spin_kf_increase: QDoubleSpinBox
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
