"""영상별 출력 해상도 후보를 판정해 축소 추정 또는 축소하지 않은 이유를 만드는 모듈"""

from dataclasses import replace

from src.core.config import ClassifierConfig
from src.core.encoder_specs import get_encode_codec_family, resolve_level_cap_kbps
from src.core.evaluation.classification_policy import DownscaleEstimate, should_use_low_quality_preset
from src.core.evaluation.quality_metrics import (
    calc_optimal_pair,
    estimate_encode_duration_sec,
    resolve_quality_zone,
)
from src.core.evaluation.size_estimator import combine_video_caps_kbps, estimate_after_size_mb
from src.core.models import DownscaleSkipReason, VideoProps
from src.core.resolution_tiers import (
    KEEP_RESOLUTION_KEY,
    RESOLUTION_TIER_BY_KEY,
    RESOLUTION_TIERS,
    ResolutionTier,
    calc_scaled_dimensions,
    find_source_tier,
    is_meaningful_downscale,
    orientation_of,
)
from src.core.smart_action import DownscaleReason


def _calc_source_ratio_at_tier(
    vid: VideoProps, config: ClassifierConfig, tier: ResolutionTier
) -> float:
    """규격 출력 크기에서 원본 FPS, 원본 코덱 기준 비트레이트 비율(r_tier) 산출"""
    out_w, out_h = calc_scaled_dimensions(vid.width, vid.height, tier)
    source_opt_kbps, _ = calc_optimal_pair(replace(vid, width=out_w, height=out_h), config)
    return vid.vid_kbps / source_opt_kbps


def _select_bitrate_fit_tier(
    vid: VideoProps, config: ClassifierConfig, original_ratio: float
) -> ResolutionTier | None:
    """비트레이트 맞춤 설정이 켜진 저화질 원본에 대해 원본보다 낮은 규격을 높은 순으로 탐색해 첫 적정 규격 반환

    - 원본이 저화질이 아니거나 설정이 꺼져 있으면 None
    - 원본이 4K 초과면 4K부터 탐색하며, 360p까지 미달이면 None
    """
    if not config.encode.auto_downscale_by_bitrate:
        return None
    if original_ratio >= config.thresholds.low_bitrate_ratio:
        return None
    source_tier = find_source_tier(vid.width, vid.height)
    upper_count = (
        len(RESOLUTION_TIERS) if source_tier is None else RESOLUTION_TIERS.index(source_tier)
    )
    for tier in reversed(RESOLUTION_TIERS[:upper_count]):
        if _calc_source_ratio_at_tier(vid, config, tier) >= config.thresholds.low_bitrate_ratio:
            return tier
    return None


def _output_pixels(vid: VideoProps, tier: ResolutionTier) -> int:
    """규격으로 줄였을 때의 출력 화소 수"""
    out_w, out_h = calc_scaled_dimensions(vid.width, vid.height, tier)
    return out_w * out_h


def _frame_analysis_tier(
    vid: VideoProps,
    config: ClassifierConfig,
    frame_target_tier: str | None,
) -> ResolutionTier | None:
    """화질 분석 축소 후보 규격. 자동 적용이 꺼져 있거나 의미 있는 축소가 아니면 None"""
    if not config.encode.auto_apply_frame_target or not frame_target_tier:
        return None
    tier = RESOLUTION_TIER_BY_KEY.get(frame_target_tier)
    if tier is None:
        return None
    if not is_meaningful_downscale(vid.width, vid.height, tier):
        return None
    return tier


_CANDIDATE_PRIORITY: dict[DownscaleReason, int] = {
    DownscaleReason.MAX_RESOLUTION: 0,
    DownscaleReason.FRAME_ANALYSIS: 1,
    DownscaleReason.BITRATE_FIT: 2,
}


def select_output_candidate(
    vid: VideoProps,
    config: ClassifierConfig,
    original_ratio: float,
    manual_target: str | None = None,
    frame_target_tier: str | None = None,
) -> tuple[ResolutionTier, DownscaleReason] | None:
    """직접 지정, 화질 분석 축소, 최대 해상도, 비트레이트 맞춤 후보 중 가장 작은 출력을 고르고 후보가 없으면 None

    - 직접 지정이 있으면 그것만 사용하며, 축소 안 함은 다른 후보도 적용하지 않음
    - 규격 키가 아닌 직접 지정 값과 의미 없는 축소 후보는 없는 것으로 봄
    - 후보가 여러 개면 출력 화소 수가 가장 작은 것을 쓰고, 같은 크기면 최대 해상도, 화질 분석 축소, 비트레이트 맞춤 순으로 사유를 정함
    """
    if manual_target == KEEP_RESOLUTION_KEY:
        return None
    if manual_target in RESOLUTION_TIER_BY_KEY:
        return RESOLUTION_TIER_BY_KEY[manual_target], DownscaleReason.MANUAL
    candidates: list[tuple[int, ResolutionTier, DownscaleReason]] = []
    max_tier = RESOLUTION_TIER_BY_KEY.get(config.encode.max_resolution_tier)
    if max_tier is not None:
        candidates.append((_output_pixels(vid, max_tier), max_tier, DownscaleReason.MAX_RESOLUTION))
    frame_tier = _frame_analysis_tier(vid, config, frame_target_tier)
    if frame_tier is not None:
        candidates.append((_output_pixels(vid, frame_tier), frame_tier, DownscaleReason.FRAME_ANALYSIS))
    fit_tier = _select_bitrate_fit_tier(vid, config, original_ratio)
    if fit_tier is not None:
        candidates.append((_output_pixels(vid, fit_tier), fit_tier, DownscaleReason.BITRATE_FIT))
    if not candidates:
        return None
    _, tier, reason = min(
        candidates,
        key=lambda item: (item[0], _CANDIDATE_PRIORITY[item[2]]),
    )
    return tier, reason


