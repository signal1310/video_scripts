"""인코딩 실행 전 품질값 정밀 결정 단계(결정 18, 6a-2/6a-3)

- 대상 선택은 순수 규칙(`core.encode.precise_tune_policy.should_precise_tune`)이 배치를 만들 때 정함
- 이 모듈은 실행만 맡고, 샘플 인코딩과 지표 측정, 결과 캐시는 `sample_tuner_service`를 그대로 씀
- 축소 후보는 같은 구간 표본으로 [원본(크롭) + 보호 품질값]을 한 번 더 재서 원본 규격 유지 여부를 정함
- 진행은 사전 예측과 같은 단계 이벤트로 보내 화면이 정밀 결정 차례를 함께 표시
- 취소되면 `TuneResult.converged`가 거짓이므로 호출자가 계획한 품질값을 그대로 씀
"""

import dataclasses
import threading
from collections.abc import Callable
from dataclasses import dataclass

from src.core.encode.encode_plan import EncodePlan
from src.core.encode.precise_tune_policy import (
    original_resolution_maxrate_kbps,
    should_compare_downscale,
)
from src.core.evaluation.sample_tuner import (
    DECISION_KEEP_DOWNSCALE,
    DECISION_KEEP_ORIGINAL,
    TUNE_MAX_PROBES,
    DownscaleComparison,
    QualityProbe,
    TuneResult,
    format_comparison_reason,
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
    compare_downscale_cached,
    open_default_cache,
    tune_quality_cached,
)

# - 주입한 탐색 함수의 서명. 기본값은 캐시를 먼저 확인하는 서비스 진입점
TuneCall = Callable[..., TuneResult]
# - 주입한 축소 비교 함수의 서명. 기본값은 캐시를 먼저 확인하는 서비스 진입점
CompareCall = Callable[..., DownscaleComparison]


def original_resolution_plan(plan: EncodePlan, job: EncodeJob, maxrate_kbps: int | None) -> EncodePlan:
    """축소 계획에서 scale과 축소 상한을 빼고 원본(크롭) 해상도 계획으로 바꿈

    - crop은 그대로 남겨 잘라내기 확정 영상의 구도를 유지
    - 원본 화소 크기를 모르면 출력 크기를 비우고 scale만 빼서 원본 프레임을 그대로 씀
    """
    width, height = job.source_width or 0, job.source_height or 0
    return dataclasses.replace(
        plan,
        output_size=(width, height) if width > 0 and height > 0 else None,
        scale_filter=None,
        maxrate_kbps=maxrate_kbps,
    )


def replace_downscale_with_original(
    job: EncodeJob,
    plan: EncodePlan,
    maxrate_kbps: int | None,
) -> tuple[EncodeJob, EncodePlan]:
    """원본 해상도 옵션이 이겼을 때 작업과 계획을 원본 규격으로 바꿔 돌려줌

    - 사전 예측과 사후 게이트가 쓰는 목표 비트레이트도 원본 해상도 기준 값으로 함께 바꿈
    - 확정 결과를 작업에 남겨 이어서 인코딩할 때 같은 비교를 반복하지 않음
    """
    original_target = job.original_target_kbps
    new_plan = original_resolution_plan(plan, job, maxrate_kbps)
    new_job = dataclasses.replace(
        job,
        output_size=new_plan.output_size,
        scale_filter=None,
        maxrate_kbps=maxrate_kbps,
        corrected_target_kbps=original_target if original_target is not None else job.corrected_target_kbps,
        downscale_decision=DECISION_KEEP_ORIGINAL,
    )
    return new_job, new_plan


def precise_phase_event(
    batch_id: int,
    source_path: str,
    *,
    probe_index: int,
    probe_total: int,
    previous_quality: float,
    current_quality: float = 0.0,
    reason: str = "",
    resolution: str = "",
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
            precise_resolution=resolution,
        ),
    )


