"""키프레임 교정 영상 품질값 자동 조정(quality_tuning) 순수 계산 단위 테스트"""

import math
import unittest

from src.core.encode.encode_gate import GatePurpose
from src.core.encode.quality_tuning import (
    BYTES_PER_MB,
    PROTECT_CQ_DELTA_MAX,
    QUALITY_DECIMAL_PLACES,
    SAMPLE_K_MAX,
    SAMPLE_K_MIN,
    SAMPLE_SEGMENT_COUNT,
    SAMPLE_SEGMENT_DURATION_SEC,
    SAMPLE_TARGET_TOLERANCE,
    adjust_saving_quality,
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


class TestSavingQualityAdjustment(unittest.TestCase):
    """절약 목적 사전 예측 초과 시 품질값 자동 조정 순수 계산을 검증"""

    def test_prediction_within_tolerance_returns_none(self) -> None:
        """예측 비트레이트가 보정 목표 x 허용 배수 이하면 조정하지 않음"""
        self.assertIsNone(
            adjust_saving_quality(
                quality=22.0,
                predicted_video_kbps=1150.0,
                corrected_target_kbps=1000.0,
                baseline_quality=22.0,
                encoder="hevc_nvenc",
            )
        )
        self.assertIsNone(
            adjust_saving_quality(
                quality=22.0,
                predicted_video_kbps=500.0,
                corrected_target_kbps=1000.0,
                baseline_quality=22.0,
            )
        )

    def test_overshoot_raises_quality_by_effective_k(self) -> None:
        """초과분은 유효 지수 K * log2(예측 / 목표x1.15)만큼 품질값을 올림"""
        expected = 22.0 + 5.8 * math.log2(1600.0 / (1000.0 * SAMPLE_TARGET_TOLERANCE))
        self.assertAlmostEqual(
            adjust_saving_quality(
                quality=22.0,
                predicted_video_kbps=1600.0,
                corrected_target_kbps=1000.0,
                baseline_quality=22.0,
                encoder="hevc_nvenc",
            )
            or 0.0,
            round(expected, QUALITY_DECIMAL_PLACES),
        )

    def test_quality_is_capped_at_baseline_delta_max(self) -> None:
        """아주 큰 초과라도 기준 품질값 + 한도를 넘지 않음"""
        self.assertEqual(
            adjust_saving_quality(
                quality=22.0,
                predicted_video_kbps=100_000.0,
                corrected_target_kbps=1000.0,
                baseline_quality=22.0,
                encoder="hevc_nvenc",
            ),
            22.0 + PROTECT_CQ_DELTA_MAX,
        )

    def test_small_overshoot_below_min_delta_is_ignored(self) -> None:
        """올릴 폭이 최소 조정 폭 미만이면 샘플 편차로 보고 조정하지 않음"""
        slight = 1000.0 * SAMPLE_TARGET_TOLERANCE * 1.01
        self.assertIsNone(
            adjust_saving_quality(
                quality=22.0,
                predicted_video_kbps=slight,
                corrected_target_kbps=1000.0,
                baseline_quality=22.0,
                encoder="hevc_nvenc",
            )
        )

    def test_integer_scale_encoder_returns_whole_number(self) -> None:
        """정수 척도 인코더는 마지막에 정수로 반올림한 품질값을 돌려줌"""
        adjusted = adjust_saving_quality(
            quality=22.0,
            predicted_video_kbps=1600.0,
            corrected_target_kbps=1000.0,
            baseline_quality=22.0,
            encoder="hevc_qsv",
            integer_scale=True,
        )
        assert adjusted is not None
        self.assertEqual(adjusted, float(int(adjusted)))

    def test_unusable_target_returns_none(self) -> None:
        """보정 목표가 0 이하이면 비교 기준이 없으므로 조정하지 않음"""
        self.assertIsNone(
            adjust_saving_quality(
                quality=22.0,
                predicted_video_kbps=1600.0,
                corrected_target_kbps=0.0,
                baseline_quality=22.0,
            )
        )


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
        self.assertIn("1차 시험 통과", format_tuned_reason(baseline_q=22.0, sample_q=22.0, final_q=22.0, retry_count=0, passed_first=True))
        self.assertIn(
            "키프레임 인코딩 우선",
            format_tuned_reason(
                baseline_q=22.0, sample_q=22.0, final_q=22.0, retry_count=0, passed_first=False,
                purpose=GatePurpose.KEYFRAME_FIX,
            ),
        )
        for purpose in (GatePurpose.TRANSFORM, GatePurpose.COMPAT):
            with self.subTest(purpose=purpose):
                reason = format_tuned_reason(
                    baseline_q=22.0, sample_q=22.0, final_q=22.0, retry_count=0, passed_first=False,
                    purpose=purpose,
                )
                self.assertNotIn("키프레임", reason)
                self.assertEqual(reason, "품질값 22.0 유지 (품질 시험)")

    def test_max_tuned_quality_follows_setting(self) -> None:
        """품질값 자동 조절 한도 설정이 상한 계산에 반영됨"""
        self.assertEqual(max_tuned_quality(22.0), 28.0)
        self.assertEqual(max_tuned_quality(22.0, delta_max=4.0), 26.0)
        self.assertEqual(max_tuned_quality(22.0, delta_max=0.0), 22.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
