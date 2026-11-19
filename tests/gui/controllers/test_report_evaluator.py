"""캐시 재평가 워커의 결과 적용과 창 종료 때 진행 중 작업 대기를 검증"""

import os
import threading
import time
import unittest
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.models import SummaryReport
from src.gui.controllers.report_evaluator import ReportEvaluator
from tests._factories import make_report

_APP = QApplication.instance() or QApplication([])
_WAIT_SEC: float = 5.0
_RELEASE_DELAY_SEC: float = 0.05


class ReportEvaluatorTests(unittest.TestCase):
    """재평가 결과 적용과 종료 대기 동작을 검증"""

    def setUp(self) -> None:
        self.evaluator = ReportEvaluator()
        self.addCleanup(self.evaluator.shutdown)

    def test_latest_result_is_delivered_on_the_gui_thread(self) -> None:
        """최신 요청의 결과가 이벤트 처리 뒤 콜백으로 전달되고 대기 상태가 풀리는지 검증"""
        delivered: list[SummaryReport] = []
        report = make_report([])
        self.evaluator.submit(lambda: report, delivered.append)
        self.assertTrue(self._wait_until(lambda: len(delivered) == 1))
        self.assertIs(delivered[0], report)
        self.assertFalse(self.evaluator.pending)

    def test_shutdown_waits_for_the_running_job_and_drops_its_result(self) -> None:
        """종료가 진행 중 작업을 끝까지 기다리고, 그 결과는 콜백으로 전달하지 않는지 검증

        작업이 DB 연결을 쥔 채 끝나기 전에 창이 닫히면 임시 폴더 정리가 파일 잠금에 막힘
        """
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        delivered: list[SummaryReport] = []

        def job() -> SummaryReport:
            entered.set()
            release.wait(_WAIT_SEC)
            finished.set()
            return make_report([])

        self.evaluator.submit(job, delivered.append)
        self.assertTrue(entered.wait(_WAIT_SEC))
        threading.Timer(_RELEASE_DELAY_SEC, release.set).start()
        self.evaluator.shutdown()
        self.assertTrue(finished.is_set())
        _APP.processEvents()
        self.assertEqual(delivered, [])
        self.assertFalse(self.evaluator.pending)

    def _wait_until(self, predicate: Callable[[], bool]) -> bool:
        """이벤트를 처리하며 조건이 참이 될 때까지 기다리고, 시간 안에 참이면 참을 돌려줌"""
        end = time.monotonic() + _WAIT_SEC
        while not predicate():
            if time.monotonic() > end:
                return False
            _APP.processEvents()
            time.sleep(0.01)
        return True


if __name__ == "__main__":
    unittest.main(verbosity=2)
