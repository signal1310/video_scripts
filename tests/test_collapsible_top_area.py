"""상단 영역 접기, 탭 팝업 열기, 펼치기 전환, 메뉴 단축키와 접힘 상태 복원을 검증"""

import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from src.core.ui_state import UiLayoutState
from src.gui.main_window import MainWindow
from src.gui.widgets.collapsible_top_area import (
    TOP_AREA_EXPAND_NAME,
    TOP_AREA_EXPAND_TOOLTIP,
    CollapsibleTopArea,
    TopAreaSection,
    toggle_top_area,
)
from src.gui.window_layout import export_top_area_collapsed, restore_window_ui_state
from tests._gui_helpers import GuiTestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class CollapsibleTopAreaWidgetTests(GuiTestCase):
    def setUp(self) -> None:
        self.host = QWidget()
        self.addCleanup(self.host.close)
        self.first = QLabel("first", self.host)
        self.second = QLabel("second", self.host)
        self.area = CollapsibleTopArea(
            [TopAreaSection("첫째", self.first), TopAreaSection("둘째", self.second)],
            self.host,
        )
        self.host.resize(400, 300)
        self.host.show()

    def test_collapse_hides_contents_and_shows_tab_strip(self) -> None:
        """접으면 본문이 숨겨지고 탭 줄이 나타나며, 펼치면 탭 줄이 사라지고 본문이 다시 보임을 검증"""
        self.assertFalse(self.area.collapsed)
        self.assertFalse(self.area.tab_button(0).isVisibleTo(self.area))
        self.area.set_collapsed(True)
        self.assertTrue(self.area.collapsed)
        self.assertFalse(self.first.isVisibleTo(self.area))
        self.assertFalse(self.second.isVisibleTo(self.area))
        self.assertTrue(self.area.tab_button(0).isVisibleTo(self.area))
        self.assertTrue(self.area.expand_button.isVisibleTo(self.area))

        self.area.toggle()
        self.assertFalse(self.area.collapsed)
        self.assertTrue(self.first.isVisibleTo(self.area))
        self.assertFalse(self.area.tab_button(0).isVisibleTo(self.area))

    def test_expand_button_is_icon_only_and_expands_area(self) -> None:
        """접힌 상태의 펼치기 버튼은 문자 없이 아이콘만 두고, 툴팁과 접근성 이름을 가지며 누르면 펼쳐짐을 검증"""
        expand = self.area.expand_button
        self.area.set_collapsed(True)
        self.assertEqual(expand.text(), "")
        self.assertFalse(expand.icon().isNull())
        self.assertEqual(expand.toolTip(), TOP_AREA_EXPAND_TOOLTIP)
        self.assertEqual(expand.accessibleName(), TOP_AREA_EXPAND_NAME)

        expand.click()
        self.assertFalse(self.area.collapsed)
        self.assertTrue(self.first.isVisibleTo(self.area))

    def test_popup_section_keeps_expanded_height_in_any_open_order(self) -> None:
        """팝업으로 연 영역의 높이가 어떤 순서로 열든 sizeHint와 같음을 검증"""
        sections = (self.first, self.second)
        self.area.set_collapsed(True)
        for index in (0, 1, 0):
            self.area.open_section(index)
            self.assertEqual(sections[index].height(), sections[index].sizeHint().height())
            self.area.close_section()

    def test_signal_emits_only_on_change(self) -> None:
        """같은 상태로 설정하면 시그널이 방출되지 않고 바뀔 때만 새 상태가 전달됨을 검증"""
        emitted: list[bool] = []
        self.area.collapsed_changed.connect(emitted.append)
        self.area.set_collapsed(False)
        self.area.set_collapsed(True)
        self.area.set_collapsed(True)
        self.assertEqual(emitted, [True])

    def test_tab_opens_section_as_popup_and_second_click_closes(self) -> None:
        """접힌 상태에서 탭을 누르면 영역이 팝업으로 옮겨지고 같은 탭을 다시 누르면 원래 자리로 돌아옴을 검증"""
        self.area.set_collapsed(True)
        self.area.tab_button(0).click()
        self.assertEqual(self.area.open_section_index, 0)
        self.assertIsNot(self.first.window(), self.host)
        self.assertTrue(self.area.tab_button(0).isChecked())

        self.area.tab_button(0).click()
        self.assertIsNone(self.area.open_section_index)
        self.assertIs(self.first.window(), self.host)
        self.assertFalse(self.area.tab_button(0).isChecked())

    def test_tab_switches_to_another_section(self) -> None:
        """팝업이 열린 상태에서 다른 탭을 누르면 이전 영역이 돌아가고 새 영역이 열림을 검증"""
        self.area.set_collapsed(True)
        self.area.open_section(0)
        self.area.tab_button(1).click()
        self.assertEqual(self.area.open_section_index, 1)
        self.assertIs(self.first.window(), self.host)
        self.assertIsNot(self.second.window(), self.host)

    def test_tab_click_is_ignored_when_pinned(self) -> None:
        """펼친 상태에서는 탭을 눌러도 팝업이 열리지 않고 본문이 그대로 보임을 검증"""
        self.area.open_section(0)
        self.assertIsNone(self.area.open_section_index)
        self.area.tab_button(0).click()
        self.assertIsNone(self.area.open_section_index)
        self.assertTrue(self.first.isVisibleTo(self.area))

    def test_expanding_while_popup_open_restores_section_in_body(self) -> None:
        """팝업이 열린 상태에서 펼치기를 누르면 팝업이 닫히고 모든 영역이 본문에 보임을 검증"""
        self.area.set_collapsed(True)
        self.area.open_section(1)
        self.area.expand_button.click()
        self.assertFalse(self.area.collapsed)
        self.assertIsNone(self.area.open_section_index)
        self.assertIs(self.second.window(), self.host)
        self.assertTrue(self.second.isVisibleTo(self.area))

    def test_toggle_top_area_finds_area_in_window(self) -> None:
        """창 안의 상단 영역을 찾아 전환하고, 상단 영역이 없으면 아무 일도 하지 않음을 검증"""
        toggle_top_area(self.host)
        self.assertTrue(self.area.collapsed)
        toggle_top_area(self.host)
        self.assertFalse(self.area.collapsed)
        toggle_top_area(QWidget())


