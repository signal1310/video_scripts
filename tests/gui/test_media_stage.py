"""미리 보기와 화질 비교 창이 함께 쓰는 무대 색, 다음 색 순환, 바깥 경계 점선 계산 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from src.gui.media_stage import (
    OUTLINE_WIDTH_PX,
    STAGE_BACKGROUND_LABELS,
    STAGE_BACKGROUND_SHORTCUT_DESCRIPTION,
    STAGE_BACKGROUND_TIP,
    STAGE_FRAME_OUTLINE_TIP,
    make_outline_pen,
    next_stage_background,
    stage_background_notice,
    stage_color,
    stage_frame_rect,
    stage_outline_rect,
)
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE

_APP = QApplication.instance() or QApplication([])


class StageColorTests(unittest.TestCase):
    """배경 색 키와 팔레트 토큰, 색 순환, 승인 문구"""

    def test_each_key_maps_to_its_theme_independent_token(self) -> None:
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            self.assertEqual(stage_color(palette, "black"), palette.media_bg)
            self.assertEqual(stage_color(palette, "gray"), palette.media_stage_gray)
            self.assertEqual(stage_color(palette, "white"), palette.media_stage_white)

    def test_unknown_key_falls_back_to_black(self) -> None:
        self.assertEqual(stage_color(DARK_PALETTE, "purple"), DARK_PALETTE.media_bg)

    def test_cycle_goes_black_gray_white_and_back(self) -> None:
        self.assertEqual(next_stage_background("black"), "gray")
        self.assertEqual(next_stage_background("gray"), "white")
        self.assertEqual(next_stage_background("white"), "black")
        self.assertEqual(next_stage_background("bogus"), "black")

    def test_labels_notice_and_tooltips_are_the_approved_strings(self) -> None:
        self.assertEqual(STAGE_BACKGROUND_LABELS, {"black": "검정", "gray": "회색", "white": "흰색"})
        self.assertEqual(stage_background_notice("gray"), "배경 색: 회색")
        self.assertEqual(stage_background_notice("bogus"), "배경 색: 검정")
        self.assertEqual(
            STAGE_BACKGROUND_TIP,
            "영상 뒤 배경 색을 바꿉니다. 밝은 배경에서는 검은 여백이 잘 보입니다.",
        )
        self.assertEqual(
            STAGE_BACKGROUND_SHORTCUT_DESCRIPTION,
            "영상 뒤 배경 색을 검정, 회색, 흰색 순서로 바꿉니다.",
        )
        self.assertEqual(
            STAGE_FRAME_OUTLINE_TIP,
            "영상 바깥 경계에 점선을 그립니다. 검은 배경에서도 검은 여백이 어디까지인지 보입니다.",
        )


class StageGeometryTests(unittest.TestCase):
    """점선 사각형과 배율이 적용된 영상 사각형 계산"""

    def test_outline_rect_grows_by_the_line_width(self) -> None:
        frame = QRectF(10.0, 20.0, 100.0, 60.0)
        outline = stage_outline_rect(frame)
        self.assertEqual(outline, QRectF(9.0, 19.0, 102.0, 62.0))
        self.assertAlmostEqual(outline.left(), frame.left() - OUTLINE_WIDTH_PX)
        self.assertAlmostEqual(outline.right(), frame.right() + OUTLINE_WIDTH_PX)
        self.assertEqual(stage_outline_rect(frame, 2.0), QRectF(8.0, 18.0, 104.0, 64.0))

    def test_frame_rect_applies_scale_offset_and_side_origin(self) -> None:
        rect = stage_frame_rect((200, 100), 2.0, -30.0, 5.0)
        self.assertEqual(rect, QRectF(-30.0, 5.0, 400.0, 200.0))
        right = stage_frame_rect((200, 100), 2.0, -30.0, 5.0, origin_x=250.0)
        self.assertEqual(right.x(), 220.0)

    def test_outline_pen_is_cosmetic_and_dashed(self) -> None:
        pen = make_outline_pen("#F59E0B")
        self.assertTrue(pen.isCosmetic())
        self.assertEqual(pen.color().name().upper(), "#F59E0B")
        self.assertEqual(len(pen.dashPattern()), 2)
        self.assertEqual(QColor(DARK_PALETTE.media_stage_outline).name(), "#f59e0b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
