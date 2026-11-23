"""화질 분석 순수 함수(검은 띠 합집합, 왕복 명령, 축소 후보 판정) 검증"""

import unittest

from src.core.analysis.frame_analysis import (
    ACTIVE_AREA_NO_BARS_RATIO,
    CROP_DETECT_LIMIT,
    CROP_DETECT_ROUND,
    FRAME_ANALYSIS_VERSION,
    ROUND_POINT_INDICES,
    ROUNDTRIP_SSIM_THRESHOLD,
    ROUNDTRIP_VMAF_THRESHOLD,
    SSIM_CONFIRMED_FAIL_SCORE,
    SSIM_CONFIRMED_PASS_SCORE,
    VMAF_CONFIRMED_FAIL_SCORE,
    VMAF_CONFIRMED_PASS_SCORE,
    VR_ASPECT_RATIO_MAX,
    VR_ASPECT_RATIO_MIN,
    VR_MIN_LONG_SIDE,
    ActiveRegion,
    CropBox,
    TierScore,
    analysis_version_for_mode,
    build_roundtrip_command,
    candidate_tiers_for,
    choose_round_points,
    frame_analysis_params,
    is_vr_protected,
    merge_crop_union,
    next_round_tier_keys,
    parse_cropdetect_boxes,
    score_passes,
    select_candidate_tier,
    ssim_stage_confirmed,
    tier_scores_from_json,
    tier_scores_to_json,
)
from src.core.models import ActiveFrame
from src.core.quality_probe import VMAF_THREADS
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY

_CROP_LINE: str = (
    "[Parsed_cropdetect_0 @ 0x1] x1:1312 x2:2527 y1:0 y2:2159 w:1216 h:2160 x:1312 y:0 "
    "pts:12345 pts_time:1.234 limit:24 round:2 reset:0 skip:0 crop=1216:2160:1312:0\n"
)


class TestApprovedConstants(unittest.TestCase):
    """12.17절 승인 확정값과 측정 규칙 버전이 고정값인지 검증"""

    def test_constants_match_approved_values(self) -> None:
        self.assertEqual(ACTIVE_AREA_NO_BARS_RATIO, 0.90)
        self.assertEqual(VR_ASPECT_RATIO_MIN, 1.95)
        self.assertEqual(VR_ASPECT_RATIO_MAX, 2.05)
        self.assertEqual(VR_MIN_LONG_SIDE, 2560)
        self.assertEqual(ROUNDTRIP_VMAF_THRESHOLD, 98.0)
        self.assertEqual(ROUNDTRIP_SSIM_THRESHOLD, 0.996)
        self.assertEqual(CROP_DETECT_LIMIT, 24)
        self.assertEqual(CROP_DETECT_ROUND, 2)
        self.assertEqual(FRAME_ANALYSIS_VERSION, 202)
        self.assertEqual(analysis_version_for_mode("fast"), 201)
        self.assertEqual(analysis_version_for_mode("balanced"), 202)
        self.assertEqual(analysis_version_for_mode("precise"), 203)
        self.assertEqual(analysis_version_for_mode("unknown"), 202)
        self.assertEqual(SSIM_CONFIRMED_PASS_SCORE, 0.998)
        self.assertEqual(SSIM_CONFIRMED_FAIL_SCORE, 0.995)
        self.assertEqual(VMAF_CONFIRMED_PASS_SCORE, 99.5)
        self.assertEqual(VMAF_CONFIRMED_FAIL_SCORE, 96.5)
        fast = frame_analysis_params("fast")
        self.assertEqual((fast.segment_duration_sec, fast.fast_keyframe_seek), (0.5, True))
        precise = frame_analysis_params("precise")
        self.assertEqual((precise.segment_duration_sec, precise.fast_keyframe_seek), (2.0, False))


