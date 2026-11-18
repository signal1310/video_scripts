"""시작 시 복원 모드 판정이 워커 스레드에서 수행되고, 결과와 실패가 GUI 스레드 시그널로 전달되는지 검증"""

import os
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.gui.restore_resolver import RestoreModeResolver

_APP = QApplication.instance() or QApplication([])
_WAIT_SECONDS: float = 5.0


class RestoreModeResolverTests(unittest.TestCase):
    """판정 결과 전달, 워커 스레드 실행, 실패 시 복원 생략과 DB 연결 해제를 검증"""

    def _start_and_wait(self, resolver: RestoreModeResolver) -> list[object]:
        results: list[object] = []
        resolver.resolved.connect(results.append)
        resolver.start("C:/work", recursive=True)
        deadline = time.monotonic() + _WAIT_SECONDS
        while not results and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        return results

    def test_result_is_emitted_from_worker_thread_with_call_arguments(self) -> None:
        """판정이 워커 스레드에서 호출 시점 인자로 실행되고 결과가 시그널로 전달되는지 검증"""
        store = MagicMock()
        calls: list[tuple[threading.Thread, dict[str, object]]] = []

        def fake_resolve(_store: object, root: str, **kwargs: object) -> bool:
            calls.append((threading.current_thread(), {"root": root, **kwargs}))
            return True

        with (
            patch("src.gui.restore_resolver.open_default_store", return_value=store),
            patch("src.gui.restore_resolver.resolve_restore_keyframe_mode", side_effect=fake_resolve),
        ):
            resolver = RestoreModeResolver()
            results = self._start_and_wait(resolver)

        self.assertEqual(results, [True])
        worker, kwargs = calls[0]
        self.assertIsNot(worker, threading.main_thread())
        self.assertEqual(
            kwargs,
            {
                "root": "C:/work",
                "recursive": True,
            },
        )
        store.close.assert_called_once()

    def test_failure_emits_none_logs_error_and_closes_store(self) -> None:
        """판정 중 예외가 나면 복원 생략(None)을 전달하고 오류를 기록하며 DB 연결을 닫는지 검증"""
        store = MagicMock()
        with (
            patch("src.gui.restore_resolver.open_default_store", return_value=store),
            patch(
                "src.gui.restore_resolver.resolve_restore_keyframe_mode",
                side_effect=OSError("디스크 읽기 실패"),
            ),
            self.assertLogs("src.gui.restore_resolver", level="ERROR"),
        ):
            resolver = RestoreModeResolver()
            results = self._start_and_wait(resolver)

        self.assertEqual(results, [None])
        store.close.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
