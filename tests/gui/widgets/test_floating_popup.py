"""공용 부동 팝업 기반 검증: 창 플래그, 카드 구성, 화면 안 보정, 표시 중 이벤트 처리, 숨김 정리"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QShowEvent
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from src.gui.widgets import floating_popup as module
from src.gui.widgets.analyze_mode_popup import FRAME_MODE, AnalyzeModePopup
from src.gui.widgets.floating_popup import (
    FLOATING_POPUP_SCREEN_MARGIN,
    FloatingPopupFrame,
    inherit_stays_on_top,
)
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])
_WINDOW_FLAGS = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint


class InheritStaysOnTopTests(unittest.TestCase):
    """부모 창의 항상 맨 위 플래그를 팝업 플래그에 이어받는지 검증"""

    def test_without_a_parent_the_flags_are_unchanged(self) -> None:
        self.assertEqual(inherit_stays_on_top(None, _WINDOW_FLAGS), _WINDOW_FLAGS)

    def test_parent_always_on_top_is_inherited(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        parent.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        flags = inherit_stays_on_top(parent, _WINDOW_FLAGS)
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnTopHint)

    def test_parent_without_always_on_top_keeps_the_flags(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        self.assertEqual(inherit_stays_on_top(parent, _WINDOW_FLAGS), _WINDOW_FLAGS)


class _TrackingPopup(FloatingPopupFrame):
    """숨김 훅 호출을 기록하는 팝업"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.hidden_count: int = 0

    def _on_hidden(self) -> None:
        self.hidden_count += 1


class _GrowingPopup(FloatingPopupFrame):
    """첫 표시 전에는 크기 힌트가 작았다가 표시 뒤 커지는 줄바꿈 라벨을 담은 팝업"""

    _LONG_TEXT = (
        "표시 뒤 폴리시가 끝나면 줄바꿈 라벨의 높이가 늘어나는 상황을 흉내 내는 긴 문구입니다. "
        "두 줄 이상으로 접혀 팝업 높이가 커집니다."
    )

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        card, body = self._build_card("GrowingCard", 6)
        self.label = _ShowDependentLabel("짧은 문구", card)
        self.label.setWordWrap(True)
        self.label.setFixedWidth(180)
        body.addWidget(self.label)


class _PostedLayoutPopup(FloatingPopupFrame):
    """표시 직후 이벤트 루프로 넘어가는 레이아웃 요청으로 크기 힌트가 커지는 팝업"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        card, body = self._build_card("PostedCard", 6)
        self.label = QLabel("짧은 문구", card)
        self.label.setWordWrap(False)
        body.addWidget(self.label)

    def showEvent(self, a0: QShowEvent | None) -> None:
        super().showEvent(a0)
        self.label.setText("표시 직후 예약된 레이아웃 요청으로 폭이 넓어지는 아주 긴 한 줄 문구입니다 " * 2)


class _ShowDependentLabel(QLabel):
    """표시 전에는 늘어난 높이를 힌트에 넣지 않는 라벨. 표시 뒤 힌트가 커짐"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._expanded: bool = False

    def showEvent(self, a0: QShowEvent | None) -> None:
        """첫 표시 뒤 긴 문구로 바꾸고 크기 힌트를 다시 계산하게 함"""
        if not self._expanded:
            self._expanded = True
            self.setText(_GrowingPopup._LONG_TEXT)
            self.updateGeometry()
        super().showEvent(a0)


