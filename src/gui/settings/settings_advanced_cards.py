"""고급 설정 접이식 영역의 코덱 가중치, 비트레이트 기준, 키프레임 보정, 표 서식 카드 생성 모듈"""

from typing import Any

from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox

from src.core.config import (
    BitrateModelConfig,
    CodecWeightsConfig,
    CuiConfig,
    DisplayConfig,
    ThresholdsConfig,
)
from src.gui.settings.setting_row import (
    SettingRow,
    make_combo_row,
    make_float_spin_row,
    make_int_spin_row,
)
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox


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


def build_codecs_card(
    rows: dict[str, SettingRow],
    cards: list[SettingsGroupCard],
) -> tuple[
    SettingsGroupCard,
    QDoubleSpinBox,
    QDoubleSpinBox,
    QDoubleSpinBox,
    QDoubleSpinBox,
]:
    """코덱별 압축 가중치 카드 생성, 기준은 H.264 대비 상대 비율"""
    card = SettingsGroupCard(
        icon_name="film", title="코덱별 압축 가중치", subtitle="H.264 대비 상대 비율"
    )
    cards.append(card)
    b = _CardRowBuilder(card, rows)
    return (
        card,
        b.flt(
            CodecWeightsConfig,
            "av1",
            "AV1 코덱 가중치",
            "AV1은 H.264보다 적은 데이터로 같은 화질을 담습니다. 기준선(최적 BR)을 이 비율로 낮춰 계산하며, 예를 들어 0.65이면 H.264 기준의 65%만 있어도 충분하다고 봅니다.",
            0.05,
            2,
            "배",
            row_key="analysis.codec_weights.av1",
        ),
        b.flt(
            CodecWeightsConfig,
            "hevc_vp9",
            "HEVC / VP9 코덱 가중치",
            "HEVC(H.265)와 VP9도 H.264보다 압축 성능이 좋아 더 낮은 비트레이트에서 같은 화질을 냅니다. 값이 작을수록 이 코덱 영상의 기준선(최적 BR)이 낮아집니다.",
            0.05,
            2,
            "배",
            row_key="analysis.codec_weights.hevc_vp9",
        ),
        b.flt(
            CodecWeightsConfig,
            "h264",
            "H.264 코덱 가중치",
            "다른 코덱 가중치를 비교하는 기준값입니다. 나머지 값이 모두 H.264 대비 상대 비율이므로 보통 1.00 그대로 두는 것을 권장합니다.",
            0.05,
            2,
            "배",
            row_key="analysis.codec_weights.h264",
        ),
        b.flt(
            CodecWeightsConfig,
            "legacy",
            "구형 코덱 가중치",
            "MPEG-4, WMV 같은 구형 코덱은 같은 화질에 H.264보다 더 많은 데이터가 필요합니다. 값이 클수록 이 영상의 기준선(최적 BR)이 높아져, 화질이 낮은데도 과화질로 오해하는 일을 막습니다.",
            0.05,
            2,
            "배",
            row_key="analysis.codec_weights.legacy",
        ),
    )


def build_bitrate_card(
    rows: dict[str, SettingRow],
    cards: list[SettingsGroupCard],
) -> tuple[
    SettingsGroupCard,
    CustomIntegerSpinBox,
    CustomIntegerSpinBox,
    CustomIntegerSpinBox,
    QDoubleSpinBox,
    QDoubleSpinBox,
    QDoubleSpinBox,
]:
    """기준 해상도와 비트레이트, 해상도와 FPS 스케일링 카드 생성"""
    card = SettingsGroupCard(
        icon_name="sliders", title="비트레이트 기준점 및 스케일링", subtitle=""
    )
    cards.append(card)
    b = _CardRowBuilder(card, rows)
    return (
        card,
        b.integer(
            BitrateModelConfig,
            "base_width",
            "기준 해상도 너비",
            "기준선이 되는 해상도의 너비 픽셀 수입니다. 다른 영상은 이 해상도와 화면 면적을 비교해 필요한 비트레이트를 계산합니다.",
            10,
            "px",
            row_key="analysis.bitrate_model.base_width",
        ),
        b.integer(
            BitrateModelConfig,
            "base_height",
            "기준 해상도 높이",
            "기준선이 되는 해상도의 높이 픽셀 수입니다. 너비와 곱한 면적이 다른 영상과의 비교 기준이 됩니다.",
            10,
            "px",
            row_key="analysis.bitrate_model.base_height",
        ),
        b.integer(
            BitrateModelConfig,
            "base_target_kbps",
            "기준 목표 비트레이트",
            "위 기준 해상도와 FPS의 H.264 영상이 화질 기준값 22 수준의 화질을 내기에 충분하다고 보는 비트레이트입니다. 모든 계산의 출발점이라 올리면 과화질 판정이 줄고, 내리면 늘어납니다.",
            50,
            "kbps",
            row_key="analysis.bitrate_model.base_target_kbps",
        ),
        b.flt(
            BitrateModelConfig,
            "base_fps",
            "기준 FPS",
            "기준선이 되는 FPS입니다. 이보다 프레임이 많은 영상은 비트레이트가 더 필요하다고 보정하며, 미세한 차이는 무시합니다.",
            1.0,
            1,
            "fps",
            row_key="analysis.bitrate_model.base_fps",
        ),
        b.flt(
            BitrateModelConfig,
            "area_scaling_exponent",
            "해상도 면적 스케일링 지수",
            "화면이 4배(예: 1080p에서 4K) 커져도 이웃한 픽셀이 비슷해 필요한 비트레이트는 4배보다 적게 늘어납니다. 1에 가까울수록 면적에 비례해 늘고, 작을수록 완만하게 늘어납니다.",
            0.01,
            2,
            row_key="analysis.bitrate_model.area_scaling_exponent",
        ),
        b.flt(
            BitrateModelConfig,
            "fps_scaling_exponent",
            "FPS 보정 지수",
            "프레임이 2배(30fps에서 60fps)가 돼도 앞뒤 프레임이 서로 비슷해 비트레이트가 2배까지는 필요하지 않습니다. 1에 가까울수록 프레임 수에 비례해 늘어납니다.",
            0.01,
            2,
            row_key="analysis.bitrate_model.fps_scaling_exponent",
        ),
    )


