import unittest
from unittest import mock

from src.core.config import (
    VALID_ENCODE_CODECS,
    ClassifierConfig,
    EncodeConfig,
    SavingsConfig,
    ThresholdsConfig,
)
from src.core.encoder_specs import get_encode_codec_family
from src.core.evaluation import quality_metrics
from src.core.evaluation.classification_policy import (
    DEFAULT_CLASSIFICATION_POLICIES,
    ClassificationDecision,
    EvaluationContext,
)
from src.core.evaluation.evaluator import VideoEvaluator
from src.core.evaluation.size_estimator import (
    DEFAULT_UNKNOWN_KF_SEC,
    KF_CLAMP_MAX_SEC,
    calc_keyframe_increase_ratio,
    estimate_after_size_mb,
    estimate_video_stream_mb,
)
from src.core.models import (
    BYTES_PER_MB,
    ActiveFrame,
    DownscaleSkipReason,
    QualityZone,
    VideoEvaluation,
    VideoProps,
)
from src.core.quality_target import (
    PROTECT_CQ_DELTA_MAX,
    corrected_target_kbps,
    general_maxrate_kbps,
    protect_cq_continuous,
    quality_size_factor,
)
from src.core.resolution_tiers import ENCODE_DESIGNATION_KEY
from src.core.smart_action import ActionBadgeTone, DownscaleReason, SmartAction
from src.core.sorting import WindowsNameOrder, compare_natural_order, compare_natural_tokens
from tests._legacy_model import use_legacy_size_model as _use_legacy_size_model

_AUDIO_KBPS = 128
_RATE = 0.06


def _size_mb(kbps: int, duration: float, aud_kbps: int = _AUDIO_KBPS) -> float:
    """비디오와 오디오 비트레이트, 길이로 일관된 파일 용량(MB) 계산"""
    return (kbps + aud_kbps) * 1000 * duration / 8 / BYTES_PER_MB


def _vid(
    name: str,
    kbps: int,
    duration: float,
    *,
    kf: float | None = 1.0,
    width: int = 1280,
    height: int = 720,
    fps: float = 30.0,
    codec: str = "h264",
    is_hdr: bool = False,
    is_10bit: bool = False,
    has_encode_tag: bool = False,
) -> VideoProps:
    """비트레이트와 길이에 맞춰 용량이 일관된 테스트 영상 생성 (기본값: 720p30 H.264, 키프레임 1초)"""
    return VideoProps(
        name, width, height, 0, fps, kbps, _AUDIO_KBPS, _size_mb(kbps, duration),
        duration, codec, kf, is_hdr=is_hdr, is_10bit=is_10bit, has_encode_tag=has_encode_tag,
    )


def _continuous_cq(ev: VideoEvaluation, cfg: ClassifierConfig) -> float:
    """판정에 쓰는 목표 비트레이트 비율로 연속 보호 품질값을 다시 계산한 값

    - 같은 정의를 두 번 적지 않으려고 테스트에서만 계산 경로를 재구성
    """
    return protect_cq_continuous(
        cfg.encode.baseline_cq, ev.target_bitrate_ratio, cfg.encode.encode_codec
    )


