"""비디오 화질, 키프레임 평가 및 CQ22 기반 분류 전략 판정 코어 엔진 모듈"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.core.config import (
    SAVINGS_BASIS_ENCODE,
    BitrateModelConfig,
    ClassifierConfig,
    CodecWeightsConfig,
    ThresholdsConfig,
    get_encode_codec_family,
)
from src.core.models import (
    SECONDS_PER_MINUTE,
    QualityZone,
    SavingsSummary,
    SmartAction,
    SummaryReport,
    VideoEvaluation,
    VideoProps,
)
from src.core.size_estimator import estimate_after_size_mb

ExceptionRule = Callable[[VideoProps], bool]


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    """단일 비디오 분류 정책 판정에 전달되는 불변 평가 컨텍스트"""

    video: VideoProps
    optimal_kbps: int
    bitrate_ratio: float
    target_optimal_kbps: int
    target_bitrate_ratio: float
    quality_zone: QualityZone
    is_keyframe_exceeded: bool
    estimated_encode_sec: float
    potential_saved_mb: float
    config: ClassifierConfig
    exception_rules: tuple[ExceptionRule, ...]
    is_exception_matched: bool | None = None


@dataclass(frozen=True, slots=True)
class ClassificationDecision:
    """분류 정책이 산출하는 판정, 대상 폴더 및 선택적 용량 예측 결과"""

    smart_action: SmartAction
    target_dirname: str | None
    size_estimate: tuple[float, float] | None = None


ClassificationPolicy = Callable[[EvaluationContext], ClassificationDecision | None]

MIN_OPTIMAL_BITRATE_KBPS: int = 100
# - 기준 FPS 근처의 미세 오차로 보정이 걸리지 않도록 두는 불감대(dead band) 여유치
FPS_HYSTERESIS_TOLERANCE: float = 2.0
REFERENCE_ENCODE_WIDTH: int = 1920
REFERENCE_ENCODE_HEIGHT: int = 1080
REFERENCE_ENCODE_FPS: float = 30.0
MIN_ENCODE_SPEED_FACTOR: float = 0.01


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


def _calc_saved_mb_per_min(saved_mb: float, seconds: float) -> float:
    """작업 길이(재생 또는 예상 인코딩 시간) 1분당 절감 효율(MB/분) 계산"""
    if seconds <= 0:
        return 0.0
    return saved_mb / (seconds / SECONDS_PER_MINUTE)


def _is_below_savings_efficiency(ctx: EvaluationContext, saved_mb: float) -> bool:
    """선택한 절약 선별 기준의 분당 절약량이 최소 기준 미만인지 판별

    - 재생 기준: 재생 1분당 절약량, 대용량 절약 예외 적용
    - 인코딩 기준: 예상 인코딩 1분당 절약량, 예외 미적용, 예상 시간이 0이면 선별 생략
    """
    savings = ctx.config.savings
    if savings.min_mb_per_min <= 0:
        return False
    if savings.selection_basis == SAVINGS_BASIS_ENCODE:
        work_sec = ctx.estimated_encode_sec
    else:
        if savings.exempt_saved_mb > 0 and saved_mb >= savings.exempt_saved_mb:
            return False
        work_sec = ctx.video.duration
    if work_sec <= 0:
        return False
    return _calc_saved_mb_per_min(saved_mb, work_sec) < savings.min_mb_per_min


def _policy_exception_rule(ctx: EvaluationContext) -> ClassificationDecision | None:
    """사용자 예외 규칙과 일치하면 원본 보존 판정 반환"""
    matched = (
        ctx.is_exception_matched
        if ctx.is_exception_matched is not None
        else any(rule(ctx.video) for rule in ctx.exception_rules)
    )
    if matched:
        return ClassificationDecision(SmartAction.PASS_EXCEPTION, None)
    return None


def _policy_hdr(ctx: EvaluationContext) -> ClassificationDecision | None:
    """HDR 영상은 현재 인코딩 설정으로 색 정보를 잃을 수 있어 화질 등급과 무관하게 원본 보존"""
    if ctx.video.is_hdr:
        return ClassificationDecision(SmartAction.PASS_HDR, None)
    return None


def _policy_over_zone(ctx: EvaluationContext) -> ClassificationDecision | None:
    """과화질 구간의 실익, 시간 효율 선별과 키프레임 불량 우선 인코딩 판정"""
    if ctx.quality_zone != QualityZone.OVER:
        return None
    # - 키프레임 불량 영상은 탐색 성능 교정을 위해 절감 효율과 무관하게 인코딩 진행
    if not ctx.is_keyframe_exceeded:
        if ctx.potential_saved_mb < ctx.config.savings.min_saved_mb:
            return ClassificationDecision(SmartAction.PASS_LOW_SAVED, None)
        if _is_below_savings_efficiency(ctx, ctx.potential_saved_mb):
            return ClassificationDecision(SmartAction.PASS_LOW_PER_MIN, None)
    return ClassificationDecision(
        SmartAction.ENCODE_CQ22_OVER, ctx.config.dir_standard_cq22
    )


def _policy_optimal_zone(ctx: EvaluationContext) -> ClassificationDecision | None:
    """적정화질 구간의 키프레임 불량 여부 기반 판정"""
    if ctx.quality_zone != QualityZone.OPTIMAL:
        return None
    if ctx.is_keyframe_exceeded:
        return ClassificationDecision(
            SmartAction.ENCODE_CQ22_OPT_KF, ctx.config.dir_standard_cq22
        )
    return ClassificationDecision(SmartAction.PASS_OPTIMAL, None)


def _policy_low_zone(ctx: EvaluationContext) -> ClassificationDecision | None:
    """저화질 구간의 키프레임 결함 교정 또는 원본 보존 판정"""
    if ctx.quality_zone != QualityZone.LOW:
        return None
    if ctx.is_keyframe_exceeded:
        return ClassificationDecision(
            SmartAction.ENCODE_LOW_KF, ctx.config.dir_low_quality_keyframe
        )
    return ClassificationDecision(SmartAction.PASS_LOW, None)


DEFAULT_CLASSIFICATION_POLICIES: tuple[ClassificationPolicy, ...] = (
    _policy_exception_rule,
    _policy_hdr,
    _policy_over_zone,
    _policy_optimal_zone,
    _policy_low_zone,
)


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
        """화소 처리량 비례 작업량과 기준 인코딩 배속으로 예상 인코딩 소요 시간(초) 산출"""
        if vid.duration <= 0:
            return 0.0
        workload_ratio = cls.calc_encode_workload_ratio(vid.width, vid.height, vid.fps)
        speed = max(config.encode.encode_speed_factor, MIN_ENCODE_SPEED_FACTOR)
        return max(0.0, (vid.duration * workload_ratio) / speed)

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
    ) -> QualityZone:
        """원본 코덱 기준 저화질을 먼저 판정하고, 나머지는 출력 코덱 기준 과화질 여부로 구분

        저화질 보호를 우선해 원본을 다시 압축하지 않도록 함
        """
        if cls.classify_quality_zone(source_ratio, thresholds) == QualityZone.LOW:
            return QualityZone.LOW
        if cls.classify_quality_zone(output_ratio, thresholds) == QualityZone.OVER:
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
        source_ratio = vid.vid_kbps / source_opt_kbps
        target_ratio = vid.vid_kbps / target_opt_kbps
        zone = cls.resolve_quality_zone(source_ratio, target_ratio, config.thresholds)
        is_keyframe_exceeded = cls.is_keyframe_exceeded(vid, config.thresholds)
        estimated_encode_sec = cls.estimate_encode_duration_sec(vid, config)
        encoded_after_mb = estimate_after_size_mb(
            vid,
            target_ratio,
            config.thresholds.keyframe_1s_increase_rate,
            cap_at_original=zone == QualityZone.OVER,
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
            estimated_encode_sec=estimated_encode_sec,
            target_optimal_kbps=target_opt_kbps,
            target_bitrate_ratio=target_ratio,
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
        elif ev.smart_action in (
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
