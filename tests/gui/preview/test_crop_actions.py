"""활성 화면 직접 편집 아이콘 버튼의 아이콘, 접근성 이름, 툴팁, 신호 검증"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.gui.preview.crop_actions import (
    CROP_FULL_ICON,
    CROP_FULL_TEXT,
    CROP_FULL_TIP,
    CROP_RESET_ICON,
    CROP_RESET_TEXT,
    CROP_RESET_TIP,
    CropActionButtons,
)
from src.gui.styles.icons import SVG_TEMPLATES
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


class CropActionButtonsTests(GuiTestCase):
    """두 아이콘 버튼의 표기와 클릭 신호"""

    def setUp(self) -> None:
        self.buttons = CropActionButtons()
        self.addCleanup(self.buttons.deleteLater)
        self.reset_hits: list[None] = []
        self.full_hits: list[None] = []
        self.buttons.reset_requested.connect(lambda: self.reset_hits.append(None))
        self.buttons.keep_full_requested.connect(lambda: self.full_hits.append(None))

    def test_icons_are_defined_and_render(self) -> None:
        for name in (CROP_RESET_ICON, CROP_FULL_ICON):
            with self.subTest(name=name):
                self.assertIn(name, SVG_TEMPLATES)
        self.assertFalse(self.buttons.reset_button.icon().isNull())
        self.assertFalse(self.buttons.keep_full_button.icon().isNull())

    def test_buttons_have_no_text_and_noun_phrase_accessible_names(self) -> None:
        self.assertEqual(self.buttons.reset_button.text(), "")
        self.assertEqual(self.buttons.keep_full_button.text(), "")
        self.assertEqual(self.buttons.reset_button.accessibleName(), CROP_RESET_TEXT)
        self.assertEqual(self.buttons.keep_full_button.accessibleName(), CROP_FULL_TEXT)
        self.assertEqual(CROP_RESET_TEXT, "자동 감지로 되돌리기")
        self.assertEqual(CROP_FULL_TEXT, "전체 화면 유지")

    def test_buttons_carry_polite_tooltips(self) -> None:
        self.assertEqual(self.buttons.reset_button.toolTip(), CROP_RESET_TIP)
        self.assertEqual(self.buttons.keep_full_button.toolTip(), CROP_FULL_TIP)
        self.assertTrue(CROP_RESET_TIP.endswith("니다."))
        self.assertTrue(CROP_FULL_TIP.endswith("니다."))

    def test_clicking_emits_each_request(self) -> None:
        self.buttons.reset_button.click()
        self.buttons.keep_full_button.click()
        self.assertEqual(self.reset_hits, [None])
        self.assertEqual(self.full_hits, [None])

    def test_buttons_start_hidden(self) -> None:
        self.assertFalse(self.buttons.isVisible())

    def test_refresh_icons_keeps_icons_available(self) -> None:
        self.buttons.refresh_icons()
        self.assertFalse(self.buttons.reset_button.icon().isNull())
        self.assertFalse(self.buttons.keep_full_button.icon().isNull())