class TestEvaluatorModule(unittest.TestCase):
    """비트레이트 스케일링 수식, 화질 등급 경계값, 용량 추정, 절약 선별, HDR 보존 등 코어 판정 엔진을 검증"""

    def setUp(self) -> None:
        _use_legacy_size_model(self)
        self.cfg = ClassifierConfig()

    def test_optimal_bitrate_resolution_monotonicity(self) -> None:
        """360p부터 8K까지 해상도 증가에 따라 최적 비트레이트가 엄격히 단조 증가하는지 검증"""
        bm = self.cfg.bitrate_model
        cw = self.cfg.codec_weights
        opt_720 = quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "h264")
        self.assertEqual(opt_720, 1500)

        opt_360 = quality_metrics.calc_optimal_bitrate(640, 360, bm, cw, 30.0, "h264")
        opt_480 = quality_metrics.calc_optimal_bitrate(854, 480, bm, cw, 30.0, "h264")
        opt_1080 = quality_metrics.calc_optimal_bitrate(1920, 1080, bm, cw, 30.0, "h264")
        opt_1440 = quality_metrics.calc_optimal_bitrate(2560, 1440, bm, cw, 30.0, "h264")
        opt_4k = quality_metrics.calc_optimal_bitrate(3840, 2160, bm, cw, 30.0, "h264")
        opt_8k = quality_metrics.calc_optimal_bitrate(7680, 4320, bm, cw, 30.0, "h264")

        self.assertTrue(100 < opt_360 < opt_480 < opt_720 < opt_1080 < opt_1440 < opt_4k < opt_8k)

    def test_optimal_bitrate_extreme_dimensions(self) -> None:
        """16x16, 0x0 등 극한의 초소형 해상도 입력 시에도 최소 하한선(100kbps)을 유지하는지 검증"""
        bm = self.cfg.bitrate_model
        cw = self.cfg.codec_weights
        self.assertEqual(quality_metrics.calc_optimal_bitrate(16, 16, bm, cw, 30.0, "h264"), 100)
        self.assertEqual(quality_metrics.calc_optimal_bitrate(0, 0, bm, cw, 30.0, "h264"), 100)

    def test_optimal_bitrate_fps_scaling(self) -> None:
        """30fps 이하와 불감대 이내(32fps)는 1.0 가중치를 유지하고, 60fps 및 120fps는 비선형 지수에 따라 증가하는지 검증"""
        bm = self.cfg.bitrate_model
        cw = self.cfg.codec_weights
        base = quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "h264")
        self.assertEqual(base, quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 15.0, "h264"))
        self.assertEqual(base, quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 24.0, "h264"))
        self.assertEqual(base, quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 32.0, "h264"))

        opt_60 = quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 60.0, "h264")
        opt_120 = quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 120.0, "h264")
        self.assertEqual(opt_60, round(1500 * ((60.0 / 30.0) ** 0.60)))
        self.assertEqual(opt_120, round(1500 * ((120.0 / 30.0) ** 0.60)))
        self.assertTrue(base < opt_60 < opt_120)

    def test_optimal_bitrate_codec_weights(self) -> None:
        """코덱별 효율 가중치와 미등록 코덱 기본값(1.00) 적용을 검증"""
        bm = self.cfg.bitrate_model
        cw = self.cfg.codec_weights
        self.assertEqual(quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "av1"), 975)
        self.assertEqual(quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "hevc"), 1200)
        self.assertEqual(quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "h264"), 1500)
        self.assertEqual(quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "mpeg4"), 1950)
        self.assertEqual(quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "mpeg2video"), 2400)
        self.assertEqual(quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "prores"), 3750)
        self.assertEqual(quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, " HEVC "), 1200)
        self.assertEqual(quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "unknown_codec_xyz"), 1500)

    def test_encode_codec_family_covers_every_choice(self) -> None:
        """설정의 모든 목표 인코딩 코덱이 코덱 그룹 별칭으로 연결되어 출력 기준 최적 비트레이트 계산에 쓰이는지 검증"""
        expected = {
            "hevc_nvenc": "hevc", "libx265": "hevc", "hevc_qsv": "hevc", "hevc_amf": "hevc",
            "h264_nvenc": "h264", "libx264": "h264", "av1_nvenc": "av1", "libsvtav1": "av1",
        }
        for codec in VALID_ENCODE_CODECS:
            with self.subTest(codec=codec):
                self.assertEqual(get_encode_codec_family(codec), expected[codec])
        self.assertEqual(get_encode_codec_family("unknown_codec"), "h264")

    def test_classify_quality_zone_strict_boundaries(self) -> None:
        """화질 3구간 경계값(0.80 미만: LOW, 0.80~1.20: OPTIMAL, 1.20 초과: OVER)의 엄밀한 판정을 검증"""
        th = self.cfg.thresholds
        self.assertEqual(quality_metrics.classify_quality_zone(0.7999, th), QualityZone.LOW)
        self.assertEqual(quality_metrics.classify_quality_zone(0.8000, th), QualityZone.OPTIMAL)
        self.assertEqual(quality_metrics.classify_quality_zone(1.2000, th), QualityZone.OPTIMAL)
        self.assertEqual(quality_metrics.classify_quality_zone(1.2001, th), QualityZone.OVER)

    def test_resolve_quality_zone_uses_source_for_low_and_output_for_over(self) -> None:
        """저화질은 원본 코덱 기준을 우선하고, 과화질은 출력 코덱 기준으로 판정하는지 검증"""
        th = self.cfg.thresholds
        # 720p H.264 1500kbps: 원본 기준 1.00, HEVC 출력 기준 1.25
        self.assertEqual(quality_metrics.resolve_quality_zone(1.0, 1.25, th), QualityZone.OVER)
        # 원본 기준 저화질이면 출력 기준이 과화질이어도 저화질 보호 우선
        self.assertEqual(quality_metrics.resolve_quality_zone(0.79, 1.215, th), QualityZone.LOW)
        self.assertEqual(quality_metrics.resolve_quality_zone(1.0, 1.0, th), QualityZone.OPTIMAL)

    def test_output_codec_changes_quality_zone_of_h264_source(self) -> None:
        """목표 인코딩 코덱을 바꾸면 같은 원본의 과화질 판정과 출력 비트레이트 비율이 달라지는지 검증"""
        vid = _vid("h264.mp4", 1500, 60.0)
        hevc = VideoEvaluator.evaluate_video(vid, ClassifierConfig())
        self.assertEqual(hevc.quality_zone, QualityZone.OVER)
        self.assertEqual(hevc.target_optimal_kbps, 1200)
        self.assertAlmostEqual(hevc.bitrate_ratio, 1.0)
        self.assertAlmostEqual(hevc.target_bitrate_ratio, 1.25)

        x264 = VideoEvaluator.evaluate_video(
            vid, ClassifierConfig(encode=EncodeConfig(encode_codec="libx264"))
        )
        self.assertEqual(x264.quality_zone, QualityZone.OPTIMAL)
        self.assertAlmostEqual(x264.target_bitrate_ratio, 1.0)

    def test_is_keyframe_exceeded_boundaries(self) -> None:
        """키프레임 결함 판단 임계값(기본 2.2초) 경계 판정 및 None 방어를 검증"""
        th = self.cfg.thresholds
        def make(kf: float | None) -> VideoProps:
            return VideoProps("t.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 10.0, "h264", keyframe_interval=kf)

        self.assertFalse(VideoEvaluator.is_keyframe_exceeded(make(2.2), th))
        self.assertTrue(VideoEvaluator.is_keyframe_exceeded(make(2.2001), th))
        self.assertFalse(VideoEvaluator.is_keyframe_exceeded(make(None), th))

    def test_stream_size_is_bitrate_times_duration_capped_by_file_size(self) -> None:
        """비디오 스트림 용량은 비트레이트 x 길이로 계산하고 파일 전체 용량을 넘지 않으며, 길이를 모르면 파일 전체로 간주"""
        vid = _vid("s.mp4", 3000, 10.0)
        self.assertAlmostEqual(estimate_video_stream_mb(vid), 3000 * 1000 * 10 / 8 / BYTES_PER_MB)
        oversized = VideoProps("o.mp4", 1280, 720, 0, 30.0, 9000, 128, 1.0, 10.0, "h264", 1.0)
        self.assertEqual(estimate_video_stream_mb(oversized), 1.0)
        unknown_len = VideoProps("u.mp4", 1280, 720, 0, 30.0, 3000, 128, 7.0, 0.0, "h264", 1.0)
        self.assertEqual(estimate_video_stream_mb(unknown_len), 7.0)

    def test_after_size_splits_stream_and_fixed_parts(self) -> None:
        """예상 인코딩 후 크기는 오디오 등 고정 부분을 유지하고, 영상 스트림에만 비트레이트 비율과 키프레임 증가율을 적용하는지 검증"""
        vid = _vid("s.mp4", 3000, 10.0, kf=1.0)
        stream = estimate_video_stream_mb(vid)
        fixed = vid.vid_size_MB - stream
        self.assertAlmostEqual(fixed, _AUDIO_KBPS * 1000 * 10 / 8 / BYTES_PER_MB)
        self.assertAlmostEqual(estimate_after_size_mb(vid, 2.0, _RATE, cap_at_original=False), fixed + stream / 2)

        vid_kf5 = _vid("k.mp4", 3000, 10.0, kf=5.0)
        after_kf5 = estimate_after_size_mb(vid_kf5, 1.0, _RATE, cap_at_original=False)
        self.assertAlmostEqual(after_kf5, fixed + stream * (1.0 + _RATE * (1.0 - 1.0 / 5.0)))
        vid_none = _vid("n.mp4", 3000, 10.0, kf=None)
        self.assertAlmostEqual(estimate_after_size_mb(vid_none, 1.0, _RATE, cap_at_original=False), after_kf5)

    def test_after_size_cap_applies_only_when_requested(self) -> None:
        """과화질 구간은 원본보다 커지지 않도록 상한을 적용하고, 그 외 구간은 키프레임 증가분을 그대로 반영하는지 검증"""
        vid = _vid("c.mp4", 3000, 10.0, kf=5.0)
        uncapped = estimate_after_size_mb(vid, 0.5, _RATE, cap_at_original=False)
        self.assertGreater(uncapped, vid.vid_size_MB)
        self.assertAlmostEqual(estimate_after_size_mb(vid, 0.5, _RATE, cap_at_original=True), vid.vid_size_MB)

    def test_keyframe_increase_ratio_clamps_interval(self) -> None:
        """키프레임 미측정은 기본 5초, 1초 이하는 증가 없음, 긴 간격은 10초 상한으로 클램핑하는지 검증"""
        self.assertAlmostEqual(calc_keyframe_increase_ratio(None, _RATE), _RATE * (1.0 - 1.0 / DEFAULT_UNKNOWN_KF_SEC))
        self.assertEqual(calc_keyframe_increase_ratio(0.5, _RATE), 0.0)
        self.assertEqual(calc_keyframe_increase_ratio(1.0, _RATE), 0.0)
        self.assertAlmostEqual(calc_keyframe_increase_ratio(2.2, _RATE), _RATE * (1.0 - 1.0 / 2.2))
        self.assertAlmostEqual(calc_keyframe_increase_ratio(100.0, _RATE), _RATE * (1.0 - 1.0 / KF_CLAMP_MAX_SEC))

    def test_keyframe_increase_ratio_applies_fps_and_static_screen_weights(self) -> None:
        """FPS 보정(0.5~1.5 클램핑)과 정적 화면 초저비트레이트 가중치를 검증(시나리오 H, I)"""
        base = calc_keyframe_increase_ratio(5.0, _RATE, fps=30.0, base_fps=30.0)
        self.assertAlmostEqual(
            calc_keyframe_increase_ratio(5.0, _RATE, fps=60.0, base_fps=30.0),
            base * ((30.0 / 60.0) ** 0.5),
        )
        # 120fps 이상은 하한 0.5, 저FPS는 상한 1.5 클램핑
        self.assertAlmostEqual(calc_keyframe_increase_ratio(5.0, _RATE, fps=120.0, base_fps=30.0), base * 0.5)
        self.assertAlmostEqual(calc_keyframe_increase_ratio(5.0, _RATE, fps=240.0, base_fps=30.0), base * 0.5)
        self.assertAlmostEqual(calc_keyframe_increase_ratio(5.0, _RATE, fps=5.0, base_fps=30.0), base * 1.5)

        # 정적 화면: r_quality < 0.35이면 0.35 / max(r_quality, 0.15) 가중
        self.assertAlmostEqual(calc_keyframe_increase_ratio(5.0, _RATE, source_ratio=0.35), base)
        self.assertAlmostEqual(calc_keyframe_increase_ratio(5.0, _RATE, source_ratio=0.175), base * 2.0)
        self.assertAlmostEqual(
            calc_keyframe_increase_ratio(5.0, _RATE, source_ratio=0.05),
            base * (0.35 / 0.15),
        )

    def test_after_size_reflects_codec_upgrade_and_downgrade_ratios(self) -> None:
        """코덱 전환 압축 이득(시나리오 C)과 코덱 역전 용량 증가(시나리오 G)를 검증"""
        vid = _vid("legacy.mp4", 1200, 60.0, kf=1.0, codec="mpeg4")
        stream = estimate_video_stream_mb(vid)
        fixed = vid.vid_size_MB - stream
        # 시나리오 C: mpeg4(1.30) -> hevc(0.80), r_quality=0.615 < 1.0이어도 C_target/C_src=0.8/1.3 적용
        after_upgrade = estimate_after_size_mb(
            vid,
            output_ratio=1.0,
            keyframe_increase_rate=_RATE,
            cap_at_original=False,
            source_ratio=1200 / 1950,
            codec_weight_ratio=0.80 / 1.30,
        )
        self.assertAlmostEqual(after_upgrade, fixed + stream * (0.80 / 1.30))

        # 시나리오 G: av1(0.65) -> h264(1.00), r_saving=0.50 < 1.0이면 min(1/0.50, 1.00/0.65) 배 증가
        av1_vid = _vid("av1.mp4", 750, 60.0, kf=1.0, codec="av1")
        av1_stream = estimate_video_stream_mb(av1_vid)
        av1_fixed = av1_vid.vid_size_MB - av1_stream
        after_downgrade = estimate_after_size_mb(
            av1_vid,
            output_ratio=0.50,
            keyframe_increase_rate=_RATE,
            cap_at_original=False,
            source_ratio=750 / 975,
            codec_weight_ratio=1.00 / 0.65,
        )
        self.assertAlmostEqual(after_downgrade, av1_fixed + av1_stream * (1.00 / 0.65))

    def test_savings_filter_playback_basis(self) -> None:
        """재생 시간 기준 절약 선별의 최소 절감량, 분당 절감량, 대용량 예외, 키프레임 불량 우선 규칙을 검증"""
        cfg_eff = ClassifierConfig(savings=SavingsConfig(min_saved_mb=5.0, min_mb_per_min=3.0, exempt_saved_mb=50.0))

        # 1. 절감량 < min_saved_mb (2400kbps, r_out=2.0, 10초 -> 약 1.4MB 절감)
        vid_small = _vid("small.mp4", 2400, 10.0)
        ev_small = VideoEvaluator.evaluate_video(vid_small, cfg_eff)
        self.assertEqual(ev_small.smart_action, SmartAction.PASS_LOW_SAVED)
        self.assertFalse(ev_small.is_encode_target)
        self.assertEqual(ev_small.expected_delta_mb, 0.0)

        # 2. 절감량은 충분하지만 재생 시간 1분당 절감량 미달 (1500kbps, r_out=1.25, 10분 -> 약 21.5MB, 2.1MB/분)
        vid_slow = _vid("slow.mp4", 1500, 600.0)
        ev_slow = VideoEvaluator.evaluate_video(vid_slow, cfg_eff)
        self.assertEqual(ev_slow.smart_action, SmartAction.PASS_LOW_PER_MIN)
        self.assertFalse(ev_slow.is_encode_target)

        # 3. 같은 영상이라도 절감량이 면제 기준 이상이면 분당 기준 면제
        vid_exempt = _vid("exempt.mp4", 1500, 1800.0)
        self.assertEqual(VideoEvaluator.evaluate_video(vid_exempt, cfg_eff).smart_action, SmartAction.ENCODE_OVER)

        # 4. exempt_saved_mb == 0 이면 면제 비활성
        cfg_no_exempt = ClassifierConfig(savings=SavingsConfig(min_saved_mb=5.0, min_mb_per_min=3.0, exempt_saved_mb=0.0))
        self.assertEqual(VideoEvaluator.evaluate_video(vid_exempt, cfg_no_exempt).smart_action, SmartAction.PASS_LOW_PER_MIN)

        # 5. 키프레임 불량이면 절감 효율과 무관하게 인코딩 대상 유지
        vid_bad_kf = _vid("bad_kf.mp4", 1500, 600.0, kf=5.0)
        self.assertEqual(VideoEvaluator.evaluate_video(vid_bad_kf, cfg_eff).smart_action, SmartAction.ENCODE_OVER)

    def test_savings_basis_playback_and_encode_select_differently(self) -> None:
        """같은 영상이라도 재생 시간 기준은 미달, 예상 인코딩 시간 기준은 통과로 갈릴 수 있는지 검증"""
        vid = _vid("basis.mp4", 1500, 600.0)  # 재생 시간 1분당 2.1MB, 예상 인코딩 시간 1분당 약 4.8MB
        base = dict(min_saved_mb=5.0, min_mb_per_min=3.0, exempt_saved_mb=50.0)
        cfg_play = ClassifierConfig(savings=SavingsConfig(**base, selection_basis="playback"))
        cfg_enc = ClassifierConfig(savings=SavingsConfig(**base, selection_basis="encode"))
        self.assertEqual(VideoEvaluator.evaluate_video(vid, cfg_play).smart_action, SmartAction.PASS_LOW_PER_MIN)
        self.assertEqual(VideoEvaluator.evaluate_video(vid, cfg_enc).smart_action, SmartAction.ENCODE_OVER)

    def test_encode_basis_skips_efficiency_check_when_time_is_zero(self) -> None:
        """예상 인코딩 시간이나 재생 길이가 0이면 분당 효율 검사를 생략하고 절감량 기준만 적용하는지 검증"""
        vid = VideoProps("z.mp4", 1280, 720, 0, 30.0, 1500, 128, 100.0, 0.0, "h264", 1.0)
        for basis in ("playback", "encode"):
            cfg = ClassifierConfig(savings=SavingsConfig(min_saved_mb=5.0, min_mb_per_min=3.0, selection_basis=basis))
            with self.subTest(basis=basis):
                self.assertEqual(VideoEvaluator.evaluate_video(vid, cfg).smart_action, SmartAction.ENCODE_OVER)

    def test_over_zone_thresholds_are_inclusive_at_boundaries(self) -> None:
        """최소 절감량, 분당 절감량, 대용량 면제 기준이 경계값과 같을 때 인코딩 쪽으로 판정되는지 검증"""
        vid = _vid("v.mp4", 2400, 600.0)  # r_out=2.0, 절감량 약 85.8MB
        base_ev = VideoEvaluator.evaluate_video(vid, ClassifierConfig(savings=SavingsConfig(min_saved_mb=0.0, min_mb_per_min=0.0, exempt_saved_mb=0.0)))
        saved = base_ev.preview_delta_mb
        per_min = saved / (vid.duration / 60.0)

        def action(min_saved: float, min_per_min: float, exempt: float) -> SmartAction:
            savings = SavingsConfig(min_saved_mb=min_saved, min_mb_per_min=min_per_min, exempt_saved_mb=exempt)
            return VideoEvaluator.evaluate_video(vid, ClassifierConfig(savings=savings)).smart_action

        self.assertEqual(action(saved, 0.0, 0.0), SmartAction.ENCODE_OVER)
        self.assertEqual(action(saved * 1.000001, 0.0, 0.0), SmartAction.PASS_LOW_SAVED)
        self.assertEqual(action(0.0, per_min * (1 - 1e-9), 0.0), SmartAction.ENCODE_OVER)
        self.assertEqual(action(0.0, per_min * 1.000001, 0.0), SmartAction.PASS_LOW_PER_MIN)
        self.assertEqual(action(0.0, per_min * 1.000001, saved), SmartAction.ENCODE_OVER)
        self.assertEqual(action(0.0, per_min * 1.000001, saved * 1.000001), SmartAction.PASS_LOW_PER_MIN)

    def test_hdr_is_preserved_before_zone_policies(self) -> None:
        """HDR 영상은 과화질이어도 원본을 보존하고, 예외 규칙은 HDR보다 우선하는지 검증"""
        vid = _vid("hdr.mp4", 2400, 60.0, is_hdr=True)
        ev = VideoEvaluator.evaluate_video(vid, self.cfg)
        self.assertEqual(ev.smart_action, SmartAction.PASS_HDR)
        self.assertEqual(ev.quality_zone, QualityZone.OVER)
        self.assertFalse(ev.is_encode_target)
        self.assertEqual(ev.expected_delta_mb, 0.0)
        self.assertGreater(ev.preview_delta_mb, 0.0)
        self.assertEqual(SmartAction.PASS_HDR.reason_label, "HDR 보존")

        ev_exc = VideoEvaluator.evaluate_video(vid, self.cfg, exception_rules=[lambda v: True])
        self.assertEqual(ev_exc.smart_action, SmartAction.PASS_EXCEPTION)

    def test_evaluate_all_savings_summary_and_empty_guard(self) -> None:
        """evaluate_all의 빈 목록 안전 가드와 SavingsSummary, 작업 시간 집계를 검증"""
        empty_report = VideoEvaluator.evaluate_all("C:/empty", [], self.cfg)
        self.assertEqual(empty_report.total_count, 0)
        self.assertEqual(empty_report.encode_avg_mb_per_min, 0.0)
        self.assertEqual(empty_report.savings_summary.encode_duration_sec, 0.0)
        self.assertEqual(empty_report.savings_summary.excluded_over_count, 0)

        cfg_eff = ClassifierConfig(savings=SavingsConfig(min_saved_mb=5.0, min_mb_per_min=3.0, exempt_saved_mb=50.0))
        enc = _vid("enc.mp4", 2400, 60.0)
        vids = [
            enc,
            _vid("small.mp4", 2400, 30.0),
            _vid("small2.mp4", 2400, 30.0),
            _vid("slow.mp4", 1500, 600.0),
            _vid("opt.mp4", 1200, 60.0, codec="hevc"),
        ]
        rep = VideoEvaluator.evaluate_all("C:/archive", vids, cfg_eff)
        summary = rep.savings_summary
        enc_stream = estimate_video_stream_mb(enc)
        self.assertEqual(rep.encoded_count, 1)
        self.assertEqual(rep.passed_count, 4)
        self.assertAlmostEqual(rep.net_saved_mb, enc_stream * 0.5)
        self.assertAlmostEqual(summary.encode_duration_sec, 60.0)
        self.assertAlmostEqual(rep.encode_avg_mb_per_min, enc_stream * 0.5)
        self.assertEqual(summary.low_saved_count, 2)
        self.assertEqual(summary.low_per_min_count, 1)
        self.assertEqual(summary.excluded_over_count, 3)
        self.assertAlmostEqual(summary.excluded_over_duration_sec, 660.0)
        slow_stream = estimate_video_stream_mb(vids[3])
        small_stream = estimate_video_stream_mb(vids[1])
        self.assertAlmostEqual(
            summary.excluded_over_potential_saved_mb,
            small_stream * 0.5 * 2 + slow_stream * 0.2,
        )

    def test_evaluate_video_decision_matrix_all_actions(self) -> None:
        """화질 3구간과 키프레임 상태의 조합에 따른 SmartAction 및 타겟 폴더 할당을 검증"""
        v_over = _vid("o.mp4", 2400, 60.0)
        ev_over = VideoEvaluator.evaluate_video(v_over, self.cfg)
        self.assertEqual(ev_over.smart_action, SmartAction.ENCODE_OVER)
        self.assertEqual((ev_over.is_encode_target, ev_over.use_low_quality_preset), (True, False))

        ev_opt_good = VideoEvaluator.evaluate_video(_vid("og.mp4", 1200, 60.0, codec="hevc"), self.cfg)
        self.assertEqual(ev_opt_good.smart_action, SmartAction.PASS_OPTIMAL)
        self.assertFalse(ev_opt_good.is_encode_target)

        ev_opt_bad = VideoEvaluator.evaluate_video(_vid("ob.mp4", 1300, 60.0, kf=5.0), self.cfg)
        self.assertEqual(ev_opt_bad.smart_action, SmartAction.ENCODE_OPT_KF)
        self.assertEqual((ev_opt_bad.is_encode_target, ev_opt_bad.use_low_quality_preset), (True, False))

        ev_low_good = VideoEvaluator.evaluate_video(_vid("lg.mp4", 800, 60.0), self.cfg)
        self.assertEqual(ev_low_good.smart_action, SmartAction.PASS_LOW)
        self.assertFalse(ev_low_good.is_encode_target)

        ev_low_bad = VideoEvaluator.evaluate_video(_vid("lb.mp4", 800, 60.0, kf=5.0), self.cfg)
        self.assertEqual(ev_low_bad.smart_action, SmartAction.ENCODE_LOW_KF)
        self.assertEqual((ev_low_bad.is_encode_target, ev_low_bad.use_low_quality_preset), (True, True))

    def test_evaluate_video_exception_rules(self) -> None:
        """사용자 정의 예외 규칙이 일치하면 화질 등급과 무관하게 PASS_EXCEPTION이고 인코딩 대상이 아닌지 검증"""
        vid_over = _vid("vip_archive.mp4", 6000, 60.0, kf=5.0)

        def rule(v: VideoProps) -> bool:
            return "vip" in v.filename

        ev = VideoEvaluator.evaluate_video(vid_over, self.cfg, exception_rules=[rule])
        self.assertEqual(ev.smart_action, SmartAction.PASS_EXCEPTION)
        self.assertFalse(ev.is_encode_target)
        self.assertEqual(ev.expected_delta_mb, 0.0)

    def test_encode_designation_forces_manual_at_source_resolution(self) -> None:
        """인코딩 지정은 이미 인코딩, 적정 영상도 원본 해상도 품질 인코딩 대상으로 바꾸는지 검증"""
        tagged = _vid("tagged.mp4", 1200, 60.0, has_encode_tag=True)
        optimal = _vid("optimal.mp4", 1200, 60.0, codec="hevc")
        for vid in (tagged, optimal):
            with self.subTest(filename=vid.filename):
                rep = VideoEvaluator.evaluate_all(
                    "C:/designated", [vid], self.cfg, manual_targets={vid.filename: ENCODE_DESIGNATION_KEY}
                )
                ev = rep.evaluations[0]
                self.assertEqual(ev.smart_action, SmartAction.ENCODE_MANUAL)
                self.assertTrue(ev.is_encode_target)
                self.assertIsNone(ev.target_tier)
                self.assertIsNone(ev.target_width)
                self.assertEqual(ev.reason_label, "직접 지정")
                self.assertEqual(ev.manual_target, ENCODE_DESIGNATION_KEY)
        # - 지정이 없으면 원래 판정(이미 인코딩, 적정 보존)으로 돌아감
        rep_plain = VideoEvaluator.evaluate_all("C:/designated", [tagged], self.cfg)
        self.assertEqual(rep_plain.evaluations[0].smart_action, SmartAction.PASS_ALREADY_ENCODED)

    def test_summary_report_aggregation_empty_and_normal(self) -> None:
        """SummaryReport 집계 시 빈 디렉터리 및 다중 파일에서의 누적/순수 절감량 계산 정확성을 검증"""
        rep_empty = VideoEvaluator.evaluate_all("C:/empty", [], self.cfg)
        self.assertEqual(rep_empty.total_count, 0)
        self.assertEqual(rep_empty.net_saved_mb, 0.0)

        videos = [_vid("over.mp4", 2400, 60.0), _vid("opt_bad_kf.mp4", 1200, 60.0, kf=5.0, codec="hevc")]
        rep = VideoEvaluator.evaluate_all("C:/test", videos, self.cfg)
        self.assertEqual(rep.total_count, 2)
        self.assertEqual(rep.encoded_count, 2)
        self.assertEqual(rep.passed_count, 0)
        self.assertGreater(rep.gross_saved_mb, 0.0)
        self.assertGreater(rep.keyframe_increase_mb, 0.0)
        self.assertAlmostEqual(rep.net_saved_mb, rep.gross_saved_mb - rep.keyframe_increase_mb)

    def test_custom_classification_policy_injection_and_badge_tones(self) -> None:
        """우선순위 정책 체인 주입으로 판정 확장이 가능하고 SmartAction 배지 범주가 일관적인지 검증"""
        self.assertEqual(SmartAction.ENCODE_OVER.badge_tone, ActionBadgeTone.STD_ENCODE)
        self.assertEqual(SmartAction.ENCODE_LOW_KF.badge_tone, ActionBadgeTone.LOW_KF_ENCODE)
        self.assertEqual(SmartAction.PASS_OPTIMAL.badge_tone, ActionBadgeTone.PASS)
        self.assertEqual(SmartAction.PASS_HDR.badge_tone, ActionBadgeTone.PASS)

        vid_over = _vid("custom.mp4", 2400, 60.0)

        def force_archive_policy(ctx: EvaluationContext) -> ClassificationDecision | None:
            if ctx.video.filename.startswith("custom"):
                return ClassificationDecision(
                    smart_action=SmartAction.PASS_EXCEPTION,
                    size_estimate=(ctx.video.vid_size_MB, 0.0),
                )
            return None

        rep = VideoEvaluator.evaluate_all(
            "C:/test", [vid_over], self.cfg, policies=(force_archive_policy, *DEFAULT_CLASSIFICATION_POLICIES)
        )
        self.assertEqual(rep.passed_count, 1)
        self.assertEqual(rep.evaluations[0].smart_action, SmartAction.PASS_EXCEPTION)
        self.assertEqual(rep.evaluations[0].expected_delta_mb, 0.0)

    def test_hypothetical_preview_for_pass_actions_without_distorting_report(self) -> None:
        """원본 보존 항목도 가상 인코딩 예상 용량을 산출하되 실제 리포트 합계는 왜곡하지 않음을 검증"""
        cfg_eff = ClassifierConfig(savings=SavingsConfig(min_saved_mb=10.0, min_mb_per_min=3.0, exempt_saved_mb=0.0))
        vid_slow = _vid("slow.mp4", 1500, 600.0)  # 시간 비효율 (절감 약 21.5MB, 2.1MB/분)
        vid_small = _vid("small.mp4", 2400, 10.0)  # 절감 미달 (약 1.4MB)
        vid_opt = _vid("opt.mp4", 1300, 60.0, codec="hevc")  # 적정 구간, r_out=1.08
        vid_low_2s = _vid("low2s.mp4", 800, 60.0, kf=2.0, codec="hevc")  # 저화질, 키프레임 2초

        rep = VideoEvaluator.evaluate_all("C:/test", [vid_slow, vid_small, vid_opt, vid_low_2s], cfg_eff)
        self.assertEqual(rep.encoded_count, 0)
        self.assertAlmostEqual(rep.net_saved_mb, 0.0)
        self.assertAlmostEqual(rep.total_expected_after_mb, rep.total_orig_mb)

        ev_slow, ev_small, ev_opt, ev_low_2s = rep.evaluations
        self.assertAlmostEqual(ev_slow.preview_delta_mb, estimate_video_stream_mb(vid_slow) * 0.2)
        self.assertAlmostEqual(ev_small.preview_delta_mb, estimate_video_stream_mb(vid_small) * 0.5)
        self.assertAlmostEqual(ev_opt.preview_delta_mb, estimate_video_stream_mb(vid_opt) * (1 - 1 / (1300 / 1200)))
        # - 저화질 보호 경로는 연속 규칙으로 정한 보호 품질값의 용량 계수와 KF 증가분을 함께 적용해 가상 절약이 양수가 됨
        protect_delta = (ev_low_2s.output_cq or 0.0) - cfg_eff.encode.baseline_cq
        protect_factor = quality_size_factor(protect_delta) * (1.0 + calc_keyframe_increase_ratio(2.0, _RATE))
        self.assertAlmostEqual(
            ev_low_2s.preview_delta_mb,
            estimate_video_stream_mb(vid_low_2s) * (1.0 - protect_factor),
        )

        ev_exc = VideoEvaluator.evaluate_video(vid_slow, cfg_eff, exception_rules=[lambda v: "slow" in v.filename])
        self.assertEqual(ev_exc.smart_action, SmartAction.PASS_EXCEPTION)
        self.assertEqual(ev_exc.expected_delta_mb, 0.0)
        self.assertAlmostEqual(ev_exc.preview_delta_mb, estimate_video_stream_mb(vid_slow) * 0.2)

    def test_estimate_encode_duration_is_linear_in_pixel_rate(self) -> None:
        """예상 인코딩 시간이 해상도와 fps의 화소 처리량에 비례하고 기준 배속으로 나뉘는지 검증"""
        from src.core.analysis.system_probe import recommend_encode_speed_for_profile

        fhd_30 = _vid("fhd30.mp4", 9000, 120.0, width=1920, height=1080)
        hd_30 = _vid("hd30.mp4", 4500, 120.0, width=1280, height=720)
        uhd_60 = _vid("uhd60.mp4", 30000, 120.0, width=3840, height=2160, fps=60.0)
        opt_pass = _vid("opt.mp4", 2600, 120.0, width=1920, height=1080)

        cfg_2x = ClassifierConfig(encode=EncodeConfig(encode_speed_factor=2.0))
        self.assertAlmostEqual(quality_metrics.calc_encode_workload_ratio(1920, 1080, 30.0), 1.0)
        self.assertAlmostEqual(quality_metrics.calc_encode_workload_ratio(3840, 2160, 60.0), 8.0)
        self.assertAlmostEqual(quality_metrics.calc_encode_workload_ratio(1280, 720, 30.0), 0.444, places=3)
        self.assertAlmostEqual(quality_metrics.estimate_encode_duration_sec(fhd_30, cfg_2x), 60.0)
        self.assertLess(
            quality_metrics.estimate_encode_duration_sec(hd_30, cfg_2x),
            quality_metrics.estimate_encode_duration_sec(fhd_30, cfg_2x),
        )
        self.assertAlmostEqual(
            quality_metrics.estimate_encode_duration_sec(uhd_60, cfg_2x),
            quality_metrics.estimate_encode_duration_sec(fhd_30, cfg_2x) * 8.0,
        )
        # 기준 비트레이트 모델의 fps를 바꿔도 인코딩 작업량 기준은 1080p 30fps로 고정
        cfg_fps60 = ClassifierConfig(encode=EncodeConfig(encode_speed_factor=2.0))
        cfg_fps60.bitrate_model.base_fps = 60.0
        self.assertAlmostEqual(quality_metrics.estimate_encode_duration_sec(fhd_30, cfg_fps60), 60.0)

        cfg_x264_2x = ClassifierConfig(
            encode=EncodeConfig(encode_codec="libx264", encode_speed_factor=2.0)
        )
        rep = VideoEvaluator.evaluate_all("C:/test", [fhd_30, opt_pass], cfg_x264_2x)
        self.assertEqual(rep.encoded_count, 1)
        self.assertAlmostEqual(rep.evaluations[0].estimated_encode_sec, 60.0)
        self.assertAlmostEqual(rep.evaluations[1].estimated_encode_sec, 60.0)
        self.assertAlmostEqual(rep.total_estimated_encode_sec, 60.0)

        rec_nvenc = recommend_encode_speed_for_profile(
            16, frozenset({"hevc_nvenc"}), ("NVIDIA GeForce RTX 4070",), target_codec="hevc_nvenc"
        )
        self.assertGreaterEqual(rec_nvenc.recommended_speed_factor, 4.0)
        rec_x265 = recommend_encode_speed_for_profile(
            16, frozenset({"hevc_nvenc"}), ("NVIDIA GeForce RTX 4070",), target_codec="libx265"
        )
        self.assertLess(rec_x265.recommended_speed_factor, rec_nvenc.recommended_speed_factor)
        rec_h264_nv = recommend_encode_speed_for_profile(
            16, frozenset({"h264_nvenc"}), ("NVIDIA GeForce RTX 4070",), target_codec="h264_nvenc"
        )
        self.assertGreater(rec_h264_nv.recommended_speed_factor, rec_nvenc.recommended_speed_factor)
        rec_av1_missing = recommend_encode_speed_for_profile(
            16, frozenset({"hevc_nvenc"}), ("NVIDIA GeForce RTX 3080",), target_codec="av1_nvenc"
        )
        self.assertIn("미감지", rec_av1_missing.detail_summary)

    def test_encode_duration_is_zero_for_non_positive_length(self) -> None:
        for duration in (0.0, -5.0):
            vid = VideoProps("v.mp4", 1920, 1080, 0, 30.0, 9000, 128, 200.0, duration, "h264", 1.0)
            self.assertEqual(quality_metrics.estimate_encode_duration_sec(vid, self.cfg), 0.0)

    def test_compare_natural_order_three_way_and_equivalence(self) -> None:
        """compare_natural_order의 단일 패스 3방향 비교(-1, 0, 1) 및 compare_natural_tokens 일관성 검증"""
        t2 = WindowsNameOrder("Clip_2.mp4")
        t10 = WindowsNameOrder("clip_10.mp4")
        self.assertEqual(compare_natural_order(t2, t10), -1)
        self.assertEqual(compare_natural_order(t10, t2), 1)
        self.assertEqual(compare_natural_order(t2, WindowsNameOrder("CLIP_2.mp4")), 0)
        self.assertEqual(compare_natural_order([1, "a"], [1, "a", 2]), -1)
        self.assertEqual(compare_natural_order([1, "a", 2], [1, "a"]), 1)
        self.assertTrue(compare_natural_tokens(t2, t10))
        self.assertFalse(compare_natural_tokens(t10, t2))

    def test_scenario_a_optimal_codec_upgrade_and_savings_filter(self) -> None:
        """적정 구간에서도 C_src/C_target >= 1.25이면 절약 선별을 거쳐 ENCODE_CODEC_UPGRADE로 분류함을 검증(시나리오 A)"""
        # 1080p30 H.264(1.00) -> HEVC(0.80): C_src/C_target = 1.25, r_quality=0.89(적정), r_saving=1.11(적정)
        vid_pass_savings = _vid("upgrade.mp4", 2600, 120.0, width=1920, height=1080, codec="h264")
        ev = VideoEvaluator.evaluate_video(vid_pass_savings, self.cfg)
        self.assertEqual(ev.quality_zone, QualityZone.OPTIMAL)
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_CODEC_UPGRADE)
        self.assertEqual(ev.smart_action.reason_label, "코덱 전환")
        self.assertEqual(ev.action_label, "품질 인코딩")
        self.assertEqual(ev.planned_cq, 22.0)
        self.assertEqual((ev.is_encode_target, ev.use_low_quality_preset), (True, False))

        # 절약 미달 / 분당 미달 보존은 적정 구간이면 제외분 집계에 넣지 않음
        vid_low_saved = _vid("small_up.mp4", 2600, 30.0, width=1920, height=1080, codec="h264")
        vid_low_rate = _vid("slow_up.mp4", 1300, 600.0, codec="h264")
        rep = VideoEvaluator.evaluate_all("C:/test", [vid_low_saved, vid_low_rate], self.cfg)
        self.assertEqual(rep.evaluations[0].smart_action, SmartAction.PASS_CODEC_SAVED)
        self.assertEqual(rep.evaluations[1].smart_action, SmartAction.PASS_CODEC_SAVED)
        self.assertEqual(rep.savings_summary.low_saved_count, 0)
        self.assertEqual(rep.savings_summary.low_per_min_count, 0)
        self.assertEqual(rep.savings_summary.excluded_over_count, 0)

        # 과화질 구간에서 절약 미달이면 제외분으로 집계됨
        vid_over_small = _vid("over_small.mp4", 4400, 10.0, width=1920, height=1080, codec="h264")
        rep_over = VideoEvaluator.evaluate_all("C:/test", [vid_over_small], self.cfg)
        self.assertEqual(rep_over.evaluations[0].quality_zone, QualityZone.OVER)
        self.assertEqual(rep_over.savings_summary.excluded_over_count, 1)

        # HEVC(0.80) -> AV1(0.65)는 1.23배 < 1.25이므로 적정 보존 유지
        cfg_av1 = ClassifierConfig(encode=EncodeConfig(encode_codec="av1_nvenc"))
        ev_hevc = VideoEvaluator.evaluate_video(_vid("hevc_opt.mp4", 2200, 600.0, width=1920, height=1080, codec="hevc"), cfg_av1)
        self.assertEqual(ev_hevc.smart_action, SmartAction.PASS_OPTIMAL)

    def test_scenario_b_and_j_same_or_better_codec_and_10bit_sdr_margins(self) -> None:
        """고성능 코덱(+0.15)과 비-HDR 10-bit(+0.10) 과화질 여유치 가산을 검증(시나리오 B, J)"""
        # HEVC -> HEVC: 1.20 + 0.15 = 1.35 기준
        hevc_130 = _vid("hevc130.mp4", 1560, 120.0, codec="hevc")  # 1560 / 1200 = 1.30
        self.assertEqual(VideoEvaluator.evaluate_video(hevc_130, self.cfg).quality_zone, QualityZone.OPTIMAL)
        hevc_140 = _vid("hevc140.mp4", 1680, 120.0, codec="hevc")  # 1680 / 1200 = 1.40
        self.assertEqual(VideoEvaluator.evaluate_video(hevc_140, self.cfg).quality_zone, QualityZone.OVER)

        # 비-HDR 10-bit H.264 -> HEVC: 1.20 + 0.10 = 1.30 기준
        h264_125_8bit = _vid("8bit.mp4", 1500, 120.0, codec="h264", is_10bit=False)
        h264_125_10bit = _vid("10bit.mp4", 1500, 120.0, codec="h264", is_10bit=True)
        self.assertEqual(VideoEvaluator.evaluate_video(h264_125_8bit, self.cfg).quality_zone, QualityZone.OVER)
        self.assertEqual(VideoEvaluator.evaluate_video(h264_125_10bit, self.cfg).quality_zone, QualityZone.OPTIMAL)

        # 10-bit HEVC SDR -> HEVC: 1.20 + 0.15 + 0.10 = 1.45 기준
        hevc_10bit_140 = _vid("hevc10_140.mp4", 1680, 120.0, codec="hevc", is_10bit=True)
        self.assertEqual(VideoEvaluator.evaluate_video(hevc_10bit_140, self.cfg).quality_zone, QualityZone.OPTIMAL)

        # HDR 10-bit는 10-bit SDR 마진을 적용하지 않음
        self.assertEqual(
            quality_metrics.resolve_quality_zone(1.0, 1.25, self.cfg.thresholds, source_weight=1.0, target_weight=0.8, is_10bit=True, is_hdr=True),
            QualityZone.OVER,
        )

    def test_scenario_d_e_g_h_routing_and_intra_capture(self) -> None:
        """블록 노이즈 방어(D), 인트라 코덱 우선 포착(E), 코덱 역전(G), 정적 화면(H) 라우팅을 검증"""
        # 시나리오 D: mpeg4(1.30), r_quality=0.85 < 0.90 -> 저화질 프리셋 및 KF 인코딩
        mpeg4_low_opt = _vid("mpeg4_d.mp4", 1658, 300.0, codec="mpeg4")
        ev_d = VideoEvaluator.evaluate_video(mpeg4_low_opt, self.cfg)
        self.assertEqual((ev_d.is_encode_target, ev_d.use_low_quality_preset), (True, True))
        self.assertEqual(ev_d.action_label, "품질 인코딩")
        # - 블록 노이즈 경로는 원본 비율이 0.80 이상이라 출력 비율이 1을 넘으므로 연속 규칙이 기준 품질값을 그대로 둠
        self.assertGreater(ev_d.target_bitrate_ratio, 1.0)
        self.assertAlmostEqual(ev_d.planned_cq or 0.0, _continuous_cq(ev_d, self.cfg))
        self.assertEqual(ev_d.applied_cq, self.cfg.encode.baseline_cq)

        # 시나리오 D 경계 밖(r_quality >= 0.90)은 CQ22 폴더
        mpeg4_clean = _vid("mpeg4_clean.mp4", 1800, 300.0, codec="mpeg4")  # 1800 / 1950 = 0.923
        ev_d_clean = VideoEvaluator.evaluate_video(mpeg4_clean, self.cfg)
        self.assertEqual((ev_d_clean.is_encode_target, ev_d_clean.use_low_quality_preset), (True, False))
        self.assertEqual(ev_d_clean.action_label, "품질 인코딩")

        # 시나리오 E: mjpeg(2.50) LOW 등급 + 정상 키프레임(0.033s)이어도 ENCODE_CODEC_UPGRADE 포착
        prores_low = _vid("prores_low.mov", 2400, 120.0, kf=0.033, codec="prores")  # r_q = 2400/3750 = 0.64 (LOW)
        ev_e_low = VideoEvaluator.evaluate_video(prores_low, self.cfg)
        self.assertEqual(ev_e_low.quality_zone, QualityZone.LOW)
        self.assertEqual(ev_e_low.smart_action, SmartAction.ENCODE_CODEC_UPGRADE)
        self.assertEqual(ev_e_low.smart_action.reason_label, "코덱 전환")
        self.assertEqual((ev_e_low.is_encode_target, ev_e_low.use_low_quality_preset), (True, True))
        self.assertEqual(ev_e_low.action_label, "품질 인코딩")
        # - 원본 비율은 0.64로 저화질이지만 출력 기준 비율은 2.0이라 연속 규칙이 기준 품질값을 그대로 둠
        self.assertGreater(ev_e_low.target_bitrate_ratio, 1.0)
        self.assertAlmostEqual(ev_e_low.planned_cq or 0.0, _continuous_cq(ev_e_low, self.cfg))
        self.assertEqual(ev_e_low.applied_cq, self.cfg.encode.baseline_cq)

        # 시나리오 E 고비트레이트(r_quality >= 0.90)는 CQ22 폴더로 ENCODE_CODEC_UPGRADE
        mjpeg_high = _vid("mjpeg_hi.avi", 4000, 120.0, kf=0.033, codec="mjpeg")
        ev_e_hi = VideoEvaluator.evaluate_video(mjpeg_high, self.cfg)
        self.assertEqual(ev_e_hi.smart_action, SmartAction.ENCODE_CODEC_UPGRADE)
        self.assertEqual((ev_e_hi.is_encode_target, ev_e_hi.use_low_quality_preset), (True, False))
        self.assertEqual(ev_e_hi.action_label, "품질 인코딩")

        # 시나리오 G: av1(0.65) -> libx264(1.00), r_saving < 1.0, KF 초과 시 용량 증가 반영 + 저화질 프리셋 라우팅
        cfg_x264 = ClassifierConfig(encode=EncodeConfig(encode_codec="libx264"))
        av1_downgrade = _vid("av1_down.mp4", 900, 120.0, kf=5.0, codec="av1")
        ev_g = VideoEvaluator.evaluate_video(av1_downgrade, cfg_x264)
        self.assertEqual(ev_g.smart_action, SmartAction.ENCODE_OPT_KF)
        self.assertEqual((ev_g.is_encode_target, ev_g.use_low_quality_preset), (True, True))
        self.assertEqual(ev_g.action_label, "품질 인코딩")
        self.assertAlmostEqual(ev_g.planned_cq or 0.0, _continuous_cq(ev_g, cfg_x264))
        self.assertEqual(ev_g.applied_cq, cfg_x264.encode.baseline_cq + PROTECT_CQ_DELTA_MAX)
        self.assertLess(ev_g.expected_after_mb, av1_downgrade.vid_size_MB)

        # 시나리오 H: r_quality < 0.35 정적 화면은 low_bitrate_ratio가 낮아도 저화질 프리셋으로 라우팅
        cfg_low_floor = ClassifierConfig(thresholds=ThresholdsConfig(low_bitrate_ratio=0.10))
        static_vid = _vid("lecture.mp4", 375, 120.0, kf=5.0, codec="h264")  # r_q = 0.25 < 0.35
        ev_h = VideoEvaluator.evaluate_video(static_vid, cfg_low_floor)
        self.assertEqual((ev_h.is_encode_target, ev_h.use_low_quality_preset), (True, True))
        self.assertEqual(ev_h.applied_cq, cfg_low_floor.encode.baseline_cq + PROTECT_CQ_DELTA_MAX)

    def test_scenario_l_decode_complexity_and_cq26_speed_factor(self) -> None:
        """원본 코덱 디코딩 부하와 CQ26 라우팅 가속 계수(0.85)가 예상 인코딩 시간에 반영됨을 검증(시나리오 L)"""
        cfg_1x = ClassifierConfig(encode=EncodeConfig(encode_speed_factor=1.0))
        h264_over = _vid("h264.mp4", 9000, 100.0, width=1920, height=1080, codec="h264")
        av1_over = _vid("av1.mp4", 9000, 100.0, width=1920, height=1080, codec="av1")
        prores_over = _vid("prores.mov", 15000, 100.0, width=1920, height=1080, codec="prores")
        h264_low = _vid("low.mp4", 1000, 100.0, width=1920, height=1080, kf=5.0, codec="h264")

        # 기준 시간(절약 선별용)에는 디코딩 부하를 넣지 않음
        self.assertAlmostEqual(quality_metrics.estimate_encode_duration_sec(av1_over, cfg_1x), 100.0)
        display = VideoEvaluator.estimate_display_encode_duration_sec
        self.assertAlmostEqual(display(h264_over, cfg_1x, is_low_quality_preset=False), 100.0)
        self.assertAlmostEqual(display(av1_over, cfg_1x, is_low_quality_preset=False), 115.0)
        self.assertAlmostEqual(display(prores_over, cfg_1x, is_low_quality_preset=False), 110.0)
        self.assertAlmostEqual(display(h264_low, cfg_1x, is_low_quality_preset=True), 85.0)

    def test_scenario_f_force_legacy_compat_routes_without_savings_filter(self) -> None:
        """구형 코덱 호환 인코딩 옵션이 켜지면 절약 선별과 무관하게 ENCODE_COMPAT으로 분류함을 검증(시나리오 F)"""
        # 기본값(False)에서는 짧은 wmv3 파일이 절약 미달로 보존됨
        short_wmv3 = _vid("legacy_short.wmv", 1800, 10.0, kf=1.0, codec="wmv3")
        ev_off = VideoEvaluator.evaluate_video(short_wmv3, self.cfg)
        self.assertEqual(ev_off.smart_action, SmartAction.PASS_LOW_SAVED)
        self.assertFalse(ev_off.is_encode_target)

        # 옵션 활성 시 절약 선별 없이 ENCODE_COMPAT("코덱 호환")으로 분류
        cfg_compat = ClassifierConfig(encode=EncodeConfig(force_legacy_compat=True))
        ev_on_std = VideoEvaluator.evaluate_video(short_wmv3, cfg_compat)
        self.assertEqual(ev_on_std.smart_action, SmartAction.ENCODE_COMPAT)
        self.assertEqual(ev_on_std.smart_action.reason_label, "코덱 호환")
        self.assertEqual(ev_on_std.action_label, "코덱 호환 인코딩")
        self.assertEqual((ev_on_std.is_encode_target, ev_on_std.use_low_quality_preset), (True, False))

        # 저화질/시나리오 D 구형 코덱은 저화질 프리셋 및 KF 인코딩으로 라우팅
        low_vp8 = _vid("legacy_low.webm", 1200, 10.0, kf=1.0, codec="vp8")
        ev_on_low = VideoEvaluator.evaluate_video(low_vp8, cfg_compat)
        self.assertEqual(ev_on_low.smart_action, SmartAction.ENCODE_COMPAT)
        self.assertEqual(ev_on_low.smart_action.reason_label, "코덱 호환")
        self.assertEqual(ev_on_low.action_label, "코덱 호환 인코딩")
        self.assertAlmostEqual(ev_on_low.planned_cq or 0.0, _continuous_cq(ev_on_low, cfg_compat))
        self.assertEqual((ev_on_low.is_encode_target, ev_on_low.use_low_quality_preset), (True, True))

        # HDR 보존과 예외 규칙은 구형 코덱 호환 옵션보다 우선
        hdr_wmv3 = _vid("hdr.wmv", 1800, 10.0, codec="wmv3", is_hdr=True)
        self.assertEqual(VideoEvaluator.evaluate_video(hdr_wmv3, cfg_compat).smart_action, SmartAction.PASS_HDR)
        self.assertEqual(
            VideoEvaluator.evaluate_video(short_wmv3, cfg_compat, exception_rules=[lambda _v: True]).smart_action,
            SmartAction.PASS_EXCEPTION,
        )
        # 최신 코덱(H.264/HEVC)은 옵션 영향을 받지 않음
        h264_opt = _vid("modern.mp4", 2600, 30.0, width=1920, height=1080, codec="h264")
        self.assertEqual(VideoEvaluator.evaluate_video(h264_opt, cfg_compat).smart_action, SmartAction.PASS_CODEC_SAVED)


