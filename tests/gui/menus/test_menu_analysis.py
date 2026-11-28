"""분석 메뉴의 화질 분석 항목 구성과 콜백 연결 검증"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow

from src.gui.menus.menu_analysis import build_analysis_menu
from src.gui.menus.menu_base import MenuContext
from tests._gui_helpers import make_menu_callbacks

_APP = QApplication.instance() or QApplication([])


class AnalysisMenuTests(unittest.TestCase):
    """분석 메뉴에 화질 분석 항목이 추가되고 호출되는지 검증"""

    def setUp(self) -> None:
        self.window = QMainWindow()
        self.addCleanup(self.window.deleteLater)
        bar = self.window.menuBar()
        assert bar is not None
        self.triggered: list[str] = []
        callbacks = make_menu_callbacks()
        callbacks.on_frame_analysis_selected = lambda: self.triggered.append("selected")
        callbacks.on_frame_analysis_all = lambda: self.triggered.append("all")
        self.menu = build_analysis_menu(MenuContext(self.window), bar, callbacks)

    def test_crop_scan_actions_use_the_crop_icon(self) -> None:
        """여백 감지 두 항목이 film, analyze가 아닌 crop 아이콘을 쓰는지 검증"""
        recorded: dict[str, str] = {}
        original = MenuContext.create_action

        def spy(
            context: MenuContext,
            text: str,
            slot: object,
            *,
            action_id: str,
            icon_name: str = "",
            status_tip: str = "",
        ) -> object:
            recorded[action_id] = icon_name
            return original(
                context,
                text,
                slot,  # type: ignore[arg-type]
                action_id=action_id,
                icon_name=icon_name,
                status_tip=status_tip,
            )

        bar = self.window.menuBar()
        assert bar is not None
        with mock.patch.object(MenuContext, "create_action", spy):
            build_analysis_menu(MenuContext(self.window), bar, make_menu_callbacks())
        self.assertEqual(recorded["analyze.crop_selected"], "crop")
        self.assertEqual(recorded["analyze.crop_all"], "crop")

    def test_frame_analysis_items_and_actions_are_present(self) -> None:
        menu = self.menu.analyze_mode_menu.parentWidget()
        self.assertIsNotNone(menu)
        assert menu is not None
        labels = [action.text() for action in menu.actions()]
        self.assertIn("선택 영상 화질 분석", labels)
        self.assertIn("전체 영상 화질 분석", labels)
        self.menu.act_frame_analysis_selected.trigger()
        self.menu.act_frame_analysis_all.trigger()
        self.assertEqual(self.triggered, ["selected", "all"])


if __name__ == "__main__":
    unittest.main()
