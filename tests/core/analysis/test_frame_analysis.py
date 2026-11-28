"""화질 분석 순수 함수(화면 여백 합집합, 왕복 명령, 축소 후보 판정) 검증"""

import unittest
from dataclasses import dataclass

from src.core.analysis.frame_analysis import (
    ACTIVE_AREA_NO_BARS_RATIO,
    CROP_SCAN_VERSION,
    DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    EDGE_SNAP_MIN_PX,
    EDGE_SNAP_RATIO,
    FRAME_ANALYSIS_VERSION,
    MANUAL_FRAME_MIN_SIZE_PX,
    MARGIN_VARY_EXTRA_RATIO,
    MARGIN_VARY_MIN_POINTS,
    METRIC_CROP_ONLY,
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
    EdgeDarkness,
    FrameAnalysisThresholds,
    TierScore,
    analysis_version_for_mode,
    apply_no_bars_ratio,
    build_roundtrip_command,
    can_reuse_tier_scores,
    candidate_tiers_for,
    choose_round_points,
    count_failed_crop_scans,
    frame_analysis_params,
    frame_thresholds_from_config,
    is_crop_only_result,
    is_failed_crop_scan,
    is_usable_analysis_version,
    is_vr_protected,
    margins_vary,
    merge_crop_union,
    next_round_tier_keys,
    normalize_manual_active_frame,
    rejudge_candidate_tier,
    resolve_crop_frame,
    score_passes,
    select_candidate_tier,
    ssim_stage_confirmed,
    tier_scores_from_json,
    tier_scores_to_json,
)
from src.core.analysis.hdr_tone_map import tonemap_filter_chain
from src.core.config import ClassifierConfig
from src.core.models import ActiveFrame
from src.core.quality_probe import VMAF_THREADS
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY, SCALE_ALGORITHM_VALUES


def _all_mode_versions() -> list[int]:
    """화질 분석 방식과 보간 방식 모든 조합의 캐시 버전"""
    return [
        analysis_version_for_mode(mode, algorithm)
        for mode in ("fast", "balanced", "precise")
        for algorithm in SCALE_ALGORITHM_VALUES
    ]


class TestApprovedConstants(unittest.TestCase):
    """12.17절 승인 확정값과 측정 규칙 버전이 고정값인지 검증"""

    def test_constants_match_approved_values(self) -> None:
        self.assertEqual(ACTIVE_AREA_NO_BARS_RATIO, 0.90)
        self.assertEqual(VR_ASPECT_RATIO_MIN, 1.95)
        self.assertEqual(VR_ASPECT_RATIO_MAX, 2.05)
        self.assertEqual(VR_MIN_LONG_SIDE, 2560)
        self.assertEqual(ROUNDTRIP_VMAF_THRESHOLD, 98.0)
        self.assertEqual(ROUNDTRIP_SSIM_THRESHOLD, 0.996)
        self.assertEqual(EDGE_SNAP_RATIO, 0.02)
        self.assertEqual(EDGE_SNAP_MIN_PX, 8)
        self.assertEqual(MANUAL_FRAME_MIN_SIZE_PX, 32)
        self.assertEqual(FRAME_ANALYSIS_VERSION, 558)
        self.assertEqual(analysis_version_for_mode("fast"), 557)
        self.assertEqual(analysis_version_for_mode("balanced"), 558)
        self.assertEqual(analysis_version_for_mode("precise"), 559)
        self.assertEqual(analysis_version_for_mode("fast", "lanczos"), 627)
        self.assertEqual(analysis_version_for_mode("precise", "spline"), 639)
        self.assertNotIn(CROP_SCAN_VERSION, _all_mode_versions())
        self.assertNotIn(337, _all_mode_versions())
        self.assertEqual(analysis_version_for_mode("unknown"), 558)
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

    def test_to_active_frame_keeps_size_and_offset(self) -> None:
        region = ActiveRegion(width=720, height=1080, x=600, y=0, has_black_bars=True)
        self.assertEqual(region.to_active_frame(), ActiveFrame(width=720, height=1080, x=600, y=0))

    def test_margins_vary_defaults_to_false_and_keeps_frame_conversion(self) -> None:
        """새 필드는 기본값을 가져 기존 생성자 호출이 그대로 유효함"""
        region = ActiveRegion(720, 1080, 600, 0, True)
        self.assertFalse(region.margins_vary)
        self.assertFalse(region.dark_outliers_ignored)
        self.assertEqual(region.to_active_frame(), ActiveFrame(width=720, height=1080, x=600, y=0))

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
    """검출 상자 합집합 확정 검증"""

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
        over = merge_crop_union([CropBox(380, 380, 0, 0)], 400, 400)
        under = merge_crop_union([CropBox(360, 360, 0, 0)], 400, 400)
        self.assertEqual(over, ActiveRegion(400, 400, 0, 0, False))
        self.assertEqual(under, ActiveRegion(360, 360, 0, 0, True))

    def test_zero_sized_frame_or_empty_union_returns_full_frame(self) -> None:
        self.assertEqual(merge_crop_union([CropBox(10, 10, 0, 0)], 0, 0), ActiveRegion(0, 0, 0, 0, False))
        self.assertEqual(
            merge_crop_union([CropBox(0, 0, 0, 0)], 200, 200), ActiveRegion(200, 200, 0, 0, False)
        )


    def test_full_frame_fallback_does_not_flag_ignored_outliers(self) -> None:
        """어두운 근거로 지점을 무시했더라도 전체 프레임으로 되돌리면 안내 플래그를 세우지 않음"""
        boxes = [CropBox(320, 228, 0, 12)] * 7 + [CropBox(320, 240, 0, 0)] * 2
        darkness = [EdgeDarkness(12, 12, 0, 0)] * 7 + [EdgeDarkness(240, 0, 0, 0)] * 2
        region = merge_crop_union(boxes, 320, 240, darkness=darkness)
        self.assertEqual(region, ActiveRegion(320, 240, 0, 0, False))
        self.assertFalse(region.dark_outliers_ignored)


