"""진행 창의 진행 비율, 남은 시간, 표기 문자열과 순간 비트레이트 기록을 검증"""

import unittest

from src.core.encode.encode_progress_view import (
    NO_VALUE_TEXT,
    SERIES_CAPACITY,
    BitrateSeries,
    SeriesPoint,
    clock_text,
    kbps_text,
    moving_average,
    overall_remaining_sec,
    point_label,
    progress_fraction,
    remaining_sec,
    run_counts,
    speed_text,
    value_stats,
)
from src.core.encode.encode_state import EncodeState


class ProgressEstimateTests(unittest.TestCase):
    """진행 비율과 남은 시간 추정이 측정 불가 값과 끝 값을 안전하게 처리하는지 검증"""

    def test_fraction_is_clamped_and_missing_inputs_give_none(self) -> None:
        """출력 위치를 원본 길이로 나눈 비율이 0~1로 잘리고, 값이 없으면 없음인지 검증"""
        self.assertEqual(progress_fraction(30.0, 60.0), 0.5)
        self.assertEqual(progress_fraction(90.0, 60.0), 1.0)
        self.assertEqual(progress_fraction(-1.0, 60.0), 0.0)
        self.assertIsNone(progress_fraction(None, 60.0))
        self.assertIsNone(progress_fraction(30.0, 0.0))

    def test_remaining_time_follows_elapsed_and_fraction(self) -> None:
        """경과 시간과 진행 비율로 남은 시간을 추정하고, 진행이 없으면 없음인지 검증"""
        self.assertEqual(remaining_sec(60.0, 0.25), 180.0)
        self.assertEqual(remaining_sec(60.0, 1.0), 0.0)
        self.assertIsNone(remaining_sec(60.0, None))
        self.assertIsNone(remaining_sec(60.0, 0.0))
        self.assertIsNone(remaining_sec(0.0, 0.5))

    def test_overall_remaining_uses_the_average_of_processed_items(self) -> None:
        """처리한 항목의 평균 시간으로 전체 남은 시간을 추정하고, 처리 전이나 끝이면 값을 맞추는지 검증"""
        self.assertEqual(overall_remaining_sec(100.0, 2, 6), 200.0)
        half_done = overall_remaining_sec(100.0, 1.5, 2)
        assert half_done is not None
        self.assertAlmostEqual(half_done, 100.0 / 3)
        self.assertEqual(overall_remaining_sec(100.0, 6, 6), 0.0)
        self.assertIsNone(overall_remaining_sec(100.0, 0, 6))
        self.assertIsNone(overall_remaining_sec(100.0, 2, 0))


class DisplayTextTests(unittest.TestCase):
    """표기 문자열이 승인된 형식이고 값이 없으면 정보 없음을 쓰는지 검증"""

    def test_speed_and_bitrate_use_the_approved_formats(self) -> None:
        """인코딩 속도는 소수 둘째 자리 배율, 비트레이트는 정수 kbps로 나오는지 검증"""
        self.assertEqual(speed_text(1.2456), "1.25배")
        self.assertEqual(kbps_text(2232.8), "2233kbps")
        self.assertEqual(speed_text(None), NO_VALUE_TEXT)
        self.assertEqual(kbps_text(None), NO_VALUE_TEXT)

    def test_clock_text_formats_seconds_and_missing_value(self) -> None:
        """시각 문자열이 초를 시분초 형식으로 바꾸고 값이 없으면 정보 없음인지 검증"""
        self.assertEqual(clock_text(193.0), "03:13")
        self.assertEqual(clock_text(None), NO_VALUE_TEXT)

    def test_point_label_shows_time_and_value_paused_span_or_no_value(self) -> None:
        """툴팁 문장이 시각과 값을 함께 보이고, 일시중지 지점은 구간 이름, 잴 수 없는 점은 정보 없음으로 나오는지 검증"""
        self.assertEqual(point_label(SeriesPoint(72.0, 2950.0)), "01:12, 2950kbps")
        self.assertEqual(point_label(SeriesPoint(150.0, None, paused=True)), "02:30, 일시중지 구간")
        self.assertEqual(point_label(SeriesPoint(150.0, None)), "02:30, 정보 없음")


