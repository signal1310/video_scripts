import unittest

from src.core.config import (
    VALID_ENCODE_CODECS,
    ClassifierConfig,
    EncodeConfig,
    SavingsConfig,
    get_encode_codec_family,
)
from src.core.evaluator import (
    DEFAULT_CLASSIFICATION_POLICIES,
    ClassificationDecision,
    EvaluationContext,
    VideoEvaluator,
)
from src.core.models import (
    BYTES_PER_MB,
    ActionBadgeTone,
    QualityZone,
    SmartAction,
    VideoProps,
)
from src.core.size_estimator import (
    DEFAULT_UNKNOWN_KF_SEC,
    KF_CLAMP_MAX_SEC,
    calc_keyframe_increase_ratio,
    estimate_after_size_mb,
    estimate_video_stream_mb,
)
from src.core.sorting import WindowsNameOrder, compare_natural_order, compare_natural_tokens

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
) -> VideoProps:
    """비트레이트와 길이에 맞춰 용량이 일관된 테스트 영상 생성 (기본값: 720p30 H.264, 키프레임 1초)"""
    return VideoProps(
        name, width, height, 0, fps, kbps, _AUDIO_KBPS, _size_mb(kbps, duration),
        duration, codec, kf, is_hdr=is_hdr,
    )


