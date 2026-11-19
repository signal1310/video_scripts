"""재인코딩 컨트롤러 검증: 배치 시작과 대상 없음, 워커 실행과 큐 배출, 제어 전달, 중복 시작 거부, 예외 종료"""

import os
import tempfile
import threading
import time
import unittest
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.encode.encode_state import EncodeState
from src.core.models import VideoEvaluation
from src.gui.controllers.encode_controller import EncodeController, make_encode_runner
from src.gui.controllers.encode_worker import BatchFinished, QueueSink
from src.service.encode.encode_events import EVENT_STATE, EncodeEvent
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_job import EncodeSettings
from src.service.encode.encode_service import EncodeRunner
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_WAIT_SEC: float = 5.0
_SOURCE_NAME: str = "a.mp4"


def _wait(predicate: Callable[[], bool], timeout_sec: float = _WAIT_SEC) -> bool:
    """신호가 전달되도록 이벤트를 처리하며 조건이 참이 될 때까지 기다림"""
    end = time.monotonic() + timeout_sec
    while not predicate():
        if time.monotonic() > end:
            return False
        _APP.processEvents()
        time.sleep(0.01)
    return True


class _FakeRunner:
    """실제 인코딩 없이 상태 이벤트를 보내고, 제어 호출을 기록하는 실행기"""

    def __init__(self, sink: QueueSink, *, gate: threading.Event | None = None, error: str | None = None) -> None:
        self._sink: QueueSink = sink
        self._gate: threading.Event | None = gate
        self._error: str | None = error
        self.calls: list[str] = []

    def run(self, batch_id: int) -> None:
        if self._gate is not None:
            self._gate.wait(_WAIT_SEC)
        self._sink(EncodeEvent(EVENT_STATE, batch_id, _SOURCE_NAME, state=EncodeState.DONE))
        if self._error is not None:
            raise RuntimeError(self._error)

    def pause_all(self) -> None:
        self.calls.append("pause")

    def resume_all(self) -> None:
        self.calls.append("resume")

    def cancel_all(self) -> None:
        self.calls.append("cancel")

    def request_stop_after_current(self) -> None:
        self.calls.append("stop")


class EncodeControllerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root: str = self._tmp.name
        self.history = EncodeHistory(os.path.join(self.root, "video_cache.sqlite3"), sleep=lambda _s: None)
        with open(os.path.join(self.root, _SOURCE_NAME), "wb") as handle:
            handle.write(b"source")
        self.runners: list[_FakeRunner] = []
        self.gate = threading.Event()
        self.finished: list[object] = []
        self.events: list[object] = []

    def tearDown(self) -> None:
        self.gate.set()
        self.history.close()
        self._tmp.cleanup()

    def _controller(self, *, gate: threading.Event | None = None, error: str | None = None) -> EncodeController:
        def make(_settings: EncodeSettings, sink: QueueSink) -> _FakeRunner:
            runner = _FakeRunner(sink, gate=gate, error=error)
            self.runners.append(runner)
            return runner

        controller = EncodeController(self.history, make_runner=make)
        controller.event_received.connect(self.events.append)
        controller.batch_finished.connect(self.finished.append)
        return controller

    def _target(self) -> list[VideoEvaluation]:
        return [make_eval(video=make_video(filename=_SOURCE_NAME))]

    def test_start_without_targets_records_nothing(self) -> None:
        """대상 판정이 없으면 배치를 만들지 않고 실행 중 상태로 바뀌지 않음"""
        controller = self._controller()

        batch_id = controller.start(ClassifierConfig(), [], self.root, self.root)

        self.assertIsNone(batch_id)
        self.assertFalse(controller.is_running)
        self.assertEqual(self.history.list_open_batches(), [])

    def test_batch_runs_in_worker_and_events_reach_the_screen_thread(self) -> None:
        """워커가 보낸 이벤트와 종료 항목이 화면 스레드의 타이머를 거쳐 신호로 전달됨"""
        controller = self._controller()

        batch_id = controller.start(ClassifierConfig(), self._target(), self.root, self.root)

        self.assertIsNotNone(batch_id)
        self.assertTrue(_wait(lambda: len(self.finished) == 1))
        self.assertEqual(self.finished, [BatchFinished(batch_id or 0, "")])
        self.assertEqual(len(self.events), 1)
        event = self.events[0]
        assert isinstance(event, EncodeEvent)
        self.assertIs(event.state, EncodeState.DONE)
        self.assertFalse(controller.is_running)

    def test_controls_forward_to_runner_and_second_start_is_refused(self) -> None:
        """실행 중에는 제어 호출이 실행기에 전달되고, 다시 시작하면 거부됨. 끝난 뒤에는 전달되지 않음"""
        controller = self._controller(gate=self.gate)
        controller.start(ClassifierConfig(), self._target(), self.root, self.root)
        self.assertTrue(controller.is_running)

        with self.assertRaises(RuntimeError):
            controller.start(ClassifierConfig(), self._target(), self.root, self.root)
        controller.pause_all()
        controller.resume_all()
        controller.cancel_all()
        controller.request_stop_after_current()
        self.gate.set()
        self.assertTrue(_wait(lambda: len(self.finished) == 1))
        controller.pause_all()

        self.assertEqual(self.runners[0].calls, ["pause", "resume", "cancel", "stop"])
        self.assertFalse(controller.is_running)

    def test_pause_signal_is_sent_only_while_a_batch_runs(self) -> None:
        """일시중지와 재개 상태 신호가 실행 중에만 나가고 실행 전 호출은 무시되는지 검증"""
        changes: list[bool] = []
        controller = self._controller(gate=self.gate)
        controller.pause_changed.connect(changes.append)
        controller.pause_all()
        self.assertEqual(changes, [])

        controller.start(ClassifierConfig(), self._target(), self.root, self.root)
        controller.pause_all()
        controller.resume_all()
        self.gate.set()
        self.assertTrue(_wait(lambda: len(self.finished) == 1))
        self.assertEqual(changes, [True, False])

    def test_runner_exception_is_reported_in_finished_item(self) -> None:
        """실행기가 예외로 끝나면 종료 항목의 오류 문자열로 전달되고 실행 상태는 풀림"""
        controller = self._controller(error="저장 공간 부족")

        with self.assertLogs("src.gui.controllers.encode_worker", level="ERROR"):
            controller.start(ClassifierConfig(), self._target(), self.root, self.root)
            self.assertTrue(_wait(lambda: len(self.finished) == 1))
        finished = self.finished[0]
        assert isinstance(finished, BatchFinished)
        self.assertEqual(finished.error, "저장 공간 부족")
        self.assertFalse(controller.is_running)

    def test_default_runner_factory_builds_encode_runner_without_running(self) -> None:
        """기본 실행기는 설정 사본으로 만들며 실행은 하지 않음"""
        settings = EncodeSettings.from_config(ClassifierConfig())

        runner = make_encode_runner(self.history, None, settings, self.events.append)

        self.assertIsInstance(runner, EncodeRunner)
        self.assertEqual(self.history.list_open_batches(), [])


    def test_shutdown_cancels_a_started_batch_and_is_a_no_op_when_idle(self) -> None:
        """창 종료 정리가 실행 중 배치에 취소를 보내고 워커를 기다려 실행 중 상태를 풀며, 대기 중에는 아무것도 하지 않는지 검증"""
        controller = self._controller(gate=self.gate)
        controller.shutdown()
        self.assertFalse(controller.is_running)
        controller.start(ClassifierConfig(), self._target(), self.root, self.root)
        self.gate.set()
        controller.shutdown()
        self.assertFalse(controller.is_running)
        self.assertIn("cancel", self.runners[0].calls)

if __name__ == "__main__":
    unittest.main(verbosity=2)
