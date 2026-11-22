"""화질 비교 창의 프레임 쌍 추출 실행기: 요청 결과, 워커 본문, 전용 스레드

- 워커 본문은 스레드 밖 함수(`run_extract_body`)로 분리해 직접 테스트
- 취소는 결과를 보내지 않고, 파일 부재와 실행 오류는 구분해 넘김
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QWidget

from src.core.analysis.frame_compare import FrameSource
from src.service.frame_compare_service import ExtractedFrames, FrameCompareFileMissing


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
        """같은 시점의 두 프레임 PNG 바이트를 반환"""
        ...


@dataclass(frozen=True, slots=True)
class ExtractOutcome:
    """추출 요청 한 건의 결과: 요청 순번, 프레임, 오류 문구, 파일 부재 여부, 시점"""

    request_id: int
    frames: ExtractedFrames | None
    error: str = ""
    file_missing: bool = False
    time_sec: float = 0.0


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
        parent: QWidget | None = None,
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
