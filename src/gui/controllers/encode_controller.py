"""재인코딩 실행 컨트롤러: 배치 생성, 전용 워커 시작, 큐 배출과 실행 중 제어를 맡음

- 화면 스레드에서만 호출. 워커가 채운 큐는 화면 스레드 타이머가 주기적으로 비움(쟁점 C)
- 시작 확인과 원본 교체 확인은 호출자의 대화상자가 맡고, 이 컨트롤러는 확인하지 않음
"""

import queue
from collections.abc import Callable, Sequence
from functools import partial
from typing import Protocol

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from src.core.config import ClassifierConfig
from src.core.models import VideoEvaluation
from src.gui.controllers.encode_worker import BatchFinished, EncodeWorker, QueueItem, QueueSink
from src.service.analysis_store import AnalysisStore
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_job import EncodeSettings, create_batch, jobs_from_evaluations
from src.service.encode.encode_service import EncodeRunner

EVENT_DRAIN_INTERVAL_MS: int = 100


class ControllableRunner(Protocol):
    """배치 실행과 실행 중 제어 메서드를 가진 실행기 인터페이스"""

    def run(self, batch_id: int) -> None:
        """배치를 끝까지 실행"""

    def pause_all(self) -> None:
        """실행 중인 모든 영상을 일시중지"""

    def resume_all(self) -> None:
        """일시중지한 영상을 다시 실행"""

    def cancel_all(self) -> None:
        """실행 중인 영상을 즉시 중지"""

    def request_stop_after_current(self) -> None:
        """현재 영상 후 중지"""

    def pause_one(self, source: str) -> bool:
        """실행 중인 영상 하나를 일시중지. 적용하면 참"""
        ...

    def resume_one(self, source: str) -> bool:
        """개별 일시중지한 영상 하나를 다시 실행. 적용하면 참"""
        ...

    def cancel_one(self, source: str) -> None:
        """실행 중인 영상 하나를 즉시 중지하고 대기로 되돌림"""


RunnerFactory = Callable[[EncodeSettings, QueueSink], ControllableRunner]


def make_encode_runner(
    history: EncodeHistory,
    store: AnalysisStore | None,
    settings: EncodeSettings,
    sink: QueueSink,
) -> EncodeRunner:
    """배치 설정 사본으로 실행기를 만듦. 실행은 하지 않음"""
    return EncodeRunner(history, settings, on_event=sink, store=store)


class EncodeController(QObject):
    """재인코딩 배치 하나의 시작, 실행 중 제어, 이벤트 전달을 맡음

    - event_received는 진행과 상태 이벤트를 화면 스레드에서 보냄
    - batch_finished는 배치가 끝나 상태가 정리된 뒤 한 번 보냄
    """

    event_received = pyqtSignal(object)
    batch_finished = pyqtSignal(object)
    pause_changed = pyqtSignal(bool)
    item_pause_changed = pyqtSignal(str, bool)

    def __init__(
        self,
        history: EncodeHistory,
        *,
        store: AnalysisStore | None = None,
        make_runner: RunnerFactory | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._make_runner: RunnerFactory = (
            make_runner if make_runner is not None else partial(make_encode_runner, history, store)
        )
        self._history: EncodeHistory = history
        self._queue: queue.SimpleQueue[QueueItem] = queue.SimpleQueue()
        self._timer: QTimer = QTimer(self)
        self._timer.setInterval(EVENT_DRAIN_INTERVAL_MS)
        self._timer.timeout.connect(self._drain)
        self._runner: ControllableRunner | None = None
        self._worker: EncodeWorker | None = None

    @property
    def is_running(self) -> bool:
        """배치가 실행 중이면 참. 워커가 끝난 뒤 종료 항목을 배출하기 전까지도 실행 중으로 봄"""
        return self._worker is not None

    def start(
        self,
        config: ClassifierConfig,
        evaluations: Sequence[VideoEvaluation],
        root_spec: str,
        root_dir: str,
    ) -> int | None:
        """대상 판정으로 배치를 기록하고 워커를 시작. 대상이 없으면 기록하지 않고 None"""
        if self.is_running:
            raise RuntimeError("재인코딩이 이미 실행 중입니다")
        jobs = jobs_from_evaluations(evaluations, root_spec)
        if not jobs:
            return None
        settings = EncodeSettings.from_config(config)
        batch_id = create_batch(self._history, settings, root_dir, jobs)
        runner = self._make_runner(settings, self._queue.put)
        self._runner = runner
        self._worker = EncodeWorker(runner, batch_id, self._queue.put, self)
        self._timer.start()
        self._worker.start()
        return batch_id

    def pause_all(self) -> None:
        """실행 중인 모든 영상을 일시중지"""
        if self._runner is not None:
            self._runner.pause_all()
            self.pause_changed.emit(True)

    def resume_all(self) -> None:
        """일시중지한 영상을 다시 실행"""
        if self._runner is not None:
            self._runner.resume_all()
            self.pause_changed.emit(False)

    def pause_one(self, source: str) -> None:
        """실행 중인 영상 하나를 일시중지하고 그 항목의 일시중지 변화를 알림"""
        if self._runner is not None and self._runner.pause_one(source):
            self.item_pause_changed.emit(source, True)

    def resume_one(self, source: str) -> None:
        """개별 일시중지한 영상 하나를 다시 실행하고 그 항목의 일시중지 변화를 알림"""
        if self._runner is not None and self._runner.resume_one(source):
            self.item_pause_changed.emit(source, False)

    def cancel_one(self, source: str) -> None:
        """실행 중인 영상 하나를 즉시 중지. 대기로 되돌아가는 상태 변화는 이벤트로 전달됨"""
        if self._runner is not None:
            self._runner.cancel_one(source)

    def cancel_all(self) -> None:
        """실행 중인 영상을 즉시 중지하고 대기로 되돌림"""
        if self._runner is not None:
            self._runner.cancel_all()

    def request_stop_after_current(self) -> None:
        """실행 중인 영상이 끝나면 새 영상을 시작하지 않고 멈춤"""
        if self._runner is not None:
            self._runner.request_stop_after_current()

    def shutdown(self) -> None:
        """창 종료 때 실행 중인 배치를 즉시 중지하고 워커가 끝날 때까지 기다림. 남은 큐 항목은 버림

        - 중지는 기존 즉시 중지와 같아 원본은 바뀌지 않고 영상은 대기로 되돌아감
        - 종료 확인 대화상자는 5d에서 추가. 그 전까지는 확인 없이 중지함
        """
        worker = self._worker
        if worker is None:
            return
        self.cancel_all()
        worker.wait()
        self._timer.stop()
        self._worker = None
        self._runner = None

    def _drain(self) -> None:
        """큐의 항목을 순서대로 전달하고, 종료 항목이 나오면 배치 상태를 정리"""
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if isinstance(item, BatchFinished):
                self._finish(item)
                return
            self.event_received.emit(item)

    def _finish(self, finished: BatchFinished) -> None:
        self._timer.stop()
        worker = self._worker
        self._worker = None
        self._runner = None
        if worker is not None:
            worker.wait()
        self.batch_finished.emit(finished)
