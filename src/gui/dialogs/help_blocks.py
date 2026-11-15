"""사용 가이드 HTML의 공통 스타일, 강조 상자, 카드, 수식 패널, 분수, 줄무늬 표를 생성하는 모듈

- Qt 리치 텍스트는 div의 padding, border, border-radius를 적용하지 않음
- 여백은 cellpadding, 배경은 bgcolor로 지정하고, 분수 막대는 얇은 높이 셀로 표현
"""

from __future__ import annotations

from collections.abc import Sequence

from src.gui.constants import FONT_MONO_FAMILY, FONT_UI_FAMILY
from src.gui.styles.palettes import ThemePalette

HELP_BODY_FONT_PT: int = 11
TABLE_CELL_PADDING_PX: int = 9
CALLOUT_PADDING_PX: int = 16
CARD_PADDING_PX: int = 16
CARD_ACCENT_WIDTH_PX: int = 4
FORMULA_CELL_PADDING_PX: int = 6
FORMULA_ROW_PADDING_PX: int = 6
FORMULA_FONT_FAMILY: str = "Cambria Math"
FORMULA_FONT_PT: int = 12
NOTE_FONT_PT: int = 9
FRACTION_BAR_HEIGHT_PX: int = 2
# 표는 외부 여백을 줄 수 없어 인접한 블록 사이에 빈 문단으로 간격을 줌
GAP_HTML: str = "<p style='margin: 0; font-size: 4pt;'>&nbsp;</p>"


def _formula_font_style(p: ThemePalette) -> str:
    family = f'"{FORMULA_FONT_FAMILY}", "{FONT_UI_FAMILY}", serif'
    return f"font-family: {family}; font-size: {FORMULA_FONT_PT}pt; color: {p.text_primary};"


def callout_html(p: ThemePalette, body: str) -> str:
    """강조 상자, 배경은 `card_highlight_bg` 토큰으로 본문과 구분"""
    return (
        "<table width='100%' cellspacing='0' cellpadding='0'>"
        f"<tr><td bgcolor='{p.card_highlight_bg}'>"
        f"<table width='100%' cellspacing='0' cellpadding='{CALLOUT_PADDING_PX}'>"
        f"<tr><td>{body}</td></tr></table>"
        "</td></tr></table>"
    )


def fraction_html(p: ThemePalette, numerator: str, denominator: str) -> str:
    """분수 셀 내용, 분자와 분모를 막대로 구분하는 세로 표"""
    style = f"padding: 0 {FORMULA_CELL_PADDING_PX}px; {_formula_font_style(p)}"
    bar = (
        f"<tr><td height='{FRACTION_BAR_HEIGHT_PX}' bgcolor='{p.text_secondary}' "
        "style='font-size: 1pt;'>&nbsp;</td></tr>"
    )
    return (
        "<table cellspacing='0' cellpadding='0'>"
        f"<tr><td align='center' style='{style}'>{numerator}</td></tr>"
        f"{bar}"
        f"<tr><td align='center' style='{style}'>{denominator}</td></tr>"
        "</table>"
    )


def formula_html(p: ThemePalette, rows: Sequence[Sequence[str]]) -> str:
    """수식 패널, 행마다 항목 셀을 나열하고 패널과 수식을 가운데 정렬"""
    cell_style = (
        f"padding: {FORMULA_ROW_PADDING_PX}px {FORMULA_CELL_PADDING_PX}px; "
        f"{_formula_font_style(p)}"
    )
    # 행마다 별도 표로 두어 셀 개수가 다른 행끼리 열 폭이 공유되지 않게 함
    lines = "".join(
        "<table align='center' cellspacing='0' cellpadding='0'><tr>"
        + "".join(
            f"<td align='center' valign='middle' style='{cell_style}'>{cell}</td>"
            for cell in row
        )
        + "</tr></table>"
        for row in rows
    )
    return f"<table width='100%' cellspacing='0' cellpadding='0'><tr><td align='center'>{lines}</td></tr></table>"


