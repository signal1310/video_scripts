"""분류 정책 계층 모듈: 판정 문맥과 결정 타입, 절약 선별, 구간별 정책 함수와 기본 정책 순서"""

from collections.abc import Callable
from dataclasses import dataclass

from src.core.config import LEGACY_COMPAT_CODECS, SAVINGS_BASIS_ENCODE, ClassifierConfig
from src.core.models import (
    SECONDS_PER_MINUTE,
    DownscaleReason,
    QualityZone,
    SmartAction,
    VideoProps,
)
from src.core.resolution_tiers import ResolutionTier
from src.core.size_estimator import STATIC_SCREEN_RATIO_THRESHOLD

ExceptionRule = Callable[[VideoProps], bool]


@dataclass(frozen=True, slots=True)
class DownscaleEstimate:
    """축소 후보 규격의 출력 해상도, 출력 기준 지표, 예상 용량과 인코딩 시간

    - 축소 후에도 화질 보호 대상이 아닌 경우에만 생성되며, 정책은 이 값으로 절약 선별과 분류를 결정
    """

    tier: ResolutionTier
    reason: DownscaleReason
    target_width: int
    target_height: int
    target_optimal_kbps: int
    target_bitrate_ratio: float
    source_ratio_at_target: float
    after_mb: float
    potential_saved_mb: float
    encode_sec: float


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
    source_codec_weight: float = 1.0
    target_codec_weight: float = 1.0
    downscale: DownscaleEstimate | None = None


@dataclass(frozen=True, slots=True)
class ClassificationDecision:
    """분류 정책이 산출하는 판정과 선택적 용량 예측 결과"""

    smart_action: SmartAction
    size_estimate: tuple[float, float] | None = None
    use_low_quality_preset: bool = False


ClassificationPolicy = Callable[[EvaluationContext], ClassificationDecision | None]


LEGACY_BLOCK_NOISE_MIN_WEIGHT: float = 1.30
LEGACY_BLOCK_NOISE_MAX_RATIO: float = 0.90
INTRA_CODEC_MIN_WEIGHT: float = 2.0
# - 기본 가중치에서 H.264(1.00) -> HEVC(0.80) 이득이 정확히 1.25라 적정 H.264도 의도적으로 코덱 전환 후보에 포함
CODEC_UPGRADE_MIN_GAIN_RATIO: float = 1.25
# - 가중치 나눗셈의 부동소수점 오차로 경계값(1.00 / 0.80 등)이 빠지지 않도록 두는 허용 오차
CODEC_GAIN_COMPARE_TOLERANCE: float = 1e-9


def _calc_saved_mb_per_min(saved_mb: float, seconds: float) -> float:
    """작업 길이(재생 또는 예상 인코딩 시간) 1분당 절감 효율(MB/분) 계산"""
    if seconds <= 0:
        return 0.0
    return saved_mb / (seconds / SECONDS_PER_MINUTE)


def _is_below_savings_efficiency(
    ctx: EvaluationContext, saved_mb: float, encode_sec: float
) -> bool:
    """선택한 절약 선별 기준의 분당 절약량이 최소 기준 미만인지 판별

    - 재생 시간 기준: 재생 시간 1분당 절약량, 대용량 절약 예외 적용
    - 예상 인코딩 시간 기준: 예상 인코딩 시간 1분당 절약량, 예외 미적용, 예상 시간이 0이면 선별 생략
    """
    savings = ctx.config.savings
    if savings.min_mb_per_min <= 0:
        return False
    if savings.selection_basis == SAVINGS_BASIS_ENCODE:
        work_sec = encode_sec
    else:
        if savings.exempt_saved_mb > 0 and saved_mb >= savings.exempt_saved_mb:
            return False
        work_sec = ctx.video.duration
    if work_sec <= 0:
        return False
    return _calc_saved_mb_per_min(saved_mb, work_sec) < savings.min_mb_per_min


