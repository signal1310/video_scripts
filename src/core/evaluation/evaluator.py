"""비디오 화질, 키프레임 평가 및 화질 기준값 기반 판정 코어 엔진 모듈"""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from src.core.codec_groups import CODEC_WEIGHT_GROUPS
from src.core.config import (
    ClassifierConfig,
    ThresholdsConfig,
)
from src.core.encoder_specs import get_encode_codec_family, resolve_level_cap_kbps
from src.core.evaluation.classification_policy import (
    DEFAULT_CLASSIFICATION_POLICIES,
    ClassificationDecision,
    ClassificationPolicy,
    EvaluationContext,
    ExceptionRule,
    should_use_low_quality_preset,
)
from src.core.evaluation.downscale_resolver import resolve_downscale
from src.core.evaluation.quality_metrics import (
    calc_optimal_pair,
    estimate_encode_duration_sec,
    resolve_quality_zone,
)
from src.core.evaluation.size_estimator import (
    calc_keyframe_increase_ratio,
    estimate_after_size_mb,
    estimate_video_stream_mb,
)
from src.core.models import (
    ActiveFrame,
    QualityZone,
    SavingsSummary,
    SummaryReport,
    VideoEvaluation,
    VideoProps,
)
from src.core.quality_target import PROTECT_CQ_OFFSET, protect_cq_value
from src.core.smart_action import SmartAction

