"""비디오 화질, 키프레임 평가 및 CQ22 기반 분류 전략 판정 코어 엔진 모듈"""

from collections.abc import Mapping, Sequence

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
    ClassifierConfig,
    ThresholdsConfig,
    get_encode_codec_family,
)
from src.core.downscale_resolver import resolve_downscale
from src.core.models import (
    QualityZone,
    SavingsSummary,
    SmartAction,
    SummaryReport,
    VideoEvaluation,
    VideoProps,
)
from src.core.quality_metrics import (
    calc_optimal_pair,
    estimate_encode_duration_sec,
    resolve_quality_zone,
)
from src.core.size_estimator import estimate_after_size_mb

CQ26_PRESET_SPEED_MULTIPLIER: float = 0.85
# - 디코딩 부하와 CQ26 가속은 미실측 추정치라 표시용 예상 시간에만 반영하고 절약 선별에는 쓰지 않음
SOURCE_DECODE_COMPLEXITY: dict[str, float] = {
    **{alias: 1.15 for alias in CODEC_WEIGHT_GROUPS["av1"]},
    "prores": 1.10,
    "dnxhd": 1.10,
    **{alias: 1.05 for alias in CODEC_WEIGHT_GROUPS["hevc_vp9"]},
    "mjpeg": 1.05,
}


class VideoEvaluator:
    """해상도, 코덱, FPS 기반 최적 비트레이트 산출 및 분류 판정 엔진

    원본 코덱 기준 저화질 판정, 출력 코덱 기준 과화질 판정, 키프레임 1초 교정 용량 예측,
    실익, 시간 효율 선별과 HDR 보존을 결합해 판정 수행
    """

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
        return estimate_encode_duration_sec(vid, config) * decode_factor * preset_factor

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
    def _evaluate_precomputed(
        cls,
        vid: VideoProps,
        source_opt_kbps: int,
        target_opt_kbps: int,
        is_excluded: bool | None,
        config: ClassifierConfig,
        rules: tuple[ExceptionRule, ...],
        policies: Sequence[ClassificationPolicy] | None = None,
        manual_target: str | None = None,
    ) -> VideoEvaluation:
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
        is_keyframe_exceeded = cls.is_keyframe_exceeded(vid, config.thresholds)
        use_cq26 = should_use_low_quality_preset(
            zone, source_ratio, target_ratio, src_weight, target_weight
        )
        estimated_encode_sec = estimate_encode_duration_sec(vid, config)
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

        downscale, skip_reason = resolve_downscale(vid, config, source_ratio, manual_target)
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
            downscale=downscale,
        )
        decision = cls.resolve_decision(ctx, policies=policies)
        # - 예외 규칙과 HDR 보존 영상은 축소를 검토하지 않으므로 출력 해상도 정보도 남기지 않음
        if decision.smart_action in (SmartAction.PASS_EXCEPTION, SmartAction.PASS_HDR):
            downscale, skip_reason = None, None
        is_target = decision.target_dirname is not None
        # - 정책이 축소 용량을 넘긴 경우 예상과 가정 값 모두 축소 기준을 따르고, 나머지는 원본 해상도 재인코딩 기준
        if decision.size_estimate is not None:
            hyp_after_mb, hyp_delta_mb = decision.size_estimate
        else:
            hyp_after_mb = encoded_after_mb
            hyp_delta_mb = vid.vid_size_MB - encoded_after_mb
        if is_target:
            after_mb, delta_mb = hyp_after_mb, hyp_delta_mb
        else:
            after_mb, delta_mb = vid.vid_size_MB, 0.0

        # - 축소 후보가 있으면 출력 기준 지표와 인코딩 시간을 표시(원본 디코딩 부하 미반영)
        if downscale is not None:
            target_opt_kbps = downscale.target_optimal_kbps
            target_ratio = downscale.target_bitrate_ratio
            display_encode_sec = downscale.encode_sec

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
            target_width=downscale.target_width if downscale else None,
            target_height=downscale.target_height if downscale else None,
            target_tier=downscale.tier if downscale else None,
            downscale_reason=downscale.reason if downscale else None,
            downscale_skip_reason=skip_reason,
            manual_target=manual_target,
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
        source_kbps, target_kbps = calc_optimal_pair(vid, config)
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
        manual_targets: Mapping[str, str] | None = None,
    ) -> SummaryReport:
        """전체 비디오 목록을 일괄 평가하고 인코딩 대상 수량과 총 용량 변화를 집계하여 SummaryReport를 생성

        manual_targets는 파일명 -> 규격 키 또는 축소 안 함 값의 직접 지정 맵이며, 없는 파일은 자동 판정
        """
        rules = tuple(exception_rules or [])

        evaluations: list[VideoEvaluation] = []
        for vid in videos:
            is_excluded = any(rule(vid) for rule in rules) if rules else False
            source_kbps, target_kbps = calc_optimal_pair(vid, config)
            evaluations.append(
                cls._evaluate_precomputed(
                    vid,
                    source_kbps,
                    target_kbps,
                    is_excluded,
                    config,
                    rules,
                    policies=policies,
                    manual_target=manual_targets.get(vid.filename) if manual_targets else None,
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
