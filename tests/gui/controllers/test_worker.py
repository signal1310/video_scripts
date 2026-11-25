"""백그라운드 분석 워커의 실행 함수 위임, 취소 전파와 진행 처리 예외를 검증"""

import unittest
from unittest.mock import MagicMock

from src.core.messages import MSG_ANALYSIS_CANCELLED
from src.gui.controllers.worker import (
    ClassifierWorker,
    _execute_analyze_fast,
    _execute_analyze_full,
)
from src.service.analysis_probe import StoredRows
from tests._factories import make_report


class AnalyzeExecutorTests(unittest.TestCase):
    """분석 실행 함수가 판정 결과와 키프레임 모드를 세션 리포트 요청으로 넘기는지 검증"""

    def test_fast_executor_forwards_prescan_rows(self) -> None:
        """빠른 분석 실행이 키프레임 제외와 판정 결과를 그대로 넘기는지 검증"""
        stored = StoredRows((), frozenset(), False)
        clf = MagicMock()
        clf.get_summary_report.return_value = make_report([])

        outcome = _execute_analyze_fast(clf, stored)

        clf.include_keyframe_interval.assert_called_once_with(False)
        clf.get_summary_report.assert_called_once_with(ensure_keyframe=False, prescan=stored)
        self.assertIsNotNone(outcome.report)

    def test_full_executor_forwards_prescan_rows(self) -> None:
        """정밀 분석 실행이 지연 준비 키프레임 모드와 판정 결과를 그대로 넘기는지 검증"""
        stored = StoredRows((), frozenset(), False)
        clf = MagicMock()
        clf.get_summary_report.return_value = make_report([])

        outcome = _execute_analyze_full(clf, stored)

        clf.include_keyframe_interval.assert_called_once_with(True, eager_prepare=False)
        clf.get_summary_report.assert_called_once_with(ensure_keyframe=True, prescan=stored)
        self.assertIsNotNone(outcome.report)


class ClassifierWorkerTests(unittest.TestCase):
    """워커의 중단 플래그와 진행 처리 예외가 그대로 동작하는지 검증"""

    def _worker(self) -> ClassifierWorker:
        return ClassifierWorker(MagicMock(), ClassifierWorker.TASK_ANALYZE_FAST)

    def test_cancel_sets_the_flag_and_is_cancelled_reports_it(self) -> None:
        """취소 요청이 플래그를 세우고 그 상태를 알리는지 검증"""
        worker = self._worker()
        self.assertFalse(worker.is_cancelled())

        worker.cancel()

        self.assertTrue(worker.is_cancelled())

    def test_progress_after_cancel_stops_with_an_interrupted_error(self) -> None:
        """취소된 워커가 진행 보고를 받으면 중단 예외로 멈추는지 검증"""
        worker = self._worker()
        worker.cancel()

        with self.assertRaisesRegex(InterruptedError, MSG_ANALYSIS_CANCELLED):
            worker._handle_progress(1, 10, "clip.mp4")

    def test_progress_before_cancel_is_emitted(self) -> None:
        """취소 전 진행 보고는 신호로 내보내는지 검증"""
        worker = self._worker()
        seen: list[tuple[int, int, str]] = []
        worker.progress_updated.connect(lambda *args: seen.append(args))

        worker._handle_progress(2, 10, "clip.mp4")

        self.assertEqual(seen, [(2, 10, "clip.mp4")])

    def test_run_sets_callbacks_and_emits_the_report(self) -> None:
        """실행이 세션 콜백을 걸고 완료 결과를 신호로 넘기며 콜백을 되돌리는지 검증"""
        clf = MagicMock()
        report = make_report([])
        clf.get_summary_report.return_value = report
        worker = ClassifierWorker(clf, ClassifierWorker.TASK_ANALYZE_FAST)
        done: list[tuple[str, object]] = []
        worker.task_completed.connect(lambda task, rep: done.append((task, rep)))

        worker.run()

        self.assertEqual(done, [(ClassifierWorker.TASK_ANALYZE_FAST, report)])
        clf.set_callbacks.assert_any_call(
            on_progress=worker._handle_progress, on_is_cancelled=worker.is_cancelled
        )
        clf.set_callbacks.assert_any_call(on_progress=None, on_is_cancelled=None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
