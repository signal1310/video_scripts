"""셀 글꼴 종류별 QFont 선택과 취소선 변형 캐시를 검증"""

import os
import unittest

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from src.gui.table.table_cell_fonts import FONT_KINDS, CellFonts
from src.gui.table.table_column_styles import (
    FONT_BOLD,
    FONT_MONO,
    FONT_MONO_BOLD,
    FONT_PATH,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class CellFontsTests(unittest.TestCase):
    """글꼴 종류별 QFont와 취소선 변형이 같은 객체를 재사용하는지 검증"""

    def setUp(self) -> None:
        self.fonts = CellFonts()

    def test_known_kinds_map_to_distinct_fonts(self) -> None:
        bold = self.fonts.for_kind(FONT_BOLD)
        path = self.fonts.for_kind(FONT_PATH)
        mono_bold = self.fonts.for_kind(FONT_MONO_BOLD)
        mono = self.fonts.for_kind(FONT_MONO)
        self.assertTrue(bold.bold())
        self.assertTrue(mono_bold.bold())
        self.assertFalse(mono.bold())
        self.assertEqual(len({bold.key(), path.key(), mono_bold.key(), mono.key()}), 4)
        self.assertEqual(FONT_KINDS, frozenset({FONT_BOLD, FONT_MONO, FONT_MONO_BOLD, FONT_PATH}))

    def test_unknown_kind_falls_back_to_mono(self) -> None:
        self.assertEqual(self.fonts.for_kind(None).key(), self.fonts.for_kind(FONT_MONO).key())
        self.assertEqual(self.fonts.for_kind("nope").key(), self.fonts.for_kind(FONT_MONO).key())

    def test_strikeout_font_keeps_base_family_and_is_cached(self) -> None:
        first = self.fonts.strikeout_for(19, FONT_MONO_BOLD)
        second = self.fonts.strikeout_for(19, FONT_MONO_BOLD)
        self.assertIs(first, second)
        self.assertTrue(first.strikeOut())
        self.assertEqual(first.family(), self.fonts.for_kind(FONT_MONO_BOLD).family())

    def test_strikeout_font_is_cached_per_column(self) -> None:
        other = self.fonts.strikeout_for(20, FONT_MONO_BOLD)
        self.assertIsNot(other, self.fonts.strikeout_for(19, FONT_MONO_BOLD))
        self.assertIsInstance(other, QFont)


if __name__ == "__main__":
    unittest.main(verbosity=2)
