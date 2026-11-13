"""GUI 라이트·다크 테마 색상 팔레트 데이터클래스 및 상수 정의 모듈"""

from dataclasses import dataclass

from src.gui.constants import ON_ACCENT_COLOR


@dataclass(frozen=True)
class ThemePalette:
    """테마를 구성하는 배경, 텍스트, 경계선, 뱃지 및 상태 색상 모음"""

    name: str
    is_dark: bool

    # 표면 및 배경
    bg_base: str
    bg_card: str
    bg_elevated: str
    bg_input: str
    bg_table_alt: str
    bg_hover: str
    bg_selected: str

    # 경계선
    border_subtle: str
    border_focus: str
    grid_line: str

    # 텍스트
    text_primary: str
    text_secondary: str
    text_muted: str

    # 액센트 및 버튼
    accent_blue: str
    btn_bg: str
    btn_border: str
    btn_hover: str

    # 카드 하이라이트
    card_highlight_bg: str
    card_highlight_border: str
    card_highlight_val: str
    subtitle_bg: str
    subtitle_border: str
    subtitle_text: str

    # 테이블 헤더
    header_bg: str
    header_text: str
    header_border: str
    header_hover: str

    # 스크롤바
    scrollbar_handle: str
    scrollbar_hover: str

    # 뱃지 색상: (배경색, 글자색, 테두리색)
    badge_over: tuple[str, str, str]
    badge_optimal: tuple[str, str, str]
    badge_low: tuple[str, str, str]
    badge_defect: tuple[str, str, str]
    badge_std_encode: tuple[str, str, str]
    badge_low_kf_encode: tuple[str, str, str]
    badge_pass: tuple[str, str, str]
    badge_moved: tuple[str, str, str]
    badge_pseudo: tuple[str, str, str]

    # 셀 텍스트 강조 전경색
    fg_ratio_over: str
    fg_ratio_opt: str
    fg_ratio_low: str
    fg_kf_defect: str
    fg_kf_opt: str
    fg_saved_pos: str
    fg_saved_neg: str

    # 시맨틱 액션 버튼 및 강조 상태 공용 색상
    text_on_accent: str = ON_ACCENT_COLOR
    action_primary_bg: str = "#2563EB"
    action_primary_hover: str = "#1D4ED8"
    action_primary_border: str = "#3B82F6"
    action_success_bg: str = "#059669"
    action_success_hover: str = "#047857"
    action_success_border: str = "#10B981"
    action_danger_bg: str = "#DC2626"
    action_danger_hover: str = "#B91C1C"
    action_danger_border: str = "#EF4444"


# 1. 다크 테마 팔레트
DARK_PALETTE = ThemePalette(
    name="dark",
    is_dark=True,
    bg_base="#121212",
    bg_card="#1C1C1C",
    bg_elevated="#262626",
    bg_input="#161616",
    bg_table_alt="#181818",
    bg_hover="#262626",
    bg_selected="#333333",
    border_subtle="#2E2E2E",
    border_focus="#525252",
    grid_line="#222222",
    text_primary="#EDEDED",
    text_secondary="#A3A3A3",
    text_muted="#666666",
    accent_blue="#3B82F6",
    btn_bg="#262626",
    btn_border="#383838",
    btn_hover="#303030",
    card_highlight_bg="#1A261E",
    card_highlight_border="#284832",
    card_highlight_val="#4ADE80",
    subtitle_bg="#242424",
    subtitle_border="#383838",
    subtitle_text="#D4D4D4",
    header_bg="#161616",
    header_text="#CCCCCC",
    header_border="#2E2E2E",
    header_hover="#222222",
    scrollbar_handle="#383838",
    scrollbar_hover="#4D4D4D",
    badge_over=("#2E1C1C", "#FCA5A5", "#4E2A2A"),
    badge_optimal=("#1A291F", "#86EFAC", "#274230"),
    badge_low=("#1C232E", "#93C5FD", "#283647"),
    badge_defect=("#33181E", "#FDA4AF", "#57242E"),
    badge_std_encode=("#241E2E", "#D8B4FE", "#3D314E"),
    badge_low_kf_encode=("#2E1C2B", "#F0ABFC", "#4E2A48"),
    badge_pass=("#242424", "#A3A3A3", "#383838"),
    badge_moved=("#1A291F", "#6EE7B7", "#284230"),
    badge_pseudo=("#262626", "#D4D4D4", "#404040"),
    fg_ratio_over="#FCA5A5",
    fg_ratio_opt="#86EFAC",
    fg_ratio_low="#93C5FD",
    fg_kf_defect="#FDA4AF",
    fg_kf_opt="#86EFAC",
    fg_saved_pos="#4ADE80",
    fg_saved_neg="#FBBF24",
)


# 2. 라이트 테마 팔레트
LIGHT_PALETTE = ThemePalette(
    name="light",
    is_dark=False,
    bg_base="#F5F6F8",
    bg_card="#FFFFFF",
    bg_elevated="#EAECEF",
    bg_input="#FFFFFF",
    bg_table_alt="#F9FAFB",
    bg_hover="#F1F3F5",
    bg_selected="#E2E8F0",
    border_subtle="#E2E8F0",
    border_focus="#94A3B8",
    grid_line="#F1F3F5",
    text_primary="#0F172A",
    text_secondary="#475569",
    text_muted="#94A3B8",
    accent_blue="#2563EB",
    btn_bg="#F1F5F9",
    btn_border="#CBD5E1",
    btn_hover="#E2E8F0",
    card_highlight_bg="#F0FDF4",
    card_highlight_border="#86EFAC",
    card_highlight_val="#15803D",
    subtitle_bg="#EFF6FF",
    subtitle_border="#BFDBFE",
    subtitle_text="#1D4ED8",
    header_bg="#F8FAFC",
    header_text="#334155",
    header_border="#E2E8F0",
    header_hover="#E2E8F0",
    scrollbar_handle="#CBD5E1",
    scrollbar_hover="#94A3B8",
    badge_over=("#FEF2F2", "#991B1B", "#FECACA"),
    badge_optimal=("#F0FDF4", "#166534", "#BBF7D0"),
    badge_low=("#EFF6FF", "#1E40AF", "#BFDBFE"),
    badge_defect=("#FFF1F2", "#9F1239", "#FECDD3"),
    badge_std_encode=("#FAF5FF", "#6B21A8", "#E9D5FF"),
    badge_low_kf_encode=("#FDF4FF", "#86198F", "#F5D0FE"),
    badge_pass=("#F1F5F9", "#475569", "#CBD5E1"),
    badge_moved=("#ECFDF5", "#065F46", "#A7F3D0"),
    badge_pseudo=("#F1F5F9", "#1E293B", "#94A3B8"),
    fg_ratio_over="#DC2626",
    fg_ratio_opt="#16A34A",
    fg_ratio_low="#2563EB",
    fg_kf_defect="#E11D48",
    fg_kf_opt="#16A34A",
    fg_saved_pos="#15803D",
    fg_saved_neg="#D97706",
)
