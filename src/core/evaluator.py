"""비디오 화질·키프레임 평가 및 CQ22 기반 분류 전략 판정 코어 엔진 모듈"""

from collections.abc import Callable

from src.core.config import ClassifierConfig
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

MIN_OPTIMAL_BITRATE_KBPS: int = 100
FPS_HYSTERESIS_TOLERANCE: float = 2.0
OVER_KEYFRAME_OVERHEAD_FACTOR: float = 1.04
DEFAULT_UNKNOWN_KF_SEC: float = 5.0
MIN_CLAMP_KF_SEC: float = 2.2
MAX_CLAMP_KF_SEC: float = 10.0


class VideoEvaluator:
    """해상도·코덱·FPS 기반 최적 비트레이트 산출 및 분류 처방 결정 엔진

    키프레임 1초 교정 용량 예측, 소형 영상 분포 분석, 화질 3구간 결합 판정 수행
    """

    @staticmethod
    def _calc_over_potential_saved_mb(vid_size_mb: float, bitrate_ratio: float) -> float:
        """과화질 영상의 기준 비트레이트 대비 이론적 절감 잠재량(MB) 계산"""
        if bitrate_ratio <= 0:
            return 0.0
        return vid_size_mb * max(0.0, 1.0 - (1.0 / bitrate_ratio))

    @staticmethod
    def calc_optimal_bitrate(
        width: int,
        height: int,
        config: ClassifierConfig,
        fps: float = 30.0,
        codec: str = "h264",
    ) -> int:
        """해상도·FPS·코덱 효율을 반영한 목표 화질(CQ22 수준) 최적 비트레이트(kbps) 산출

        수식: B_opt(W, H, fps, codec) = B_base_h264 * ((W * H) / (W_base * H_base))^alpha * K_fps * C_codec
        """
        base_pixels = max(config.base_width * config.base_height, 1)
        curr_pixels = max(width * height, 1)

        area_factor: float = (curr_pixels / base_pixels) ** config.area_scaling_exponent

        # - 기준 FPS 근처의 미세 오차로 과도한 보정이 걸리지 않도록 히스테리시스 여유치를 적용
        if fps > config.base_fps + FPS_HYSTERESIS_TOLERANCE:
            fps_factor: float = (fps / config.base_fps) ** config.fps_scaling_exponent
        else:
            fps_factor = 1.0

        codec_factor: float = config.get_codec_weight(codec)

        optimal_kbps = round(
            config.base_target_bitrate * area_factor * fps_factor * codec_factor
        )
        return max(optimal_kbps, MIN_OPTIMAL_BITRATE_KBPS)

    @staticmethod
    def classify_quality_zone(
        bitrate_ratio: float, config: ClassifierConfig
    ) -> QualityZone:
        """최적 비트레이트 대비 현재 비트레이트 비율(r = B_curr / B_opt)을 설정된 경계 배수와 비교해 화질 등급을 3구간으로 판정"""
        if bitrate_ratio < config.low_bitrate_ratio:
            return QualityZone.LOW
        if bitrate_ratio > config.over_bitrate_ratio:
            return QualityZone.OVER
        return QualityZone.OPTIMAL

    @staticmethod
    def is_keyframe_defective(vid: VideoProps, config: ClassifierConfig) -> bool:
        """비디오 키프레임 간격의 허용 상한(keyframe_threshold) 초과 여부 판별

        소형 영상(짧은 소형 영상 포함) 여부와 무관하게 전체 영상에 동일 적용
        """
        return (
            vid.keyframe_interval is not None
            and vid.keyframe_interval > config.keyframe_threshold
        )

    @staticmethod
    def estimate_size_change_mb(
        vid: VideoProps,
        bitrate_ratio: float,
        quality_zone: QualityZone,
        is_keyframe_defective: bool,
        will_encode: bool,
        config: ClassifierConfig,
    ) -> tuple[float, float]:
        """인코딩 완료 후 예상 파일 크기(expected_after_mb) 및 용량 변화량(expected_delta_mb) 계산

        양수(+)는 용량 절감량, 음수(-)는 1초 키프레임 교정에 따른 용량 증가분 의미
        """
        orig_mb = vid.vid_size_MB
        if not will_encode or bitrate_ratio <= 0:
            return orig_mb, 0.0

        if quality_zone == QualityZone.OVER:
            # - 과화질 영상은 CQ 22 수준(B_opt)으로 수렴하도록 크기를 줄이며, 키프레임 불량 교정 시 I-프레임 오버헤드를 함께 고려
            keyframe_overhead_factor = (
                OVER_KEYFRAME_OVERHEAD_FACTOR if is_keyframe_defective else 1.0
            )
            after_mb = min(
                orig_mb, orig_mb * (1.0 / bitrate_ratio) * keyframe_overhead_factor
            )
            return after_mb, orig_mb - after_mb

        # - 적정화질 또는 저화질 상태에서 키프레임만 1초로 교정할 때는, 화질을 보존한 채 I-프레임 빈도 증가에 따른 약 3~6% 증가분을 예측
        keyframe_interval_val = min(
            max(vid.keyframe_interval or DEFAULT_UNKNOWN_KF_SEC, MIN_CLAMP_KF_SEC),
            MAX_CLAMP_KF_SEC,
        )
        overhead_ratio = config.keyframe_1s_overhead_rate * (
            1.0 - (1.0 / keyframe_interval_val)
        )
        after_mb = orig_mb * (1.0 + overhead_ratio)
        return after_mb, orig_mb - after_mb

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
        rules = exception_rules or []
        total_over_count = 0
        small_over_count = 0
        total_potential_saved_mb = 0.0
        small_potential_saved_mb = 0.0

        for vid in videos:
            if any(rule(vid) for rule in rules):
                continue
            opt_kbps = cls.calc_optimal_bitrate(
                vid.width, vid.height, config, vid.fps, vid.codec
            )
            r = vid.vid_kbps / opt_kbps
            if r > config.over_bitrate_ratio:
                saved_mb = cls._calc_over_potential_saved_mb(vid.vid_size_MB, r)
                total_over_count += 1
                total_potential_saved_mb += saved_mb

                if vid.vid_size_MB < config.small_footage_mb:
                    small_over_count += 1
                    small_potential_saved_mb += saved_mb

        share_ratio = (
            small_potential_saved_mb / total_potential_saved_mb
            if total_potential_saved_mb > 0
            else 0.0
        )
        share_pct = share_ratio * PERCENT_MULTIPLIER
        summary_prefix = (
            f"소형 영상(<{config.small_footage_mb:.0f}MB)의 예상 절감 합계가 "
            f"{small_potential_saved_mb:.1f}MB(전체 절감 가능량의 {share_pct:.1f}%)"
        )

        if (
            share_ratio >= config.small_footage_cumulative_ratio
            or small_potential_saved_mb >= config.small_footage_cumulative_mb
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
            small_threshold_mb=config.small_footage_mb,
            min_saved_mb=config.small_footage_min_saved_mb,
            total_over_count=total_over_count,
            small_over_count=small_over_count,
            total_potential_saved_mb=total_potential_saved_mb,
            small_potential_saved_mb=small_potential_saved_mb,
            small_share_ratio=share_ratio,
            strategy=strategy,
            reason=reason,
        )

    @staticmethod
    def _decide_action(
        vid: VideoProps,
        bitrate_ratio: float,
        zone: QualityZone,
        is_keyframe_defective: bool,
        is_small: bool,
        small_dist: SmallFootageDistribution,
        config: ClassifierConfig,
        rules: list[ExceptionRule],
    ) -> tuple[SmartAction, str | None]:
        """화질 구간·키프레임·예외규칙에 따라 SmartAction과 대상 폴더명을 결정"""
        if any(rule(vid) for rule in rules):
            return SmartAction.PASS_EXCEPTION, None

        if zone == QualityZone.OVER:
            potential_saved_mb = VideoEvaluator._calc_over_potential_saved_mb(
                vid.vid_size_MB, bitrate_ratio
            )
            # - 키프레임이 불량인 영상은 소형 영상이라도 탐색 편의성을 위해 무조건 인코딩을 진행
            if (
                not is_keyframe_defective
                and is_small
                and (
                    small_dist.strategy == FootageStrategy.IGNORE_SMALL
                    or potential_saved_mb < config.small_footage_min_saved_mb
                )
            ):
                return SmartAction.PASS_SMALL_FOOTAGE, None
            return SmartAction.ENCODE_CQ22_OVER, config.dir_standard_cq22

        if zone == QualityZone.OPTIMAL:
            if is_keyframe_defective:
                return SmartAction.ENCODE_CQ22_OPT_KF, config.dir_standard_cq22
            return SmartAction.PASS_OPTIMAL, None

        if is_keyframe_defective:
            return SmartAction.ENCODE_LOW_KF, config.dir_low_quality_keyframe
        return SmartAction.PASS_LOW, None

    @classmethod
    def evaluate_video(
        cls,
        vid: VideoProps,
        config: ClassifierConfig,
        small_dist: SmallFootageDistribution,
        exception_rules: list[ExceptionRule] | None = None,
    ) -> VideoEvaluation:
        """단일 비디오에 대해 해상도·코덱 기반 목표치와 키프레임을 종합 평가하여 분류 결과를 생성"""
        rules = exception_rules or []
        opt_kbps = cls.calc_optimal_bitrate(
            vid.width, vid.height, config, vid.fps, vid.codec
        )
        r = vid.vid_kbps / opt_kbps
        zone = cls.classify_quality_zone(r, config)
        is_keyframe_defective = cls.is_keyframe_defective(vid, config)
        is_small = vid.vid_size_MB < config.small_footage_mb

        action, target_dirname = cls._decide_action(
            vid, r, zone, is_keyframe_defective, is_small, small_dist, config, rules
        )
        will_encode = target_dirname is not None
        if will_encode:
            after_mb, delta_mb = cls.estimate_size_change_mb(
                vid, r, zone, is_keyframe_defective, True, config
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
            smart_action=action,
            target_dirname=target_dirname,
        )

    @classmethod
    def evaluate_all(
        cls,
        root_dir: str,
        videos: list[VideoProps],
        config: ClassifierConfig,
        exception_rules: list[ExceptionRule] | None = None,
    ) -> SummaryReport:
        """전체 비디오 목록을 일괄 평가하고 인코딩 대상 수량과 총 용량 변화를 집계하여 SummaryReport를 생성"""
        small_dist = cls.analyze_small_footage_distribution(
            videos, config, exception_rules
        )
        evaluations: list[VideoEvaluation] = [
            cls.evaluate_video(vid, config, small_dist, exception_rules)
            for vid in videos
        ]

        total_orig_mb = sum(e.video.vid_size_MB for e in evaluations)
        total_after_mb = sum(e.expected_after_mb for e in evaluations)
        gross_saved_mb = sum(
            e.expected_delta_mb for e in evaluations if e.expected_delta_mb > 0
        )
        keyframe_increase_mb = sum(
            -e.expected_delta_mb for e in evaluations if e.expected_delta_mb < 0
        )
        net_saved_mb = gross_saved_mb - keyframe_increase_mb
        encoded_count = sum(1 for e in evaluations if e.target_dirname is not None)
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
