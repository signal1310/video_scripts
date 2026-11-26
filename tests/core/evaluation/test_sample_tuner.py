"""품질값 정밀 결정 이진 탐색과 축소 비교 순수 규칙 검증(결정 18)

- 단조 감소 합성 측정 곡선으로 수렴, 실패 전량, 통과 전량, 정수 눈금, 점수 없음 경로를 확인
"""

import unittest

from src.core.evaluation.sample_tuner import (
    COMPARE_CANDIDATE_SMALLER,
    REASON_CONVERGED,
    REASON_FALLBACK_BASELINE,
    REASON_UNMEASURABLE,
    TUNE_MAX_PROBES,
    DownscaleComparison,
    QualityProbe,
    TuneTarget,
    compare_downscale,
    format_comparison_reason,
    format_result_reason,
    probe_passes,
    quality_granularity,
    relative_bitrate_saving_percent,
    search_quality,
    tune_bounds,
)


def _curve(vmaf_at_baseline: float, slope: float, *, baseline: float = 22.0):
    """품질값이 오르면 VMAF가 단조 감소하는 합성 측정 함수"""

    def _measure(quality: float) -> QualityProbe:
        vmaf = vmaf_at_baseline - slope * (quality - baseline)
        return QualityProbe(quality=quality, vmaf=vmaf, ssim=0.999, bitrate_kbps=4000.0)

    return _measure


class TestSearchQuality(unittest.TestCase):
    """이진 탐색의 수렴, 상한, 실패 전량, 점수 없음 처리"""

    def test_converges_within_four_probes(self) -> None:
        calls: list[float] = []

        def _measure(quality: float) -> QualityProbe:
            calls.append(quality)
            vmaf = 99.5 - 2.0 * (quality - 22.0)
            return QualityProbe(quality=quality, vmaf=vmaf, ssim=0.999, bitrate_kbps=4000.0)

        low, high = tune_bounds(22.0, delta_max=6.0)
        result = search_quality(_measure, low, high)
        self.assertTrue(result.converged)
        self.assertEqual(result.reason, REASON_CONVERGED)
        self.assertLessEqual(len(calls), TUNE_MAX_PROBES)
        self.assertLessEqual(abs(result.best_quality - 22.75), 0.4)
        self.assertTrue(probe_passes(result.best_probe, TuneTarget()))

    def test_failed_probe_is_never_returned(self) -> None:
        result = search_quality(_curve(99.0, 2.0), 22.0, 28.0)
        self.assertTrue(result.converged)
        for probe in result.probes:
            if probe.quality == result.best_quality:
                self.assertTrue(probe_passes(probe, TuneTarget()))
        self.assertLess(result.best_quality, 22.5)

    def test_fails_all_returns_baseline_without_convergence(self) -> None:
        result = search_quality(_curve(50.0, 1.0), 22.0, 28.0)
        self.assertFalse(result.converged)
        self.assertEqual(result.reason, REASON_FALLBACK_BASELINE)
        self.assertEqual(result.best_quality, 22.0)

    def test_passes_all_returns_high_bound(self) -> None:
        result = search_quality(_curve(100.0, 0.1), 22.0, 28.0)
        self.assertTrue(result.converged)
        self.assertEqual(result.best_quality, 28.0)
        self.assertEqual(len(result.probes), 2)

    def test_integer_scale_stops_at_whole_value(self) -> None:
        result = search_quality(_curve(99.9, 0.6), 20.0, 26.0, integer_scale=True)
        self.assertTrue(result.converged)
        self.assertEqual(result.best_quality, float(int(result.best_quality)))
        self.assertTrue(all(probe.quality == int(probe.quality) for probe in result.probes))
        self.assertEqual(quality_granularity(True), 1.0)
        self.assertEqual(quality_granularity(False), 0.1)

    def test_granularity_limit_stops_early(self) -> None:
        result = search_quality(_curve(99.0, 2.0), 22.0, 22.6)
        self.assertTrue(result.converged)
        self.assertLessEqual(len(result.probes), 3)
        self.assertGreaterEqual(result.best_quality, 22.5)

    def test_none_scores_are_unmeasurable(self) -> None:
        result = search_quality(lambda quality: None, 22.0, 28.0)
        self.assertFalse(result.converged)
        self.assertEqual(result.reason, REASON_UNMEASURABLE)
        self.assertEqual(result.best_quality, 22.0)
        self.assertFalse(any(probe.measurable for probe in result.probes))

    def test_missing_vmaf_uses_ssim_only(self) -> None:
        def _measure(quality: float) -> QualityProbe:
            ssim = 0.999 - 0.001 * (quality - 22.0)
            return QualityProbe(quality=quality, ssim=ssim, bitrate_kbps=1000.0)

        result = search_quality(_measure, 22.0, 28.0)
        self.assertTrue(result.converged)
        self.assertEqual(result.best_quality, 25.0)

    def test_measure_exception_is_treated_as_failure(self) -> None:
        def _measure(_quality: float) -> QualityProbe:
            raise RuntimeError("샘플 인코딩 실패")

        result = search_quality(_measure, 22.0, 28.0)
        self.assertFalse(result.converged)
        self.assertEqual(result.reason, REASON_UNMEASURABLE)

    def test_max_probes_caps_measurement_count(self) -> None:
        calls: list[float] = []

        def _measure(quality: float) -> QualityProbe:
            calls.append(quality)
            return QualityProbe(quality=quality, vmaf=99.0 - 3.0 * (quality - 22.0))

        search_quality(_measure, 22.0, 28.0, max_probes=2)
        self.assertEqual(len(calls), 2)

    def test_bounds_follow_protect_limit(self) -> None:
        self.assertEqual(tune_bounds(21.5, delta_max=4.0), (21.5, 25.5))


