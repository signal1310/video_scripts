"""설정(gui/settings), 도움말(gui/help), 공용 대화상자(gui/dialogs) 전용 QSS 규칙 생성 모듈"""

from src.gui.constants import FONT_STACK_MONO, FONT_STACK_UI
from src.gui.styles.palettes import ThemePalette


def build_dialogs_qss(p: ThemePalette) -> str:
    """전달된 팔레트로 설정, 도움말, 작업 기록, 예외 규칙 대화상자 QSS 문자열 반환"""
    return f"""
QLabel#ErrorHintLabel {{
    font-size: 8.5pt;
    font-weight: 600;
    color: {p.fg_ratio_over};
    padding-left: 2px;
}}
QLabel#WarningHintLabel {{
    font-size: 8.5pt;
    font-weight: 600;
    color: {p.fg_maxrate_warn};
    padding-left: 2px;
}}
QFrame#EncodeWarningBox {{
    background-color: {p.bg_card};
    border: 1px solid {p.fg_maxrate_warn};
    border-radius: 6px;
}}
QFrame#EncodeBriefCard {{
    background-color: {p.bg_card};
    border: 1px solid {p.border_subtle};
    border-radius: 8px;
}}
QLabel#EncodeBriefCaption {{
    color: {p.text_muted};
}}
QLabel#EncodeBriefValue {{
    color: {p.text_primary};
    font-weight: 600;
}}
QLabel#EncodeSummarySubtitle {{
    color: {p.text_muted};
}}
QLabel#EncodeSummaryBadge {{
    background-color: {p.bg_selected};
    color: {p.accent_blue};
    border: 1px solid {p.action_primary_border};
    border-radius: 9px;
    padding: 1px 8px;
    font-weight: 600;
}}
QLabel#EncodeSummaryLive {{
    color: {p.text_secondary};
}}
QFrame#EncodeAttentionBanner {{
    background-color: {p.bg_card};
    border: 1px solid {p.fg_maxrate_warn};
    border-radius: 6px;
}}
QTreeWidget#EncodeImpactTree::item:selected,
QTreeWidget#EncodeImpactTree::item:selected:!active {{
    background-color: {p.bg_selected};
    color: {p.text_primary};
}}
QListWidget#ExceptionRuleList {{
    background-color: {p.bg_input};
    alternate-background-color: {p.bg_table_alt};
    color: {p.text_primary};
    selection-background-color: {p.bg_selected};
    selection-color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    border-radius: 6px;
    padding: 4px;
    outline: none;
}}
QListWidget#ExceptionRuleList:focus {{
    border: 1px solid {p.border_focus};
}}
QListWidget#ExceptionRuleList::item {{
    color: {p.text_primary};
    padding: 6px 8px;
    border-radius: 4px;
}}
QListWidget#ExceptionRuleList::item:hover {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
}}
QListWidget#ExceptionRuleList::item:selected,
QListWidget#ExceptionRuleList::item:selected:active,
QListWidget#ExceptionRuleList::item:selected:!active {{
    background-color: {p.bg_selected};
    color: {p.text_primary};
}}
QPlainTextEdit#LogViewer, QTextBrowser#HelpViewer {{
    background-color: {p.bg_input};
    color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    border-radius: 8px;
    padding: 8px;
}}
QPlainTextEdit#LogViewer {{
    font-family: {FONT_STACK_MONO};
    font-size: 9pt;
}}
QTextBrowser#HelpViewer {{
    font-family: {FONT_STACK_UI};
    font-size: 11pt;
    padding: 14px 16px;
}}
QDialog#SettingsDialog {{
    background-color: {p.bg_card};
}}
QTabWidget#SettingsTabs {{
    background-color: {p.bg_card};
}}
QTabWidget#SettingsTabs::pane {{
    border: none;
    background-color: {p.bg_card};
}}
QTabWidget#SettingsTabs::tab-bar {{
    left: 0px;
    background-color: {p.bg_card};
}}
QTabWidget#SettingsTabs QTabBar {{
    background-color: {p.bg_card};
    qproperty-drawBase: 0;
}}
QTabWidget#SettingsTabs QTabBar::tab {{
    background-color: {p.bg_card};
    color: {p.text_secondary};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 6px 12px;
    font-size: 9.5pt;
    font-weight: 600;
}}
QTabWidget#SettingsTabs QTabBar::tab:hover {{
    color: {p.text_primary};
}}
QTabWidget#SettingsTabs QTabBar::tab:selected {{
    color: {p.accent_blue};
    border-bottom: 2px solid {p.accent_blue};
}}
QPushButton#InlineResetButton {{
    background-color: {p.bg_elevated};
    color: {p.text_secondary};
    border: 1px solid {p.border_subtle};
    border-radius: 5px;
    padding: 2px 8px;
    font-size: 8.5pt;
    font-weight: 500;
    min-height: 22px;
}}
QPushButton#InlineResetButton:hover {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
    border-color: {p.border_focus};
}}
QPushButton#InlineResetButton:disabled {{
    background-color: transparent;
    color: {p.text_muted};
    border-color: {p.border_subtle};
}}
QPushButton#ShortcutCaptureButton {{
    background-color: transparent;
    color: {p.accent_blue};
    border: 1px solid {p.border_subtle};
    border-radius: 5px;
    padding: 2px 10px;
    font-size: 9pt;
    font-weight: 500;
    min-width: 112px;
    min-height: 22px;
}}
QPushButton#ShortcutCaptureButton:hover {{
    background-color: {p.bg_hover};
    border-color: {p.border_focus};
}}
QPushButton#ShortcutCaptureButton:focus, QPushButton#ShortcutCaptureButton[capturing="true"] {{
    border-color: {p.accent_blue};
}}
QLabel#GroupHeaderSubtitle {{
    font-size: 8.5pt;
    color: {p.text_secondary};
}}
QFrame#SettingsCardBox {{
    background-color: {p.bg_card};
    border: 1px solid {p.border_subtle};
    border-radius: 10px;
}}
QFrame#SettingsCard {{
    background-color: transparent;
    border: none;
    border-bottom: 1px solid {p.border_subtle};
    padding: 1px 2px;
}}
QFrame#SettingsCard:hover {{
    background-color: {p.bg_hover};
}}
QFrame#SettingsCard[is_first_row="true"] {{
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
}}
QFrame#SettingsCard[is_last_row="true"] {{
    border-bottom: none;
    border-bottom-left-radius: 9px;
    border-bottom-right-radius: 9px;
}}
QFrame#SettingsCard[is_first_row="true"][is_last_row="true"] {{
    border-radius: 9px;
}}
QFrame#TwistieHeader {{
    background-color: transparent;
    border: none;
    border-radius: 7px;
    padding: 3px 6px;
}}
QFrame#TwistieHeader:hover {{
    background-color: {p.bg_hover};
}}
QFrame#TwistieHeader:focus {{
    border: 1px solid {p.accent_blue};
}}
QLabel#TwistieTitle {{
    font-size: 10pt;
    font-weight: 700;
    color: {p.text_primary};
}}
QLabel#TwistieSubtitle {{
    font-size: 8.5pt;
    color: {p.text_secondary};
}}
QLabel#SettingTitle {{
    font-size: 9.5pt;
    font-weight: 500;
    color: {p.text_primary};
}}
QLabel#SettingDesc {{
    font-size: 8.5pt;
    color: {p.text_secondary};
}}
QLabel#TunerHintLabel {{
    font-size: 8.5pt;
    color: {p.text_secondary};
    padding-left: 2px;
}}
QLabel#HintExampleLabel {{
    font-size: 8.5pt;
    font-weight: 600;
    color: {p.accent_blue};
    padding-left: 2px;
}}
QFrame#SettingsHintBar {{
    background-color: transparent;
    border: none;
}}
QPushButton#ResetButton {{
    background-color: transparent;
    border: none;
    border-radius: 6px;
    padding: 0px;
}}
QPushButton#ResetButton:hover {{
    background-color: {p.bg_hover};
}}
QWidget#SettingsSidebar {{
    background-color: {p.bg_card};
    border: none;
    border-right: 1px solid {p.border_subtle};
    border-radius: 0px;
}}
QFrame#SettingsHeaderDivider {{
    background-color: {p.border_subtle};
    border: none;
}}
QToolButton#SettingsNavButton, QToolButton#SettingsNavResults {{
    background-color: transparent;
    color: {p.text_secondary};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 6px 8px;
    font-size: 9.5pt;
    font-weight: 600;
    text-align: left;
}}
QToolButton#SettingsNavButton:hover, QToolButton#SettingsNavResults:hover {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
}}
QToolButton#SettingsNavButton:checked, QToolButton#SettingsNavResults:checked {{
    background-color: {p.bg_selected};
    color: {p.accent_blue};
    border: 1px solid {p.action_primary_border};
}}
QToolButton#SettingsNavToggle {{
    background-color: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px;
}}
QToolButton#SettingsNavToggle:hover {{
    background-color: {p.bg_hover};
}}
QLabel#SettingsNavBadge {{
    background-color: {p.bg_elevated};
    color: {p.text_secondary};
    border-radius: 9px;
    font-size: 8pt;
    font-weight: 700;
    padding: 0px 6px;
}}
QPushButton#DataCleanupPresetPill {{
    background-color: transparent;
    color: {p.text_secondary};
    border: 1px solid {p.btn_border};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 9pt;
    font-weight: 600;
}}
QPushButton#DataCleanupPresetPill:hover {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
}}
QPushButton#DataCleanupPresetPill[presetWarning="true"] {{
    color: {p.badge_pending[1]};
    border: 1px solid {p.badge_pending[2]};
}}
QPushButton#DataCleanupPresetPill[presetWarning="true"]:hover {{
    background-color: {p.bg_hover};
    color: {p.badge_pending[1]};
}}
QPushButton#DataCleanupPresetPill[presetActive="true"] {{
    background-color: {p.bg_selected};
    color: {p.text_primary};
    border: 1px solid {p.accent_blue};
}}
QPushButton#DataCleanupPresetPill[presetWarning="true"][presetActive="true"] {{
    background-color: {p.badge_pending[0]};
    color: {p.badge_pending[1]};
    border: 1px solid {p.badge_pending[2]};
}}
QLineEdit#SettingsSearchEdit {{
    background-color: {p.bg_input};
    color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    border-radius: 6px;
    padding: 3px 8px;
    min-height: 20px;
}}
QLineEdit#SettingsSearchEdit:focus {{
    border-color: {p.border_focus};
}}
QPushButton#IconToggleButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 0px;
}}
QPushButton#IconToggleButton:hover {{
    background-color: {p.bg_hover};
    border-color: {p.border_subtle};
}}
QPushButton#IconToggleButton:checked {{
    background-color: {p.bg_selected};
    border-color: {p.action_primary_border};
}}
QPushButton#HelpIconButton {{
    background-color: transparent;
    border: none;
    border-radius: 10px;
    padding: 0px;
}}
QPushButton#HelpIconButton:hover {{
    background-color: {p.bg_hover};
}}
QPushButton#HelpLinkButton {{
    background-color: transparent;
    color: {p.accent_blue};
    border: none;
    padding: 2px 4px;
    font-size: 9pt;
    font-weight: 600;
}}
QPushButton#HelpLinkButton:hover {{
    color: {p.text_primary};
}}
QPushButton#FfmpegStatusBadge {{
    background-color: {p.bg_elevated};
    color: {p.text_secondary};
    border: 1px solid {p.border_subtle};
    border-radius: 11px;
    padding: 2px 10px;
    font-size: 9pt;
    font-weight: 600;
}}
QPushButton#FfmpegStatusBadge:hover {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
}}
QPushButton#FfmpegStatusBadge[statusKind="ok"] {{
    color: {p.fg_saved_pos};
    border-color: {p.fg_saved_pos};
}}
QPushButton#FfmpegStatusBadge[statusKind="error"] {{
    color: {p.action_danger_border};
    border-color: {p.action_danger_border};
}}
QPushButton#SettingsResultButton {{
    background-color: {p.bg_card};
    color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    border-radius: 6px;
    padding: 6px 10px;
    text-align: left;
    font-size: 9.5pt;
}}
QPushButton#SettingsResultButton:hover {{
    background-color: {p.bg_hover};
    border-color: {p.border_focus};
}}
"""