def _downscale_cfg(max_tier: str = "720p", **thresholds: float) -> ClassifierConfig:
    """최대 해상도만 지정한 설정 생성, 판정 임계값은 키워드로 덮어쓰기"""
    return ClassifierConfig(
        encode=EncodeConfig(max_resolution_tier=max_tier),
        thresholds=ThresholdsConfig(**thresholds),
    )


class TestMaxResolutionDownscale(unittest.TestCase):
    """최대 해상도 초과 축소 인코딩의 판정 순서, 출력 지표, 5절 화질 보호 되돌림, 예상 용량과 시간을 검증"""

    def setUp(self) -> None:
        _use_legacy_size_model(self)
        self.cfg = ClassifierConfig()

    def test_source_above_limit_routes_to_downscale_folder(self) -> None:
        """1080p 원본이 720p 상한을 넘으면 축소 인코딩 폴더로 분류되고 출력 해상도 정보가 채워짐"""
        cfg = _downscale_cfg("720p")
        vid = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080)
        ev = VideoEvaluator.evaluate_video(vid, cfg)
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertEqual((ev.smart_action, getattr(ev.target_tier, "key", None)), (SmartAction.ENCODE_DOWNSCALE, "720p"))
        self.assertEqual((ev.target_width, ev.target_height), (1280, 720))
        self.assertIsNotNone(ev.target_tier)
        assert ev.target_tier is not None
        self.assertEqual(ev.target_tier.key, "720p")
        self.assertEqual(ev.downscale_reason, DownscaleReason.MAX_RESOLUTION)
        self.assertEqual(ev.reason_label, "최대 해상도 초과")
        self.assertEqual(ev.action_label, "축소 재인코딩")
        self.assertEqual(ev.planned_cq, 22.0)
        self.assertFalse(ev.use_low_quality_preset)
        self.assertIsNone(ev.downscale_skip_reason)

        report = VideoEvaluator.evaluate_all("C:/dummy", [vid], cfg)
        self.assertEqual(report.encoded_count, 1)

    def test_output_metrics_use_output_resolution_and_target_codec(self) -> None:
        """출력 해상도 기준 목표 최적값, 출력 BR 비율, 인코딩 시간을 산출하고 디코딩 부하는 반영하지 않음"""
        cfg = _downscale_cfg("720p")
        vid = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080, codec="av1")
        ev = VideoEvaluator.evaluate_video(vid, cfg)
        expected_opt = quality_metrics.calc_optimal_bitrate(
            1280, 720, cfg.bitrate_model, cfg.codec_weights, 30.0, "hevc"
        )
        self.assertEqual(ev.target_optimal_kbps, expected_opt)
        self.assertAlmostEqual(ev.target_bitrate_ratio, 9000 / expected_opt)
        self.assertAlmostEqual(ev.estimated_encode_sec, 100.0 * (1280 * 720) / (1920 * 1080))
        self.assertEqual(ev.optimal_kbps, quality_metrics.calc_optimal_bitrate(
            1920, 1080, cfg.bitrate_model, cfg.codec_weights, 30.0, "av1"
        ))

    def test_downscale_after_size_is_capped_by_max_bitrate(self) -> None:
        """예상 용량 계산은 영상 비트레이트를 최대 비트레이트 이하로 제한함"""
        vid = _vid("cap.mp4", 9000, 100.0, width=1920, height=1080)
        capped = estimate_after_size_mb(
            vid, 0.5, _RATE, cap_at_original=False, max_video_kbps=2800
        )
        stream_at_cap = estimate_video_stream_mb(_vid("cap_ref.mp4", 2800, 100.0))
        self.assertAlmostEqual(
            capped, (vid.vid_size_MB - estimate_video_stream_mb(vid)) + stream_at_cap, places=6
        )
        uncapped = estimate_after_size_mb(vid, 0.5, _RATE, cap_at_original=False)
        self.assertGreater(uncapped, capped)

    def test_near_same_size_candidate_is_skipped_with_reason(self) -> None:
        """후보 규격보다 살짝 큰 원본은 의미 있는 축소가 아니므로 CQ22 기존 판정을 따르고 사유만 남김"""
        cfg = _downscale_cfg("1080p")
        vid = _vid("almost.mp4", 9000, 100.0, width=1920, height=1088)
        ev = VideoEvaluator.evaluate_video(vid, cfg)
        self.assertEqual(ev.downscale_skip_reason, DownscaleSkipReason.NEAR_SAME_SIZE)
        self.assertIsNone(ev.target_width)
        self.assertEqual((ev.is_encode_target, ev.use_low_quality_preset), (True, False))
        self.assertNotEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)

        # 864x480은 480p 박스를 넘지만 허용 오차 안에서 축소 효과가 없어 같은 사유, 854x480은 박스 안이라 사유 없음
        cfg_480 = _downscale_cfg("480p")
        slight = VideoEvaluator.evaluate_video(_vid("864.mp4", 1800, 60.0, width=864, height=480), cfg_480)
        self.assertEqual(slight.downscale_skip_reason, DownscaleSkipReason.NEAR_SAME_SIZE)
        inside = VideoEvaluator.evaluate_video(_vid("854.mp4", 1800, 60.0, width=854, height=480), cfg_480)
        self.assertIsNone(inside.downscale_skip_reason)

    def test_source_beyond_4k_box_is_above_every_limit(self) -> None:
        """4K 박스를 의미 있게 넘는 원본(7680x4320)은 모든 상한보다 높으므로 상한 규격으로 축소"""
        vid = _vid("8k.mp4", 40000, 100.0, width=7680, height=4320)
        ev = VideoEvaluator.evaluate_video(vid, _downscale_cfg("720p"))
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertEqual((ev.target_width, ev.target_height), (1280, 720))

    def test_low_quality_after_downscale_reverts_to_source_judgement_for_each_condition(self) -> None:
        """축소 후 CQ26 대상이면 축소하지 않고 원본 해상도 판정으로 되돌림(네 조건 각각)"""
        cfg = _downscale_cfg("720p")
        bm, cw = cfg.bitrate_model, cfg.codec_weights

        # 조건 1: 출력 해상도 기준 저화질(r_tier < 0.80)
        low_vid = _vid("low.mp4", 750, 300.0, width=1920, height=1080)
        ev_low = VideoEvaluator.evaluate_video(low_vid, cfg)
        self.assertEqual(ev_low.downscale_skip_reason, DownscaleSkipReason.LOW_QUALITY_AFTER_DOWNSCALE)
        self.assertEqual(ev_low.smart_action, SmartAction.PASS_LOW)
        self.assertIsNone(ev_low.target_width)

        # 조건 2: 구형 코덱 블록 노이즈 증폭(mpeg4 가중치 1.30, r_tier < 0.90)
        block_vid = _vid("block.mp4", 1658, 300.0, width=1920, height=1080, codec="mpeg4")
        ev_block = VideoEvaluator.evaluate_video(block_vid, cfg)
        self.assertEqual(ev_block.downscale_skip_reason, DownscaleSkipReason.LOW_QUALITY_AFTER_DOWNSCALE)
        self.assertNotEqual(getattr(ev_block.target_tier, "key", None), "720p")

        # 조건 3: 코덱 역전(av1 -> hevc, r_out < 1.0, r_tier 1.0)
        av1_opt_720 = quality_metrics.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "av1")
        reversal_vid = _vid("reverse.mp4", av1_opt_720, 300.0, width=1920, height=1080, codec="av1")
        ev_reversal = VideoEvaluator.evaluate_video(reversal_vid, cfg)
        self.assertEqual(ev_reversal.downscale_skip_reason, DownscaleSkipReason.LOW_QUALITY_AFTER_DOWNSCALE)
        self.assertNotEqual(getattr(ev_reversal.target_tier, "key", None), "720p")

        # 조건 4: 정적 화면(r_tier < 0.35). 저화질 하한을 낮춰 저화질 조건과 분리
        cfg_static = _downscale_cfg("720p", low_bitrate_ratio=0.10)
        static_vid = _vid("static.mp4", 375, 300.0, width=1920, height=1080)
        ev_static = VideoEvaluator.evaluate_video(static_vid, cfg_static)
        self.assertEqual(ev_static.downscale_skip_reason, DownscaleSkipReason.LOW_QUALITY_AFTER_DOWNSCALE)
        self.assertNotEqual(getattr(ev_static.target_tier, "key", None), "720p")

    def test_savings_filter_uses_downscaled_saving_and_keeps_reference_size(self) -> None:
        """축소 기준 절약이 미달이면 원본 해상도 재판정 없이 보존하고, 참고 예상 용량은 축소 기준을 씀"""
        cfg = _downscale_cfg("720p")
        short_vid = _vid("short.mp4", 1300, 10.0, width=1920, height=1080)
        ev = VideoEvaluator.evaluate_video(short_vid, cfg)
        self.assertEqual(ev.smart_action, SmartAction.PASS_LOW_SAVED)
        self.assertFalse(ev.is_encode_target)
        self.assertEqual(ev.downscale_reason, DownscaleReason.MAX_RESOLUTION)
        self.assertLess(ev.hypothetical_after_mb or 0.0, short_vid.vid_size_MB)
        self.assertEqual(ev.preview_after_mb, ev.hypothetical_after_mb)
        self.assertEqual(ev.expected_after_mb, short_vid.vid_size_MB)

    def test_low_quality_source_becomes_cq22_downscale_after_resolution_drop(self) -> None:
        """원본 해상도에서는 저화질이지만 720p로 줄이면 CQ22 대상이 되어 축소 인코딩됨"""
        vid = _vid("low_src.mp4", 1650, 120.0, width=1920, height=1080)
        ev_orig = VideoEvaluator.evaluate_video(vid, self.cfg)
        self.assertEqual(ev_orig.quality_zone, QualityZone.LOW)
        self.assertEqual(ev_orig.smart_action, SmartAction.PASS_LOW)

        ev_down = VideoEvaluator.evaluate_video(vid, _downscale_cfg("720p"))
        self.assertEqual(ev_down.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertEqual((ev_down.smart_action, getattr(ev_down.target_tier, "key", None)), (SmartAction.ENCODE_DOWNSCALE, "720p"))
        self.assertAlmostEqual(ev_down.target_bitrate_ratio, 1.375, places=3)

    def test_over_quality_below_downscale_savings_is_counted_as_excluded(self) -> None:
        """축소 기준 절약 미달 과화질 영상은 원본 보존되고 제외분 집계에 포함됨"""
        cfg = _downscale_cfg("720p")
        vid = _vid("short_over.mp4", 2800, 5.0, width=1920, height=1080)
        ev = VideoEvaluator.evaluate_video(vid, cfg)
        self.assertEqual(ev.quality_zone, QualityZone.OVER)
        self.assertEqual(ev.smart_action, SmartAction.PASS_LOW_SAVED)
        self.assertFalse(ev.is_encode_target)
        self.assertEqual((ev.target_width, ev.target_height), (1280, 720))

        report = VideoEvaluator.evaluate_all("C:/dummy", [vid], cfg)
        self.assertEqual(report.encoded_count, 0)
        self.assertEqual(report.savings_summary.low_saved_count, 1)
        self.assertEqual(report.savings_summary.excluded_over_count, 1)

    def test_keyframe_exceeded_downscale_skips_savings_filter(self) -> None:
        """키프레임 초과 영상은 축소 기준 절약과 무관하게 축소 인코딩으로 분류"""
        cfg = _downscale_cfg("720p")
        short_kf = _vid("short_kf.mp4", 1300, 10.0, kf=5.0, width=1920, height=1080)
        ev = VideoEvaluator.evaluate_video(short_kf, cfg)
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertEqual((ev.smart_action, getattr(ev.target_tier, "key", None)), (SmartAction.ENCODE_DOWNSCALE, "720p"))

    def test_legacy_compat_downscale_skips_savings_filter(self) -> None:
        """구형 코덱 호환 옵션이 켜진 미지원 코덱은 축소 기준 절약과 무관하게 축소 인코딩으로 분류"""
        short_wmv3 = _vid("legacy.wmv", 1800, 10.0, width=1920, height=1080, codec="wmv3")
        ev_off = VideoEvaluator.evaluate_video(short_wmv3, _downscale_cfg("720p"))
        self.assertEqual(ev_off.smart_action, SmartAction.PASS_LOW_SAVED)

        cfg_compat = ClassifierConfig(
            encode=EncodeConfig(max_resolution_tier="720p", force_legacy_compat=True)
        )
        ev_on = VideoEvaluator.evaluate_video(short_wmv3, cfg_compat)
        self.assertEqual(ev_on.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertEqual((ev_on.smart_action, getattr(ev_on.target_tier, "key", None)), (SmartAction.ENCODE_DOWNSCALE, "720p"))

    def test_hdr_and_exception_do_not_review_downscale(self) -> None:
        """HDR과 예외 규칙 영상은 축소를 검토하지 않아 출력 해상도 정보가 비어 있음"""
        cfg = _downscale_cfg("720p")
        hdr_vid = _vid("hdr.mp4", 9000, 100.0, width=1920, height=1080, is_hdr=True)
        ev_hdr = VideoEvaluator.evaluate_video(hdr_vid, cfg)
        self.assertEqual(ev_hdr.smart_action, SmartAction.PASS_HDR)
        self.assertIsNone(ev_hdr.downscale_reason)
        self.assertIsNone(ev_hdr.target_width)

        vid = _vid("excluded.mp4", 9000, 100.0, width=1920, height=1080)
        ev_exc = VideoEvaluator.evaluate_video(vid, cfg, exception_rules=[lambda v: True])
        self.assertEqual(ev_exc.smart_action, SmartAction.PASS_EXCEPTION)
        self.assertIsNone(ev_exc.downscale_reason)
        self.assertIsNone(ev_exc.target_tier)

    def test_downscale_estimate_uses_general_maxrate_of_corrected_target(self) -> None:
        """축소 예상 용량은 실제 명령과 같은 규칙(보정 목표 x 여유, 규격 상한)의 최대 비트레이트로 제한됨을 검증"""
        cfg = _downscale_cfg("720p")
        vid = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080)
        ev = VideoEvaluator.evaluate_video(vid, cfg)
        assert ev.target_tier is not None
        maxrate = general_maxrate_kbps(
            corrected_target_kbps(ev.target_optimal_kbps, cfg.encode.encode_codec),
            ev.target_tier.max_bitrate_kbps,
        )
        expected = estimate_after_size_mb(
            vid,
            ev.target_bitrate_ratio,
            cfg.thresholds.keyframe_1s_increase_rate,
            cap_at_original=True,
            source_ratio=vid.vid_kbps / ev.optimal_kbps,
            codec_weight_ratio=1.0,
            base_fps=cfg.bitrate_model.base_fps,
            max_video_kbps=maxrate,
            output_delta_cq=0.0,
            encoder=cfg.encode.encode_codec,
        )
        self.assertAlmostEqual(ev.expected_after_mb, expected)

    def test_maxrate_headroom_warns_when_high_fps_eats_the_margin(self) -> None:
        """출력 기준 최적값 대비 최대 비트레이트 여유가 1.5배 미만이면 최대 BR 근접으로 판정"""
        cfg = _downscale_cfg("720p")
        normal = VideoEvaluator.evaluate_video(
            _vid("n30.mp4", 9000, 100.0, width=1920, height=1080, fps=30.0), cfg
        )
        self.assertFalse(normal.is_maxrate_near)
        self.assertAlmostEqual(normal.maxrate_headroom or 0.0, 2800 / normal.target_optimal_kbps)

        fast = VideoEvaluator.evaluate_video(
            _vid("n120.mp4", 9000, 100.0, width=1920, height=1080, fps=120.0), cfg
        )
        self.assertTrue(fast.is_maxrate_near)
        self.assertLess(fast.maxrate_headroom or 0.0, 1.5)

    def test_no_limit_keeps_downscale_fields_empty(self) -> None:
        """최대 해상도가 제한 없음이면 축소 관련 필드가 모두 비어 있고 기존 원본 해상도 판정을 따름"""
        vid = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080)
        ev = VideoEvaluator.evaluate_video(vid, self.cfg)
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_OVER)
        self.assertIsNone(ev.target_tier)
        self.assertIsNone(ev.downscale_reason)
        self.assertIsNone(ev.downscale_skip_reason)
        self.assertFalse(ev.is_maxrate_near)


