"""재인코딩 워커 본문 검증: 정상 종료와 예외 종료의 큐 항목, 스레드 없이 워커 본문 실행"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.gui.controllers.encode_worker import BatchFinished, EncodeWorker, QueueItem, run_batch_body

BATCH_ID: int = 7


class _FakeRunner:
    def __init__(self, error: Exception | None = None) -> None:
        self._error: Exception | None = error
        self.batch_ids: list[int] = []

    def run(self, batch_id: int) -> None:
        self.batch_ids.append(batch_id)
        if self._error is not None:
            raise self._error


class RunBatchBodyTest(unittest.TestCase):
    def test_normal_run_sends_finished_without_error(self) -> None:
        """예외가 없으면 빈 오류 문자열로 종료 항목을 한 번 보냄"""
        runner = _FakeRunner()
        sent: list[QueueItem] = []

        run_batch_body(runner, BATCH_ID, sent.append)

        self.assertEqual(runner.batch_ids, [BATCH_ID])
        self.assertEqual(sent, [BatchFinished(BATCH_ID, "")])

    def test_exception_is_sent_as_error_text_and_logged(self) -> None:
        """실행 중 예외는 스레드 밖으로 던지지 않고 오류 문자열과 로그로 남김"""
        runner = _FakeRunner(RuntimeError("디스크 오류"))
        sent: list[QueueItem] = []

        with self.assertLogs("src.gui.controllers.encode_worker", level="ERROR"):
            run_batch_body(runner, BATCH_ID, sent.append)

        self.assertEqual(sent, [BatchFinished(BATCH_ID, "디스크 오류")])


class EncodeWorkerTest(unittest.TestCase):
    def test_worker_body_runs_in_run_method_and_reports_to_sink(self) -> None:
        """워커의 run은 스레드를 시작하지 않아도 본문과 같은 결과를 큐에 보냄"""
        runner = _FakeRunner()
        sent: list[QueueItem] = []
        worker = EncodeWorker(runner, BATCH_ID, sent.append)

        worker.run()

        self.assertEqual(runner.batch_ids, [BATCH_ID])
        self.assertEqual(sent, [BatchFinished(BATCH_ID, "")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