class TestCropUnionEdges(unittest.TestCase):
    """4면 독립 외곽 합집합, 면별 스냅, 짝수 정렬과 비대칭 오프셋 보존 검증"""

    def test_asymmetric_margins_keep_the_offset(self) -> None:
        region = merge_crop_union([CropBox(1600, 1000, 260, 40)], 1920, 1080)
        self.assertEqual(region, ActiveRegion(1600, 1000, 260, 40, True))

    def test_single_side_bar_is_cropped_on_that_side_only(self) -> None:
        region = merge_crop_union([CropBox(1920, 880, 0, 100)], 1920, 1080)
        self.assertEqual(region, ActiveRegion(1920, 880, 0, 100, True))

    def test_four_side_windowbox_removes_every_edge(self) -> None:
        region = merge_crop_union([CropBox(1700, 900, 110, 90)], 1920, 1080)
        self.assertEqual(region, ActiveRegion(1700, 900, 110, 90, True))

    def test_variable_thickness_keeps_the_widest_outer_union(self) -> None:
        boxes = [CropBox(720, 1280, 180, 0), CropBox(800, 1280, 140, 0)]
        region = merge_crop_union(boxes, 1080, 1280)
        self.assertEqual(region, ActiveRegion(800, 1280, 140, 0, True))

    def test_margin_below_snap_threshold_goes_to_the_frame_edge(self) -> None:
        region = merge_crop_union([CropBox(1400, 1000, 260, 20)], 1920, 1080)
        self.assertEqual(region, ActiveRegion(1400, 1020, 260, 0, True))

    def test_snap_threshold_uses_eight_pixels_on_small_frames(self) -> None:
        kept = merge_crop_union([CropBox(180, 80, 10, 10)], 200, 100)
        snapped = merge_crop_union([CropBox(188, 88, 6, 6)], 200, 100)
        self.assertEqual(kept, ActiveRegion(180, 80, 10, 10, True))
        self.assertEqual(snapped, ActiveRegion(200, 100, 0, 0, False))

    def test_snapped_union_aligns_start_and_size_to_even_pixels(self) -> None:
        region = merge_crop_union([CropBox(1441, 1001, 261, 41)], 1920, 1080)
        self.assertEqual(region, ActiveRegion(1442, 1002, 260, 40, True))

    def test_flat_faces_skip_the_dark_only_noise_snap(self) -> None:
        """평탄 규칙이 합의로 자른 면은 2% 노이즈 스냅을 건너뛰어 4K의 얇은 회색 여백도 남김"""
        width, height, margin = 3840, 2160, 40
        thresholds = FrameAnalysisThresholds(active_area_no_bars_ratio=0.99)
        boxes = [CropBox(width - margin, height, margin, 0)] * 9
        kept = merge_crop_union(boxes, width, height, thresholds=thresholds, flat_faces=("left",))
        snapped = merge_crop_union(boxes, width, height, thresholds=thresholds)
        self.assertEqual(kept, ActiveRegion(width - margin, height, margin, 0, True))
        self.assertEqual(snapped, ActiveRegion(width, height, 0, 0, False))

    def test_flat_face_names_only_protect_their_own_side(self) -> None:
        """합의한 면 이름에 없는 다른 면은 기존 어두운 노이즈 스냅을 그대로 씀"""
        width, height, margin = 3840, 2160, 40
        thresholds = FrameAnalysisThresholds(active_area_no_bars_ratio=0.99)
        boxes = [CropBox(width - margin, height, margin, 0)] * 9
        region = merge_crop_union(boxes, width, height, thresholds=thresholds, flat_faces=("top",))
        self.assertEqual(region, ActiveRegion(width, height, 0, 0, False))


