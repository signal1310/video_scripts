"""환경설정 대화상자 섹션별 카드 및 입력 위젯 생성, 툴팁 정의 모듈"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.config import (
    ENCODE_CODEC_CHOICES,
    BitrateModelConfig,
    CodecWeightsConfig,
    CuiConfig,
    DisplayConfig,
    EfficiencyConfig,
    PathsConfig,
    ThresholdsConfig,
)
from src.core.paths import DEFAULT_ROOT_DIR
from src.core.ui_state import DEFAULT_THEME_MODE
from src.gui.dialogs.setting_controls import (
    RESET_BUTTON_SIZE,
    ScrollSafeComboBox,
    ScrollSafeDoubleSpinBox,
)
from src.gui.dialogs.setting_row import (
    SettingRow,
    make_combo_row,
    make_float_spin_row,
    make_int_spin_row,
    make_text_row,
    resolve_field_bounds,
    resolve_field_default,
)
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.theme import ThemeManager
from src.gui.widgets.collapsible_section import CollapsibleSection
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox
from src.gui.widgets.settings_group_card import SettingsGroupCard


@dataclass
class SettingsFormFields:
    """환경설정 다이얼로그의 입력 위젯과 그룹 카드, 설정 행 맵을 보관하는 컨테이너"""

    rows: dict[str, SettingRow]
    cards: list[SettingsGroupCard]
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


def _build_env_card(
    rows: dict[str, SettingRow],
    cards: list[SettingsGroupCard],
    on_browse_root: Callable[[], None],
) -> tuple[
    SettingsGroupCard,
    QComboBox,
    QLineEdit,
    QPushButton,
    QLineEdit,
    QLineEdit,
    QComboBox,
    QDoubleSpinBox,
    QPushButton,
]:
    card_env = SettingsGroupCard(
        icon_name="folder",
        title="기본 환경 및 분류 폴더",
        subtitle="",
    )
    cards.append(card_env)

    combo_theme = ScrollSafeComboBox()
    p = get_active_palette()
    combo_theme.addItem(get_icon("monitor", palette=p, size=14, pad_right=6), "시스템 설정 따름", ThemeManager.MODE_SYSTEM)
    combo_theme.addItem(get_icon("moon", palette=p, size=14, pad_right=6), "다크", ThemeManager.MODE_DARK)
    combo_theme.addItem(get_icon("sun", palette=p, size=14, pad_right=6), "라이트", ThemeManager.MODE_LIGHT)
    combo_theme.setFixedWidth(max(146, combo_theme.sizeHint().width() + 16))

    row_theme = SettingRow(
        key="ui.theme_mode",
        title="화면 테마",
        desc="운영체제 테마 연동, 다크 또는 라이트 테마 중 하나를 선택합니다.",
        editor=combo_theme,
        default_val=DEFAULT_THEME_MODE,
        on_reset=lambda: combo_theme.setCurrentIndex(
            combo_theme.findData(DEFAULT_THEME_MODE)
        ),
    )
    rows["ui.theme_mode"] = row_theme
    card_env.add_row(row_theme)

    path_box = QWidget()
    path_layout = QHBoxLayout(path_box)
    path_layout.setContentsMargins(0, 0, 0, 0)
    path_layout.setSpacing(4)

    edit_root_dir = QLineEdit()
    edit_root_dir.setPlaceholderText("작업 폴더 경로...")
    edit_root_dir.setFixedWidth(164)

    btn_browse_root = QPushButton()
    btn_browse_root.setObjectName("IconActionButton")
    btn_browse_root.setIcon(get_icon("folder", palette=p, size=14))
    btn_browse_root.setToolTip("기본 작업 폴더를 선택합니다.")
    btn_browse_root.setFixedSize(RESET_BUTTON_SIZE, RESET_BUTTON_SIZE)
    btn_browse_root.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_browse_root.clicked.connect(on_browse_root)
    path_layout.addWidget(edit_root_dir)
    path_layout.addWidget(btn_browse_root)

    row_root = SettingRow(
        key="root_dir",
        title="기본 작업 폴더",
        desc="분석 대상 비디오 파일들이 위치한 기본 작업 폴더 경로를 지정합니다.",
        editor=path_box,
        default_val=DEFAULT_ROOT_DIR,
        on_reset=lambda: edit_root_dir.setText(DEFAULT_ROOT_DIR),
    )
    rows["root_dir"] = row_root
    card_env.add_row(row_root)

    edit_dir_std = make_text_row(
        rows,
        card_env,
        PathsConfig,
        "dir_standard_cq22",
        "표준 인코딩 폴더명",
        "과화질 및 키프레임 간격 초과 영상의 재인코딩 대상 하위 폴더 이름입니다.",
        width=172,
        row_key="paths.dir_standard_cq22",
    )
    edit_dir_low = make_text_row(
        rows,
        card_env,
        PathsConfig,
        "dir_low_quality_keyframe",
        "저화질 키프레임 폴더명",
        "저화질이면서 키프레임 간격이 초과된 영상이 노이즈 증폭 방지 프리셋으로 재인코딩 분류될 하위 폴더 이름입니다.",
        width=172,
        row_key="paths.dir_low_quality_keyframe",
    )

    combo_encode_codec = make_combo_row(
        rows,
        card_env,
        EfficiencyConfig,
        "encode_codec",
        "목표 인코딩 방식",
        (
            "영상을 인코딩할 목표 코덱, 장치를 선택합니다. "
            "방식을 바꾸면 현재 PC 사양에 맞는 1080p 예상 배속을 자동으로 다시 추천합니다."
        ),
        ENCODE_CODEC_CHOICES,
        row_key="efficiency.encode_codec",
    )

    speed_default = float(resolve_field_default(EfficiencyConfig, "encode_speed_factor"))
    speed_min, speed_max = resolve_field_bounds(EfficiencyConfig, "encode_speed_factor")
    speed_box = QWidget()
    speed_layout = QHBoxLayout(speed_box)
    speed_layout.setContentsMargins(0, 0, 0, 0)
    speed_layout.setSpacing(4)

    spin_encode_speed = ScrollSafeDoubleSpinBox()
    spin_encode_speed.setRange(speed_min, speed_max)
    spin_encode_speed.setSingleStep(0.5)
    spin_encode_speed.setDecimals(1)
    spin_encode_speed.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    spin_encode_speed.setSuffix(" 배속")
    spin_encode_speed.setFixedWidth(92)

    btn_detect_encode_speed = QPushButton("시스템 추천")
    btn_detect_encode_speed.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_detect_encode_speed.setToolTip(
        "현재 PC의 CPU 스레드와 GPU 하드웨어 인코더(NVENC/QSV/AMF) 지원 여부를 조회해\n"
        "1080p 30fps 기준 추천 인코딩 배속을 자동으로 입력합니다."
    )
    speed_layout.addWidget(spin_encode_speed)
    speed_layout.addWidget(btn_detect_encode_speed)

    row_speed = SettingRow(
        key="efficiency.encode_speed_factor",
        title="기준 인코딩 배속 (1080p)",
        desc=(
            "1080p 30fps 영상 기준 예상 인코딩 배속입니다. 각 영상의 해상도, FPS 가중치를 반영해 "
            "표와 하단 합계의 예상 시간을 추정합니다."
        ),
        editor=speed_box,
        default_val=speed_default,
        unit="배속",
        on_reset=lambda: spin_encode_speed.setValue(speed_default),
        value_widget=spin_encode_speed,
    )
    rows["efficiency.encode_speed_factor"] = row_speed
    card_env.add_row(row_speed)

    return (
        card_env,
        combo_theme,
        edit_root_dir,
        btn_browse_root,
        edit_dir_std,
        edit_dir_low,
        combo_encode_codec,
        spin_encode_speed,
        btn_detect_encode_speed,
    )


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


def _build_codecs_card(
    rows: dict[str, SettingRow],
    cards: list[SettingsGroupCard],
) -> tuple[
    SettingsGroupCard,
    QDoubleSpinBox,
    QDoubleSpinBox,
    QDoubleSpinBox,
    QDoubleSpinBox,
]:
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
            "최신 AV1 코덱은 H.264보다 적은 비트레이트로도 같은 화질을 냅니다. 이 비율만큼 기준선(최적 BR)을 낮춰 과화질, 저화질 판정 오류를 막습니다.",
            0.05,
            2,
            "배",
            row_key="analysis.codec_weights.av1",
        ),
        b.flt(
            CodecWeightsConfig,
            "hevc_vp9",
            "HEVC / VP9 코덱 가중치",
            "HEVC(H.265), VP9는 H.264보다 압축 효율이 높아 더 낮은 비트레이트에서도 동등 화질을 냅니다.",
            0.05,
            2,
            "배",
            row_key="analysis.codec_weights.hevc_vp9",
        ),
        b.flt(
            CodecWeightsConfig,
            "h264",
            "H.264 코덱 가중치",
            "모든 코덱 비교와 CQ22 기준선 계산의 중심 기준(1.00 고정 권장)입니다.",
            0.05,
            2,
            "배",
            row_key="analysis.codec_weights.h264",
        ),
        b.flt(
            CodecWeightsConfig,
            "legacy",
            "구형 코덱 가중치",
            "MPEG-4, WMV 같은 구형 코덱은 같은 화질을 내려면 H.264보다 더 높은 비트레이트가 필요해 기준선을 높여 줍니다.",
            0.05,
            2,
            "배",
            row_key="analysis.codec_weights.legacy",
        ),
    )


def _build_bitrate_card(
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
            "표준 CQ22 목표 비트레이트가 정의되는 기준 해상도의 너비입니다.",
            10,
            "px",
            row_key="analysis.bitrate_model.base_width",
        ),
        b.integer(
            BitrateModelConfig,
            "base_height",
            "기준 해상도 높이",
            "표준 CQ22 목표 비트레이트가 정의되는 기준 해상도의 높이입니다.",
            10,
            "px",
            row_key="analysis.bitrate_model.base_height",
        ),
        b.integer(
            BitrateModelConfig,
            "base_target_kbps",
            "기준 목표 비트레이트",
            "설정한 기준 해상도와 프레임레이트(H.264)에서의 CQ22 수준 목표 비트레이트입니다.",
            50,
            "kbps",
            row_key="analysis.bitrate_model.base_target_kbps",
        ),
        b.flt(
            BitrateModelConfig,
            "base_fps",
            "기준 프레임레이트",
            "비트레이트 스케일링의 기준점이 되는 표준 프레임레이트입니다.",
            1.0,
            1,
            "fps",
            row_key="analysis.bitrate_model.base_fps",
        ),
        b.flt(
            BitrateModelConfig,
            "area_scaling_exponent",
            "해상도 면적 스케일링 지수",
            "화면이 4배(예: HD→4K) 커져도 필요한 비트레이트는 비선형(약 0.72제곱)으로 완만하게 늘어나는 특성을 반영해 고해상도 과화질 판정을 막습니다.",
            0.01,
            2,
            row_key="analysis.bitrate_model.area_scaling_exponent",
        ),
        b.flt(
            BitrateModelConfig,
            "fps_scaling_exponent",
            "프레임레이트 보정 지수",
            "60fps 같은 고주사율 영상은 프레임 간 중복이 많아 비트레이트가 2배까지 필요하지 않은 점을 보정합니다.",
            0.01,
            2,
            row_key="analysis.bitrate_model.fps_scaling_exponent",
        ),
    )


def _build_overhead_and_cui_card(
    rows: dict[str, SettingRow],
    cards: list[SettingsGroupCard],
) -> tuple[
    SettingsGroupCard,
    QDoubleSpinBox,
    CustomIntegerSpinBox,
]:
    card = SettingsGroupCard(
        icon_name="bar-chart", title="키프레임 보정 및 콘솔 출력", subtitle=""
    )
    cards.append(card)
    b = _CardRowBuilder(card, rows)
    return (
        card,
        b.flt(
            ThresholdsConfig,
            "keyframe_1s_overhead_rate",
            "키프레임 재인코딩 용량 증가율",
            "키프레임 간격이 긴 저화질 영상을 1초 간격으로 재인코딩하면 I-프레임이 늘어 용량이 소폭 증가합니다. 예상 용량 증가율 추정에 사용합니다.",
            0.01,
            2,
            "배",
            row_key="analysis.thresholds.keyframe_1s_overhead_rate",
        ),
        b.integer(
            CuiConfig,
            "filename_maxlen",
            "콘솔 파일명 최대 길이",
            "주피터 노트북과 터미널 출력에서 파일명 열의 최대 글자 폭을 지정합니다 (초과 시 말줄임표로 표시합니다).",
            5,
            "자",
            row_key="cui.filename_maxlen",
        ),
    )


def _build_display_card(
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
]:
    card = SettingsGroupCard(
        icon_name="sliders",
        title="표 표시 서식",
        subtitle="소수점 자릿수, 용량 단위",
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
            "CUI와 GUI 표의 FPS 열에 표시할 최대 소수점 자릿수입니다.",
        ),
        b.combo(
            DisplayConfig,
            "fps_trim_whole",
            "FPS 정수 표기 방식",
            "FPS 반올림 결과가 정수(.00)일 때 소수점을 생략해 정수로 출력할지 선택합니다.",
            (
                ("정수 생략 (30)", True),
                ("고정 (30.00)", False),
            ),
            row_key="display.fps_trim_whole",
        ),
        _dec(
            "duration_decimals",
            "길이(초) 소수점 자릿수",
            "CUI 표의 길이(초) 열에 표시할 소수점 자릿수입니다.",
        ),
        _dec(
            "ratio_decimals",
            "BR 비율 소수점 자릿수",
            "CUI와 GUI 표의 비트레이트 비율 열에 표시할 소수점 자릿수입니다.",
        ),
        _dec(
            "keyframe_decimals",
            "키프레임 간격 소수점 자릿수",
            "CUI와 GUI 표의 키프레임 간격(초) 열에 표시할 소수점 자릿수입니다.",
        ),
        _dec(
            "size_decimals",
            "현재 용량 소수점 자릿수",
            "CUI와 GUI 표의 현재 용량 열에 표시할 소수점 자릿수입니다.",
        ),
        _dec(
            "after_size_decimals",
            "예상 용량 소수점 자릿수",
            "CUI와 GUI 표의 예상 용량 열에 표시할 소수점 자릿수입니다.",
        ),
        _dec(
            "saved_size_decimals",
            "예상 절약 소수점 자릿수",
            "CUI와 GUI 표의 예상 절약 열에 표시할 소수점 자릿수입니다.",
        ),
        b.combo(
            DisplayConfig,
            "size_unit",
            "용량 표기 단위",
            "용량 열을 고정 단위(MB, GB) 또는 크기별 자동 스케일(UNIX -h 방식) 중 하나로 표시합니다.",
            (
                ("MB 고정", "mb"),
                ("GB 고정", "gb"),
                ("자동 (KB, MB, GB)", "auto"),
            ),
            row_key="display.size_unit",
        ),
    )


def build_settings_form(
    general_layout: QVBoxLayout,
    advanced_section: CollapsibleSection,
    on_browse_root: Callable[[], None],
) -> SettingsFormFields:
    """환경설정 기본, 고급 그룹 카드와 각 입력 위젯 조립, 배치"""
    rows: dict[str, SettingRow] = {}
    cards: list[SettingsGroupCard] = []

    (
        card_env,
        combo_theme,
        edit_root_dir,
        btn_browse_root,
        edit_dir_std,
        edit_dir_low,
        combo_encode_codec,
        spin_encode_speed,
        btn_detect_encode_speed,
    ) = _build_env_card(rows, cards, on_browse_root)
    general_layout.addWidget(card_env)

    card_codecs, spin_w_av1, spin_w_hevc, spin_w_h264, spin_w_legacy = (
        _build_codecs_card(rows, cards)
    )
    advanced_section.add_widget(card_codecs)

    (
        card_scaling,
        spin_base_w,
        spin_base_h,
        spin_base_bitrate,
        spin_base_fps,
        spin_area_exp,
        spin_fps_exp,
    ) = _build_bitrate_card(rows, cards)
    advanced_section.add_widget(card_scaling)

    card_overhead_cui, spin_kf_overhead, spin_cui_maxlen = (
        _build_overhead_and_cui_card(rows, cards)
    )
    advanced_section.add_widget(card_overhead_cui)

    (
        card_display,
        spin_fps_decimals,
        combo_fps_trim_whole,
        spin_duration_decimals,
        spin_ratio_decimals,
        spin_keyframe_decimals,
        spin_size_decimals,
        spin_after_size_decimals,
        spin_saved_size_decimals,
        combo_size_unit,
    ) = _build_display_card(rows, cards)
    advanced_section.add_widget(card_display)

    return SettingsFormFields(
        rows=rows,
        cards=cards,
        combo_theme=combo_theme,
        edit_root_dir=edit_root_dir,
        btn_browse_root=btn_browse_root,
        edit_dir_std=edit_dir_std,
        edit_dir_low=edit_dir_low,
        combo_encode_codec=combo_encode_codec,
        spin_encode_speed=spin_encode_speed,
        btn_detect_encode_speed=btn_detect_encode_speed,
        spin_w_av1=spin_w_av1,
        spin_w_hevc=spin_w_hevc,
        spin_w_h264=spin_w_h264,
        spin_w_legacy=spin_w_legacy,
        spin_base_w=spin_base_w,
        spin_base_h=spin_base_h,
        spin_base_bitrate=spin_base_bitrate,
        spin_base_fps=spin_base_fps,
        spin_area_exp=spin_area_exp,
        spin_fps_exp=spin_fps_exp,
        spin_kf_overhead=spin_kf_overhead,
        spin_cui_maxlen=spin_cui_maxlen,
        spin_fps_decimals=spin_fps_decimals,
        combo_fps_trim_whole=combo_fps_trim_whole,
        spin_duration_decimals=spin_duration_decimals,
        spin_ratio_decimals=spin_ratio_decimals,
        spin_keyframe_decimals=spin_keyframe_decimals,
        spin_size_decimals=spin_size_decimals,
        spin_after_size_decimals=spin_after_size_decimals,
        spin_saved_size_decimals=spin_saved_size_decimals,
        combo_size_unit=combo_size_unit,
    )
