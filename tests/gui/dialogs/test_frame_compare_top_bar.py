"""화질 비교 상단 2줄 조작 바 검증: 도킹, 접기, 호버 오버레이, 안전장치, 툴팁, 배율"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.analysis.frame_compare import resolve_compare_plan
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs.frame_compare_canvas import MODE_SIDE, MODE_SPLIT, MODE_TOGGLE
from src.gui.dialogs.frame_compare_top_bar import CompareTopBar
from src.gui.styles.palettes import LIGHT_PALETTE
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _image() -> QImage:
    image = QImage(80, 40, QImage.Format.Format_RGB32)
    image.fill(QColor("#FF0000"))
    return image


class CompareTopBarTests(GuiTestCase):
    """상단 바 구성과 접기, 오버레이, 배율 조작"""

    def setUp(self) -> None:
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.window.resize(900, 600)
        self.modes: list[str] = []
        self.zooms: list[str] = []
        self.targets: list[str] = []
        self.stage = layout.build_stage(self.window, lambda _scale: None, lambda _side: None)
        self.top = CompareTopBar(
            self.window,
            self.stage.canvas,
            on_mode=self.modes.append,
            on_zoom=self.zooms.append,
            on_target=self.targets.append,
        )
        self.window.show()
        _APP.processEvents()

    def _margins(self) -> tuple[int, int, int, int]:
        wrap = self.stage.canvas.parentWidget()
        assert wrap is not None
        layout_obj = wrap.layout()
        assert layout_obj is not None
        margins = layout_obj.contentsMargins()
        return margins.left(), margins.top(), margins.right(), margins.bottom()

    def test_default_expanded_bar_is_docked_above_the_canvas(self) -> None:
        self.assertFalse(self.top.is_collapsed())
        self.assertTrue(self.top.container.isVisible())
        self.assertGreater(self._margins()[1], 0)
        self.assertFalse(self.top.is_overlay_visible())
        self.assertEqual(self.top.filename.text(), "")
        self.assertEqual(self.top.collapse_button.accessibleName(), "조작 바 접기")

    def test_mode_zoom_and_mode_buttons_call_back(self) -> None:
        self.top.mode_buttons[MODE_SPLIT].click()
        self.top.mode_buttons[MODE_TOGGLE].click()
        self.top.mode_buttons[MODE_SIDE].click()
        self.top.zoom_buttons["200"].click()
        self.assertEqual(self.modes, [MODE_SPLIT, MODE_TOGGLE, MODE_SIDE])
        self.assertEqual(self.zooms, ["200"])

    def test_collapse_removes_the_docking_margin_and_shows_overlay_on_activity(self) -> None:
        """접으면 도킹 여백이 사라지고 마우스 움직임에 반투명 오버레이가 뜨는지 검증"""
        expanded_top = self._margins()[1]
        self.top.set_collapsed(True)
        self.assertTrue(self.top.is_collapsed())
        self.assertTrue(self.top.container.isHidden())
        self.assertLess(self._margins()[1], expanded_top)
        self.assertEqual(self.top.collapse_button.accessibleName(), "조작 바 펼치기")
        self.top.note_mouse_activity()
        self.assertTrue(self.top.is_overlay_visible())
        self.assertTrue(self.top.container.isVisible())
        self.assertEqual(self.top.container.property("overlay"), True)
        self.assertGreater(self.stage.canvas._top_inset, 0)

    def test_safety_check_blocks_auto_hide_until_released(self) -> None:
        allowed = [False]
        self.top.set_collapsed(True)
        self.top.set_safety_check(lambda: allowed[0])
        self.top.reveal()
        self.top.hide_overlay_if_allowed()
        self.assertTrue(self.top.is_overlay_visible())
        allowed[0] = True
        self.top.hide_overlay_if_allowed()
        self.assertFalse(self.top.is_overlay_visible())
        self.assertTrue(self.top.container.isHidden())
        self.assertEqual(self.stage.canvas._top_inset, 0)

    def test_toggle_collapsed_flips_the_state(self) -> None:
        self.top.toggle_collapsed()
        self.assertTrue(self.top.is_collapsed())
        self.top.toggle_collapsed()
        self.assertFalse(self.top.is_collapsed())

    def test_shortcut_tooltips_include_custom_keys(self) -> None:
        """사용자가 바꾼 키가 상단 버튼 툴팁에 반영되는지 검증"""
        shortcuts = dict(DEFAULT_SHORTCUTS)
        shortcuts["compare.mode_split"] = "F2"
        shortcuts["compare.zoom_fit"] = "Z"
        shortcuts["compare.toggle_controls"] = "G"
        self.top.sync_shortcut_tooltips(shortcuts)
        self.assertIn("[F2]", self.top.mode_buttons[MODE_SPLIT].toolTip())
        self.assertIn("[Z]", self.top.zoom_buttons["fit"].toolTip())
        self.assertIn("[G]", self.top.collapse_button.toolTip())
        self.assertEqual(self.top.target_button.toolTip(), layout.TARGET_MENU_TIP)

    def test_target_menu_is_rebuilt_and_triggers_the_callback(self) -> None:
        plan = resolve_compare_plan(source_path=__file__, width=3840, height=2160, size_bytes=1024)
        self.top.rebuild_target_menu(plan)
        actions = self.top.target_menu.actions()
        self.assertTrue(actions)
        self.assertTrue(actions[0].isChecked())
        actions[0].trigger()
        self.assertEqual(self.targets, [plan.targets[0].key])

    def test_close_open_menu_reports_whether_it_closed(self) -> None:
        self.assertFalse(self.top.close_open_menu())
        with (
            mock.patch.object(self.top.target_menu, "isVisible", return_value=True),
            mock.patch.object(self.top.target_menu, "close") as close,
        ):
            self.assertTrue(self.top.close_open_menu())
        close.assert_called_once()

    def test_zoom_input_and_manual_zoom_update_checks_and_value(self) -> None:
        self.stage.canvas.set_images(_image(), _image())
        self.top.set_zoom_checks("1")
        self.assertTrue(self.top.zoom_buttons["1"].isChecked())
        self.top.zoom_input.setValue(250)
        self.assertTrue(self.top.apply_zoom_input())
        self.assertAlmostEqual(self.stage.canvas.scale(), 2.5)
        self.assertFalse(any(button.isChecked() for button in self.top.zoom_buttons.values()))
        self.top.manual_zoom(1)
        self.assertAlmostEqual(self.stage.canvas.scale(), 2.5 * 1.25)
        self.assertEqual(self.top.zoom_input.value(), round(self.stage.canvas.scale() * 100))
        self.top.sync_zoom_input()
        self.assertEqual(self.top.zoom_input.value(), round(self.stage.canvas.scale() * 100))

    def test_mode_checks_follow_the_current_mode(self) -> None:
        self.top.set_mode_checks(MODE_SIDE)
        self.assertTrue(self.top.mode_buttons[MODE_SIDE].isChecked())
        self.assertFalse(self.top.mode_buttons[MODE_SPLIT].isChecked())

    def test_apply_theme_and_resize_keep_the_bar_aligned(self) -> None:
        self.top.apply_theme(LIGHT_PALETTE)
        self.assertFalse(self.top.collapse_button.icon().isNull())
        self.assertFalse(self.top.target_button.icon().isNull())
        self.stage.canvas.resize(500, 300)
        QApplication.sendEvent(self.stage.canvas, QEvent(QEvent.Type.Resize))
        self.assertLessEqual(self.top.container.width(), max(self.stage.canvas.width(), 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