class BitrateSeriesTests(unittest.TestCase):
    """실시간 비트레이트 기록이 일시중지 지점을 합치고 용량을 넘으면 오래된 점부터 버리는지 검증"""

    def test_consecutive_pause_marks_collapse_into_one(self) -> None:
        """일시중지 지점이 연속되면 하나만 남고, 잴 수 없는 점 뒤의 일시중지는 따로 기록되는지 검증"""
        series = BitrateSeries()
        series.add_sample(0.0, 2000.0)
        series.add_pause(3.0)
        series.add_pause(4.0)
        series.add_sample(9.0, None)
        series.add_pause(10.0)
        self.assertEqual(
            series.points(),
            (
                SeriesPoint(0.0, 2000.0),
                SeriesPoint(3.0, None, paused=True),
                SeriesPoint(9.0, None),
                SeriesPoint(10.0, None, paused=True),
            ),
        )

    def test_oldest_points_are_dropped_when_capacity_is_reached(self) -> None:
        """용량을 넘는 점은 가장 오래된 것부터 버려지는지 검증"""
        series = BitrateSeries(capacity=2)
        series.add_sample(0.0, 1.0)
        series.add_sample(1.0, 2.0)
        series.add_sample(2.0, 3.0)
        self.assertEqual(series.points(), (SeriesPoint(1.0, 2.0), SeriesPoint(2.0, 3.0)))

    def test_default_capacity_is_the_module_constant(self) -> None:
        """기본 용량이 공개 상수와 같은지 검증"""
        series = BitrateSeries()
        for index in range(SERIES_CAPACITY + 5):
            series.add_sample(float(index), 1000.0)
        self.assertEqual(len(series.points()), SERIES_CAPACITY)


class SmoothingAndStatsTests(unittest.TestCase):
    """이동 평균이 창 안의 값만 평균하고 일시중지에서 창을 비우며, 평균과 최고값이 값 점만 보는지 검증"""

    def test_moving_average_averages_only_values_inside_the_window(self) -> None:
        """각 값 점의 평균이 그 시각 앞 창 안의 값으로만 계산되는지 검증"""
        points = (
            SeriesPoint(0.0, 1000.0),
            SeriesPoint(1.0, 2000.0),
            SeriesPoint(2.0, 3000.0),
            SeriesPoint(6.0, 9000.0),
        )
        smoothed = moving_average(points, 2.0)
        self.assertEqual([p.kbps for p in smoothed], [1000.0, 1500.0, 2500.0, 9000.0])

    def test_pause_point_stays_and_restarts_the_average(self) -> None:
        """일시중지 지점은 그대로 남고, 이후 평균이 정지 전 값을 섞지 않는지 검증"""
        points = (SeriesPoint(0.0, 1000.0), SeriesPoint(1.0, None, paused=True), SeriesPoint(2.0, 3000.0))
        smoothed = moving_average(points, 10.0)
        self.assertEqual(
            smoothed,
            (SeriesPoint(0.0, 1000.0), SeriesPoint(1.0, None, paused=True), SeriesPoint(2.0, 3000.0)),
        )

    def test_unmeasured_point_keeps_the_window_and_stays_empty(self) -> None:
        """잴 수 없는 점은 값 없이 자리만 남고, 앞뒤 값의 평균 창은 비우지 않는지 검증"""
        points = (SeriesPoint(0.0, 1000.0), SeriesPoint(1.0, None), SeriesPoint(2.0, 3000.0))
        smoothed = moving_average(points, 10.0)
        self.assertEqual(smoothed, (SeriesPoint(0.0, 1000.0), SeriesPoint(1.0, None), SeriesPoint(2.0, 2000.0)))

    def test_value_stats_uses_values_only(self) -> None:
        """평균과 최고값이 일시중지 지점과 잴 수 없는 점을 빼고 계산되고, 값이 없으면 없음인지 검증"""
        self.assertEqual(value_stats((SeriesPoint(0.0, 1000.0), SeriesPoint(1.0, None), SeriesPoint(2.0, 3000.0))), (2000.0, 3000.0))
        self.assertEqual(value_stats((SeriesPoint(0.0, None),)), (None, None))


class RunCountsTests(unittest.TestCase):
    """이번 실행의 대상과 끝난 수: 건너뜀과 인코딩을 시작하지 않고 끝난 항목은 두 값에서 모두 빠지고, 인코딩을 거친 상태만 끝난 수로 셈"""

    def test_skipped_items_leave_both_counts_and_encoded_states_count_as_done(self) -> None:
        items = [
            (EncodeState.DONE, True),
            (EncodeState.SKIPPED, False),
            (EncodeState.DISCARDED, True),
            (EncodeState.REPLACE_PENDING, True),
            (EncodeState.RUNNING, True),
            (EncodeState.PENDING, False),
        ]
        self.assertEqual(run_counts(items), (5, 3))

    def test_item_that_ended_without_starting_leaves_both_counts(self) -> None:
        items = [
            (EncodeState.FAILED, False),
            (EncodeState.DONE, True),
            (EncodeState.RUNNING, True),
            (EncodeState.PENDING, False),
        ]
        self.assertEqual(run_counts(items), (3, 1))

    def test_no_items_gives_zero_counts(self) -> None:
        self.assertEqual(run_counts([]), (0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
