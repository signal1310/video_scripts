"""화질 비교 프레임 추출 워커 검증: 스레드 밖 본문의 성공, 취소, 원인별 오류와 시그널 전달

- 추출기는 가짜로 바꿔 실제 미디어와 FFmpeg 없이 확인
"""

import os
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.analysis.frame_compare import FrameSource
from src.gui.dialogs.frame_compare_worker import (
    ExtractOutcome,
    FrameCompareWorker,
    run_extract_body,
)
from src.service.frame_compare_service import ExtractedFrames, FrameCompareFileMissing

_APP = QApplication.instance() or QApplication([])


class _Extractor:
    """호출을 기록하고 정해진 결과나 오류를 돌려주는 가짜 추출기"""

    def __init__(self) -> None:
        self.failure: Exception | None = None

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
        if self.failure is not None:
            raise self.failure
        return ExtractedFrames(original_png=b"a", compared_png=b"b")


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
