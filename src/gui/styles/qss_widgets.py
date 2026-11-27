"""GUI 범용 위젯(gui/widgets)과 메인 창(gui/) 직속 위젯 전용 QSS 규칙 생성 모듈"""

from src.gui.constants import FOLDER_NAME_FONT_PT, FOLDER_NAME_PAD_X_PX
from src.gui.styles.palettes import ThemePalette


def build_widgets_qss(p: ThemePalette) -> str:
    """전달된 팔레트로 범용 위젯 및 메인 창 직속 위젯 QSS 문자열 반환"""
    return f"""
QFrame#HeaderBar, QFrame#TopAreaTabStrip, QFrame#FilterBar, QFrame#FooterBar, QFrame#BentoCard {{
    background-color: {p.bg_card};
    border: 1px solid {p.border_subtle};
    border-radius: 10px;
}}
QFrame#BentoCardHighlight {{
    background-color: {p.card_highlight_bg};
    border: 1px solid {p.card_highlight_border};
    border-radius: 10px;
}}
QFrame#DropOverlay {{
    background-color: {p.drop_overlay_bg};
    border: 2px dashed {p.accent_blue};
    border-radius: 12px;
}}
QFrame#CrashNoticeBar {{
    background-color: {p.bg_elevated};
    border: 1px solid {p.accent_orange};
    border-radius: 8px;
}}
QLabel#CrashNoticeText {{
    color: {p.text_primary};
    font-size: 9pt;
}}
QLabel#DropOverlayTitle {{
    font-size: 13pt;
    font-weight: 700;
    color: {p.text_primary};
    background-color: transparent;
}}
QLabel#DropOverlayDesc {{
    font-size: 9.5pt;
    color: {p.text_secondary};
    background-color: transparent;
}}
QLabel#CardValue {{
    font-size: 15pt;
    font-weight: 700;
    color: {p.text_primary};
}}
QLabel#CardValueHighlight {{
    font-size: 15pt;
    font-weight: 700;
    color: {p.card_highlight_val};
}}
QLineEdit[invalid="true"] {{
    border: 1px solid {p.action_danger_border};
}}
QFrame#CustomSpinBox {{
    background-color: {p.bg_input};
    border: 1px solid {p.border_subtle};
    border-radius: 6px;
    padding: 1px 3px 1px 6px;
    min-height: 24px;
}}
QFrame#CustomSpinBox:focus-within {{
    border: 1px solid {p.border_focus};
}}
QLineEdit#CustomSpinBoxEdit {{
    background-color: transparent;
    color: {p.text_primary};
    border: none;
    padding: 0px;
    font-size: 9.5pt;
    font-weight: 500;
}}
QLabel#SpinSuffix, QLabel#SpinPrefix {{
    font-size: 8.5pt;
    color: {p.text_secondary};
    padding-left: 2px;
    padding-right: 2px;
}}
QPushButton#SpinStepUp, QPushButton#SpinStepDown {{
    background-color: transparent;
    border: none;
    border-radius: 3px;
    padding: 0px;
    margin: 0px;
    min-width: 18px;
    max-width: 18px;
    min-height: 11px;
    max-height: 13px;
}}
QPushButton#SpinStepUp:hover, QPushButton#SpinStepDown:hover {{
    background-color: {p.bg_hover};
}}
QPushButton#SpinStepUp:pressed, QPushButton#SpinStepDown:pressed {{
    background-color: {p.bg_selected};
}}
QPushButton#SplitMainButton, QPushButton#SplitArrowButton {{
    background-color: {p.action_primary_bg};
    color: {p.text_on_accent};
    border: 1px solid {p.action_primary_border};
}}
QPushButton#SplitMainButton:hover, QPushButton#SplitArrowButton:hover {{
    background-color: {p.action_primary_hover};
}}
QPushButton#SplitMainButton {{
    border-right: none;
    border-top-left-radius: 7px;
    border-bottom-left-radius: 7px;
    border-top-right-radius: 0px;
    border-bottom-right-radius: 0px;
    padding: 6px 14px;
    font-weight: 600;
    min-height: 22px;
}}
QPushButton#SplitMainButton:disabled {{
    background-color: {p.bg_elevated};
    color: {p.text_muted};
    border-color: {p.border_subtle};
}}
QPushButton#SplitArrowButton {{
    border-left: 1px solid {p.split_divider};
    border-top-left-radius: 0px;
    border-bottom-left-radius: 0px;
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
    padding: 6px 0px;
    min-height: 22px;
}}
QPushButton#SplitArrowButton:disabled {{
    background-color: {p.bg_elevated};
    color: {p.text_muted};
    border-color: {p.border_subtle};
}}
QFrame#ActionSegment {{
    background-color: {p.btn_bg};
    border: 1px solid {p.btn_border};
    border-radius: 9px;
}}
QPushButton#SegmentButton {{
    background-color: transparent;
    border: none;
    border-radius: 0px;
    padding: 0px;
    min-height: 0px;
}}
QPushButton#SegmentButton:hover {{
    background-color: {p.bg_hover};
}}
QPushButton#SegmentButton:pressed {{
    background-color: {p.bg_selected};
}}
QPushButton#SegmentButton:disabled {{
    background-color: transparent;
}}
QFrame#SegmentDivider {{
    background-color: {p.btn_border};
}}
QPushButton#DangerButton {{
    background-color: {p.action_danger_bg};
    color: {p.text_on_accent};
    border: 1px solid {p.action_danger_border};
}}
QPushButton#DangerButton:hover {{
    background-color: {p.action_danger_hover};
}}
QPushButton#AnalyzeModeItem {{
    background-color: transparent;
    color: {p.text_primary};
    border: none;
    border-radius: 4px;
    padding: 6px 14px 6px 10px;
    text-align: left;
}}
QPushButton#AnalyzeModeItem:hover {{
    background-color: transparent;
}}
QFrame#AnalyzeModeRow {{
    background-color: transparent;
    border: none;
    border-radius: 4px;
}}
QFrame#AnalyzeModeRow:hover {{
    background-color: {p.bg_selected};
}}
QFrame#AnalyzeFrameGroup {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}}
QFrame#AnalyzeFrameGroup[selected="true"] {{
    background-color: {p.bg_selected};
    border: 1px solid {p.action_primary_border};
}}
QFrame#AnalyzeFrameGroup[selected="true"] QFrame#AnalyzeModeRow:hover {{
    background-color: transparent;
}}
QPushButton#FolderNameButton {{
    background-color: transparent;
    color: {p.text_primary};
    border: none;
    border-radius: 6px;
    padding: 4px {FOLDER_NAME_PAD_X_PX}px;
    font-size: {FOLDER_NAME_FONT_PT}pt;
    font-weight: 700;
    text-align: left;
}}
QPushButton#FolderNameButton:hover {{
    background-color: {p.bg_hover};
}}
QPushButton#FolderNameButton:disabled {{
    color: {p.text_muted};
}}
QPushButton#TopAreaTab {{
    background-color: transparent;
    color: {p.text_secondary};
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
    padding: 0px 12px;
    font-weight: 600;
}}
QPushButton#TopAreaTab:hover {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
    border-bottom-color: {p.text_muted};
}}
QPushButton#TopAreaTab:checked, QPushButton#TopAreaTab:checked:hover {{
    color: {p.accent_blue};
    border-bottom-color: {p.accent_blue};
}}
QFrame#ToolbarDivider {{
    background-color: {p.border_subtle};
    border: none;
    max-width: 1px;
    min-width: 1px;
}}
QFrame#EncodeFooterChip {{
    background-color: {p.bg_elevated};
    border: 1px solid {p.border_subtle};
    border-radius: 11px;
}}
QFrame#EncodeFooterChip QProgressBar {{
    min-height: 6px;
    max-height: 6px;
}}
QFrame#EncodeFooterChip[chipState="running"] {{
    background-color: {p.bg_selected};
    border: 1px solid {p.action_primary_border};
}}
QFrame#EncodeFooterChip[chipState="paused"] {{
    background-color: {p.badge_pending[0]};
    border: 1px solid {p.badge_pending[2]};
}}
QFrame#EncodeFooterChip[chipState="finished"] {{
    background-color: {p.bg_elevated};
    border: 1px solid {p.btn_border};
}}
QLabel#EncodeFooterChipText {{
    background-color: transparent;
    color: {p.text_secondary};
    font-size: 8.5pt;
    font-weight: 600;
}}
QFrame#EncodeFooterChip[chipState="running"] QLabel#EncodeFooterChipText {{
    color: {p.text_primary};
}}
QFrame#EncodeFooterChip[chipState="paused"] QLabel#EncodeFooterChipText {{
    color: {p.badge_pending[1]};
}}
QPushButton#ClearColumnFiltersButton {{
    background-color: {p.subtitle_bg};
    color: {p.accent_blue};
    border: 1px solid {p.accent_blue};
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 8.5pt;
    font-weight: 600;
    min-height: 20px;
}}
QPushButton#ClearColumnFiltersButton:hover {{
    background-color: {p.bg_hover};
}}
QPushButton#ClearAllFiltersButton {{
    background-color: transparent;
    color: {p.text_secondary};
    border: 1px solid {p.border_subtle};
    border-radius: 11px;
    padding: 1px 9px;
    font-size: 8.5pt;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton#ClearAllFiltersButton:hover {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
}}
QPushButton#ReviewFilterChip {{
    background-color: {p.badge_pending[0]};
    color: {p.badge_pending[1]};
    border: 1px solid {p.badge_pending[1]};
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 8.5pt;
    font-weight: 600;
    min-height: 20px;
}}
QPushButton#ReviewFilterChip:hover {{
    background-color: {p.bg_hover};
}}
QPushButton#ReviewFilterChip:checked {{
    background-color: {p.badge_pending[0]};
    color: {p.badge_pending[1]};
    border: 2px solid {p.badge_pending[1]};
}}
QPushButton#CompactTableButton:checked, QPushButton#CompactTableButton:checked:hover {{
    background-color: {p.bg_selected};
    border-color: {p.action_primary_border};
}}
"""
