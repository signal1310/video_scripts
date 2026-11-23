"""공용 단축키 안내 팝오버 검증: 항목 구성, 호버 열기와 이탈 닫힘, 포커스 비탈취, 최신 설정 반영"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QEnterEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.widgets import shortcut_guide_popover as module
from src.gui.widgets.shortcut_guide_popover import (
    GUIDE_TEXT,
    POPOVER_GAP_PX,
    ShortcutEntry,
    ShortcutGuideButton,
    ShortcutGuidePopover,
    build_guide_qss,
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

    def test_qss_does_not_repeat_button_rules(self) -> None:
        """버튼은 팝오버 자식이 아니므로 팝오버 스타일시트에 버튼 규칙을 두지 않는지 검증"""
        qss = build_guide_qss(LIGHT_PALETTE)
        self.assertIn("ShortcutGuideCard", qss)
        self.assertNotIn("QToolButton#ShortcutGuideButton", qss)

    def test_qss_uses_the_dark_hud_tokens_in_both_themes(self) -> None:
        """팝오버가 라이트와 다크 모두에서 어두운 반투명 HUD 색을 쓰는지 검증"""
        for palette in (LIGHT_PALETTE, DARK_PALETTE):
            qss = build_guide_qss(palette)
            self.assertIn(f"background-color: {palette.media_hud_bg};", qss)
            self.assertIn(f"border: 1px solid {palette.media_track};", qss)
            self.assertIn(f"color: {palette.media_fg};", qss)
            self.assertIn(f"color: {palette.media_overlay_label};", qss)
            self.assertIn(f"background-color: {palette.media_keycap_bg};", qss)
            self.assertIn(f"border: 1px solid {palette.media_keycap_border};", qss)
            self.assertNotIn("ShortcutGuideBadge", qss)
            self.assertNotIn('ShortcutGuideKey[custom="true"]', qss)
            self.assertNotIn(palette.accent_blue, qss)


class PopoverTests(GuiTestCase):
    """팝오버 표시와 포커스 비탈취"""

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
        """라벨과 키캡만 2열로 배치하고 변경 배지 열을 두지 않는지 검증"""
        entries = [
            ShortcutEntry("확대", "=", False),
            ShortcutEntry("다음 프레임", ".", True),
            ShortcutEntry("재생", "K", False),
        ]
        self.popover.set_entries("화질 비교 창 단축키", entries)
        self.assertEqual(self.popover._title.text(), "화질 비교 창 단축키")
        self.assertEqual(self.popover._grid.count(), 6)
        keys = self.popover.findChildren(QLabel, "ShortcutGuideKey")
        self.assertEqual([label.text() for label in keys], ["=", ".", "K"])
        labels = self.popover.findChildren(QLabel, "ShortcutGuideLabel")
        self.assertEqual([label.text() for label in labels], ["확대", "다음 프레임", "재생"])
        self.assertIsNone(self.popover.findChild(QLabel, "ShortcutGuideBadge"))
        self.assertIsNone(keys[1].property("custom"))
        self.assertEqual(self.popover._grid.getItemPosition(1)[:2], (0, 1))
        self.assertEqual(self.popover._grid.getItemPosition(3)[:2], (0, 3))
        self.assertEqual(self.popover._grid.getItemPosition(5)[:2], (1, 1))

    def test_first_open_sits_above_the_button_with_the_gap(self) -> None:
        """최초 표시부터 크기를 확정해 버튼 위 POPOVER_GAP_PX 간격에 놓는지 검증"""
        self.host.move(100, 120)
        self.button.move(160, 50)
        entries = [
            ShortcutEntry(f"명령 {index}", f"F{index}", False) for index in range(6)
        ]
        self.popover.set_entries("제목", entries)
        self.popover.show_for(self.button)
        _APP.processEvents()
        button_top = self.button.mapToGlobal(QPoint(0, 0)).y()
        self.assertEqual(self.popover.y(), button_top - self.popover.height() - POPOVER_GAP_PX)
        self.assertLess(self.popover.geometry().bottom(), button_top)

    def test_popover_enter_stops_and_leave_restarts_the_hide_timer(self) -> None:
        """마우스가 팝오버 안으로 오면 닫힘을 멈추고, 밖으로 나가면 다시 세는지 검증"""
        self.button.open_guide()
        _APP.processEvents()
        popover = self.button._ensure_popover()
        self.button._hide_timer.start()
        QApplication.sendEvent(
            popover, QEnterEvent(QPointF(2, 2), QPointF(2, 2), QPointF(2, 2))
        )
        self.assertFalse(self.button._hide_timer.isActive())
        QApplication.sendEvent(popover, QEvent(QEvent.Type.Leave))
        self.assertTrue(self.button._hide_timer.isActive())

    def test_show_for_opens_above_the_button_without_accepting_focus(self) -> None:
        """호버 표시가 포커스를 받지 않는 창 속성으로 열리는지 검증"""
        self.popover.set_entries("제목", ())
        self.popover.show_for(self.button)
        _APP.processEvents()
        self.assertTrue(self.popover.is_open())
        self.assertTrue(self.popover.contains_global(self.popover.frameGeometry().center()))
        self.assertTrue(
            self.popover.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.assertTrue(
            self.popover.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        )
        self.assertFalse(self.popover.hasFocus())
        self.assertIsNone(self.popover.focusWidget())
        self.popover.apply_theme(LIGHT_PALETTE)
        self.assertIn("ShortcutGuideCard", self.popover.styleSheet())


class GuideButtonTests(GuiTestCase):
    """호버로 팝오버를 여는 버튼"""

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

    def test_click_does_not_open_or_pin_the_guide(self) -> None:
        """클릭해도 안내를 열거나 고정 상태를 만들지 않는지 검증"""
        self._press()
        _APP.processEvents()
        self.assertFalse(self.button.is_guide_open())
        self.assertFalse(hasattr(self.button._ensure_popover(), "pinned"))

    def test_click_on_hover_opened_guide_keeps_it_open_without_changing_state(self) -> None:
        """호버로 열린 안내를 클릭해도 닫히거나 상태가 바뀌지 않는지 검증"""
        self.host.move(100, 120)
        self.button.move(200, 120)
        _APP.processEvents()
        self.button.open_guide()
        _APP.processEvents()
        popover = self.button._ensure_popover()
        geometry = popover.geometry()
        self._press()
        _APP.processEvents()
        self.assertTrue(popover.is_open())
        self.assertEqual(popover.geometry(), geometry)
        self.assertFalse(hasattr(popover, "pinned"))

    def test_cursor_uses_the_default_arrow(self) -> None:
        self.assertEqual(self.button.cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_outside_mouse_closes_the_guide(self) -> None:
        self.button.open_guide()
        _APP.processEvents()
        with (
            mock.patch.object(module.QCursor, "pos", return_value=QPoint(4000, 4000)),
            mock.patch.object(self.button, "underMouse", return_value=False),
        ):
            self.button.hide_if_outside()
        _APP.processEvents()
        self.assertFalse(self.button.is_guide_open())

    def test_leaving_the_popover_restarts_the_outside_check_and_closes(self) -> None:
        """팝오버 밖으로 나가 닫힘 대기가 시작되면 바깥 확인 뒤 닫히는지 검증"""
        self.button.open_guide()
        _APP.processEvents()
        self.button._on_popover_leave()
        self.assertTrue(self.button._hide_timer.isActive())
        with (
            mock.patch.object(module.QCursor, "pos", return_value=QPoint(4000, 4000)),
            mock.patch.object(self.button, "underMouse", return_value=False),
        ):
            self.button.hide_if_outside()
        _APP.processEvents()
        self.assertFalse(self.button.is_guide_open())

    def test_icon_refresh_and_accessible_name(self) -> None:
        self.button.refresh_icon()
        self.assertFalse(self.button.icon().isNull())
        self.assertEqual(self.button.accessibleName(), GUIDE_TEXT)
        self.assertEqual(self.button.toolTip(), GUIDE_TEXT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