class TestActiveRegion(unittest.TestCase):
    """활성 화면 값과 판정 입력 변환, VR 보호 판정 검증"""

    def test_to_active_frame_returns_size_only(self) -> None:
        region = ActiveRegion(width=720, height=1080, x=600, y=0, has_black_bars=True)
        self.assertEqual(region.to_active_frame(), ActiveFrame(width=720, height=1080))

    def test_vr_protected_by_metadata_or_manual_flag(self) -> None:
        self.assertTrue(is_vr_protected(1920, 1080, has_vr_metadata=True))
        self.assertTrue(is_vr_protected(1920, 1080, manual_vr=True))
        self.assertTrue(is_vr_protected(0, 0, has_vr_metadata=True))

    def test_vr_inferred_from_two_to_one_aspect_and_long_side(self) -> None:
        self.assertTrue(is_vr_protected(3840, 1920))
        self.assertTrue(is_vr_protected(2560, 1280))
        self.assertFalse(is_vr_protected(1920, 960))
        self.assertFalse(is_vr_protected(3840, 1600))
        self.assertFalse(is_vr_protected(0, 0))


class TestCropDetection(unittest.TestCase):
    """cropdetect 출력 파싱과 합집합 확정 검증"""

    def test_parse_boxes_collects_all_lines_in_order(self) -> None:
        boxes = parse_cropdetect_boxes(_CROP_LINE + "[Parsed_cropdetect_0 @ 0x2] crop=720:1080:600:0\n")
        self.assertEqual(boxes, [CropBox(1216, 2160, 1312, 0), CropBox(720, 1080, 600, 0)])
        self.assertEqual(parse_cropdetect_boxes("no crop here"), [])

    def test_no_boxes_returns_full_frame(self) -> None:
        region = merge_crop_union([], 1920, 1080)
        self.assertEqual(region, ActiveRegion(1920, 1080, 0, 0, False))

    def test_vr_forces_full_frame(self) -> None:
        region = merge_crop_union([CropBox(720, 1080, 600, 0)], 1920, 1080, is_vr=True)
        self.assertEqual(region, ActiveRegion(1920, 1080, 0, 0, False))

    def test_union_uses_outer_edges(self) -> None:
        boxes = [CropBox(100, 100, 0, 0), CropBox(100, 100, 150, 150)]
        region = merge_crop_union(boxes, 400, 400)
        self.assertEqual(region, ActiveRegion(250, 250, 0, 0, True))

    def test_union_aligns_every_value_to_even(self) -> None:
        region = merge_crop_union([CropBox(99, 99, 51, 51)], 400, 400)
        self.assertEqual(region, ActiveRegion(100, 100, 50, 50, True))

    def test_union_clamps_to_frame_bounds(self) -> None:
        region = merge_crop_union([CropBox(500, 500, 100, 100)], 200, 200)
        self.assertEqual(region, ActiveRegion(100, 100, 100, 100, True))

    def test_union_over_ninety_percent_means_no_black_bars(self) -> None:
        over = merge_crop_union([CropBox(96, 96, 0, 0)], 100, 100)
        under = merge_crop_union([CropBox(94, 94, 0, 0)], 100, 100)
        self.assertEqual(over, ActiveRegion(100, 100, 0, 0, False))
        self.assertEqual(under, ActiveRegion(94, 94, 0, 0, True))

    def test_zero_sized_frame_or_empty_union_returns_full_frame(self) -> None:
        self.assertEqual(merge_crop_union([CropBox(10, 10, 0, 0)], 0, 0), ActiveRegion(0, 0, 0, 0, False))
        self.assertEqual(
            merge_crop_union([CropBox(0, 0, 0, 0)], 200, 200), ActiveRegion(200, 200, 0, 0, False)
        )