class TestManualOutputTier(unittest.TestCase):
    """영상별 출력 해상도 직접 지정의 우선순위, 축소 안 함, 절약 선별, 화질 보호 되돌림, 예외와 HDR 우선을 검증"""

    def test_manual_tier_overrides_max_resolution_in_both_directions(self) -> None:
        """직접 지정 규격은 최대 해상도보다 낮아도 높아도 그 값만 쓰고 사유를 직접 지정으로 표시"""
        vid = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080)
        lower = VideoEvaluator.evaluate_all(
            "C:/dummy", [vid], _downscale_cfg("720p"), manual_targets={"hd.mp4": "360p"}
        ).evaluations[0]
        self.assertEqual(lower.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertEqual((lower.smart_action, getattr(lower.target_tier, "key", None)), (SmartAction.ENCODE_DOWNSCALE, "360p"))
        self.assertEqual((lower.target_width, lower.target_height), (640, 360))
        self.assertEqual(lower.downscale_reason, DownscaleReason.MANUAL)

        higher = VideoEvaluator.evaluate_all(
            "C:/dummy", [vid], _downscale_cfg("360p"), manual_targets={"hd.mp4": "720p"}
        ).evaluations[0]
        self.assertEqual((higher.smart_action, getattr(higher.target_tier, "key", None)), (SmartAction.ENCODE_DOWNSCALE, "720p"))
        self.assertEqual(higher.downscale_reason, DownscaleReason.MANUAL)

    def test_keep_ignores_max_resolution(self) -> None:
        """축소 안 함은 최대 해상도 상한을 무시하고 축소 후보와 사유를 만들지 않음"""
        vid = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080)
        ev = VideoEvaluator.evaluate_all(
            "C:/dummy", [vid], _downscale_cfg("720p"), manual_targets={"hd.mp4": "keep"}
        ).evaluations[0]
        self.assertNotEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertIsNone(ev.target_width)
        self.assertIsNone(ev.downscale_reason)
        self.assertIsNone(ev.downscale_skip_reason)

    def test_manual_tier_ignores_savings_filter(self) -> None:
        """직접 지정이면 절약이 최소 기준에 못 미쳐도 축소 인코딩하고 축소 기준 참고값을 남김"""
        vid = _vid("short.mp4", 1800, 10.0, width=1920, height=1080)
        ev = VideoEvaluator.evaluate_all(
            "C:/dummy", [vid], _downscale_cfg("720p"), manual_targets={"short.mp4": "720p"}
        ).evaluations[0]
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertTrue(ev.is_encode_target)
        self.assertEqual(ev.downscale_reason, DownscaleReason.MANUAL)

    def test_manual_tier_reverts_when_low_quality_after_downscale(self) -> None:
        """직접 지정 규격으로 줄여도 CQ26 대상이면 축소하지 않고 원본 해상도 판정으로 되돌림"""
        vid = _vid("low.mp4", 750, 300.0, width=1920, height=1080)
        ev = VideoEvaluator.evaluate_all(
            "C:/dummy", [vid], _downscale_cfg("1080p"), manual_targets={"low.mp4": "720p"}
        ).evaluations[0]
        self.assertEqual(ev.downscale_skip_reason, DownscaleSkipReason.LOW_QUALITY_AFTER_DOWNSCALE)
        self.assertEqual(ev.smart_action, SmartAction.PASS_LOW)
        self.assertIsNone(ev.target_width)

    def test_manual_tier_near_same_size_is_skipped_with_reason(self) -> None:
        """직접 지정 규격과 거의 같은 크기의 원본은 축소하지 않고 거의 같은 크기 사유를 남김"""
        vid = _vid("almost.mp4", 9000, 100.0, width=1920, height=1088)
        ev = VideoEvaluator.evaluate_all(
            "C:/dummy", [vid], _downscale_cfg("720p"), manual_targets={"almost.mp4": "1080p"}
        ).evaluations[0]
        self.assertEqual(ev.downscale_skip_reason, DownscaleSkipReason.NEAR_SAME_SIZE)
        self.assertNotEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)

    def test_exception_and_hdr_take_precedence_over_manual_tier(self) -> None:
        """예외 규칙과 HDR 영상은 직접 지정이 있어도 보존되고 출력 해상도 정보를 남기지 않음"""
        cfg = _downscale_cfg("720p")
        hdr = _vid("hdr.mp4", 9000, 100.0, width=1920, height=1080, is_hdr=True)
        ev_hdr = VideoEvaluator.evaluate_all(
            "C:/dummy", [hdr], cfg, manual_targets={"hdr.mp4": "360p"}
        ).evaluations[0]
        self.assertEqual(ev_hdr.smart_action, SmartAction.PASS_HDR)
        self.assertIsNone(ev_hdr.downscale_reason)

        excluded = _vid("excluded.mp4", 9000, 100.0, width=1920, height=1080)
        ev_exc = VideoEvaluator.evaluate_all(
            "C:/dummy",
            [excluded],
            cfg,
            exception_rules=[lambda _v: True],
            manual_targets={"excluded.mp4": "360p"},
        ).evaluations[0]
        self.assertEqual(ev_exc.smart_action, SmartAction.PASS_EXCEPTION)
        self.assertIsNone(ev_exc.downscale_reason)

    def test_invalid_manual_value_falls_back_to_automatic(self) -> None:
        """규격 키와 keep 외 값은 무시하고 최대 해상도 기준 자동 판정을 사용"""
        vid = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080)
        cfg = _downscale_cfg("720p")
        auto = VideoEvaluator.evaluate_all("C:/dummy", [vid], cfg).evaluations[0]
        broken = VideoEvaluator.evaluate_all(
            "C:/dummy", [vid], cfg, manual_targets={"hd.mp4": "8K"}
        ).evaluations[0]
        self.assertEqual(broken.smart_action, auto.smart_action)
        self.assertEqual(broken.downscale_reason, DownscaleReason.MAX_RESOLUTION)

    def test_manual_map_applies_only_to_matching_filenames(self) -> None:
        """직접 지정 맵은 파일명이 같은 영상에만 적용되고 나머지는 자동 판정을 유지"""
        cfg = _downscale_cfg("720p")
        hd = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080)
        other = _vid("other.mp4", 9000, 100.0, width=1920, height=1080)
        report = VideoEvaluator.evaluate_all(
            "C:/dummy", [hd, other], cfg, manual_targets={"hd.mp4": "keep"}
        )
        by_name = {ev.video.filename: ev for ev in report.evaluations}
        self.assertNotEqual(by_name["hd.mp4"].smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertEqual(by_name["other.mp4"].smart_action, SmartAction.ENCODE_DOWNSCALE)


_MANUAL_FRAME = ActiveFrame(1920, 800, 0, 140)


class TestManualActiveFrameJudgment(unittest.TestCase):
    """사용자 지정 활성 화면이 화질 등급과 절약 선별보다 먼저 적용되는지 조합으로 검증"""

    def setUp(self) -> None:
        _use_legacy_size_model(self)

    def _evaluate(
        self,
        vid: VideoProps,
        cfg: ClassifierConfig | None = None,
        *,
        manual_target: str | None = None,
        with_manual_frame: bool = True,
    ) -> VideoEvaluation:
        return VideoEvaluator.evaluate_all(
            "C:/dummy",
            [vid],
            cfg or ClassifierConfig(),
            manual_targets=(
                {vid.filename: manual_target} if manual_target is not None else None
            ),
            manual_active_frames=(
                {vid.filename: _MANUAL_FRAME} if with_manual_frame else None
            ),
        ).evaluations[0]

    def test_manual_active_frame_encodes_optimal_low_and_small_savings_videos(self) -> None:
        """적정, 저화질, 절약 미달 구간 영상 모두 직접 지정으로 품질 인코딩하고 사유를 직접 지정으로 표시"""
        cases = {
            "optimal": _vid("ok.mp4", 2600, 120.0, width=1920, height=1080),
            "low": _vid("low.mp4", 800, 120.0, width=1920, height=1080),
            "small_savings": _vid("short.mp4", 9000, 10.0, width=1920, height=1080),
        }
        for label, vid in cases.items():
            with self.subTest(case=label):
                ev = self._evaluate(vid)
                self.assertEqual(ev.smart_action, SmartAction.ENCODE_MANUAL)
                self.assertTrue(ev.is_encode_target)
                self.assertEqual(ev.reason_label, "직접 지정")
                self.assertEqual(ev.action_label, "품질 인코딩")
                self.assertEqual(ev.active_frame, _MANUAL_FRAME)

    def test_manual_active_frame_keeps_low_quality_preset_routing(self) -> None:
        """저화질 구간 직접 지정은 기존 저화질 보호 프리셋 경로를 그대로 씀"""
        ev = self._evaluate(_vid("low.mp4", 800, 120.0, width=1920, height=1080))
        self.assertEqual(ev.quality_zone, QualityZone.LOW)
        self.assertTrue(ev.use_low_quality_preset)
        self.assertEqual(ev.action_badge_tone, ActionBadgeTone.LOW_KF_ENCODE)

    def test_manual_active_frame_reencodes_already_encoded_video(self) -> None:
        """이미 인코딩 영상도 사용자 지정 활성 화면이 적용되면 다시 판정"""
        tagged = _vid("tag.mp4", 2600, 120.0, width=1920, height=1080, has_encode_tag=True)
        self.assertEqual(
            self._evaluate(tagged, with_manual_frame=False).smart_action,
            SmartAction.PASS_ALREADY_ENCODED,
        )
        ev = self._evaluate(tagged)
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_MANUAL)
        self.assertTrue(ev.is_encode_target)

    def test_manual_output_tier_with_active_frame_uses_downscale(self) -> None:
        """출력 해상도 직접 지정과 활성 화면 지정이 함께 있으면 축소 인코딩하고 사유는 직접 지정"""
        vid = _vid("ok.mp4", 2600, 120.0, width=1920, height=1080)
        ev = self._evaluate(vid, manual_target="720p")
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertEqual(ev.downscale_reason, DownscaleReason.MANUAL)
        # - 축소 목표는 활성 화면(1920x800) 비율을 유지해 정해짐
        self.assertEqual((ev.target_width, ev.target_height), (1280, 532))
        self.assertEqual(ev.active_frame, _MANUAL_FRAME)

    def test_manual_output_tier_without_active_frame_uses_downscale(self) -> None:
        """활성 화면 지정이 없어도 출력 해상도 직접 지정은 절약 선별 없이 축소 인코딩"""
        vid = _vid("ok.mp4", 2600, 120.0, width=1920, height=1080)
        ev = self._evaluate(vid, manual_target="720p", with_manual_frame=False)
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertEqual(ev.downscale_reason, DownscaleReason.MANUAL)
        self.assertIsNone(ev.active_frame)

    def test_keep_resolution_with_active_frame_uses_manual_encode(self) -> None:
        """축소 안 함은 축소 후보를 만들지 않으므로 활성 화면 지정은 품질 인코딩으로 처리"""
        ev = self._evaluate(
            _vid("ok.mp4", 2600, 120.0, width=1920, height=1080), manual_target="keep"
        )
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_MANUAL)
        self.assertIsNone(ev.downscale_reason)
        self.assertIsNone(ev.target_width)

    def test_exception_rule_and_hdr_keep_precedence(self) -> None:
        """예외 규칙과 HDR 보존은 활성 화면 지정보다 우선하고 출력 해상도 정보를 남기지 않음"""
        vid = _vid("ok.mp4", 2600, 120.0, width=1920, height=1080)
        ev_exc = VideoEvaluator.evaluate_all(
            "C:/dummy",
            [vid],
            ClassifierConfig(),
            exception_rules=[lambda _v: True],
            manual_active_frames={vid.filename: _MANUAL_FRAME},
        ).evaluations[0]
        self.assertEqual(ev_exc.smart_action, SmartAction.PASS_EXCEPTION)
        self.assertIsNone(ev_exc.downscale_reason)

        hdr = _vid("hdr.mp4", 2600, 120.0, width=1920, height=1080, is_hdr=True)
        self.assertEqual(self._evaluate(hdr).smart_action, SmartAction.PASS_HDR)

    def test_max_resolution_candidate_wins_over_manual_active_frame(self) -> None:
        """최대 해상도 초과 자동 축소 후보가 있으면 활성 화면 지정과 함께라도 절약 선별 없이 축소 인코딩"""
        cfg = ClassifierConfig(encode=EncodeConfig(max_resolution_tier="720p"))
        ev = self._evaluate(_vid("ok.mp4", 2600, 120.0, width=1920, height=1080), cfg)
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertEqual(ev.downscale_reason, DownscaleReason.MAX_RESOLUTION)
        self.assertEqual((ev.target_width, ev.target_height), (1280, 532))

    def test_max_resolution_candidate_keeps_savings_filter(self) -> None:
        """최대 해상도 초과 자동 축소는 사용자 지정 활성 화면이 없으면 기존 절약 선별을 유지"""
        cfg = ClassifierConfig(encode=EncodeConfig(max_resolution_tier="720p"))
        ev = self._evaluate(
            _vid("tiny.mp4", 2400, 10.0, width=1920, height=1080),
            cfg,
            with_manual_frame=False,
        )
        self.assertEqual(ev.smart_action, SmartAction.PASS_LOW_SAVED)
        self.assertFalse(ev.is_encode_target)

    def test_manual_active_frame_applies_when_crop_is_disabled(self) -> None:
        """전역 잘라내기 설정이 꺼져 있어도 활성 화면 지정은 직접 지정 판정으로 적용됨"""
        cfg = ClassifierConfig(encode=EncodeConfig(crop_black_bars=False))
        vid = _vid("ok.mp4", 2600, 120.0, width=1920, height=1080)
        ev = self._evaluate(vid, cfg)
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_MANUAL)
        self.assertTrue(ev.is_encode_target)
        self.assertEqual(ev.active_frame, _MANUAL_FRAME)
        self.assertIsNone(ev.downscale_reason)
        # - 전역 설정 끔은 자동 감지 검은 여백만 잘라내지 않음
        self.assertIsNone(
            VideoEvaluator.evaluate_all("C:/dummy", [vid], cfg).evaluations[0].active_frame
        )


