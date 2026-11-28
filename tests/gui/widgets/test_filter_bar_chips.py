"""필터 칩 명세와 개수 배지 색상 규칙 검증"""

import unittest

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.widgets.filter_bar_chips import (
    BADGE_RADIUS_PX,
    CHIP_REVIEW,
    CHIP_SPECS,
    CHIP_VARY,
    count_badge_style,
    muted_count_badge_style,
)


class ChipSpecTests(unittest.TestCase):
    """칩 명세 표가 두 칩을 이름, 아이콘, 배지 색과 함께 담는지 검증"""

    def test_specs_cover_the_two_chips_with_unique_object_names(self) -> None:
        self.assertEqual([CHIP_REVIEW, CHIP_VARY], [spec[0] for spec in CHIP_SPECS])
        self.assertEqual(2, len({spec[1] for spec in CHIP_SPECS}))
        self.assertEqual(
            ["ReviewFilterChip", "VaryFilterChip"], [spec[1] for spec in CHIP_SPECS]
        )
        self.assertEqual(["alert-triangle", "margins-vary"], [spec[4] for spec in CHIP_SPECS])

    def test_badge_style_uses_the_chip_palette_token_per_tone(self) -> None:
        review = count_badge_style(DARK_PALETTE, CHIP_REVIEW)
        vary = count_badge_style(DARK_PALETTE, CHIP_VARY)
        clear = count_badge_style(LIGHT_PALETTE, "clear")
        self.assertIn(DARK_PALETTE.badge_pending[2], review)
        self.assertIn(DARK_PALETTE.badge_vary[2], vary)
        self.assertIn(LIGHT_PALETTE.accent_blue, clear)
        for css in (review, vary, clear):
            self.assertIn("QLabel#FilterBarCountBadge {", css)
            self.assertIn("font-weight: 700;", css)

    def test_muted_badge_style_uses_the_elevated_background_tokens(self) -> None:
        """묶기 메뉴와 버튼의 알약이 값 강조색 대신 조용한 팔레트 토큰을 쓰는지 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            css = muted_count_badge_style(palette)
            self.assertIn(palette.bg_elevated, css)
            self.assertIn(palette.text_secondary, css)
            self.assertIn("QLabel#FilterBarCountBadge {", css)
            self.assertIn(f"border-radius: {BADGE_RADIUS_PX}px;", css)
            # - 값 강조색은 쓰지 않아 칩 배지와 구분됨
            self.assertNotIn(palette.accent_blue, css)
            self.assertNotIn(palette.text_on_accent, css)


if __name__ == "__main__":
    unittest.main()
