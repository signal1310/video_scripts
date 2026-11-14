"""비디오 화질·키프레임 평가 및 CQ22 기반 분류 전략 판정 코어 엔진 모듈"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.core.config import (
    BitrateModelConfig,
    ClassifierConfig,
    CodecWeightsConfig,
    ThresholdsConfig,
)
from src.core.models import (
    SECONDS_PER_MINUTE,
    EfficiencySummary,
    QualityZone,
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
    config: ClassifierConfig
    exception_rules: tuple[ExceptionRule, ...]
    is_exception_matched: bool | None = None


@dataclass(frozen=True, slots=True)
class ClassificationDecision:
    """분류 정책이 산출하는 판정·대상 폴더 및 선택적 용량 예측 결과"""

    smart_action: SmartAction
    target_dirname: str | None
    size_estimate: tuple[float, float] | None = None


ClassificationPolicy = Callable[[EvaluationContext], ClassificationDecision | None]
SizeZoneEstimator = Callable[
    [VideoProps, float, bool, ThresholdsConfig], tuple[float, float]
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
    _thresholds: ThresholdsConfig,
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
    thresholds: ThresholdsConfig,
) -> tuple[float, float]:
    """적정·저화질 구간에서 1초 키프레임 교정 시 발생하는 용량 증가분 예측"""
    orig_mb = vid.vid_size_MB
    keyframe_interval_val = min(
        max(vid.keyframe_interval or DEFAULT_UNKNOWN_KF_SEC, MIN_CLAMP_KF_SEC),
        MAX_CLAMP_KF_SEC,
    )
    overhead_ratio = thresholds.keyframe_1s_overhead_rate * (
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


def _calc_saved_mb_per_min(saved_mb: float, duration_sec: float) -> float:
    """재생 1분당 예상 용량 절감 효율(MB/분) 계산"""
    if duration_sec <= 0:
        return 0.0
    return saved_mb / (duration_sec / SECONDS_PER_MINUTE)


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


def _policy_over_zone(ctx: EvaluationContext) -> ClassificationDecision | None:
    """과화질 구간의 실익·시간 효율 선별과 키프레임 불량 우선 인코딩 판정"""
    if ctx.quality_zone != QualityZone.OVER:
        return None
    # - 키프레임 불량 영상은 탐색 성능 교정을 위해 절감 효율과 무관하게 인코딩 진행
    if not ctx.is_keyframe_defective:
        saved = _calc_over_potential_saved_mb(ctx.video.vid_size_MB, ctx.bitrate_ratio)
        eff = ctx.config.efficiency
        if saved < eff.min_saved_mb:
            return ClassificationDecision(SmartAction.PASS_LOW_SAVINGS, None)
        is_exempt = eff.exempt_saved_mb > 0 and saved >= eff.exempt_saved_mb
        if (
            not is_exempt
            and eff.min_mb_per_min > 0
            and ctx.video.duration > 0
            and _calc_saved_mb_per_min(saved, ctx.video.duration) < eff.min_mb_per_min
        ):
            return ClassificationDecision(SmartAction.PASS_LOW_EFFICIENCY, None)
    return ClassificationDecision(
        SmartAction.ENCODE_CQ22_OVER, ctx.config.dir_standard_cq22
    )


def _policy_optimal_zone(ctx: EvaluationContext) -> ClassificationDecision | None:
    """적정화질 구간의 키프레임 불량 여부 기반 판정"""
    if ctx.quality_zone != QualityZone.OPTIMAL:
        return None
    if ctx.is_keyframe_defective:
        return ClassificationDecision(
            SmartAction.ENCODE_CQ22_OPT_KF, ctx.config.dir_standard_cq22
        )
    return ClassificationDecision(SmartAction.PASS_OPTIMAL, None)


def _policy_low_zone(ctx: EvaluationContext) -> ClassificationDecision | None:
    """저화질 구간의 키프레임 결함 교정 또는 원본 보존 판정"""
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
    """해상도·코덱·FPS 기반 최적 비트레이트 산출 및 분류 판정 엔진

    키프레임 1초 교정 용량 예측, 실익·시간 효율 선별, 화질 3구간 결합 판정 수행
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
        """비디오 키프레임 간격의 허용 상한(keyframe_interval_sec) 초과 여부 판별"""
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
        thresholds: ThresholdsConfig,
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
        return estimator(vid, bitrate_ratio, is_keyframe_defective, thresholds)

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
        rules: tuple[ExceptionRule, ...],
        policies: Sequence[ClassificationPolicy] | None = None,
    ) -> VideoEvaluation:
        zone = cls.classify_quality_zone(r, config.thresholds)
        is_keyframe_defective = cls.is_keyframe_defective(vid, config.thresholds)

        ctx = EvaluationContext(
            video=vid,
            optimal_kbps=opt_kbps,
            bitrate_ratio=r,
            quality_zone=zone,
            is_keyframe_defective=is_keyframe_defective,
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
                config.thresholds,
            )
        else:
            after_mb, delta_mb = vid.vid_size_MB, 0.0

        return VideoEvaluation(
            video=vid,
            optimal_kbps=opt_kbps,
            bitrate_ratio=r,
            quality_zone=zone,
            is_keyframe_defective=is_keyframe_defective,
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
        bitrate_model = config.bitrate_model
        codec_weights = config.codec_weights
        eff = config.efficiency

        evaluations: list[VideoEvaluation] = []
        total_orig_mb = 0.0
        total_after_mb = 0.0
        gross_saved_mb = 0.0
        keyframe_increase_mb = 0.0
        encoded_count = 0
        encode_duration_sec = 0.0
        low_savings_count = 0
        low_efficiency_count = 0
        skipped_over_duration_sec = 0.0
        skipped_over_potential_saved_mb = 0.0

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
            ev = cls._evaluate_precomputed(
                vid,
                opt_kbps,
                r,
                is_excluded,
                config,
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
                encode_duration_sec += max(0.0, vid.duration)
            elif ev.smart_action in (
                SmartAction.PASS_LOW_SAVINGS,
                SmartAction.PASS_LOW_EFFICIENCY,
            ):
                skipped_over_duration_sec += max(0.0, vid.duration)
                skipped_over_potential_saved_mb += _calc_over_potential_saved_mb(
                    vid.vid_size_MB, r
                )
                if ev.smart_action == SmartAction.PASS_LOW_SAVINGS:
                    low_savings_count += 1
                else:
                    low_efficiency_count += 1

        net_saved_mb = gross_saved_mb - keyframe_increase_mb
        passed_count = len(evaluations) - encoded_count
        efficiency_summary = EfficiencySummary(
            min_saved_mb=eff.min_saved_mb,
            min_mb_per_min=eff.min_mb_per_min,
            exempt_saved_mb=eff.exempt_saved_mb,
            encode_duration_sec=encode_duration_sec,
            low_savings_count=low_savings_count,
            low_efficiency_count=low_efficiency_count,
            skipped_over_duration_sec=skipped_over_duration_sec,
            skipped_over_potential_saved_mb=skipped_over_potential_saved_mb,
        )

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
            efficiency_summary=efficiency_summary,
            evaluations=evaluations,
        )