@dataclass(frozen=True, slots=True)
class PreciseTuneOutcome:
    """정밀 결정 결과와 인코딩 계획에 반영할 품질값, 해상도 판정, 결과 사유 줄

    - decision이 DECISION_KEEP_ORIGINAL이면 호출자가 출력 해상도와 최대 비트레이트를 원본 규격으로 바꿈
    - maxrate_kbps는 원본 규격에서 적용할 최대 비트레이트(상한이 없으면 None)
    """

    result: TuneResult
    previous_quality: float
    adopted_quality: float
    reason: str
    decision: str = ""
    maxrate_kbps: int | None = None


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
        compare: CompareCall | None = None,
        delta_max: float | None = None,
    ) -> None:
        self._ffmpeg: str = ffmpeg_exec
        self._factory: ProcessFactory = process_factory
        self._abort: threading.Event = abort
        self._control: PauseControl | None = control
        self._emit: EventSink | None = emit
        self._cache: SampleTunerCache = cache if cache is not None else open_default_cache()
        self._tune: TuneCall = tune if tune is not None else tune_quality_cached
        self._compare: CompareCall = compare if compare is not None else compare_downscale_cached
        self._delta_max: float | None = delta_max

    def __call__(self, batch_id: int, job: EncodeJob, plan: EncodePlan) -> PreciseTuneOutcome | None:
        """정밀 결정을 실행. 취소로 중단했으면 None"""
        source = job.source_path
        previous_quality = job.quality
        tune_job = TuneJob(
            source_path=source,
            source_size=job.source_size,
            source_mtime_ns=job.source_mtime_ns,
            source_duration_sec=job.source_duration_sec,
            encoder=plan.encoder,
            baseline_quality=job.quality if job.baseline_quality is None else job.baseline_quality,
            width=job.source_width or 0,
            height=job.source_height or 0,
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
        decision = ""
        maxrate_kbps: int | None = None
        if should_compare_downscale(
            job.precise_tune,
            job.manual_downscale,
            plan.output_size is not None,
            bool(job.downscale_decision),
        ):
            comparison = self._compare_resolution(job, plan, tune_job, result, adopted)
            if self._abort.is_set():
                return None
            if comparison is not None:
                decision = DECISION_KEEP_DOWNSCALE if comparison.use_downscale else DECISION_KEEP_ORIGINAL
                reason = f"{reason} / {format_comparison_reason(comparison)}"
                if decision == DECISION_KEEP_ORIGINAL:
                    maxrate_kbps = original_resolution_maxrate_kbps(
                        job.original_target_kbps,
                        job.source_width or 0,
                        job.source_height or 0,
                    )
        self._notify(
            batch_id, source, previous_quality, len(result.probes), TUNE_MAX_PROBES,
            current_quality=adopted, reason=reason, resolution=decision,
        )
        return PreciseTuneOutcome(
            result=result,
            previous_quality=previous_quality,
            adopted_quality=adopted,
            reason=reason,
            decision=decision,
            maxrate_kbps=maxrate_kbps,
        )

    def _compare_resolution(
        self,
        job: EncodeJob,
        plan: EncodePlan,
        tune_job: TuneJob,
        result: TuneResult,
        adopted: float,
    ) -> DownscaleComparison | None:
        """축소 후보와 원본 규격을 같은 구간 표본으로 비교. 비교할 근거가 없으면 None

        - 원본 옵션은 crop만 남기고 scale과 축소 상한을 뺀 계획이며, 품질값은 판정이 정한 보호 값(job.quality)
        - 축소 옵션은 정밀 탐색이 이미 잰 프로브를 그대로 넘겨 추가 샘플 인코딩 없이 비교
        - 정밀 탐색이 값을 정하지 못했으면(미수렴, 측정 불가) 비교도 근거가 없으므로 계획을 그대로 둠
        """
        if not result.converged:
            return None
        probe = result.best_probe or QualityProbe(quality=adopted)
        if not probe.measurable:
            return None
        original_plan = original_resolution_plan(
            plan,
            job,
            original_resolution_maxrate_kbps(
                job.original_target_kbps,
                job.source_width or 0,
                job.source_height or 0,
            ),
        )
        return self._compare(
            job=tune_job,
            candidate_plan=plan,
            original_plan=original_plan,
            candidate_probe=probe,
            original_quality=job.quality,
            cache=self._cache,
            process_factory=self._factory,
            ffmpeg_exec=self._ffmpeg,
            cancel_event=self._abort,
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
        resolution: str = "",
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
                resolution=resolution,
            )
        )
