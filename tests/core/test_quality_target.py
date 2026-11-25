"""화질 기준값, 품질값 차이 계수, 기준 비트레이트, 인코더 척도 매핑과 품질값 표기를 검증하는 단위 테스트"""

import unittest

from src.core.quality_target import (
    BASELINE_CQ_DEFAULT,
    BASELINE_CQ_MAX,
    BASELINE_CQ_MIN,
    PROTECT_CQ_OFFSET,
    QUALITY_BITRATE_K,
    calc_base_bitrate_kbps,
    clamp_baseline_cq,
    effective_quality_bitrate_k,
    encoder_size_factor,
    format_cq_value,
    protect_cq_value,
    quality_bitrate_k,
    quality_size_factor,
    to_encoder_quality,
)
from src.core.smart_action import SmartAction
from tests._factories import make_eval


class TestBaselineClamp(unittest.TestCase):
    """설정 로드 시 화질 기준값 범위 제한과 비유한 값의 기본값 대체를 검증"""

    def test_values_outside_range_are_clamped(self) -> None:
        """범위 밖 값은 최소, 최대 경계로 제한됨을 검증"""
        self.assertEqual(clamp_baseline_cq(BASELINE_CQ_MAX + 100.0), BASELINE_CQ_MAX)
        self.assertEqual(clamp_baseline_cq(BASELINE_CQ_MIN - 100.0), BASELINE_CQ_MIN)

    def test_in_range_value_is_kept(self) -> None:
        """범위 안의 소수 값은 그대로 유지됨을 검증"""
        self.assertEqual(clamp_baseline_cq(23.9), 23.9)

    def test_non_finite_values_fall_back_to_default(self) -> None:
        """NaN과 무한대는 기본값으로 대체됨을 검증"""
        self.assertEqual(clamp_baseline_cq(float("nan")), BASELINE_CQ_DEFAULT)
        self.assertEqual(clamp_baseline_cq(float("inf")), BASELINE_CQ_DEFAULT)
        self.assertEqual(clamp_baseline_cq(float("-inf")), BASELINE_CQ_DEFAULT)


class TestBaseBitrate(unittest.TestCase):
    """B_base(Q) = B_ref * 2^((22.0 - Q) / K) 환산 경계를 검증"""

    def test_reference_bitrate_is_kept_at_default_baseline(self) -> None:
        """기본 기준 품질값 22.0에서는 기준 비트레이트가 그대로 유지됨을 검증"""
        self.assertAlmostEqual(calc_base_bitrate_kbps(1500, BASELINE_CQ_DEFAULT), 1500.0)

    def test_bitrate_doubles_per_k_steps_toward_higher_quality(self) -> None:
        """품질값이 K만큼 낮아지면 기준 비트레이트가 2배, K만큼 높아지면 절반이 됨을 검증"""
        self.assertAlmostEqual(
            calc_base_bitrate_kbps(1500, BASELINE_CQ_DEFAULT - QUALITY_BITRATE_K), 3000.0
        )
        self.assertAlmostEqual(
            calc_base_bitrate_kbps(1500, BASELINE_CQ_DEFAULT + QUALITY_BITRATE_K), 750.0
        )

    def test_fractional_quality_is_not_rounded(self) -> None:
        """소수 품질값 23.9를 정수로 반올림하지 않고 그대로 환산함을 검증"""
        expected = 1500 * 2.0 ** ((22.0 - 23.9) / QUALITY_BITRATE_K)
        self.assertAlmostEqual(calc_base_bitrate_kbps(1500, 23.9), expected)


class TestQualitySizeFactor(unittest.TestCase):
    """용량 계수 2^(-delta/K)의 경계값과 보호 품질값 계수를 검증"""

    def test_zero_difference_keeps_size(self) -> None:
        """품질값 차이가 0이면 계수가 1임을 검증"""
        self.assertEqual(quality_size_factor(0.0), 1.0)

    def test_k_step_halves_and_negative_step_doubles(self) -> None:
        """차이가 K이면 계수 0.5, -K이면 2.0임을 검증"""
        self.assertAlmostEqual(quality_size_factor(QUALITY_BITRATE_K), 0.5)
        self.assertAlmostEqual(quality_size_factor(-QUALITY_BITRATE_K), 2.0)

    def test_protect_offset_factor(self) -> None:
        """저화질 보호 오프셋 4.0의 계수가 2^(-4/6)임을 검증"""
        self.assertAlmostEqual(quality_size_factor(PROTECT_CQ_OFFSET), 2.0 ** (-4.0 / 6.0))


