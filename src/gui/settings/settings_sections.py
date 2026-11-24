"""일반, 해상도 및 분석 범주 페이지의 카드와 입력 위젯 생성 모듈"""

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.config import (
    FRAME_ANALYSIS_MODE_CHOICES,
    NO_MAX_RESOLUTION,
    DisplayConfig,
    EncodeConfig,
)
from src.core.paths import DEFAULT_ROOT_DIR
from src.core.resolution_tiers import RESOLUTION_TIERS
from src.core.settings.ui_state import DEFAULT_THEME_MODE
from src.gui.constants import make_path_font
from src.gui.settings.collapsible_section import CollapsibleSection
from src.gui.settings.setting_controls import (
    RESET_BUTTON_SIZE,
    ScrollSafeComboBox,
)
from src.gui.settings.setting_row import SettingRow, make_combo_row
from src.gui.settings.settings_advanced_cards import (
    build_console_card,
    build_display_format_card,
    build_frame_threshold_card,
)
from src.gui.settings.settings_form_fields import SettingsPageForm
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.theme import ThemeManager

# - 범주 페이지 제목은 사이드바 항목 이름과 같게 유지해 진입점과 화면 이름이 어긋나지 않게 함
GENERAL_PAGE_TITLE: str = "일반"
RESOLUTION_PAGE_TITLE: str = "해상도 및 분석"


def _build_theme_row(rows: dict[str, SettingRow], card: SettingsGroupCard) -> QComboBox:
    p = get_active_palette()
    combo_theme = ScrollSafeComboBox()
    combo_theme.addItem(
        get_icon("monitor", palette=p, size=14, pad_right=6),
        "시스템 설정 따름",
        ThemeManager.MODE_SYSTEM,
    )
    combo_theme.addItem(
        get_icon("moon", palette=p, size=14, pad_right=6), "다크", ThemeManager.MODE_DARK
    )
    combo_theme.addItem(
        get_icon("sun", palette=p, size=14, pad_right=6), "라이트", ThemeManager.MODE_LIGHT
    )
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
    btn_browse_root.setAccessibleName("작업 폴더 선택")
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
        value_widget=edit_root_dir,
    )
    rows["root_dir"] = row_root
    card.add_row(row_root)
    return edit_root_dir, btn_browse_root


_MAX_RESOLUTION_CHOICES: tuple[tuple[str, str], ...] = (
    ("제한 없음 (기본)", NO_MAX_RESOLUTION),
    *(
        (f"{tier.display} ({tier.long_side}x{tier.short_side})", tier.key)
        for tier in reversed(RESOLUTION_TIERS)
    ),
)

_FRAME_ANALYSIS_MIN_TIER_CHOICES: tuple[tuple[str, str], ...] = (
    ("전체", "all"),
    *((f"{tier.display} 이상", tier.key) for tier in RESOLUTION_TIERS),
)

_ON_OFF_CHOICES: tuple[tuple[str, bool], ...] = (
    ("사용 (기본)", True),
    ("사용 안 함", False),
)


def build_general_page(
    body_layout: QVBoxLayout,
    advanced: CollapsibleSection,
    on_browse_root: Callable[[], None],
) -> SettingsPageForm:
    """일반 범주 페이지(기본 환경, 표시, 고급 표 서식과 콘솔 출력) 조립"""
    rows: dict[str, SettingRow] = {}
    cards: list[SettingsGroupCard] = []

    card_env = SettingsGroupCard(icon_name="folder", title="기본 환경")
    cards.append(card_env)
    combo_theme = _build_theme_row(rows, card_env)
    edit_root_dir, btn_browse_root = _build_root_dir_row(rows, card_env, on_browse_root)
    body_layout.addWidget(card_env)

    card_display = SettingsGroupCard(icon_name="sliders", title="표시")
    cards.append(card_display)
    make_combo_row(
        rows,
        card_display,
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
    )
    make_combo_row(
        rows,
        card_display,
        DisplayConfig,
        "filename_align",
        "파일 이름 정렬",
        "표의 이름 열 글자를 왼쪽 또는 오른쪽에 맞춰 보여 줍니다. 오른쪽 붙음을 고르면 긴 파일 이름의 끝부분이 보입니다.",
        (
            ("왼쪽 붙음", "left"),
            ("오른쪽 붙음", "right"),
        ),
        row_key="display.filename_align",
    )
    body_layout.addWidget(card_display)

    card_format = build_display_format_card(rows)
    cards.append(card_format)
    advanced.add_widget(card_format)
    card_console = build_console_card(rows)
    cards.append(card_console)
    advanced.add_widget(card_console)
    return SettingsPageForm(
        rows=rows,
        cards=cards,
        advanced=advanced,
        extras={
            "combo_theme": combo_theme,
            "edit_root_dir": edit_root_dir,
            "btn_browse_root": btn_browse_root,
        },
    )