class MarginVaryTests(unittest.TestCase):
    """샘플 지점별 화면 여백 차이 감지와 합집합 결과의 플래그 기록 검증"""

    frame_width: int = 1920
    frame_height: int = 1080

    def test_constant_margins_do_not_vary(self) -> None:
        boxes = [CropBox(1440, 1080, 240, 0)] * 6
        self.assertFalse(margins_vary(boxes, self.frame_width, self.frame_height))
        self.assertFalse(merge_crop_union(boxes, self.frame_width, self.frame_height).margins_vary)

    def test_two_clusters_with_enough_points_each_vary(self) -> None:
        boxes = [CropBox(1440, 1080, 240, 0)] * 3 + [CropBox(1920, 1080, 0, 0)] * 3
        self.assertTrue(margins_vary(boxes, self.frame_width, self.frame_height))

    def test_single_outlier_point_does_not_vary(self) -> None:
        """지점 하나만 튀면 차이의 한쪽에 지점이 모자라 참이 되지 않음"""
        boxes = [CropBox(1440, 1080, 240, 0)] * 5 + [CropBox(1920, 1080, 0, 0)]
        self.assertFalse(margins_vary(boxes, self.frame_width, self.frame_height))

    def test_black_or_invalid_points_are_ignored(self) -> None:
        """검은 화면(0 크기)과 프레임 밖 좌표는 비교에서 빠짐"""
        boxes = [CropBox(1440, 1080, 240, 0)] * 4 + [
            CropBox(0, 0, 0, 0),
            CropBox(1440, 1080, 240, 0),
            CropBox(1440, 1080, 5000, 0),
        ]
        self.assertFalse(margins_vary(boxes, self.frame_width, self.frame_height))

    def test_disagreement_below_tolerance_does_not_vary(self) -> None:
        """허용 오차(스냅 기준 + 축 길이의 5%) 이하 차이는 여백이 조금씩 다른 정도로 봄"""
        tolerance = (
            max(EDGE_SNAP_MIN_PX, round(self.frame_width * EDGE_SNAP_RATIO))
            + round(self.frame_width * MARGIN_VARY_EXTRA_RATIO)
        )
        inside = [CropBox(1440, 1080, 240, 0)] * 2 + [
            CropBox(1440 + tolerance, 1080, 240 - tolerance, 0)
        ] * 2
        outside = [CropBox(1440, 1080, 240, 0)] * 2 + [
            CropBox(1440 + tolerance + 2, 1080, 240 - tolerance - 2, 0)
        ] * 2
        self.assertFalse(margins_vary(inside, self.frame_width, self.frame_height))
        self.assertTrue(margins_vary(outside, self.frame_width, self.frame_height))

    def test_full_frame_and_pillarboxed_segments_vary(self) -> None:
        """편집 영상처럼 전체 화면 구간과 좌우 여백 구간이 섞이면 참이고 합집합은 전체 프레임"""
        boxes = [CropBox(1440, 1080, 240, 0)] * 4 + [CropBox(1920, 1080, 0, 0)] * 4
        self.assertTrue(margins_vary(boxes, self.frame_width, self.frame_height))
        region = merge_crop_union(boxes, self.frame_width, self.frame_height)
        self.assertEqual(region.width, self.frame_width)
        self.assertFalse(region.has_black_bars)
        self.assertTrue(region.margins_vary)

    def test_too_few_valid_points_do_not_vary(self) -> None:
        self.assertFalse(margins_vary([], self.frame_width, self.frame_height))
        self.assertFalse(
            margins_vary(
                [CropBox(1440, 1080, 240, 0), CropBox(1920, 1080, 0, 0)],
                self.frame_width,
                self.frame_height,
            )
        )
        self.assertFalse(margins_vary([CropBox(1440, 1080, 240, 0)], 0, 0))

    def test_min_points_constant_stays_two(self) -> None:
        self.assertEqual(MARGIN_VARY_MIN_POINTS, 2)
        self.assertEqual(MARGIN_VARY_EXTRA_RATIO, 0.05)


