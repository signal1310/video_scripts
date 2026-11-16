"""설정 대화상자 위젯과 ClassifierConfig 필드 사이의 양방향 매핑 명세 모듈"""

from dataclasses import dataclass

from src.core.config import (
    DEFAULT_DIR_DOWNSCALE_PREFIX,
    DEFAULT_DIR_LOW_QUALITY_KEYFRAME,
    DEFAULT_DIR_STANDARD_CQ22,
)

FLOAT_SETTING_ROUND_DIGITS: int = 4


@dataclass(frozen=True, slots=True)
class ConfigFieldBinding:
    """설정 대화상자 위젯과 ClassifierConfig 필드 간의 양방향 매핑 명세"""

    widget_attr: str
    section_attr: str | None
    field_name: str
    default_text: str = ""
    round_digits: int | None = None


CONFIG_FIELD_BINDINGS: tuple[ConfigFieldBinding, ...] = (
    ConfigFieldBinding(
        "edit_dir_std", None, "dir_standard_cq22", DEFAULT_DIR_STANDARD_CQ22
    ),
    ConfigFieldBinding("edit_dir_low", None, "dir_low_quality_keyframe", DEFAULT_DIR_LOW_QUALITY_KEYFRAME),
    ConfigFieldBinding("edit_dir_downscale", None, "dir_downscale_prefix", DEFAULT_DIR_DOWNSCALE_PREFIX),
    ConfigFieldBinding("combo_encode_codec", "encode", "encode_codec"),
    ConfigFieldBinding("combo_force_legacy_compat", "encode", "force_legacy_compat"),
    ConfigFieldBinding("combo_max_resolution", "encode", "max_resolution_tier"),
    ConfigFieldBinding("combo_auto_downscale_by_bitrate", "encode", "auto_downscale_by_bitrate"),
    ConfigFieldBinding("combo_savings_basis", "savings", "selection_basis"),
    ConfigFieldBinding(
        "spin_encode_speed",
        "encode",
        "encode_speed_factor",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    ConfigFieldBinding(
        "spin_kf_increase",
        "thresholds",
        "keyframe_1s_increase_rate",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    ConfigFieldBinding(
        "spin_w_av1",
        "codec_weights",
        "av1",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    ConfigFieldBinding(
        "spin_w_hevc",
        "codec_weights",
        "hevc_vp9",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    ConfigFieldBinding(
        "spin_w_h264",
        "codec_weights",
        "h264",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    ConfigFieldBinding(
        "spin_w_legacy",
        "codec_weights",
        "legacy",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    ConfigFieldBinding("spin_base_w", "bitrate_model", "base_width"),
    ConfigFieldBinding("spin_base_h", "bitrate_model", "base_height"),
    ConfigFieldBinding("spin_base_bitrate", "bitrate_model", "base_target_kbps"),
    ConfigFieldBinding(
        "spin_base_fps",
        "bitrate_model",
        "base_fps",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    ConfigFieldBinding(
        "spin_area_exp",
        "bitrate_model",
        "area_scaling_exponent",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    ConfigFieldBinding(
        "spin_fps_exp",
        "bitrate_model",
        "fps_scaling_exponent",
        round_digits=FLOAT_SETTING_ROUND_DIGITS,
    ),
    ConfigFieldBinding("spin_cui_maxlen", "cui", "filename_maxlen"),
    ConfigFieldBinding("spin_fps_decimals", "display", "fps_decimals"),
    ConfigFieldBinding("combo_fps_trim_whole", "display", "fps_trim_whole"),
    ConfigFieldBinding("spin_duration_decimals", "display", "duration_decimals"),
    ConfigFieldBinding("spin_ratio_decimals", "display", "ratio_decimals"),
    ConfigFieldBinding("spin_keyframe_decimals", "display", "keyframe_decimals"),
    ConfigFieldBinding("spin_size_decimals", "display", "size_decimals"),
    ConfigFieldBinding("spin_after_size_decimals", "display", "after_size_decimals"),
    ConfigFieldBinding("spin_saved_size_decimals", "display", "saved_size_decimals"),
    ConfigFieldBinding("combo_size_unit", "display", "size_unit"),
)