LOW_QUALITY_PRESET_SPEED_MULTIPLIER: float = 0.85
# - 디코딩 부하와 저화질 보호 프리셋 가속은 미실측 추정치라 표시용 예상 시간에만 반영하고 절약 선별에는 쓰지 않음
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
        """기준 예상 시간에 원본 디코딩 부하와 저화질 보호 프리셋 계수를 곱한 표시용 예상 인코딩 시간(초) 산출"""
        codec_key = (vid.codec or "").lower().strip()
        decode_factor = SOURCE_DECODE_COMPLEXITY.get(codec_key, 1.0)
        preset_factor = LOW_QUALITY_PRESET_SPEED_MULTIPLIER if is_low_quality_preset else 1.0
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
        judged_vid: VideoProps,
        source_opt_kbps: int,
        target_opt_kbps: int,
        is_excluded: bool | None,
        config: ClassifierConfig,
        rules: tuple[ExceptionRule, ...],
        policies: Sequence[ClassificationPolicy] | None = None,
        manual_target: str | None = None,
        known_encode_tag_names: tuple[str, ...] = (),
        encoded_history_filenames: frozenset[str] = frozenset(),
    ) -> VideoEvaluation:
        """원본 영상과 판정 입력 영상(활성 화면 기준)의 기준 비트레이트로 판정 결과 생성

        - judged_vid는 기준 비트레이트와 축소 후보 산출에만 쓰고, 용량과 인코딩 시간은 원본 영상 기준
        """
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
        # - 저화질 보호 경로는 기준 품질값 + 보호 오프셋으로 인코딩하므로 품질값 차이만큼 용량 계수를 적용
        # - NVENC 자동 레벨 상한은 명시 최대 비트레이트가 없을 때 인코더가 거는 출력 상한이므로 예상 출력에 반영
        encoded_after_mb = estimate_after_size_mb(
            vid,
            target_ratio,
            config.thresholds.keyframe_1s_increase_rate,
            cap_at_original=zone == QualityZone.OVER,
            source_ratio=source_ratio,
            codec_weight_ratio=codec_weight_ratio,
            base_fps=config.bitrate_model.base_fps,
            output_delta_cq=PROTECT_CQ_OFFSET if use_cq26 else 0.0,
            max_video_kbps=resolve_level_cap_kbps(
                config.encode.encode_codec, vid.width, vid.height, vid.fps
            ),
            encoder=config.encode.encode_codec,
        )
        # - 키프레임 초과 영상은 결정 33이 품질값을 올려 목표 크기(S_other + V_orig x (1 + r_kf))에 맞추므로
        #   판정 예상도 그 목표를 넘지 않게 해 과도한 절약 표시를 막음
        if is_keyframe_exceeded:
            kf_target_mb = vid.vid_size_MB + estimate_video_stream_mb(
                vid
            ) * calc_keyframe_increase_ratio(
                vid.keyframe_interval,
                config.thresholds.keyframe_1s_increase_rate,
                fps=vid.fps,
                base_fps=config.bitrate_model.base_fps,
                source_ratio=source_ratio,
            )
            encoded_after_mb = min(encoded_after_mb, kf_target_mb)

        downscale, skip_reason = resolve_downscale(judged_vid, config, source_ratio, manual_target)
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
            known_encode_tag_names=known_encode_tag_names,
            encoded_history_filenames=encoded_history_filenames,
        )
        decision = cls.resolve_decision(ctx, policies=policies)
        # - 예외 규칙, HDR 보존, 이미 재인코딩 영상은 축소를 검토하지 않으므로 출력 해상도 정보도 남기지 않음
        if decision.smart_action in (
            SmartAction.PASS_EXCEPTION,
            SmartAction.PASS_HDR,
            SmartAction.PASS_ALREADY_ENCODED,
        ):
            downscale, skip_reason = None, None
        is_target = decision.smart_action.is_encode
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
            hypothetical_after_mb=hyp_after_mb,
            hypothetical_delta_mb=hyp_delta_mb,
            estimated_encode_sec=display_encode_sec,
            target_optimal_kbps=target_opt_kbps,
            target_bitrate_ratio=target_ratio,
            use_low_quality_preset=decision.use_low_quality_preset,
            output_cq=(
                protect_cq_value(config.encode.baseline_cq)
                if decision.use_low_quality_preset
                else config.encode.baseline_cq
            ),
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
        active_frame: ActiveFrame | None = None,
    ) -> VideoEvaluation:
        """단일 비디오에 대해 원본, 출력 코덱 기준 목표치와 키프레임을 종합 평가하여 분류 결과를 생성

        active_frame이 없으면 전체 프레임을 판정 입력으로 사용
        """
        rules = tuple(exception_rules or [])
        judged = _judged_view(vid, active_frame)
        source_kbps, target_kbps = calc_optimal_pair(judged, config)
        return cls._evaluate_precomputed(
            vid,
            judged,
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
        active_frames: Mapping[str, ActiveFrame] | None = None,
        known_encode_tag_names: tuple[str, ...] = (),
        encoded_history_filenames: frozenset[str] = frozenset(),
    ) -> SummaryReport:
        """전체 비디오 목록을 일괄 평가하고 인코딩 대상 수량과 총 용량 변화를 집계하여 SummaryReport를 생성

        manual_targets는 파일명 -> 규격 키 또는 축소 안 함 값의 직접 지정 맵이며, 없는 파일은 자동 판정
        active_frames는 파일명 -> 활성 화면 맵이며, 없는 파일은 전체 프레임으로 판정
        """
        rules = tuple(exception_rules or [])

        evaluations: list[VideoEvaluation] = []
        for vid in videos:
            is_excluded = any(rule(vid) for rule in rules) if rules else False
            active = active_frames.get(vid.filename) if active_frames else None
            judged = _judged_view(vid, active)
            source_kbps, target_kbps = calc_optimal_pair(judged, config)
            evaluations.append(
                cls._evaluate_precomputed(
                    vid,
                    judged,
                    source_kbps,
                    target_kbps,
                    is_excluded,
                    config,
                    rules,
                    policies=policies,
                    manual_target=manual_targets.get(vid.filename) if manual_targets else None,
                    known_encode_tag_names=known_encode_tag_names,
                    encoded_history_filenames=encoded_history_filenames,
                )
            )
        return _build_summary_report(root_dir, evaluations, config)


def _judged_view(vid: VideoProps, active_frame: ActiveFrame | None) -> VideoProps:
    """판정 입력 영상 정보를 반환하며, 활성 화면이 없으면 전체 프레임 그대로 사용"""
    if active_frame is None:
        return vid
    return replace(vid, width=active_frame.width, height=active_frame.height)


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
        if ev.is_encode_target:
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