def _frame_target_cfg(*, max_tier: str = "none", auto_apply: bool = True) -> ClassifierConfig:
    """화질 분석 축소 후보 자동 반영 설정 생성"""
    return ClassifierConfig(
        encode=EncodeConfig(max_resolution_tier=max_tier, auto_apply_frame_target=auto_apply)
    )


class TestFrameAnalysisTarget(unittest.TestCase):
    """화질 분석 축소 후보 자동 반영의 우선순위, 겹침 크기 선택, 자동 적용 끔을 검증"""

    def setUp(self) -> None:
        _use_legacy_size_model(self)

    def test_frame_target_alone_promotes_optimal_video(self) -> None:
        """적정 화질 영상도 축소 후보가 있으면 화질 분석 축소 사유로 인코딩 대상이 됨"""
        vid = _vid("ok.mp4", 2500, 600.0, width=1920, height=1080, codec="hevc")
        ev = VideoEvaluator.evaluate_all(
            "C:/dummy", [vid], _frame_target_cfg(), frame_targets={"ok.mp4": "720p"}
        ).evaluations[0]
        self.assertEqual(ev.quality_zone, QualityZone.OPTIMAL)
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertEqual(ev.downscale_reason, DownscaleReason.FRAME_ANALYSIS)
        self.assertEqual(ev.reason_label, "화질 분석 축소")
        self.assertEqual((ev.target_width, ev.target_height), (1280, 720))

    def test_frame_target_ignored_when_auto_apply_is_off(self) -> None:
        """자동 적용을 끄면 축소 후보가 있어도 후보를 반영하지 않음"""
        vid = _vid("ok.mp4", 2500, 600.0, width=1920, height=1080, codec="hevc")
        ev = VideoEvaluator.evaluate_all(
            "C:/dummy",
            [vid],
            _frame_target_cfg(auto_apply=False),
            frame_targets={"ok.mp4": "720p"},
        ).evaluations[0]
        self.assertEqual(ev.smart_action, SmartAction.PASS_OPTIMAL)
        self.assertIsNone(ev.downscale_reason)

    def test_manual_target_wins_over_frame_target(self) -> None:
        """직접 지정(축소 안 함 포함)이 있으면 화질 분석 축소 후보를 쓰지 않음"""
        vid = _vid("ok.mp4", 2500, 600.0, width=1920, height=1080, codec="hevc")
        cfg = _frame_target_cfg()
        manual = VideoEvaluator.evaluate_all(
            "C:/dummy", [vid], cfg, manual_targets={"ok.mp4": "360p"}, frame_targets={"ok.mp4": "720p"}
        ).evaluations[0]
        self.assertEqual(manual.downscale_reason, DownscaleReason.MANUAL)
        self.assertEqual((manual.target_width, manual.target_height), (640, 360))
        keep = VideoEvaluator.evaluate_all(
            "C:/dummy", [vid], cfg, manual_targets={"ok.mp4": "keep"}, frame_targets={"ok.mp4": "720p"}
        ).evaluations[0]
        self.assertIsNone(keep.downscale_reason)
        self.assertEqual(keep.smart_action, SmartAction.PASS_OPTIMAL)

    def test_smallest_output_size_wins_between_candidates(self) -> None:
        """최대 해상도와 화질 분석 후보가 겹치면 출력 화소 수가 더 작은 쪽을 채택"""
        vid = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080)
        ev = VideoEvaluator.evaluate_all(
            "C:/dummy",
            [vid],
            _frame_target_cfg(max_tier="1080p"),
            frame_targets={"hd.mp4": "720p"},
        ).evaluations[0]
        self.assertEqual(ev.downscale_reason, DownscaleReason.FRAME_ANALYSIS)
        self.assertEqual((ev.target_width, ev.target_height), (1280, 720))

    def test_same_size_prefers_max_resolution_reason(self) -> None:
        """같은 출력 크기면 최대 해상도 초과를 사유로 씀"""
        vid = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080)
        ev = VideoEvaluator.evaluate_all(
            "C:/dummy",
            [vid],
            _frame_target_cfg(max_tier="720p"),
            frame_targets={"hd.mp4": "720p"},
        ).evaluations[0]
        self.assertEqual(ev.downscale_reason, DownscaleReason.MAX_RESOLUTION)

    def test_frame_target_map_applies_only_to_matching_filename(self) -> None:
        """화질 분석 후보 맵은 파일명이 같은 영상에만 적용됨"""
        cfg = _frame_target_cfg()
        first = _vid("first.mp4", 2900, 600.0, width=1920, height=1080)
        second = _vid("second.mp4", 2900, 600.0, width=1920, height=1080)
        report = VideoEvaluator.evaluate_all(
            "C:/dummy", [first, second], cfg, frame_targets={"first.mp4": "720p"}
        )
        by_name = {ev.video.filename: ev for ev in report.evaluations}
        self.assertEqual(by_name["first.mp4"].downscale_reason, DownscaleReason.FRAME_ANALYSIS)
        self.assertIsNone(by_name["second.mp4"].downscale_reason)


