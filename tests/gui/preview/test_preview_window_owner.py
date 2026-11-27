"""내장 미리 보기 창 소유자 추적(전환, 소멸 대비, 숨김 뒤 올리기) 검증

- 표시 상태, 전체화면, 최소화는 실제 창 대신 대역으로 바꿔 확인
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from src.gui.preview.preview_window_owner import PreviewOwnerTracker
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


class PreviewOwnerTrackerTests(GuiTestCase):
    """소유 창 전환과 소멸, 숨김 뒤 올리기 동작"""

    def _tracker(self) -> tuple[QWidget, list[bool], PreviewOwnerTracker]:
        window = QWidget(None, Qt.WindowType.Window)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.hide)
        switching: list[bool] = []
        return window, switching, PreviewOwnerTracker(window, switching.append)

    def test_set_owner_parents_the_window_and_tracks_the_owner(self) -> None:
        """소유자를 넘기면 부모가 바뀌고 기억되며, 같은 소유자를 다시 넘기면 부모를 바꾸지 않는지 검증"""
        window, switching, tracker = self._tracker()
        owner = QWidget(None)
        self.addCleanup(owner.deleteLater)
        tracker.set_owner(owner)
        self.assertIs(window.parentWidget(), owner)
        self.assertIs(tracker.owner, owner)
        self.assertEqual(switching, [True, False])
        with mock.patch.object(window, "setParent") as set_parent:
            tracker.set_owner(owner)
        set_parent.assert_not_called()

    def test_set_owner_restores_geometry_and_fullscreen_for_a_visible_window(self) -> None:
        """표시 중이던 창은 소유자 변경 뒤 이전 위치와 전체화면 상태로 다시 표시되는지 검증"""
        window, _switching, tracker = self._tracker()
        owner = QWidget(None)
        self.addCleanup(owner.deleteLater)
        window.show()
        _APP.processEvents()
        with (
            mock.patch.object(window, "isFullScreen", return_value=True),
            mock.patch.object(window, "showFullScreen") as show_full,
        ):
            tracker.set_owner(owner)
        show_full.assert_called_once()
        plain = QWidget(None)
        self.addCleanup(plain.deleteLater)
        window.show()
        _APP.processEvents()
        with (
            mock.patch.object(window, "isFullScreen", return_value=False),
            mock.patch.object(window, "setGeometry") as set_geometry,
        ):
            tracker.set_owner(plain)
        set_geometry.assert_called_once()

    def test_set_owner_restores_activation_for_an_active_window(self) -> None:
        """활성 창이던 미리 보기는 소유자 변경 뒤 포커스를 되찾는지 검증"""
        window, _switching, tracker = self._tracker()
        owner = QWidget(None)
        self.addCleanup(owner.deleteLater)
        window.show()
        _APP.processEvents()
        with (
            mock.patch.object(window, "isActiveWindow", return_value=True),
            mock.patch.object(window, "activateWindow") as activate,
        ):
            tracker.set_owner(owner)
        activate.assert_called_once()

    def test_hidden_window_keeps_its_visibility_state_on_owner_change(self) -> None:
        """숨긴 창은 소유자 변경 뒤에도 표시하지 않는지 검증"""
        window, _switching, tracker = self._tracker()
        owner = QWidget(None)
        self.addCleanup(owner.deleteLater)
        with mock.patch.object(window, "show") as show:
            tracker.set_owner(owner)
        show.assert_not_called()

    def test_deleted_owner_is_ignored(self) -> None:
        """이미 지워진 창을 소유자로 넘기면 부모를 바꾸지 않고 독립 창으로 두는지 검증"""
        window, _switching, tracker = self._tracker()
        owner = QWidget(None)
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(sip.isdeleted(owner))
        window.show()
        _APP.processEvents()
        tracker.set_owner(owner)
        self.assertIsNone(window.parentWidget())
        self.assertIsNone(tracker.owner)

    def test_destroyed_owner_detaches_the_preview_window(self) -> None:
        """소유 창이 지워져도 미리 보기 창이 남고 부모가 풀리는지 검증"""
        window, switching, tracker = self._tracker()
        owner = QWidget(None)
        tracker.set_owner(owner)
        window.show()
        _APP.processEvents()
        self.assertIs(window.parentWidget(), owner)
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _APP.processEvents()
        self.assertFalse(sip.isdeleted(window))
        self.assertIsNone(window.parentWidget())
        self.assertIsNone(tracker.owner)
        self.assertIn(True, switching)

    def test_destroyed_owner_skips_a_window_without_a_parent(self) -> None:
        """부모가 이미 없는 창에서는 소멸 처리로 부모를 다시 바꾸지 않는지 검증"""
        window, _switching, tracker = self._tracker()
        with mock.patch.object(window, "setParent") as set_parent:
            tracker._on_owner_destroyed()
        set_parent.assert_not_called()

    def test_raise_owner_skips_hidden_and_minimized_owners(self) -> None:
        """숨았거나 최소화한 소유 창은 올리지 않고, 보이는 소유 창만 올리는지 검증"""
        _window, _switching, tracker = self._tracker()
        owner = QWidget(None)
        self.addCleanup(owner.deleteLater)
        tracker.set_owner(owner)
        with (
            mock.patch.object(owner, "isVisible", return_value=False),
            mock.patch.object(owner, "raise_") as raise_hidden,
        ):
            tracker.raise_owner()
        raise_hidden.assert_not_called()
        with (
            mock.patch.object(owner, "isVisible", return_value=True),
            mock.patch.object(owner, "isMinimized", return_value=True),
            mock.patch.object(owner, "raise_") as raise_minimized,
        ):
            tracker.raise_owner()
        raise_minimized.assert_not_called()
        with (
            mock.patch.object(owner, "isVisible", return_value=True),
            mock.patch.object(owner, "isMinimized", return_value=False),
            mock.patch.object(owner, "raise_") as raise_visible,
        ):
            tracker.raise_owner()
        raise_visible.assert_called_once()

    def test_raise_owner_does_nothing_without_an_owner(self) -> None:
        """소유자가 없으면 아무 창도 올리지 않는지 검증"""
        _window, _switching, tracker = self._tracker()
        tracker.raise_owner()
        tracker.forget()
        self.assertIsNone(tracker.owner)

    def test_suppressed_raise_keeps_the_owner_in_place(self) -> None:
        """올리기를 끈 동안에는 보이는 소유 창도 올리지 않는지 검증"""
        _window, _switching, tracker = self._tracker()
        owner = QWidget(None)
        self.addCleanup(owner.deleteLater)
        tracker.set_owner(owner)
        with (
            mock.patch.object(owner, "isVisible", return_value=True),
            mock.patch.object(owner, "isMinimized", return_value=False),
            mock.patch.object(owner, "raise_") as raise_owner,
        ):
            tracker.set_raise_suppressed(True)
            tracker.raise_owner()
            tracker.set_raise_suppressed(False)
            tracker.raise_owner()
        raise_owner.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)

