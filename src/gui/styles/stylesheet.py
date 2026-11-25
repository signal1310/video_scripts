"""활성 테마 팔레트 기반 PyQt6 전체 QSS 스타일시트 조립 및 공통 규칙 모듈"""

from PyQt6.QtWidgets import QWidget

from src.gui.constants import FONT_STACK_UI
from src.gui.styles.icons import CHECK_QSS_STROKE_WIDTH, resolve_qss_icon_path
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.qss_dialogs import build_dialogs_qss
from src.gui.styles.qss_preview import build_preview_qss
from src.gui.styles.qss_table import build_table_qss
from src.gui.styles.qss_widgets import build_widgets_qss


def set_state_property(widget: QWidget, name: str, value: bool) -> None:
    """QSS 속성 선택자가 상태 변경을 반영하도록 불리언 동적 속성을 설정하고 스타일을 다시 적용"""
    widget.setProperty(name, "true" if value else "false")
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


def set_state_value(widget: QWidget, name: str, value: str) -> None:
    """상태 이름을 담는 동적 속성을 설정하고, 값이 그대로여도 QSS 선택자를 다시 적용"""
    widget.setProperty(name, value)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


def _build_common_qss(p: ThemePalette) -> str:
    """위젯 유형 공통 규칙과 둘 이상 영역이 공유하는 QSS 문자열 반환"""
    up_arrow = resolve_qss_icon_path("chevron-up", p)
    down_arrow = resolve_qss_icon_path("chevron-down", p)
    check_icon = resolve_qss_icon_path(
        "check", p, color=p.text_on_accent, stroke_width=CHECK_QSS_STROKE_WIDTH
    )
    up_arrow_rule = f'image: url("{up_arrow}");' if up_arrow else ""
    down_arrow_rule = f'image: url("{down_arrow}");' if down_arrow else ""
    check_icon_rule = f'image: url("{check_icon}");' if check_icon else ""
    return f"""
QMainWindow, QDialog {{
    background-color: {p.bg_base};
    color: {p.text_primary};
    font-family: {FONT_STACK_UI};
}}
QWidget {{
    color: {p.text_primary};
}}
QLabel#CardTitle, QLabel#SectionLabel {{
    font-size: 9pt;
    font-weight: 600;
    color: {p.text_secondary};
}}
QLabel#CardSubtext {{
    font-size: 8.5pt;
    color: {p.text_muted};
}}
QLabel#FooterSummaryText {{
    font-size: 9.5pt;
    font-weight: 400;
    color: {p.text_secondary};
}}
QLabel#StatusText {{
    font-size: 8.5pt;
    color: {p.text_secondary};
}}
QLabel#GroupHeaderTitle {{
    font-size: 10pt;
    font-weight: 700;
    color: {p.text_primary};
}}
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
    background-color: {p.bg_input};
    color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 22px;
    selection-background-color: {p.input_selection_bg};
    selection-color: {p.text_primary};
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
    {up_arrow_rule}
    width: 10px;
    height: 10px;
}}
QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {{
    {down_arrow_rule}
    width: 10px;
    height: 10px;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    {down_arrow_rule}
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
QCheckBox::indicator:hover {{
    border-color: {p.accent_blue};
}}
QCheckBox::indicator:checked {{
    background-color: {p.accent_blue};
    border: 1px solid {p.accent_blue};
    {check_icon_rule}
}}
QCheckBox::indicator:disabled {{
    background-color: {p.bg_base};
    border-color: {p.border_subtle};
}}
QCheckBox::indicator:checked:disabled {{
    background-color: {p.border_focus};
    border: 1px solid {p.border_focus};
    {check_icon_rule}
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
    background-color: {p.action_primary_bg};
    color: {p.text_on_accent};
    border: 1px solid {p.action_primary_border};
}}
QPushButton#PrimaryButton:hover {{
    background-color: {p.action_primary_hover};
}}
QPushButton#PrimaryButton:disabled {{
    background-color: {p.bg_elevated};
    color: {p.text_muted};
    border-color: {p.border_subtle};
}}
QPushButton#IconActionButton, QPushButton#TopAreaCollapseButton, QPushButton#TopAreaExpandButton {{
    padding: 0px;
    min-height: 22px;
}}
QPushButton#IconActionButton:checked {{
    background-color: {p.input_selection_bg};
    border-color: {p.accent_blue};
}}
QFrame#AnalyzeModePopupCard, QFrame#TopAreaPopupCard, QFrame#PreviewSettingsPopupCard {{
    background-color: {p.bg_card};
    border: 1px solid {p.border_subtle};
    border-radius: 6px;
}}
/* 진행 막대의 트랙과 채움은 FillProgressBar가 팔레트 토큰으로 직접 그림 */
QProgressBar {{
    font-weight: 600;
    font-size: 8.5pt;
    min-height: 18px;
    max-height: 18px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {p.scrollbar_handle};
    min-height: 30px;
    min-width: 30px;
    margin: 2px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background: {p.scrollbar_hover};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
    height: 0px;
    width: 0px;
}}
QAbstractScrollArea::corner, QTableView QTableCornerButton::section {{
    background: transparent;
    border: none;
}}
QMenuBar {{
    background-color: {p.bg_base};
    color: {p.text_primary};
    border-bottom: 1px solid {p.border_subtle};
    padding: 2px 6px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 4px 10px;
    border-radius: 4px;
}}
QMenuBar::item:selected, QMenuBar::item:pressed {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
}}
QMenu {{
    background-color: {p.bg_card};
    color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::icon {{
    left: 8px;
}}
QMenu::item {{
    padding: 6px 20px 6px 10px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {p.bg_selected};
    color: {p.text_primary};
}}
QMenu::item:disabled {{
    color: {p.text_muted};
    background-color: transparent;
}}
QMenu::item:checked {{
    color: {p.accent_blue};
    font-weight: 600;
}}
QMenu::separator {{
    height: 1px;
    background-color: {p.border_subtle};
    margin: 4px 8px;
}}
QScrollArea, QScrollArea > QWidget > QWidget {{
    background-color: transparent;
    border: none;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {p.border_subtle};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {p.action_primary_bg};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal:disabled {{
    background: {p.text_muted};
}}
QSlider::handle:horizontal {{
    background: {p.action_primary_bg};
    border: 2px solid {p.bg_card};
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{
    background: {p.action_primary_hover};
}}
QSlider::handle:horizontal:disabled {{
    background: {p.text_muted};
}}
QToolTip {{
    background-color: {p.bg_card};
    color: {p.text_primary};
    border: 1px solid {p.border_focus};
    border-radius: 4px;
    padding: 6px;
}}
"""


def build_stylesheet(p: ThemePalette) -> str:
    """전달된 ThemePalette의 색상 변수를 바탕으로 전체 QSS 스타일시트를 조립"""
    return "".join((
        _build_common_qss(p),
        build_widgets_qss(p),
        build_table_qss(p),
        build_preview_qss(p),
        build_dialogs_qss(p),
    ))