def _bitrate_fit_cfg(max_tier: str = "none") -> ClassifierConfig:
    """비트레이트 맞춤 축소를 켠 설정 생성, 최대 해상도는 선택 입력"""
    return ClassifierConfig(
        encode=EncodeConfig(max_resolution_tier=max_tier, auto_downscale_by_bitrate=True)
    )


class TestBitrateFitDownscale(unittest.TestCase):
    """비트레이트 맞춤 후보의 탐색 순서, 4K 초과 시작점, 360p 미달, 최대 해상도 합성, 직접 지정, 예외와 HDR 우선, 5절 되돌림을 검증"""

    def setUp(self) -> None:
        _use_legacy_size_model(self)

    def test_low_bitrate_4k_routes_to_first_fitting_tier(self) -> None:
        """4K 저화질 영상은 1440p(r=0.64)를 건너뛰고 1080p(r=1.03)를 비트레이트 맞춤 사유로 축소"""
        vid = _vid("uhd.mp4", 3000, 600.0, width=3840, height=2160)
        ev = VideoEvaluator.evaluate_video(vid, _bitrate_fit_cfg())
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertEqual((ev.smart_action, getattr(ev.target_tier, "key", None)), (SmartAction.ENCODE_DOWNSCALE, "1080p"))
        self.assertEqual((ev.target_width, ev.target_height), (1920, 1080))
        self.assertEqual(ev.downscale_reason, DownscaleReason.BITRATE_FIT)
        self.assertEqual(ev.reason_label, "비트레이트 맞춤")

    def test_source_above_4k_starts_search_at_4k_tier(self) -> None:
        """4K를 넘는 원본(4096x2160)은 4K 규격부터 탐색해 r=0.83인 4K 규격을 후보로 선택"""
        vid = _vid("dci.mp4", 7200, 600.0, width=4096, height=2160)
        ev = VideoEvaluator.evaluate_video(vid, _bitrate_fit_cfg())
        self.assertEqual((ev.smart_action, getattr(ev.target_tier, "key", None)), (SmartAction.ENCODE_DOWNSCALE, "2160p"))
        self.assertEqual((ev.target_width, ev.target_height), (3840, 2024))
        self.assertEqual(ev.downscale_reason, DownscaleReason.BITRATE_FIT)

    def test_no_candidate_when_even_360p_does_not_fit(self) -> None:
        """360p까지 줄여도 비트레이트가 맞지 않으면 후보 없이 축소 필드를 비움"""
        vid = _vid("fhd.mp4", 300, 600.0, width=1920, height=1080)
        ev = VideoEvaluator.evaluate_video(vid, _bitrate_fit_cfg())
        self.assertNotEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertIsNone(ev.target_tier)
        self.assertIsNone(ev.downscale_reason)

    def test_source_not_low_quality_has_no_candidate(self) -> None:
        """원본 화질이 저화질이 아니면(r_src >= 0.8) 4K 해상도라도 비트레이트 맞춤 후보를 만들지 않음"""
        vid = _vid("uhd_ok.mp4", 9000, 600.0, width=3840, height=2160)
        ev = VideoEvaluator.evaluate_video(vid, _bitrate_fit_cfg())
        self.assertNotEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertIsNone(ev.downscale_reason)
        self.assertIsNone(ev.downscale_skip_reason)

    def test_360p_source_has_no_lower_candidate(self) -> None:
        """360p 원본은 더 낮은 규격이 없어 저화질이어도 비트레이트 맞춤 후보를 만들지 않음"""
        vid = _vid("sd.mp4", 100, 600.0, width=640, height=360)
        ev = VideoEvaluator.evaluate_video(vid, _bitrate_fit_cfg())
        self.assertNotEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertIsNone(ev.downscale_reason)

    def test_disabled_setting_matches_default_config(self) -> None:
        """설정이 꺼져 있으면 기본 설정과 같은 판정 결과를 내고 비트레이트 맞춤 필드를 채우지 않음"""
        vid = _vid("uhd.mp4", 3000, 600.0, width=3840, height=2160)
        off = VideoEvaluator.evaluate_video(
            vid, ClassifierConfig(encode=EncodeConfig(auto_downscale_by_bitrate=False))
        )
        default = VideoEvaluator.evaluate_video(vid, ClassifierConfig())
        self.assertEqual(off, default)
        self.assertNotEqual(off.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertIsNone(off.downscale_reason)

    def test_max_resolution_and_fit_candidate_pick_lower_tier(self) -> None:
        """최대 해상도 1440p와 비트레이트 맞춤 1080p가 함께 있으면 더 낮은 1080p를 비트레이트 맞춤 사유로 적용"""
        vid = _vid("uhd.mp4", 3000, 600.0, width=3840, height=2160)
        ev = VideoEvaluator.evaluate_video(vid, _bitrate_fit_cfg("1440p"))
        self.assertEqual((ev.smart_action, getattr(ev.target_tier, "key", None)), (SmartAction.ENCODE_DOWNSCALE, "1080p"))
        self.assertEqual(ev.downscale_reason, DownscaleReason.BITRATE_FIT)

    def test_max_resolution_wins_when_lower_than_fit_candidate(self) -> None:
        """최대 해상도 720p가 비트레이트 맞춤 1080p보다 낮으면 720p를 최대 해상도 초과 사유로 적용"""
        vid = _vid("uhd.mp4", 3000, 600.0, width=3840, height=2160)
        ev = VideoEvaluator.evaluate_video(vid, _bitrate_fit_cfg("720p"))
        self.assertEqual((ev.smart_action, getattr(ev.target_tier, "key", None)), (SmartAction.ENCODE_DOWNSCALE, "720p"))
        self.assertEqual(ev.downscale_reason, DownscaleReason.MAX_RESOLUTION)

    def test_same_tier_reports_max_resolution_reason(self) -> None:
        """최대 해상도와 비트레이트 맞춤 후보가 같은 1080p면 판정 사유를 최대 해상도 초과로 표시"""
        vid = _vid("uhd.mp4", 3000, 600.0, width=3840, height=2160)
        ev = VideoEvaluator.evaluate_video(vid, _bitrate_fit_cfg("1080p"))
        self.assertEqual((ev.smart_action, getattr(ev.target_tier, "key", None)), (SmartAction.ENCODE_DOWNSCALE, "1080p"))
        self.assertEqual(ev.downscale_reason, DownscaleReason.MAX_RESOLUTION)

    def test_manual_tier_ignores_bitrate_fit_candidate(self) -> None:
        """직접 지정 규격이 있으면 비트레이트 맞춤 후보를 무시하고 직접 지정 사유로 축소"""
        vid = _vid("uhd.mp4", 3000, 600.0, width=3840, height=2160)
        ev = VideoEvaluator.evaluate_all(
            "C:/dummy", [vid], _bitrate_fit_cfg(), manual_targets={"uhd.mp4": "720p"}
        ).evaluations[0]
        self.assertEqual((ev.smart_action, getattr(ev.target_tier, "key", None)), (SmartAction.ENCODE_DOWNSCALE, "720p"))
        self.assertEqual(ev.downscale_reason, DownscaleReason.MANUAL)

    def test_manual_keep_ignores_bitrate_fit_candidate(self) -> None:
        """직접 지정 축소 안 함이면 비트레이트 맞춤 후보를 무시하고 축소하지 않음"""
        vid = _vid("uhd.mp4", 3000, 600.0, width=3840, height=2160)
        ev = VideoEvaluator.evaluate_all(
            "C:/dummy", [vid], _bitrate_fit_cfg(), manual_targets={"uhd.mp4": "keep"}
        ).evaluations[0]
        self.assertNotEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertIsNone(ev.target_tier)

    def test_exception_and_hdr_precede_bitrate_fit_candidate(self) -> None:
        """예외 규칙과 HDR 영상은 비트레이트 맞춤 후보가 있어도 축소를 검토하지 않고 보존"""
        vid = _vid("uhd.mp4", 3000, 600.0, width=3840, height=2160)
        excepted = VideoEvaluator.evaluate_video(
            vid, _bitrate_fit_cfg(), exception_rules=[lambda _video: True]
        )
        self.assertEqual(excepted.smart_action, SmartAction.PASS_EXCEPTION)
        self.assertIsNone(excepted.target_tier)

        hdr_vid = _vid("uhd_hdr.mp4", 3000, 600.0, width=3840, height=2160, is_hdr=True)
        hdr = VideoEvaluator.evaluate_video(hdr_vid, _bitrate_fit_cfg())
        self.assertEqual(hdr.smart_action, SmartAction.PASS_HDR)
        self.assertIsNone(hdr.target_tier)

    def test_block_noise_after_fit_candidate_keeps_original(self) -> None:
        """비트레이트 맞춤 1080p 후보가 블록 노이즈 조건에 걸리면 축소하지 않고 축소 후 화질 보호 사유를 남김"""
        vid = _vid("legacy.mp4", 3200, 600.0, width=3840, height=2160, codec="mpeg4")
        ev = VideoEvaluator.evaluate_video(vid, _bitrate_fit_cfg())
        self.assertNotEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertIsNone(ev.target_tier)
        self.assertEqual(ev.downscale_skip_reason, DownscaleSkipReason.LOW_QUALITY_AFTER_DOWNSCALE)


class TestQualityTargetJudgment(unittest.TestCase):
    """기준 품질값, 저화질 보호 용량 계수, 활성 화면 입력이 판정과 권장 처리 명칭에 미치는 영향을 검증"""

    def setUp(self) -> None:
        _use_legacy_size_model(self)

    def test_baseline_cq_lowers_target_bitrate_and_relabels_action(self) -> None:
        """기준 품질값을 24.0으로 올리면 최적 비트레이트가 낮아지고 품질값 열의 값이 24.0이 됨을 검증"""
        vid = _vid("over.mp4", 2500, 60.0)
        default_ev = VideoEvaluator.evaluate_video(vid, ClassifierConfig())
        cfg_24 = ClassifierConfig(encode=EncodeConfig(baseline_cq=24.0))
        ev_24 = VideoEvaluator.evaluate_video(vid, cfg_24)

        self.assertLess(ev_24.optimal_kbps, default_ev.optimal_kbps)
        self.assertEqual(ev_24.smart_action, SmartAction.ENCODE_OVER)
        self.assertEqual(ev_24.action_label, "품질 인코딩")
        self.assertEqual(ev_24.planned_cq, 24.0)

    def test_default_active_frame_matches_full_frame(self) -> None:
        """활성 화면을 주지 않거나 전체 프레임과 같게 주면 판정 결과가 같음을 검증"""
        vid = _vid("full.mp4", 1200, 60.0, width=1920, height=1080)
        cfg = ClassifierConfig()
        full = VideoEvaluator.evaluate_video(vid, cfg)
        same = VideoEvaluator.evaluate_video(vid, cfg, active_frame=ActiveFrame(1920, 1080))

        self.assertEqual(full.optimal_kbps, same.optimal_kbps)
        self.assertEqual(full.smart_action, same.smart_action)
        self.assertAlmostEqual(full.bitrate_ratio, same.bitrate_ratio)

    def test_confirmed_crop_is_stored_only_when_smaller_than_the_source(self) -> None:
        """원본보다 작은 활성 화면만 잘라내기 확정으로 남고, 전체 프레임은 남기지 않는지 검증"""
        vid = _vid("crop.mp4", 1200, 60.0, width=1920, height=1080)
        cfg = ClassifierConfig()
        crop = ActiveFrame(1600, 1000, 260, 40)
        ev = VideoEvaluator.evaluate_video(vid, cfg, active_frame=crop)
        self.assertEqual(ev.active_frame, crop)
        full = VideoEvaluator.evaluate_video(vid, cfg, active_frame=ActiveFrame(1920, 1080))
        self.assertIsNone(full.active_frame)
        report = VideoEvaluator.evaluate_all(
            "root", [vid], cfg, active_frames={vid.filename: crop}
        )
        self.assertEqual(report.evaluations[0].active_frame, crop)

    def test_narrower_active_frame_lowers_reference_bitrate(self) -> None:
        """화면 여백을 뺀 활성 화면이 작으면 기준 비트레이트가 낮아져 같은 비트레이트의 비율이 올라감을 검증"""
        vid = _vid("letterbox.mp4", 1200, 60.0, width=1920, height=1080)
        cfg = ClassifierConfig()
        full = VideoEvaluator.evaluate_video(vid, cfg)
        active = VideoEvaluator.evaluate_video(vid, cfg, active_frame=ActiveFrame(1920, 800))

        self.assertLess(active.optimal_kbps, full.optimal_kbps)
        self.assertGreater(active.bitrate_ratio, full.bitrate_ratio)

    def test_protect_offset_flips_only_intra_codec_savings_case(self) -> None:
        """보호 용량 계수 도입으로 절약 기준을 넘는 인트라 코덱 저화질 영상만 원본 보존에서 코덱 전환으로 바뀜을 검증

        - 전체 격자(코덱, 해상도, FPS, 비트레이트, KF, 길이) 비교에서 바뀐 사례는 이 조건(ProRes, MJPEG, 600kbps, KF 2.0초, 600초)뿐
        """
        vid = _vid("intra.mp4", 600, 600.0, codec="prores", kf=2.0)
        cfg = ClassifierConfig()
        with mock.patch(
            "src.core.evaluation.evaluator.protect_cq_continuous",
            side_effect=lambda baseline, ratio, encoder=None: baseline,
        ):
            before = VideoEvaluator.evaluate_video(vid, cfg)
        after = VideoEvaluator.evaluate_video(vid, cfg)

        self.assertEqual(before.smart_action, SmartAction.PASS_CODEC_SAVED)
        self.assertEqual(after.smart_action, SmartAction.ENCODE_CODEC_UPGRADE)
        self.assertGreater(after.applied_cq, cfg.encode.baseline_cq)
        self.assertEqual(after.action_label, "품질 인코딩")
        self.assertAlmostEqual(after.planned_cq or 0.0, _continuous_cq(after, cfg))

    def test_protect_offset_keeps_non_protected_judgment(self) -> None:
        """보호 경로가 아닌 적정 구간 영상은 보호 용량 계수와 무관하게 판정이 같음을 검증"""
        vid = _vid("optimal.mp4", 1500, 600.0, kf=2.0)
        cfg = ClassifierConfig()
        with mock.patch(
            "src.core.evaluation.evaluator.protect_cq_continuous",
            side_effect=lambda baseline, ratio, encoder=None: baseline,
        ):
            before = VideoEvaluator.evaluate_video(vid, cfg)
        after = VideoEvaluator.evaluate_video(vid, cfg)

        self.assertFalse(after.use_low_quality_preset)
        self.assertEqual(before.smart_action, after.smart_action)
        self.assertAlmostEqual(before.hypothetical_after_mb or 0.0, after.hypothetical_after_mb or 0.0)


class TestAlreadyEncodedJudgment(unittest.TestCase):
    """이미 인코딩 영상은 자동 판정에서 보존되고, 출력 해상도를 직접 지정했을 때만 같은 판정 체인으로 다시 판정되는지 검증"""

    _CFG_MAX_720P = _downscale_cfg("720p")

    def _evaluate(
        self, vid: VideoProps, cfg: ClassifierConfig, manual: dict[str, str] | None = None
    ) -> VideoEvaluation:
        return VideoEvaluator.evaluate_all("C:/dummy", [vid], cfg, manual_targets=manual).evaluations[0]

    def test_tagged_over_quality_video_is_preserved_while_plain_is_reencoded(self) -> None:
        """과화질 영상이라도 표식이 있으면 보존하고, 같은 조건의 표식 없는 영상은 인코딩 대상"""
        plain = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080)
        tagged = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080, has_encode_tag=True)
        cfg = ClassifierConfig()
        self.assertTrue(self._evaluate(plain, cfg).is_encode_target)
        ev = self._evaluate(tagged, cfg)
        self.assertEqual(ev.smart_action, SmartAction.PASS_ALREADY_ENCODED)
        self.assertFalse(ev.is_encode_target)
        self.assertEqual(ev.reason_label, "이미 인코딩")

    def test_manual_tier_with_savings_reencodes_tagged_video(self) -> None:
        """표식 영상도 출력 해상도를 직접 지정하고 절약 선별을 통과하면 축소 인코딩 대상이 됨"""
        vid = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080, has_encode_tag=True)
        ev = self._evaluate(vid, self._CFG_MAX_720P, {"hd.mp4": "360p"})
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertTrue(ev.is_encode_target)
        self.assertEqual(ev.downscale_reason, DownscaleReason.MANUAL)

    def test_manual_tier_matching_plain_video_ignores_savings(self) -> None:
        """표식 영상의 직접 지정은 절약 미달이어도 축소 인코딩으로 다시 판정됨"""
        tagged = _vid("short.mp4", 1800, 10.0, width=1920, height=1080, has_encode_tag=True)
        tagged_ev = self._evaluate(tagged, self._CFG_MAX_720P, {"short.mp4": "720p"})
        self.assertEqual(tagged_ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertTrue(tagged_ev.is_encode_target)
        self.assertEqual(tagged_ev.downscale_reason, DownscaleReason.MANUAL)

    def test_keep_preserves_tagged_video_without_resolution_info(self) -> None:
        """축소 안 함 지정은 표식 영상을 다시 판정하지 않고 보존하며 출력 해상도 정보를 남기지 않음"""
        vid = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080, has_encode_tag=True)
        ev = self._evaluate(vid, self._CFG_MAX_720P, {"hd.mp4": "keep"})
        self.assertEqual(ev.smart_action, SmartAction.PASS_ALREADY_ENCODED)
        self.assertFalse(ev.is_encode_target)
        self.assertIsNone(ev.downscale_reason)
        self.assertIsNone(ev.target_width)

    def test_meaningless_manual_tier_does_not_leak_into_encode_policies(self) -> None:
        """원본과 거의 같은 규격을 지정해도 표식 영상은 과화질 정책으로 새지 않고 보존됨"""
        plain = _vid("almost.mp4", 9000, 100.0, width=1920, height=1088)
        tagged = _vid("almost.mp4", 9000, 100.0, width=1920, height=1088, has_encode_tag=True)
        self.assertTrue(self._evaluate(plain, ClassifierConfig()).is_encode_target)
        ev = self._evaluate(tagged, self._CFG_MAX_720P, {"almost.mp4": "1080p"})
        self.assertEqual(ev.smart_action, SmartAction.PASS_ALREADY_ENCODED)
        self.assertFalse(ev.is_encode_target)

    def test_automatic_max_resolution_candidate_is_not_applied_to_tagged_video(self) -> None:
        """최대 해상도 초과 자동 축소 후보가 있어도 표식 영상은 직접 지정이 없으면 보존"""
        plain = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080)
        tagged = _vid("hd.mp4", 9000, 100.0, width=1920, height=1080, has_encode_tag=True)
        self.assertEqual(self._evaluate(plain, self._CFG_MAX_720P).downscale_reason, DownscaleReason.MAX_RESOLUTION)
        ev = self._evaluate(tagged, self._CFG_MAX_720P)
        self.assertEqual(ev.smart_action, SmartAction.PASS_ALREADY_ENCODED)
        self.assertIsNone(ev.downscale_reason)


