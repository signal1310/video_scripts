import unittest

from src.core.config import ClassifierConfig
from src.core.evaluator import VideoEvaluator
from src.core.models import (
    FootageStrategy,
    QualityZone,
    SmartAction,
    VideoProps,
)


class TestEvaluatorModule(unittest.TestCase):
    """
    - 비트레이트 스케일링 수식, 화질 등급 경계값, 키프레임 판별, 소형 영상 분포 분석 등 코어 도메인 엔진의 무결성을 검증.
    """

    def setUp(self) -> None:
        self.cfg = ClassifierConfig.from_env()

    def test_optimal_bitrate_resolution_monotonicity(self) -> None:
        """
        - 360p부터 8K까지 해상도 증가에 따라 최적 비트레이트가 엄격히 단조 증가하는지 검증.
        """
        opt_720 = VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 30.0, "h264")
        self.assertEqual(opt_720, 1500)

        opt_360 = VideoEvaluator.calc_optimal_bitrate(640, 360, self.cfg, 30.0, "h264")
        opt_480 = VideoEvaluator.calc_optimal_bitrate(854, 480, self.cfg, 30.0, "h264")
        opt_1080 = VideoEvaluator.calc_optimal_bitrate(1920, 1080, self.cfg, 30.0, "h264")
        opt_1440 = VideoEvaluator.calc_optimal_bitrate(2560, 1440, self.cfg, 30.0, "h264")
        opt_4k = VideoEvaluator.calc_optimal_bitrate(3840, 2160, self.cfg, 30.0, "h264")
        opt_8k = VideoEvaluator.calc_optimal_bitrate(7680, 4320, self.cfg, 30.0, "h264")

        self.assertTrue(100 < opt_360 < opt_480 < opt_720 < opt_1080 < opt_1440 < opt_4k < opt_8k)

    def test_optimal_bitrate_extreme_dimensions(self) -> None:
        """
        - 16x16, 1x1, 0x0 등 극한의 초소형 해상도 입력 시에도 0으로 나누기 오류 없이 최소 하한선(100kbps)을 유지하는지 검증.
        """
        opt_tiny = VideoEvaluator.calc_optimal_bitrate(16, 16, self.cfg, 30.0, "h264")
        self.assertEqual(opt_tiny, 100)

        opt_zero = VideoEvaluator.calc_optimal_bitrate(0, 0, self.cfg, 30.0, "h264")
        self.assertEqual(opt_zero, 100)

    def test_optimal_bitrate_fps_scaling(self) -> None:
        """
        - 30fps 이하는 1.0 가중치를 유지하고, 60fps 및 120fps는 비선형 지수(beta=0.60)에 따라 합리적으로 증가하는지 검증.
        """
        base = VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 30.0, "h264")
        opt_15 = VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 15.0, "h264")
        opt_24 = VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 24.0, "h264")
        opt_32 = VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 32.0, "h264")

        self.assertEqual(base, opt_15)
        self.assertEqual(base, opt_24)
        self.assertEqual(base, opt_32)

        opt_60 = VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 60.0, "h264")
        opt_120 = VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 120.0, "h264")

        expected_60 = round(1500 * ((60.0 / 30.0) ** 0.60))
        expected_120 = round(1500 * ((120.0 / 30.0) ** 0.60))

        self.assertEqual(opt_60, expected_60)
        self.assertEqual(opt_120, expected_120)
        self.assertTrue(base < opt_60 < opt_120)

    def test_optimal_bitrate_codec_efficiency_weights(self) -> None:
        """
        - 코덱별 효율 가중치(AV1=0.65, HEVC=0.80, H264=1.00, MPEG4=1.35, MPEG2=1.60, ProRes=2.50)와 미등록 코덱 기본값(1.00) 적용을 검증.
        """
        opt_av1 = VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 30.0, "av1")
        opt_hevc = VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 30.0, "hevc")
        opt_h264 = VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 30.0, "h264")
        opt_mpeg4 = VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 30.0, "mpeg4")
        opt_mpeg2 = VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 30.0, "mpeg2video")
        opt_prores = VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 30.0, "prores")

        self.assertEqual(opt_av1, 975)
        self.assertEqual(opt_hevc, 1200)
        self.assertEqual(opt_h264, 1500)
        self.assertEqual(opt_mpeg4, 1950)
        self.assertEqual(opt_mpeg2, 2400)
        self.assertEqual(opt_prores, 3750)

        # 대소문자 및 양끝 공백 무시
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 30.0, " HEVC "), 1200)

        # 미등록 코덱의 1.00 fallback
        self.assertEqual(VideoEvaluator.calc_optimal_bitrate(1280, 720, self.cfg, 30.0, "unknown_codec_xyz"), 1500)

    def test_classify_quality_zone_strict_boundaries(self) -> None:
        """
        - 화질 3구간 경계값(0.80 미만: LOW, 0.80~1.20: OPTIMAL, 1.20 초과: OVER)의 엄밀한 판정을 검증.
        """
        self.assertEqual(VideoEvaluator.classify_quality_zone(0.7999, self.cfg), QualityZone.LOW)
        self.assertEqual(VideoEvaluator.classify_quality_zone(0.8000, self.cfg), QualityZone.OPTIMAL)
        self.assertEqual(VideoEvaluator.classify_quality_zone(1.0000, self.cfg), QualityZone.OPTIMAL)
        self.assertEqual(VideoEvaluator.classify_quality_zone(1.2000, self.cfg), QualityZone.OPTIMAL)
        self.assertEqual(VideoEvaluator.classify_quality_zone(1.2001, self.cfg), QualityZone.OVER)

    def test_is_keyframe_defective_boundaries(self) -> None:
        """
        - 키프레임 결함 판단 임계값(기본 2.2초) 경계 판정 및 None 방어를 검증.
        """
        vid_normal = VideoProps("test.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 10.0, "h264", keyframe_interval=2.2000)
        self.assertFalse(VideoEvaluator.is_keyframe_defective(vid_normal, self.cfg))

        vid_defective = VideoProps("test.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 10.0, "h264", keyframe_interval=2.2001)
        self.assertTrue(VideoEvaluator.is_keyframe_defective(vid_defective, self.cfg))

        vid_none = VideoProps("test.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 10.0, "h264", keyframe_interval=None)
        self.assertFalse(VideoEvaluator.is_keyframe_defective(vid_none, self.cfg))

    def test_estimate_size_change_over_zone(self) -> None:
        """
        - 과화질 구간 감축 시의 용량 변화 예측과 1초 키프레임 오버헤드 반영을 검증.
        """
        vid_good_kf = VideoProps("test.mp4", 1280, 720, 0, 30.0, 3000, 128, 100.0, 10.0, "h264", keyframe_interval=1.0)
        after_mb, delta_mb = VideoEvaluator.estimate_size_change_mb(
            vid_good_kf, 2.0, QualityZone.OVER, is_keyframe_defective=False, will_encode=True, config=self.cfg
        )
        self.assertAlmostEqual(after_mb, 50.0)
        self.assertAlmostEqual(delta_mb, 50.0)

        # 키프레임 불량 시 4% I-프레임 오버헤드 추가
        after_mb_bad, delta_mb_bad = VideoEvaluator.estimate_size_change_mb(
            vid_good_kf, 2.0, QualityZone.OVER, is_keyframe_defective=True, will_encode=True, config=self.cfg
        )
        self.assertAlmostEqual(after_mb_bad, 52.0)
        self.assertAlmostEqual(delta_mb_bad, 48.0)

        # will_encode=False 일 때는 변화량 0.0 보장
        after_mb_pass, delta_mb_pass = VideoEvaluator.estimate_size_change_mb(
            vid_good_kf, 2.0, QualityZone.OVER, is_keyframe_defective=False, will_encode=False, config=self.cfg
        )
        self.assertEqual(after_mb_pass, 100.0)
        self.assertEqual(delta_mb_pass, 0.0)

    def test_estimate_size_change_optimal_and_low_zones(self) -> None:
        """
        - 적정 및 저화질 영상의 1초 키프레임 교정 시 발생하는 미세 용량 증가(3~6%) 및 음수 delta 표기를 검증.
        """
        vid_opt = VideoProps("test.mp4", 1280, 720, 0, 30.0, 1500, 128, 100.0, 10.0, "h264", keyframe_interval=5.0)
        after_mb, delta_mb = VideoEvaluator.estimate_size_change_mb(
            vid_opt, 1.0, QualityZone.OPTIMAL, is_keyframe_defective=True, will_encode=True, config=self.cfg
        )
        self.assertGreater(after_mb, 100.0)
        self.assertLess(delta_mb, 0.0)
        self.assertAlmostEqual(after_mb - 100.0, -delta_mb)

    def test_small_footage_distribution_zero_division_guard(self) -> None:
        """
        - 분석 대상 영상 목록이 빈 리스트([])인 경우에도 ZeroDivisionError 없이 안전하게 기본 분포 결과를 반환하는지 검증.
        """
        dist = VideoEvaluator.analyze_small_footage_distribution([], self.cfg)
        self.assertEqual(dist.total_over_count, 0)
        self.assertEqual(dist.small_over_count, 0)
        self.assertEqual(dist.total_potential_saved_mb, 0.0)
        self.assertEqual(dist.small_potential_saved_mb, 0.0)
        self.assertEqual(dist.small_share_ratio, 0.0)
        self.assertEqual(dist.strategy, FootageStrategy.IGNORE_SMALL)

    def test_small_footage_distribution_thresholds(self) -> None:
        """
        - 절감 비중(15%) 또는 절감 용량(500MB) 초과 시 누적 최적화 모드로 전환되는지 검증.
        """
        vids_ratio = [
            VideoProps("big.mp4", 1280, 720, 0, 30.0, 3000, 128, 160.0, 10.0, "h264"),  # saved 80MB
            VideoProps("small.mp4", 1280, 720, 0, 30.0, 3000, 128, 25.0, 10.0, "h264"), # saved 12.5MB (small)
        ]
        dist_ratio = VideoEvaluator.analyze_small_footage_distribution(vids_ratio, self.cfg)
        # small share: 12.5 / 92.5 = 13.5% (< 15% -> IGNORE_SMALL)
        self.assertEqual(dist_ratio.strategy, FootageStrategy.IGNORE_SMALL)

        # 소형 영상 1개 추가하여 25MB 절감 -> 25 / 105 = 23.8% (>= 15% -> ACCUMULATE)
        vids_ratio.append(VideoProps("small2.mp4", 1280, 720, 0, 30.0, 3000, 128, 25.0, 10.0, "h264"))
        dist_accum = VideoEvaluator.analyze_small_footage_distribution(vids_ratio, self.cfg)
        self.assertEqual(dist_accum.strategy, FootageStrategy.ACCUMULATE)

        # 비중은 15% 미만이지만 소형 영상 총 절감 용량이 500MB 이상이면 누적 최적화로 전환
        huge_big = VideoProps("huge_big.mp4", 1280, 720, 0, 30.0, 3000, 128, 9000.0, 10.0, "h264")
        small_below_500 = [
            VideoProps(f"sb_{i}.mp4", 1280, 720, 0, 30.0, 15000, 128, 25.0, 10.0, "h264")
            for i in range(21)
        ]
        dist_below_500 = VideoEvaluator.analyze_small_footage_distribution(
            [huge_big, *small_below_500], self.cfg
        )
        self.assertEqual(dist_below_500.strategy, FootageStrategy.IGNORE_SMALL)

        small_above_500 = [
            VideoProps(f"sa_{i}.mp4", 1280, 720, 0, 30.0, 15000, 128, 25.0, 10.0, "h264")
            for i in range(23)
        ]
        dist_above_500 = VideoEvaluator.analyze_small_footage_distribution(
            [huge_big, *small_above_500], self.cfg
        )
        self.assertEqual(dist_above_500.strategy, FootageStrategy.ACCUMULATE)

    def test_evaluate_video_small_footage_keyframe_and_min_saved_cutoff(self) -> None:
        """
        - 소형 영상의 IGNORE_SMALL/ACCUMULATE 분기, 키프레임 불량 우선 인코딩, 최소 절감량 컷오프를 검증.
        """
        cfg_small = ClassifierConfig(small_footage_min_saved_mb=10.0)
        big_ref = VideoProps("big_ref.mp4", 1280, 720, 0, 30.0, 3000, 128, 160.0, 10.0, "h264", 1.0)
        small_below = VideoProps("small_below.mp4", 1280, 720, 0, 30.0, 3000, 128, 10.0, 10.0, "h264", 1.0)
        small_above = VideoProps("small_above.mp4", 1280, 720, 0, 30.0, 15000, 128, 25.0, 10.0, "h264", 1.0)
        small_bad_kf = VideoProps("small_bad_kf.mp4", 1280, 720, 0, 30.0, 3000, 128, 25.0, 10.0, "h264", 5.0)

        dist_ignore = VideoEvaluator.analyze_small_footage_distribution([big_ref, small_below], cfg_small)
        self.assertEqual(dist_ignore.strategy, FootageStrategy.IGNORE_SMALL)

        ev_big_ref = VideoEvaluator.evaluate_video(big_ref, cfg_small, dist_ignore)
        ev_small_ignored = VideoEvaluator.evaluate_video(small_above, cfg_small, dist_ignore)
        self.assertEqual(ev_small_ignored.smart_action, SmartAction.PASS_SMALL_FOOTAGE)
        self.assertIsNone(ev_small_ignored.target_dirname)

        # IGNORE_SMALL 전략이라도 키프레임이 불량한 소형 과비트레이트 영상은 표준 인코딩 대상으로 분류
        ev_small_bad_kf = VideoEvaluator.evaluate_video(small_bad_kf, cfg_small, dist_ignore)
        self.assertEqual(ev_small_bad_kf.smart_action, SmartAction.ENCODE_CQ22_OVER)
        self.assertEqual(ev_small_bad_kf.target_dirname, ev_big_ref.target_dirname)

        dist_accum = VideoEvaluator.analyze_small_footage_distribution(
            [small_below, small_above], cfg_small
        )
        self.assertEqual(dist_accum.strategy, FootageStrategy.ACCUMULATE)

        ev_accum_below = VideoEvaluator.evaluate_video(small_below, cfg_small, dist_accum)
        self.assertEqual(ev_accum_below.smart_action, SmartAction.PASS_SMALL_FOOTAGE)
        self.assertIsNone(ev_accum_below.target_dirname)

        ev_accum_above = VideoEvaluator.evaluate_video(small_above, cfg_small, dist_accum)
        self.assertEqual(ev_accum_above.smart_action, SmartAction.ENCODE_CQ22_OVER)
        self.assertEqual(ev_accum_above.target_dirname, ev_big_ref.target_dirname)

    def test_evaluate_video_decision_matrix_all_actions(self) -> None:
        """
        - 화질 3구간과 키프레임 상태의 조합에 따른 6가지 SmartAction 및 타겟 폴더 할당을 검증.
        """
        dist = VideoEvaluator.analyze_small_footage_distribution([], self.cfg)

        # 1. 과화질
        v_over = VideoProps("o.mp4", 1280, 720, 0, 30.0, 2400, 128, 50.0, 10.0, "h264", 1.0)
        ev_over = VideoEvaluator.evaluate_video(v_over, self.cfg, dist)
        self.assertEqual(ev_over.smart_action, SmartAction.ENCODE_CQ22_OVER)
        self.assertEqual(ev_over.target_dirname, self.cfg.dir_standard_cq22)

        # 2. 적정화질 + 정상 KF
        v_opt_good = VideoProps("og.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 10.0, "h264", 1.0)
        ev_opt_good = VideoEvaluator.evaluate_video(v_opt_good, self.cfg, dist)
        self.assertEqual(ev_opt_good.smart_action, SmartAction.PASS_OPTIMAL)
        self.assertIsNone(ev_opt_good.target_dirname)

        # 3. 적정화질 + 불량 KF
        v_opt_bad = VideoProps("ob.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 10.0, "h264", 5.0)
        ev_opt_bad = VideoEvaluator.evaluate_video(v_opt_bad, self.cfg, dist)
        self.assertEqual(ev_opt_bad.smart_action, SmartAction.ENCODE_CQ22_OPT_KF)
        self.assertEqual(ev_opt_bad.target_dirname, self.cfg.dir_standard_cq22)

        # 4. 저화질 + 정상 KF
        v_low_good = VideoProps("lg.mp4", 1280, 720, 0, 30.0, 800, 128, 50.0, 10.0, "h264", 1.0)
        ev_low_good = VideoEvaluator.evaluate_video(v_low_good, self.cfg, dist)
        self.assertEqual(ev_low_good.smart_action, SmartAction.PASS_LOW)
        self.assertIsNone(ev_low_good.target_dirname)

        # 5. 저화질 + 불량 KF
        v_low_bad = VideoProps("lb.mp4", 1280, 720, 0, 30.0, 800, 128, 50.0, 10.0, "h264", 5.0)
        ev_low_bad = VideoEvaluator.evaluate_video(v_low_bad, self.cfg, dist)
        self.assertEqual(ev_low_bad.smart_action, SmartAction.ENCODE_LOW_KF)
        self.assertEqual(ev_low_bad.target_dirname, self.cfg.dir_low_quality_keyframe)

    def test_evaluate_video_exception_rules(self) -> None:
        """
        - 사용자 정의 예외 규칙이 등록된 경우 화질 등급에 관계없이 PASS_EXCEPTION 및 target_dirname=None이 보장되는지 검증.
        """
        vid_over = VideoProps("vip_archive.mp4", 1280, 720, 0, 30.0, 6000, 128, 500.0, 10.0, "h264", 5.0)
        dist = VideoEvaluator.analyze_small_footage_distribution([vid_over], self.cfg)

        def rule(v: VideoProps) -> bool:
            return "vip" in v.filename

        ev = VideoEvaluator.evaluate_video(vid_over, self.cfg, dist, exception_rules=[rule])

        self.assertEqual(ev.smart_action, SmartAction.PASS_EXCEPTION)
        self.assertIsNone(ev.target_dirname)
        self.assertEqual(ev.expected_delta_mb, 0.0)

    def test_summary_report_aggregation_empty_and_normal(self) -> None:
        """
        - SummaryReport 집계 시 빈 디렉터리 및 다중 파일에서의 누적/순수 절감량 계산 정확성을 검증.
        """
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
