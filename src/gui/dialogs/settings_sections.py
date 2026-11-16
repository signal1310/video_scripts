"""환경설정 대화상자 섹션별 카드 및 입력 위젯 생성, 툴팁 정의 모듈"""

from collections.abc import Callable

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
    NO_MAX_RESOLUTION,
    SAVINGS_BASIS_CHOICES,
    EncodeConfig,
    PathsConfig,
    SavingsConfig,
)
from src.core.paths import DEFAULT_ROOT_DIR
from src.core.resolution_tiers import RESOLUTION_TIERS
from src.core.ui_state import DEFAULT_THEME_MODE
from src.gui.constants import make_path_font
from src.gui.dialogs.setting_controls import (
    RESET_BUTTON_SIZE,
    ScrollSafeComboBox,
    ScrollSafeDoubleSpinBox,
)
from src.gui.dialogs.setting_row import (
    SettingRow,
    make_combo_row,
    make_text_row,
    resolve_field_bounds,
    resolve_field_default,
)
from src.gui.dialogs.settings_advanced_cards import (
    build_bitrate_card,
    build_codecs_card,
    build_display_card,
    build_increase_and_cui_card,
)
from src.gui.dialogs.settings_form_fields import SettingsFormFields
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.theme import ThemeManager
from src.gui.widgets.collapsible_section import CollapsibleSection
from src.gui.widgets.settings_group_card import SettingsGroupCard


def _build_theme_row(rows: dict[str, SettingRow], card: SettingsGroupCard) -> QComboBox:
    p = get_active_palette()
    combo_theme = ScrollSafeComboBox()
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
    card.add_row(row_theme)
    return combo_theme


def _build_root_dir_row(
    rows: dict[str, SettingRow],
    card: SettingsGroupCard,
    on_browse_root: Callable[[], None],
) -> tuple[QLineEdit, QPushButton]:
    p = get_active_palette()
    path_box = QWidget()
    path_layout = QHBoxLayout(path_box)
    path_layout.setContentsMargins(0, 0, 0, 0)
    path_layout.setSpacing(4)

    edit_root_dir = QLineEdit()
    edit_root_dir.setFont(make_path_font())
    edit_root_dir.setPlaceholderText("작업 폴더 경로...")
    edit_root_dir.setFixedWidth(164)

    btn_browse_root = QPushButton()
    btn_browse_root.setObjectName("IconActionButton")
    btn_browse_root.setIcon(get_icon("folder", palette=p, size=14))
    btn_browse_root.setToolTip("작업 폴더를 선택합니다.")
    btn_browse_root.setFixedSize(RESET_BUTTON_SIZE, RESET_BUTTON_SIZE)
    btn_browse_root.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_browse_root.clicked.connect(on_browse_root)
    path_layout.addWidget(edit_root_dir)
    path_layout.addWidget(btn_browse_root)

    row_root = SettingRow(
        key="root_dir",
        title="작업 폴더",
        desc="프로그램을 열 때 기본으로 사용할 작업 폴더입니다. 오른쪽 폴더 버튼으로 고르거나 경로를 직접 입력할 수 있습니다.",
        editor=path_box,
        default_val=DEFAULT_ROOT_DIR,
        on_reset=lambda: edit_root_dir.setText(DEFAULT_ROOT_DIR),
    )
    rows["root_dir"] = row_root
    card.add_row(row_root)
    return edit_root_dir, btn_browse_root


def _build_speed_row(
    rows: dict[str, SettingRow],
    card: SettingsGroupCard,
) -> tuple[QDoubleSpinBox, QPushButton]:
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
        title="기준 인코딩 배속 (1080p 30fps)",
        desc=(
            "1080p 30fps 영상을 영상 길이의 몇 배 속도로 인코딩하는지 나타냅니다. 2.0배속이면 10분 영상에 약 5분이 걸립니다. "
            "해상도와 FPS가 큰 영상은 화소 처리량에 비례해 더 오래 걸리는 것으로 계산해 표와 하단 합계의 예상 시간에 반영합니다. 모르면 시스템 추천을 누르세요."
        ),
        editor=speed_box,
        default_val=speed_default,
        unit="배속",
        on_reset=lambda: spin_encode_speed.setValue(speed_default),
        value_widget=spin_encode_speed,
    )
    rows["encode.encode_speed_factor"] = row_speed
    card.add_row(row_speed)
    return spin_encode_speed, btn_detect_encode_speed


