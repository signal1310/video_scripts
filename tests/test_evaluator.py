import unittest

from src.core.config import ClassifierConfig, EfficiencyConfig
from src.core.evaluator import (
    DEFAULT_CLASSIFICATION_POLICIES,
    ClassificationDecision,
    EvaluationContext,
    VideoEvaluator,
)
from src.core.models import (
    ActionBadgeTone,
    QualityZone,
    SmartAction,
    VideoProps,
)
from src.core.sorting import WindowsNameOrder, compare_natural_order, compare_natural_tokens


class TestEvaluatorModule(unittest.TestCase):
    """비트레이트 스케일링 수식, 화질 등급 경계값, 키프레임 판별, 소형 영상 분포 분석 등 코어 도메인 엔진의 무결성을 검증"""

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
        """16x16, 1x1, 0x0 등 극한의 초소형 해상도 입력 시에도 0으로 나누기 오류 없이 최소 하한선(100kbps)을 유지하는지 검증"""
        bm = self.cfg.bitrate_model
        cw = self.cfg.codec_weights
        opt_tiny = VideoEvaluator.calc_optimal_bitrate(16, 16, bm, cw, 30.0, "h264")
        self.assertEqual(opt_tiny, 100)

        opt_zero = VideoEvaluator.calc_optimal_bitrate(0, 0, bm, cw, 30.0, "h264")
        self.assertEqual(opt_zero, 100)

    def test_optimal_bitrate_fps_scaling(self) -> None:
        """30fps 이하는 1.0 가중치를 유지하고, 60fps 및 120fps는 비선형 지수(beta=0.60)에 따라 합리적으로 증가하는지 검증"""
        bm = self.cfg.bitrate_model
        cw = self.cfg.codec_weights
        base = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "h264")
        opt_15 = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 15.0, "h264")
        opt_24 = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 24.0, "h264")
        opt_32 = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 32.0, "h264")

        self.assertEqual(base, opt_15)
        self.assertEqual(base, opt_24)
        self.assertEqual(base, opt_32)

        opt_60 = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 60.0, "h264")
        opt_120 = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 120.0, "h264")

        expected_60 = round(1500 * ((60.0 / 30.0) ** 0.60))
        expected_120 = round(1500 * ((120.0 / 30.0) ** 0.60))

        self.assertEqual(opt_60, expected_60)
        self.assertEqual(opt_120, expected_120)
        self.assertTrue(base < opt_60 < opt_120)

    def test_optimal_bitrate_codec_efficiency_weights(self) -> None:
        """코덱별 효율 가중치(AV1=0.65, HEVC=0.80, H264=1.00, MPEG4=1.35, MPEG2=1.60, ProRes=2.50)와 미등록 코덱 기본값(1.00) 적용을 검증"""
        bm = self.cfg.bitrate_model
        cw = self.cfg.codec_weights
        opt_av1 = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "av1")
        opt_hevc = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "hevc")
        opt_h264 = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "h264")
        opt_mpeg4 = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "mpeg4")
        opt_mpeg2 = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "mpeg2video")
        opt_prores = VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "prores")

        self.assertEqual(opt_av1, 975)
        self.assertEqual(opt_hevc, 1200)
        self.assertEqual(opt_h264, 1500)
        self.assertEqual(opt_mpeg4, 1950)
        self.assertEqual(opt_mpeg2, 2400)
        self.assertEqual(opt_prores, 3750)

        # 대소문자 및 양끝 공백 무시
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, " HEVC "), 1200)

        # 미등록 코덱의 1.00 fallback
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(1280, 720, bm, cw, 30.0, "unknown_codec_xyz"), 1500)

    def test_classify_quality_zone_strict_boundaries(self) -> None:
        """화질 3구간 경계값(0.80 미만: LOW, 0.80~1.20: OPTIMAL, 1.20 초과: OVER)의 엄밀한 판정을 검증"""
        th = self.cfg.thresholds
        self.assertEqual(VideoEvaluator.classify_quality_zone(0.7999, th), QualityZone.LOW)
        self.assertEqual(VideoEvaluator.classify_quality_zone(0.8000, th), QualityZone.OPTIMAL)
        self.assertEqual(VideoEvaluator.classify_quality_zone(1.0000, th), QualityZone.OPTIMAL)
        self.assertEqual(VideoEvaluator.classify_quality_zone(1.2000, th), QualityZone.OPTIMAL)
        self.assertEqual(VideoEvaluator.classify_quality_zone(1.2001, th), QualityZone.OVER)

    def test_is_keyframe_defective_boundaries(self) -> None:
        """키프레임 결함 판단 임계값(기본 2.2초) 경계 판정 및 None 방어를 검증"""
        th = self.cfg.thresholds
        vid_normal = VideoProps("test.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 10.0, "h264", keyframe_interval=2.2000)
        self.assertFalse(VideoEvaluator.is_keyframe_defective(vid_normal, th))

        vid_defective = VideoProps("test.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 10.0, "h264", keyframe_interval=2.2001)
        self.assertTrue(VideoEvaluator.is_keyframe_defective(vid_defective, th))

        vid_none = VideoProps("test.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 10.0, "h264", keyframe_interval=None)
        self.assertFalse(VideoEvaluator.is_keyframe_defective(vid_none, th))

    def test_estimate_size_change_over_zone(self) -> None:
        """과화질 구간 감축 시의 용량 변화 예측과 1초 키프레임 오버헤드 반영을 검증"""
        th = self.cfg.thresholds
        vid_good_kf = VideoProps("test.mp4", 1280, 720, 0, 30.0, 3000, 128, 100.0, 10.0, "h264", keyframe_interval=1.0)
        after_mb, delta_mb = VideoEvaluator.estimate_size_change_mb(
            vid_good_kf, 2.0, QualityZone.OVER, is_keyframe_defective=False, will_encode=True, thresholds=th
        )
        self.assertAlmostEqual(after_mb, 50.0)
        self.assertAlmostEqual(delta_mb, 50.0)

        # 키프레임 불량 시 4% I-프레임 오버헤드 추가
        after_mb_bad, delta_mb_bad = VideoEvaluator.estimate_size_change_mb(
            vid_good_kf, 2.0, QualityZone.OVER, is_keyframe_defective=True, will_encode=True, thresholds=th
        )
        self.assertAlmostEqual(after_mb_bad, 52.0)
        self.assertAlmostEqual(delta_mb_bad, 48.0)

        # will_encode=False 일 때는 변화량 0.0 보장
        after_mb_pass, delta_mb_pass = VideoEvaluator.estimate_size_change_mb(
            vid_good_kf, 2.0, QualityZone.OVER, is_keyframe_defective=False, will_encode=False, thresholds=th
        )
        self.assertEqual(after_mb_pass, 100.0)
        self.assertEqual(delta_mb_pass, 0.0)

    def test_estimate_size_change_optimal_and_low_zones(self) -> None:
        """적정 및 저화질 영상의 1초 키프레임 교정 시 발생하는 미세 용량 증가(3~6%) 및 음수 delta 표기를 검증"""
        vid_opt = VideoProps("test.mp4", 1280, 720, 0, 30.0, 1500, 128, 100.0, 10.0, "h264", keyframe_interval=5.0)
        after_mb, delta_mb = VideoEvaluator.estimate_size_change_mb(
            vid_opt, 1.0, QualityZone.OPTIMAL, is_keyframe_defective=True, will_encode=True, thresholds=self.cfg.thresholds
        )
        self.assertGreater(after_mb, 100.0)
        self.assertLess(delta_mb, 0.0)
        self.assertAlmostEqual(after_mb - 100.0, -delta_mb)

    def test_efficiency_filter_low_savings_time_roi_and_exempt(self) -> None:
        """절감 효율 필터의 최소 절감량·분당 절감량·큰 절감 예외·KF 불량 우선 규칙을 검증"""
        cfg_eff = ClassifierConfig(
            efficiency=EfficiencyConfig(
                min_saved_mb=10.0,
                min_mb_per_min=5.0,
                exempt_saved_mb=50.0,
            )
        )

        # 1. 예상 절감량 < min_saved_mb (16MB -> 8MB saving < 10MB, duration=10s) -> PASS_LOW_SAVINGS
        vid_low_sav = VideoProps("low_sav.mp4", 1280, 720, 0, 30.0, 3000, 128, 16.0, 10.0, "h264", 1.0)
        ev_low_sav = VideoEvaluator.evaluate_video(vid_low_sav, cfg_eff)
        self.assertEqual(ev_low_sav.smart_action, SmartAction.PASS_LOW_SAVINGS)
        self.assertIsNone(ev_low_sav.target_dirname)
        self.assertEqual(ev_low_sav.expected_delta_mb, 0.0)

        # 2. 예상 절감량 >= min_saved_mb 이지만 분당 절감량 < min_mb_per_min
        # 40MB, ratio 2.0 => saving 20MB (>= 10MB), duration 600s (10분) => 2.0 MB/min (< 5.0) -> PASS_LOW_EFFICIENCY
        vid_slow = VideoProps("slow.mp4", 1280, 720, 0, 30.0, 3000, 128, 40.0, 600.0, "h264", 1.0)
        ev_slow = VideoEvaluator.evaluate_video(vid_slow, cfg_eff)
        self.assertEqual(ev_slow.smart_action, SmartAction.PASS_LOW_EFFICIENCY)
        self.assertIsNone(ev_slow.target_dirname)
        self.assertEqual(ev_slow.expected_delta_mb, 0.0)

        # 3. 분당 절감량이 낮아도 예상 절감량 >= exempt_saved_mb 이면 예외 통과
        # 120MB, ratio 2.0 => saving 60MB (>= 50MB), duration 1800s (30분) => 2.0 MB/min -> ENCODE_CQ22_OVER
        vid_exempt = VideoProps("exempt.mp4", 1280, 720, 0, 30.0, 3000, 128, 120.0, 1800.0, "h264", 1.0)
        ev_exempt = VideoEvaluator.evaluate_video(vid_exempt, cfg_eff)
        self.assertEqual(ev_exempt.smart_action, SmartAction.ENCODE_CQ22_OVER)
        self.assertEqual(ev_exempt.target_dirname, cfg_eff.dir_standard_cq22)

        # 4. exempt_saved_mb == 0.0 이면 큰 절감 예외 비활성 -> PASS_LOW_EFFICIENCY
        cfg_no_exempt = ClassifierConfig(
            efficiency=EfficiencyConfig(
                min_saved_mb=10.0,
                min_mb_per_min=5.0,
                exempt_saved_mb=0.0,
            )
        )
        ev_no_exempt = VideoEvaluator.evaluate_video(vid_exempt, cfg_no_exempt)
        self.assertEqual(ev_no_exempt.smart_action, SmartAction.PASS_LOW_EFFICIENCY)
        self.assertIsNone(ev_no_exempt.target_dirname)

        # 5. 키프레임 불량 + 과비트레이트면 절감 미미·시간 비효율과 무관하게 인코딩 대상 유지
        vid_bad_kf = VideoProps("bad_kf.mp4", 1280, 720, 0, 30.0, 3000, 128, 10.0, 600.0, "h264", 5.0)
        ev_bad_kf = VideoEvaluator.evaluate_video(vid_bad_kf, cfg_eff)
        self.assertEqual(ev_bad_kf.smart_action, SmartAction.ENCODE_CQ22_OVER)
        self.assertEqual(ev_bad_kf.target_dirname, cfg_eff.dir_standard_cq22)

    def test_evaluate_all_efficiency_summary_and_empty_guard(self) -> None:
        """evaluate_all의 빈 목록 안전 가드와 EfficiencySummary·작업 시간 집계를 검증"""
        empty_report = VideoEvaluator.evaluate_all("C:/empty", [], self.cfg)
        self.assertEqual(empty_report.total_count, 0)
        self.assertEqual(empty_report.encode_avg_mb_per_min, 0.0)
        self.assertEqual(empty_report.efficiency_summary.encode_duration_sec, 0.0)
        self.assertEqual(empty_report.efficiency_summary.skipped_over_count, 0)

        cfg_eff = ClassifierConfig(
            efficiency=EfficiencyConfig(min_saved_mb=5.0, min_mb_per_min=3.0, exempt_saved_mb=50.0)
        )
        vids = [
            # encode: 100MB -> 50MB (+50MB), duration 60s
            VideoProps("enc.mp4", 1280, 720, 0, 30.0, 3000, 128, 100.0, 60.0, "h264", 1.0),
            # low_savings: 6MB -> 3MB (+3MB < 5MB), duration 30s
            VideoProps("small.mp4", 1280, 720, 0, 30.0, 3000, 128, 6.0, 30.0, "h264", 1.0),
            # low_eff: 20MB -> 10MB (+10MB >= 5MB), duration 600s (1.0 MB/min < 3.0)
            VideoProps("slow.mp4", 1280, 720, 0, 30.0, 3000, 128, 20.0, 600.0, "h264", 1.0),
            # optimal pass
            VideoProps("opt.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 60.0, "h264", 1.0),
        ]
        rep = VideoEvaluator.evaluate_all("C:/archive", vids, cfg_eff)
        eff = rep.efficiency_summary
        self.assertEqual(rep.encoded_count, 1)
        self.assertEqual(rep.passed_count, 3)
        self.assertAlmostEqual(rep.net_saved_mb, 50.0)
        self.assertAlmostEqual(eff.encode_duration_sec, 60.0)
        self.assertAlmostEqual(rep.encode_avg_mb_per_min, 50.0)
        self.assertEqual(eff.low_savings_count, 1)
        self.assertEqual(eff.low_efficiency_count, 1)
        self.assertEqual(eff.skipped_over_count, 2)
        self.assertAlmostEqual(eff.skipped_over_duration_sec, 630.0)
        self.assertAlmostEqual(eff.skipped_over_potential_saved_mb, 13.0)

    def test_evaluate_video_decision_matrix_all_actions(self) -> None:
        """화질 3구간과 키프레임 상태의 조합에 따른 6가지 SmartAction 및 타겟 폴더 할당을 검증"""
        # 1. 과화질
        v_over = VideoProps("o.mp4", 1280, 720, 0, 30.0, 2400, 128, 50.0, 10.0, "h264", 1.0)
        ev_over = VideoEvaluator.evaluate_video(v_over, self.cfg)
        self.assertEqual(ev_over.smart_action, SmartAction.ENCODE_CQ22_OVER)
        self.assertEqual(ev_over.target_dirname, self.cfg.dir_standard_cq22)

        # 2. 적정화질 + 정상 KF
        v_opt_good = VideoProps("og.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 10.0, "h264", 1.0)
        ev_opt_good = VideoEvaluator.evaluate_video(v_opt_good, self.cfg)
        self.assertEqual(ev_opt_good.smart_action, SmartAction.PASS_OPTIMAL)
        self.assertIsNone(ev_opt_good.target_dirname)

        # 3. 적정화질 + 불량 KF
        v_opt_bad = VideoProps("ob.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 10.0, "h264", 5.0)
        ev_opt_bad = VideoEvaluator.evaluate_video(v_opt_bad, self.cfg)
        self.assertEqual(ev_opt_bad.smart_action, SmartAction.ENCODE_CQ22_OPT_KF)
        self.assertEqual(ev_opt_bad.target_dirname, self.cfg.dir_standard_cq22)

        # 4. 저화질 + 정상 KF
        v_low_good = VideoProps("lg.mp4", 1280, 720, 0, 30.0, 800, 128, 50.0, 10.0, "h264", 1.0)
        ev_low_good = VideoEvaluator.evaluate_video(v_low_good, self.cfg)
        self.assertEqual(ev_low_good.smart_action, SmartAction.PASS_LOW)
        self.assertIsNone(ev_low_good.target_dirname)

        # 5. 저화질 + 불량 KF
        v_low_bad = VideoProps("lb.mp4", 1280, 720, 0, 30.0, 800, 128, 50.0, 10.0, "h264", 5.0)
        ev_low_bad = VideoEvaluator.evaluate_video(v_low_bad, self.cfg)
        self.assertEqual(ev_low_bad.smart_action, SmartAction.ENCODE_LOW_KF)
        self.assertEqual(ev_low_bad.target_dirname, self.cfg.dir_low_quality_keyframe)

    def test_evaluate_video_exception_rules(self) -> None:
        """사용자 정의 예외 규칙이 등록된 경우 화질 등급에 관계없이 PASS_EXCEPTION 및 target_dirname=None이 보장되는지 검증"""
        vid_over = VideoProps("vip_archive.mp4", 1280, 720, 0, 30.0, 6000, 128, 500.0, 10.0, "h264", 5.0)

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
        self.assertEqual(rep_empty.encoded_count, 0)
        self.assertEqual(rep_empty.passed_count, 0)
        self.assertEqual(rep_empty.net_saved_mb, 0.0)

        videos = [
            VideoProps("over.mp4", 1280, 720, 0, 30.0, 3000, 128, 100.0, 10.0, "h264", 1.0),
            VideoProps("opt_bad_kf.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 10.0, "h264", 5.0),
        ]
        rep = VideoEvaluator.evaluate_all("C:/test", videos, self.cfg)
        self.assertEqual(rep.total_count, 2)
        self.assertEqual(rep.encoded_count, 2)
        self.assertEqual(rep.passed_count, 0)
        self.assertGreater(rep.gross_saved_mb, 0.0)
        self.assertGreater(rep.keyframe_increase_mb, 0.0)
        self.assertAlmostEqual(rep.net_saved_mb, rep.gross_saved_mb - rep.keyframe_increase_mb)

    def test_custom_classification_policy_injection_and_badge_tones(self) -> None:
        """우선순위 정책 체인 주입으로 VideoEvaluator 수정 없이 판정 확장 및 SmartAction 배지 범주 일관성 검증"""
        self.assertEqual(SmartAction.ENCODE_CQ22_OVER.badge_tone, ActionBadgeTone.STD_ENCODE)
        self.assertEqual(SmartAction.ENCODE_LOW_KF.badge_tone, ActionBadgeTone.LOW_KF_ENCODE)
        self.assertEqual(SmartAction.PASS_OPTIMAL.badge_tone, ActionBadgeTone.PASS)

        vid_over = VideoProps("custom.mp4", 1280, 720, 0, 30.0, 4000, 128, 100.0, 10.0, "h264", 1.0)

        def force_archive_policy(ctx: EvaluationContext) -> ClassificationDecision | None:
            if ctx.video.filename.startswith("custom"):
                return ClassificationDecision(
                    smart_action=SmartAction.PASS_EXCEPTION,
                    target_dirname=None,
                    size_estimate=(ctx.video.vid_size_MB, 0.0),
                )
            return None

        rep = VideoEvaluator.evaluate_all(
            "C:/test",
            [vid_over],
            self.cfg,
            policies=(force_archive_policy, *DEFAULT_CLASSIFICATION_POLICIES),
        )
        self.assertEqual(rep.total_count, 1)
        self.assertEqual(rep.passed_count, 1)
        self.assertEqual(rep.evaluations[0].smart_action, SmartAction.PASS_EXCEPTION)
        self.assertEqual(rep.evaluations[0].expected_delta_mb, 0.0)

    def test_compare_natural_order_three_way_and_equivalence(self) -> None:
        """compare_natural_order의 단일 패스 3방향 비교(-1·0·1) 및 compare_natural_tokens 일관성 검증"""
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
