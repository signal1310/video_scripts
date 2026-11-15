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
    EncodeConfig,
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
        desc="화면 색상 모드를 고릅니다. '시스템 설정 따름'을 고르면 운영체제의 다크, 라이트 설정이 바뀔 때 이 프로그램도 함께 바뀝니다.",
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
        desc="프로그램을 열 때 기본으로 사용할 작업 폴더입니다. 오른쪽 폴더 버튼으로 고르거나 경로를 직접 입력할 수 있습니다.",
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
        "분류 이동을 실행하면 과화질 영상과 키프레임 간격 초과 영상이 모이는 하위 폴더 이름입니다. 샤나인코더의 CQ 22 프리셋으로 재인코딩할 대상 폴더입니다.",
        width=172,
        row_key="paths.dir_standard_cq22",
    )
    edit_dir_low = make_text_row(
        rows,
        card_env,
        PathsConfig,
        "dir_low_quality_keyframe",
        "저화질 키프레임 폴더명",
        "이미 저화질인데 키프레임 간격만 긴 영상이 모이는 하위 폴더 이름입니다. 압축 노이즈가 부풀지 않도록 따로 모아 더 가벼운 CQ 26 프리셋으로 재인코딩합니다.",
        width=172,
        row_key="paths.dir_low_quality_keyframe",
    )

    combo_encode_codec = make_combo_row(
        rows,
        card_env,
        EncodeConfig,
        "encode_codec",
        "목표 인코딩 방식",
        (
            "영상을 인코딩할 목표 코덱과 장치(CPU 또는 GPU)를 선택합니다. 예상 시간 계산에 쓰입니다. "
            "방식을 바꾸면 현재 PC 사양에 맞는 1080p 예상 배속을 자동으로 다시 추천합니다. 실제 화질은 샤나인코더 프리셋이 정합니다."
        ),
        ENCODE_CODEC_CHOICES,
        row_key="encode.encode_codec",
    )

    speed_default = float(resolve_field_default(EncodeConfig, "encode_speed_factor"))
    speed_min, speed_max = resolve_field_bounds(EncodeConfig, "encode_speed_factor")
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
        key="encode.encode_speed_factor",
        title="기준 인코딩 배속 (1080p)",
        desc=(
            "1080p 30fps 영상을 영상 길이의 몇 배 속도로 인코딩하는지 나타냅니다. 2.0배속이면 10분 영상에 약 5분이 걸립니다. "
            "해상도, FPS가 큰 영상은 더 오래 걸리는 것으로 계산해 표와 하단 합계의 예상 시간에 반영합니다. 모르면 시스템 추천을 누르세요."
        ),
        editor=speed_box,
        default_val=speed_default,
        unit="배속",
        on_reset=lambda: spin_encode_speed.setValue(speed_default),
        value_widget=spin_encode_speed,
    )
    rows["encode.encode_speed_factor"] = row_speed
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
            "HEVC(H.265)와 VP9도 H.264보다 압축 효율이 좋아 더 낮은 비트레이트에서 같은 화질을 냅니다. 값이 작을수록 이 코덱 영상의 기준선(최적 BR)이 낮아집니다.",
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
            "기준선이 되는 해상도의 가로 픽셀 수입니다. 다른 영상은 이 해상도와 화면 면적을 비교해 필요한 비트레이트를 계산합니다.",
            10,
            "px",
            row_key="analysis.bitrate_model.base_width",
        ),
        b.integer(
            BitrateModelConfig,
            "base_height",
            "기준 해상도 높이",
            "기준선이 되는 해상도의 세로 픽셀 수입니다. 가로와 곱한 면적이 다른 영상과의 비교 기준이 됩니다.",
            10,
            "px",
            row_key="analysis.bitrate_model.base_height",
        ),
        b.integer(
            BitrateModelConfig,
            "base_target_kbps",
            "기준 목표 비트레이트",
            "위 기준 해상도와 프레임레이트의 H.264 영상이 CQ 22 화질을 내기에 충분하다고 보는 비트레이트입니다. 모든 계산의 출발점이라 올리면 과화질 판정이 줄고, 내리면 늘어납니다.",
            50,
            "kbps",
            row_key="analysis.bitrate_model.base_target_kbps",
        ),
        b.flt(
            BitrateModelConfig,
            "base_fps",
            "기준 프레임레이트",
            "기준선이 되는 프레임레이트입니다. 이보다 프레임이 많은 영상은 비트레이트가 더 필요하다고 보정하며, 미세한 차이는 무시합니다.",
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
            "프레임레이트 보정 지수",
            "프레임이 2배(30fps에서 60fps)가 돼도 앞뒤 프레임이 서로 비슷해 비트레이트가 2배까지는 필요하지 않습니다. 1에 가까울수록 프레임 수에 비례해 늘어납니다.",
            0.01,
            2,
            row_key="analysis.bitrate_model.fps_scaling_exponent",
        ),
    )


def _build_increase_and_cui_card(
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

    card_increase_cui, spin_kf_increase, spin_cui_maxlen = (
        _build_increase_and_cui_card(rows, cards)
    )
    advanced_section.add_widget(card_increase_cui)

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
        spin_kf_increase=spin_kf_increase,
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