class TestAdaptiveSampling(unittest.TestCase):
    """적응형 라운드 지점 선택과 SSIM 조기 확정, 다음 라운드 규격 선별 검증"""

    def test_round_order_accumulates_one_three_five_nine_points(self) -> None:
        valid = set(range(9))
        used: set[int] = set()
        counts: list[int] = []
        for group in ROUND_POINT_INDICES:
            chosen = choose_round_points(group, valid, used)
            counts.append(len(used | set(chosen)))
            used.update(chosen)
        self.assertEqual(counts, [1, 3, 5, 9])

    def test_invalid_or_used_point_is_replaced_by_nearest_valid_point(self) -> None:
        valid = {0, 1, 2, 7, 8}
        self.assertEqual(choose_round_points((4,), valid, set()), (2,))
        self.assertEqual(choose_round_points((2, 6), valid, {2}), (1, 7))
        self.assertEqual(choose_round_points((0,), valid, set(range(9))), ())

    def test_ssim_stage_confirms_only_with_both_margins(self) -> None:
        self.assertTrue(ssim_stage_confirmed([0.999, 0.998, 0.990]))
        self.assertFalse(ssim_stage_confirmed([0.997, 0.997, 0.990]))
        self.assertFalse(ssim_stage_confirmed([0.999, 0.998, 0.996]))
        self.assertTrue(ssim_stage_confirmed([0.999, 0.998]))
        self.assertTrue(ssim_stage_confirmed([0.990, 0.990]))
        self.assertTrue(ssim_stage_confirmed([0.991, 0.985]))
        self.assertFalse(ssim_stage_confirmed([0.9955, 0.990]))
        self.assertFalse(ssim_stage_confirmed([0.999, None, 0.990]))

    def test_next_round_keeps_unconfirmed_and_boundary_tiers(self) -> None:
        tiers = candidate_tiers_for(3840, 2160)
        medians = {
            "1440p": 0.9995,
            "1080p": 0.997,
            "900p": 0.990,
            "720p": 0.9999,
            "540p": 0.9999,
            "480p": 0.9999,
            "360p": 0.9999,
        }
        self.assertEqual(next_round_tier_keys(tiers, medians), ("1080p", "900p"))
        self.assertEqual(
            next_round_tier_keys(tiers, {tier.key: 0.9999 for tier in tiers}),
            ("360p",),
        )


class TestCandidateTiers(unittest.TestCase):
    """활성 화면 기준 축소 후보 규격 목록 검증"""

    def test_four_k_returns_all_downscale_tiers_descending(self) -> None:
        keys = tuple(tier.key for tier in candidate_tiers_for(3840, 2160))
        self.assertEqual(keys, ("1440p", "1080p", "900p", "720p", "540p", "480p", "360p"))

    def test_1080p_starts_at_900p(self) -> None:
        keys = tuple(tier.key for tier in candidate_tiers_for(1920, 1080))
        self.assertEqual(keys[0], "900p")
        self.assertEqual(keys[-1], "360p")

    def test_small_frame_has_no_candidate(self) -> None:
        self.assertEqual(candidate_tiers_for(320, 240), ())

    def test_portrait_frame_uses_short_and_long_side(self) -> None:
        tiers = candidate_tiers_for(2160, 3840)
        self.assertTrue(tiers)
        self.assertGreater(tiers[0].short_side, tiers[-1].short_side)
        self.assertEqual(RESOLUTION_TIER_BY_KEY[tiers[0].key].short_side, 1440)


