"""키프레임 교정 영상의 샘플 구간 시험 인코딩 및 본 인코딩 재시도 서비스(결정 33)

- `encode_service`에서 분리해 책임 비대화를 막고, 워커 스레드에서 Qt 객체에 접근하지 않음
- 샘플 인코딩은 `build_sample_ffmpeg_args`로 본 인코딩 인자를 재사용하고 임시 폴더에서 정리
"""

import threading
from collections.abc import Callable
from dataclasses import replace

from src.core.encode.encode_plan import EncodePlan
from src.core.encode.encode_progress import projected_output_bytes
from src.core.encode.quality_tuning import (
    TUNE_MAX_RETRIES,
    TUNE_RETRY_MIN_PROGRESS,
    KeyframeTargetSpec,
    SampleTuningResult,
    choose_second_sample_quality,
    compute_kf_target_bytes,
    compute_retry_quality,
    estimate_full_video_bytes,
    format_tuned_reason,
    max_tuned_quality,
    resolve_video_stream_bytes,
    sample_segments,
    solve_final_sample_quality,
)
from src.core.encode.stream_layout import StreamLayout
from src.core.encode_tag import with_tag_quality
from src.core.encoder_specs import get_encoder_spec
from src.core.quality_target import QUALITY_BITRATE_K
from src.service.encode.encode_events import (
    EVENT_PHASE,
    EVENT_PROGRESS,
    PHASE_MAIN,
    PHASE_RETRY,
    PHASE_SAMPLING,
    EncodeEvent,
    EventSink,
    TuningPhase,
)
from src.service.encode.encode_job import EncodeJob
from src.service.encode.encode_outcome import EncodeAttempt
from src.service.encode.encode_pause import PauseControl
from src.service.encode.encode_ports import ProcessFactory
from src.service.encode.encode_sample_engine import run_sample_pass

RunMainAttempt = Callable[[EncodePlan, "RetryWatch | None"], EncodeAttempt]
RetryRequest = Callable[[float], bool]


class RetryWatch:
    """키프레임 교정 본 인코딩 중 out_time 기준 예상 크기를 감시해 1회 재시도를 요청하는 싱크"""

    def __init__(
        self,
        *,
        forward: EventSink,
        duration_sec: float,
        current_q: float,
        q_max: float,
        target_total_bytes: int,
        slope_k: float,
        integer_scale: bool,
        request_retry: RetryRequest,
    ) -> None:
        self._forward: EventSink = forward
        self._duration_sec: float = duration_sec
        self._current_q: float = current_q
        self._q_max: float = q_max
        self._target_total_bytes: int = target_total_bytes
        self._slope_k: float = slope_k
        self._integer_scale: bool = integer_scale
        self._request_retry: RetryRequest = request_retry
        self._triggered: bool = False
        self.retry_quality: float | None = None

    def __call__(self, event: EncodeEvent) -> None:
        """진행 이벤트를 전달하면서 재시도 조건을 확인"""
        self._forward(event)
        if self._triggered or event.kind != EVENT_PROGRESS or event.sample is None:
            return
        sample = event.sample
        if sample.out_time_sec is None or self._duration_sec <= 0:
            return
        progress = sample.out_time_sec / self._duration_sec
        if progress < TUNE_RETRY_MIN_PROGRESS:
            return
        projected = projected_output_bytes(sample, self._duration_sec)
        if projected is None:
            return
        new_q = compute_retry_quality(
            current_q=self._current_q,
            q_max=self._q_max,
            estimated_total_bytes=projected,
            target_total_bytes=self._target_total_bytes,
            slope_k=self._slope_k,
            integer_scale=self._integer_scale,
        )
        if new_q is None:
            return
        if self._request_retry(new_q):
            self._triggered = True
            self.retry_quality = new_q


