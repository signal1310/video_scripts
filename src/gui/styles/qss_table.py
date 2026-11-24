"""테이블 뷰, 빈 상태 오버레이 및 열 필터 팝오버(gui/table) 전용 QSS 규칙 생성 모듈"""

from src.gui.constants import FONT_TABLE_HEADER_PT
from src.gui.styles.palettes import ThemePalette


def build_table_qss(p: ThemePalette) -> str:
    """전달된 팔레트로 테이블 및 필터 팝오버 영역 QSS 문자열 반환"""
    return f"""
QLabel#EmptyStateTitle {{
    font-size: 12pt;
    font-weight: 700;
    color: {p.text_primary};
}}
QLabel#EmptyStateDesc {{
    font-size: 9.5pt;
    color: {p.text_secondary};
}}
QFrame#EmptyGuideCard {{
    background-color: {p.bg_elevated};
    border: 1px solid {p.border_subtle};
    border-radius: 10px;
}}
QLabel#EmptyStepBadge {{
    background-color: {p.subtitle_bg};
    color: {p.subtitle_text};
    border: 1px solid {p.subtitle_border};
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 8.5pt;
    font-weight: 700;
}}
QLabel#EmptyStepDesc {{
    font-size: 8.8pt;
    color: {p.text_secondary};
}}
QWidget#TableEmptyOverlay {{
    background-color: transparent;
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
    font-size: {FONT_TABLE_HEADER_PT}pt;
    padding: 8px 6px;
    border: none;
    border-bottom: 2px solid {p.header_border};
    border-right: 1px solid {p.grid_line};
}}
QHeaderView::section:hover {{
    background-color: {p.header_hover};
}}
QFrame#ColumnFilterPopover {{
    background-color: transparent;
    border: none;
}}
QFrame#ColumnFilterPopoverCard {{
    background-color: {p.bg_card};
    color: {p.text_primary};
    border: 1px solid {p.border_focus};
    border-radius: 10px;
}}
QLabel#PopoverTitle {{
    font-size: 10pt;
    font-weight: 700;
    color: {p.text_primary};
}}
QLabel#PopoverTypeBadge {{
    background-color: {p.subtitle_bg};
    color: {p.subtitle_text};
    border: 1px solid {p.subtitle_border};
    border-radius: 9px;
    padding: 1px 7px;
    font-size: 8pt;
    font-weight: 600;
}}
QPushButton#PopoverLinkButton {{
    background-color: transparent;
    color: {p.accent_blue};
    border: none;
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 8.5pt;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton#PopoverLinkButton:hover {{
    background-color: {p.bg_hover};
}}
QPushButton#PopoverLinkButton:disabled {{
    color: {p.text_muted};
    background-color: transparent;
}}
QListWidget#ColumnFilterValueList {{
    background-color: {p.bg_input};
    alternate-background-color: {p.bg_table_alt};
    color: {p.text_primary};
    selection-background-color: {p.bg_selected};
    selection-color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    border-radius: 6px;
    padding: 3px;
    outline: none;
}}
QListWidget#ColumnFilterValueList::item {{
    color: {p.text_primary};
    padding: 4px 6px;
    border-radius: 4px;
}}
QListWidget#ColumnFilterValueList::item:hover {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
}}
QListWidget#ColumnFilterValueList::item:selected,
QListWidget#ColumnFilterValueList::item:selected:active,
QListWidget#ColumnFilterValueList::item:selected:!active {{
    background-color: {p.bg_selected};
    color: {p.text_primary};
}}
"""
