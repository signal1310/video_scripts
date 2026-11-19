"""지연 호출이 소유 객체 수명을 따르는지 검증. 창을 지운 뒤 예약된 호출이 죽은 객체에 닿지 않아야 함"""

import os
import sys
import tempfile
import time
import unittest
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, QObject
from PyQt6.QtWidgets import QApplication

from src.gui.constants import call_later
from src.gui.main_window import _SETTINGS_PREWARM_DELAY_MS, MainWindow
from tests._gui_helpers import isolate_app_state

_APP = QApplication.instance() or QApplication([])
# - 가장 긴 지연 호출(설정 창 미리 만들기)이 실행될 만큼 충분히 기다림
_PUMP_MARGIN_SEC: float = 0.3
_SHORT_PUMP_SEC: float = 0.2


def _pump(seconds: float) -> None:
    """이벤트를 처리하며 지정 시간 동안 대기"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


class CallLaterTests(unittest.TestCase):
    """소유 객체가 살아 있을 때만 한 번 실행되고, 지워지면 실행되지 않는지 검증"""

    def test_callback_runs_once_while_owner_is_alive(self) -> None:
        """소유 객체가 살아 있으면 콜백이 정확히 한 번 호출되는지 검증"""
        owner = QObject()
        self.addCleanup(owner.deleteLater)
        calls: list[int] = []
        call_later(owner, 0, lambda: calls.append(1))
        _pump(_SHORT_PUMP_SEC)
        self.assertEqual(calls, [1])

    def test_callback_skipped_when_owner_is_deleted_before_delay(self) -> None:
        """지연 전에 소유 객체가 지워지면 콜백이 호출되지 않는지 검증"""
        owner = QObject()
        calls: list[int] = []
        call_later(owner, 50, lambda: calls.append(1))
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _pump(_SHORT_PUMP_SEC)
        self.assertEqual(calls, [])


class MainWindowDeferredCallTests(unittest.TestCase):
    """창을 만든 직후 지워도 예약된 지연 호출에서 슬롯 예외가 나지 않는지 검증"""

    def test_window_deleted_right_after_creation_raises_no_slot_error(self) -> None:
        """창 삭제 뒤 가장 긴 지연까지 이벤트를 처리해도 슬롯 예외가 없는지 검증"""
        state_root = tempfile.TemporaryDirectory()
        self.addCleanup(state_root.cleanup)
        isolate_app_state(self, state_root.name)
        # - 예외를 목록에 모아 실패로 드러냄. 기본 훅이면 PyQt6가 qFatal로 프로세스를 끝냄
        errors: list[BaseException] = []
        original_hook: Callable[..., object] = sys.excepthook

        def record_exception(_exc_type: type[BaseException], exc: BaseException, _tb: object) -> None:
            errors.append(exc)

        sys.excepthook = record_exception
        self.addCleanup(setattr, sys, "excepthook", original_hook)

        window = MainWindow()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _pump(_SETTINGS_PREWARM_DELAY_MS / 1000 + _PUMP_MARGIN_SEC)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
