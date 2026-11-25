"""아이콘 생성 검증: SVG 색 적용, 활성 팔레트, 아이콘 버튼, 아이콘 캐시와 QSS 경로"""

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QApplication, QPushButton

from src.gui.styles import icons as module
from src.gui.styles.icons import (
    build_svg_xml,
    create_icon_button,
    get_active_palette,
    get_icon,
    resolve_qss_icon_path,
    set_active_palette,
    set_button_icon,
)
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE

_APP = QApplication.instance() or QApplication([])


class SvgTests(unittest.TestCase):
    """SVG 문자열과 색, 굵기"""

    def test_known_icon_uses_given_color_and_stroke(self) -> None:
        xml = build_svg_xml("compare", color="#123456", stroke_width=2.5)
        self.assertIn('stroke="#123456"', xml)
        self.assertIn("stroke-width=\"2.5\"", xml)
        self.assertIn("<rect", xml)

    def test_unknown_icon_returns_empty_body(self) -> None:
        xml = build_svg_xml("no-such-icon", color="#000000")
        self.assertIn("<svg", xml)
        self.assertNotIn("<rect", xml)

    def test_move_horizontal_icon_is_registered(self) -> None:
        """가로 양방향 화살표 아이콘이 등록되고 테마 색으로 그려지는지 검증"""
        self.assertIn('<polyline points="18 8 22 12 18 16"/>', module.SVG_TEMPLATES["move-horizontal"])
        for palette in (LIGHT_PALETTE, DARK_PALETTE):
            icon = get_icon("move-horizontal", palette=palette, size=16)
            self.assertFalse(icon.isNull())
            self.assertFalse(icon.pixmap(16, 16).isNull())
        self.assertIn("maximize-2", module.SVG_TEMPLATES)


    def test_crop_off_icon_adds_one_diagonal_slash_to_the_crop_paths(self) -> None:
        """끈 상태 아이콘이 crop 패스에 대각선 하나를 더한 모양인지 검증"""
        crop = module.SVG_TEMPLATES["crop"]
        crop_off = module.SVG_TEMPLATES["crop-off"]
        self.assertTrue(crop_off.startswith(crop))
        self.assertEqual(crop_off[len(crop) :], '<path d="m2 2 20 20"/>')
        for palette in (LIGHT_PALETTE, DARK_PALETTE):
            icon = get_icon("crop-off", color=palette.media_fg, size=16)
            self.assertFalse(icon.pixmap(16, 16).toImage().isNull())


class PaletteTests(unittest.TestCase):
    """활성 팔레트 전환"""

    def test_active_palette_round_trip(self) -> None:
        original = get_active_palette()
        self.addCleanup(set_active_palette, original)
        set_active_palette(LIGHT_PALETTE)
        self.assertIs(get_active_palette(), LIGHT_PALETTE)


class IconButtonTests(unittest.TestCase):
    """아이콘, 버튼, 캐시 디렉터리와 QSS 아이콘 경로"""

    def test_get_icon_with_disabled_color_and_padding(self) -> None:
        icon = get_icon("compare", color="#FFFFFF", size=16, disabled_color="#888888", pad_right=4)
        self.assertFalse(icon.isNull())
        self.assertEqual(icon.availableSizes(), [QSize(20, 16), QSize(40, 32)])

    def test_set_button_icon_updates_icon_size(self) -> None:
        button = QPushButton()
        self.addCleanup(button.deleteLater)
        set_button_icon(button, "compare", size=14, pad_right=7)
        self.assertFalse(button.icon().isNull())
        self.assertEqual(button.iconSize(), QSize(21, 14))

    def test_create_icon_button_sets_properties_and_click(self) -> None:
        clicks: list[bool] = []
        button = create_icon_button(
            None, "툴팁", lambda: clicks.append(True), accessible_name="화질 비교", fixed_size=30
        )
        self.addCleanup(button.deleteLater)
        self.assertEqual((button.width(), button.height()), (30, 30))
        self.assertEqual(button.accessibleName(), "화질 비교")
        self.assertEqual(button.toolTip(), "툴팁")
        button.click()
        self.assertEqual(clicks, [True])

    def test_cache_dir_and_qss_icon_path_write_only_inside_temp_cache(self) -> None:
        self.assertIn(module._ICON_CACHE_SUBDIR, module._get_icon_cache_dir())
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(module, "_get_icon_cache_dir", return_value=tmp), mock.patch.object(
                module, "ASSETS_DIR", os.path.join(tmp, "missing")
            ):
                path = resolve_qss_icon_path("compare", LIGHT_PALETTE)
            self.assertTrue(path.endswith(".svg"))
            self.assertTrue(os.path.isfile(path))
            self.assertIn("compare", os.path.basename(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