def _run_sample_pass(
    *,
    batch_id: int,
    job: EncodeJob,
    plan: EncodePlan,
    ffmpeg_exec: str,
    process_factory: ProcessFactory,
    abort: threading.Event,
    emit: EventSink,
    segments: tuple[tuple[float, float], ...],
    quality: float,
    sample_pass: int,
    sample_total: int,
    build_args: Callable[..., list[str]],
    control: PauseControl | None = None,
) -> int | None:
    """대표 구간들을 병렬로 인코딩하고 출력 바이트 합계를 반환. 취소되면 None"""
    total_segs = len(segments)

    def _emit_phase(segment_done: int) -> None:
        emit(
            EncodeEvent(
                kind=EVENT_PHASE,
                batch_id=batch_id,
                source_path=job.source_path,
                phase=TuningPhase(
                    stage=PHASE_SAMPLING,
                    sample_pass=sample_pass,
                    sample_total=sample_total,
                    segment_done=segment_done,
                    segment_total=total_segs,
                    baseline_quality=job.quality,
                    current_quality=quality,
                ),
            )
        )

    _emit_phase(0)
    outcome = run_sample_pass(
        batch_id=batch_id,
        source_path=job.source_path,
        plan=plan,
        ffmpeg_exec=ffmpeg_exec,
        process_factory=process_factory,
        abort=abort,
        segments=segments,
        quality=quality,
        build_args=build_args,
        control=control,
        on_segment_done=_emit_phase,
        temp_prefix="d33_sample_",
    )
    return None if outcome.cancelled else outcome.total_bytes


def run_sample_tuning(
    *,
    batch_id: int,
    job: EncodeJob,
    plan: EncodePlan,
    layout: StreamLayout,
    ffmpeg_exec: str,
    process_factory: ProcessFactory,
    abort: threading.Event,
    emit: EventSink,
    build_args: Callable[..., list[str]],
    control: PauseControl | None = None,
) -> tuple[SampleTuningResult, KeyframeTargetSpec] | None:
    """키프레임 교정 영상의 1~2차 샘플 확인을 수행해 목표 품질값과 목표 크기를 반환. 취소되면 None"""
    v_orig = resolve_video_stream_bytes(
        layout,
        source_size_bytes=job.source_size,
        duration_sec=job.source_duration_sec,
        fallback_video_mb=job.fallback_video_mb,
    )
    target_spec = compute_kf_target_bytes(
        source_size_bytes=job.source_size,
        video_stream_bytes=v_orig,
        keyframe_increase_ratio=job.keyframe_increase_ratio,
    )
    segments = sample_segments(job.source_duration_sec)
    sample_duration = sum(seg[1] for seg in segments)
    spec = get_encoder_spec(plan.encoder)
    integer_scale = False if spec is None else spec.integer_scale
    q_min = job.quality
    q_max = max_tuned_quality(job.quality)

    bytes_1 = _run_sample_pass(
        batch_id=batch_id,
        job=job,
        plan=plan,
        ffmpeg_exec=ffmpeg_exec,
        process_factory=process_factory,
        abort=abort,
        emit=emit,
        segments=segments,
        quality=q_min,
        sample_pass=1,
        sample_total=2,
        build_args=build_args,
        control=control,
    )
    if bytes_1 is None:
        return None
    if bytes_1 <= 0:
        return (
            SampleTuningResult(
                chosen_quality=q_min,
                predicted_video_bytes=v_orig,
                slope_k=QUALITY_BITRATE_K,
                passes_used=0,
                passed_first=True,
            ),
            target_spec,
        )

    v1_est = estimate_full_video_bytes(bytes_1, sample_duration, job.source_duration_sec)
    q2 = choose_second_sample_quality(
        q1=q_min,
        q_max=q_max,
        v1_est_bytes=v1_est,
        v_target_bytes=target_spec.v_target_bytes,
        integer_scale=integer_scale,
    )
    if q2 is None:
        return (
            SampleTuningResult(
                chosen_quality=q_min,
                predicted_video_bytes=v1_est,
                slope_k=QUALITY_BITRATE_K,
                passes_used=1,
                passed_first=True,
            ),
            target_spec,
        )

    bytes_2 = _run_sample_pass(
        batch_id=batch_id,
        job=job,
        plan=plan,
        ffmpeg_exec=ffmpeg_exec,
        process_factory=process_factory,
        abort=abort,
        emit=emit,
        segments=segments,
        quality=q2,
        sample_pass=2,
        sample_total=2,
        build_args=build_args,
        control=control,
    )
    if bytes_2 is None:
        return None
    v2_est = estimate_full_video_bytes(bytes_2, sample_duration, job.source_duration_sec)
    final_q, slope_k = solve_final_sample_quality(
        q1=q_min,
        v1_est_bytes=v1_est,
        q2=q2,
        v2_est_bytes=v2_est,
        v_target_bytes=target_spec.v_target_bytes,
        q_min=q_min,
        q_max=q_max,
        integer_scale=integer_scale,
    )
    return (
        SampleTuningResult(
            chosen_quality=final_q,
            predicted_video_bytes=v2_est if v2_est > 0 else v1_est,
            slope_k=slope_k,
            passes_used=2,
            passed_first=False,
        ),
        target_spec,
    )


