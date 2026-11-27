"""키프레임 교정 영상의 품질값 자동 조정 순수 계산 및 상수 모듈(결정 33)

- 12.15절 실측 결과를 반영한 상수 초기값과 구간 배치, 기울기 역산, 재시도 판정 순수 함수만 포함
- 구간 배치 규칙은 `sample_points.py`가 기준이며 이 모듈은 결정 33 기본값으로 호출
- 프로세스 실행과 상태 저장은 서비스 계층(encode_quality_tuner)이 담당
"""

import math
from dataclasses import dataclass

from src.core.encode.encode_gate import GatePurpose
from src.core.encode.stream_layout import StreamLayout
from src.core.models import BYTES_PER_KB
from src.core.quality_target import (
    PROTECT_CQ_DELTA_MAX,
    QUALITY_BITRATE_K,
    effective_quality_bitrate_k,
    format_cq_value,
)
from src.core.quality_target import (
    snap_quality as _snap_quality,
)
from src.core.sample_points import (
    SAMPLE_SEGMENT_COUNT,
    SAMPLE_SEGMENT_DURATION_SEC,
)
from src.core.sample_points import (
    sample_segments as _sample_segments,
)

BYTES_PER_MB: int = BYTES_PER_KB * BYTES_PER_KB
BITS_PER_BYTE: int = 8

# - 12.15절 실측 확정 상수 초기값
SAMPLE_PARALLEL_WORKERS: int = 3
SAMPLE_PASS_FACTOR: float = 1.00
SAMPLE_TARGET_SAFETY_FACTOR: float = 0.95
SAMPLE_SECOND_PASS_MIN_DELTA_Q: float = 1.5
SAMPLE_K_MIN: float = 4.0
SAMPLE_K_MAX: float = 12.0
TUNE_RETRY_MIN_PROGRESS: float = 0.25
TUNE_RETRY_OVERRUN_FACTOR: float = 1.10
TUNE_RETRY_MIN_DELTA_Q: float = 0.4
TUNE_MAX_RETRIES: int = 1
QUALITY_DECIMAL_PLACES: int = 1
# - 절약 목적 샘플 예측에서 본 인코딩을 건너뛰는 기준(원본 크기 대비 배수). 설정값이 없을 때 쓰는 기본값
SAMPLE_SAVING_DISCARD_FACTOR: float = 1.05
# - 절약 목적 예측 비트레이트를 보정 목표의 이 배수까지 허용하고, 넘으면 품질값을 올림
SAMPLE_TARGET_TOLERANCE: float = 1.15


@dataclass(frozen=True, slots=True)
class KeyframeTargetSpec:
    """키프레임 교정 영상의 목표 크기와 비영상 스트림 분리 기준"""

    v_target_bytes: int
    s_other_bytes: int
    s_target_bytes: int


@dataclass(frozen=True, slots=True)
class SampleTuningResult:
    """샘플 단계가 고른 품질값, 예측 영상 크기, 실측 기울기, 조정 사유"""

    chosen_quality: float
    predicted_video_bytes: int
    slope_k: float
    passes_used: int
    passed_first: bool = False


def sample_segments(
    duration_sec: float,
    count: int = SAMPLE_SEGMENT_COUNT,
    segment_sec: float = SAMPLE_SEGMENT_DURATION_SEC,
) -> tuple[tuple[float, float], ...]:
    """결정 33 기본 구간(5지점 x 2초)으로 공통 구간 규칙을 적용. 20초 미만은 전체 1구간"""
    return _sample_segments(duration_sec, count, segment_sec)


def resolve_video_stream_bytes(
    layout: StreamLayout,
    *,
    source_size_bytes: int,
    duration_sec: float,
    fallback_video_mb: float | None,
) -> int:
    """StreamLayout의 주 영상 크기를 우선 사용하고, 없으면 분석 단계 추정치를 사용"""
    primary = layout.primary_video
    if primary is not None:
        if primary.stream_bytes is not None and primary.stream_bytes > 0:
            return min(source_size_bytes, primary.stream_bytes)
        if primary.bit_rate_bps is not None and primary.bit_rate_bps > 0 and duration_sec > 0:
            estimated = round(primary.bit_rate_bps * duration_sec / BITS_PER_BYTE)
            if estimated > 0:
                return min(source_size_bytes, estimated)
    if fallback_video_mb is not None and fallback_video_mb > 0:
        return min(source_size_bytes, round(fallback_video_mb * BYTES_PER_MB))
    return source_size_bytes


