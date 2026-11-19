"""시작 시 이전 분석 결과 판정이 워커 스레드에서 수행되고, 결과와 실패가 GUI 스레드 시그널로 전달되는지 검증"""

import os
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.gui.controllers.restore_resolver import RestoreModeResolver
from src.service.analysis_probe import StoredRows
from tests._factories import make_video

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
        """판정이 워커 스레드에서 호출 시점 인자로 실행되고 표시용 결과가 시그널로 전달되는지 검증"""
        store = MagicMock()
        payload = StoredRows((make_video(filename="a.mp4", keyframe_interval=1.0),), frozenset(), True)
        calls: list[tuple[threading.Thread, dict[str, object]]] = []

        def fake_load(_store: object, root: str, **kwargs: object) -> StoredRows:
            calls.append((threading.current_thread(), {"root": root, **kwargs}))
            return payload

        with (
            patch("src.gui.controllers.restore_resolver.open_default_store", return_value=store),
            patch("src.gui.controllers.restore_resolver.load_stored_rows", side_effect=fake_load),
        ):
            resolver = RestoreModeResolver()
            results = self._start_and_wait(resolver)

        self.assertEqual(results, [payload])
        worker, kwargs = calls[0]
        self.assertIsNot(worker, threading.main_thread())
        self.assertEqual(kwargs["root"], "C:/work")
        self.assertIs(kwargs["recursive"], True)
        store.close.assert_called_once()

    def test_empty_result_is_emitted_as_none(self) -> None:
        """복원할 행이 없으면 결과 없음(None)을 전달해 복원을 생략하는지 검증"""
        store = MagicMock()
        with (
            patch("src.gui.controllers.restore_resolver.open_default_store", return_value=store),
            patch("src.gui.controllers.restore_resolver.load_stored_rows", return_value=StoredRows((), frozenset(), False)),
        ):
            results = self._start_and_wait(RestoreModeResolver())
        self.assertEqual(results, [None])

    def test_failure_emits_none_logs_error_and_closes_store(self) -> None:
        """판정 중 예외가 나면 복원 생략(None)을 전달하고 오류를 기록하며 DB 연결을 닫는지 검증"""
        store = MagicMock()
        with (
            patch("src.gui.controllers.restore_resolver.open_default_store", return_value=store),
            patch("src.gui.controllers.restore_resolver.load_stored_rows", side_effect=OSError("디스크 읽기 실패")),
            self.assertLogs("src.gui.controllers.restore_resolver", level="ERROR"),
        ):
            resolver = RestoreModeResolver()
            results = self._start_and_wait(resolver)

        self.assertEqual(results, [None])
        store.close.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