class TestContinuousProtectQualityNotSaturated(unittest.TestCase):
    """저화질 보호 품질값이 목표 비율 구간에서 상한에 붙지 않고 연속으로 커지는지 검증

    - 비율은 판정이 쓰는 목표 비트레이트 비율(r_out)이며 인코더 크기 보정을 넣지 않음
    - hevc_nvenc(유효 지수 5.8)와 libx264(15.33)의 지수 차이로 상한에 붙는 비율이 달라짐
    """

    _CFG_HEVC = ClassifierConfig()
    _CFG_X264 = ClassifierConfig(encode=EncodeConfig(encode_codec="libx264"))

    def _protect_cq(
        self, cfg: ClassifierConfig, kbps: int, *, target_opt_kbps: int
    ) -> VideoEvaluation:
        # - 저화질 구간이면서 키프레임 초과여야 저화질 프리셋 인코딩으로 판정됨
        vid = _vid("low.mp4", kbps, 60.0, kf=5.0, codec="h264")
        ev = VideoEvaluator.evaluate_video(vid, cfg)
        self.assertTrue(ev.use_low_quality_preset)
        self.assertTrue(ev.is_encode_target)
        self.assertAlmostEqual(ev.target_bitrate_ratio, kbps / target_opt_kbps, places=3)
        return ev

    def test_hevc_keeps_increasing_between_boundary_and_cap(self) -> None:
        """hevc_nvenc는 출력 비율 0.6~0.8 구간에서 기준값과 상한 사이 값을 냄을 검증"""
        baseline = self._CFG_HEVC.encode.baseline_cq
        cap = baseline + PROTECT_CQ_DELTA_MAX
        produced: list[float] = []
        # - 비율이 작아지는 순서로 넣어 올린 폭이 커지는지 확인
        for kbps in (936, 840, 744):
            ev = self._protect_cq(self._CFG_HEVC, kbps, target_opt_kbps=1200)
            expected = protect_cq_continuous(
                baseline, ev.target_bitrate_ratio, "hevc_nvenc"
            )
            self.assertEqual(ev.applied_cq, expected)
            self.assertGreater(ev.applied_cq, baseline)
            self.assertLess(ev.applied_cq, cap)
            produced.append(ev.applied_cq)
        self.assertEqual(produced, sorted(produced))

    def test_hevc_caps_only_for_small_ratio(self) -> None:
        """hevc_nvenc는 출력 비율이 상한 비율(약 0.49) 미만일 때만 상한에 붙음을 검증"""
        baseline = self._CFG_HEVC.encode.baseline_cq
        ev = self._protect_cq(self._CFG_HEVC, 300, target_opt_kbps=1200)
        self.assertEqual(ev.applied_cq, baseline + PROTECT_CQ_DELTA_MAX)

    def test_libx264_uses_its_own_k(self) -> None:
        """libx264는 유효 지수가 커서 같은 비율에서 더 크게 오르고 상한 비율도 높음을 검증"""
        baseline = self._CFG_X264.encode.baseline_cq
        cap = baseline + PROTECT_CQ_DELTA_MAX
        ev = self._protect_cq(self._CFG_X264, 1170, target_opt_kbps=1500)
        expected = protect_cq_continuous(
            baseline, ev.target_bitrate_ratio, "libx264"
        )
        self.assertEqual(ev.applied_cq, expected)
        self.assertGreater(ev.applied_cq, baseline)
        self.assertLess(ev.applied_cq, cap)

        clamped = self._protect_cq(self._CFG_X264, 930, target_opt_kbps=1500)
        self.assertEqual(clamped.applied_cq, cap)
        self.assertGreater(clamped.applied_cq, ev.applied_cq)


    def test_downscaled_encode_keeps_baseline_quality(self) -> None:
        """원본 해상도에서 저화질이어도 축소 인코딩은 기준 품질값으로 진행함을 검증

        - 축소 후보는 축소 뒤 저화질이면 버리므로 실제 인코딩 품질값도 기준 값과 같아야 함
        """
        cfg = ClassifierConfig(encode=EncodeConfig(max_resolution_tier="720p"))
        vid = _vid("low1080.mp4", 1200, 300.0, width=1920, height=1080, kf=5.0)
        ev = VideoEvaluator.evaluate_video(vid, cfg)
        # - 원본 비율만 보면 저화질 보호 대상이지만 축소 인코딩이 채택됨
        self.assertEqual(ev.quality_zone, QualityZone.LOW)
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_DOWNSCALE)
        self.assertFalse(ev.use_low_quality_preset)
        self.assertEqual(ev.applied_cq, cfg.encode.baseline_cq)
        self.assertEqual(ev.option_label, "품질 22.0 + 최대 BR + 1초 키프레임, 축소")