class TestEncoderQualityAndFormat(unittest.TestCase):
    """보호 품질값, 정수 척도 반올림, 소수 첫째 자리 표기를 검증"""

    def test_protect_value_adds_offset_to_baseline(self) -> None:
        """보호 품질값은 기준 품질값에 보호 오프셋을 더한 값임을 검증"""
        self.assertEqual(protect_cq_value(22.0), 26.0)
        self.assertAlmostEqual(protect_cq_value(23.9), 27.9)

    def test_integer_scale_rounds_only_at_the_end(self) -> None:
        """정수 척도 인코더만 마지막 단계에서 반올림하고 소수 척도 인코더는 그대로 둠을 검증"""
        self.assertEqual(to_encoder_quality(23.9, integer_scale=True), 24.0)
        self.assertEqual(to_encoder_quality(23.4, integer_scale=True), 23.0)
        self.assertEqual(to_encoder_quality(23.9, integer_scale=False), 23.9)

    def test_quality_value_is_shown_with_one_decimal(self) -> None:
        """품질값 표기가 소수 첫째 자리로 고정됨을 검증"""
        self.assertEqual(format_cq_value(22.0), "22.0")
        self.assertEqual(format_cq_value(23.9), "23.9")


class TestEncoderCalibration(unittest.TestCase):
    """인코더별 K, 크기 보정 계수, 같은 VMAF 척도 매핑을 검증"""

    def test_k_and_size_factor_lookup(self) -> None:
        self.assertEqual(quality_bitrate_k("hevc_nvenc"), 5.8)
        self.assertEqual(quality_bitrate_k("libsvtav1"), 12.9)
        self.assertEqual(quality_bitrate_k("unknown"), QUALITY_BITRATE_K)
        self.assertEqual(quality_bitrate_k(), QUALITY_BITRATE_K)
        self.assertEqual(encoder_size_factor("hevc_nvenc"), 2.60)
        self.assertEqual(encoder_size_factor("libx265"), 1.66)
        self.assertEqual(encoder_size_factor("unknown"), 1.0)
        self.assertEqual(encoder_size_factor(), 1.0)

    def test_same_vmaf_mapping_scales_and_offsets(self) -> None:
        self.assertAlmostEqual(to_encoder_quality(22.0, encoder="libx265"), 18.26)
        self.assertAlmostEqual(to_encoder_quality(22.0, encoder="libx264"), 19.35)
        self.assertAlmostEqual(to_encoder_quality(22.0, encoder="h264_nvenc"), 23.41)
        self.assertAlmostEqual(to_encoder_quality(22.0, encoder="libsvtav1"), 11.5)
        self.assertEqual(to_encoder_quality(22.0, encoder="hevc_nvenc"), 22.0)
        self.assertEqual(to_encoder_quality(22.0), 22.0)

    def test_mapping_clamps_to_encoder_range_and_rounds_last(self) -> None:
        self.assertEqual(to_encoder_quality(0.0, encoder="libsvtav1"), 0.0)
        self.assertEqual(to_encoder_quality(100.0, encoder="libsvtav1"), 63.0)
        self.assertEqual(to_encoder_quality(100.0, encoder="libx265"), 51.0)
        self.assertEqual(to_encoder_quality(22.0, encoder="libsvtav1", integer_scale=True), 12.0)
        self.assertEqual(
            to_encoder_quality(100.0, encoder="libx265", quality_min=0.0, quality_max=30.0), 30.0
        )

    def test_base_bitrate_and_quality_factor_use_encoder_k(self) -> None:
        self.assertAlmostEqual(
            calc_base_bitrate_kbps(1500, BASELINE_CQ_DEFAULT - 5.8, encoder="hevc_nvenc"), 3000.0
        )
        self.assertAlmostEqual(quality_size_factor(5.8, encoder="hevc_nvenc"), 0.5)
        self.assertAlmostEqual(
            quality_size_factor(PROTECT_CQ_OFFSET, encoder="hevc_nvenc"),
            2.0 ** (-4.0 / 5.8),
        )


