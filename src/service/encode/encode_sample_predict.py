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
    estimate_full_video_bytes,
    resolve_video_stream_bytes,
    sample_segments,
)
from src.core.encode.stream_layout import StreamLayout
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


@dataclass(frozen=True, slots=True)
class SampleSizePrediction:
    """샘플 구간으로 예측한 전체 영상 스트림과 파일 크기, 원본 대비 초과 여부"""

    predicted_video_bytes: int
    predicted_total_bytes: int
    source_size_bytes: int
    exceeds_original: bool
    sample_duration_sec: float
    succeeded_segments: int
    total_segments: int


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
    )
