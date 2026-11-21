"""키프레임 교정 영상 품질값 자동 조정(quality_tuning) 순수 계산 단위 테스트"""

import unittest

from src.core.encode.quality_tuning import (
    BYTES_PER_MB,
    SAMPLE_K_MAX,
    SAMPLE_K_MIN,
    SAMPLE_SEGMENT_COUNT,
    SAMPLE_SEGMENT_DURATION_SEC,
    choose_second_sample_quality,
    compute_kf_target_bytes,
    compute_retry_quality,
    estimate_full_video_bytes,
    format_tuned_reason,
    max_tuned_quality,
    resolve_video_stream_bytes,
    sample_segments,
    snap_quality,
    solve_final_sample_quality,
)
from src.core.encode.stream_layout import parse_stream_layout
from src.core.encode_tag import with_tag_quality


class TestQualityTuning(unittest.TestCase):
    """결정 33 구간 배치, 비영상 분리, 2차/최종 품질값 계산, 재시도 판정을 검증"""

    def test_sample_segments_handles_short_and_normal_videos(self) -> None:
        self.assertEqual(sample_segments(0.0), ())
        self.assertEqual(sample_segments(15.0), ((0.0, 15.0),))
        segs = sample_segments(60.0)
        self.assertEqual(len(segs), SAMPLE_SEGMENT_COUNT)
        self.assertTrue(all(dur == SAMPLE_SEGMENT_DURATION_SEC for _, dur in segs))
        self.assertAlmostEqual(segs[0][0], 6.0, places=2)
        self.assertAlmostEqual(segs[-1][0], 52.0, places=2)

    def test_resolve_video_stream_bytes_prefers_layout_then_fallback(self) -> None:
        mkv_layout = parse_stream_layout(
            {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "tags": {"NUMBER_OF_BYTES": "50000000"}}]}
        )
        self.assertEqual(
            resolve_video_stream_bytes(mkv_layout, source_size_bytes=80_000_000, duration_sec=60.0, fallback_video_mb=72.0),
            50_000_000,
        )
        mkv_bps_layout = parse_stream_layout(
            {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "tags": {"BPS": "4724000"}}]}
        )
        self.assertEqual(
            resolve_video_stream_bytes(mkv_bps_layout, source_size_bytes=80_000_000, duration_sec=10.0, fallback_video_mb=72.0),
            round(4_724_000 * 10.0 / 8),
        )
        self.assertEqual(with_tag_quality("v=1;enc=hevc_nvenc;q=22.0;app=1.0", 26.6), "v=1;enc=hevc_nvenc;q=26.6;app=1.0")
        mp4_layout = parse_stream_layout(
            {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "bit_rate": "8000000"}]}
        )
        self.assertEqual(
            resolve_video_stream_bytes(mp4_layout, source_size_bytes=80_000_000, duration_sec=10.0, fallback_video_mb=None),
            10_000_000,
        )
        empty_layout = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}]})
        self.assertEqual(
            resolve_video_stream_bytes(empty_layout, source_size_bytes=80_000_000, duration_sec=10.0, fallback_video_mb=20.0),
            20 * BYTES_PER_MB,
        )

    def test_target_and_sample_pass_and_two_pass_solve(self) -> None:
        spec = compute_kf_target_bytes(
            source_size_bytes=80_000_000,
            video_stream_bytes=55_000_000,
            keyframe_increase_ratio=0.05,
        )
        self.assertEqual(spec.s_other_bytes, 25_000_000)
        self.assertGreater(spec.v_target_bytes, 55_000_000)
        self.assertEqual(spec.s_target_bytes, spec.s_other_bytes + spec.v_target_bytes)

        self.assertEqual(estimate_full_video_bytes(10_000_000, 10.0, 60.0), 60_000_000)
        self.assertIsNone(
            choose_second_sample_quality(
                q1=22.0, q_max=28.0, v1_est_bytes=50_000_000, v_target_bytes=55_000_000, integer_scale=False
            )
        )
        q2 = choose_second_sample_quality(
            q1=22.0, q_max=28.0, v1_est_bytes=90_000_000, v_target_bytes=55_000_000, integer_scale=False
        )
        self.assertIsNotNone(q2)
        assert q2 is not None
        self.assertGreaterEqual(q2, 23.5)

        final_q, slope_k = solve_final_sample_quality(
            q1=22.0,
            v1_est_bytes=90_000_000,
            q2=q2,
            v2_est_bytes=60_000_000,
            v_target_bytes=55_000_000,
            q_min=22.0,
            q_max=max_tuned_quality(22.0),
            integer_scale=False,
        )
        self.assertGreaterEqual(slope_k, SAMPLE_K_MIN)
        self.assertLessEqual(slope_k, SAMPLE_K_MAX)
        self.assertGreater(final_q, 22.0)
        self.assertLessEqual(final_q, 28.0)

    def test_retry_and_reason_formatting(self) -> None:
        self.assertIsNone(
            compute_retry_quality(
                current_q=25.0, q_max=28.0, estimated_total_bytes=41_000_000, target_total_bytes=40_000_000,
                slope_k=6.0, integer_scale=False,
            )
        )
        retry_q = compute_retry_quality(
            current_q=25.0, q_max=28.0, estimated_total_bytes=46_000_000, target_total_bytes=40_000_000,
            slope_k=5.5, integer_scale=False,
        )
        self.assertIsNotNone(retry_q)
        assert retry_q is not None
        self.assertGreaterEqual(retry_q - 25.0, 0.4)
        self.assertEqual(snap_quality(24.4, q_min=22.0, q_max=28.0, integer_scale=True), 24.0)
        self.assertIn("재시도 1회", format_tuned_reason(baseline_q=22.0, sample_q=26.9, final_q=27.6, retry_count=1, passed_first=False))
        self.assertIn("1차 샘플 통과", format_tuned_reason(baseline_q=22.0, sample_q=22.0, final_q=22.0, retry_count=0, passed_first=True))
        self.assertIn("키프레임 재인코딩 우선", format_tuned_reason(baseline_q=22.0, sample_q=22.0, final_q=22.0, retry_count=0, passed_first=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
