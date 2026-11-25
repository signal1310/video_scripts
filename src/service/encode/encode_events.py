"""인코딩 진행과 상태 이벤트, 진행 줄을 샘플과 이벤트로 바꾸는 중계

- 워커 스레드에서 호출되므로 화면 객체에 접근하지 않음. 호출자가 화면 스레드로 옮김
"""

from collections.abc import Callable
from dataclasses import dataclass

from src.core.encode.encode_progress import (
    InstantBitrateTracker,
    ProgressParser,
    ProgressSample,
    projected_output_bytes,
    should_emit_progress,
)
from src.core.encode.encode_state import EncodeState

EVENT_STATE: str = "state"
EVENT_PROGRESS: str = "progress"
EVENT_LONG_PAUSE: str = "long_pause"
EVENT_PHASE: str = "phase"

PHASE_SAMPLING: str = "sampling"
PHASE_SAMPLE_PREDICT: str = "sample_predict"
PHASE_PRECISE_TUNE: str = "precise_tune"
PHASE_MAIN: str = "main"
PHASE_RETRY: str = "retry"


@dataclass(frozen=True, slots=True)
class TuningPhase:
    """키프레임 교정 품질값 자동 조정 단계 정보(샘플 확인, 본 인코딩, 재시도)

    - adjusted_quality는 절약 목적 사전 예측에서 예측 크기가 목표를 넘어 올린 품질값
    - probe_index, probe_total, precise_quality, precise_reason은 품질값 정밀 결정 단계의 차례와 확정 값
    """

    stage: str
    sample_pass: int = 0
    sample_total: int = 2
    segment_done: int = 0
    segment_total: int = 5
    baseline_quality: float = 0.0
    current_quality: float = 0.0
    previous_quality: float | None = None
    retry_count: int = 0
    note: str = ""
    adjusted_quality: float | None = None
    probe_index: int = 0
    probe_total: int = 0
    precise_quality: float | None = None
    precise_reason: str = ""
    # - 정밀 결정이 확정한 해상도 선택(`DECISION_KEEP_ORIGINAL`/`DECISION_KEEP_DOWNSCALE`). 그 밖은 빈 문자열
    precise_resolution: str = ""


@dataclass(frozen=True, slots=True)
class EncodeEvent:
    """화면에 전달할 진행, 상태, 안내 이벤트"""

    kind: str
    batch_id: int
    source_path: str
    state: EncodeState | None = None
    reason: str = ""
    sample: ProgressSample | None = None
    instant_kbps: float | None = None
    projected_bytes: int | None = None
    elapsed_sec: float = 0.0
    phase: TuningPhase | None = None


EventSink = Callable[[EncodeEvent], None]


def main_phase_event(
    batch_id: int,
    source_path: str,
    quality: float,
    *,
    adjusted_quality: float | None = None,
    precise_quality: float | None = None,
    precise_reason: str = "",
    previous_quality: float | None = None,
    precise_resolution: str = "",
) -> EncodeEvent:
    """샘플 단계 배지를 해제하는 본 인코딩 시작 이벤트

    - adjusted_quality가 있으면 사전 예측에서 품질값을 올렸으므로 진행 창이 그 사실을 함께 표시
    - precise_quality가 있으면 품질값 정밀 결정이 확정한 값이며, previous_quality는 결정 전 계획 값
    - precise_resolution은 정밀 결정의 해상도 판정이며, 원본 유지일 때 화면이 그 사실을 함께 표시
    """
    return EncodeEvent(
        kind=EVENT_PHASE,
        batch_id=batch_id,
        source_path=source_path,
        phase=TuningPhase(
            stage=PHASE_MAIN,
            baseline_quality=quality,
            current_quality=quality if adjusted_quality is None else adjusted_quality,
            previous_quality=previous_quality,
            adjusted_quality=adjusted_quality,
            precise_quality=precise_quality,
            precise_reason=precise_reason,
            precise_resolution=precise_resolution,
        ),
    )


class ProgressRelay:
    """FFmpeg 진행 줄을 샘플로 바꾸고 0.5초 주기로 이벤트를 보내는 중계"""

    def __init__(
        self,
        *,
        emit: EventSink,
        batch_id: int,
        source_path: str,
        duration_sec: float,
        elapsed: Callable[[], float],
        clock: Callable[[], float],
    ) -> None:
        self._emit: EventSink = emit
        self._batch_id: int = batch_id
        self._source_path: str = source_path
        self._duration_sec: float = duration_sec
        self._elapsed: Callable[[], float] = elapsed
        self._clock: Callable[[], float] = clock
        self._parser: ProgressParser = ProgressParser()
        self._tracker: InstantBitrateTracker = InstantBitrateTracker()
        self._last_emit: float | None = None

    def feed(self, line: str) -> None:
        """진행 줄 한 개를 넣고, 샘플이 나오면 주기에 맞춰 이벤트로 보냄"""
        sample = self._parser.feed(line)
        if sample is None:
            return
        now = self._clock()
        if not should_emit_progress(now, self._last_emit):
            return
        self._last_emit = now
        self._emit(
            EncodeEvent(
                kind=EVENT_PROGRESS,
                batch_id=self._batch_id,
                source_path=self._source_path,
                sample=sample,
                instant_kbps=self._tracker.add(sample.out_time_sec, sample.total_size_bytes),
                projected_bytes=projected_output_bytes(sample, self._duration_sec),
                elapsed_sec=self._elapsed(),
            )
        )