class TestResolveCropFrame(unittest.TestCase):
    """잘라내기 확정 조건별 활성 화면 반환 검증"""

    def setUp(self) -> None:
        self.region = ActiveRegion(1600, 1000, 260, 40, True)

    def test_crop_frame_is_confirmed_when_every_condition_allows(self) -> None:
        self.assertEqual(resolve_crop_frame(self.region), ActiveFrame(1600, 1000, 260, 40))

    def test_disabled_excluded_vr_or_no_bars_keeps_the_full_frame(self) -> None:
        self.assertIsNone(resolve_crop_frame(self.region, crop_enabled=False))
        self.assertIsNone(resolve_crop_frame(self.region, excluded=True))
        self.assertIsNone(resolve_crop_frame(self.region, is_vr_protected=True))
        self.assertIsNone(
            resolve_crop_frame(ActiveRegion(1920, 1080, 0, 0, False))
        )

    def test_manual_frame_beats_automatic_detection_and_exclusion(self) -> None:
        """직접 지정은 자동 감지 결과와 영상별 제외를 덮어씀"""
        manual = ActiveFrame(1280, 720, 0, 180)
        self.assertEqual(resolve_crop_frame(self.region, manual_frame=manual), manual)
        self.assertEqual(
            resolve_crop_frame(self.region, excluded=True, manual_frame=manual), manual
        )
        full_region = ActiveRegion(1920, 1080, 0, 0, False)
        self.assertEqual(resolve_crop_frame(full_region, manual_frame=manual), manual)

    def test_disabled_crop_keeps_the_manual_frame_but_vr_protection_wins(self) -> None:
        """전역 잘라내기 설정이 꺼져 있어도 직접 지정은 적용되고, VR 보호만 우선함"""
        manual = ActiveFrame(1280, 720, 0, 180)
        self.assertEqual(
            resolve_crop_frame(self.region, crop_enabled=False, manual_frame=manual), manual
        )
        self.assertIsNone(
            resolve_crop_frame(self.region, is_vr_protected=True, manual_frame=manual)
        )

    def test_manual_frame_equal_to_the_full_frame_keeps_the_full_frame(self) -> None:
        """전체 프레임과 같은 직접 지정은 잘라내지 않음"""
        full_region = ActiveRegion(1920, 1080, 0, 0, False)
        self.assertIsNone(
            resolve_crop_frame(full_region, manual_frame=ActiveFrame(1920, 1080))
        )
        self.assertEqual(
            resolve_crop_frame(self.region, manual_frame=ActiveFrame(1920, 1080)),
            ActiveFrame(1920, 1080),
        )