def build_resolution_page(
    body_layout: QVBoxLayout,
    advanced: CollapsibleSection,
) -> SettingsPageForm:
    """해상도 및 분석 범주 페이지(화질 분석 및 검은 여백, 자동 해상도 축소, 고급 기준) 조립"""
    rows: dict[str, SettingRow] = {}
    cards: list[SettingsGroupCard] = []

    card_analysis = SettingsGroupCard(
        icon_name="analyze",
        title="화질 분석 및 검은 여백",
        help_anchor="frame-analysis",
    )
    cards.append(card_analysis)
    make_combo_row(
        rows,
        card_analysis,
        EncodeConfig,
        "crop_black_bars",
        "검은 여백 잘라내기",
        "상하(레터박스), 좌우(필러박스), 4면의 고정된 검은 여백을 자동으로 감지해 잘라냅니다.",
        _ON_OFF_CHOICES,
        row_key="analysis.encode.crop_black_bars",
    )
    make_combo_row(
        rows,
        card_analysis,
        EncodeConfig,
        "frame_analysis_mode",
        "화질 분석 방식",
        "축소 후보 해상도를 찾는 화질 분석의 구간 길이와 탐색 방식을 고릅니다. "
        "'빠름'은 0.5초 키프레임 구간으로 가장 빠르게 검사하고, "
        "'균형'은 1초 키프레임 구간으로 속도와 정확도를 맞추며, "
        "'정밀'은 2초 정밀 구간으로 경계 영상을 가장 꼼꼼하게 확인합니다.",
        FRAME_ANALYSIS_MODE_CHOICES,
        row_key="analysis.encode.frame_analysis_mode",
    )
    make_combo_row(
        rows,
        card_analysis,
        EncodeConfig,
        "frame_analysis_min_tier",
        "화질 분석 대상 해상도",
        "이 해상도 이상인 영상만 축소 후보 측정을 합니다. 그보다 작은 영상은 검은 여백만 빠르게 확인합니다.",
        _FRAME_ANALYSIS_MIN_TIER_CHOICES,
        row_key="analysis.encode.frame_analysis_min_tier",
    )
    make_combo_row(
        rows,
        card_analysis,
        EncodeConfig,
        "auto_apply_frame_target",
        "화질 분석 축소 후보 자동 적용",
        "화질 분석에서 찾은 축소 후보를 출력 해상도와 권장 처리에 자동으로 반영합니다. "
        "끄면 후보만 표시하고 표 우클릭으로 직접 적용합니다.",
        _ON_OFF_CHOICES,
        row_key="analysis.encode.auto_apply_frame_target",
    )
    body_layout.addWidget(card_analysis)

    card_downscale = SettingsGroupCard(icon_name="minimize-2", title="자동 해상도 축소")
    cards.append(card_downscale)
    make_combo_row(
        rows,
        card_downscale,
        EncodeConfig,
        "max_resolution_tier",
        "최대 해상도",
        "원본 해상도가 이 규격을 넘는 영상을 이 규격으로 줄여 재인코딩합니다. "
        "줄인 영상에는 최대 비트레이트가 적용되어 움직임이 많은 장면은 화질이 다소 낮아질 수 있습니다. "
        "축소 후에도 저화질 보호 대상이면 축소하지 않고 원본 해상도 기준으로 판정합니다.",
        _MAX_RESOLUTION_CHOICES,
        row_key="analysis.encode.max_resolution_tier",
    )
    make_combo_row(
        rows,
        card_downscale,
        EncodeConfig,
        "auto_downscale_by_bitrate",
        "비트레이트 맞춤 축소",
        "비트레이트가 낮은 고해상도 영상을 적정 해상도로 줄입니다. 화면 녹화, 애니메이션처럼 "
        "비트레이트가 낮아도 세부가 중요한 영상은 글자가 뭉개질 수 있으니 필요한 경우에만 켜고, "
        "예외 규칙으로 특정 영상을 제외해 주세요.",
        (
            ("사용 안 함 (기본)", False),
            ("사용", True),
        ),
        row_key="analysis.encode.auto_downscale_by_bitrate",
    )
    body_layout.addWidget(card_downscale)

    card_threshold = build_frame_threshold_card(rows)
    cards.append(card_threshold)
    advanced.add_widget(card_threshold)
    return SettingsPageForm(rows=rows, cards=cards, advanced=advanced)

