import os

from src.gui.constants import FONT_STACK_MONO, FONT_STACK_UI
from src.gui.styles.icons import ASSETS_DIR
from src.gui.styles.palettes import ThemePalette


def build_stylesheet(p: ThemePalette) -> str:
    """
    - 전달된 ThemePalette의 색상 변수를 바탕으로 QSS(Qt Style Sheet)를 동적 생성.
    """
    up_arrow = os.path.join(ASSETS_DIR, f"chevron-up-{p.name}.svg").replace("\\", "/")
    down_arrow = os.path.join(ASSETS_DIR, f"chevron-down-{p.name}.svg").replace("\\", "/")
    return f"""
QMainWindow, QDialog {{
    background-color: {p.bg_base};
    color: {p.text_primary};
    font-family: {FONT_STACK_UI};
}}
QWidget {{
    color: {p.text_primary};
}}
QFrame#HeaderBar, QFrame#FilterBar, QFrame#FooterBar, QFrame#BentoCard {{
    background-color: {p.bg_card};
    border: 1px solid {p.border_subtle};
    border-radius: 10px;
}}
QFrame#BentoCardHighlight {{
    background-color: {p.card_highlight_bg};
    border: 1px solid {p.card_highlight_border};
    border-radius: 10px;
}}
QLabel#AppTitle {{
    font-size: 13pt;
    font-weight: 700;
    color: {p.text_primary};
}}
QLabel#EmptyStateTitle {{
    font-size: 12pt;
    font-weight: 700;
    color: {p.text_primary};
}}
QLabel#EmptyStateDesc {{
    font-size: 9.5pt;
    color: {p.text_secondary};
}}
QWidget#TableEmptyOverlay {{
    background-color: transparent;
}}
QLabel#CardTitle, QLabel#SectionLabel {{
    font-size: 9pt;
    font-weight: 600;
    color: {p.text_secondary};
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
QLabel#CardSubtext {{
    font-size: 8.5pt;
    color: {p.text_muted};
}}
QLabel#StatusText {{
    font-size: 9pt;
    color: {p.text_secondary};
}}
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
    background-color: {p.bg_input};
    color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 22px;
    selection-background-color: {p.bg_selected};
}}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{
    border: 1px solid {p.border_focus};
}}
QLineEdit[invalid="true"] {{
    border: 1px solid {p.action_danger_border};
}}
QDoubleSpinBox::up-button, QSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 18px;
    border-left: 1px solid {p.border_subtle};
    border-bottom: 1px solid {p.border_subtle};
    border-top-right-radius: 5px;
    background-color: transparent;
}}
QDoubleSpinBox::down-button, QSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 18px;
    border-left: 1px solid {p.border_subtle};
    border-bottom-right-radius: 5px;
    background-color: transparent;
}}
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover,
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background-color: {p.bg_hover};
}}
QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {{
    image: url({up_arrow});
    width: 10px;
    height: 10px;
}}
QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {{
    image: url({down_arrow});
    width: 10px;
    height: 10px;
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
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    image: url({down_arrow});
    width: 10px;
    height: 10px;
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {p.bg_card};
    color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    selection-background-color: {p.bg_selected};
    selection-color: {p.text_primary};
    outline: 0;
    padding: 4px;
}}
QCheckBox {{
    spacing: 6px;
    color: {p.text_primary};
    font-weight: 500;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {p.border_focus};
    background-color: {p.bg_input};
}}
QCheckBox::indicator:checked {{
    background-color: {p.accent_blue};
    border: 1px solid {p.accent_blue};
}}
QPushButton {{
    background-color: {p.btn_bg};
    color: {p.text_primary};
    border: 1px solid {p.btn_border};
    border-radius: 7px;
    padding: 6px 14px;
    font-weight: 600;
    min-height: 22px;
}}
QPushButton:hover {{
    background-color: {p.btn_hover};
}}
QPushButton:pressed {{
    background-color: {p.bg_selected};
}}
QPushButton:disabled {{
    background-color: {p.bg_base};
    color: {p.text_muted};
    border-color: {p.border_subtle};
}}
QPushButton#PrimaryButton, QPushButton#SplitMainButton, QPushButton#SplitArrowButton {{
    background-color: {p.action_primary_bg};
    color: {p.text_on_accent};
    border: 1px solid {p.action_primary_border};
}}
QPushButton#PrimaryButton:hover, QPushButton#SplitMainButton:hover, QPushButton#SplitArrowButton:hover {{
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
    border-left: 1px solid rgba(255, 255, 255, 0.25);
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
QPushButton#IconActionButton, QPushButton#SettingsButton {{
    padding: 0px;
    min-height: 22px;
}}
QPushButton#PrimaryButton:disabled, QPushButton#SuccessButton:disabled {{
    background-color: {p.bg_elevated};
    color: {p.text_muted};
    border-color: {p.border_subtle};
}}
QPushButton#SuccessButton {{
    background-color: {p.action_success_bg};
    color: {p.text_on_accent};
    border: 1px solid {p.action_success_border};
}}
QPushButton#SuccessButton:hover {{
    background-color: {p.action_success_hover};
}}
QPushButton#DangerButton {{
    background-color: {p.action_danger_bg};
    color: {p.text_on_accent};
    border: 1px solid {p.action_danger_border};
}}
QPushButton#DangerButton:hover {{
    background-color: {p.action_danger_hover};
}}
QTableView {{
    background-color: {p.bg_card};
    alternate-background-color: {p.bg_table_alt};
    color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    border-radius: 10px;
    gridline-color: {p.grid_line};
    selection-background-color: {p.bg_selected};
    selection-color: {p.text_primary};
    outline: 0;
}}
QTableView::item {{
    padding: 4px 8px;
    border: none;
}}
QTableView::item:hover {{
    background-color: {p.bg_hover};
}}
QTableView::item:selected {{
    background-color: {p.bg_selected};
    color: {p.text_primary};
}}
QHeaderView {{
    background-color: transparent;
}}
QHeaderView::section {{
    background-color: {p.header_bg};
    color: {p.header_text};
    font-weight: 700;
    font-size: 9pt;
    padding: 8px 6px;
    border: none;
    border-bottom: 2px solid {p.header_border};
    border-right: 1px solid {p.grid_line};
}}
QHeaderView::section:hover {{
    background-color: {p.header_hover};
}}
QProgressBar {{
    background-color: {p.bg_input};
    border: 1px solid {p.border_subtle};
    border-radius: 6px;
    text-align: center;
    color: {p.text_primary};
    font-weight: 600;
    font-size: 8.5pt;
    min-height: 18px;
    max-height: 18px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0, stop: 0 {p.action_primary_bg}, stop: 1 {p.action_success_border});
    border-radius: 5px;
}}
QScrollBar:vertical {{
    background: {p.bg_base};
    width: 10px;
    margin: 2px;
    border-radius: 5px;
}}
QScrollBar:horizontal {{
    background: {p.bg_base};
    height: 10px;
    margin: 2px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {p.scrollbar_handle};
    min-height: 30px;
    min-width: 30px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background: {p.scrollbar_hover};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    height: 0px;
    width: 0px;
}}
QMenu {{
    background-color: {p.bg_card};
    color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 20px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {p.bg_selected};
    color: {p.text_primary};
}}
QPlainTextEdit#LogViewer {{
    background-color: {p.bg_input};
    color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    border-radius: 8px;
    font-family: {FONT_STACK_MONO};
    font-size: 9pt;
    padding: 8px;
}}
QScrollArea, QScrollArea > QWidget > QWidget {{
    background-color: transparent;
    border: none;
}}
QDialog#SettingsDialog {{
    background-color: {p.bg_card};
}}
QLabel#GroupHeaderTitle {{
    font-size: 10pt;
    font-weight: 700;
    color: {p.text_primary};
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
    padding: 2px 4px;
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
QPushButton#ResetButton {{
    background-color: transparent;
    border: none;
    border-radius: 6px;
    padding: 0px;
}}
QPushButton#ResetButton:hover {{
    background-color: {p.bg_hover};
}}
QToolTip {{
    background-color: {p.bg_card};
    color: {p.text_primary};
    border: 1px solid {p.border_focus};
    border-radius: 4px;
    padding: 6px;
}}
"""