def build_increase_and_cui_card(
    rows: dict[str, SettingRow],
    cards: list[SettingsGroupCard],
) -> tuple[
    SettingsGroupCard,
    QDoubleSpinBox,
    CustomIntegerSpinBox,
]:
    """키프레임 재인코딩 용량 증가율과 콘솔 파일명 길이 카드 생성"""
    card = SettingsGroupCard(
        icon_name="bar-chart", title="키프레임 보정 및 콘솔 출력", subtitle=""
    )
    cards.append(card)
    b = _CardRowBuilder(card, rows)
    return (
        card,
        b.flt(
            ThresholdsConfig,
            "keyframe_1s_increase_rate",
            "키프레임 재인코딩 용량 증가율",
            "키프레임 간격이 긴 영상을 1초 간격으로 재인코딩하면 온전한 프레임이 늘어 용량이 조금 커집니다. 그 증가 폭의 상한 비율로 예상 용량 계산에 쓰이며, 예를 들어 0.06이면 최대 6% 늘어납니다.",
            0.01,
            2,
            "배",
            row_key="analysis.thresholds.keyframe_1s_increase_rate",
        ),
        b.integer(
            CuiConfig,
            "filename_maxlen",
            "콘솔 파일명 최대 길이",
            "주피터 노트북과 터미널 표에서 파일명 열에 보여 줄 최대 글자 수입니다. 이보다 긴 이름은 끝을 말줄임표로 줄여 보여 줍니다.",
            5,
            "자",
            row_key="cui.filename_maxlen",
        ),
    )


def build_display_card(
    rows: dict[str, SettingRow],
    cards: list[SettingsGroupCard],
) -> tuple[
    SettingsGroupCard,
    CustomIntegerSpinBox,
    QComboBox,
    CustomIntegerSpinBox,
    CustomIntegerSpinBox,
    CustomIntegerSpinBox,
    CustomIntegerSpinBox,
    CustomIntegerSpinBox,
    CustomIntegerSpinBox,
    QComboBox,
    QComboBox,
]:
    """표 열의 소수점 자릿수, 용량 단위 및 파일 이름 정렬 서식 카드 생성"""
    card = SettingsGroupCard(
        icon_name="sliders",
        title="표 표시 서식",
        subtitle="소수점 자릿수, 용량 단위, 파일 이름 정렬",
    )
    cards.append(card)
    b = _CardRowBuilder(card, rows)

    def _dec(attr: str, title: str, desc: str) -> CustomIntegerSpinBox:
        return b.integer(
            DisplayConfig, attr, title, desc, 1, "자리", row_key=f"display.{attr}"
        )

    return (
        card,
        _dec(
            "fps_decimals",
            "FPS 소수점 자릿수",
            "FPS 열의 소수점 자릿수입니다. 예: 2자리면 29.97",
        ),
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
        ),
        _dec(
            "duration_decimals",
            "길이(초) 소수점 자릿수",
            "길이(초) 열의 소수점 자릿수입니다. 예: 1자리면 125.4",
        ),
        _dec(
            "ratio_decimals",
            "BR 비율 소수점 자릿수",
            "BR 비율 열의 소수점 자릿수입니다. 예: 2자리면 1.35",
        ),
        _dec(
            "keyframe_decimals",
            "키프레임 간격 소수점 자릿수",
            "키프레임 간격 열의 소수점 자릿수입니다.",
        ),
        _dec(
            "size_decimals",
            "현재 용량 소수점 자릿수",
            "현재 용량 열의 소수점 자릿수입니다.",
        ),
        _dec(
            "after_size_decimals",
            "예상 용량 소수점 자릿수",
            "예상 용량 열의 소수점 자릿수입니다.",
        ),
        _dec(
            "saved_size_decimals",
            "예상 절약 소수점 자릿수",
            "예상 절약 열의 소수점 자릿수입니다.",
        ),
        b.combo(
            DisplayConfig,
            "size_unit",
            "용량 표기 단위",
            "용량 열의 단위입니다. 고정(MB, GB) 또는 크기에 맞춘 자동 표기 중 고릅니다.",
            (
                ("MB 고정", "mb"),
                ("GB 고정", "gb"),
                ("자동 (KB, MB, GB)", "auto"),
            ),
            row_key="display.size_unit",
        ),
        b.combo(
            DisplayConfig,
            "filename_align",
            "파일 이름 정렬",
            "표의 이름 열 글자를 왼쪽 또는 오른쪽에 맞춰 보여 줍니다. 오른쪽 붙음을 고르면 긴 파일 이름의 끝부분이 보입니다.",
            (
                ("왼쪽 붙음", "left"),
                ("오른쪽 붙음", "right"),
            ),
            row_key="display.filename_align",
        ),
    )
