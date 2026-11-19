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
    format_cq_value,
    protect_cq_value,
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


class TestActionLabelRendering(unittest.TestCase):
    """권장 처리 명칭의 품질 {값} 자리가 적용 품질값으로 채워지는지 검증"""

    def test_standard_action_shows_applied_quality(self) -> None:
        """적용 품질값 23.9인 과화질 재인코딩이 품질 23.9 재인코딩으로 표시됨을 검증"""
        ev = make_eval(smart_action=SmartAction.ENCODE_OVER, output_cq=23.9)
        self.assertEqual(ev.action_label, "품질 23.9 재인코딩")
        self.assertEqual(ev.option_label, "품질 23.9 + 1초 키프레임")

    def test_protected_route_shows_protect_quality(self) -> None:
        """저화질 보호 라우팅은 보호 품질값 27.9를 품질 27.9 재인코딩으로 표시함을 검증"""
        ev = make_eval(
            smart_action=SmartAction.ENCODE_LOW_KF,
            use_low_quality_preset=True,
            output_cq=protect_cq_value(23.9),
        )
        self.assertEqual(ev.action_label, "품질 27.9 재인코딩")

    def test_default_labels_follow_default_settings(self) -> None:
        """기본 설정의 열 필터 목록과 기본 명칭이 품질 22.0, 품질 26.0 표기임을 검증"""
        self.assertEqual(SmartAction.ENCODE_OVER.action_label, "품질 22.0 재인코딩")
        self.assertEqual(SmartAction.ENCODE_LOW_KF.action_label, "품질 26.0 재인코딩")
        self.assertEqual(SmartAction.ENCODE_DOWNSCALE.action_label, "축소 재인코딩")


if __name__ == "__main__":
    unittest.main(verbosity=2)