_MAX_RESOLUTION_CHOICES: tuple[tuple[str, str], ...] = (
    ("제한 없음 (기본)", NO_MAX_RESOLUTION),
    *(
        (f"{tier.display} ({tier.long_side}x{tier.short_side})", tier.key)
        for tier in reversed(RESOLUTION_TIERS)
    ),
)


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
    QLineEdit,
    QComboBox,
    QDoubleSpinBox,
    QPushButton,
    QComboBox,
    QComboBox,
    QComboBox,
    QComboBox,
]:
    card_env = SettingsGroupCard(
        icon_name="folder",
        title="기본 환경 및 분류 폴더",
        subtitle="",
    )
    cards.append(card_env)

    combo_theme = _build_theme_row(rows, card_env)
    edit_root_dir, btn_browse_root = _build_root_dir_row(rows, card_env, on_browse_root)

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
    edit_dir_downscale = make_text_row(
        rows,
        card_env,
        PathsConfig,
        "dir_downscale_prefix",
        "축소 재인코딩 폴더명 앞부분",
        "축소 재인코딩 영상이 모이는 하위 폴더 이름의 앞부분입니다. 뒤에 규격 키(360p ... 4K)가 붙습니다. 이 값을 바꾸면 이전 이름으로 분류된 폴더는 작업 폴더로 복원 대상에서 빠집니다.",
        width=172,
        row_key="paths.dir_downscale_prefix",
    )

    combo_encode_codec = make_combo_row(
        rows,
        card_env,
        EncodeConfig,
        "encode_codec",
        "목표 인코딩 방식",
        (
            "영상을 인코딩할 목표 코덱과 장치(CPU 또는 GPU)를 선택합니다. 예상 시간 계산과 과화질 판정(출력 BR 비율), 예상 용량에 쓰입니다. "
            "방식을 바꾸면 현재 PC 사양에 맞는 1080p 예상 배속을 자동으로 다시 추천합니다. 실제 화질은 샤나인코더 프리셋이 정합니다."
        ),
        ENCODE_CODEC_CHOICES,
        row_key="encode.encode_codec",
    )

    spin_encode_speed, btn_detect_encode_speed = _build_speed_row(rows, card_env)

    combo_savings_basis = make_combo_row(
        rows, card_env, SavingsConfig, "selection_basis", "절약 선별 기준",
        "과화질 영상 중 재인코딩 작업 1분당 절약이 기준보다 적은 영상을 건너뜁니다. "
        "재생 시간 기준은 영상 재생 시간 1분당 절약을, 예상 인코딩 시간 기준은 인코딩에 걸릴 시간 1분당 절약을 봅니다. "
        "재생 시간 기준만 대용량 절약 예외를 적용합니다.",
        SAVINGS_BASIS_CHOICES, row_key="analysis.savings.selection_basis",
    )

    combo_force_legacy_compat = make_combo_row(
        rows,
        card_env,
        EncodeConfig,
        "force_legacy_compat",
        "구형 코덱 호환 재인코딩",
        (
            "하드웨어 디코딩을 지원하지 않는 구형 코덱(WMV3, VC-1, VP8, FLV1 등)을 "
            "절약 선별 없이 표준 코덱과 1초 키프레임으로 재인코딩합니다."
        ),
        (
            ("사용 안 함 (기본)", False),
            ("사용", True),
        ),
        row_key="encode.force_legacy_compat",
    )

    combo_max_resolution = make_combo_row(
        rows,
        card_env,
        EncodeConfig,
        "max_resolution_tier",
        "최대 해상도",
        (
            "원본 해상도가 이 규격을 넘는 영상을 이 규격으로 줄여 재인코딩합니다. "
            "줄인 영상에는 최대 비트레이트가 적용되어 움직임이 많은 장면은 화질이 다소 낮아질 수 있습니다. "
            "축소 후에도 저화질 보호 대상이면 축소하지 않고 원본 해상도 기준으로 판정합니다."
        ),
        _MAX_RESOLUTION_CHOICES,
        row_key="encode.max_resolution_tier",
    )

    combo_auto_downscale_by_bitrate = make_combo_row(
        rows,
        card_env,
        EncodeConfig,
        "auto_downscale_by_bitrate",
        "비트레이트 맞춤 축소",
        (
            "비트레이트가 낮은 고해상도 영상을 적정 해상도로 줄입니다. 화면 녹화, 애니메이션처럼 "
            "비트레이트가 낮아도 세부가 중요한 영상은 글자가 뭉개질 수 있으니 필요한 경우에만 켜고, "
            "예외 규칙으로 특정 영상을 제외해 주세요."
        ),
        (
            ("사용 안 함 (기본)", False),
            ("사용", True),
        ),
        row_key="encode.auto_downscale_by_bitrate",
    )

    return (
        card_env,
        combo_theme,
        edit_root_dir,
        btn_browse_root,
        edit_dir_std,
        edit_dir_low,
        edit_dir_downscale,
        combo_encode_codec,
        spin_encode_speed,
        btn_detect_encode_speed,
        combo_savings_basis,
        combo_force_legacy_compat,
        combo_max_resolution,
        combo_auto_downscale_by_bitrate,
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
        edit_dir_downscale,
        combo_encode_codec,
        spin_encode_speed,
        btn_detect_encode_speed,
        combo_savings_basis,
        combo_force_legacy_compat,
        combo_max_resolution,
        combo_auto_downscale_by_bitrate,
    ) = _build_env_card(rows, cards, on_browse_root)
    general_layout.addWidget(card_env)

    card_codecs, spin_w_av1, spin_w_hevc, spin_w_h264, spin_w_legacy = (
        build_codecs_card(rows, cards)
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
    ) = build_bitrate_card(rows, cards)
    advanced_section.add_widget(card_scaling)

    card_increase_cui, spin_kf_increase, spin_cui_maxlen = (
        build_increase_and_cui_card(rows, cards)
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
    ) = build_display_card(rows, cards)
    advanced_section.add_widget(card_display)

    return SettingsFormFields(
        rows=rows,
        cards=cards,
        combo_theme=combo_theme,
        edit_root_dir=edit_root_dir,
        btn_browse_root=btn_browse_root,
        edit_dir_std=edit_dir_std,
        edit_dir_low=edit_dir_low,
        edit_dir_downscale=edit_dir_downscale,
        combo_encode_codec=combo_encode_codec,
        spin_encode_speed=spin_encode_speed,
        btn_detect_encode_speed=btn_detect_encode_speed,
        combo_savings_basis=combo_savings_basis,
        combo_force_legacy_compat=combo_force_legacy_compat,
        combo_max_resolution=combo_max_resolution,
        combo_auto_downscale_by_bitrate=combo_auto_downscale_by_bitrate,
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
