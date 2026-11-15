"""비디오 화질, 키프레임 평가 및 CQ22 기반 분류 전략 판정 코어 엔진 모듈"""

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
    QualityZone,
    SavingsSummary,
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
    is_keyframe_exceeded: bool
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
SizeZoneEstimator = Callable[
    [VideoProps, float, bool, ThresholdsConfig], tuple[float, float]
]

MIN_OPTIMAL_BITRATE_KBPS: int = 100
FPS_HYSTERESIS_TOLERANCE: float = 2.0
OVER_KEYFRAME_INCREASE_FACTOR: float = 1.04
DEFAULT_UNKNOWN_KF_SEC: float = 5.0
MIN_CLAMP_KF_SEC: float = 2.2
MIN_HYPOTHETICAL_KF_SEC: float = 1.0
MAX_CLAMP_KF_SEC: float = 10.0
REFERENCE_ENCODE_WIDTH: int = 1920
REFERENCE_ENCODE_HEIGHT: int = 1080
MIN_ENCODE_SPEED_FACTOR: float = 0.01
MIN_WORKLOAD_FACTOR: float = 1e-6


def _calc_resolution_fps_factors(
    width: int,
    height: int,
    fps: float,
    bitrate_model: BitrateModelConfig,
) -> tuple[float, float]:
    """해상도 면적 비율과 FPS 히스테리시스를 반영한 비선형 가중 계수(area_factor, fps_factor) 산출"""
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
    return area_factor, fps_factor


def _estimate_over_size_change(
    vid: VideoProps,
    bitrate_ratio: float,
    is_keyframe_exceeded: bool,
    _thresholds: ThresholdsConfig,
) -> tuple[float, float]:
    """과화질 구간의 목표 비트레이트 수렴 및 키프레임 오버헤드 반영 용량 예측"""
    orig_mb = vid.vid_size_MB
    keyframe_increase_factor = (
        OVER_KEYFRAME_INCREASE_FACTOR if is_keyframe_exceeded else 1.0
    )
    after_mb = min(
        orig_mb, orig_mb * (1.0 / bitrate_ratio) * keyframe_increase_factor
    )
    return after_mb, orig_mb - after_mb


def _calc_keyframe_1s_increase_ratio(kf_sec: float, increase_rate: float) -> float:
    """클램핑된 키프레임 간격에서 1초 키프레임 교정 시 증가하는 용량 비율 산출"""
    clamped = min(kf_sec, MAX_CLAMP_KF_SEC)
    return increase_rate * (1.0 - (1.0 / clamped))


def _estimate_keyframe_only_size_change(
    vid: VideoProps,
    _bitrate_ratio: float,
    _is_keyframe_exceeded: bool,
    thresholds: ThresholdsConfig,
) -> tuple[float, float]:
    """적정, 저화질 구간에서 1초 키프레임 교정 시 발생하는 용량 증가분 예측"""
    orig_mb = vid.vid_size_MB
    # - 실측 키프레임 미보유 시 기본 5초, 최소 2.2초 하한으로 보수적 교정 증가분을 산정
    keyframe_interval_val = max(
        vid.keyframe_interval or DEFAULT_UNKNOWN_KF_SEC, MIN_CLAMP_KF_SEC
    )
    increase_ratio = _calc_keyframe_1s_increase_ratio(
        keyframe_interval_val, thresholds.keyframe_1s_increase_rate
    )
    after_mb = orig_mb * (1.0 + increase_ratio)
    return after_mb, orig_mb - after_mb


_ZONE_SIZE_ESTIMATORS: dict[QualityZone, SizeZoneEstimator] = {
    QualityZone.OVER: _estimate_over_size_change,
    QualityZone.OPTIMAL: _estimate_keyframe_only_size_change,
    QualityZone.LOW: _estimate_keyframe_only_size_change,
}


def _estimate_hypothetical_kf_increase_ratio(
    vid: VideoProps,
    thresholds: ThresholdsConfig,
) -> float:
    """측정된 키프레임 간격을 기준으로 1초 키프레임 인코딩 시 추가되는 용량 증가율 산출"""
    kf = vid.keyframe_interval
    # - 가상 시뮬레이션은 미측정, 1초 이하 키프레임에 추가 오버헤드를 부여하지 않음
    if kf is None or kf <= MIN_HYPOTHETICAL_KF_SEC:
        return 0.0
    return _calc_keyframe_1s_increase_ratio(kf, thresholds.keyframe_1s_increase_rate)


