import os
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
    font-family: "Pretendard", "Malgun Gothic", "Segoe UI", sans-serif;
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
QLabel#AppSubtitle {{
    font-size: 8.5pt;
    font-weight: 600;
    color: {p.subtitle_text};
    background-color: {p.subtitle_bg};
    border: 1px solid {p.subtitle_border};
    border-radius: 5px;
    padding: 2px 8px;
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
QPushButton#PrimaryButton {{
    background-color: #2563EB;
    color: #FFFFFF;
    border: 1px solid #3B82F6;
}}
QPushButton#PrimaryButton:hover {{
    background-color: #1D4ED8;
}}
QPushButton#SplitMainButton {{
    background-color: #2563EB;
    color: #FFFFFF;
    border: 1px solid #3B82F6;
    border-right: none;
    border-top-left-radius: 7px;
    border-bottom-left-radius: 7px;
    border-top-right-radius: 0px;
    border-bottom-right-radius: 0px;
    padding: 6px 14px;
    font-weight: 600;
    min-height: 22px;
}}
QPushButton#SplitMainButton:hover {{
    background-color: #1D4ED8;
}}
QPushButton#SplitMainButton:disabled {{
    background-color: {p.bg_elevated};
    color: {p.text_muted};
    border-color: {p.border_subtle};
}}
QPushButton#SplitArrowButton {{
    background-color: #2563EB;
    color: #FFFFFF;
    border: 1px solid #3B82F6;
    border-left: 1px solid rgba(255, 255, 255, 0.25);
    border-top-left-radius: 0px;
    border-bottom-left-radius: 0px;
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
    padding: 6px 0px;
    min-height: 22px;
}}
QPushButton#SplitArrowButton:hover {{
    background-color: #1D4ED8;
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
    background-color: #059669;
    color: #FFFFFF;
    border: 1px solid #10B981;
}}
QPushButton#SuccessButton:hover {{
    background-color: #047857;
}}
QPushButton#WarningButton {{
    background-color: #D97706;
    color: #FFFFFF;
    border: 1px solid #F59E0B;
}}
QPushButton#WarningButton:hover {{
    background-color: #B45309;
}}
QPushButton#DangerButton {{
    background-color: #DC2626;
    color: #FFFFFF;
    border: 1px solid #EF4444;
}}
QPushButton#DangerButton:hover {{
    background-color: #B91C1C;
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
    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0, stop: 0 #2563EB, stop: 1 #10B981);
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
QTabWidget::pane {{
    border: 1px solid {p.border_subtle};
    border-radius: 8px;
    background-color: {p.bg_card};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {p.bg_elevated};
    color: {p.text_secondary};
    padding: 8px 16px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    border: 1px solid {p.border_subtle};
    border-bottom: none;
    font-weight: 600;
    font-size: 9pt;
}}
QTabBar::tab:selected {{
    background-color: {p.bg_card};
    color: {p.text_primary};
    border-color: {p.border_focus};
    border-bottom: 2px solid {p.accent_blue};
}}
QTabBar::tab:hover:!selected {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
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
QLabel#SettingSectionHeader {{
    font-size: 9.5pt;
    font-weight: 700;
    color: {p.text_primary};
    padding-top: 6px;
    padding-bottom: 2px;
}}
QLabel#SettingSubSectionHeader {{
    font-size: 8.5pt;
    font-weight: 600;
    color: {p.accent_blue};
    padding-top: 6px;
    padding-bottom: 2px;
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
QLabel#SettingKeyBadge {{
    font-size: 8pt;
    font-family: "Consolas", monospace;
    font-weight: 600;
    color: {p.text_muted};
    background-color: {p.bg_input};
    border: 1px solid {p.border_subtle};
    border-radius: 4px;
    padding: 1px 6px;
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
