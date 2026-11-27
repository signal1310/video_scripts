"""표 셀 글꼴 종류별 QFont 보관과 취소선 글꼴 캐시 모듈

- 열 정의의 글꼴 종류(굵게, 경로, 고정폭, 고정폭 굵게)를 QFont로 바꿔 재사용
- 잘라내기 꺼짐 활성 화면 셀처럼 같은 글꼴에 취소선만 더한 변형도 열별로 한 번만 만듦
"""

from __future__ import annotations

from PyQt6.QtGui import QFont

from src.gui.constants import FONT_MONO_FAMILY, FONT_UI_FAMILY, FONT_UI_PT, make_path_font
from src.gui.table.table_column_styles import (
    FONT_BOLD,
    FONT_MONO,
    FONT_MONO_BOLD,
    FONT_PATH,
)

FONT_KINDS: frozenset[str] = frozenset({FONT_BOLD, FONT_MONO, FONT_MONO_BOLD, FONT_PATH})


class CellFonts:
    """표 셀 글꼴 묶음과 취소선 변형 캐시"""

    def __init__(self) -> None:
        self._mono = QFont(FONT_MONO_FAMILY, FONT_UI_PT)
        self._mono_bold = QFont(FONT_MONO_FAMILY, FONT_UI_PT, QFont.Weight.Bold)
        self._bold = QFont(FONT_UI_FAMILY, FONT_UI_PT, QFont.Weight.Bold)
        self._path = make_path_font(FONT_UI_PT)
        self._strikeout: dict[int, QFont] = {}

    def for_kind(self, font_kind: str | None) -> QFont:
        """열 글꼴 종류에 대응하는 글꼴, 알 수 없는 종류는 고정폭 글꼴"""
        if font_kind == FONT_BOLD:
            return self._bold
        if font_kind == FONT_PATH:
            return self._path
        if font_kind == FONT_MONO_BOLD:
            return self._mono_bold
        return self._mono

    def strikeout_for(self, col: int, font_kind: str | None) -> QFont:
        """열 글꼴에 취소선만 더한 글꼴, 열마다 한 번 만들어 재사용"""
        cached = self._strikeout.get(col)
        if cached is not None:
            return cached
        strike = QFont(self.for_kind(font_kind))
        strike.setStrikeOut(True)
        self._strikeout[col] = strike
        return strike


__all__ = ["FONT_KINDS", "CellFonts"]
