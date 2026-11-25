"""인코딩 컨트롤러 검증: 배치 시작과 대상 없음, 워커 실행과 큐 배출, 제어 전달, 중복 시작 거부, 예외 종료"""

import json
import os
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.encode.encode_state import EncodeState
from src.core.models import VideoEvaluation
from src.gui.controllers.encode_controller import (
    EncodeController,
    ReplaceRetryWorker,
    _discard_event,
    make_encode_runner,
)
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


_RETRY_ERROR_TEXT: str = "재시도 실패"


class _FakeRunner:
    """실제 인코딩 없이 상태 이벤트를 보내고, 제어 호출을 기록하는 실행기"""

    def __init__(self, sink: QueueSink, *, gate: threading.Event | None = None, error: str | None = None) -> None:
        self._sink: QueueSink = sink
        self._gate: threading.Event | None = gate
        self._error: str | None = error
        self.calls: list[str] = []
        self.accept: bool = True

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

    def retry_replace_pending(self, batch_id: int, only_source: str | None = None) -> None:
        self.calls.append(f"retry:{only_source}")

    def forfeit_replace_pending(self, batch_id: int, source_path: str) -> bool:
        self.calls.append(f"forfeit:{source_path}")
        return True

    def request_stop_after_current(self) -> None:
        self.calls.append("stop")

    def cancel_stop_after_current(self) -> None:
        self.calls.append("cancel_stop")

    def pause_one(self, source: str) -> bool:
        self.calls.append(f"pause_one:{source}")
        return self.accept

    def resume_one(self, source: str) -> bool:
        self.calls.append(f"resume_one:{source}")
        return self.accept

    def cancel_one(self, source: str) -> None:
        self.calls.append(f"cancel_one:{source}")


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

    def test_item_controls_reach_the_runner_and_signal_only_applied_pause_changes(self) -> None:
        """개별 제어가 실행기에 전달되고, 실행기가 적용한 일시중지 변화만 신호로 나오는지 검증"""
        controller = self._controller(gate=self.gate)
        changes: list[tuple[str, bool]] = []
        controller.item_pause_changed.connect(lambda path, paused: changes.append((path, paused)))
        controller.start(ClassifierConfig(), self._target(), self.root, self.root)
        runner = self.runners[0]
        controller.pause_one(_SOURCE_NAME)
        controller.resume_one(_SOURCE_NAME)
        controller.cancel_one(_SOURCE_NAME)
        runner.accept = False
        controller.pause_one(_SOURCE_NAME)
        self.assertEqual(changes, [(_SOURCE_NAME, True), (_SOURCE_NAME, False)])
        self.assertEqual(
            runner.calls,
            [
                f"pause_one:{_SOURCE_NAME}",
                f"resume_one:{_SOURCE_NAME}",
                f"cancel_one:{_SOURCE_NAME}",
                f"pause_one:{_SOURCE_NAME}",
            ],
        )
        # - 워커 스레드가 살아 있는 채로 컨트롤러가 지워지면 Qt가 프로세스를 끝내므로 먼저 정리
        self.gate.set()
        controller.shutdown()

    def test_item_controls_do_nothing_before_a_batch_runs(self) -> None:
        """배치 전에는 개별 제어가 실행기 없이 무시되고 신호도 나오지 않는지 검증"""
        controller = self._controller()
        changes: list[tuple[str, bool]] = []
        controller.item_pause_changed.connect(lambda path, paused: changes.append((path, paused)))
        controller.pause_one(_SOURCE_NAME)
        controller.resume_one(_SOURCE_NAME)
        controller.cancel_one(_SOURCE_NAME)
        self.assertEqual(changes, [])

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
        controller.cancel_stop_after_current()
        self.gate.set()
        self.assertTrue(_wait(lambda: len(self.finished) == 1))
        controller.pause_all()

        self.assertEqual(self.runners[0].calls, ["pause", "resume", "cancel", "stop", "cancel_stop"])
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

    def test_start_carries_configured_baseline_quality_into_jobs(self) -> None:
        """시작 시 설정의 화질 기준값이 기록된 작업에 담기는지 검증"""
        controller = self._controller()
        config = ClassifierConfig()
        config.encode.baseline_cq = 20.0
        config.encode.encode_codec = "hevc_nvenc"

        batch_id = controller.start(config, self._target(), self.root, self.root)

        assert batch_id is not None
        self.assertTrue(_wait(lambda: len(self.finished) == 1))
        stored = json.loads(self.history.list_items(batch_id)[0].plan_json)
        self.assertEqual(stored["baseline_quality"], 20.0)
        self.assertEqual(stored["quality"], 22.0)

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

