"""비디오 화질·키프레임 평가 및 CQ22 기반 분류 전략 판정 코어 엔진 모듈"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.core.config import (
    BitrateModelConfig,
    ClassifierConfig,
    CodecWeightsConfig,
    SmallFootageConfig,
    ThresholdsConfig,
)
from src.core.models import (
    PERCENT_MULTIPLIER,
    FootageStrategy,
    QualityZone,
    SmallFootageDistribution,
    SmartAction,
    SummaryReport,
    VideoEvaluation,
    VideoProps,
)

ExceptionRule = Callable[[VideoProps], bool]


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    """단일 비디오 분류 정책 판정에 전달되는 불변 평가 컨텍스트"""

    video: VideoProps
    optimal_kbps: int
    bitrate_ratio: float
    quality_zone: QualityZone
    is_keyframe_defective: bool
    is_small_footage: bool
    small_distribution: SmallFootageDistribution
    config: ClassifierConfig
    exception_rules: tuple[ExceptionRule, ...]
    is_exception_matched: bool | None = None


@dataclass(frozen=True, slots=True)
class ClassificationDecision:
    """분류 정책이 산출하는 처방·대상 폴더 및 선택적 용량 예측 결과"""

    smart_action: SmartAction
    target_dirname: str | None
    size_estimate: tuple[float, float] | None = None


ClassificationPolicy = Callable[[EvaluationContext], ClassificationDecision | None]
SizeZoneEstimator = Callable[
    [VideoProps, float, bool, SmallFootageConfig], tuple[float, float]
]

MIN_OPTIMAL_BITRATE_KBPS: int = 100
FPS_HYSTERESIS_TOLERANCE: float = 2.0
OVER_KEYFRAME_OVERHEAD_FACTOR: float = 1.04
DEFAULT_UNKNOWN_KF_SEC: float = 5.0
MIN_CLAMP_KF_SEC: float = 2.2
MAX_CLAMP_KF_SEC: float = 10.0


def _estimate_over_size_change(
    vid: VideoProps,
    bitrate_ratio: float,
    is_keyframe_defective: bool,
    _small_footage: SmallFootageConfig,
) -> tuple[float, float]:
    """과화질 구간의 목표 비트레이트 수렴 및 키프레임 오버헤드 반영 용량 예측"""
    orig_mb = vid.vid_size_MB
    keyframe_overhead_factor = (
        OVER_KEYFRAME_OVERHEAD_FACTOR if is_keyframe_defective else 1.0
    )
    after_mb = min(
        orig_mb, orig_mb * (1.0 / bitrate_ratio) * keyframe_overhead_factor
    )
    return after_mb, orig_mb - after_mb


def _estimate_keyframe_only_size_change(
    vid: VideoProps,
    _bitrate_ratio: float,
    _is_keyframe_defective: bool,
    small_footage: SmallFootageConfig,
) -> tuple[float, float]:
    """적정·저화질 구간에서 1초 키프레임 교정 시 발생하는 용량 증가분 예측"""
    orig_mb = vid.vid_size_MB
    keyframe_interval_val = min(
        max(vid.keyframe_interval or DEFAULT_UNKNOWN_KF_SEC, MIN_CLAMP_KF_SEC),
        MAX_CLAMP_KF_SEC,
    )
    overhead_ratio = small_footage.keyframe_1s_overhead_rate * (
        1.0 - (1.0 / keyframe_interval_val)
    )
    after_mb = orig_mb * (1.0 + overhead_ratio)
    return after_mb, orig_mb - after_mb


_ZONE_SIZE_ESTIMATORS: dict[QualityZone, SizeZoneEstimator] = {
    QualityZone.OVER: _estimate_over_size_change,
    QualityZone.OPTIMAL: _estimate_keyframe_only_size_change,
    QualityZone.LOW: _estimate_keyframe_only_size_change,
}


def _calc_over_potential_saved_mb(vid_size_mb: float, bitrate_ratio: float) -> float:
    """과화질 영상의 기준 비트레이트 대비 이론적 절감 잠재량(MB) 계산"""
    if bitrate_ratio <= 0:
        return 0.0
    return vid_size_mb * max(0.0, 1.0 - (1.0 / bitrate_ratio))


def _policy_exception_rule(ctx: EvaluationContext) -> ClassificationDecision | None:
    """사용자 예외 규칙과 일치하면 원본 보존 처방 반환"""
    matched = (
        ctx.is_exception_matched
        if ctx.is_exception_matched is not None
        else any(rule(ctx.video) for rule in ctx.exception_rules)
    )
    if matched:
        return ClassificationDecision(SmartAction.PASS_EXCEPTION, None)
    return None


def _policy_over_zone(ctx: EvaluationContext) -> ClassificationDecision | None:
    """과화질 구간의 소형 영상 절감 전략과 키프레임 불량 우선 인코딩 판정"""
    if ctx.quality_zone != QualityZone.OVER:
        return None
    # - 키프레임이 불량인 영상은 소형 영상이라도 탐색 편의성을 위해 무조건 인코딩 진행
    if not ctx.is_keyframe_defective and ctx.is_small_footage:
        saved = _calc_over_potential_saved_mb(ctx.video.vid_size_MB, ctx.bitrate_ratio)
        if (
            ctx.small_distribution.strategy == FootageStrategy.IGNORE_SMALL
            or saved < ctx.config.small_footage.min_saved_mb
        ):
            return ClassificationDecision(SmartAction.PASS_SMALL_FOOTAGE, None)
    return ClassificationDecision(
        SmartAction.ENCODE_CQ22_OVER, ctx.config.dir_standard_cq22
    )


def _policy_optimal_zone(ctx: EvaluationContext) -> ClassificationDecision | None:
    """적정화질 구간의 키프레임 불량 여부 기반 처방 판정"""
    if ctx.quality_zone != QualityZone.OPTIMAL:
        return None
    if ctx.is_keyframe_defective:
        return ClassificationDecision(
            SmartAction.ENCODE_CQ22_OPT_KF, ctx.config.dir_standard_cq22
        )
    return ClassificationDecision(SmartAction.PASS_OPTIMAL, None)


def _policy_low_zone(ctx: EvaluationContext) -> ClassificationDecision | None:
    """저화질 구간의 키프레임 결함 교정 또는 원본 보존 처방 판정"""
    if ctx.quality_zone != QualityZone.LOW:
        return None
    if ctx.is_keyframe_defective:
        return ClassificationDecision(
            SmartAction.ENCODE_LOW_KF, ctx.config.dir_low_quality_keyframe
        )
    return ClassificationDecision(SmartAction.PASS_LOW, None)


DEFAULT_CLASSIFICATION_POLICIES: tuple[ClassificationPolicy, ...] = (
    _policy_exception_rule,
    _policy_over_zone,
    _policy_optimal_zone,
    _policy_low_zone,
)


class VideoEvaluator:
    """해상도·코덱·FPS 기반 최적 비트레이트 산출 및 분류 처방 결정 엔진

    키프레임 1초 교정 용량 예측, 소형 영상 분포 분석, 화질 3구간 결합 판정 수행
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
        """해상도·FPS·코덱 효율을 반영한 목표 화질(CQ22 수준) 최적 비트레이트(kbps) 산출

        수식: B_opt(W, H, fps, codec) = B_base_h264 * ((W * H) / (W_base * H_base))^alpha * K_fps * C_codec
        """
        base_pixels = max(bitrate_model.base_width * bitrate_model.base_height, 1)
        curr_pixels = max(width * height, 1)

        area_factor: float = (
            curr_pixels / base_pixels
        ) ** bitrate_model.area_scaling_exponent

        # - 기준 FPS 근처의 미세 오차로 과도한 보정이 걸리지 않도록 히스테리시스 여유치를 적용
        if fps > bitrate_model.base_fps + FPS_HYSTERESIS_TOLERANCE:
            fps_factor: float = (
                fps / bitrate_model.base_fps
            ) ** bitrate_model.fps_scaling_exponent
        else:
            fps_factor = 1.0

        codec_factor: float = codec_weights.get_weight(codec)

        optimal_kbps = round(
            bitrate_model.base_target_kbps * area_factor * fps_factor * codec_factor
        )
        return max(optimal_kbps, MIN_OPTIMAL_BITRATE_KBPS)

    @staticmethod
    def classify_quality_zone(
        bitrate_ratio: float, thresholds: ThresholdsConfig
    ) -> QualityZone:
        """최적 비트레이트 대비 현재 비트레이트 비율(r = B_curr / B_opt)을 설정된 경계 배수와 비교해 화질 등급을 3구간으로 판정"""
        if bitrate_ratio < thresholds.low_bitrate_ratio:
            return QualityZone.LOW
        if bitrate_ratio > thresholds.over_bitrate_ratio:
            return QualityZone.OVER
        return QualityZone.OPTIMAL

    @staticmethod
    def is_keyframe_defective(vid: VideoProps, thresholds: ThresholdsConfig) -> bool:
        """비디오 키프레임 간격의 허용 상한(keyframe_interval_sec) 초과 여부 판별

        소형 영상(짧은 소형 영상 포함) 여부와 무관하게 전체 영상에 동일 적용
        """
        return (
            vid.keyframe_interval is not None
            and vid.keyframe_interval > thresholds.keyframe_interval_sec
        )

    @staticmethod
    def estimate_size_change_mb(
        vid: VideoProps,
        bitrate_ratio: float,
        quality_zone: QualityZone,
        is_keyframe_defective: bool,
        will_encode: bool,
        small_footage: SmallFootageConfig,
    ) -> tuple[float, float]:
        """인코딩 완료 후 예상 파일 크기(expected_after_mb) 및 용량 변화량(expected_delta_mb) 계산

        양수(+)는 용량 절감량, 음수(-)는 1초 키프레임 교정에 따른 용량 증가분 의미
        """
        orig_mb = vid.vid_size_MB
        if not will_encode or bitrate_ratio <= 0:
            return orig_mb, 0.0
        estimator = _ZONE_SIZE_ESTIMATORS.get(
            quality_zone, _estimate_keyframe_only_size_change
        )
        return estimator(vid, bitrate_ratio, is_keyframe_defective, small_footage)

    @staticmethod
    def _build_small_footage_distribution(
        small_footage: SmallFootageConfig,
        total_over_count: int,
        small_over_count: int,
        total_potential_saved_mb: float,
        small_potential_saved_mb: float,
    ) -> SmallFootageDistribution:
        share_ratio = (
            small_potential_saved_mb / total_potential_saved_mb
            if total_potential_saved_mb > 0
            else 0.0
        )
        share_pct = share_ratio * PERCENT_MULTIPLIER
        summary_prefix = (
            f"소형 영상(<{small_footage.size_mb:.0f}MB)의 예상 절감 합계가 "
            f"{small_potential_saved_mb:.1f}MB(전체 절감 가능량의 {share_pct:.1f}%)"
        )

        if (
            share_ratio >= small_footage.cumulative_ratio
            or small_potential_saved_mb >= small_footage.cumulative_mb
        ):
            strategy = FootageStrategy.ACCUMULATE
            reason = (
                f"{summary_prefix}에 달해 누적 절감 효과가 크므로, "
                "소형 영상도 최적화 대상에 포함합니다."
            )
        else:
            strategy = FootageStrategy.IGNORE_SMALL
            reason = (
                f"{summary_prefix}로 작아 인코딩 실익이 낮으므로, "
                "키프레임이 정상인 소형 영상은 인코딩 대상에서 제외합니다."
            )

        return SmallFootageDistribution(
            small_threshold_mb=small_footage.size_mb,
            min_saved_mb=small_footage.min_saved_mb,
            total_over_count=total_over_count,
            small_over_count=small_over_count,
            total_potential_saved_mb=total_potential_saved_mb,
            small_potential_saved_mb=small_potential_saved_mb,
            small_share_ratio=share_ratio,
            strategy=strategy,
            reason=reason,
        )

    @classmethod
    def _analyze_small_footage_sections(
        cls,
        videos: list[VideoProps],
        thresholds: ThresholdsConfig,
        small_footage: SmallFootageConfig,
        bitrate_model: BitrateModelConfig,
        codec_weights: CodecWeightsConfig,
        rules: list[ExceptionRule],
    ) -> SmallFootageDistribution:
        total_over_count = 0
        small_over_count = 0
        total_potential_saved_mb = 0.0
        small_potential_saved_mb = 0.0

        for vid in videos:
            if rules and any(rule(vid) for rule in rules):
                continue
            opt_kbps = cls.calc_optimal_bitrate(
                vid.width, vid.height, bitrate_model, codec_weights, vid.fps, vid.codec
            )
            r = vid.vid_kbps / opt_kbps
            if r > thresholds.over_bitrate_ratio:
                saved_mb = _calc_over_potential_saved_mb(vid.vid_size_MB, r)
                total_over_count += 1
                total_potential_saved_mb += saved_mb

                if vid.vid_size_MB < small_footage.size_mb:
                    small_over_count += 1
                    small_potential_saved_mb += saved_mb

        return cls._build_small_footage_distribution(
            small_footage,
            total_over_count,
            small_over_count,
            total_potential_saved_mb,
            small_potential_saved_mb,
        )

    @classmethod
    def analyze_small_footage_distribution(
        cls,
        videos: list[VideoProps],
        config: ClassifierConfig,
        exception_rules: list[ExceptionRule] | None = None,
    ) -> SmallFootageDistribution:
        """전체 영상 목록을 사전 스캔해 소형 영상의 누적 절감 잠재력 분석

        config의 소형 영상 비중·누적 절감 기준치 충족 여부에 따라 ACCUMULATE 또는 IGNORE_SMALL 모드 판정
        """
        return cls._analyze_small_footage_sections(
            videos,
            config.thresholds,
            config.small_footage,
            config.bitrate_model,
            config.codec_weights,
            exception_rules or [],
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
    def _evaluate_precomputed(
        cls,
        vid: VideoProps,
        opt_kbps: int,
        r: float,
        is_excluded: bool | None,
        config: ClassifierConfig,
        small_dist: SmallFootageDistribution,
        rules: tuple[ExceptionRule, ...],
        policies: Sequence[ClassificationPolicy] | None = None,
    ) -> VideoEvaluation:
        zone = cls.classify_quality_zone(r, config.thresholds)
        is_keyframe_defective = cls.is_keyframe_defective(vid, config.thresholds)
        is_small = vid.vid_size_MB < config.small_footage.size_mb

        ctx = EvaluationContext(
            video=vid,
            optimal_kbps=opt_kbps,
            bitrate_ratio=r,
            quality_zone=zone,
            is_keyframe_defective=is_keyframe_defective,
            is_small_footage=is_small,
            small_distribution=small_dist,
            config=config,
            exception_rules=rules,
            is_exception_matched=is_excluded,
        )
        decision = cls.resolve_decision(ctx, policies=policies)
        if decision.size_estimate is not None:
            after_mb, delta_mb = decision.size_estimate
        elif decision.target_dirname is not None:
            after_mb, delta_mb = cls.estimate_size_change_mb(
                vid,
                r,
                zone,
                is_keyframe_defective,
                True,
                config.small_footage,
            )
        else:
            after_mb, delta_mb = vid.vid_size_MB, 0.0

        return VideoEvaluation(
            video=vid,
            optimal_kbps=opt_kbps,
            bitrate_ratio=r,
            quality_zone=zone,
            is_keyframe_defective=is_keyframe_defective,
            is_small_footage=is_small,
            expected_after_mb=after_mb,
            expected_delta_mb=delta_mb,
            smart_action=decision.smart_action,
            target_dirname=decision.target_dirname,
        )

    @classmethod
    def evaluate_video(
        cls,
        vid: VideoProps,
        config: ClassifierConfig,
        small_dist: SmallFootageDistribution,
        exception_rules: list[ExceptionRule] | None = None,
        policies: Sequence[ClassificationPolicy] | None = None,
    ) -> VideoEvaluation:
        """단일 비디오에 대해 해상도·코덱 기반 목표치와 키프레임을 종합 평가하여 분류 결과를 생성"""
        rules = tuple(exception_rules or [])
        opt_kbps = cls.calc_optimal_bitrate(
            vid.width,
            vid.height,
            config.bitrate_model,
            config.codec_weights,
            vid.fps,
            vid.codec,
        )
        r = vid.vid_kbps / opt_kbps
        return cls._evaluate_precomputed(
            vid,
            opt_kbps,
            r,
            None,
            config,
            small_dist,
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
        thresholds = config.thresholds
        small_footage = config.small_footage
        bitrate_model = config.bitrate_model
        codec_weights = config.codec_weights
        over_ratio = thresholds.over_bitrate_ratio
        small_limit_mb = small_footage.size_mb

        precomputed: list[tuple[VideoProps, int, float, bool]] = []
        total_over_count = 0
        small_over_count = 0
        total_potential_saved_mb = 0.0
        small_potential_saved_mb = 0.0

        for vid in videos:
            is_excluded = any(rule(vid) for rule in rules) if rules else False
            opt_kbps = cls.calc_optimal_bitrate(
                vid.width,
                vid.height,
                bitrate_model,
                codec_weights,
                vid.fps,
                vid.codec,
            )
            r = vid.vid_kbps / opt_kbps
            precomputed.append((vid, opt_kbps, r, is_excluded))

            if not is_excluded and r > over_ratio:
                saved_mb = _calc_over_potential_saved_mb(vid.vid_size_MB, r)
                total_over_count += 1
                total_potential_saved_mb += saved_mb
                if vid.vid_size_MB < small_limit_mb:
                    small_over_count += 1
                    small_potential_saved_mb += saved_mb

        small_dist = cls._build_small_footage_distribution(
            small_footage,
            total_over_count,
            small_over_count,
            total_potential_saved_mb,
            small_potential_saved_mb,
        )

        evaluations: list[VideoEvaluation] = []
        total_orig_mb = 0.0
        total_after_mb = 0.0
        gross_saved_mb = 0.0
        keyframe_increase_mb = 0.0
        encoded_count = 0

        for vid, opt_kbps, r, is_excluded in precomputed:
            ev = cls._evaluate_precomputed(
                vid,
                opt_kbps,
                r,
                is_excluded,
                config,
                small_dist,
                rules,
                policies=policies,
            )
            evaluations.append(ev)
            total_orig_mb += vid.vid_size_MB
            total_after_mb += ev.expected_after_mb
            delta_mb = ev.expected_delta_mb
            if delta_mb > 0:
                gross_saved_mb += delta_mb
            elif delta_mb < 0:
                keyframe_increase_mb += -delta_mb
            if ev.target_dirname is not None:
                encoded_count += 1

        net_saved_mb = gross_saved_mb - keyframe_increase_mb
        passed_count = len(evaluations) - encoded_count

        return SummaryReport(
            root_dir=root_dir,
            total_count=len(evaluations),
            encoded_count=encoded_count,
            passed_count=passed_count,
            total_orig_mb=total_orig_mb,
            total_expected_after_mb=total_after_mb,
            gross_saved_mb=gross_saved_mb,
            keyframe_increase_mb=keyframe_increase_mb,
            net_saved_mb=net_saved_mb,
            small_distribution=small_dist,
            evaluations=evaluations,
        )
