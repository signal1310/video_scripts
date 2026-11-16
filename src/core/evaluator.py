"""비디오 화질, 키프레임 평가 및 CQ22 기반 분류 전략 판정 코어 엔진 모듈"""

from collections.abc import Sequence

from src.core.classification_policy import (
    DEFAULT_CLASSIFICATION_POLICIES,
    ClassificationDecision,
    ClassificationPolicy,
    EvaluationContext,
    ExceptionRule,
    should_use_low_quality_preset,
)
from src.core.config import (
    CODEC_WEIGHT_GROUPS,
    BitrateModelConfig,
    ClassifierConfig,
    CodecWeightsConfig,
    ThresholdsConfig,
    get_encode_codec_family,
)
from src.core.models import (
    QualityZone,
    SavingsSummary,
    SmartAction,
    SummaryReport,
    VideoEvaluation,
    VideoProps,
)
from src.core.size_estimator import estimate_after_size_mb

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
# - 디코딩 부하와 CQ26 가속은 미실측 추정치라 표시용 예상 시간에만 반영하고 절약 선별에는 쓰지 않음
CQ26_PRESET_SPEED_MULTIPLIER: float = 0.85
SOURCE_DECODE_COMPLEXITY: dict[str, float] = {
    **{alias: 1.15 for alias in CODEC_WEIGHT_GROUPS["av1"]},
    "prores": 1.10,
    "dnxhd": 1.10,
    **{alias: 1.05 for alias in CODEC_WEIGHT_GROUPS["hevc_vp9"]},
    "mjpeg": 1.05,
}

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


