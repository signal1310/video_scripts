"""재인코딩 실행 컨트롤러: 배치 생성, 전용 워커 시작, 큐 배출과 실행 중 제어를 맡음

- 화면 스레드에서만 호출. 워커가 채운 큐는 화면 스레드 타이머가 주기적으로 비움(쟁점 C)
- 시작 확인과 원본 교체 확인은 통합 창의 시작 흐름이 맡고, 이 컨트롤러는 확인하지 않음
"""

import logging
import queue
from collections.abc import Callable, Sequence
from functools import partial
from typing import Protocol

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from src.core.config import ClassifierConfig
from src.core.models import VideoEvaluation
from src.gui.controllers.encode_worker import BatchFinished, EncodeWorker, QueueItem, QueueSink
from src.service.analysis_store import AnalysisStore
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_job import EncodeSettings, create_batch, jobs_from_evaluations
from src.service.encode.encode_service import EncodeRunner

EVENT_DRAIN_INTERVAL_MS: int = 100

logger = logging.getLogger(__name__)


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

    def cancel_stop_after_current(self) -> None:
        """현재 영상 후 중지 예약 취소"""

    def pause_one(self, source: str) -> bool:
        """실행 중인 영상 하나를 일시중지. 적용하면 참"""
        ...

    def resume_one(self, source: str) -> bool:
        """개별 일시중지한 영상 하나를 다시 실행. 적용하면 참"""
        ...

    def cancel_one(self, source: str) -> None:
        """실행 중인 영상 하나를 즉시 중지하고 대기로 되돌림"""

    def retry_replace_pending(self, batch_id: int, only_source: str | None = None) -> None:
        """교체 대기 항목을 다시 배치(only_source가 있으면 그 항목만)"""

    def forfeit_replace_pending(self, batch_id: int, source_path: str) -> bool:
        """교체 대기 항목을 실패로 바꿈. 바꾸면 참"""
        ...


RunnerFactory = Callable[[EncodeSettings, QueueSink], ControllableRunner]


def make_encode_runner(
    history: EncodeHistory,
    store: AnalysisStore | None,
    settings: EncodeSettings,
    sink: QueueSink,
) -> EncodeRunner:
    """배치 설정 사본으로 실행기를 만듦. 실행은 하지 않음"""
    return EncodeRunner(history, settings, on_event=sink, store=store)


def _discard_event(_item: QueueItem) -> None:
    """배치 밖에서 만든 실행기(재시도, 포기)의 이벤트는 화면에 보내지 않음. 상태는 기록 DB로 다시 읽음"""


