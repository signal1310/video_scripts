"""화질 분석 배치 본문과 전용 스레드

- 워커 본문은 `run_frame_analysis_body`로 분리해 QThread 없이 직접 테스트
- 워커는 Qt 객체에 접근하지 않고 시그널로만 화면 스레드에 결과를 넘김
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Sequence

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QWidget

from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.gui.controllers.frame_analysis_events import (
    FrameAnalysisEvent,
    FrameAnalysisFinished,
    FrameAnalysisProgress,
    FrameAnalysisSink,
    FrameAnalysisStageProgress,
)
from src.service.frame_analysis_service import FrameAnalysisRequest, FrameAnalysisService


def run_frame_analysis_body(
    service: FrameAnalysisService,
    requests: Sequence[FrameAnalysisRequest],
    cancel_event: threading.Event,
    sink: FrameAnalysisSink,
    *,
    crop_only: bool = False,
    crop_only_names: frozenset[str] = frozenset(),
) -> None:
    """요청 목록을 분석하고 완료분 캐시를 읽어 종료 이벤트를 보냄

    - crop_only면 검은 여백 스캔만 실행하고 캐시 재사용 수도 같은 기준으로 셈
    - crop_only_names에 든 영상은 전체 실행에서도 검은 여백만 감지
    - 취소는 예외로 올라오므로 삼키고, 이미 저장된 완료분을 캐시에서 다시 읽어 보존
    - 캐시 조회와 결과 읽기도 DB 오류가 날 수 있어 같은 오류 경계 안에서 실행하고, 실패는 문구를 담아 종료 이벤트로 넘김
    - 캐시 재사용 수는 실행 전에 재어 두어야 결과를 저장한 뒤와 구분됨
    """

    def on_progress(completed: int, total: int, filename: str, result: FrameAnalysisResult) -> None:
        sink(FrameAnalysisProgress(completed, total, filename, result))

    def on_stage(filename: str, stage: str, done: int, total_stage: int) -> None:
        sink(FrameAnalysisStageProgress(0, 0, filename, stage, done, total_stage))

    total = len({request.key for request in requests})
    # - 캐시 조회와 결과 읽기도 DB 오류가 날 수 있으므로 함께 경계 안에 둠
    try:
        cached_count = service.count_cached(requests, crop_only=crop_only, crop_only_names=crop_only_names)
        with contextlib.suppress(InterruptedError):
            service.analyze_batch(
                requests,
                crop_only=crop_only,
                cancel_event=cancel_event,
                on_progress=on_progress,
                on_stage=on_stage,
                crop_only_names=crop_only_names,
            )
        results = service.load_results(requests)
    except Exception as error:
        sink(FrameAnalysisFinished({}, error=str(error) or type(error).__name__, crop_only=crop_only))
        return
    sink(
        FrameAnalysisFinished(
            results,
            cancelled=cancel_event.is_set(),
            total=total,
            cached_count=cached_count,
            crop_only=crop_only,
        )
    )


class FrameAnalysisWorker(QThread):
    """화질 분석 배치를 실행하는 전용 스레드"""

    progress = pyqtSignal(int, int, str, object)
    stage_progress = pyqtSignal(int, int, str, str, int, int)
    completed = pyqtSignal(object)

    def __init__(
        self,
        service: FrameAnalysisService,
        requests: Sequence[FrameAnalysisRequest],
        cancel_event: threading.Event,
        parent: QWidget | None = None,
        *,
        crop_only: bool = False,
        crop_only_names: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._requests = list(requests)
        self._cancel_event = cancel_event
        self._crop_only = crop_only
        self._crop_only_names = crop_only_names

    def cancel(self) -> None:
        """진행 중인 화질 분석 취소 요청"""
        self._cancel_event.set()

    def run(self) -> None:
        """워커 스레드 본문"""
        run_frame_analysis_body(
            self._service,
            self._requests,
            self._cancel_event,
            self._emit,
            crop_only=self._crop_only,
            crop_only_names=self._crop_only_names,
        )

    def _emit(self, event: FrameAnalysisEvent) -> None:
        """워커 스레드에서 진행과 종료를 시그널로 넘김"""
        if isinstance(event, FrameAnalysisProgress):
            self.progress.emit(event.completed, event.total, event.filename, event.result)
        elif isinstance(event, FrameAnalysisStageProgress):
            self.stage_progress.emit(
                event.completed,
                event.total,
                event.filename,
                event.stage,
                event.stage_done,
                event.stage_total,
            )
        else:
            self.completed.emit(event)