class TestNormalizeManualActiveFrame(unittest.TestCase):
    """사용자 지정 활성 화면의 프레임 안 맞춤과 짝수 정렬, 최소 크기 검증"""

    def test_aligned_value_inside_the_frame_is_kept(self) -> None:
        self.assertEqual(
            normalize_manual_active_frame(ActiveFrame(1600, 1000, 260, 40), 1920, 1080),
            ActiveFrame(1600, 1000, 260, 40),
        )

    def test_odd_values_are_floored_to_even(self) -> None:
        self.assertEqual(
            normalize_manual_active_frame(ActiveFrame(1601, 999, 261, 41), 1920, 1080),
            ActiveFrame(1600, 998, 260, 40),
        )

    def test_out_of_bounds_frame_is_rejected(self) -> None:
        """영상을 벗어난 지정은 조용히 자르지 않고 거부"""
        with self.assertRaises(ValueError):
            normalize_manual_active_frame(ActiveFrame(1920, 1080, 400, 200), 1920, 1080)
        with self.assertRaises(ValueError):
            normalize_manual_active_frame(ActiveFrame(800, 600, -40, 200), 1920, 1080)
        with self.assertRaises(ValueError):
            normalize_manual_active_frame(ActiveFrame(800, 600, 0, 1000), 1920, 1080)

    def test_full_frame_value_returns_none_and_tiny_size_raises(self) -> None:
        self.assertIsNone(
            normalize_manual_active_frame(ActiveFrame(1920, 1080), 1920, 1080)
        )
        with self.assertRaises(ValueError):
            normalize_manual_active_frame(ActiveFrame(31, 1080), 1920, 1080)
        with self.assertRaises(ValueError):
            normalize_manual_active_frame(ActiveFrame(800, 600), 0, 1080)


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

    def test_roundtrip_uses_the_configured_scale_algorithm(self) -> None:
        """왕복 scale 필터 두 곳이 설정한 보간 방식을 그대로 쓰는지 검증"""
        region = ActiveRegion(1920, 1080, 0, 0, False)
        for algorithm in SCALE_ALGORITHM_VALUES:
            with self.subTest(algorithm=algorithm):
                args = build_roundtrip_command(
                    ffmpeg_executable="ffmpeg",
                    source_path="movie.mp4",
                    start_sec=0.0,
                    duration_sec=1.0,
                    active_region=region,
                    tier=RESOLUTION_TIER_BY_KEY["720p"],
                    metric="vmaf",
                    model="vmaf_v0.6.1",
                    log_name="vmaf.json",
                    algorithm=algorithm,
                )
                chain = args[args.index("-filter_complex") + 1]
                self.assertEqual(chain.count(f"flags={algorithm}"), 2)
                for other in SCALE_ALGORITHM_VALUES:
                    if other != algorithm:
                        self.assertNotIn(f"flags={other}", chain)

    def test_roundtrip_tone_maps_hdr_before_the_shared_split(self) -> None:
        """HDR 왕복 지표는 split 앞에서 한 번 톤 매핑해 기준과 축소본을 같은 변환으로 비교함"""
        region = ActiveRegion(1920, 1080, 0, 0, False)
        args = build_roundtrip_command(
            ffmpeg_executable="ffmpeg",
            source_path="movie.mp4",
            start_sec=0.0,
            duration_sec=1.0,
            active_region=region,
            tier=RESOLUTION_TIER_BY_KEY["720p"],
            metric="vmaf",
            model="vmaf_v0.6.1",
            log_name="vmaf.json",
            tonemap=True,
        )
        chain = args[args.index("-filter_complex") + 1]
        self.assertTrue(chain.startswith(f"[0:v]{tonemap_filter_chain(hdr=True)},split=2[ref][dist];"))
        self.assertEqual(chain.count("tonemap=hable"), 1)
        sdr = build_roundtrip_command(
            ffmpeg_executable="ffmpeg",
            source_path="movie.mp4",
            start_sec=0.0,
            duration_sec=1.0,
            active_region=region,
            tier=RESOLUTION_TIER_BY_KEY["720p"],
            metric="vmaf",
            model="vmaf_v0.6.1",
            log_name="vmaf.json",
        )
        self.assertNotIn("tonemap=hable", sdr[sdr.index("-filter_complex") + 1])


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


@dataclass(frozen=True)
class _Versioned:
    """분석 버전만 가진 검사용 값"""

    analysis_version: int


@dataclass(frozen=True)
class _Unmeasurable:
    """분석 버전과 측정 불가 여부, 감지 실패 표시를 가진 검사용 값"""

    analysis_version: int
    unmeasurable: bool
    crop_scan_failed: bool = False