class ResumeRetryForfeitTests(unittest.TestCase):
    """이어서 실행, 교체 대기 재시도와 포기의 실행기 전달, 중지 요청의 정상 종료 판정"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.history = EncodeHistory(os.path.join(self._tmp.name, "video_cache.sqlite3"), sleep=lambda _s: None)
        self.addCleanup(self.history.close)
        self.runners: list[_FakeRunner] = []

        def make(_settings: EncodeSettings, sink: QueueSink) -> _FakeRunner:
            runner = _FakeRunner(sink)
            self.runners.append(runner)
            return runner

        self.controller = EncodeController(self.history, make_runner=make)
        self.addCleanup(self.controller.shutdown)
        self.settings = EncodeSettings.from_config(ClassifierConfig())
        self.retry_done: list[str] = []
        self.controller.replace_retry_finished.connect(lambda error: self.retry_done.append(error))

    def test_interrupted_follows_stop_and_cancel_requests(self) -> None:
        """현재 영상 후 중지 요청은 예약 취소로 풀리고, 즉시 중지는 되돌려지지 않음"""
        self.controller.resume(1, self.settings)
        self.assertFalse(self.controller.interrupted)
        self.controller.request_stop_after_current()
        self.assertTrue(self.controller.interrupted)
        self.controller.cancel_stop_after_current()
        self.assertFalse(self.controller.interrupted)
        self.controller.cancel_all()
        self.assertTrue(self.controller.interrupted)
        self.assertTrue(_wait(lambda: not self.controller.is_running))

    def test_event_sink_for_side_runners_is_discarded(self) -> None:
        """재시도와 포기 전용 실행기의 이벤트는 화면에 보내지 않고 버림"""
        _discard_event(EncodeEvent(EVENT_STATE, 1, _SOURCE_NAME, state=EncodeState.DONE))

    def test_replace_retry_worker_body_runs_the_runner_retry_without_a_thread(self) -> None:
        """워커 본문은 스레드 밖에서 직접 실행해 같은 원본만 재시도 대상으로 넘기는지 검증"""
        runner = _FakeRunner(_discard_event)
        ReplaceRetryWorker(runner, 1, "a.mp4").run()
        self.assertEqual(runner.calls, ["retry:a.mp4"])

    def test_forfeit_and_retry_reach_a_runner_built_for_the_batch(self) -> None:
        """포기는 바로 실행기에 전달되고, 재시도는 워커에서 실행된 뒤 완료 신호를 보냄"""
        self.assertTrue(self.controller.forfeit_replace(1, self.settings, "a.mp4"))
        self.assertIn("forfeit:a.mp4", self.runners[-1].calls)
        self.assertTrue(self.controller.retry_replace(1, self.settings, "a.mp4"))
        self.assertTrue(_wait(lambda: bool(self.retry_done)))
        self.assertIn("retry:a.mp4", self.runners[-1].calls)

    def test_retry_worker_body_keeps_the_error_text_instead_of_raising(self) -> None:
        """재시도 워커 본문에서 예외가 나도 스레드 밖으로 던지지 않고 오류 문구를 남기는지 검증"""
        runner = _FakeRunner(_discard_event)
        with mock.patch.object(runner, "retry_replace_pending", side_effect=RuntimeError(_RETRY_ERROR_TEXT)):
            worker = ReplaceRetryWorker(runner, 1, "a.mp4")
            worker.run()
        self.assertEqual(worker.error, _RETRY_ERROR_TEXT)

    def test_failed_retry_sends_its_error_text_with_the_finished_signal(self) -> None:
        """재시도가 실패해도 끝 신호는 나가고, 신호 인자로 오류 문구가 전달되는지 검증"""
        with mock.patch.object(_FakeRunner, "retry_replace_pending", side_effect=RuntimeError(_RETRY_ERROR_TEXT)):
            self.assertTrue(self.controller.retry_replace(1, self.settings, "a.mp4"))
            self.assertTrue(_wait(lambda: bool(self.retry_done)))
        self.assertEqual(self.retry_done, [_RETRY_ERROR_TEXT])

    def test_forfeit_and_retry_refuse_while_a_batch_runs(self) -> None:
        """배치가 실행 중이면 포기와 재시도를 시작하지 않음"""
        self.controller.resume(1, self.settings)
        self.assertFalse(self.controller.forfeit_replace(1, self.settings, "a.mp4"))
        self.assertFalse(self.controller.retry_replace(1, self.settings, None))
        self.controller.cancel_all()
        self.assertTrue(_wait(lambda: not self.controller.is_running))


if __name__ == "__main__":
    unittest.main(verbosity=2)
