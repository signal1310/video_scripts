"""최적 비트레이트, 화질 등급, 인코딩 작업량 지표 계산 모듈

- 판정 엔진(evaluator)과 출력 규격 계산(downscale_resolver)이 공유하는 순수 함수 모음
- 두 모듈이 이 모듈을 가리키도록 해 순환 import를 막음
"""

from src.core.config import (
    BitrateModelConfig,
    ClassifierConfig,
    CodecWeightsConfig,
    ThresholdsConfig,
)
from src.core.encoder_specs import get_encode_codec_family
from src.core.models import QualityZone, VideoProps
from src.core.quality_target import BASELINE_CQ_DEFAULT, calc_base_bitrate_kbps

MIN_OPTIMAL_BITRATE_KBPS: int = 100
# - 기준 FPS 근처의 미세 오차로 보정이 걸리지 않도록 두는 불감대(dead band) 여유치
FPS_HYSTERESIS_TOLERANCE: float = 2.0
REFERENCE_ENCODE_WIDTH: int = 1920
REFERENCE_ENCODE_HEIGHT: int = 1080
REFERENCE_ENCODE_FPS: float = 30.0
MIN_ENCODE_SPEED_FACTOR: float = 0.01
# - 아래 판정 경계는 실측값이 아닌 정책값이며, 샘플 인코딩 기반 보정(TODO.md 2절) 전까지 유지
# - 동급 이상 코덱 재인코딩은 세대 손실이 있으므로 출력 BR 비율 1.35(최소 약 26% 절약)부터 과화질로 봄
SAME_OR_BETTER_CODEC_OVER_MARGIN: float = 0.15
# - 8-bit 출력 프리셋으로 재인코딩할 때 컬러 밴딩 위험을 덜기 위한 보수적 여유치(미실측)
TEN_BIT_SDR_OVER_MARGIN: float = 0.10


def _calc_resolution_fps_factors(
    width: int,
    height: int,
    fps: float,
    bitrate_model: BitrateModelConfig,
) -> tuple[float, float]:
    """해상도 면적 비율과 FPS 불감대를 반영한 비선형 가중 계수(area_factor, fps_factor) 산출"""
    base_pixels = max(bitrate_model.base_width * bitrate_model.base_height, 1)
    curr_pixels = max(width * height, 1)
    area_factor: float = (
        curr_pixels / base_pixels
    ) ** bitrate_model.area_scaling_exponent

    if fps > bitrate_model.base_fps + FPS_HYSTERESIS_TOLERANCE:
        fps_factor: float = (
            fps / bitrate_model.base_fps
        ) ** bitrate_model.fps_scaling_exponent
    else:
        fps_factor = 1.0
    return area_factor, fps_factor


def calc_optimal_bitrate(
    width: int,
    height: int,
    bitrate_model: BitrateModelConfig,
    codec_weights: CodecWeightsConfig,
    fps: float = 30.0,
    codec: str = "h264",
    baseline_cq: float = BASELINE_CQ_DEFAULT,
    encoder: str | None = None,
) -> int:
    """해상도, FPS, 코덱 효율을 반영한 기준 품질값(baseline_cq) 수준의 최적 비트레이트(kbps) 산출

    수식: B_opt(W, H, fps, codec, Q) = B_base(Q) * ((W * H) / (W_base * H_base))^alpha * K_fps * C_codec
    - encoder를 주면 B_base(Q)의 K를 그 인코더 실측값으로 계산(기본 기준 품질값에서는 차이 없음)
    """
    area_factor, fps_factor = _calc_resolution_fps_factors(
        width, height, fps, bitrate_model
    )
    codec_factor: float = codec_weights.get_weight(codec)
    base_kbps = calc_base_bitrate_kbps(
        bitrate_model.base_target_kbps, baseline_cq, encoder=encoder
    )

    optimal_kbps = round(base_kbps * area_factor * fps_factor * codec_factor)
    return max(optimal_kbps, MIN_OPTIMAL_BITRATE_KBPS)


def calc_encode_workload_ratio(width: int, height: int, fps: float) -> float:
    """1080p 30fps 대비 초당 처리 화소량의 상대 인코딩 작업량 배수 산출

    수식: W_enc = (W * H * fps) / (1920 * 1080 * 30)
    """
    reference_pixels_per_sec = (
        REFERENCE_ENCODE_WIDTH * REFERENCE_ENCODE_HEIGHT * REFERENCE_ENCODE_FPS
    )
    return (width * height * fps) / reference_pixels_per_sec


def estimate_encode_duration_sec(vid: VideoProps, config: ClassifierConfig) -> float:
    """화소 처리량 비례 작업량과 기준 인코딩 배속으로 절약 선별용 기준 예상 인코딩 시간(초) 산출"""
    if vid.duration <= 0:
        return 0.0
    workload_ratio = calc_encode_workload_ratio(vid.width, vid.height, vid.fps)
    speed = max(config.encode.encode_speed_factor, MIN_ENCODE_SPEED_FACTOR)
    return max(0.0, (vid.duration * workload_ratio) / speed)


def classify_quality_zone(
    bitrate_ratio: float, thresholds: ThresholdsConfig
) -> QualityZone:
    """최적 비트레이트 대비 비트레이트 비율(r = B_curr / B_opt)을 경계 배수와 비교해 화질 등급을 3구간으로 판정"""
    if bitrate_ratio < thresholds.low_bitrate_ratio:
        return QualityZone.LOW
    if bitrate_ratio > thresholds.over_bitrate_ratio:
        return QualityZone.OVER
    return QualityZone.OPTIMAL


def resolve_quality_zone(
    source_ratio: float,
    output_ratio: float,
    thresholds: ThresholdsConfig,
    *,
    source_weight: float | None = None,
    target_weight: float | None = None,
    is_10bit: bool = False,
    is_hdr: bool = False,
) -> QualityZone:
    """원본 코덱 기준 저화질을 먼저 판정하고, 나머지는 고성능 코덱/10-bit SDR 여유치를 반영해 과화질 여부 구분

    저화질 보호를 우선해 원본을 다시 압축하지 않도록 함
    """
    if classify_quality_zone(source_ratio, thresholds) == QualityZone.LOW:
        return QualityZone.LOW
    effective_over = thresholds.over_bitrate_ratio
    if (
        source_weight is not None
        and target_weight is not None
        and source_weight <= target_weight
    ):
        effective_over += SAME_OR_BETTER_CODEC_OVER_MARGIN
    if is_10bit and not is_hdr:
        effective_over += TEN_BIT_SDR_OVER_MARGIN
    if output_ratio > effective_over:
        return QualityZone.OVER
    return QualityZone.OPTIMAL


def calc_optimal_pair(vid: VideoProps, config: ClassifierConfig) -> tuple[int, int]:
    """원본 코덱 기준 최적 비트레이트와 목표 인코딩 코덱 기준 최적 비트레이트 쌍 산출"""
    source_kbps = calc_optimal_bitrate(
        vid.width,
        vid.height,
        config.bitrate_model,
        config.codec_weights,
        vid.fps,
        vid.codec,
        config.encode.baseline_cq,
    )
    target_kbps = calc_optimal_bitrate(
        vid.width,
        vid.height,
        config.bitrate_model,
        config.codec_weights,
        vid.fps,
        get_encode_codec_family(config.encode.encode_codec),
        config.encode.baseline_cq,
        encoder=config.encode.encode_codec,
    )
    return source_kbps, target_kbps