class TestCompareDownscale(unittest.TestCase):
    """축소 후보와 원본 규격의 승자 판정 표"""

    def _probe(self, vmaf: float | None, ssim: float | None, kbps: float) -> QualityProbe:
        return QualityProbe(quality=22.0, vmaf=vmaf, ssim=ssim, bitrate_kbps=kbps)

    def test_table(self) -> None:
        cases = (
            # (후보 통과, 후보 kbps, 원본 통과, 원본 kbps, 축소 여부, 사유)
            (True, 900.0, True, 1200.0, True, "candidate_smaller"),
            (True, 900.0, True, 900.0, False, "original_not_larger"),
            (True, 1500.0, True, 1200.0, False, "original_not_larger"),
            (True, 900.0, False, 1200.0, True, "candidate_only_pass"),
            (False, 150.0, True, 1200.0, False, "candidate_failed"),
            (False, 150.0, False, 1200.0, False, "candidate_failed"),
        )
        for candidate_ok, candidate_kbps, original_ok, original_kbps, expected, reason in cases:
            with self.subTest(candidate_ok=candidate_ok, original_ok=original_ok):
                candidate = self._probe(99.0 if candidate_ok else 90.0, 0.999, candidate_kbps)
                original = self._probe(99.0 if original_ok else 90.0, 0.999, original_kbps)
                comparison = compare_downscale(candidate, original)
                self.assertEqual(comparison.use_downscale, expected)
                self.assertEqual(comparison.reason, reason)

    def test_ssim_below_target_fails_even_with_high_vmaf(self) -> None:
        probe = self._probe(99.9, 0.990, 800.0)
        self.assertFalse(probe_passes(probe, TuneTarget()))
        self.assertFalse(compare_downscale(probe, self._probe(99.0, 0.999, 1200.0)).use_downscale)

    def test_missing_vmaf_falls_back_to_ssim(self) -> None:
        probe = self._probe(None, 0.999, 800.0)
        self.assertTrue(probe_passes(probe, TuneTarget()))
        self.assertTrue(compare_downscale(probe, self._probe(None, 0.999, 1200.0)).use_downscale)

    def test_custom_target_thresholds_apply(self) -> None:
        target = TuneTarget(vmaf_min=95.0, ssim_min=0.99)
        probe = self._probe(95.5, 0.995, 800.0)
        self.assertTrue(probe_passes(probe, target))
        self.assertEqual(target.cache_key, "95.000/0.990000")


class TestFormatResultReason(unittest.TestCase):
    """결과 사유 문구가 고른 품질값과 사유 코드를 반영하는지 확인"""

    def test_converged_reason_counts_probes(self) -> None:
        result = search_quality(_curve(100.0, 0.1), 22.0, 28.0)
        self.assertEqual(format_result_reason(result), "품질값 28.0 확인 (정밀 시험 2회)")

    def test_fallback_and_unmeasurable_reasons(self) -> None:
        fallback = search_quality(_curve(50.0, 1.0), 22.0, 28.0)
        self.assertEqual(format_result_reason(fallback), "품질값 22.0 유지 (기준 미달)")
        unmeasurable = search_quality(lambda _quality: None, 22.0, 28.0)
        self.assertEqual(format_result_reason(unmeasurable), "품질값 22.0 유지 (화질 측정 불가)")


class TestFormatComparisonReason(unittest.TestCase):
    """축소 비교 사유 문구가 확정 해상도와 비트레이트 차이를 반영하는지 확인"""

    def _probe(self, kbps: float, *, vmaf: float = 99.0) -> QualityProbe:
        return QualityProbe(quality=22.0, vmaf=vmaf, ssim=0.999, bitrate_kbps=kbps)

    def test_original_kept_for_both_reasons(self) -> None:
        failed = compare_downscale(self._probe(100.0, vmaf=90.0), self._probe(1200.0))
        self.assertEqual(
            format_comparison_reason(failed),
            "품질값 정밀 결정: 원본 해상도 유지 (축소 시 화질 기준 미달)",
        )
        not_larger = compare_downscale(self._probe(1300.0), self._probe(1200.0))
        self.assertEqual(
            format_comparison_reason(not_larger),
            "품질값 정밀 결정: 원본 해상도 유지 (축소 이득 없음)",
        )

    def test_downscale_kept_reports_saving_percent(self) -> None:
        smaller = compare_downscale(self._probe(900.0), self._probe(1200.0))
        self.assertEqual(smaller.reason, COMPARE_CANDIDATE_SMALLER)
        self.assertEqual(
            format_comparison_reason(smaller),
            "품질값 정밀 결정: 축소 유지 (원본 해상도 대비 25% 작음)",
        )
        only_pass = compare_downscale(self._probe(900.0), self._probe(1200.0, vmaf=90.0))
        self.assertEqual(
            format_comparison_reason(only_pass),
            "품질값 정밀 결정: 축소 유지 (원본 해상도는 화질 기준 미달)",
        )

    def test_missing_reference_size_reason(self) -> None:
        unmeasurable = DownscaleComparison(
            False,
            QualityProbe(quality=22.0),
            QualityProbe(quality=26.0),
            REASON_UNMEASURABLE,
        )
        self.assertEqual(
            format_comparison_reason(unmeasurable),
            "품질값 정밀 결정: 원본 해상도 유지 (화질 측정 불가)",
        )

    def test_zero_original_bitrate_reports_zero_percent(self) -> None:
        comparison = compare_downscale(self._probe(900.0), self._probe(0.0))
        self.assertEqual(relative_bitrate_saving_percent(comparison), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
