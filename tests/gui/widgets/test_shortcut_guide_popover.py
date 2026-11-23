"""공용 단축키 안내 팝오버 검증: 항목 구성, 호버 열기, 고정, 바깥 닫힘, 최신 설정 반영"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QEnterEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.styles.palettes import LIGHT_PALETTE
from src.gui.widgets import shortcut_guide_popover as module
from src.gui.widgets.shortcut_guide_popover import (
    CUSTOM_BADGE_TEXT,
    GUIDE_TEXT,
    ShortcutEntry,
    ShortcutGuideButton,
    ShortcutGuidePopover,
    build_shortcut_entries,
)
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])
_ACTIONS = ("compare.zoom_in", "preview.frame_forward")


class EntryTests(unittest.TestCase):
    """표시 항목 구성"""

    def test_entries_keep_order_and_flag_custom_keys(self) -> None:
        shortcuts = dict(DEFAULT_SHORTCUTS)
        shortcuts["compare.zoom_in"] = "Z"
        entries = build_shortcut_entries(shortcuts, _ACTIONS)
        self.assertEqual([entry.key for entry in entries], ["Z", "."])
        self.assertTrue(entries[0].customized)
        self.assertFalse(entries[1].customized)
        self.assertEqual(entries[0].label, "확대")

    def test_entries_skip_missing_or_unbound_commands(self) -> None:
        shortcuts = dict(DEFAULT_SHORTCUTS)
        shortcuts["preview.frame_forward"] = ""
        entries = build_shortcut_entries(shortcuts, (*_ACTIONS, "unknown.action"))
        self.assertEqual([entry.label for entry in entries], ["확대"])


class PopoverTests(GuiTestCase):
    """팝오버 표시와 고정"""

    def setUp(self) -> None:
        self.host = QWidget()
        self.addCleanup(self.host.deleteLater)
        self.host.resize(600, 400)
        self.button = ShortcutGuideButton(
            self.host, lambda: DEFAULT_SHORTCUTS, _ACTIONS, title="화질 비교 창 단축키"
        )
        self.host.show()
        _APP.processEvents()
        self.popover = ShortcutGuidePopover(self.host)
        self.addCleanup(self.popover.deleteLater)

    def test_set_entries_builds_two_column_keycaps(self) -> None:
        entries = [ShortcutEntry("확대", "=", False), ShortcutEntry("다음 프레임", ".", True)]
        self.popover.set_entries("화질 비교 창 단축키", entries)
        self.assertEqual(self.popover._title.text(), "화질 비교 창 단축키")
        self.assertEqual(self.popover._grid.count(), 6)
        keys = self.popover.findChildren(QLabel, "ShortcutGuideKey")
        self.assertEqual([label.text() for label in keys], ["=", "."])
        self.assertEqual(
            [label.text() for label in self.popover.findChildren(QLabel, "ShortcutGuideBadge")],
            ["", CUSTOM_BADGE_TEXT],
        )
        self.assertTrue(keys[1].property("custom"))

    def test_show_for_opens_above_the_button_and_tracks_pinning(self) -> None:
        self.popover.set_entries("제목", ())
        self.popover.show_for(self.button)
        _APP.processEvents()
        self.assertTrue(self.popover.is_open())
        self.assertTrue(self.popover.contains_global(self.popover.frameGeometry().center()))
        self.popover.set_pinned(True)
        self.assertTrue(self.popover.pinned)
        self.popover.close()
        _APP.processEvents()
        self.assertFalse(self.popover.pinned)
        self.popover.apply_theme(LIGHT_PALETTE)
        self.assertIn("ShortcutGuideCard", self.popover.styleSheet())


class GuideButtonTests(GuiTestCase):
    """호버와 클릭으로 팝오버를 여는 버튼"""

    def setUp(self) -> None:
        self.host = QWidget()
        self.addCleanup(self.host.deleteLater)
        self.host.resize(600, 400)
        self.shortcuts = dict(DEFAULT_SHORTCUTS)
        self.button = ShortcutGuideButton(
            self.host, lambda: self.shortcuts, _ACTIONS, title="미리 보기 창 단축키"
        )
        self.host.show()
        _APP.processEvents()

    def _press(self) -> None:
        center = QPointF(self.button.width() / 2, self.button.height() / 2)
        global_pos = QPointF(self.button.mapToGlobal(self.button.rect().center()))
        QApplication.sendEvent(
            self.button,
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                center,
                global_pos,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )

    def test_enter_and_leave_schedule_the_timers(self) -> None:
        QApplication.sendEvent(
            self.button, QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1))
        )
        self.assertTrue(self.button._open_timer.isActive())
        QApplication.sendEvent(self.button, QEvent(QEvent.Type.Leave))
        self.assertFalse(self.button._open_timer.isActive())
        self.assertTrue(self.button._hide_timer.isActive())

    def test_open_guide_reads_the_latest_shortcuts(self) -> None:
        self.shortcuts["compare.zoom_in"] = "Z"
        self.button.open_guide()
        _APP.processEvents()
        self.assertTrue(self.button.is_guide_open())
        keys = self.button._ensure_popover().findChildren(QLabel, "ShortcutGuideKey")
        self.assertEqual(keys[0].text(), "Z")
        self.button.refresh_entries()
        self.assertTrue(self.button.is_guide_open())
        self.assertTrue(self.button.close_guide_if_open())
        self.assertFalse(self.button.close_guide_if_open())

    def test_click_toggles_pinned_guide(self) -> None:
        self._press()
        _APP.processEvents()
        self.assertTrue(self.button.is_guide_open())
        self.assertTrue(self.button._ensure_popover().pinned)
        self._press()
        _APP.processEvents()
        self.assertFalse(self.button.is_guide_open())

    def test_outside_mouse_closes_unpinned_guide(self) -> None:
        self.button.open_guide()
        _APP.processEvents()
        with (
            mock.patch.object(module.QCursor, "pos", return_value=QPoint(4000, 4000)),
            mock.patch.object(self.button, "underMouse", return_value=False),
        ):
            self.button.hide_if_outside()
        _APP.processEvents()
        self.assertFalse(self.button.is_guide_open())
        self.button.toggle_pinned()
        _APP.processEvents()
        with (
            mock.patch.object(module.QCursor, "pos", return_value=QPoint(4000, 4000)),
            mock.patch.object(self.button, "underMouse", return_value=False),
        ):
            self.button.hide_if_outside()
        self.assertTrue(self.button.is_guide_open())
        self.button.close_guide()

    def test_icon_refresh_and_accessible_name(self) -> None:
        self.button.refresh_icon()
        self.assertFalse(self.button.icon().isNull())
        self.assertEqual(self.button.accessibleName(), GUIDE_TEXT)
        self.assertEqual(self.button.toolTip(), GUIDE_TEXT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
