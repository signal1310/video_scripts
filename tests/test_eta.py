import unittest

from src.core.eta import (
    MIN_SAMPLE_COUNT,
    ProgressEstimator,
    format_approx_duration,
    format_estimate_label,
    format_rate,
    format_remaining,
)


class _FakeClock:
    """테스트에서 시각을 직접 진행시키는 가짜 시계"""

    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now


class TestProgressEstimator(unittest.TestCase):
    """표본 부족 구간 보류와 누적 평균 속도 기반 남은 시간 계산을 검증"""

    def setUp(self) -> None:
        self.clock = _FakeClock()
        self.estimator = ProgressEstimator(clock=self.clock)

    def test_estimate_withheld_until_min_sample_count(self) -> None:
        self.clock.now = 10.0
        self.estimator.estimate(0, 100)
        self.clock.now = 20.0
        result = self.estimator.estimate(MIN_SAMPLE_COUNT - 1, 100)
        self.assertIsNone(result.rate_per_sec)
        self.assertIsNone(result.remaining_sec)
        self.assertEqual(result.done, MIN_SAMPLE_COUNT - 1)

    def test_estimate_withheld_until_min_elapsed(self) -> None:
        self.clock.now = 0.0
        self.estimator.estimate(0, 100)
        self.clock.now = 1.0
        result = self.estimator.estimate(MIN_SAMPLE_COUNT, 100)
        self.assertIsNone(result.remaining_sec)

    def test_remaining_from_rate_since_anchor(self) -> None:
        # 기준점(0초, 0개) 이후 10초에 20개 완료 -> 2개/초, 남은 80개는 40초
        self.clock.now = 0.0
        self.estimator.estimate(0, 100)
        self.clock.now = 10.0
        result = self.estimator.estimate(20, 100)
        self.assertAlmostEqual(result.rate_per_sec or 0.0, 2.0)
        self.assertAlmostEqual(result.remaining_sec or 0.0, 40.0)

    def test_skipped_burst_before_anchor_does_not_inflate_rate(self) -> None:
        # 키프레임 보강은 건너뛴 40개를 시작 직후 한꺼번에 보고하므로 속도 계산에서 제외되어야 함
        self.clock.now = 0.0
        self.estimator.estimate(40, 100)
        self.clock.now = 10.0
        result = self.estimator.estimate(50, 100)
        self.assertAlmostEqual(result.rate_per_sec or 0.0, 1.0)
        self.assertAlmostEqual(result.remaining_sec or 0.0, 50.0)

    def test_remaining_zero_when_done(self) -> None:
        self.clock.now = 0.0
        self.estimator.estimate(0, 50)
        self.clock.now = 10.0
        result = self.estimator.estimate(50, 50)
        self.assertEqual(result.remaining_sec, 0.0)

    def test_elapsed_counts_from_start(self) -> None:
        self.clock.now = 100.0
        self.estimator.start()
        self.clock.now = 110.0
        result = self.estimator.estimate(0, 40)
        self.assertAlmostEqual(result.elapsed_sec, 10.0)

    def test_seconds_per_item_excludes_anchor_items(self) -> None:
        self.clock.now = 0.0
        self.estimator.estimate(10, 100)
        self.clock.now = 30.0
        # 기준점 이전 10개는 시간과 항목 수 모두에서 제외
        self.assertAlmostEqual(self.estimator.seconds_per_item(100) or 0.0, 30.0 / 90)

    def test_seconds_per_item_none_without_anchor(self) -> None:
        self.assertIsNone(self.estimator.seconds_per_item(10))


class TestApproxFormatting(unittest.TestCase):
    """추정 시간과 속도 표기의 반올림 경계를 검증"""

    def test_seconds_rounded_up_to_step(self) -> None:
        cases = [(0.0, "약 5초"), (1.0, "약 5초"), (5.0, "약 5초"), (5.1, "약 10초"), (29.3, "약 30초"), ]
        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(format_approx_duration(seconds), expected)

    def test_minutes_use_korean_summary(self) -> None:
        self.assertEqual(format_approx_duration(59.9), "약 1분")
        self.assertEqual(format_approx_duration(60.0), "약 1분")
        self.assertEqual(format_approx_duration(3700.0), "약 1시간 1분")

    def test_rate_one_decimal(self) -> None:
        self.assertEqual(format_rate(1.84), "1.8개/초")
        self.assertEqual(format_rate(2.0), "2.0개/초")

    def test_remaining_text_states(self) -> None:
        self.assertEqual(format_remaining(None), "남은 시간 계산 중")
        self.assertEqual(format_remaining(28.0), "약 30초 남음")

    def test_estimate_label_states(self) -> None:
        self.assertEqual(format_estimate_label(0, 0.0), "분석할 영상 없음")
        self.assertEqual(format_estimate_label(0, None), "분석할 영상 없음")
        self.assertEqual(format_estimate_label(31, None), "31개, 측정 전")
        self.assertEqual(format_estimate_label(1200, 65.0), "1,200개, 약 1분")


if __name__ == "__main__":
    unittest.main()
