"""영상 스트림 비트레이트 배수와 키프레임 증가율 추정 검증"""

import unittest

from src.core.evaluation.size_estimator import (
    STATIC_SCREEN_RATIO_FLOOR,
    STATIC_SCREEN_RATIO_THRESHOLD,
    calc_keyframe_increase_ratio,
    calc_stream_bitrate_factor,
    stream_mb_at_kbps,
)
from src.core.models import BYTES_PER_MB
from src.core.quality_target import encoder_size_factor


class TestSizeEstimator(unittest.TestCase):
    """factor_br 세 항의 최솟값 선택과 정적 화면 가중치 분기 검증"""

    def test_stream_mb_at_kbps_converts_bits_to_mebibytes(self) -> None:
        """비트레이트와 길이를 메비바이트로 환산하는지 검증"""
        self.assertAlmostEqual(stream_mb_at_kbps(8000.0, 60.0), 8000 * 1000 * 60 / 8 / BYTES_PER_MB)

    def test_bitrate_factor_picks_the_smallest_term(self) -> None:
        """출력 비율 항이 가장 작으면 그 값이 배수가 되는지 검증"""
        size_factor = encoder_size_factor(None)
        self.assertAlmostEqual(calc_stream_bitrate_factor(4.0), size_factor / 4.0)

    def test_bitrate_factor_caps_at_encoder_size_factor(self) -> None:
        """출력 비율이 1 미만이어도 인코더 크기 보정 계수를 넘지 않는지 검증"""
        size_factor = encoder_size_factor(None)
        self.assertAlmostEqual(calc_stream_bitrate_factor(0.5), size_factor)

    def test_bitrate_factor_uses_codec_ratio_on_codec_reversal(self) -> None:
        """코덱 역전이고 출력 비율이 1 미만이면 코덱 비율 항만 비교하는지 검증"""
        size_factor = encoder_size_factor(None)
        codec_ratio = 2.0
        self.assertAlmostEqual(
            calc_stream_bitrate_factor(0.25, codec_weight_ratio=codec_ratio),
            codec_ratio * size_factor,
        )

    def test_bitrate_factor_prefers_output_ratio_on_codec_reversal(self) -> None:
        """코덱 역전에서 출력 비율 항이 더 작으면 그 항을 쓰는지 검증"""
        size_factor = encoder_size_factor(None)
        self.assertAlmostEqual(
            calc_stream_bitrate_factor(0.5, codec_weight_ratio=4.0),
            size_factor / 0.5,
        )

    def test_static_screen_weight_boundaries(self) -> None:
        """정적 화면 가중치가 기준 미만에서만 적용되고 아니면 1인지 검증"""
        rate = 0.06
        fps = 60.0
        threshold = STATIC_SCREEN_RATIO_THRESHOLD
        below = calc_keyframe_increase_ratio(8.0, rate, fps=fps, base_fps=30.0, source_ratio=threshold - 0.01)
        at_threshold = calc_keyframe_increase_ratio(8.0, rate, fps=fps, base_fps=30.0, source_ratio=threshold)
        base = rate * (1.0 - 1.0 / 8.0) * (30.0 / fps) ** 0.5
        self.assertAlmostEqual(below, base * (threshold / max(threshold - 0.01, STATIC_SCREEN_RATIO_FLOOR)))
        self.assertAlmostEqual(at_threshold, base)


if __name__ == "__main__":
    unittest.main(verbosity=2)