def should_use_low_quality_preset(
    zone: QualityZone,
    source_ratio: float,
    target_ratio: float,
    source_weight: float,
    target_weight: float,
) -> bool:
    """저화질, 블록 노이즈 증폭 위험, 코덱 역전, 초저비트레이트 정적 화면 여부를 판별"""
    # 정적 화면 조건은 저화질 하한이 기본값(0.80)이면 저화질 조건에 포함되며, 하한을 정적 임계값 미만으로 낮춘 경우에만 추가로 작동
    return (
        zone == QualityZone.LOW
        or (
            source_weight >= LEGACY_BLOCK_NOISE_MIN_WEIGHT
            and source_ratio < LEGACY_BLOCK_NOISE_MAX_RATIO
        )
        or (source_weight < target_weight and target_ratio < 1.0)
        or source_ratio < STATIC_SCREEN_RATIO_THRESHOLD
    )


def _is_low_quality_preset_ctx(ctx: EvaluationContext) -> bool:
    return should_use_low_quality_preset(
        ctx.quality_zone,
        ctx.bitrate_ratio,
        ctx.target_bitrate_ratio,
        ctx.source_codec_weight,
        ctx.target_codec_weight,
    )


def _make_encode_decision(
    smart_action: SmartAction, ctx: EvaluationContext
) -> ClassificationDecision:
    return ClassificationDecision(
        smart_action,
        use_low_quality_preset=_is_low_quality_preset_ctx(ctx),
    )


def _evaluate_savings_filter(
    ctx: EvaluationContext, saved_mb: float, encode_sec: float
) -> ClassificationDecision | None:
    """최소 총 절약량 및 분당 최소 절약량 미달 시 원본 보존 결정을 반환"""
    if saved_mb < ctx.config.savings.min_saved_mb:
        return ClassificationDecision(SmartAction.PASS_LOW_SAVED)
    if _is_below_savings_efficiency(ctx, saved_mb, encode_sec):
        return ClassificationDecision(SmartAction.PASS_LOW_PER_MIN)
    return None


def _policy_exception_rule(ctx: EvaluationContext) -> ClassificationDecision | None:
    """사용자 예외 규칙과 일치하면 원본 보존 판정 반환"""
    matched = (
        ctx.is_exception_matched
        if ctx.is_exception_matched is not None
        else any(rule(ctx.video) for rule in ctx.exception_rules)
    )
    if matched:
        return ClassificationDecision(SmartAction.PASS_EXCEPTION)
    return None


def _policy_hdr(ctx: EvaluationContext) -> ClassificationDecision | None:
    """HDR 영상은 현재 인코딩 설정으로 색 정보를 잃을 수 있어 화질 등급과 무관하게 원본 보존"""
    if ctx.video.is_hdr:
        return ClassificationDecision(SmartAction.PASS_HDR)
    return None


def _is_force_legacy_compat_target(ctx: EvaluationContext) -> bool:
    """구형 코덱 호환 재인코딩 옵션이 켜져 있고 미지원 코덱인지 판별"""
    if not ctx.config.encode.force_legacy_compat:
        return False
    codec_key = (ctx.video.codec or "").lower().strip()
    return codec_key in LEGACY_COMPAT_CODECS


def _policy_legacy_compat(ctx: EvaluationContext) -> ClassificationDecision | None:
    """구형 코덱 호환 재인코딩 옵션이 켜져 있고 미지원 코덱이면 절약 선별 없이 재인코딩"""
    if not _is_force_legacy_compat_target(ctx):
        return None
    return _make_encode_decision(SmartAction.ENCODE_COMPAT, ctx)