def resolve_downscale(
    vid: VideoProps,
    config: ClassifierConfig,
    original_ratio: float,
    manual_target: str | None = None,
    frame_target_tier: str | None = None,
) -> tuple[DownscaleEstimate | None, DownscaleSkipReason | None]:
    """출력 규격 후보를 출력 해상도 기준 축소 추정으로 변환하고, 후보를 버린 이유를 함께 반환

    - 후보가 없거나(축소 안 함, 제한 없음) 원본이 상한 박스 안이면 (None, None)
    - 상한 박스를 조금 넘어 의미 있는 축소가 아니면 (None, 거의 같은 크기)
    - 축소 후에도 저화질 보호 대상이면 (None, 축소 후에도 화질 보호 대상)
    - 출력 해상도 지표는 원본과 같은 FPS, 목표 코덱 기준으로 산출
    - original_ratio는 원본 해상도 기준 비트레이트 비율(r_src), 비트레이트 맞춤 후보의 저화질 판정에 사용
    """
    candidate = select_output_candidate(vid, config, original_ratio, manual_target, frame_target_tier)
    if candidate is None:
        return None, None
    tier, reason = candidate
    if not is_meaningful_downscale(vid.width, vid.height, tier):
        # - 상한 박스를 조금 넘는 원본만 사유를 남김(박스 안 원본은 축소 후보 자체가 아님)
        box_w, box_h = tier.box(orientation_of(vid.width, vid.height))
        exceeds_box = vid.width > box_w or vid.height > box_h
        return None, DownscaleSkipReason.NEAR_SAME_SIZE if exceeds_box else None

    out_w, out_h = calc_scaled_dimensions(vid.width, vid.height, tier)
    out_vid = replace(vid, width=out_w, height=out_h)
    source_opt_kbps, target_opt_kbps = calc_optimal_pair(out_vid, config)
    src_weight = config.get_codec_weight(vid.codec)
    target_weight = config.get_codec_weight(
        get_encode_codec_family(config.encode.encode_codec)
    )
    codec_weight_ratio = target_weight / src_weight if src_weight > 0 else 1.0
    source_ratio = vid.vid_kbps / source_opt_kbps
    target_ratio = vid.vid_kbps / target_opt_kbps
    zone = resolve_quality_zone(
        source_ratio,
        target_ratio,
        config.thresholds,
        source_weight=src_weight,
        target_weight=target_weight,
        is_10bit=vid.is_10bit,
        is_hdr=vid.is_hdr,
    )
    if should_use_low_quality_preset(
        zone, source_ratio, target_ratio, src_weight, target_weight
    ):
        return None, DownscaleSkipReason.LOW_QUALITY_AFTER_DOWNSCALE

    # - 위에서 저화질 보호 대상은 제외했으므로 축소 경로는 기준 품질값 그대로 인코딩하고 품질값 차이는 0
    after_mb = estimate_after_size_mb(
        vid,
        target_ratio,
        config.thresholds.keyframe_1s_increase_rate,
        cap_at_original=True,
        source_ratio=source_ratio,
        codec_weight_ratio=codec_weight_ratio,
        base_fps=config.bitrate_model.base_fps,
        max_video_kbps=combine_video_caps_kbps(
            tier.max_bitrate_kbps,
            resolve_level_cap_kbps(config.encode.encode_codec, out_w, out_h, vid.fps),
        ),
        output_delta_cq=0.0,
        encoder=config.encode.encode_codec,
    )
    estimate = DownscaleEstimate(
        tier=tier,
        reason=reason,
        target_width=out_w,
        target_height=out_h,
        target_optimal_kbps=target_opt_kbps,
        target_bitrate_ratio=target_ratio,
        source_ratio_at_target=source_ratio,
        after_mb=after_mb,
        potential_saved_mb=vid.vid_size_MB - after_mb,
        encode_sec=estimate_encode_duration_sec(out_vid, config),
    )
    return estimate, None