class TestCommandBuilders(unittest.TestCase):
    """FFmpeg 왕복 지표 명령 구성 검증"""

    def test_roundtrip_crops_active_region_before_split(self) -> None:
        region = ActiveRegion(720, 1080, 600, 0, True)
        args = build_roundtrip_command(
            ffmpeg_executable="ffmpeg",
            source_path="movie.mp4",
            start_sec=0.0,
            duration_sec=2.0,
            active_region=region,
            tier=RESOLUTION_TIER_BY_KEY["540p"],
            metric="ssim",
            model="vmaf_v0.6.1",
            log_name="metric_ssim.log",
        )
        chain = args[args.index("-filter_complex") + 1]
        self.assertTrue(chain.startswith("[0:v]crop=720:1080:600:0,split=2[ref][dist];"))
        self.assertIn("[dist]scale=540:808:flags=bicubic,scale=720:1080:flags=bicubic[sc]", chain)
        self.assertTrue(chain.endswith("[sc][ref]ssim=stats_file=metric_ssim.log"))

    def test_roundtrip_without_black_bars_keeps_full_input(self) -> None:
        region = ActiveRegion(3840, 2160, 0, 0, False)
        args = build_roundtrip_command(
            ffmpeg_executable="ffmpeg",
            source_path="movie.mp4",
            start_sec=0.0,
            duration_sec=2.0,
            active_region=region,
            tier=RESOLUTION_TIER_BY_KEY["1080p"],
            metric="vmaf",
            model="vmaf_4k_v0.6.1",
            log_name="metric_vmaf.json",
        )
        chain = args[args.index("-filter_complex") + 1]
        self.assertTrue(chain.startswith("[0:v]split=2[ref][dist];"))
        self.assertNotIn("crop=", chain)
        self.assertIn(f"[sc][ref]libvmaf=model=version=vmaf_4k_v0.6.1:n_threads={VMAF_THREADS}", chain)
        self.assertIn("log_path=metric_vmaf.json:log_fmt=json", chain)

    def test_roundtrip_adds_fast_seek_and_vmaf_subsample(self) -> None:
        region = ActiveRegion(1920, 1080, 0, 0, False)
        args = build_roundtrip_command(
            ffmpeg_executable="ffmpeg",
            source_path="movie.mp4",
            start_sec=1.0,
            duration_sec=1.0,
            active_region=region,
            tier=RESOLUTION_TIER_BY_KEY["720p"],
            metric="vmaf",
            model="vmaf_v0.6.1",
            log_name="vmaf.json",
            fast_keyframe_seek=True,
            n_subsample=2,
        )
        self.assertIn("-noaccurate_seek", args)
        chain = args[args.index("-filter_complex") + 1]
        self.assertIn("n_subsample=2", chain)


class TestScoring(unittest.TestCase):
    """SSIM 선행 필터와 축소 후보 선택, 저장 형식 검증"""

    def test_score_passes_requires_ssim_and_vmaf(self) -> None:
        self.assertTrue(score_passes(TierScore("1080p", 0.996, 98.0), use_vmaf=True))
        self.assertFalse(score_passes(TierScore("1080p", 0.996, 97.9), use_vmaf=True))
        self.assertFalse(score_passes(TierScore("1080p", 0.9959, 99.0), use_vmaf=True))
        self.assertTrue(score_passes(TierScore("1080p", 0.996, None), use_vmaf=False))
        self.assertFalse(score_passes(TierScore("1080p", None, 99.0), use_vmaf=False))

    def test_select_candidate_returns_lowest_consecutive_pass(self) -> None:
        scores = [
            TierScore("1440p", 0.999, 99.0),
            TierScore("1080p", 0.998, 98.5),
            TierScore("900p", 0.990, 95.0),
            TierScore("720p", 0.999, 99.0),
        ]
        self.assertEqual(select_candidate_tier(scores, use_vmaf=True), "1080p")
        self.assertIsNone(select_candidate_tier([TierScore("1440p", 0.990, 99.0)], use_vmaf=True))
        self.assertIsNone(select_candidate_tier([], use_vmaf=False))

    def test_tier_scores_json_roundtrip(self) -> None:
        scores = (TierScore("1440p", 0.9991, 99.25), TierScore("1080p", 0.9975, None))
        self.assertEqual(tier_scores_from_json(tier_scores_to_json(scores)), scores)

    def test_tier_scores_json_rejects_invalid_payloads(self) -> None:
        self.assertEqual(tier_scores_from_json("not json"), ())
        self.assertEqual(tier_scores_from_json('{"tier": "1080p"}'), ())
        text = '[{"tier": ""}, {"tier": "720p", "ssim": "x", "vmaf": NaN}, 7, {"tier": "480p"}]'
        self.assertEqual(tier_scores_from_json(text), (TierScore("720p", None, None), TierScore("480p")))


if __name__ == "__main__":
    unittest.main()