def tune_and_encode(
    *,
    batch_id: int,
    job: EncodeJob,
    plan: EncodePlan,
    layout: StreamLayout,
    ffmpeg_exec: str,
    process_factory: ProcessFactory,
    abort: threading.Event,
    emit: EventSink,
    build_args: Callable[..., list[str]],
    run_main: RunMainAttempt,
    cancel_for_retry: Callable[[], bool],
    remove_part: Callable[[str], None],
    control: PauseControl | None = None,
) -> tuple[EncodePlan, EncodeAttempt]:
    """키프레임 교정 영상의 샘플 확인, 본 인코딩, 목표 초과 시 1회 재시도까지 수행"""
    tuned = run_sample_tuning(
        batch_id=batch_id,
        job=job,
        plan=plan,
        layout=layout,
        ffmpeg_exec=ffmpeg_exec,
        process_factory=process_factory,
        abort=abort,
        emit=emit,
        build_args=build_args,
        control=control,
    )
    if tuned is None:
        return plan, EncodeAttempt(cancelled=True)
    sample_res, target_spec = tuned
    sample_q = sample_res.chosen_quality
    active_plan = replace(plan, quality=sample_q, tag_value=with_tag_quality(plan.tag_value, sample_q))
    emit(
        EncodeEvent(
            kind=EVENT_PHASE,
            batch_id=batch_id,
            source_path=job.source_path,
            phase=TuningPhase(
                stage=PHASE_MAIN,
                baseline_quality=job.quality,
                current_quality=sample_q,
            ),
        )
    )

    spec = get_encoder_spec(plan.encoder)
    integer_scale = False if spec is None else spec.integer_scale
    q_max = max_tuned_quality(job.quality)
    retry_count = 0

    for _ in range(TUNE_MAX_RETRIES + 1):
        pending_retry_q: list[float] = []

        def _request_retry(new_q: float, _box: list[float] = pending_retry_q) -> bool:
            if not cancel_for_retry():
                return False
            _box.append(new_q)
            return True

        watch = (
            RetryWatch(
                forward=emit,
                duration_sec=job.source_duration_sec,
                current_q=active_plan.quality,
                q_max=q_max,
                target_total_bytes=target_spec.s_target_bytes,
                slope_k=sample_res.slope_k,
                integer_scale=integer_scale,
                request_retry=_request_retry,
            )
            if retry_count < TUNE_MAX_RETRIES
            else None
        )
        attempt = run_main(active_plan, watch)
        if pending_retry_q and not abort.is_set():
            prev_q = active_plan.quality
            next_q = pending_retry_q[0]
            remove_part(active_plan.part_path)
            retry_count += 1
            active_plan = replace(active_plan, quality=next_q, tag_value=with_tag_quality(plan.tag_value, next_q))
            emit(
                EncodeEvent(
                    kind=EVENT_PHASE,
                    batch_id=batch_id,
                    source_path=job.source_path,
                    phase=TuningPhase(
                        stage=PHASE_RETRY,
                        baseline_quality=job.quality,
                        current_quality=next_q,
                        previous_quality=prev_q,
                        retry_count=retry_count,
                    ),
                )
            )
            continue
        reason = format_tuned_reason(
            baseline_q=job.quality,
            sample_q=sample_q,
            final_q=active_plan.quality,
            retry_count=retry_count,
            passed_first=sample_res.passed_first,
        )
        return active_plan, replace(
            attempt,
            tuned_quality=active_plan.quality,
            sample_predicted_bytes=sample_res.predicted_video_bytes,
            tune_retry_count=retry_count,
            tuning_reason=reason,
        )

    return active_plan, EncodeAttempt(cancelled=True)