class MainWindowTopAreaTests(GuiTestCase):
    def _make_window(self, tmpdir: str) -> MainWindow:
        dummy_state = os.path.join(tmpdir, "data", "config.json")
        dummy_root = os.path.join(tmpdir, ".rootdir.test")
        with (
            patch("src.core.app_state.get_state_path", return_value=dummy_state),
            patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
        ):
            win = MainWindow()
        self.addCleanup(win.close)
        return win

    def test_header_collapse_button_collapses_and_expand_button_restores(self) -> None:
        """헤더 접기 버튼은 펼친 상태에서만 보이고, 접힌 뒤 펼치기로 다시 펼쳐짐을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            win = self._make_window(tmpdir)
            top_area = win.findChild(CollapsibleTopArea)
            self.assertIsNotNone(top_area)
            if top_area is None:
                return
            self.assertFalse(win.header_bar.btn_collapse_top.isHidden())

            win.header_bar.btn_collapse_top.click()
            self.assertTrue(export_top_area_collapsed(win))
            self.assertTrue(win.header_bar.btn_collapse_top.isHidden())

            top_area.expand_button.click()
            self.assertFalse(export_top_area_collapsed(win))
            self.assertFalse(win.header_bar.btn_collapse_top.isHidden())

    def test_menu_shortcut_toggles_top_area_and_saves_state(self) -> None:
        """보기 메뉴의 Ctrl+F1 동작이 상단 영역을 접고 펼치며, 접힘 상태를 저장 값으로 내보냄을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            win = self._make_window(tmpdir)
            self.assertFalse(export_top_area_collapsed(win))
            self.assertEqual(win.menu.act_toggle_top_area.shortcut().toString(), "Ctrl+F1")

            win.menu.act_toggle_top_area.trigger()
            self.assertTrue(export_top_area_collapsed(win))
            self.assertFalse(win.header_bar.isVisibleTo(win))
            self.assertTrue(win.table_view.isVisibleTo(win))

            win.menu.act_toggle_top_area.trigger()
            self.assertFalse(export_top_area_collapsed(win))
            self.assertTrue(win.header_bar.isVisibleTo(win))

    def test_restore_applies_saved_collapsed_state(self) -> None:
        """저장된 접힘 상태를 복원하면 상단 영역이 접힌 상태로 열림을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            win = self._make_window(tmpdir)
            restore_window_ui_state(win, UiLayoutState(top_area_collapsed=True), win.table_view)
            self.assertTrue(export_top_area_collapsed(win))
            self.assertFalse(win.dashboard.isVisibleTo(win))
            restore_window_ui_state(win, UiLayoutState(top_area_collapsed=False), win.table_view)
            self.assertFalse(export_top_area_collapsed(win))


if __name__ == "__main__":
    unittest.main()