def _policy_downscale(ctx: EvaluationContext) -> ClassificationDecision | None:
    """축소 후보가 있으면 KF 초과, 구형 코덱 호환은 바로 축소 재인코딩, 그 외는 절약 선별 후 축소 재인코딩

    - 절약 미달이면 원인과 무관하게 축소 기준 절약 값으로 보존 판정하고 원본 해상도 재판정은 하지 않음
    - 축소 후보가 없거나 축소 후에도 화질 보호 대상이면 None으로 기존 정책에 넘김
    """
    estimate = ctx.downscale
    if estimate is None:
        return None
    size_estimate = (estimate.after_mb, estimate.potential_saved_mb)
    if not (ctx.is_keyframe_exceeded or _is_force_legacy_compat_target(ctx)):
        blocked = _evaluate_savings_filter(
            ctx, estimate.potential_saved_mb, estimate.encode_sec
        )
        if blocked is not None:
            return ClassificationDecision(blocked.smart_action, size_estimate=size_estimate)
    return ClassificationDecision(SmartAction.ENCODE_DOWNSCALE, size_estimate=size_estimate)


def _evaluate_codec_savings_filter(
    ctx: EvaluationContext,
) -> ClassificationDecision | None:
    """코덱 전환 후보가 절약 선별에서 막히면 전환 절약 미달로 원본 보존 결정을 반환"""
    if _evaluate_savings_filter(ctx, ctx.potential_saved_mb, ctx.estimated_encode_sec) is None:
        return None
    return ClassificationDecision(SmartAction.PASS_CODEC_SAVED)


def _policy_intra_codec(ctx: EvaluationContext) -> ClassificationDecision | None:
    """인트라 전용 코덱(C_src >= 2.0)은 화질 등급과 무관하게 절약 선별을 거쳐 코덱 전환으로 분류"""
    if ctx.source_codec_weight < INTRA_CODEC_MIN_WEIGHT:
        return None
    if not ctx.is_keyframe_exceeded and (blocked := _evaluate_codec_savings_filter(ctx)):
        return blocked
    return _make_encode_decision(SmartAction.ENCODE_CODEC_UPGRADE, ctx)


def _policy_over_zone(ctx: EvaluationContext) -> ClassificationDecision | None:
    """과화질 구간의 절약 선별과 키프레임 초과 우선 인코딩 판정"""
    if ctx.quality_zone != QualityZone.OVER:
        return None
    # - 키프레임 초과 영상은 구간 이동 성능 확보를 위해 절약 선별과 무관하게 재인코딩 진행
    if not ctx.is_keyframe_exceeded and (
        blocked := _evaluate_savings_filter(ctx, ctx.potential_saved_mb, ctx.estimated_encode_sec)
    ):
        return blocked
    return _make_encode_decision(SmartAction.ENCODE_CQ22_OVER, ctx)


def _policy_optimal_zone(ctx: EvaluationContext) -> ClassificationDecision | None:
    """적정 구간의 키프레임 초과 및 코덱 전환 절약 포착 판정"""
    if ctx.quality_zone != QualityZone.OPTIMAL:
        return None
    if ctx.is_keyframe_exceeded:
        return _make_encode_decision(SmartAction.ENCODE_CQ22_OPT_KF, ctx)
    if (
        ctx.target_codec_weight > 0
        and ctx.source_codec_weight / ctx.target_codec_weight
        >= CODEC_UPGRADE_MIN_GAIN_RATIO - CODEC_GAIN_COMPARE_TOLERANCE
    ):
        if blocked := _evaluate_codec_savings_filter(ctx):
            return blocked
        return _make_encode_decision(SmartAction.ENCODE_CODEC_UPGRADE, ctx)
    return ClassificationDecision(SmartAction.PASS_OPTIMAL)


def _policy_low_zone(ctx: EvaluationContext) -> ClassificationDecision | None:
    """저화질 구간의 키프레임 간격 초과 재인코딩 또는 원본 보존 판정"""
    if ctx.quality_zone != QualityZone.LOW:
        return None
    if ctx.is_keyframe_exceeded:
        return _make_encode_decision(SmartAction.ENCODE_LOW_KF, ctx)
    return ClassificationDecision(SmartAction.PASS_LOW)


DEFAULT_CLASSIFICATION_POLICIES: tuple[ClassificationPolicy, ...] = (
    _policy_exception_rule,
    _policy_hdr,
    _policy_downscale,
    _policy_legacy_compat,
    _policy_intra_codec,
    _policy_over_zone,
    _policy_optimal_zone,
    _policy_low_zone,
)
