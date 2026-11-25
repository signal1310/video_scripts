"""범주 페이지의 고급, 기준 카드 생성 모듈

- 카드마다 수식 안내가 필요한 항목은 사용 가이드 판정 원리 앵커를 붙여 물음표 버튼을 둠
"""

from typing import Any

from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox

from src.core.config import (
    BitrateModelConfig,
    CodecWeightsConfig,
    CuiConfig,
    DisplayConfig,
    EncodeConfig,
    ThresholdsConfig,
)
from src.gui.settings.setting_row import (
    SettingRow,
    make_combo_row,
    make_float_spin_row,
    make_int_spin_row,
    make_percent_row,
)
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox

HELP_TOOLTIP_TEMPLATE: str = "이 항목의 계산 원리를 사용 가이드 판정 원리 탭에서 엽니다."


class _CardRowBuilder:
    def __init__(self, card: SettingsGroupCard, rows: dict[str, SettingRow]) -> None:
        self._card = card
        self._rows = rows

    def flt(
        self,
        cfg_cls: type[Any],
        attr: str,
        title: str,
        desc: str,
        step: float,
        decimals: int,
        unit: str = "",
        *,
        row_key: str,
    ) -> QDoubleSpinBox:
        return make_float_spin_row(
            self._rows,
            self._card,
            cfg_cls,
            attr,
            title,
            desc,
            step,
            decimals,
            unit,
            row_key=row_key,
        )

    def integer(
        self,
        cfg_cls: type[Any],
        attr: str,
        title: str,
        desc: str,
        step: int,
        unit: str = "",
        *,
        row_key: str,
    ) -> CustomIntegerSpinBox:
        return make_int_spin_row(
            self._rows,
            self._card,
            cfg_cls,
            attr,
            title,
            desc,
            step,
            unit,
            row_key=row_key,
        )

    def percent(
        self,
        cfg_cls: type[Any],
        attr: str,
        title: str,
        desc: str,
        step_percent: float,
        *,
        row_key: str,
    ) -> QDoubleSpinBox:
        return make_percent_row(
            self._rows,
            self._card,
            cfg_cls,
            attr,
            title,
            desc,
            step_percent,
            row_key=row_key,
        )

    def combo(
        self,
        cfg_cls: type[Any],
        attr: str,
        title: str,
        desc: str,
        choices: tuple[tuple[str, Any], ...],
        *,
        row_key: str,
    ) -> QComboBox:
        return make_combo_row(
            self._rows,
            self._card,
            cfg_cls,
            attr,
            title,
            desc,
            choices,
            row_key=row_key,
        )


def _card(
    rows: dict[str, SettingRow],
    icon_name: str,
    title: str,
    subtitle: str = "",
    *,
    help_anchor: str = "",
) -> tuple[SettingsGroupCard, _CardRowBuilder]:
    card = SettingsGroupCard(
        icon_name=icon_name,
        title=title,
        subtitle=subtitle,
        help_anchor=help_anchor,
        help_tooltip=HELP_TOOLTIP_TEMPLATE if help_anchor else "",
    )
    return card, _CardRowBuilder(card, rows)


