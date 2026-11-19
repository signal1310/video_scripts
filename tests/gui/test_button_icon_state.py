"""버튼 비활성 상태에서 아이콘 색이 테마 팔레트의 비활성 텍스트 색과 일치하는지 검증"""

import os
import unittest

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon, QImage
from PyQt6.QtWidgets import QApplication, QPushButton

from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE, ThemePalette
from src.gui.widgets.header_bar import SplitAnalyzeButton
from tests._gui_helpers import GuiTestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])

_ICON_NAME = "folder"
_ICON_SIZE = 14
_PAD_RIGHT = 7


def _opaque_rgb(icon: QIcon, mode: QIcon.Mode, width: int, height: int) -> set[str]:
    """아이콘을 지정 모드로 렌더링한 뒤 불투명 픽셀의 RGB 집합 반환"""
    image: QImage = icon.pixmap(QSize(width, height), mode).toImage()
    colors: set[str] = set()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 255:
                colors.add(color.name())
    return colors


def _assert_icon_colors(
    test: unittest.TestCase, button: QPushButton, palette: ThemePalette, width: int, height: int
) -> None:
    """버튼 아이콘의 활성, 비활성 색이 팔레트의 온 액센트, 비활성 텍스트 색과 같은지 확인"""
    icon = button.icon()
    test.assertEqual(
        _opaque_rgb(icon, QIcon.Mode.Normal, width, height), {palette.text_on_accent.lower()}
    )
    test.assertEqual(
        _opaque_rgb(icon, QIcon.Mode.Disabled, width, height), {palette.text_muted.lower()}
    )


class ButtonIconStateTests(GuiTestCase):
    def test_disabled_color_applies_only_to_disabled_mode(self) -> None:
        """disabled_color를 지정하면 활성 모드는 기본 색, 비활성 모드는 지정 색으로 렌더링됨을 검증"""
        icon = get_icon(
            _ICON_NAME,
            color=LIGHT_PALETTE.text_on_accent,
            size=_ICON_SIZE,
            pad_right=_PAD_RIGHT,
            disabled_color=LIGHT_PALETTE.text_muted,
        )
        size = (_ICON_SIZE + _PAD_RIGHT, _ICON_SIZE)
        self.assertEqual(
            _opaque_rgb(icon, QIcon.Mode.Normal, *size), {LIGHT_PALETTE.text_on_accent.lower()}
        )
        self.assertEqual(
            _opaque_rgb(icon, QIcon.Mode.Disabled, *size), {LIGHT_PALETTE.text_muted.lower()}
        )

    def test_split_analyze_icons_follow_theme_in_both_states(self) -> None:
        """분할 분석 버튼의 본체와 화살표 아이콘이 테마마다 활성, 비활성 텍스트 색과 맞춰짐을 검증"""
        for theme, palette in (("dark", DARK_PALETTE), ("light", LIGHT_PALETTE)):
            with self.subTest(theme=theme):
                split = SplitAnalyzeButton()
                self.addCleanup(split.close)
                split.apply_theme(palette)
                _assert_icon_colors(
                    self, split.btn_main, palette, _ICON_SIZE + _PAD_RIGHT, _ICON_SIZE
                )
                _assert_icon_colors(self, split.btn_arrow, palette, 12, 12)


if __name__ == "__main__":
    unittest.main()
