"""인코딩 실행 전 품질값 정밀 결정 단계(결정 18, 6a-2)

- 대상 선택은 순수 규칙(`core.encode.precise_tune_policy.should_precise_tune`)이 배치를 만들 때 정함
- 이 모듈은 실행만 맡고, 샘플 인코딩과 지표 측정, 결과 캐시는 `sample_tuner_service`를 그대로 씀
- 진행은 사전 예측과 같은 단계 이벤트로 보내 화면이 정밀 결정 차례를 함께 표시
- 취소되면 `TuneResult.converged`가 거짓이므로 호출자가 계획한 품질값을 그대로 씀
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass

from src.core.encode.encode_plan import EncodePlan
from src.core.evaluation.sample_tuner import (
    TUNE_MAX_PROBES,
    TuneResult,
    format_result_reason,
)
from src.core.quality_target import format_cq_value
from src.service.encode.encode_events import (
    EVENT_PHASE,
    PHASE_PRECISE_TUNE,
    EncodeEvent,
    EventSink,
    TuningPhase,
)
from src.service.encode.encode_job import EncodeJob
from src.service.encode.encode_pause import PauseControl
from src.service.encode.encode_ports import ProcessFactory
from src.service.sample_tuner_service import (
    SampleTunerCache,
    TuneJob,
    open_default_cache,
    tune_quality_cached,
)

# - 주입한 탐색 함수의 서명. 기본값은 캐시를 먼저 확인하는 서비스 진입점
TuneCall = Callable[..., TuneResult]


def precise_phase_event(
    batch_id: int,
    source_path: str,
    *,
    probe_index: int,
    probe_total: int,
    previous_quality: float,
    current_quality: float = 0.0,
    reason: str = "",
) -> EncodeEvent:
    """품질값 정밀 결정 단계 이벤트. current_quality는 탐색이 끝난 뒤 확정 값만 담음"""
    return EncodeEvent(
        kind=EVENT_PHASE,
        batch_id=batch_id,
        source_path=source_path,
        phase=TuningPhase(
            stage=PHASE_PRECISE_TUNE,
            baseline_quality=previous_quality,
            previous_quality=previous_quality,
            current_quality=current_quality,
            probe_index=probe_index,
            probe_total=probe_total,
            precise_quality=current_quality if current_quality > 0 else None,
            precise_reason=reason,
        ),
    )


@dataclass(frozen=True, slots=True)
class PreciseTuneOutcome:
    """정밀 결정 결과와 인코딩 계획에 반영할 품질값, 결과 사유 줄"""

    result: TuneResult
    previous_quality: float
    adopted_quality: float
    reason: str


class PreciseQualityTuner:
    """영상 한 개의 정밀 결정을 실행하고 확정 품질값을 돌려줌

    - 캐시를 먼저 확인하는 서비스 진입점을 그대로 쓰므로 같은 계획의 재실행은 샘플 인코딩을 건너뜀
    - 취소되면 결과를 그대로 돌려주고 호출자가 계획 품질값을 유지하게 함
    """

    def __init__(
        self,
        *,
        ffmpeg_exec: str,
        process_factory: ProcessFactory,
        abort: threading.Event,
        control: PauseControl | None = None,
        emit: EventSink | None = None,
        cache: SampleTunerCache | None = None,
        tune: TuneCall | None = None,
        delta_max: float | None = None,
    ) -> None:
        self._ffmpeg: str = ffmpeg_exec
        self._factory: ProcessFactory = process_factory
        self._abort: threading.Event = abort
        self._control: PauseControl | None = control
        self._emit: EventSink | None = emit
        self._cache: SampleTunerCache = cache if cache is not None else open_default_cache()
        self._tune: TuneCall = tune if tune is not None else tune_quality_cached
        self._delta_max: float | None = delta_max

    def __call__(self, batch_id: int, job: EncodeJob, plan: EncodePlan) -> PreciseTuneOutcome | None:
        """정밀 결정을 실행. 취소로 중단했으면 None"""
        source = job.source_path
        previous_quality = job.quality
        output_size = plan.output_size
        tune_job = TuneJob(
            source_path=source,
            source_size=job.source_size,
            source_mtime_ns=job.source_mtime_ns,
            source_duration_sec=job.source_duration_sec,
            encoder=plan.encoder,
            baseline_quality=job.quality if job.baseline_quality is None else job.baseline_quality,
            width=0 if output_size is None else output_size[0],
            height=0 if output_size is None else output_size[1],
        )
        self._notify(batch_id, source, previous_quality, 0, TUNE_MAX_PROBES)

        def _on_probe(index: int, total: int, _quality: float) -> None:
            self._notify(batch_id, source, previous_quality, index, total)

        result = self._tune(
            job=tune_job,
            plan=plan,
            cache=self._cache,
            process_factory=self._factory,
            ffmpeg_exec=self._ffmpeg,
            cancel_event=self._abort,
            on_probe=_on_probe,
            control=self._control,
            batch_id=batch_id,
            delta_max=self._delta_max,
        )
        if self._abort.is_set():
            return None
        adopted = result.best_quality if result.converged else previous_quality
        detail = format_result_reason(result)
        reason = (
            f"품질값 정밀 결정: {format_cq_value(previous_quality)} -> {format_cq_value(adopted)} / {detail}"
            if adopted != previous_quality
            else detail
        )
        self._notify(
            batch_id, source, previous_quality, len(result.probes), TUNE_MAX_PROBES,
            current_quality=adopted, reason=reason,
        )
        return PreciseTuneOutcome(
            result=result,
            previous_quality=previous_quality,
            adopted_quality=adopted,
            reason=reason,
        )

    def _notify(
        self,
        batch_id: int,
        source: str,
        previous_quality: float,
        index: int,
        total: int,
        *,
        current_quality: float = 0.0,
        reason: str = "",
    ) -> None:
        """진행 표시 이벤트 전달. 화면이 없으면 아무것도 하지 않음"""
        if self._emit is None:
            return
        self._emit(
            precise_phase_event(
                batch_id,
                source,
                probe_index=index,
                probe_total=total,
                previous_quality=previous_quality,
                current_quality=current_quality,
                reason=reason,
            )
        )