def build_display_format_card(rows: dict[str, SettingRow]) -> SettingsGroupCard:
    """표 열의 소수점 자릿수와 FPS 정수 표기 방식 카드 생성"""
    card, b = _card(rows, "sliders", "표 서식", "소수점 자릿수, FPS 정수 표기")

    def _dec(attr: str, title: str, desc: str) -> CustomIntegerSpinBox:
        return b.integer(
            DisplayConfig, attr, title, desc, 1, "자리", row_key=f"display.{attr}"
        )

    _dec(
        "fps_decimals",
        "FPS 소수점 자릿수",
        "FPS 열의 소수점 자릿수입니다. 예: 2자리면 29.97로 표시합니다.",
    )
    b.combo(
        DisplayConfig,
        "fps_trim_whole",
        "FPS 정수 표기 방식",
        "FPS가 정수일 때 30 또는 30.00 중 표기를 고릅니다.",
        (
            ("정수 생략 (30)", True),
            ("고정 (30.00)", False),
        ),
        row_key="display.fps_trim_whole",
    )
    _dec(
        "duration_decimals",
        "길이(초) 소수점 자릿수",
        "길이(초) 열의 소수점 자릿수입니다. 예: 1자리면 125.4로 표시합니다.",
    )
    _dec(
        "ratio_decimals",
        "BR 비율 소수점 자릿수",
        "BR 비율 열의 소수점 자릿수입니다. 예: 2자리면 1.35로 표시합니다.",
    )
    _dec(
        "keyframe_decimals",
        "키프레임 간격 소수점 자릿수",
        "키프레임 간격 열의 소수점 자릿수입니다.",
    )
    _dec(
        "size_decimals",
        "현재 용량 소수점 자릿수",
        "현재 용량 열의 소수점 자릿수입니다.",
    )
    _dec(
        "after_size_decimals",
        "예상 용량 소수점 자릿수",
        "예상 용량 열의 소수점 자릿수입니다.",
    )
    _dec(
        "saved_size_decimals",
        "예상 절약 소수점 자릿수",
        "예상 절약 열의 소수점 자릿수입니다.",
    )
    return card


def build_frame_threshold_card(rows: dict[str, SettingRow]) -> SettingsGroupCard:
    """축소 후보 판정 기준과 검은 여백 무시 면적 상한 카드 생성"""
    card, b = _card(
        rows,
        "analyze",
        "화질 분석 및 여백 기준",
        "축소 후보 판정 점수, 여백 무시 기준",
        help_anchor="frame-analysis",
    )
    b.flt(
        EncodeConfig,
        "roundtrip_vmaf_threshold",
        "축소 후보 VMAF 기준",
        "왕복 화질 점수가 이 값 이상인 가장 낮은 해상도를 축소 후보로 정합니다. "
        "낮출수록 더 많이 줄이지만 화질 차이가 커질 수 있습니다. 기본값은 98.0점입니다.",
        0.1,
        1,
        "점",
        row_key="analysis.encode.roundtrip_vmaf_threshold",
    )
    b.flt(
        EncodeConfig,
        "roundtrip_ssim_threshold",
        "축소 후보 SSIM 기준",
        "VMAF를 쓸 수 없을 때 대신 쓰는 구조 유사도 기준입니다. 1에 가까울수록 원본과 비슷합니다. "
        "기본값은 0.996입니다.",
        0.001,
        3,
        row_key="analysis.encode.roundtrip_ssim_threshold",
    )
    b.percent(
        EncodeConfig,
        "active_area_no_bars_ratio",
        "검은 여백 무시 면적 상한",
        "검은 여백을 잘라낸 활성 화면이 원본 면적에서 차지하는 비율이 이 값 이상이면 "
        "검은 여백이 없는 영상으로 보고 잘라내지 않습니다. 기본값은 90 %입니다.",
        1,
        row_key="analysis.encode.active_area_no_bars_ratio",
    )
    return card