def compute_kf_target_bytes(
    *,
    source_size_bytes: int,
    video_stream_bytes: int,
    keyframe_increase_ratio: float,
) -> KeyframeTargetSpec:
    """비영상 스트림을 분리한 목표 영상 크기(V_target)와 전체 목표 크기(S_target) 계산"""
    v_target = max(1, round(video_stream_bytes * (1.0 + max(0.0, keyframe_increase_ratio))))
    s_other = max(0, source_size_bytes - video_stream_bytes)
    return KeyframeTargetSpec(
        v_target_bytes=v_target,
        s_other_bytes=s_other,
        s_target_bytes=s_other + v_target,
    )


def estimate_full_video_bytes(sample_bytes: int, sample_duration_sec: float, full_duration_sec: float) -> int:
    """샘플 출력 바이트 합계로 전체 영상 스트림 크기 추정"""
    if sample_bytes <= 0 or sample_duration_sec <= 0 or full_duration_sec <= 0:
        return 0
    return max(1, round(sample_bytes * (full_duration_sec / sample_duration_sec)))


def snap_quality(quality: float, *, q_min: float, q_max: float, integer_scale: bool) -> float:
    """품질값을 [q_min, q_max] 범위와 인코더 눈금(정수 또는 소수 첫째 자리)에 맞춤

    - 정의는 `quality_target.snap_quality` 한 곳을 씀(evaluation 영역과 공유)
    """
    return _snap_quality(quality, q_min=q_min, q_max=q_max, integer_scale=integer_scale)


def max_tuned_quality(
    baseline_quality: float,
    *,
    delta_max: float = PROTECT_CQ_DELTA_MAX,
) -> float:
    """키프레임 교정 품질값 상한(기준 + 설정된 자동 조절 한도)"""
    return round(baseline_quality + delta_max, QUALITY_DECIMAL_PLACES)


def choose_second_sample_quality(
    *,
    q1: float,
    q_max: float,
    v1_est_bytes: int,
    v_target_bytes: int,
    integer_scale: bool,
) -> float | None:
    """1차 샘플이 목표를 넘으면 2차 샘플 품질값을 반환. 통과했거나 상한이면 None"""
    if v1_est_bytes <= round(v_target_bytes * SAMPLE_PASS_FACTOR) or q1 >= q_max:
        return None
    v_aim = max(1.0, v_target_bytes * SAMPLE_TARGET_SAFETY_FACTOR)
    if v1_est_bytes <= v_aim:
        raw_q2 = q1 + SAMPLE_SECOND_PASS_MIN_DELTA_Q
    else:
        raw_q2 = q1 + QUALITY_BITRATE_K * math.log2(v1_est_bytes / v_aim)
    lower = min(q1 + SAMPLE_SECOND_PASS_MIN_DELTA_Q, q_max)
    q2 = snap_quality(raw_q2, q_min=lower, q_max=q_max, integer_scale=integer_scale)
    return q2 if q2 > q1 else None


def solve_final_sample_quality(
    *,
    q1: float,
    v1_est_bytes: int,
    q2: float,
    v2_est_bytes: int,
    v_target_bytes: int,
    q_min: float,
    q_max: float,
    integer_scale: bool,
) -> tuple[float, float]:
    """두 샘플 결과로 영상별 기울기 K(클램핑)와 최종 품질값을 계산"""
    slope_k = QUALITY_BITRATE_K
    if q2 > q1 and v2_est_bytes > 0 and v1_est_bytes > v2_est_bytes:
        measured_k = (q2 - q1) / math.log2(v1_est_bytes / v2_est_bytes)
        slope_k = min(max(measured_k, SAMPLE_K_MIN), SAMPLE_K_MAX)
    v_aim = max(1.0, v_target_bytes * SAMPLE_TARGET_SAFETY_FACTOR)
    anchor_q, anchor_bytes = (q2, v2_est_bytes) if v2_est_bytes > 0 else (q1, v1_est_bytes)
    if anchor_bytes <= 0:
        return snap_quality(anchor_q, q_min=q_min, q_max=q_max, integer_scale=integer_scale), slope_k
    raw_q = anchor_q + slope_k * math.log2(anchor_bytes / v_aim)
    return snap_quality(raw_q, q_min=q_min, q_max=q_max, integer_scale=integer_scale), slope_k


