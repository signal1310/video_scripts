"""화질 분석 배치 본문과 워커 스레드의 진행, 완료, 취소, 오류 처리 검증"""

import threading
import unittest
from typing import cast

from PyQt6.QtWidgets import QApplication

from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.gui.controllers.frame_analysis_events import (
    FrameAnalysisFinished,
    FrameAnalysisProgress,
    FrameAnalysisStageProgress,
    completion_status_text,
)
from src.gui.controllers.frame_analysis_worker import (
    FrameAnalysisWorker,
    run_frame_analysis_body,
)
from src.gui.controllers.frame_progress_buffer import frame_analysis_stage_text
from src.service.frame_analysis_service import FrameAnalysisRequest, FrameAnalysisService
from tests._frame_analysis_fakes import FakeFailedCropService, FakeFrameAnalysisService

_APP = QApplication.instance() or QApplication([])


def _request() -> FrameAnalysisRequest:
    """본문에 넘기는 요청 한 건"""
    return FrameAnalysisRequest(
        filename="a.mp4",
        path="a.mp4",
        width=1920,
        height=1080,
        duration_sec=20.0,
        filesize_bytes=100,
        mtime_ns=1,
    )


class WorkerBodyTests(unittest.TestCase):
    """QThread 밖 본문의 진행, 완료, 취소, 오류 처리를 검증"""

    def test_success_reports_progress_and_loads_cached_results(self) -> None:
        """정상 실행이 단계, 진행, 완료 이벤트를 차례로 보내는지 검증"""
        events: list[object] = []
        service = FakeFrameAnalysisService()
        run_frame_analysis_body(cast("FrameAnalysisService", service), [], threading.Event(), events.append)
        self.assertIsInstance(events[0], FrameAnalysisStageProgress)
        self.assertTrue(any(isinstance(event, FrameAnalysisProgress) for event in events))
        finished = events[-1]
        self.assertIsInstance(finished, FrameAnalysisFinished)
        assert isinstance(finished, FrameAnalysisFinished)
        self.assertEqual(set(finished.results), {"a.mp4"})
        self.assertFalse(finished.cancelled)

    def test_stage_text_matches_approved_wording(self) -> None:
        """하단 세부 단계 문구가 TERMINOLOGY 6.17절 표기와 같은지 검증"""
        self.assertEqual(frame_analysis_stage_text("scan", 3, 9), "여백 확인")
        self.assertEqual(frame_analysis_stage_text("ssim", 2, 9), "축소 후보 선별 (2/9)")
        self.assertEqual(frame_analysis_stage_text("vmaf", 1, 3), "VMAF 정밀 확인 (1/3)")
        self.assertEqual(frame_analysis_stage_text("unknown", 1, 3), "")

    def test_cancel_and_error_still_finish(self) -> None:
        """취소와 오류도 완료 이벤트로 끝나는지 검증"""
        cancel = threading.Event()
        events: list[object] = []
        run_frame_analysis_body(
            cast("FrameAnalysisService", FakeFrameAnalysisService("cancel")), [], cancel, events.append
        )
        finished = events[-1]
        assert isinstance(finished, FrameAnalysisFinished)
        self.assertTrue(finished.cancelled)
        error_events: list[object] = []
        run_frame_analysis_body(
            cast("FrameAnalysisService", FakeFrameAnalysisService("error")),
            [],
            threading.Event(),
            error_events.append,
        )
        error_finished = error_events[-1]
        assert isinstance(error_finished, FrameAnalysisFinished)
        self.assertIn("boom", error_finished.error)

    def test_exception_without_a_message_reports_its_class_name(self) -> None:
        """메시지 없는 예외도 오류로 분류되도록 예외 이름을 문구에 담는지 검증"""
        events: list[object] = []
        run_frame_analysis_body(
            cast("FrameAnalysisService", FakeFrameAnalysisService("empty_error")),
            [],
            threading.Event(),
            events.append,
        )
        finished = events[-1]
        assert isinstance(finished, FrameAnalysisFinished)
        self.assertEqual(finished.error, "RuntimeError")

    def test_cache_errors_still_finish_with_the_error_message(self) -> None:
        """캐시 조회나 결과 읽기가 실패해도 오류 문구를 담은 종료 이벤트를 보내는지 검증"""
        for behaviour, message in (("count_error", "cache locked"), ("load_error", "cache read failed")):
            events: list[object] = []
            run_frame_analysis_body(
                cast("FrameAnalysisService", FakeFrameAnalysisService(behaviour)),
                [],
                threading.Event(),
                events.append,
            )
            finished = events[-1]
            assert isinstance(finished, FrameAnalysisFinished)
            self.assertIn(message, finished.error)

    def test_body_reports_request_total_and_cache_reuse(self) -> None:
        """요청 수와 캐시 재사용 수를 실행 전에 재어 종료 이벤트에 담는지 검증"""
        events: list[object] = []
        service = FakeFrameAnalysisService(cached_count=1)
        run_frame_analysis_body(cast("FrameAnalysisService", service), [_request()], threading.Event(), events.append)
        finished = events[-1]
        assert isinstance(finished, FrameAnalysisFinished)
        self.assertEqual((finished.total, finished.cached_count), (1, 1))

    def test_crop_only_body_uses_the_crop_cache_and_reports_the_mode(self) -> None:
        """워커 본문이 감지 전용 여부를 서비스와 종료 이벤트에 그대로 넘기는지 검증"""
        events: list[object] = []
        service = FakeFrameAnalysisService(cached_count=1)
        run_frame_analysis_body(
            cast("FrameAnalysisService", service),
            [],
            threading.Event(),
            events.append,
            crop_only=True,
        )
        self.assertTrue(service.count_crop_only)
        self.assertTrue(service.crop_only)
        finished = events[-1]
        assert isinstance(finished, FrameAnalysisFinished)
        self.assertTrue(finished.crop_only)

    def test_progress_events_carry_each_finished_result(self) -> None:
        """워커 본문이 완료된 영상마다 그 영상의 결과를 담아 넘기는지 검증"""
        events: list[object] = []
        run_frame_analysis_body(
            cast("FrameAnalysisService", FakeFrameAnalysisService()), [], threading.Event(), events.append
        )
        progress = [event for event in events if isinstance(event, FrameAnalysisProgress)]
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0].filename, "a.mp4")
        self.assertIsInstance(progress[0].result, FrameAnalysisResult)

    def test_worker_emits_progress_and_completed_signals(self) -> None:
        """워커 스레드가 진행, 단계, 완료를 시그널로 넘기고 취소를 받는지 검증"""
        worker = FrameAnalysisWorker(cast("FrameAnalysisService", FakeFrameAnalysisService()), [], threading.Event())
        progress: list[tuple[int, int, str]] = []
        stages: list[tuple[str, int, int]] = []
        completed: list[object] = []
        worker.progress.connect(lambda c, t, n: progress.append((c, t, n)))
        worker.stage_progress.connect(lambda _c, _t, _n, stage, done, total: stages.append((stage, done, total)))
        worker.completed.connect(completed.append)
        worker.run()
        self.assertTrue(progress)
        self.assertEqual(stages, [("ssim", 2, 9)])
        self.assertEqual(len(completed), 1)
        worker.cancel()

    def test_crop_scan_body_finished_event_carries_failed_scans(self) -> None:
        """감지 전용 실행 종료 이벤트의 결과가 완료 상태 줄의 감지 실패 수로 이어지는지 검증"""
        events: list[object] = []
        service = FakeFailedCropService()
        run_frame_analysis_body(
            cast("FrameAnalysisService", service), [_request()], threading.Event(), events.append, crop_only=True
        )
        finished = events[-1]
        self.assertIsInstance(finished, FrameAnalysisFinished)
        assert isinstance(finished, FrameAnalysisFinished)
        self.assertEqual(
            completion_status_text(finished, label="여백 감지"),
            "여백 감지 완료: 1개 (감지 실패 1개)",
        )


if __name__ == "__main__":
    unittest.main()
