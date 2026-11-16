"""상단 영역 접기, 탭 팝업 열기, 펼쳐 두기 전환, 메뉴 단축키와 접힘 상태 복원을 검증"""

import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from src.gui.main_window import MainWindow
from src.gui.widgets.collapsible_top_area import (
    TOP_AREA_PIN_NAME,
    TOP_AREA_PIN_TEXT,
    TOP_AREA_PIN_TOOLTIP,
    TOP_AREA_UNPIN_NAME,
    TOP_AREA_UNPIN_TEXT,
    TOP_AREA_UNPIN_TOOLTIP,
    CollapsibleTopArea,
    TopAreaSection,
    toggle_top_area,
)
from src.gui.window_layout import export_top_area_collapsed, restore_window_ui_state

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class CollapsibleTopAreaWidgetTests(unittest.TestCase):
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

    def test_collapse_hides_contents_and_keeps_tabs(self) -> None:
        """접으면 본문이 숨겨지고 탭 줄만 남으며 펼치면 다시 보임을 검증"""
        self.assertFalse(self.area.collapsed)
        self.area.set_collapsed(True)
        self.assertTrue(self.area.collapsed)
        self.assertFalse(self.first.isVisibleTo(self.area))
        self.assertFalse(self.second.isVisibleTo(self.area))
        self.assertTrue(self.area.pin_button.isVisibleTo(self.area))

        self.area.toggle()
        self.assertFalse(self.area.collapsed)
        self.assertTrue(self.first.isVisibleTo(self.area))

    def test_pin_button_text_follows_state(self) -> None:
        """펼쳐 두기 버튼의 문구, 툴팁, 접근성 이름과 체크 상태가 현재 상태를 따름을 검증"""
        pin = self.area.pin_button
        self.assertEqual(pin.text(), TOP_AREA_UNPIN_TEXT)
        self.assertEqual(pin.toolTip(), TOP_AREA_UNPIN_TOOLTIP)
        self.assertEqual(pin.accessibleName(), TOP_AREA_UNPIN_NAME)
        self.assertTrue(pin.isChecked())

        self.area.set_collapsed(True)
        self.assertEqual(pin.text(), TOP_AREA_PIN_TEXT)
        self.assertEqual(pin.toolTip(), TOP_AREA_PIN_TOOLTIP)
        self.assertEqual(pin.accessibleName(), TOP_AREA_PIN_NAME)
        self.assertFalse(pin.isChecked())

        pin.click()
        self.assertFalse(self.area.collapsed)
        self.assertEqual(pin.text(), TOP_AREA_UNPIN_TEXT)

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

    def test_pinning_while_popup_open_restores_section_in_body(self) -> None:
        """팝업이 열린 상태에서 펼쳐 두기를 켜면 팝업이 닫히고 모든 영역이 본문에 보임을 검증"""
        self.area.set_collapsed(True)
        self.area.open_section(1)
        self.area.pin_button.click()
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


class MainWindowTopAreaTests(unittest.TestCase):
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
            restore_window_ui_state(win, "", "", win.table_view, top_area_collapsed=True)
            self.assertTrue(export_top_area_collapsed(win))
            self.assertFalse(win.dashboard.isVisibleTo(win))
            restore_window_ui_state(win, "", "", win.table_view, top_area_collapsed=False)
            self.assertFalse(export_top_area_collapsed(win))


if __name__ == "__main__":
    unittest.main()