class FloatingPopupFrameTests(GuiTestCase):
    """부동 팝업의 구성, 표시 위치 보정, 이벤트 필터 처리를 검증"""

    def setUp(self) -> None:
        self.host = QWidget()
        self.host.resize(500, 400)
        self.host.show()
        self.addCleanup(self.host.deleteLater)
        self.popup = FloatingPopupFrame(self.host)
        self.popup.resize(120, 80)
        self.addCleanup(self.popup.deleteLater)
        _APP.processEvents()

    def _show(self) -> None:
        self.popup.show()
        _APP.processEvents()

    def _mouse_press(self, global_pos: QPoint) -> QMouseEvent:
        return QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(1, 1),
            QPointF(global_pos),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

    def test_constructor_uses_a_frameless_translucent_tool_window(self) -> None:
        flags = self.popup.windowFlags()
        self.assertTrue(flags & Qt.WindowType.Tool)
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.NoDropShadowWindowHint)
        self.assertTrue(self.popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))

    def test_build_card_applies_object_name_padding_and_styled_background(self) -> None:
        card, body = self.popup._build_card("TestCard", 7)
        self.assertEqual(card.objectName(), "TestCard")
        self.assertTrue(card.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
        margins = body.contentsMargins()
        self.assertEqual(
            (margins.left(), margins.top(), margins.right(), margins.bottom()), (7, 7, 7, 7)
        )

    def test_set_toggle_rect_stores_a_copy(self) -> None:
        rect = QRect(QPoint(10, 20), QSize(30, 40))
        self.popup.set_toggle_rect(rect)
        rect.moveTo(0, 0)
        self.assertEqual(self.popup._toggle_rect_global, QRect(QPoint(10, 20), QSize(30, 40)))

    def test_show_clamped_at_keeps_the_popup_inside_the_available_screen(self) -> None:
        screen = mock.Mock()
        screen.availableGeometry.return_value = QRect(0, 0, 800, 600)
        with (
            mock.patch.object(module.QApplication, "screenAt", return_value=None),
            mock.patch.object(module.QApplication, "primaryScreen", return_value=screen),
            mock.patch.object(self.popup, "adjustSize"),
        ):
            self.popup.show_clamped_at(QPoint(790, 590))
        self.assertEqual(
            self.popup.pos(),
            QPoint(
                800 - 1 - 120 - FLOATING_POPUP_SCREEN_MARGIN,
                600 - 1 - 80 - FLOATING_POPUP_SCREEN_MARGIN,
            ),
        )
        self.popup.hide()
        with (
            mock.patch.object(module.QApplication, "screenAt", return_value=None),
            mock.patch.object(module.QApplication, "primaryScreen", return_value=screen),
            mock.patch.object(self.popup, "adjustSize"),
        ):
            self.popup.show_clamped_at(QPoint(-50, -50))
        self.assertEqual(
            self.popup.pos(),
            QPoint(FLOATING_POPUP_SCREEN_MARGIN, FLOATING_POPUP_SCREEN_MARGIN),
        )

    def test_show_clamped_at_fits_the_size_hint_of_a_freshly_built_popup(self) -> None:
        """새로 만든 팝업을 한 번만 열어도 표시 뒤 크기가 크기 힌트와 같음(첫 열림 잘림 방지) 검증"""
        popup = _GrowingPopup(self.host)
        self.addCleanup(popup.deleteLater)
        popup.show_clamped_at(QPoint(20, 20))
        try:
            self.assertTrue(popup.isVisible())
            self.assertEqual(popup.size(), popup.sizeHint())
        finally:
            popup.close()

    def test_show_clamped_at_delivers_posted_layout_requests_before_the_second_fit(self) -> None:
        """표시 직후 예약된 레이아웃 요청을 두 번째 크기 맞춤 전에 처리하는지 검증(실제 화면 첫 열림 폭 어긋남 방지)"""
        popup = _PostedLayoutPopup(self.host)
        self.addCleanup(popup.deleteLater)
        calls: list[str] = []
        real_fit = popup._fit_and_place
        with (
            mock.patch.object(popup, "_fit_and_place", side_effect=lambda pos: (calls.append("fit"), real_fit(pos))),
            mock.patch.object(
                module.QApplication,
                "sendPostedEvents",
                side_effect=lambda receiver, event_type: calls.append(f"posted:{event_type}"),
            ),
        ):
            popup.show_clamped_at(QPoint(20, 20))
        try:
            self.assertEqual(calls, ["fit", f"posted:{int(QEvent.Type.LayoutRequest)}", "fit"])
        finally:
            popup.close()

    def test_first_open_analyze_popup_matches_its_size_hint(self) -> None:
        """앱 시작 뒤 처음 연 분석 모드 팝업도 표시 직후 크기 힌트와 같음을 검증"""
        popup = AnalyzeModePopup(self.host)
        self.addCleanup(popup.deleteLater)
        popup.set_estimates(
            {"fast": "12개, 약 4초", "full": "12개, 약 40초", "frame": "8개, 약 1분"}
        )
        popup.set_current_mode(FRAME_MODE)
        popup.show_clamped_at(QPoint(20, 20))
        try:
            self.assertTrue(popup.isVisible())
            self.assertEqual(popup.size(), popup.sizeHint())
        finally:
            popup.close()

    def test_show_installs_the_app_filter_and_hide_removes_it(self) -> None:
        self._show()
        self.assertTrue(self.popup._app_filter_installed)
        self.popup.hide()
        _APP.processEvents()
        self.assertFalse(self.popup._app_filter_installed)

    def test_hide_calls_the_on_hidden_hook(self) -> None:
        popup = _TrackingPopup(self.host)
        self.addCleanup(popup.deleteLater)
        popup.show()
        _APP.processEvents()
        popup.hide()
        _APP.processEvents()
        self.assertEqual(popup.hidden_count, 1)

    def test_has_active_child_popup_only_counts_descendants(self) -> None:
        child = QWidget(self.popup)
        self.addCleanup(child.deleteLater)
        other = QWidget()
        self.addCleanup(other.deleteLater)
        with mock.patch.object(module.QApplication, "activePopupWidget", return_value=child):
            self.assertTrue(self.popup._has_active_child_popup())
        with mock.patch.object(module.QApplication, "activePopupWidget", return_value=other):
            self.assertFalse(self.popup._has_active_child_popup())
        with mock.patch.object(module.QApplication, "activePopupWidget", return_value=None):
            self.assertFalse(self.popup._has_active_child_popup())

    def test_event_filter_ignores_missing_events_and_hidden_popups(self) -> None:
        self.assertFalse(self.popup.eventFilter(None, None))
        self.assertFalse(self.popup.eventFilter(self.host, QEvent(QEvent.Type.KeyPress)))

    def test_application_deactivate_closes_without_consuming(self) -> None:
        self._show()
        self.assertFalse(
            self.popup.eventFilter(self.host, QEvent(QEvent.Type.ApplicationDeactivate))
        )
        self.assertFalse(self.popup.isVisible())

    def test_escape_closes_when_no_child_popup_is_active(self) -> None:
        self._show()
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        self.assertTrue(self.popup.eventFilter(self.host, event))
        self.assertFalse(self.popup.isVisible())

    def test_escape_is_ignored_while_a_child_popup_is_active(self) -> None:
        self._show()
        child = QWidget(self.popup)
        self.addCleanup(child.deleteLater)
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        with mock.patch.object(module.QApplication, "activePopupWidget", return_value=child):
            self.assertFalse(self.popup.eventFilter(self.host, event))
        self.assertTrue(self.popup.isVisible())

    def test_other_keys_and_events_are_not_consumed(self) -> None:
        self._show()
        key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)
        self.assertFalse(self.popup.eventFilter(self.host, key))
        self.assertFalse(self.popup.eventFilter(self.host, QEvent(QEvent.Type.HoverMove)))
        self.assertTrue(self.popup.isVisible())

    def test_press_inside_the_popup_is_not_consumed(self) -> None:
        self._show()
        event = self._mouse_press(self.popup.mapToGlobal(self.popup.rect().center()))
        self.assertFalse(self.popup.eventFilter(self.host, event))
        self.assertTrue(self.popup.isVisible())

    def test_press_on_a_descendant_widget_is_not_consumed(self) -> None:
        self._show()
        child = QWidget(self.popup)
        self.addCleanup(child.deleteLater)
        with mock.patch.object(module.QApplication, "widgetAt", return_value=child):
            self.assertFalse(
                self.popup.eventFilter(self.host, self._mouse_press(QPoint(4000, 4000)))
            )
        self.assertTrue(self.popup.isVisible())

    def test_press_on_an_active_child_popup_is_not_consumed(self) -> None:
        self._show()
        child = QWidget(self.popup)
        self.addCleanup(child.deleteLater)
        with mock.patch.object(module.QApplication, "activePopupWidget", return_value=child):
            self.assertFalse(
                self.popup.eventFilter(self.host, self._mouse_press(QPoint(4000, 4000)))
            )
        self.assertTrue(self.popup.isVisible())

    def test_press_on_the_toggle_rect_closes_and_is_consumed(self) -> None:
        self._show()
        self.popup.set_toggle_rect(QRect(QPoint(100, 100), QSize(40, 20)))
        with mock.patch.object(module.QApplication, "widgetAt", return_value=None):
            consumed = self.popup.eventFilter(self.host, self._mouse_press(QPoint(110, 110)))
        self.assertTrue(consumed)
        self.assertFalse(self.popup.isVisible())

    def test_press_outside_closes_without_consuming(self) -> None:
        self._show()
        with mock.patch.object(module.QApplication, "widgetAt", return_value=None):
            consumed = self.popup.eventFilter(self.host, self._mouse_press(QPoint(4000, 4000)))
        self.assertFalse(consumed)
        self.assertFalse(self.popup.isVisible())


if __name__ == "__main__":
    unittest.main(verbosity=2)