class ReplaceRetryWorker(QThread):
    """교체 대기 재시도를 화면 스레드 밖에서 실행. 파일 옮기기가 오래 걸릴 수 있어 막지 않게 함"""

    def __init__(
        self,
        runner: ControllableRunner,
        batch_id: int,
        only_source: str | None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner: ControllableRunner = runner
        self._batch_id: int = batch_id
        self._only_source: str | None = only_source
        self.error: str = ""

    def run(self) -> None:
        """교체 대기 항목을 다시 배치. 예외는 스레드 밖으로 내보내지 않고 오류 문구로 남김(처리되지 않은 예외는 앱을 끝냄)"""
        try:
            self._runner.retry_replace_pending(self._batch_id, self._only_source)
        except Exception as error:
            logger.exception("교체 대기 재시도가 중단되었습니다: %s", self._batch_id)
            self.error = str(error)


class EncodeController(QObject):
    """재인코딩 배치 하나의 시작, 실행 중 제어, 이벤트 전달을 맡음

    - event_received는 진행과 상태 이벤트를 화면 스레드에서 보냄
    - batch_finished는 배치가 끝나 상태가 정리된 뒤 한 번 보냄
    """

    event_received = pyqtSignal(object)
    batch_finished = pyqtSignal(object)
    pause_changed = pyqtSignal(bool)
    item_pause_changed = pyqtSignal(str, bool)
    replace_retry_finished = pyqtSignal(str)

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
        self._retry_worker: ReplaceRetryWorker | None = None
        self._stop_after: bool = False
        self._cancelled: bool = False

    @property
    def is_running(self) -> bool:
        """배치가 실행 중이면 참. 워커가 끝난 뒤 종료 항목을 배출하기 전까지도 실행 중으로 봄"""
        return self._worker is not None

    @property
    def interrupted(self) -> bool:
        """마지막 배치가 즉시 중지나 현재 영상 후 중지로 끝났는지. 완료 후 동작은 정상 종료일 때만 실행"""
        return self._cancelled or self._stop_after

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
        jobs = jobs_from_evaluations(
            evaluations,
            root_spec,
            keyframe_increase_rate=config.thresholds.keyframe_1s_increase_rate,
            base_fps=config.bitrate_model.base_fps,
        )
        if not jobs:
            return None
        settings = EncodeSettings.from_config(config)
        batch_id = create_batch(self._history, settings, root_dir, jobs)
        self._launch(batch_id, settings)
        return batch_id

    def resume(self, batch_id: int, settings: EncodeSettings) -> None:
        """기록된 배치를 이어서 실행. 재개 준비(대기 되돌림, 원본 변경 제외)는 호출자가 먼저 끝냄"""
        if self.is_running:
            raise RuntimeError("재인코딩이 이미 실행 중입니다")
        self._launch(batch_id, settings)

    def forfeit_replace(self, batch_id: int, settings: EncodeSettings, source: str) -> bool:
        """교체 대기 항목을 실패로 바꿈. 배치가 실행 중이면 바꾸지 않고 거짓을 돌려줌"""
        if self.is_running:
            return False
        return self._make_runner(settings, _discard_event).forfeit_replace_pending(batch_id, source)

    def retry_replace(self, batch_id: int, settings: EncodeSettings, only_source: str | None) -> bool:
        """교체 대기 재시도를 워커에서 시작. 배치나 다른 재시도가 실행 중이면 시작하지 않고 거짓을 돌려줌"""
        if self.is_running or self._retry_worker is not None:
            return False
        worker = ReplaceRetryWorker(self._make_runner(settings, _discard_event), batch_id, only_source, self)
        worker.finished.connect(self._on_retry_finished)
        self._retry_worker = worker
        worker.start()
        return True

    def _on_retry_finished(self) -> None:
        worker = self._retry_worker
        self._retry_worker = None
        self.replace_retry_finished.emit(worker.error if worker is not None else "")

    def _launch(self, batch_id: int, settings: EncodeSettings) -> None:
        self._stop_after = False
        self._cancelled = False
        runner = self._make_runner(settings, self._queue.put)
        self._runner = runner
        self._worker = EncodeWorker(runner, batch_id, self._queue.put, self)
        self._timer.start()
        self._worker.start()

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
            self._cancelled = True
            self._runner.cancel_all()

    def request_stop_after_current(self) -> None:
        """실행 중인 영상이 끝나면 새 영상을 시작하지 않고 멈춤"""
        if self._runner is not None:
            self._stop_after = True
            self._runner.request_stop_after_current()

    def cancel_stop_after_current(self) -> None:
        """현재 영상 후 중지 예약을 취소하고 대기 영상을 이어서 시작하게 함"""
        if self._runner is not None:
            self._stop_after = False
            self._runner.cancel_stop_after_current()

    def shutdown(self) -> None:
        """실행 중인 배치를 즉시 중지하고 워커가 끝날 때까지 기다림. 남은 큐 항목은 버림

        - 화면 닫기는 조정자가 실행 중이 아닐 때만 이 함수에 도달하게 함. 여기서 기다리는 것은 워커가 남아 있을 때의 안전장치
        - 중지는 즉시 중지와 같아 원본은 바뀌지 않고 영상은 대기로 되돌아감
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