class CropScanVersionTests(unittest.TestCase):
    """여백 감지 전용 버전과 캐시 유효성 기준 검증"""

    def test_crop_only_result_is_recognized_by_its_version(self) -> None:
        self.assertEqual(CROP_SCAN_VERSION, 337)
        self.assertEqual(METRIC_CROP_ONLY, "")
        self.assertTrue(is_crop_only_result(_Versioned(CROP_SCAN_VERSION)))
        self.assertFalse(is_crop_only_result(_Versioned(FRAME_ANALYSIS_VERSION)))

    def test_crop_version_stays_usable_across_quality_modes(self) -> None:
        self.assertTrue(is_usable_analysis_version(CROP_SCAN_VERSION, 557))
        self.assertTrue(is_usable_analysis_version(CROP_SCAN_VERSION, 559))
        self.assertTrue(is_usable_analysis_version(558, 558))
        self.assertFalse(is_usable_analysis_version(233, 558))

    def test_failed_crop_scan_reads_the_stored_flag_for_every_version(self) -> None:
        """감지 실패 판정이 저장한 플래그를 먼저 보고, 플래그가 없는 감지 전용 행은 측정 불가로 판정"""
        flagged = _Unmeasurable(FRAME_ANALYSIS_VERSION, unmeasurable=True, crop_scan_failed=True)
        self.assertTrue(is_failed_crop_scan(flagged))
        self.assertTrue(
            is_failed_crop_scan(
                _Unmeasurable(CROP_SCAN_VERSION, unmeasurable=False, crop_scan_failed=True)
            )
        )
        self.assertFalse(
            is_failed_crop_scan(
                _Unmeasurable(FRAME_ANALYSIS_VERSION, unmeasurable=False, crop_scan_failed=False)
            )
        )

    def test_failed_crop_scan_keeps_recognizing_old_crop_only_rows(self) -> None:
        """플래그를 저장하기 전의 감지 전용 행은 측정 불가를 감지 실패로 읽음"""
        failed = _Unmeasurable(CROP_SCAN_VERSION, unmeasurable=True)
        self.assertTrue(is_failed_crop_scan(failed))
        self.assertFalse(is_failed_crop_scan(_Unmeasurable(CROP_SCAN_VERSION, unmeasurable=False)))
        self.assertFalse(
            is_failed_crop_scan(_Unmeasurable(FRAME_ANALYSIS_VERSION, unmeasurable=True))
        )

    def test_count_failed_crop_scans_counts_only_failed_detection_rows(self) -> None:
        """감지 실패 수 집계가 감지 실패로 저장한 행만 세는지 검증"""
        rows = [
            _Unmeasurable(CROP_SCAN_VERSION, unmeasurable=True),
            _Unmeasurable(CROP_SCAN_VERSION, unmeasurable=False),
            _Unmeasurable(FRAME_ANALYSIS_VERSION, unmeasurable=True),
            _Unmeasurable(CROP_SCAN_VERSION, unmeasurable=True),
            _Unmeasurable(FRAME_ANALYSIS_VERSION, unmeasurable=True, crop_scan_failed=True),
            _Unmeasurable(FRAME_ANALYSIS_VERSION, unmeasurable=False),
        ]
        self.assertEqual(count_failed_crop_scans(rows), 3)
        self.assertEqual(count_failed_crop_scans([]), 0)


