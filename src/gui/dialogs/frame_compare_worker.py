"""화질 비교 창의 프레임 쌍 추출 실행기: 요청 결과, 워커 본문, 전용 스레드, 요청 조율

- 워커 본문은 스레드 밖 함수(`run_extract_body`)로 분리해 직접 테스트
- 취소는 결과를 보내지 않고, 파일 부재와 실행 오류는 구분해 넘김
- 조율자는 같은 시점과 대상을 다시 요청하지 않고, 짧은 디바운스로 빠른 시점 이동의 추출을 한 번만 실행
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from src.core.analysis.frame_compare import FrameSource
from src.service.frame_compare_service import ExtractedFrames, FrameCompareFileMissing

EXTRACT_DEBOUNCE_MS: int = 120
DEDUPE_TIME_EPSILON_SEC: float = 0.001


class FrameExtractor(Protocol):
    """화질 비교 창이 쓰는 프레임 추출기"""

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
        """같은 시점의 두 프레임을 반환"""
        ...

    def release_decoders(self) -> None:
        """메모리 프레임 디코더가 연 컨테이너와 파일 핸들을 놓음"""
        ...


@dataclass(frozen=True, slots=True)
class ExtractOutcome:
    """추출 요청 한 건의 결과: 요청 순번, 프레임, 오류 문구, 파일 부재 여부, 시점"""

    request_id: int
    frames: ExtractedFrames | None
    error: str = ""
    file_missing: bool = False
    time_sec: float = 0.0


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    """프레임 쌍 추출 요청: 비교 쌍, 프레임 기준 크기, 시점, 대상 키, 즉시 실행 여부"""

    original: FrameSource
    compared: FrameSource
    frame_width: int
    frame_height: int
    time_sec: float
    target_key: str
    immediate: bool


def run_extract_body(
    extractor: FrameExtractor,
    request: ExtractOutcome,
    original: FrameSource,
    compared: FrameSource,
    frame_width: int,
    frame_height: int,
    cancel_event: threading.Event,
    sink: Callable[[ExtractOutcome], None],
) -> None:
    """프레임 쌍 추출을 실행해 결과나 원인별 오류를 넘김

    - 취소는 결과를 보내지 않고, 파일 부재와 실행 오류는 구분해 넘김
    """
    try:
        frames = extractor.extract_pair(
            original,
            compared,
            time_sec=request.time_sec,
            frame_width=frame_width,
            frame_height=frame_height,
            cancel_event=cancel_event,
        )
    except InterruptedError:
        return
    except FrameCompareFileMissing as error:
        sink(ExtractOutcome(request.request_id, None, str(error), True, request.time_sec))
        return
    except Exception as error:
        sink(ExtractOutcome(request.request_id, None, str(error), False, request.time_sec))
        return
    sink(ExtractOutcome(request.request_id, frames, "", False, request.time_sec))


class FrameCompareWorker(QThread):
    """프레임 쌍 추출을 실행하는 전용 스레드"""

    completed = pyqtSignal(object)

    def __init__(
        self,
        extractor: FrameExtractor,
        request: ExtractOutcome,
        original: FrameSource,
        compared: FrameSource,
        frame_width: int,
        frame_height: int,
        cancel_event: threading.Event,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._extractor: FrameExtractor = extractor
        self._request: ExtractOutcome = request
        self._original: FrameSource = original
        self._compared: FrameSource = compared
        self._frame_width: int = frame_width
        self._frame_height: int = frame_height
        self._cancel_event: threading.Event = cancel_event

    def cancel(self) -> None:
        """진행 중인 추출 취소 요청"""
        self._cancel_event.set()

    def run(self) -> None:
        """워커 스레드 본문"""
        run_extract_body(
            self._extractor,
            self._request,
            self._original,
            self._compared,
            self._frame_width,
            self._frame_height,
            self._cancel_event,
            self.completed.emit,
        )


class FrameCompareExtraction(QObject):
    """시점과 대상이 바뀔 때 이전 요청을 취소하고 최신 요청만 실행하는 조율자

    - 같은 시점과 대상을 이미 불러왔으면 다시 추출하지 않음
    - 즉시가 아닌 요청은 짧게 모아 빠른 시점 이동에서 추출을 한 번만 함
    """

    started = pyqtSignal()
    loaded = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, extractor: FrameExtractor, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._extractor = extractor
        self._pending: ExtractionRequest | None = None
        self._request_id: int = 0
        self._worker: FrameCompareWorker | None = None
        self._loaded_key: str | None = None
        self._loaded_time: float = -1.0
        self._active_key: str | None = None
        self._active_time: float = 0.0
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(EXTRACT_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._start_pending)

    @property
    def busy(self) -> bool:
        """진행 중인 추출 스레드가 있는지"""
        return self._worker is not None

    def request(self, request: ExtractionRequest) -> bool:
        """요청을 예약하고 실행을 시작했으면 참. 이미 불러온 시점과 대상이면 거짓"""
        if (
            request.target_key == self._loaded_key
            and abs(request.time_sec - self._loaded_time) < DEDUPE_TIME_EPSILON_SEC
        ):
            return False
        self._pending = request
        if request.immediate:
            self._start_pending()
        else:
            self._debounce.start()
        return True

    def cancel(self) -> None:
        """예약과 진행 중인 추출을 취소하고 스레드 종료를 기다림"""
        self._debounce.stop()
        self._pending = None
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            worker.wait()

    def release(self) -> None:
        """추출을 취소하고 추출기가 연 메모리 디코더를 닫아 파일 핸들을 놓음"""
        self.cancel()
        self._extractor.release_decoders()

    def _start_pending(self) -> None:
        """예약된 요청으로 추출 스레드를 시작"""
        request = self._pending
        self._pending = None
        if request is None:
            return
        self.cancel()
        self._request_id += 1
        self._active_key = request.target_key
        self._active_time = request.time_sec
        self.started.emit()
        worker = FrameCompareWorker(
            self._extractor,
            ExtractOutcome(self._request_id, None, time_sec=request.time_sec),
            request.original,
            request.compared,
            request.frame_width,
            request.frame_height,
            threading.Event(),
            self,
        )
        worker.completed.connect(self._on_completed)
        self._worker = worker
        worker.start()

    def _on_completed(self, outcome: object) -> None:
        """최신 요청의 성공 결과만 반영하고 지연 도착한 이전 결과는 버림"""
        if not isinstance(outcome, ExtractOutcome) or outcome.request_id != self._request_id:
            return
        self._worker = None
        if outcome.error or outcome.frames is None:
            self.failed.emit(outcome)
            return
        self._loaded_key = self._active_key
        self._loaded_time = self._active_time
        self.loaded.emit(outcome)