class TestEvaluatorModule(unittest.TestCase):
    """비트레이트 스케일링 수식, 화질 등급 경계값, 용량 추정, 절약 선별, HDR 보존 등 코어 판정 엔진을 검증"""

    def setUp(self) -> None:
        self.cfg = ClassifierConfig()

    def test_optimal_bitrate_resolution_monotonicity(self) -> None:
        """360p부터 8K까지 해상도 증가에 따라 최적 비트레이트가 엄격히 단조 증가하는지 검증"""
        bm = self.cfg.bitrate_model
        cw = self.cfg.codec_weights
        opt_720 = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "h264")
        self.assertEqual(opt_720, 1500)

        opt_360 = VideoEvaluator.calc_optimal_bitrate(640, 360, bm, cw, 30.0, "h264")
        opt_480 = VideoEvaluator.calc_optimal_bitrate(854, 480, bm, cw, 30.0, "h264")
        opt_1080 = VideoEvaluator.calc_optimal_bitrate(1920, 1080, bm, cw, 30.0, "h264")
        opt_1440 = VideoEvaluator.calc_optimal_bitrate(2560, 1440, bm, cw, 30.0, "h264")
        opt_4k = VideoEvaluator.calc_optimal_bitrate(3840, 2160, bm, cw, 30.0, "h264")
        opt_8k = VideoEvaluator.calc_optimal_bitrate(7680, 4320, bm, cw, 30.0, "h264")

        self.assertTrue(100 < opt_360 < opt_480 < opt_720 < opt_1080 < opt_1440 < opt_4k < opt_8k)

    def test_optimal_bitrate_extreme_dimensions(self) -> None:
        """16x16, 0x0 등 극한의 초소형 해상도 입력 시에도 최소 하한선(100kbps)을 유지하는지 검증"""
        bm = self.cfg.bitrate_model
        cw = self.cfg.codec_weights
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(16, 16, bm, cw, 30.0, "h264"), 100)
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(0, 0, bm, cw, 30.0, "h264"), 100)

    def test_optimal_bitrate_fps_scaling(self) -> None:
        """30fps 이하와 불감대 이내(32fps)는 1.0 가중치를 유지하고, 60fps 및 120fps는 비선형 지수에 따라 증가하는지 검증"""
        bm = self.cfg.bitrate_model
        cw = self.cfg.codec_weights
        base = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "h264")
        self.assertEqual(base, VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 15.0, "h264"))
        self.assertEqual(base, VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 24.0, "h264"))
        self.assertEqual(base, VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 32.0, "h264"))

        opt_60 = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 60.0, "h264")
        opt_120 = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 120.0, "h264")
        self.assertEqual(opt_60, round(1500 * ((60.0 / 30.0) ** 0.60)))
        self.assertEqual(opt_120, round(1500 * ((120.0 / 30.0) ** 0.60)))
        self.assertTrue(base < opt_60 < opt_120)

    def test_optimal_bitrate_codec_weights(self) -> None:
        """코덱별 효율 가중치와 미등록 코덱 기본값(1.00) 적용을 검증"""
        bm = self.cfg.bitrate_model
        cw = self.cfg.codec_weights
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "av1"), 975)
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "hevc"), 1200)
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "h264"), 1500)
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "mpeg4"), 1950)
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "mpeg2video"), 2400)
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "prores"), 3750)
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, " HEVC "), 1200)
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "unknown_codec_xyz"), 1500)

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
        self.assertEqual(VideoEvaluator.classify_quality_zone(0.7999, th), QualityZone.LOW)
        self.assertEqual(VideoEvaluator.classify_quality_zone(0.8000, th), QualityZone.OPTIMAL)
        self.assertEqual(VideoEvaluator.classify_quality_zone(1.2000, th), QualityZone.OPTIMAL)
        self.assertEqual(VideoEvaluator.classify_quality_zone(1.2001, th), QualityZone.OVER)

    def test_resolve_quality_zone_uses_source_for_low_and_output_for_over(self) -> None:
        """저화질은 원본 코덱 기준을 우선하고, 과화질은 출력 코덱 기준으로 판정하는지 검증"""
        th = self.cfg.thresholds
        # 720p H.264 1500kbps: 원본 기준 1.00, HEVC 출력 기준 1.25
        self.assertEqual(VideoEvaluator.resolve_quality_zone(1.0, 1.25, th), QualityZone.OVER)
        # 원본 기준 저화질이면 출력 기준이 과화질이어도 저화질 보호 우선
        self.assertEqual(VideoEvaluator.resolve_quality_zone(0.79, 1.215, th), QualityZone.LOW)
        self.assertEqual(VideoEvaluator.resolve_quality_zone(1.0, 1.0, th), QualityZone.OPTIMAL)

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

    def test_savings_filter_playback_basis(self) -> None:
        """재생 기준 절약 선별의 최소 절감량, 분당 절감량, 대용량 예외, 키프레임 불량 우선 규칙을 검증"""
        cfg_eff = ClassifierConfig(savings=SavingsConfig(min_saved_mb=5.0, min_mb_per_min=3.0, exempt_saved_mb=50.0))

        # 1. 절감량 < min_saved_mb (2400kbps, r_out=2.0, 10초 -> 약 1.4MB 절감)
        vid_small = _vid("small.mp4", 2400, 10.0)
        ev_small = VideoEvaluator.evaluate_video(vid_small, cfg_eff)
        self.assertEqual(ev_small.smart_action, SmartAction.PASS_LOW_SAVED)
        self.assertIsNone(ev_small.target_dirname)
        self.assertEqual(ev_small.expected_delta_mb, 0.0)

        # 2. 절감량은 충분하지만 재생 1분당 절감량 미달 (1500kbps, r_out=1.25, 10분 -> 약 21.5MB, 2.1MB/분)
        vid_slow = _vid("slow.mp4", 1500, 600.0)
        ev_slow = VideoEvaluator.evaluate_video(vid_slow, cfg_eff)
        self.assertEqual(ev_slow.smart_action, SmartAction.PASS_LOW_PER_MIN)
        self.assertIsNone(ev_slow.target_dirname)

        # 3. 같은 영상이라도 절감량이 면제 기준 이상이면 분당 기준 면제
        vid_exempt = _vid("exempt.mp4", 1500, 1800.0)
        self.assertEqual(VideoEvaluator.evaluate_video(vid_exempt, cfg_eff).smart_action, SmartAction.ENCODE_CQ22_OVER)

        # 4. exempt_saved_mb == 0 이면 면제 비활성
        cfg_no_exempt = ClassifierConfig(savings=SavingsConfig(min_saved_mb=5.0, min_mb_per_min=3.0, exempt_saved_mb=0.0))
        self.assertEqual(VideoEvaluator.evaluate_video(vid_exempt, cfg_no_exempt).smart_action, SmartAction.PASS_LOW_PER_MIN)

        # 5. 키프레임 불량이면 절감 효율과 무관하게 인코딩 대상 유지
        vid_bad_kf = _vid("bad_kf.mp4", 1500, 600.0, kf=5.0)
        self.assertEqual(VideoEvaluator.evaluate_video(vid_bad_kf, cfg_eff).smart_action, SmartAction.ENCODE_CQ22_OVER)

    def test_savings_basis_playback_and_encode_select_differently(self) -> None:
        """같은 영상이라도 재생 기준은 미달, 예상 인코딩 기준은 통과로 갈릴 수 있는지 검증"""
        vid = _vid("basis.mp4", 1500, 600.0)  # 재생 1분당 2.1MB, 예상 인코딩 1분당 약 4.8MB
        base = dict(min_saved_mb=5.0, min_mb_per_min=3.0, exempt_saved_mb=50.0)
        cfg_play = ClassifierConfig(savings=SavingsConfig(**base, selection_basis="playback"))
        cfg_enc = ClassifierConfig(savings=SavingsConfig(**base, selection_basis="encode"))
        self.assertEqual(VideoEvaluator.evaluate_video(vid, cfg_play).smart_action, SmartAction.PASS_LOW_PER_MIN)
        self.assertEqual(VideoEvaluator.evaluate_video(vid, cfg_enc).smart_action, SmartAction.ENCODE_CQ22_OVER)

    def test_encode_basis_skips_efficiency_check_when_time_is_zero(self) -> None:
        """예상 인코딩 시간이나 재생 길이가 0이면 분당 효율 검사를 생략하고 절감량 기준만 적용하는지 검증"""
        vid = VideoProps("z.mp4", 1280, 720, 0, 30.0, 1500, 128, 100.0, 0.0, "h264", 1.0)
        for basis in ("playback", "encode"):
            cfg = ClassifierConfig(savings=SavingsConfig(min_saved_mb=5.0, min_mb_per_min=3.0, selection_basis=basis))
            with self.subTest(basis=basis):
                self.assertEqual(VideoEvaluator.evaluate_video(vid, cfg).smart_action, SmartAction.ENCODE_CQ22_OVER)

    def test_over_zone_thresholds_are_inclusive_at_boundaries(self) -> None:
        """최소 절감량, 분당 절감량, 대용량 면제 기준이 경계값과 같을 때 인코딩 쪽으로 판정되는지 검증"""
        vid = _vid("v.mp4", 2400, 600.0)  # r_out=2.0, 절감량 약 85.8MB
        base_ev = VideoEvaluator.evaluate_video(vid, ClassifierConfig(savings=SavingsConfig(min_saved_mb=0.0, min_mb_per_min=0.0, exempt_saved_mb=0.0)))
        saved = base_ev.preview_delta_mb
        per_min = saved / (vid.duration / 60.0)

        def action(min_saved: float, min_per_min: float, exempt: float) -> SmartAction:
            savings = SavingsConfig(min_saved_mb=min_saved, min_mb_per_min=min_per_min, exempt_saved_mb=exempt)
            return VideoEvaluator.evaluate_video(vid, ClassifierConfig(savings=savings)).smart_action

        self.assertEqual(action(saved, 0.0, 0.0), SmartAction.ENCODE_CQ22_OVER)
        self.assertEqual(action(saved * 1.000001, 0.0, 0.0), SmartAction.PASS_LOW_SAVED)
        self.assertEqual(action(0.0, per_min * (1 - 1e-9), 0.0), SmartAction.ENCODE_CQ22_OVER)
        self.assertEqual(action(0.0, per_min * 1.000001, 0.0), SmartAction.PASS_LOW_PER_MIN)
        self.assertEqual(action(0.0, per_min * 1.000001, saved), SmartAction.ENCODE_CQ22_OVER)
        self.assertEqual(action(0.0, per_min * 1.000001, saved * 1.000001), SmartAction.PASS_LOW_PER_MIN)

    def test_hdr_is_preserved_before_zone_policies(self) -> None:
        """HDR 영상은 과화질이어도 원본을 보존하고, 예외 규칙은 HDR보다 우선하는지 검증"""
        vid = _vid("hdr.mp4", 2400, 60.0, is_hdr=True)
        ev = VideoEvaluator.evaluate_video(vid, self.cfg)
        self.assertEqual(ev.smart_action, SmartAction.PASS_HDR)
        self.assertEqual(ev.quality_zone, QualityZone.OVER)
        self.assertIsNone(ev.target_dirname)
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
            _vid("opt.mp4", 1300, 60.0),
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
        self.assertEqual(ev_over.smart_action, SmartAction.ENCODE_CQ22_OVER)
        self.assertEqual(ev_over.target_dirname, self.cfg.dir_standard_cq22)

        ev_opt_good = VideoEvaluator.evaluate_video(_vid("og.mp4", 1300, 60.0), self.cfg)
        self.assertEqual(ev_opt_good.smart_action, SmartAction.PASS_OPTIMAL)
        self.assertIsNone(ev_opt_good.target_dirname)

        ev_opt_bad = VideoEvaluator.evaluate_video(_vid("ob.mp4", 1300, 60.0, kf=5.0), self.cfg)
        self.assertEqual(ev_opt_bad.smart_action, SmartAction.ENCODE_CQ22_OPT_KF)
        self.assertEqual(ev_opt_bad.target_dirname, self.cfg.dir_standard_cq22)

        ev_low_good = VideoEvaluator.evaluate_video(_vid("lg.mp4", 800, 60.0), self.cfg)
        self.assertEqual(ev_low_good.smart_action, SmartAction.PASS_LOW)
        self.assertIsNone(ev_low_good.target_dirname)

        ev_low_bad = VideoEvaluator.evaluate_video(_vid("lb.mp4", 800, 60.0, kf=5.0), self.cfg)
        self.assertEqual(ev_low_bad.smart_action, SmartAction.ENCODE_LOW_KF)
        self.assertEqual(ev_low_bad.target_dirname, self.cfg.dir_low_quality_keyframe)

    def test_evaluate_video_exception_rules(self) -> None:
        """사용자 정의 예외 규칙이 일치하면 화질 등급과 무관하게 PASS_EXCEPTION이고 target_dirname=None인지 검증"""
        vid_over = _vid("vip_archive.mp4", 6000, 60.0, kf=5.0)

        def rule(v: VideoProps) -> bool:
            return "vip" in v.filename

        ev = VideoEvaluator.evaluate_video(vid_over, self.cfg, exception_rules=[rule])
        self.assertEqual(ev.smart_action, SmartAction.PASS_EXCEPTION)
        self.assertIsNone(ev.target_dirname)
        self.assertEqual(ev.expected_delta_mb, 0.0)

    def test_summary_report_aggregation_empty_and_normal(self) -> None:
        """SummaryReport 집계 시 빈 디렉터리 및 다중 파일에서의 누적/순수 절감량 계산 정확성을 검증"""
        rep_empty = VideoEvaluator.evaluate_all("C:/empty", [], self.cfg)
        self.assertEqual(rep_empty.total_count, 0)
        self.assertEqual(rep_empty.net_saved_mb, 0.0)

        videos = [_vid("over.mp4", 2400, 60.0), _vid("opt_bad_kf.mp4", 1200, 60.0, kf=5.0)]
        rep = VideoEvaluator.evaluate_all("C:/test", videos, self.cfg)
        self.assertEqual(rep.total_count, 2)
        self.assertEqual(rep.encoded_count, 2)
        self.assertEqual(rep.passed_count, 0)
        self.assertGreater(rep.gross_saved_mb, 0.0)
        self.assertGreater(rep.keyframe_increase_mb, 0.0)
        self.assertAlmostEqual(rep.net_saved_mb, rep.gross_saved_mb - rep.keyframe_increase_mb)

    def test_custom_classification_policy_injection_and_badge_tones(self) -> None:
        """우선순위 정책 체인 주입으로 판정 확장이 가능하고 SmartAction 배지 범주가 일관적인지 검증"""
        self.assertEqual(SmartAction.ENCODE_CQ22_OVER.badge_tone, ActionBadgeTone.STD_ENCODE)
        self.assertEqual(SmartAction.ENCODE_LOW_KF.badge_tone, ActionBadgeTone.LOW_KF_ENCODE)
        self.assertEqual(SmartAction.PASS_OPTIMAL.badge_tone, ActionBadgeTone.PASS)
        self.assertEqual(SmartAction.PASS_HDR.badge_tone, ActionBadgeTone.PASS)

        vid_over = _vid("custom.mp4", 2400, 60.0)

        def force_archive_policy(ctx: EvaluationContext) -> ClassificationDecision | None:
            if ctx.video.filename.startswith("custom"):
                return ClassificationDecision(
                    smart_action=SmartAction.PASS_EXCEPTION,
                    target_dirname=None,
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
        vid_opt = _vid("opt.mp4", 1300, 60.0)  # 적정 구간, r_out=1.08
        vid_low_2s = _vid("low2s.mp4", 800, 60.0, kf=2.0)  # 저화질, 키프레임 2초

        rep = VideoEvaluator.evaluate_all("C:/test", [vid_slow, vid_small, vid_opt, vid_low_2s], cfg_eff)
        self.assertEqual(rep.encoded_count, 0)
        self.assertAlmostEqual(rep.net_saved_mb, 0.0)
        self.assertAlmostEqual(rep.total_expected_after_mb, rep.total_orig_mb)

        ev_slow, ev_small, ev_opt, ev_low_2s = rep.evaluations
        self.assertAlmostEqual(ev_slow.preview_delta_mb, estimate_video_stream_mb(vid_slow) * 0.2)
        self.assertAlmostEqual(ev_small.preview_delta_mb, estimate_video_stream_mb(vid_small) * 0.5)
        self.assertAlmostEqual(ev_opt.preview_delta_mb, estimate_video_stream_mb(vid_opt) * (1 - 1 / (1300 / 1200)))
        keyframe_only = estimate_video_stream_mb(vid_low_2s) * calc_keyframe_increase_ratio(2.0, _RATE)
        self.assertAlmostEqual(ev_low_2s.preview_delta_mb, -keyframe_only)

        ev_exc = VideoEvaluator.evaluate_video(vid_slow, cfg_eff, exception_rules=[lambda v: "slow" in v.filename])
        self.assertEqual(ev_exc.smart_action, SmartAction.PASS_EXCEPTION)
        self.assertEqual(ev_exc.expected_delta_mb, 0.0)
        self.assertAlmostEqual(ev_exc.preview_delta_mb, estimate_video_stream_mb(vid_slow) * 0.2)

    def test_estimate_encode_duration_is_linear_in_pixel_rate(self) -> None:
        """예상 인코딩 시간이 해상도와 fps의 화소 처리량에 비례하고 기준 배속으로 나뉘는지 검증"""
        from src.core.system_probe import recommend_encode_speed_for_profile

        fhd_30 = _vid("fhd30.mp4", 9000, 120.0, width=1920, height=1080)
        hd_30 = _vid("hd30.mp4", 4500, 120.0, width=1280, height=720)
        uhd_60 = _vid("uhd60.mp4", 30000, 120.0, width=3840, height=2160, fps=60.0)
        opt_pass = _vid("opt.mp4", 2600, 120.0, width=1920, height=1080)

        cfg_2x = ClassifierConfig(encode=EncodeConfig(encode_speed_factor=2.0))
        self.assertAlmostEqual(VideoEvaluator.calc_encode_workload_ratio(1920, 1080, 30.0), 1.0)
        self.assertAlmostEqual(VideoEvaluator.calc_encode_workload_ratio(3840, 2160, 60.0), 8.0)
        self.assertAlmostEqual(VideoEvaluator.calc_encode_workload_ratio(1280, 720, 30.0), 0.444, places=3)
        self.assertAlmostEqual(VideoEvaluator.estimate_encode_duration_sec(fhd_30, cfg_2x), 60.0)
        self.assertLess(
            VideoEvaluator.estimate_encode_duration_sec(hd_30, cfg_2x),
            VideoEvaluator.estimate_encode_duration_sec(fhd_30, cfg_2x),
        )
        self.assertAlmostEqual(
            VideoEvaluator.estimate_encode_duration_sec(uhd_60, cfg_2x),
            VideoEvaluator.estimate_encode_duration_sec(fhd_30, cfg_2x) * 8.0,
        )
        # 기준 비트레이트 모델의 fps를 바꿔도 인코딩 작업량 기준은 1080p 30fps로 고정
        cfg_fps60 = ClassifierConfig(encode=EncodeConfig(encode_speed_factor=2.0))
        cfg_fps60.bitrate_model.base_fps = 60.0
        self.assertAlmostEqual(VideoEvaluator.estimate_encode_duration_sec(fhd_30, cfg_fps60), 60.0)

        rep = VideoEvaluator.evaluate_all("C:/test", [fhd_30, opt_pass], cfg_2x)
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
            self.assertEqual(VideoEvaluator.estimate_encode_duration_sec(vid, self.cfg), 0.0)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
