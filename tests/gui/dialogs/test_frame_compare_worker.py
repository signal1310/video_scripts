"""화질 비교 프레임 추출 워커 검증: 본문의 성공, 취소, 원인별 오류와 요청 조율

- 추출기는 가짜로 바꿔 실제 미디어와 FFmpeg 없이 확인
"""

import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.analysis.frame_compare import FrameSource
from src.gui.dialogs.frame_compare_worker import (
    ExtractionRequest,
    ExtractOutcome,
    FrameCompareExtraction,
    FrameCompareWorker,
    run_extract_body,
)
from src.service.frame_compare_service import ExtractedFrames, FrameCompareFileMissing

_APP = QApplication.instance() or QApplication([])
_WAIT_SECONDS: float = 5.0


class _Extractor:
    """호출을 기록하고 정해진 결과나 오류를 돌려주는 가짜 추출기"""

    def __init__(self) -> None:
        self.failure: Exception | None = None
        self.calls: list[float] = []

    def extract_pair(
        self,
        original: FrameSource,
        compared: FrameSource,
        *,
        time_sec: float,
        frame_width: int,
        frame_height: int,
        cancel_event: threading.Event,
    ) -> ExtractedFrames:
        self.calls.append(time_sec)
        if self.failure is not None:
            raise self.failure
        return ExtractedFrames(original_bytes=b"a", compared_bytes=b"b")


def _request(time_sec: float, *, key: str = "result", immediate: bool = True) -> ExtractionRequest:
    return ExtractionRequest(
        original=FrameSource(key="원본", path="a.mp4", label="원본"),
        compared=FrameSource(key=key, path="b.mp4", label="결과"),
        frame_width=100,
        frame_height=50,
        time_sec=time_sec,
        target_key=key,
        immediate=immediate,
    )


class ExtractBodyTests(unittest.TestCase):
    """스레드 밖 추출 본문의 성공, 취소, 원인별 오류"""

    def setUp(self) -> None:
        self.extractor = _Extractor()
        self.original = FrameSource(key="원본", path="a.mp4", label="원본")
        self.compared = FrameSource(key="result", path="b.mp4", label="결과")
        self.events: list[ExtractOutcome] = []

    def _run(self, event: threading.Event) -> None:
        run_extract_body(
            self.extractor,
            ExtractOutcome(3, None, time_sec=2.0),
            self.original,
            self.compared,
            100,
            50,
            event,
            self.events.append,
        )

    def test_success_reports_frames_and_time(self) -> None:
        self._run(threading.Event())
        self.assertEqual(len(self.events), 1)
        self.assertIsNotNone(self.events[0].frames)
        self.assertEqual(self.events[0].time_sec, 2.0)

    def test_interrupted_body_sends_nothing(self) -> None:
        self.extractor.failure = InterruptedError("취소")
        self._run(threading.Event())
        self.assertEqual(self.events, [])

    def test_file_missing_and_generic_errors_are_distinguished(self) -> None:
        self.extractor.failure = FrameCompareFileMissing("파일 없음")
        self._run(threading.Event())
        self.assertTrue(self.events[0].file_missing)
        self.assertEqual(self.events[0].error, "파일 없음")
        self.extractor.failure = RuntimeError("boom")
        self._run(threading.Event())
        self.assertFalse(self.events[1].file_missing)
        self.assertEqual(self.events[1].error, "boom")


class WorkerTests(unittest.TestCase):
    """워커 본문을 직접 실행해 시그널로 결과를 넘기는지"""

    def test_run_emits_the_extraction_outcome(self) -> None:
        original = FrameSource(key="원본", path="a.mp4", label="원본")
        compared = FrameSource(key="result", path="b.mp4", label="결과")
        worker = FrameCompareWorker(
            _Extractor(),
            ExtractOutcome(7, None, time_sec=1.5),
            original,
            compared,
            10,
            20,
            threading.Event(),
        )
        events: list[ExtractOutcome] = []
        worker.completed.connect(events.append)
        worker.run()
        _APP.processEvents()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].request_id, 7)
        self.assertEqual(events[0].time_sec, 1.5)


class ExtractionRunnerTests(unittest.TestCase):
    """요청 조율: 즉시 실행, 디바운스, 같은 시점 건너뜀, 취소, 지연 결과 폐기"""

    def setUp(self) -> None:
        self.extractor = _Extractor()
        self.runner = FrameCompareExtraction(self.extractor)
        self.addCleanup(self.runner.deleteLater)
        self.addCleanup(self.runner.cancel)
        self.loaded: list[ExtractOutcome] = []
        self.failures: list[ExtractOutcome] = []
        self.starts: list[bool] = []
        self.runner.loaded.connect(self.loaded.append)
        self.runner.failed.connect(self.failures.append)
        self.runner.started.connect(lambda: self.starts.append(True))

    def _wait(self) -> None:
        deadline = time.monotonic() + _WAIT_SECONDS
        while self.runner.busy and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        _APP.processEvents()

    def test_immediate_request_runs_and_reports_loaded_frames(self) -> None:
        self.assertTrue(self.runner.request(_request(1.0)))
        self._wait()
        self.assertEqual(len(self.loaded), 1)
        self.assertEqual(self.starts, [True])
        self.assertFalse(self.runner.busy)

    def test_same_target_and_time_is_not_requested_again(self) -> None:
        self.runner.request(_request(1.0))
        self._wait()
        self.assertFalse(self.runner.request(_request(1.0)))
        self.assertTrue(self.runner.request(_request(2.0)))
        self._wait()
        self.assertEqual(self.extractor.calls, [1.0, 2.0])

    def test_debounced_request_runs_after_the_timer(self) -> None:
        self.assertTrue(self.runner.request(_request(3.0, immediate=False)))
        self.assertFalse(self.runner.busy)
        deadline = time.monotonic() + _WAIT_SECONDS
        while not self.extractor.calls and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        self._wait()
        self.assertEqual(self.extractor.calls, [3.0])
        self.assertEqual(len(self.loaded), 1)

    def test_failure_is_reported_without_marking_loaded(self) -> None:
        self.extractor.failure = FrameCompareFileMissing("파일 없음")
        self.runner.request(_request(1.0))
        self._wait()
        self.assertEqual(self.failures[0].error, "파일 없음")
        self.assertTrue(self.runner.request(_request(1.0)))

    def test_cancel_before_start_drops_the_pending_request(self) -> None:
        self.runner.request(_request(4.0, immediate=False))
        self.runner.cancel()
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        self.assertEqual(self.extractor.calls, [])
        self.assertFalse(self.runner.busy)

    def test_stale_outcome_is_discarded(self) -> None:
        self.runner._request_id = 5
        self.runner._active_key = "result"
        self.runner._active_time = 1.0
        stale = ExtractOutcome(
            4,
            ExtractedFrames(original_bytes=b"a", compared_bytes=b"b"),
            time_sec=1.0,
        )
        self.runner._on_completed(stale)
        self.assertEqual(self.loaded, [])
        self.assertEqual(self.failures, [])



if __name__ == "__main__":
    unittest.main(verbosity=2)
