"""미리 보기 영상 위에 뜨는 활성 화면 편집 도구 모음(HUD) 검증"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtWidgets import QApplication, QFrame

from src.core.models import ActiveFrame
from src.gui.preview.crop_actions import CROP_FULL_TEXT, CROP_RESET_TEXT
from src.gui.preview.crop_hud import (
    CROP_HUD_FRAME_OBJECT,
    CROP_HUD_SIZE_OBJECT,
    HUD_TOP_GAP_PX,
    CropEditHud,
    hud_position,
)
from src.gui.styles.palettes import LIGHT_PALETTE
from src.gui.styles.qss_preview import build_preview_qss
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])
_AREA = QRect(100, 200, 800, 600)


class HudPositionTests(GuiTestCase):
    """도구 모음을 놓을 위치 계산"""

    def test_centered_at_the_top_of_the_area(self) -> None:
        pos = hud_position(_AREA, QSize(200, 40))
        self.assertEqual(pos, QPoint(100 + 300, 200 + HUD_TOP_GAP_PX))

    def test_stays_inside_the_left_edge_when_too_wide(self) -> None:
        pos = hud_position(_AREA, QSize(900, 40))
        self.assertEqual(pos, QPoint(100, 200 + HUD_TOP_GAP_PX))


class CropEditHudTests(GuiTestCase):
    """HUD 표기, 버튼, 표시와 숨김"""

    def setUp(self) -> None:
        self.area = QRect(_AREA)
        self.hud = CropEditHud(None, lambda: self.area)
        self.addCleanup(self.hud.deleteLater)
        self.reset_hits: list[None] = []
        self.full_hits: list[None] = []
        self.hud.action_buttons.reset_requested.connect(lambda: self.reset_hits.append(None))
        self.hud.action_buttons.keep_full_requested.connect(lambda: self.full_hits.append(None))

    def test_size_label_shows_the_kept_width_and_height(self) -> None:
        self.hud.set_kept(ActiveFrame(1280, 720, 0, 140))
        self.assertEqual(self.hud.size_label.text(), "1280 x 720")

    def test_object_names_match_the_preview_stylesheet(self) -> None:
        self.assertEqual(self.hud.size_label.objectName(), CROP_HUD_SIZE_OBJECT)
        frames = self.hud.findChildren(QFrame, CROP_HUD_FRAME_OBJECT)
        self.assertEqual(len(frames), 1)
        qss = build_preview_qss(LIGHT_PALETTE)
        self.assertIn(f"QFrame#{CROP_HUD_FRAME_OBJECT}", qss)
        self.assertIn(f"QLabel#{CROP_HUD_SIZE_OBJECT}", qss)

    def test_actions_keep_their_accessible_names_and_tooltips(self) -> None:
        self.assertEqual(
            self.hud.action_buttons.reset_button.accessibleName(), CROP_RESET_TEXT
        )
        self.assertEqual(
            self.hud.action_buttons.keep_full_button.accessibleName(), CROP_FULL_TEXT
        )
        self.assertEqual(self.hud.action_buttons.reset_button.text(), "")
        self.assertEqual(self.hud.action_buttons.keep_full_button.text(), "")

    def test_clicking_the_action_buttons_emits_the_requests(self) -> None:
        self.hud.action_buttons.reset_button.click()
        self.hud.action_buttons.keep_full_button.click()
        self.assertEqual(self.reset_hits, [None])
        self.assertEqual(self.full_hits, [None])

    def test_show_and_reposition_follow_the_video_area(self) -> None:
        self.hud.set_kept(ActiveFrame(1920, 800, 0, 140))
        self.hud.show_hud()
        _APP.processEvents()
        self.assertTrue(self.hud.isVisible())
        self.assertGreaterEqual(self.hud.x(), _AREA.left())
        self.assertLess(self.hud.x() + self.hud.width(), _AREA.right())
        self.assertEqual(self.hud.y(), _AREA.top() + HUD_TOP_GAP_PX)
        self.area = QRect(300, 400, 640, 360)
        self.hud.reposition()
        _APP.processEvents()
        self.assertGreaterEqual(self.hud.y(), 400)
        self.assertLessEqual(self.hud.x(), 300 + 640 - self.hud.width())

    def test_reposition_ignored_while_hidden(self) -> None:
        self.hud.reposition()
        self.assertFalse(self.hud.isVisible())

    def test_refresh_icons_keeps_icons_available(self) -> None:
        self.hud.refresh_icons()
        self.assertFalse(self.hud.action_buttons.reset_button.icon().isNull())
        self.assertFalse(self.hud.action_buttons.keep_full_button.icon().isNull())