class TestEncoderScaleSlope(unittest.TestCase):
    """기준 척도 품질값 이동이 인코더 척도 기울기만큼 환산되는지 검증"""

    def test_size_factor_applies_encoder_scale_slope(self) -> None:
        """기준 척도 +4.0 이동은 libx265 CRF +1.72 이동이므로 계수가 2^(-1.72/5.7)임을 검증"""
        expected = 2.0 ** (-(PROTECT_CQ_OFFSET * 0.43) / 5.7)
        self.assertAlmostEqual(quality_size_factor(PROTECT_CQ_OFFSET, encoder="libx265"), expected)
        self.assertAlmostEqual(quality_size_factor(PROTECT_CQ_OFFSET, encoder="libx265"), 0.811, places=3)

    def test_base_bitrate_applies_encoder_scale_slope(self) -> None:
        """libx265 기준 비트레이트도 용량 계수와 같은 비율로 줄어듦을 검증"""
        expected = 1500.0 * 2.0 ** (-(PROTECT_CQ_OFFSET * 0.43) / 5.7)
        self.assertAlmostEqual(
            calc_base_bitrate_kbps(1500.0, BASELINE_CQ_DEFAULT + PROTECT_CQ_OFFSET, encoder="libx265"),
            expected,
        )

    def test_baseline_encoder_and_none_keep_previous_values(self) -> None:
        """hevc_nvenc와 None은 기울기 1.0이라 기존 지수 그대로임을 검증"""
        self.assertAlmostEqual(
            quality_size_factor(PROTECT_CQ_OFFSET, encoder="hevc_nvenc"), 2.0 ** (-4.0 / 5.8)
        )
        self.assertAlmostEqual(quality_size_factor(PROTECT_CQ_OFFSET), 2.0 ** (-4.0 / 6.0))
        self.assertAlmostEqual(
            calc_base_bitrate_kbps(1500.0, BASELINE_CQ_DEFAULT + PROTECT_CQ_OFFSET, encoder="hevc_nvenc"),
            1500.0 * 2.0 ** (-4.0 / 5.8),
        )
        self.assertAlmostEqual(
            calc_base_bitrate_kbps(1500.0, BASELINE_CQ_DEFAULT + PROTECT_CQ_OFFSET),
            1500.0 * 2.0 ** (-4.0 / 6.0),
        )

    def test_effective_k_divides_by_encoder_scale_slope(self) -> None:
        """유효 지수는 K / scale이며 매핑 없는 인코더는 K 그대로임을 검증"""
        self.assertAlmostEqual(effective_quality_bitrate_k("libx265"), 5.7 / 0.43)
        self.assertAlmostEqual(effective_quality_bitrate_k("libsvtav1"), 12.9 / 1.25)
        self.assertEqual(effective_quality_bitrate_k("hevc_nvenc"), 5.8)
        self.assertEqual(effective_quality_bitrate_k("unknown"), QUALITY_BITRATE_K)
        self.assertEqual(effective_quality_bitrate_k(), QUALITY_BITRATE_K)


class TestActionLabelRendering(unittest.TestCase):
    """권장 처리 명칭의 품질 {값} 자리가 적용 품질값으로 채워지는지 검증"""

    def test_standard_action_shows_applied_quality(self) -> None:
        """적용 품질값 23.9인 과화질 인코딩이 품질 23.9 인코딩으로 표시됨을 검증"""
        ev = make_eval(smart_action=SmartAction.ENCODE_OVER, output_cq=23.9)
        self.assertEqual(ev.action_label, "품질 23.9 인코딩")
        self.assertEqual(ev.option_label, "품질 23.9 + 1초 키프레임")

    def test_protected_route_shows_protect_quality(self) -> None:
        """저화질 보호 라우팅은 보호 품질값 27.9를 품질 27.9 인코딩으로 표시함을 검증"""
        ev = make_eval(
            smart_action=SmartAction.ENCODE_LOW_KF,
            use_low_quality_preset=True,
            output_cq=protect_cq_value(23.9),
        )
        self.assertEqual(ev.action_label, "품질 27.9 인코딩")

    def test_default_labels_follow_default_settings(self) -> None:
        """기본 설정의 열 필터 목록과 기본 명칭이 품질 22.0, 품질 26.0 표기임을 검증"""
        self.assertEqual(SmartAction.ENCODE_OVER.action_label, "품질 22.0 인코딩")
        self.assertEqual(SmartAction.ENCODE_LOW_KF.action_label, "품질 26.0 인코딩")
        self.assertEqual(SmartAction.ENCODE_DOWNSCALE.action_label, "축소 인코딩")


if __name__ == "__main__":
    unittest.main(verbosity=2)