def _estimate_hypothetical_size_change(
    vid: VideoProps,
    bitrate_ratio: float,
    quality_zone: QualityZone,
    is_keyframe_exceeded: bool,
    thresholds: ThresholdsConfig,
) -> tuple[float, float]:
    """원본 보존 항목까지 포함해 만약 인코딩할 때의 예상 용량(MB)과 증감(MB) 계산"""
    orig_mb = vid.vid_size_MB
    if bitrate_ratio <= 0 or orig_mb <= 0:
        return orig_mb, 0.0
    if quality_zone == QualityZone.OVER:
        return _estimate_over_size_change(
            vid, bitrate_ratio, is_keyframe_exceeded, thresholds
        )
    bitrate_factor = (1.0 / bitrate_ratio) if bitrate_ratio > 1.0 else 1.0
    increase_ratio = _estimate_hypothetical_kf_increase_ratio(vid, thresholds)
    after_mb = orig_mb * bitrate_factor * (1.0 + increase_ratio)
    return after_mb, orig_mb - after_mb


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
    """과화질 구간의 실익, 시간 효율 선별과 키프레임 불량 우선 인코딩 판정"""
    if ctx.quality_zone != QualityZone.OVER:
        return None
    # - 키프레임 불량 영상은 탐색 성능 교정을 위해 절감 효율과 무관하게 인코딩 진행
    if not ctx.is_keyframe_exceeded:
        saved = _calc_over_potential_saved_mb(ctx.video.vid_size_MB, ctx.bitrate_ratio)
        savings = ctx.config.savings
        if saved < savings.min_saved_mb:
            return ClassificationDecision(SmartAction.PASS_LOW_SAVED, None)
        is_exempt = savings.exempt_saved_mb > 0 and saved >= savings.exempt_saved_mb
        if (
            not is_exempt
            and savings.min_mb_per_min > 0
            and ctx.video.duration > 0
            and _calc_saved_mb_per_min(saved, ctx.video.duration) < savings.min_mb_per_min
        ):
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
    _policy_over_zone,
    _policy_optimal_zone,
    _policy_low_zone,
)


class VideoEvaluator:
    """해상도, 코덱, FPS 기반 최적 비트레이트 산출 및 분류 판정 엔진

    키프레임 1초 교정 용량 예측, 실익, 시간 효율 선별, 화질 3구간 결합 판정 수행
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
    def calc_encode_workload_ratio(
        width: int,
        height: int,
        fps: float,
        bitrate_model: BitrateModelConfig,
    ) -> float:
        """1080p 기준 FPS 대비 현재 해상도, FPS의 상대 인코딩 작업량 배수 산출"""
        ref_area, ref_fps = _calc_resolution_fps_factors(
            REFERENCE_ENCODE_WIDTH,
            REFERENCE_ENCODE_HEIGHT,
            bitrate_model.base_fps,
            bitrate_model,
        )
        curr_area, curr_fps = _calc_resolution_fps_factors(
            width, height, fps, bitrate_model
        )
        return (curr_area * curr_fps) / max(ref_area * ref_fps, MIN_WORKLOAD_FACTOR)

    @classmethod
    def estimate_encode_duration_sec(
        cls,
        vid: VideoProps,
        config: ClassifierConfig,
    ) -> float:
        """비선형 해상도, FPS 가중치와 기준 인코딩 배속으로 예상 인코딩 소요 시간(초) 산출"""
        if vid.duration <= 0:
            return 0.0
        workload_ratio = cls.calc_encode_workload_ratio(
            vid.width, vid.height, vid.fps, config.bitrate_model
        )
        speed = max(config.encode.encode_speed_factor, MIN_ENCODE_SPEED_FACTOR)
        return max(0.0, (vid.duration * workload_ratio) / speed)

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
    def is_keyframe_exceeded(vid: VideoProps, thresholds: ThresholdsConfig) -> bool:
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
        is_keyframe_exceeded: bool,
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
        return estimator(vid, bitrate_ratio, is_keyframe_exceeded, thresholds)

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
        is_keyframe_exceeded = cls.is_keyframe_exceeded(vid, config.thresholds)

        ctx = EvaluationContext(
            video=vid,
            optimal_kbps=opt_kbps,
            bitrate_ratio=r,
            quality_zone=zone,
            is_keyframe_exceeded=is_keyframe_exceeded,
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
                is_keyframe_exceeded,
                True,
                config.thresholds,
            )
        else:
            after_mb, delta_mb = vid.vid_size_MB, 0.0

        hyp_after_mb, hyp_delta_mb = (
            (after_mb, delta_mb)
            if decision.target_dirname is not None
            else _estimate_hypothetical_size_change(
                vid, r, zone, is_keyframe_exceeded, config.thresholds
            )
        )

        estimated_encode_sec = cls.estimate_encode_duration_sec(vid, config)

        return VideoEvaluation(
            video=vid,
            optimal_kbps=opt_kbps,
            bitrate_ratio=r,
            quality_zone=zone,
            is_keyframe_exceeded=is_keyframe_exceeded,
            expected_after_mb=after_mb,
            expected_delta_mb=delta_mb,
            smart_action=decision.smart_action,
            target_dirname=decision.target_dirname,
            hypothetical_after_mb=hyp_after_mb,
            hypothetical_delta_mb=hyp_delta_mb,
            estimated_encode_sec=estimated_encode_sec,
        )

    @classmethod
    def evaluate_video(
        cls,
        vid: VideoProps,
        config: ClassifierConfig,
        exception_rules: list[ExceptionRule] | None = None,
        policies: Sequence[ClassificationPolicy] | None = None,
    ) -> VideoEvaluation:
        """단일 비디오에 대해 해상도, 코덱 기반 목표치와 키프레임을 종합 평가하여 분류 결과를 생성"""
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

        evaluations: list[VideoEvaluation] = []
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
            evaluations.append(
                cls._evaluate_precomputed(
                    vid,
                    opt_kbps,
                    r,
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
            excluded_over_potential_saved_mb += _calc_over_potential_saved_mb(
                vid.vid_size_MB, ev.bitrate_ratio
            )
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
