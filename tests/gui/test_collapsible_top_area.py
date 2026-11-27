"""상단 영역 접기, 탭 팝업 열기, 펼치기 전환, 메뉴 단축키와 접힘 상태 복원을 검증"""

import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from src.core.settings.ui_state import UiLayoutState
from src.gui.constants import (
    FOLDER_NAME_FONT_PT,
    FOLDER_NAME_PAD_X_PX,
    HEADER_PADDING_X_PX,
    HEADER_SPACING_PX,
    TOOLBAR_ICON_BTN_SIZE,
    make_path_font,
)
from src.gui.main_window import MainWindow
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.widgets.collapsible_top_area import (
    TOP_AREA_EXPAND_NAME,
    TOP_AREA_EXPAND_TOOLTIP,
    CollapsibleTopArea,
    TopAreaSection,
    toggle_top_area,
)
from src.gui.window_layout import (
    export_top_area_collapsed,
    format_top_area_summary,
    restore_window_ui_state,
)
from tests._gui_helpers import GuiTestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def _click(button: Qt.MouseButton) -> QMouseEvent:
    """위젯 가운데를 누르고 떼는 마우스 사건"""
    point = QPointF(4.0, 4.0)
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        point,
        point,
        button,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


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

    def test_expand_button_is_on_the_left_of_the_collapsed_line(self) -> None:
        """접힌 줄이 펼치기 버튼, 폴더 아이콘, 폴더 이름, 탭 순서이고 펼치기 버튼이 헤더 접기 버튼과 같은 왼쪽 끝인지 검증

        - 펼친 헤더의 접기 버튼도 왼쪽 끝이라 같은 쪽에 있어야 전환해도 클릭 위치가 같음
        """
        self.area.set_summary_text("clips")
        self.area.set_collapsed(True)
        self.area.resize(600, self.area.sizeHint().height())
        self.host.show()
        self.addCleanup(self.area.set_summary_text, "")
        QApplication.processEvents()
        expand = self.area.expand_button
        icon = self.area.folder_icon
        summary = self.area.summary_label
        first_tab = self.area.tab_button(0)
        self.assertEqual(expand.geometry().left(), HEADER_PADDING_X_PX)
        self.assertLess(expand.geometry().right(), icon.geometry().left())
        self.assertLess(icon.geometry().right(), summary.geometry().left())
        self.assertLess(summary.geometry().right(), first_tab.geometry().left())
        self.assertEqual(summary.text(), "clips")
        self.assertTrue(summary.isVisibleTo(self.area))
        self.assertTrue(icon.isVisibleTo(self.area))

    def test_summary_text_hidden_when_empty(self) -> None:
        """요약 문구가 비면 라벨을 숨겨 접힌 줄에 빈 자리를 남기지 않는지 검증"""
        self.area.set_collapsed(True)
        self.area.set_summary_text("clips")
        self.assertTrue(self.area.summary_label.isVisibleTo(self.area))
        self.area.set_summary_text("")
        self.assertFalse(self.area.summary_label.isVisibleTo(self.area))

    def test_collapsed_line_stays_one_row_tall(self) -> None:
        """접힌 줄 높이가 펼치기 버튼 한 줄로 유지되는지 검증"""
        self.area.set_summary_text("clips")
        self.area.set_collapsed(True)
        self.host.resize(600, 300)
        self.host.show()
        QApplication.processEvents()
        strip_height = self.area.tab_button(0).parentWidget()
        assert strip_height is not None
        self.assertEqual(strip_height.height(), TOOLBAR_ICON_BTN_SIZE)

    def test_summary_label_shrinks_with_an_ellipsis_in_a_narrow_window(self) -> None:
        """좁은 창에서 긴 폴더 이름이 말줄임으로 줄고 접힌 줄 높이는 그대로인지 검증"""
        long_name = "아주_긴_작업_폴더_이름_예시_스물다섯_글자_이상_길이"
        self.area.set_summary_text(long_name)
        self.area.set_collapsed(True)
        self.host.resize(420, 300)
        self.host.show()
        QApplication.processEvents()
        summary = self.area.summary_label
        full_width = summary.fontMetrics().horizontalAdvance(long_name)
        self.assertLess(summary.width(), full_width)
        self.assertEqual(summary.full_text, long_name)
        strip = self.area.tab_button(0).parentWidget()
        assert strip is not None
        self.assertEqual(strip.height(), TOOLBAR_ICON_BTN_SIZE)
        self.assertLess(summary.geometry().right(), self.area.tab_button(0).geometry().left())

    def test_collapsed_summary_matches_the_expanded_folder_name_style(self) -> None:
        """접힌 줄의 폴더 이름 글꼴과 아이콘 여백이 펼친 헤더의 폴더 이름과 같은지 검증"""
        self.area.set_summary_text("clips")
        self.area.set_collapsed(True)
        summary = self.area.summary_label
        self.assertEqual(summary.font().pointSize(), FOLDER_NAME_FONT_PT)
        self.assertTrue(summary.font().bold())
        self.assertEqual(summary.font().families(), make_path_font().families())
        margins = summary.contentsMargins()
        self.assertEqual((margins.left(), margins.right()), (FOLDER_NAME_PAD_X_PX,) * 2)
        icon = self.area.folder_icon
        self.area.resize(600, self.area.sizeHint().height())
        self.host.show()
        QApplication.processEvents()
        self.assertEqual(summary.geometry().left() - icon.geometry().right() - 1, HEADER_SPACING_PX)

    def test_left_click_on_the_summary_expands_and_right_click_does_not(self) -> None:
        """접힌 줄 폴더 이름을 좌클릭하면 펼쳐지고 우클릭은 아무 동작도 하지 않는지 검증"""
        self.area.set_summary_text("clips")
        self.area.set_collapsed(True)
        summary = self.area.summary_label
        QApplication.sendEvent(summary, _click(Qt.MouseButton.RightButton))
        self.assertTrue(self.area.collapsed)
        QApplication.sendEvent(summary, _click(Qt.MouseButton.LeftButton))
        self.assertFalse(self.area.collapsed)

    def test_summary_color_follows_the_theme(self) -> None:
        """요약 라벨 글자색이 활성 테마의 보조 글자 토큰으로 바뀌는지 검증"""
        self.area.set_summary_text("clips")
        self.area.apply_theme(DARK_PALETTE)
        self.assertEqual(self.area.summary_label.color, DARK_PALETTE.text_secondary)
        self.area.apply_theme(LIGHT_PALETTE)
        self.assertEqual(self.area.summary_label.color, LIGHT_PALETTE.text_secondary)

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
            patch("src.core.settings.app_state.get_state_path", return_value=dummy_state),
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

    def test_collapse_and_expand_buttons_share_the_same_window_position(self) -> None:
        """실제 메인 창에서 헤더 접기 버튼과 접힌 줄 펼치기 버튼이 창 기준 같은 좌표(가로, 세로)에 놓이는지 검증

        - 전환할 때 버튼이 움직이면 클릭 동선이 끊기므로 같은 x여야 함
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            win = self._make_window(tmpdir)
            win.resize(1100, 700)
            win.show()
            top_area = win.findChild(CollapsibleTopArea)
            assert top_area is not None
            QApplication.processEvents()
            collapse = win.header_bar.btn_collapse_top
            collapse_pos = collapse.mapTo(win, collapse.rect().topLeft())
            collapse.click()
            QApplication.processEvents()
            expand = top_area.expand_button
            self.assertEqual(expand.mapTo(win, expand.rect().topLeft()), collapse_pos)

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


class TopAreaSummaryTextTests(unittest.TestCase):
    """접힌 줄 요약 문구가 폴더 이름과 추가 폴더 수로 만들어지는지 검증"""

    def test_empty_path_has_no_summary(self) -> None:
        """폴더가 없으면 빈 문구를 돌려주는지 검증"""
        self.assertEqual(format_top_area_summary(""), "")

    def test_single_folder_shows_only_its_name(self) -> None:
        """작업 폴더가 하나면 그 폴더 이름만 남는지 검증"""
        summary = format_top_area_summary("C:/work/clips")
        self.assertEqual(summary, "clips")
        self.assertNotIn("C:", summary)

    def test_multiple_folders_append_the_extra_count(self) -> None:
        """작업 폴더가 여럿이면 첫 폴더 이름 뒤에 나머지 수가 붙는지 검증"""
        summary = format_top_area_summary("C:/work/clips;D:/other/second")
        self.assertEqual(summary, "clips 외 1개")


class MainWindowSummaryWireTests(GuiTestCase):
    """메인 창이 접힌 줄 요약을 현재 작업 폴더로 채우는지 검증"""

    def test_window_fills_the_summary_from_the_current_folder(self) -> None:
        """창을 만들면 접힌 줄 요약이 현재 작업 폴더 이름으로 채워지는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_state = os.path.join(tmpdir, "data", "config.json")
            dummy_root = os.path.join(tmpdir, ".rootdir.test")
            work = os.path.join(tmpdir, "clips")
            os.makedirs(work, exist_ok=True)
            with (
                patch("src.core.settings.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                win = MainWindow()
                self.addCleanup(win.close)
                top_area = win.findChild(CollapsibleTopArea)
                self.assertIsNotNone(top_area)
                assert top_area is not None
                self.assertEqual(top_area.summary_label.text(), format_top_area_summary(win.header_bar.committed_path))
                win.header_bar.set_path(work)
                self.assertEqual(top_area.summary_label.text(), "clips")


if __name__ == "__main__":
    unittest.main()