class VideoEvaluator:
    """해상도, 코덱, FPS 기반 최적 비트레이트 산출 및 분류 판정 엔진

    원본 코덱 기준 저화질 판정, 출력 코덱 기준 과화질 판정, 키프레임 1초 교정 용량 예측,
    실익, 시간 효율 선별과 HDR 보존을 결합해 판정 수행
    """

    @staticmethod
    def calc_optimal_bitrate(
        width: int,
        height: int,
        bitrate_model: BitrateModelConfig,
        codec_weights: CodecWeightsConfig,
        fps: float = 30.0,
        codec: str = "h264",
    ) -> int:
        """해상도, FPS, 코덱 효율을 반영한 목표 화질(CQ22 수준) 최적 비트레이트(kbps) 산출

        수식: B_opt(W, H, fps, codec) = B_base_h264 * ((W * H) / (W_base * H_base))^alpha * K_fps * C_codec
        """
        area_factor, fps_factor = _calc_resolution_fps_factors(
            width, height, fps, bitrate_model
        )
        codec_factor: float = codec_weights.get_weight(codec)

        optimal_kbps = round(
            bitrate_model.base_target_kbps * area_factor * fps_factor * codec_factor
        )
        return max(optimal_kbps, MIN_OPTIMAL_BITRATE_KBPS)

    @staticmethod
    def calc_encode_workload_ratio(width: int, height: int, fps: float) -> float:
        """1080p 30fps 대비 초당 처리 화소량의 상대 인코딩 작업량 배수 산출

        수식: W_enc = (W * H * fps) / (1920 * 1080 * 30)
        """
        reference_pixels_per_sec = (
            REFERENCE_ENCODE_WIDTH * REFERENCE_ENCODE_HEIGHT * REFERENCE_ENCODE_FPS
        )
        return (width * height * fps) / reference_pixels_per_sec

    @classmethod
    def estimate_encode_duration_sec(
        cls,
        vid: VideoProps,
        config: ClassifierConfig,
    ) -> float:
        """화소 처리량 비례 작업량과 기준 인코딩 배속으로 절약 선별용 기준 예상 인코딩 시간(초) 산출"""
        if vid.duration <= 0:
            return 0.0
        workload_ratio = cls.calc_encode_workload_ratio(vid.width, vid.height, vid.fps)
        speed = max(config.encode.encode_speed_factor, MIN_ENCODE_SPEED_FACTOR)
        return max(0.0, (vid.duration * workload_ratio) / speed)

    @classmethod
    def estimate_display_encode_duration_sec(
        cls,
        vid: VideoProps,
        config: ClassifierConfig,
        *,
        is_low_quality_preset: bool,
    ) -> float:
        """기준 예상 시간에 원본 디코딩 부하와 CQ26 프리셋 계수를 곱한 표시용 예상 인코딩 시간(초) 산출"""
        codec_key = (vid.codec or "").lower().strip()
        decode_factor = SOURCE_DECODE_COMPLEXITY.get(codec_key, 1.0)
        preset_factor = CQ26_PRESET_SPEED_MULTIPLIER if is_low_quality_preset else 1.0
        return cls.estimate_encode_duration_sec(vid, config) * decode_factor * preset_factor

    @staticmethod
    def classify_quality_zone(
        bitrate_ratio: float, thresholds: ThresholdsConfig
    ) -> QualityZone:
        """최적 비트레이트 대비 비트레이트 비율(r = B_curr / B_opt)을 경계 배수와 비교해 화질 등급을 3구간으로 판정"""
        if bitrate_ratio < thresholds.low_bitrate_ratio:
            return QualityZone.LOW
        if bitrate_ratio > thresholds.over_bitrate_ratio:
            return QualityZone.OVER
        return QualityZone.OPTIMAL

    @classmethod
    def resolve_quality_zone(
        cls,
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
        if cls.classify_quality_zone(source_ratio, thresholds) == QualityZone.LOW:
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

    @staticmethod
    def is_keyframe_exceeded(vid: VideoProps, thresholds: ThresholdsConfig) -> bool:
        """비디오 키프레임 간격의 허용 상한(keyframe_interval_sec) 초과 여부 판별"""
        return (
            vid.keyframe_interval is not None
            and vid.keyframe_interval > thresholds.keyframe_interval_sec
        )

    @staticmethod
    def resolve_decision(
        ctx: EvaluationContext,
        policies: Sequence[ClassificationPolicy] | None = None,
    ) -> ClassificationDecision:
        """등록된 우선순위 정책 체인을 순차 평가하여 첫 일치 분류 결정을 반환"""
        active_policies = (
            policies if policies is not None else DEFAULT_CLASSIFICATION_POLICIES
        )
        for policy in active_policies:
            if decision := policy(ctx):
                return decision
        raise ValueError("평가 컨텍스트와 일치하는 분류 정책을 찾지 못했습니다")

    @classmethod
    def _calc_optimal_pair(
        cls, vid: VideoProps, config: ClassifierConfig
    ) -> tuple[int, int]:
        """원본 코덱 기준 최적 비트레이트와 목표 인코딩 코덱 기준 최적 비트레이트 쌍 산출"""
        source_kbps = cls.calc_optimal_bitrate(
            vid.width,
            vid.height,
            config.bitrate_model,
            config.codec_weights,
            vid.fps,
            vid.codec,
        )
        target_kbps = cls.calc_optimal_bitrate(
            vid.width,
            vid.height,
            config.bitrate_model,
            config.codec_weights,
            vid.fps,
            get_encode_codec_family(config.encode.encode_codec),
        )
        return source_kbps, target_kbps

    @classmethod
    def _evaluate_precomputed(
        cls,
        vid: VideoProps,
        source_opt_kbps: int,
        target_opt_kbps: int,
        is_excluded: bool | None,
        config: ClassifierConfig,
        rules: tuple[ExceptionRule, ...],
        policies: Sequence[ClassificationPolicy] | None = None,
    ) -> VideoEvaluation:
        src_weight = config.get_codec_weight(vid.codec)
        target_weight = config.get_codec_weight(
            get_encode_codec_family(config.encode.encode_codec)
        )
        codec_weight_ratio = target_weight / src_weight if src_weight > 0 else 1.0
        source_ratio = vid.vid_kbps / source_opt_kbps
        target_ratio = vid.vid_kbps / target_opt_kbps
        zone = cls.resolve_quality_zone(
            source_ratio,
            target_ratio,
            config.thresholds,
            source_weight=src_weight,
            target_weight=target_weight,
            is_10bit=vid.is_10bit,
            is_hdr=vid.is_hdr,
        )
        is_keyframe_exceeded = cls.is_keyframe_exceeded(vid, config.thresholds)
        use_cq26 = should_use_low_quality_preset(
            zone, source_ratio, target_ratio, src_weight, target_weight
        )
        estimated_encode_sec = cls.estimate_encode_duration_sec(vid, config)
        display_encode_sec = cls.estimate_display_encode_duration_sec(
            vid, config, is_low_quality_preset=use_cq26
        )
        encoded_after_mb = estimate_after_size_mb(
            vid,
            target_ratio,
            config.thresholds.keyframe_1s_increase_rate,
            cap_at_original=zone == QualityZone.OVER,
            source_ratio=source_ratio,
            codec_weight_ratio=codec_weight_ratio,
            base_fps=config.bitrate_model.base_fps,
        )

        ctx = EvaluationContext(
            video=vid,
            optimal_kbps=source_opt_kbps,
            bitrate_ratio=source_ratio,
            target_optimal_kbps=target_opt_kbps,
            target_bitrate_ratio=target_ratio,
            quality_zone=zone,
            is_keyframe_exceeded=is_keyframe_exceeded,
            estimated_encode_sec=estimated_encode_sec,
            potential_saved_mb=vid.vid_size_MB - encoded_after_mb,
            config=config,
            exception_rules=rules,
            is_exception_matched=is_excluded,
            source_codec_weight=src_weight,
            target_codec_weight=target_weight,
        )
        decision = cls.resolve_decision(ctx, policies=policies)
        is_target = decision.target_dirname is not None
        if decision.size_estimate is not None:
            after_mb, delta_mb = decision.size_estimate
        elif is_target:
            after_mb, delta_mb = encoded_after_mb, vid.vid_size_MB - encoded_after_mb
        else:
            after_mb, delta_mb = vid.vid_size_MB, 0.0

        if is_target:
            hyp_after_mb, hyp_delta_mb = after_mb, delta_mb
        else:
            hyp_after_mb = encoded_after_mb
            hyp_delta_mb = vid.vid_size_MB - encoded_after_mb

        return VideoEvaluation(
            video=vid,
            optimal_kbps=source_opt_kbps,
            bitrate_ratio=source_ratio,
            quality_zone=zone,
            is_keyframe_exceeded=is_keyframe_exceeded,
            expected_after_mb=after_mb,
            expected_delta_mb=delta_mb,
            smart_action=decision.smart_action,
            target_dirname=decision.target_dirname,
            hypothetical_after_mb=hyp_after_mb,
            hypothetical_delta_mb=hyp_delta_mb,
            estimated_encode_sec=display_encode_sec,
            target_optimal_kbps=target_opt_kbps,
            target_bitrate_ratio=target_ratio,
            use_low_quality_preset=decision.use_low_quality_preset,
        )

    @classmethod
    def evaluate_video(
        cls,
        vid: VideoProps,
        config: ClassifierConfig,
        exception_rules: list[ExceptionRule] | None = None,
        policies: Sequence[ClassificationPolicy] | None = None,
    ) -> VideoEvaluation:
        """단일 비디오에 대해 원본, 출력 코덱 기준 목표치와 키프레임을 종합 평가하여 분류 결과를 생성"""
        rules = tuple(exception_rules or [])
        source_kbps, target_kbps = cls._calc_optimal_pair(vid, config)
        return cls._evaluate_precomputed(
            vid,
            source_kbps,
            target_kbps,
            None,
            config,
            rules,
            policies=policies,
        )

    @classmethod
    def evaluate_all(
        cls,
        root_dir: str,
        videos: list[VideoProps],
        config: ClassifierConfig,
        exception_rules: list[ExceptionRule] | None = None,
        policies: Sequence[ClassificationPolicy] | None = None,
    ) -> SummaryReport:
        """전체 비디오 목록을 일괄 평가하고 인코딩 대상 수량과 총 용량 변화를 집계하여 SummaryReport를 생성"""
        rules = tuple(exception_rules or [])

        evaluations: list[VideoEvaluation] = []
        for vid in videos:
            is_excluded = any(rule(vid) for rule in rules) if rules else False
            source_kbps, target_kbps = cls._calc_optimal_pair(vid, config)
            evaluations.append(
                cls._evaluate_precomputed(
                    vid,
                    source_kbps,
                    target_kbps,
                    is_excluded,
                    config,
                    rules,
                    policies=policies,
                )
            )
        return _build_summary_report(root_dir, evaluations, config)


def _build_summary_report(
    root_dir: str,
    evaluations: list[VideoEvaluation],
    config: ClassifierConfig,
) -> SummaryReport:
    """개별 평가 결과 목록을 용량, 작업량, 실익 효율 지표로 집계해 SummaryReport 생성"""
    savings = config.savings
    total_orig_mb = 0.0
    total_after_mb = 0.0
    gross_saved_mb = 0.0
    keyframe_increase_mb = 0.0
    encoded_count = 0
    encode_duration_sec = 0.0
    total_estimated_encode_sec = 0.0
    low_saved_count = 0
    low_per_min_count = 0
    excluded_over_duration_sec = 0.0
    excluded_over_potential_saved_mb = 0.0

    for ev in evaluations:
        vid = ev.video
        total_orig_mb += vid.vid_size_MB
        total_after_mb += ev.expected_after_mb
        delta_mb = ev.expected_delta_mb
        if delta_mb > 0:
            gross_saved_mb += delta_mb
        elif delta_mb < 0:
            keyframe_increase_mb += -delta_mb
        if ev.target_dirname is not None:
            encoded_count += 1
            encode_duration_sec += max(0.0, vid.duration)
            total_estimated_encode_sec += max(0.0, ev.estimated_encode_sec)
        elif ev.quality_zone == QualityZone.OVER and ev.smart_action in (
            SmartAction.PASS_LOW_SAVED,
            SmartAction.PASS_LOW_PER_MIN,
        ):
            excluded_over_duration_sec += max(0.0, vid.duration)
            excluded_over_potential_saved_mb += max(0.0, ev.preview_delta_mb)
            if ev.smart_action == SmartAction.PASS_LOW_SAVED:
                low_saved_count += 1
            else:
                low_per_min_count += 1

    savings_summary = SavingsSummary(
        min_saved_mb=savings.min_saved_mb,
        min_mb_per_min=savings.min_mb_per_min,
        exempt_saved_mb=savings.exempt_saved_mb,
        encode_duration_sec=encode_duration_sec,
        low_saved_count=low_saved_count,
        low_per_min_count=low_per_min_count,
        excluded_over_duration_sec=excluded_over_duration_sec,
        excluded_over_potential_saved_mb=excluded_over_potential_saved_mb,
    )
    return SummaryReport(
        root_dir=root_dir,
        total_count=len(evaluations),
        encoded_count=encoded_count,
        passed_count=len(evaluations) - encoded_count,
        total_orig_mb=total_orig_mb,
        total_expected_after_mb=total_after_mb,
        gross_saved_mb=gross_saved_mb,
        keyframe_increase_mb=keyframe_increase_mb,
        net_saved_mb=gross_saved_mb - keyframe_increase_mb,
        savings_summary=savings_summary,
        evaluations=evaluations,
        total_estimated_encode_sec=total_estimated_encode_sec,
    )