class TestEncoderCalibration(unittest.TestCase):
    """4c 확정 상수(인코더 크기 보정, 키프레임 목표 상한)가 판정에 반영되는지 검증"""

    def test_size_estimator_applies_encoder_factor_without_codec_cap(self) -> None:
        """코덱 가중치가 인코더 크기 보정을 깎지 않고 그대로 곱해지는지 검증"""
        vid = _vid("cal.mp4", 3000, 10.0)
        stream = estimate_video_stream_mb(vid)
        fixed = vid.vid_size_MB - stream
        after = estimate_after_size_mb(
            vid, 2.5, _RATE, cap_at_original=False, codec_weight_ratio=0.8, encoder="hevc_nvenc"
        )
        self.assertAlmostEqual(after, fixed + stream * (2.60 * min(1.0, 1.0 / 2.5, 0.8)))

    def test_keyframe_exceeded_expected_size_is_capped_at_decision_33_target(self) -> None:
        """키프레임 초과 영상의 예상 크기가 결정 33 목표 크기를 넘지 않는지 검증"""
        cfg = ClassifierConfig()
        vid = _vid("optkf.mp4", 1300, 60.0, kf=5.0)
        ev = VideoEvaluator.evaluate_video(vid, cfg)
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_OPT_KF)
        stream = estimate_video_stream_mb(vid)
        increase = stream * calc_keyframe_increase_ratio(
            5.0, cfg.thresholds.keyframe_1s_increase_rate
        )
        self.assertAlmostEqual(ev.expected_after_mb, vid.vid_size_MB + increase)
        self.assertAlmostEqual(ev.expected_delta_mb, -increase)

    def test_calibrated_over_quality_item_reports_no_savings(self) -> None:
        """보정 뒤 예상이 원본에 닿는 과화질 항목은 절약 미달로 보존되는지 검증"""
        vid = _vid("over.mp4", 3000, 60.0)
        ev = VideoEvaluator.evaluate_video(vid, ClassifierConfig())
        self.assertEqual(ev.quality_zone, QualityZone.OVER)
        self.assertEqual(ev.smart_action, SmartAction.PASS_LOW_SAVED)
        self.assertEqual(ev.expected_after_mb, vid.vid_size_MB)


if __name__ == "__main__":
    unittest.main(verbosity=2)