class FrameAnalysisThresholdTests(unittest.TestCase):
    """설정 연동 판정 기준(축소 후보 VMAF/SSIM, 여백 면적 상한)과 캐시 재판정 검증"""

    def test_default_thresholds_keep_approved_values(self) -> None:
        self.assertEqual(DEFAULT_FRAME_ANALYSIS_THRESHOLDS.vmaf, 98.0)
        self.assertEqual(DEFAULT_FRAME_ANALYSIS_THRESHOLDS.ssim, 0.996)
        self.assertEqual(DEFAULT_FRAME_ANALYSIS_THRESHOLDS.active_area_no_bars_ratio, 0.90)
        self.assertAlmostEqual(DEFAULT_FRAME_ANALYSIS_THRESHOLDS.ssim_pass_score, SSIM_CONFIRMED_PASS_SCORE)
        self.assertAlmostEqual(DEFAULT_FRAME_ANALYSIS_THRESHOLDS.ssim_fail_score, SSIM_CONFIRMED_FAIL_SCORE)
        self.assertAlmostEqual(DEFAULT_FRAME_ANALYSIS_THRESHOLDS.vmaf_pass_score, VMAF_CONFIRMED_PASS_SCORE)
        self.assertAlmostEqual(DEFAULT_FRAME_ANALYSIS_THRESHOLDS.vmaf_fail_score, VMAF_CONFIRMED_FAIL_SCORE)

    def test_confirmed_offsets_follow_custom_criteria(self) -> None:
        thresholds = FrameAnalysisThresholds(vmaf=99.0, ssim=0.998)
        self.assertAlmostEqual(thresholds.ssim_pass_score, 0.9999)
        self.assertAlmostEqual(thresholds.ssim_fail_score, 0.997)
        self.assertAlmostEqual(thresholds.vmaf_pass_score, 100.0)
        self.assertAlmostEqual(thresholds.vmaf_fail_score, 97.5)
        self.assertEqual(FrameAnalysisThresholds(ssim=0.999).ssim_pass_score, 0.9999)
        self.assertEqual(FrameAnalysisThresholds(vmaf=85.0).vmaf_fail_score, 83.5)

    def test_frame_thresholds_from_config(self) -> None:
        config = ClassifierConfig()
        config.encode.roundtrip_vmaf_threshold = 97.0
        config.encode.roundtrip_ssim_threshold = 0.994
        config.encode.active_area_no_bars_ratio = 0.85
        thresholds = frame_thresholds_from_config(config)
        self.assertEqual((thresholds.vmaf, thresholds.ssim), (97.0, 0.994))
        self.assertEqual(thresholds.active_area_no_bars_ratio, 0.85)

    def test_scoring_uses_configured_criteria(self) -> None:
        strict = FrameAnalysisThresholds(vmaf=99.0, ssim=0.998)
        self.assertTrue(score_passes(TierScore("720p", 0.998, 99.0), use_vmaf=True, thresholds=strict))
        self.assertFalse(score_passes(TierScore("720p", 0.997, 99.0), use_vmaf=True, thresholds=strict))
        scores = [TierScore("1080p", 0.997, 98.5), TierScore("720p", 0.9965, 98.2)]
        self.assertEqual(select_candidate_tier(scores, use_vmaf=True), "720p")
        self.assertEqual(select_candidate_tier(scores, use_vmaf=True, thresholds=strict), None)

    def test_stage_confirmation_uses_configured_criteria(self) -> None:
        lenient = FrameAnalysisThresholds(ssim=0.994)
        self.assertTrue(ssim_stage_confirmed([0.9975], thresholds=lenient))
        self.assertFalse(ssim_stage_confirmed([0.9975]))
        medians = {"1080p": 0.9965, "720p": 0.9955}
        tiers = (RESOLUTION_TIER_BY_KEY["1080p"], RESOLUTION_TIER_BY_KEY["720p"])
        self.assertEqual(next_round_tier_keys(tiers, medians, thresholds=lenient), ("720p",))

    def test_no_bars_ratio_controls_crop_detection(self) -> None:
        boxes = [CropBox(1800, 1000, 60, 40)]
        default_region = merge_crop_union(boxes, 1920, 1080)
        self.assertTrue(default_region.has_black_bars)
        lenient = FrameAnalysisThresholds(active_area_no_bars_ratio=0.50)
        self.assertFalse(merge_crop_union(boxes, 1920, 1080, thresholds=lenient).has_black_bars)

    def test_apply_no_bars_ratio_reverts_large_active_region(self) -> None:
        region = ActiveRegion(1800, 1000, 60, 40, True)
        self.assertFalse(
            apply_no_bars_ratio(
                region, 1920, 1080, thresholds=FrameAnalysisThresholds(active_area_no_bars_ratio=0.50)
            ).has_black_bars
        )
        self.assertTrue(apply_no_bars_ratio(region, 1920, 1080).has_black_bars)
        full = ActiveRegion(1920, 1080, 0, 0, False)
        self.assertEqual(apply_no_bars_ratio(full, 1920, 1080), full)

    def test_rejudge_without_stored_candidate_uses_scores(self) -> None:
        """엄격 기준에서 탈락으로 저장된 행도 저장 점수로 다시 판정하는지 검증"""
        scores = (TierScore("1080p", 0.9975, None), TierScore("720p", 0.9965, None))
        relaxed = FrameAnalysisThresholds(ssim=0.996)
        self.assertEqual(
            rejudge_candidate_tier(scores, None, use_vmaf=False, thresholds=relaxed), "720p"
        )
        strict = FrameAnalysisThresholds(ssim=0.999)
        self.assertIsNone(rejudge_candidate_tier(scores, None, use_vmaf=False, thresholds=strict))

    def test_can_reuse_tier_scores_when_relaxed_scores_decide(self) -> None:
        """기준을 완화해 통과 연쇄가 저장 점수 안에서 정해지면 재사용 가능한지 검증"""
        scores = (
            TierScore("900p", 0.998, 99.0),
            TierScore("720p", 0.997, 98.5),
            TierScore("540p", 0.994, 96.0),
            TierScore("480p", 0.990, 93.0),
        )
        relaxed = FrameAnalysisThresholds(vmaf=95.0, ssim=0.993)
        self.assertTrue(can_reuse_tier_scores(scores, use_vmaf=True, thresholds=relaxed))
        self.assertTrue(can_reuse_tier_scores(scores, use_vmaf=True))

    def test_can_reuse_tier_scores_needs_the_vmaf_of_the_next_tier(self) -> None:
        """기준을 강화해 후보가 위로 옮겨 가야 하면 재지 않은 VMAF 때문에 재측정인지 검증"""
        scores = (
            TierScore("1440p", 0.999, None),
            TierScore("1080p", 0.998, 98.6),
            TierScore("720p", 0.994, None),
        )
        strict = FrameAnalysisThresholds(vmaf=99.0)
        self.assertFalse(can_reuse_tier_scores(scores, use_vmaf=True, thresholds=strict))
        self.assertTrue(can_reuse_tier_scores(scores, use_vmaf=True))

    def test_can_reuse_tier_scores_requires_every_tier_when_all_pass(self) -> None:
        """모든 저장 점수가 통과해도 재지 않은 하위 규격이 있으면 재측정인지 검증"""
        scores = (TierScore("1080p", 0.999, 99.5), TierScore("720p", 0.998, 99.0))
        self.assertFalse(
            can_reuse_tier_scores(
                scores, use_vmaf=True, all_tier_keys=("1080p", "720p", "540p")
            )
        )
        self.assertTrue(
            can_reuse_tier_scores(scores, use_vmaf=True, all_tier_keys=("1080p", "720p"))
        )
        self.assertFalse(
            can_reuse_tier_scores((), use_vmaf=True, all_tier_keys=("1080p",))
        )
        self.assertFalse(
            can_reuse_tier_scores(
                (TierScore("720p", 0.999, 99.5),), use_vmaf=True, all_tier_keys=("1080p", "720p")
            )
        )
        self.assertFalse(
            can_reuse_tier_scores(
                scores, use_vmaf=False, all_tier_keys=("1080p", "720p", "540p")
            )
        )
        self.assertTrue(
            can_reuse_tier_scores(scores, use_vmaf=False, all_tier_keys=("1080p", "720p"))
        )

    def test_rejudge_recovers_higher_tier_when_stored_tier_fails(self) -> None:
        """기준 강화로 저장 후보가 떨어져도 위쪽 규격 VMAF가 기준을 넘으면 그 규격을 돌려주는지 검증"""
        scores = (
            TierScore("1440p", 0.999, 99.4),
            TierScore("1080p", 0.998, 98.5),
            TierScore("720p", 0.994, None),
        )
        strict = FrameAnalysisThresholds(vmaf=99.0)
        self.assertTrue(
            can_reuse_tier_scores(
                scores,
                use_vmaf=True,
                thresholds=strict,
                all_tier_keys=("1440p", "1080p", "720p", "540p"),
            )
        )
        self.assertEqual(
            rejudge_candidate_tier(scores, "1080p", use_vmaf=True, thresholds=strict),
            "1440p",
        )

    def test_rejudge_candidate_keeps_or_drops_stored_choice(self) -> None:
        scores = (
            TierScore("1440p", 0.999, None),
            TierScore("1080p", 0.998, 98.6),
            TierScore("720p", 0.997, None),
        )
        self.assertEqual(
            rejudge_candidate_tier(scores, "1080p", use_vmaf=True), "1080p"
        )
        strict = FrameAnalysisThresholds(vmaf=99.0)
        self.assertIsNone(rejudge_candidate_tier(scores, "1080p", use_vmaf=True, thresholds=strict))
        self.assertIsNone(rejudge_candidate_tier((), "1080p", use_vmaf=True))
        self.assertIsNone(rejudge_candidate_tier(scores, None, use_vmaf=True))
        self.assertIsNone(rejudge_candidate_tier(scores, "not-a-tier", use_vmaf=True))

    def test_rejudge_candidate_extends_when_criteria_relaxed(self) -> None:
        scores = (TierScore("1080p", 0.9962, 98.1), TierScore("720p", 0.994, 96.0))
        relaxed = FrameAnalysisThresholds(ssim=0.993)
        self.assertEqual(
            rejudge_candidate_tier(scores, "1080p", use_vmaf=False, thresholds=relaxed), "720p"
        )
        self.assertEqual(
            rejudge_candidate_tier(scores, "1080p", use_vmaf=True, thresholds=relaxed),
            "1080p",
        )


if __name__ == "__main__":
    unittest.main()
