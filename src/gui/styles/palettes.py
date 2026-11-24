"""GUI 라이트, 다크 테마 색상 팔레트 데이터클래스 및 상수 정의 모듈"""

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
    input_selection_bg: str

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
    accent_orange: str
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

    # 오버레이
    drop_overlay_bg: str

    # 뱃지 색상: (배경색, 글자색, 테두리색)
    badge_over: tuple[str, str, str]
    badge_optimal: tuple[str, str, str]
    badge_low: tuple[str, str, str]
    badge_exceeded: tuple[str, str, str]
    badge_std_encode: tuple[str, str, str]
    badge_low_kf_encode: tuple[str, str, str]
    badge_pass: tuple[str, str, str]
    badge_moved: tuple[str, str, str]
    badge_pending: tuple[str, str, str]
    badge_exception: tuple[str, str, str]
    badge_encode_running: tuple[str, str, str]

    # 재인코딩 진행 행 강조: 행 배경색과 파일명 열 왼쪽 띠 색
    row_running_bg: str
    row_running_accent: str
    row_paused_bg: str
    row_paused_accent: str

    # 셀 텍스트 강조 전경색
    fg_cell_muted: str
    fg_ratio_over: str
    fg_ratio_opt: str
    fg_ratio_low: str
    fg_kf_exceeded: str
    fg_kf_opt: str
    fg_saved_pos: str
    fg_saved_neg: str
    fg_maxrate_warn: str

    # 시맨틱 액션 버튼 및 강조 상태 공용 색상
    text_on_accent: str = ON_ACCENT_COLOR
    split_divider: str = "rgba(255, 255, 255, 0.25)"
    action_primary_bg: str = "#2563EB"
    action_primary_hover: str = "#1D4ED8"
    action_primary_border: str = "#3B82F6"
    action_success_bg: str = "#059669"
    action_success_hover: str = "#047857"
    action_success_border: str = "#10B981"
    action_danger_bg: str = "#DC2626"
    action_danger_hover: str = "#B91C1C"
    action_danger_border: str = "#EF4444"

    # 진행 막대: 평면 트랙과 단색 채움
    progress_track_bg: str = "#262626"
    progress_fill: str = "#2563EB"

    # 미리 보기 조작 바: 검은 레터박스와 이어지도록 테마와 무관하게 어두운 고정 표면
    media_bg: str = "#0B0B0B"
    media_fg: str = "#F5F5F5"
    media_muted: str = "#8C8C8C"
    media_track: str = "#333333"
    media_overlay_bg: str = "rgba(11, 11, 11, 56)"
    media_panel_bg: str = "rgba(11, 11, 11, 224)"
    media_overlay_label: str = "#D2D2D2"
    media_overlay_outline: str = "#000000"
    # 단축키 안내처럼 영상 위에 겹치는 HUD: 뒤 영상이 은은하게 비치는 반투명 키캡
    media_hud_bg: str = "rgba(11, 11, 11, 196)"
    media_keycap_bg: str = "rgba(255, 255, 255, 20)"
    media_keycap_border: str = "rgba(255, 255, 255, 42)"


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
    bg_selected="#233E63",
    input_selection_bg="#2B4C7E",
    border_subtle="#2E2E2E",
    border_focus="#525252",
    grid_line="#222222",
    text_primary="#EDEDED",
    text_secondary="#A3A3A3",
    text_muted="#8C8C8C",
    accent_blue="#3B82F6",
    accent_orange="#E39A5B",
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
    drop_overlay_bg="rgba(18, 18, 18, 215)",
    badge_over=("#2E1C1C", "#FCA5A5", "#4E2A2A"),
    badge_optimal=("#1A291F", "#86EFAC", "#274230"),
    badge_low=("#1C232E", "#93C5FD", "#283647"),
    badge_exceeded=("#33181E", "#FDA4AF", "#57242E"),
    badge_std_encode=("#241E2E", "#D8B4FE", "#3D314E"),
    badge_low_kf_encode=("#2E1C2B", "#F0ABFC", "#4E2A48"),
    badge_pass=("#1F1F1F", "#8E8E8E", "#353535"),
    badge_moved=("#123824", "#4ADE80", "#228B4E"),
    badge_pending=("#362409", "#FBBF24", "#B45309"),
    badge_exception=("#1E1B4B", "#A5B4FC", "#4338CA"),
    badge_encode_running=("#123829", "#6EE7B7", "#10B981"),
    row_running_bg="#123829",
    row_running_accent="#10B981",
    row_paused_bg="#3D2B0D",
    row_paused_accent="#F59E0B",
    fg_cell_muted="#4B4B4B",
    fg_ratio_over="#FCA5A5",
    fg_ratio_opt="#86EFAC",
    fg_ratio_low="#93C5FD",
    fg_kf_exceeded="#FDA4AF",
    fg_kf_opt="#86EFAC",
    fg_saved_pos="#4ADE80",
    fg_saved_neg="#FBBF24",
    fg_maxrate_warn="#FB923C",
    progress_track_bg="#262626",
    progress_fill="#2563EB",
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
    bg_selected="#D4E6FF",
    input_selection_bg="#BFDBFE",
    border_subtle="#E2E8F0",
    border_focus="#94A3B8",
    grid_line="#F1F3F5",
    text_primary="#0F172A",
    text_secondary="#475569",
    text_muted="#5B6B80",
    accent_blue="#2563EB",
    accent_orange="#EA580C",
    btn_bg="#F1F5F9",
    btn_border="#CBD5E1",
    btn_hover="#E2E8F0",
    card_highlight_bg="#DCFCE7",
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
    drop_overlay_bg="rgba(245, 246, 248, 225)",
    badge_over=("#FEF2F2", "#991B1B", "#FECACA"),
    badge_optimal=("#F0FDF4", "#166534", "#BBF7D0"),
    badge_low=("#EFF6FF", "#1E40AF", "#BFDBFE"),
    badge_exceeded=("#FFF1F2", "#9F1239", "#FECDD3"),
    badge_std_encode=("#FAF5FF", "#6B21A8", "#E9D5FF"),
    badge_low_kf_encode=("#FDF4FF", "#86198F", "#F5D0FE"),
    badge_pass=("#F8FAFC", "#64748B", "#CBD5E1"),
    badge_moved=("#DCFCE7", "#166534", "#22C55E"),
    badge_pending=("#FEF3C7", "#92400E", "#F59E0B"),
    badge_exception=("#E0E7FF", "#3730A3", "#818CF8"),
    badge_encode_running=("#D1FAE5", "#065F46", "#059669"),
    row_running_bg="#D1FAE5",
    row_running_accent="#059669",
    row_paused_bg="#FDE68A",
    row_paused_accent="#D97706",
    fg_cell_muted="#BCC7D4",
    fg_ratio_over="#DC2626",
    fg_ratio_opt="#16A34A",
    fg_ratio_low="#2563EB",
    fg_kf_exceeded="#E11D48",
    fg_kf_opt="#16A34A",
    fg_saved_pos="#15803D",
    fg_saved_neg="#D97706",
    fg_maxrate_warn="#C2410C",
    progress_track_bg="#E2E8F0",
    progress_fill="#1D4ED8",
)