def compute_retry_quality(
    *,
    current_q: float,
    q_max: float,
    estimated_total_bytes: int,
    target_total_bytes: int,
    slope_k: float,
    integer_scale: bool,
) -> float | None:
    """본 인코딩 중 추정 크기가 허용 배수를 넘었을 때 재시도 품질값을 반환. 조건 미달이면 None"""
    if current_q >= q_max or target_total_bytes <= 0:
        return None
    if estimated_total_bytes <= round(target_total_bytes * TUNE_RETRY_OVERRUN_FACTOR):
        return None
    v_aim = max(1.0, target_total_bytes * SAMPLE_TARGET_SAFETY_FACTOR)
    raw_q = current_q + slope_k * math.log2(estimated_total_bytes / v_aim)
    new_q = snap_quality(raw_q, q_min=current_q, q_max=q_max, integer_scale=integer_scale)
    if new_q - current_q < TUNE_RETRY_MIN_DELTA_Q:
        return None
    return new_q


def adjust_saving_quality(
    *,
    quality: float,
    predicted_video_kbps: float,
    corrected_target_kbps: float,
    baseline_quality: float,
    encoder: str | None = None,
    integer_scale: bool = False,
    delta_max: float = PROTECT_CQ_DELTA_MAX,
) -> float | None:
    """절약 목적 샘플 예측 비트레이트가 허용 배수를 넘으면 올릴 품질값 반환. 조정할 필요가 없으면 None

    - 목표는 보정 목표 비트레이트 x SAMPLE_TARGET_TOLERANCE이며, 넘은 만큼 유효 지수 K로 품질값을 올림
    - 올린 폭이 TUNE_RETRY_MIN_DELTA_Q 미만이면 샘플 편차와 구분되지 않으므로 조정하지 않음
    - 상한은 기준 품질값 + delta_max이므로 저화질 보호 품질값을 넘기지 않음
    """
    aim = corrected_target_kbps * SAMPLE_TARGET_TOLERANCE
    if aim <= 0 or predicted_video_kbps <= aim:
        return None
    raw = quality + effective_quality_bitrate_k(encoder) * math.log2(predicted_video_kbps / aim)
    new_quality = snap_quality(
        raw,
        q_min=quality,
        q_max=round(baseline_quality + delta_max, QUALITY_DECIMAL_PLACES),
        integer_scale=integer_scale,
    )
    if new_quality - quality < TUNE_RETRY_MIN_DELTA_Q:
        return None
    return new_quality


def format_saving_adjustment(baseline_quality: float, tuned_quality: float) -> str:
    """절약 목적 사전 예측에서 품질값을 올린 결과 사유 줄 문구"""
    return (
        "품질값 자동 조정: "
        f"{format_cq_value(baseline_quality)} -> {format_cq_value(tuned_quality)} (사전 예측 초과)"
    )


def format_tuned_reason(
    *,
    baseline_q: float,
    sample_q: float,
    final_q: float,
    retry_count: int,
    passed_first: bool,
    purpose: GatePurpose = GatePurpose.KEYFRAME_FIX,
) -> str:
    """결과 목록 사유 줄 문구. 기준 그대로 유지한 경우와 조정된 경우를 구분

    - 기준을 유지한 마지막 사유는 목적에 맞춰 씀. 키프레임 교정만 기준 품질값을 우선한 것이므로
      변환 우선과 호환 인코딩은 목적을 드러내지 않는 중립 문구를 씀
    """
    base_txt = format_cq_value(baseline_q)
    final_txt = format_cq_value(final_q)
    if retry_count > 0:
        sample_txt = format_cq_value(sample_q)
        if sample_q > baseline_q:
            return f"품질값 자동 조정: {base_txt} -> {sample_txt} -> {final_txt} (본 인코딩 재시도 {retry_count}회)"
        return f"품질값 자동 조정: {base_txt} -> {final_txt} (본 인코딩 재시도 {retry_count}회)"
    if final_q > baseline_q:
        return f"품질값 자동 조정: {base_txt} -> {final_txt} (품질 시험)"
    if passed_first:
        return f"품질값 {base_txt} 유지 (1차 시험 통과)"
    if purpose is GatePurpose.KEYFRAME_FIX:
        return f"품질값 {base_txt} 유지 (키프레임 인코딩 우선)"
    return f"품질값 {base_txt} 유지 (품질 시험)"