def note_html(p: ThemePalette, text: str) -> str:
    """수식 보조 설명, 본문보다 작고 흐린 글자"""
    ui = f'"{FONT_UI_FAMILY}", sans-serif'
    return (
        f"<span style='font-family: {ui}; font-size: {NOTE_FONT_PT}pt; "
        f"color: {p.text_muted};'>{text}</span>"
    )


def card_html(p: ThemePalette, body: str) -> str:
    """항목 카드, 왼쪽 강조선과 옅은 배경으로 구분하고 테두리는 두지 않음"""
    return (
        "<table width='100%' cellspacing='0' cellpadding='0'><tr>"
        f"<td width='{CARD_ACCENT_WIDTH_PX}' bgcolor='{p.accent_blue}'>&nbsp;</td>"
        f"<td bgcolor='{p.bg_table_alt}'>"
        f"<table width='100%' cellspacing='0' cellpadding='{CARD_PADDING_PX}'>"
        f"<tr><td>{body}</td></tr></table>"
        "</td></tr></table>"
    )


def pad_inline_code(html_text: str) -> str:
    """인라인 코드 배경의 좌우 여백을 위해 양끝에 줄바꿈되지 않는 공백 삽입

    - Qt 인라인 span은 padding을 지원하지 않아 공백으로 여백을 대신함
    """
    return html_text.replace("<code>", "<code>&nbsp;").replace("</code>", "&nbsp;</code>")


def render_styles(p: ThemePalette) -> str:
    """사용 가이드 공통 스타일 시트, 본문 줄 간격 1.5와 인라인 코드 칩 색상 포함"""
    ui_fonts = f'"{FONT_UI_FAMILY}", "Segoe UI", sans-serif'
    mono_fonts = f'"{FONT_MONO_FAMILY}", "{FONT_UI_FAMILY}", monospace'
    return (
        "<style>"
        f"body {{ font-family: {ui_fonts}; font-size: {HELP_BODY_FONT_PT}pt; "
        f"color: {p.text_primary}; margin: 0; }}"
        f"h2 {{ color: {p.accent_blue}; font-size: 14pt; margin: 20px 0 12px 0; }}"
        f"h3 {{ color: {p.text_primary}; font-size: 12pt; margin: 26px 0 10px 0; }}"
        f"p {{ color: {p.text_secondary}; margin: 8px 0 14px 0; }}"
        f"ul, ol {{ color: {p.text_secondary}; margin: 8px 0 16px 0; padding-left: 20px; }}"
        "p, li { line-height: 1.5; }"
        "li { margin-bottom: 9px; }"
        f"b, strong {{ color: {p.text_primary}; }}"
        f"code {{ color: {p.accent_orange}; background-color: {p.bg_elevated}; "
        f"font-family: {mono_fonts}; font-size: 10pt; }}"
        f"td {{ color: {p.text_secondary}; }}"
        "</style>"
    )


def striped_table_html(
    p: ThemePalette,
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    first_col_width_pct: int,
) -> str:
    """머리글과 홀수 행에 배경을 깔아 줄을 구분하는 표, 셀 내용은 호출자가 이스케이프한 HTML"""
    head = "".join(
        f"<th align='left' bgcolor='{p.bg_elevated}'"
        + (f" width='{first_col_width_pct}%'" if index == 0 else "")
        + f">{header}</th>"
        for index, header in enumerate(headers)
    )
    stripe = f" bgcolor='{p.bg_elevated}'"
    body = "".join(
        f"<tr{stripe if row_index % 2 else ''}>"
        + "".join(f"<td>{cell}</td>" for cell in row)
        + "</tr>"
        for row_index, row in enumerate(rows)
    )
    return (
        f"<table width='100%' cellspacing='0' cellpadding='{TABLE_CELL_PADDING_PX}'>"
        f"<tr>{head}</tr>{body}</table>"
    )
