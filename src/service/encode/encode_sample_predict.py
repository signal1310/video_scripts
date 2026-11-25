"""절약 목적 영상의 샘플 크기 예측 서비스(결정 29 B)

- 결정 33과 같은 샘플 엔진(5지점 x 2초, 본 인코딩 인자)으로 전체 크기를 예측
- 예측이 원본의 여유 배수를 넘으면 본 인코딩을 건너뛰게 하고 예측 크기를 돌려줌
- 샘플 실패, 유효 구간 0개, 취소는 보수적으로 처리해 오제외를 막음
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass

from src.core.encode.encode_plan import EncodePlan
from src.core.encode.quality_tuning import (
    SAMPLE_SAVING_DISCARD_FACTOR,
    adjust_saving_quality,
    estimate_full_video_bytes,
    resolve_video_stream_bytes,
    sample_segments,
)
from src.core.encode.stream_layout import StreamLayout
from src.core.encoder_specs import get_encoder_spec
from src.core.quality_target import PROTECT_CQ_DELTA_MAX, quality_size_factor
from src.service.encode.encode_events import (
    EVENT_PHASE,
    PHASE_SAMPLE_PREDICT,
    EncodeEvent,
    EventSink,
    TuningPhase,
)
from src.service.encode.encode_job import EncodeJob
from src.service.encode.encode_pause import PauseControl
from src.service.encode.encode_ports import ProcessFactory
from src.service.encode.encode_sample_engine import run_sample_pass

# - 5지점 x 2초 샘플의 14% 편차를 감안한 보수적 제외 기준(초기값). 예측 전체 크기가 이 배수 이상이면 폐기
SAMPLE_TEMP_PREFIX: str = "d29_sample_"
BITS_PER_KBIT: int = 1000
BITS_PER_BYTE: int = 8


@dataclass(frozen=True, slots=True)
class SampleSizePrediction:
    """샘플 구간으로 예측한 전체 영상 스트림과 파일 크기, 원본 대비 초과 여부

    - adjusted_quality가 있으면 예측 비트레이트가 목표를 넘어 올린 품질값이며, 예측 크기는 그 품질값 기준으로 다시 계산한 값
    """

    predicted_video_bytes: int
    predicted_total_bytes: int
    source_size_bytes: int
    exceeds_original: bool
    sample_duration_sec: float
    succeeded_segments: int
    total_segments: int
    adjusted_quality: float | None = None


def _sample_phase_emitter(
    emit: EventSink | None,
    batch_id: int,
    source_path: str,
    quality: float,
    segment_total: int,
) -> Callable[[int], None] | None:
    """구간 완료 수를 샘플 확인 단계 이벤트로 바꾸는 콜백, emit이 없으면 None"""
    if emit is None:
        return None

    def _on_segment(done: int) -> None:
        emit(
            EncodeEvent(
                kind=EVENT_PHASE,
                batch_id=batch_id,
                source_path=source_path,
                phase=TuningPhase(
                    stage=PHASE_SAMPLE_PREDICT,
                    segment_done=done,
                    segment_total=segment_total,
                    baseline_quality=quality,
                    current_quality=quality,
                ),
            )
        )

    return _on_segment


def predict_saving_size(
    *,
    batch_id: int,
    job: EncodeJob,
    plan: EncodePlan,
    layout: StreamLayout,
    ffmpeg_exec: str,
    process_factory: ProcessFactory,
    abort: threading.Event,
    build_args: Callable[..., list[str]],
    control: PauseControl | None = None,
    emit: EventSink | None = None,
    discard_factor: float = SAMPLE_SAVING_DISCARD_FACTOR,
) -> SampleSizePrediction | None:
    """절약 목적 영상의 샘플 크기를 예측. 취소되면 None

    - 성공 구간이 하나라도 모자라면 예측을 믿지 않고 원본 크기를 그대로 돌려 초과 판정을 하지 않음
    - 예측 전체 크기가 원본 x 중단 기준 배수 이상이면 exceeds_original 참
    - emit이 있으면 구간 완료마다 사전 예측 단계 이벤트를 보내 진행 창에 표시
    """
    segments = sample_segments(job.source_duration_sec)
    sample_duration = sum(segment[1] for segment in segments)
    video_bytes = resolve_video_stream_bytes(
        layout,
        source_size_bytes=job.source_size,
        duration_sec=job.source_duration_sec,
        fallback_video_mb=job.fallback_video_mb,
    )
    other_bytes = max(0, job.source_size - video_bytes)
    on_segment = _sample_phase_emitter(emit, batch_id, job.source_path, job.quality, len(segments))
    # - 첫 구간이 끝나기 전에도 진행 창에 샘플 단계와 0/전체를 표시
    if on_segment is not None:
        on_segment(0)
    outcome = run_sample_pass(
        batch_id=batch_id,
        source_path=job.source_path,
        plan=plan,
        ffmpeg_exec=ffmpeg_exec,
        process_factory=process_factory,
        abort=abort,
        segments=segments,
        quality=job.quality,
        build_args=build_args,
        control=control,
        on_segment_done=on_segment,
        temp_prefix=SAMPLE_TEMP_PREFIX,
    )
    if outcome.cancelled:
        return None
    if outcome.succeeded_segments == 0 or outcome.succeeded_segments < outcome.segment_count:
        return SampleSizePrediction(
            predicted_video_bytes=video_bytes,
            predicted_total_bytes=job.source_size,
            source_size_bytes=job.source_size,
            exceeds_original=False,
            sample_duration_sec=sample_duration,
            succeeded_segments=outcome.succeeded_segments,
            total_segments=outcome.segment_count,
        )
    predicted_video = estimate_full_video_bytes(
        outcome.total_bytes, sample_duration, job.source_duration_sec
    )
    adjusted_quality, predicted_video = adjust_prediction_quality(
        job=job,
        encoder=plan.encoder,
        predicted_video_bytes=predicted_video,
        # - 상한은 실제 기준 CQ 기준. 품질값은 저화질 보호 오프셋만큼 기준보다 높을 수 있음
        baseline_quality=job.quality if job.baseline_quality is None else job.baseline_quality,
    )
    predicted_total = other_bytes + predicted_video
    exceeds = predicted_total >= round(job.source_size * discard_factor)
    return SampleSizePrediction(
        predicted_video_bytes=predicted_video,
        predicted_total_bytes=predicted_total,
        source_size_bytes=job.source_size,
        exceeds_original=exceeds,
        sample_duration_sec=sample_duration,
        succeeded_segments=outcome.succeeded_segments,
        total_segments=outcome.segment_count,
        adjusted_quality=adjusted_quality,
    )


def predicted_video_kbps(predicted_video_bytes: int, duration_sec: float) -> float:
    """예측 영상 스트림 바이트를 길이로 나눈 비트레이트(kbps) 반환. 길이를 모르면 0"""
    if predicted_video_bytes <= 0 or duration_sec <= 0:
        return 0.0
    return predicted_video_bytes * BITS_PER_BYTE / BITS_PER_KBIT / duration_sec


def adjust_prediction_quality(
    *,
    job: EncodeJob,
    encoder: str,
    predicted_video_bytes: int,
    baseline_quality: float,
    delta_max: float = PROTECT_CQ_DELTA_MAX,
) -> tuple[float | None, int]:
    """예측 비트레이트가 보정 목표의 허용 배수를 넘으면 올릴 품질값과 그 품질값의 예측 크기 반환

    - 보정 목표를 모르는 이전 작업 기록은 조정하지 않고 예측 크기만 그대로 돌려줌
    - 추가 샘플 없이 같은 유효 지수 K로 올릴 품질값을 구하고, 용량 계수로 예측 크기를 다시 환산
    """
    target = job.corrected_target_kbps
    if target is None:
        return None, predicted_video_bytes
    kbps = predicted_video_kbps(predicted_video_bytes, job.source_duration_sec)
    spec = get_encoder_spec(encoder)
    new_quality = adjust_saving_quality(
        quality=job.quality,
        predicted_video_kbps=kbps,
        corrected_target_kbps=target,
        baseline_quality=baseline_quality,
        encoder=encoder,
        integer_scale=spec is not None and spec.integer_scale,
        delta_max=delta_max,
    )
    if new_quality is None:
        return None, predicted_video_bytes
    scaled = round(
        predicted_video_bytes * quality_size_factor(new_quality - job.quality, encoder=encoder)
    )
    return new_quality, max(1, scaled)
