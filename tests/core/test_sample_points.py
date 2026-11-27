"""공통 샘플 구간 선택과 무효 구간 판정, 중앙값 집계 단위 테스트"""

import itertools
import unittest

from src.core.sample_points import (
    BLACK_RATIO_INVALID_THRESHOLD,
    DEFAULT_SEGMENT_COUNT,
    DEFAULT_SEGMENT_DURATION_SEC,
    FLAT_ROUNDTRIP_SSIM_DB,
    SAMPLE_SEGMENT_COUNT,
    SAMPLE_SEGMENT_DURATION_SEC,
    SegmentQuality,
    is_invalid_segment,
    median_valid_metric,
    sample_segments,
)


class TestSamplePoints(unittest.TestCase):
    """구간 배치 규칙과 무효 구간 판정, 중앙값 집계를 검증"""

    def test_default_grid_uses_nine_two_second_segments(self) -> None:
        segments = sample_segments(60.0)
        self.assertEqual(len(segments), DEFAULT_SEGMENT_COUNT)
        self.assertTrue(all(length == DEFAULT_SEGMENT_DURATION_SEC for _, length in segments))
        self.assertAlmostEqual(segments[0][0], 6.0, places=2)
        self.assertAlmostEqual(segments[-1][0], 52.0, places=2)

    def test_caller_count_and_length_override_defaults(self) -> None:
        segments = sample_segments(60.0, SAMPLE_SEGMENT_COUNT, SAMPLE_SEGMENT_DURATION_SEC)
        self.assertEqual(len(segments), SAMPLE_SEGMENT_COUNT)
        self.assertAlmostEqual(segments[0][0], 6.0, places=2)
        self.assertAlmostEqual(segments[-1][0], 52.0, places=2)

    def test_short_or_degenerate_durations_use_whole_video(self) -> None:
        self.assertEqual(sample_segments(0.0), ())
        self.assertEqual(sample_segments(-1.0), ())
        self.assertEqual(sample_segments(15.0), ((0.0, 15.0),))
        self.assertEqual(sample_segments(60.0, 1), ((0.0, 60.0),))
        self.assertEqual(sample_segments(1.5), ((0.0, 1.5),))

    def test_segments_do_not_overlap_between_twenty_and_twenty_two_and_a_half_seconds(self) -> None:
        """20초 이상 22.5초 미만에서 구간이 겹쳐 같은 프레임을 두 번 재지 않는지 검증"""
        for tenths in range(200, 226):
            duration = tenths / 10.0
            segments = sample_segments(duration)
            self.assertLessEqual(len(segments), DEFAULT_SEGMENT_COUNT)
            self.assertGreaterEqual(len(segments), 2)
            for start, length in segments:
                self.assertEqual(length, DEFAULT_SEGMENT_DURATION_SEC)
                self.assertGreaterEqual(start, 0.0)
                self.assertLessEqual(start + length, duration + 1e-9)
            for (start, length), (next_start, _) in itertools.pairwise(segments):
                self.assertGreaterEqual(next_start, start + length - 1e-9)

    def test_overlap_avoidance_keeps_default_grid_for_long_videos(self) -> None:
        """긴 영상은 기존 9지점 배치를 그대로 유지하는지 검증"""
        self.assertEqual(len(sample_segments(60.0)), DEFAULT_SEGMENT_COUNT)
        self.assertEqual(len(sample_segments(22.5)), DEFAULT_SEGMENT_COUNT)
        self.assertEqual(len(sample_segments(22.4)), DEFAULT_SEGMENT_COUNT - 1)

    def test_invalid_segment_by_black_ratio_or_flat_roundtrip(self) -> None:
        black = SegmentQuality(0.0, 2.0, black_ratio=BLACK_RATIO_INVALID_THRESHOLD)
        self.assertTrue(is_invalid_segment(black))
        flat = SegmentQuality(0.0, 2.0, roundtrip_ssim_db=FLAT_ROUNDTRIP_SSIM_DB)
        self.assertTrue(is_invalid_segment(flat))
        measured = SegmentQuality(0.0, 2.0, black_ratio=0.1, roundtrip_ssim_db=38.0)
        self.assertFalse(is_invalid_segment(measured))
        unmeasured = SegmentQuality(0.0, 2.0)
        self.assertFalse(is_invalid_segment(unmeasured))

    def test_median_of_valid_metrics_skips_invalid_and_missing(self) -> None:
        samples = [
            SegmentQuality(0.0, 2.0, metric_value=10.0),
            SegmentQuality(2.0, 2.0, metric_value=20.0),
            SegmentQuality(4.0, 2.0, metric_value=30.0),
            SegmentQuality(6.0, 2.0, black_ratio=0.9, metric_value=99.0),
            SegmentQuality(8.0, 2.0, roundtrip_ssim_db=41.0, metric_value=98.0),
            SegmentQuality(10.0, 2.0),
        ]
        self.assertEqual(median_valid_metric(samples), 20.0)
        self.assertEqual(median_valid_metric(samples[:2]), 15.0)
        self.assertIsNone(median_valid_metric([]))
        self.assertIsNone(median_valid_metric(samples[3:]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