def build_bitrate_base_card(rows: dict[str, SettingRow]) -> SettingsGroupCard:
    """비트레이트 기준점(화질 기준값, 기준 목표 비트레이트, 기준 해상도, 기준 FPS) 카드 생성"""
    card, b = _card(
        rows,
        "gauge",
        "비트레이트 기준점",
        "모든 비트레이트 계산의 출발점",
        help_anchor="optimal-bitrate",
    )
    b.flt(
        EncodeConfig,
        "baseline_cq",
        "화질 기준값",
        "인코딩할 때 목표로 삼는 화질 기준값입니다. 다른 프로그램의 CQ(NVENC), CRF(x264, x265)에 해당합니다. "
        "낮을수록 고화질이며, 같은 값이어도 인코더에 따라 결과 크기와 화질이 다릅니다. "
        "기준 목표 비트레이트를 이 값에 맞춰 환산합니다. 기본값은 22.0입니다.",
        0.5,
        1,
        row_key="analysis.encode.baseline_cq",
    )
    b.integer(
        BitrateModelConfig,
        "base_target_kbps",
        "기준 목표 비트레이트",
        "기준 해상도와 FPS의 H.264 영상이 화질 기준값 수준의 화질을 내기에 충분하다고 보는 비트레이트입니다. "
        "모든 계산의 출발점이라 올리면 과화질 판정이 줄고, 내리면 늘어납니다.",
        50,
        "kbps",
        row_key="analysis.bitrate_model.base_target_kbps",
    )
    b.integer(
        BitrateModelConfig,
        "base_width",
        "기준 해상도 너비",
        "기준선이 되는 해상도의 너비 픽셀 수입니다. 다른 영상은 이 해상도와 화면 면적을 비교해 필요한 비트레이트를 계산합니다.",
        10,
        "px",
        row_key="analysis.bitrate_model.base_width",
    )
    b.integer(
        BitrateModelConfig,
        "base_height",
        "기준 해상도 높이",
        "기준선이 되는 해상도의 높이 픽셀 수입니다. 너비와 곱한 면적이 다른 영상과의 비교 기준이 됩니다.",
        10,
        "px",
        row_key="analysis.bitrate_model.base_height",
    )
    b.flt(
        BitrateModelConfig,
        "base_fps",
        "기준 FPS",
        "기준선이 되는 FPS입니다. 이보다 프레임이 많은 영상은 비트레이트가 더 필요하다고 보정하며, 미세한 차이는 무시합니다.",
        1.0,
        1,
        "fps",
        row_key="analysis.bitrate_model.base_fps",
    )
    return card
def build_console_card(rows: dict[str, SettingRow]) -> SettingsGroupCard:
    """콘솔 표 파일명 최대 길이 카드 생성"""
    card, b = _card(rows, "file-text", "콘솔 출력", "")
    b.integer(
        CuiConfig,
        "filename_maxlen",
        "콘솔 파일명 최대 길이",
        "주피터 노트북과 터미널 표에서 파일명 열에 보여 줄 최대 글자 수입니다. "
        "이보다 긴 이름은 끝을 말줄임표로 줄여 보여 줍니다.",
        5,
        "자",
        row_key="cui.filename_maxlen",
    )
    return card



def build_criteria_margin_card(rows: dict[str, SettingRow]) -> SettingsGroupCard:
    """동급 코덱, 10비트 SDR 과화질 여유와 코덱 전환 최소 배수 카드 생성"""
    card, b = _card(
        rows,
        "sliders",
        "판별 여유 및 코덱 전환",
        "판정 경계를 넓히는 여유값",
        help_anchor="ratio",
    )
    b.flt(
        ThresholdsConfig,
        "same_or_better_codec_over_margin",
        "동급 코덱 과화질 여유",
        "원본 코덱이 목표 코덱 이상의 압축 성능이면 다시 인코딩할 때 세대 손실이 생기므로 "
        "과화질 판정 상한에 이 값을 더합니다. 기본값은 0.15배입니다.",
        0.01,
        2,
        "배",
        row_key="analysis.thresholds.same_or_better_codec_over_margin",
    )
    b.flt(
        ThresholdsConfig,
        "ten_bit_sdr_over_margin",
        "10비트 SDR 과화질 여유",
        "비-HDR 10비트 영상은 8비트로 줄일 때 색 경계가 뭉개질 수 있어 과화질 판정 상한에 이 값을 더합니다. "
        "기본값은 0.10배입니다.",
        0.01,
        2,
        "배",
        row_key="analysis.thresholds.ten_bit_sdr_over_margin",
    )
    b.flt(
        ThresholdsConfig,
        "codec_upgrade_min_gain_ratio",
        "코덱 전환 최소 압축 성능 배수",
        "원본 코덱의 압축 성능이 목표 코덱보다 이 배수 이상 낮을 때만 코덱 전환 인코딩 대상으로 봅니다. "
        "기본값은 1.25배입니다.",
        0.05,
        2,
        "배",
        row_key="analysis.thresholds.codec_upgrade_min_gain_ratio",
    )
    return card



def build_codecs_card(rows: dict[str, SettingRow]) -> SettingsGroupCard:
    """코덱별 압축 가중치 카드 생성, 기준은 H.264 대비 상대 비율"""
    card, b = _card(
        rows,
        "film",
        "코덱별 압축 가중치",
        "H.264 대비 상대 비율",
        help_anchor="optimal-bitrate",
    )
    b.flt(
        CodecWeightsConfig,
        "av1",
        "AV1 코덱 가중치",
        "AV1은 H.264보다 적은 데이터로 같은 화질을 담습니다. 기준선(최적 BR)을 이 비율로 낮춰 계산하며, "
        "예를 들어 0.65이면 H.264 기준의 65%만 있어도 충분하다고 봅니다.",
        0.05,
        2,
        "배",
        row_key="analysis.codec_weights.av1",
    )
    b.flt(
        CodecWeightsConfig,
        "hevc_vp9",
        "HEVC / VP9 코덱 가중치",
        "HEVC(H.265)와 VP9도 H.264보다 압축 성능이 좋아 더 낮은 비트레이트에서 같은 화질을 냅니다. "
        "값이 작을수록 이 코덱 영상의 기준선(최적 BR)이 낮아집니다.",
        0.05,
        2,
        "배",
        row_key="analysis.codec_weights.hevc_vp9",
    )
    b.flt(
        CodecWeightsConfig,
        "h264",
        "H.264 코덱 가중치",
        "다른 코덱 가중치를 비교하는 기준값입니다. 나머지 값이 모두 H.264 대비 상대 비율이므로 "
        "보통 1.00 그대로 두는 것을 권장합니다.",
        0.05,
        2,
        "배",
        row_key="analysis.codec_weights.h264",
    )
    b.flt(
        CodecWeightsConfig,
        "legacy",
        "구형 코덱 가중치",
        "MPEG-4, WMV 같은 구형 코덱은 같은 화질에 H.264보다 더 많은 데이터가 필요합니다. "
        "값이 클수록 이 영상의 기준선(최적 BR)이 높아져, 화질이 낮은데도 과화질로 오해하는 일을 막습니다.",
        0.05,
        2,
        "배",
        row_key="analysis.codec_weights.legacy",
    )
    return card


def build_scaling_card(rows: dict[str, SettingRow]) -> SettingsGroupCard:
    """해상도 면적, FPS 스케일링 지수와 키프레임 인코딩 용량 증가율 카드 생성"""
    card, b = _card(
        rows,
        "bar-chart",
        "스케일링 지수 및 키프레임 보정",
        "면적, FPS 비선형 보정",
        help_anchor="size",
    )
    b.flt(
        BitrateModelConfig,
        "area_scaling_exponent",
        "해상도 면적 스케일링 지수",
        "화면이 4배(예: 1080p에서 4K) 커져도 이웃한 픽셀이 비슷해 필요한 비트레이트는 4배보다 적게 늘어납니다. "
        "1에 가까울수록 면적에 비례해 늘고, 작을수록 완만하게 늘어납니다.",
        0.01,
        2,
        row_key="analysis.bitrate_model.area_scaling_exponent",
    )
    b.flt(
        BitrateModelConfig,
        "fps_scaling_exponent",
        "FPS 보정 지수",
        "프레임이 2배(30fps에서 60fps)가 돼도 앞뒤 프레임이 서로 비슷해 비트레이트가 2배까지는 필요하지 않습니다. "
        "1에 가까울수록 프레임 수에 비례해 늘어납니다.",
        0.01,
        2,
        row_key="analysis.bitrate_model.fps_scaling_exponent",
    )
    b.flt(
        ThresholdsConfig,
        "keyframe_1s_increase_rate",
        "키프레임 인코딩 용량 증가율",
        "키프레임 간격이 긴 영상을 1초 간격으로 인코딩하면 온전한 프레임이 늘어 용량이 조금 커집니다. "
        "그 증가 폭의 상한 비율로 예상 용량 계산에 쓰이며, 예를 들어 0.06이면 최대 6% 늘어납니다.",
        0.01,
        2,
        "배",
        row_key="analysis.thresholds.keyframe_1s_increase_rate",
    )
    return card

