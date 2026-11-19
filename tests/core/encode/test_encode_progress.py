"""FFmpeg 진행 출력 파싱, 순간 비트레이트, 예상 최종 크기, 일시중지 제외 경과 시간, 장시간 안내 기준을 검증하는 단위 테스트"""

import unittest

from src.core.encode.encode_progress import (
    EARLY_STOP_MARGIN,
    EARLY_STOP_MIN_PROGRESS,
    EARLY_STOP_RECHECK_SEC,
    LONG_RUN_NOTICE_SEC,
    EarlyStopJudge,
    InstantBitrateTracker,
    PausableClock,
    ProgressParser,
    ProgressSample,
    needs_long_run_notice,
    progress_ratio,
    projected_output_bytes,
    should_emit_progress,
)

_BLOCK = """frame=120
fps=60.00
stream_0_0_q=23.9
bitrate=4000.0kbits/s
total_size=1000000
out_time_us=2000000
speed=2.5x
progress=continue
"""


class TestProgressParsing(unittest.TestCase):
    """진행 블록 파싱과 측정 불가 값 처리"""

    def test_block_is_parsed_when_progress_line_arrives(self) -> None:
        """progress 줄이 올 때 블록 값이 샘플로 만들어지고 continue는 진행 중임을 검증"""
        parser = ProgressParser()
        sample = None
        for line in _BLOCK.splitlines():
            sample = parser.feed(line) or sample
        assert sample is not None
        self.assertEqual(sample.frame, 120)
        self.assertEqual(sample.quality, 23.9)
        self.assertEqual(sample.bitrate_kbps, 4000.0)
        self.assertEqual(sample.total_size_bytes, 1_000_000)
        self.assertAlmostEqual(sample.out_time_sec or 0.0, 2.0)
        self.assertEqual(sample.speed, 2.5)
        self.assertFalse(sample.finished)

    def test_not_available_values_become_none_and_end_marks_finished(self) -> None:
        """N/A 값은 None이 되고 progress=end는 종료 샘플로 표시됨을 검증"""
        parser = ProgressParser()
        parser.feed("bitrate=N/A")
        parser.feed("speed=N/A")
        sample = parser.feed("progress=end")
        assert sample is not None
        self.assertIsNone(sample.bitrate_kbps)
        self.assertIsNone(sample.speed)
        self.assertTrue(sample.finished)

    def test_lines_without_equal_sign_are_ignored(self) -> None:
        """등호가 없는 줄은 블록에 들어가지 않고 샘플도 만들지 않음을 검증"""
        self.assertIsNone(ProgressParser().feed("garbage line"))


class TestDerivedValues(unittest.TestCase):
    """순간 비트레이트, 예상 최종 크기, 일시중지 제외 경과 시간"""

    def test_instant_bitrate_needs_a_full_window_then_reports_kbps(self) -> None:
        """1초 창이 채워지기 전에는 None이고, 이후에는 증가 크기로 kbps를 계산함을 검증"""
        tracker = InstantBitrateTracker(window_sec=1.0)
        self.assertIsNone(tracker.add(0.0, 0))
        self.assertIsNone(tracker.add(0.5, 62_500))
        kbps = tracker.add(1.0, 125_000)
        assert kbps is not None
        # - 1초 동안 125,000바이트 = 1,000,000비트 = 1000kbps
        self.assertAlmostEqual(kbps, 1000.0)

    def test_projected_size_scales_by_progress(self) -> None:
        """진행률 절반에서 누적 크기의 두 배가 예상 최종 크기임을 검증"""
        parser = ProgressParser()
        sample = None
        for line in ("total_size=500", "out_time_us=5000000", "progress=continue"):
            sample = parser.feed(line) or sample
        assert sample is not None
        self.assertEqual(projected_output_bytes(sample, 10.0), 1000)
        self.assertIsNone(projected_output_bytes(sample, 0.0))

    def test_paused_time_is_excluded_from_elapsed(self) -> None:
        """일시중지 구간은 경과 시간에서 빠지고 재개 후에는 다시 흐름을 검증"""
        clock = PausableClock(now=100.0)
        clock.pause(now=110.0)
        self.assertAlmostEqual(clock.elapsed(now=130.0), 10.0)
        clock.resume(now=130.0)
        self.assertAlmostEqual(clock.elapsed(now=140.0), 20.0)

    def test_current_pause_seconds_is_zero_while_running(self) -> None:
        """재생 중이거나 재개한 뒤에는 현재 일시중지 지속 시간이 0"""
        clock = PausableClock(now=100.0)
        self.assertEqual(clock.current_pause_sec(now=150.0), 0.0)
        clock.pause(now=110.0)
        clock.resume(now=130.0)
        self.assertEqual(clock.current_pause_sec(now=140.0), 0.0)

    def test_current_pause_seconds_counts_only_the_running_pause(self) -> None:
        """이전 일시중지 구간은 빼고 지금 멈춘 구간의 길이만 셈"""
        clock = PausableClock(now=100.0)
        clock.pause(now=110.0)
        clock.resume(now=300.0)
        clock.pause(now=400.0)
        self.assertAlmostEqual(clock.current_pause_sec(now=430.0), 30.0)

    def test_long_run_notice_is_based_on_current_pause_not_elapsed_time(self) -> None:
        """안내는 현재 일시중지 지속 시간이 10분을 넘을 때만 나오며, 재생 시간이 길어도 안내하지 않음"""
        self.assertFalse(needs_long_run_notice(LONG_RUN_NOTICE_SEC))
        self.assertTrue(needs_long_run_notice(LONG_RUN_NOTICE_SEC + 0.1))
        clock = PausableClock(now=0.0)
        clock.pause(now=LONG_RUN_NOTICE_SEC * 3)
        clock.resume(now=LONG_RUN_NOTICE_SEC * 3 + 60.0)
        self.assertFalse(needs_long_run_notice(clock.current_pause_sec(now=LONG_RUN_NOTICE_SEC * 3 + 60.0)))

    def test_progress_emit_is_throttled_to_half_second(self) -> None:
        """첫 샘플은 보내고 0.5초 안에는 다시 보내지 않으며 이후에는 보냄을 검증"""
        self.assertTrue(should_emit_progress(10.0, None))
        self.assertFalse(should_emit_progress(10.3, 10.0))
        self.assertTrue(should_emit_progress(10.5, 10.0))


class TestProgressRatioAndEarlyStop(unittest.TestCase):
    """out_time 기준 진행 비율과 조기 중단 판정의 경계를 검증"""

    @staticmethod
    def _sample(*, out_time: float | None = None, frame: int | None = None, size: int | None = 1000) -> ProgressSample:
        return ProgressSample(
            frame=frame,
            fps=None,
            quality=None,
            bitrate_kbps=None,
            total_size_bytes=size,
            out_time_sec=out_time,
            speed=None,
            finished=False,
        )

    def test_progress_ratio_uses_out_time_and_is_clamped(self) -> None:
        """진행 비율은 out_time / 길이이고 1을 넘지 않음"""
        ratio = progress_ratio(self._sample(out_time=5.0), 10.0)
        assert ratio is not None
        self.assertAlmostEqual(ratio, 0.5)
        self.assertEqual(progress_ratio(self._sample(out_time=20.0), 10.0), 1.0)

    def test_frame_count_never_stands_in_for_out_time(self) -> None:
        """out_time이 N/A이면 프레임 수가 있어도 진행 비율과 추정 크기를 만들지 않음"""
        sample = self._sample(frame=900, size=500)
        self.assertIsNone(progress_ratio(sample, 10.0))
        self.assertIsNone(projected_output_bytes(sample, 10.0))
        judge = EarlyStopJudge(source_size=1000, duration_sec=10.0)
        self.assertIsNone(judge.observe(sample, 1.0))

    def test_no_progress_when_out_time_or_duration_is_not_usable(self) -> None:
        """out_time이 0 이하이거나 길이가 0 이하이면 진행 비율을 모름"""
        self.assertIsNone(progress_ratio(self._sample(out_time=0.0), 10.0))
        self.assertIsNone(progress_ratio(self._sample(out_time=5.0), 0.0))
        self.assertIsNone(projected_output_bytes(self._sample(out_time=5.0), 0.0))

    def test_projected_size_scales_by_progress(self) -> None:
        """진행 비율 절반에서 누적 크기의 두 배가 예상 최종 크기"""
        self.assertEqual(projected_output_bytes(self._sample(out_time=5.0, size=500), 10.0), 1000)

    def test_default_constants_match_agreed_values(self) -> None:
        """승인된 조기 중단 상수(최소 진행 0.10, 여유 배수 1.20, 재확인 1.0초)"""
        self.assertEqual((EARLY_STOP_MIN_PROGRESS, EARLY_STOP_MARGIN, EARLY_STOP_RECHECK_SEC), (0.10, 1.20, 1.0))

    def test_judge_stops_when_estimate_reaches_margin_exactly(self) -> None:
        """추정 크기가 원본 x 여유 배수와 같으면 중단(같을 때 포함)"""
        judge = EarlyStopJudge(source_size=1000, duration_sec=10.0)
        hit = judge.observe(self._sample(out_time=1.0, size=120), 1.0)
        assert hit is not None
        self.assertEqual(hit.estimate_bytes, 1200)
        self.assertAlmostEqual(hit.progress, 0.1)

    def test_judge_ignores_progress_below_minimum_and_estimate_below_margin(self) -> None:
        """최소 진행 비율 미만이거나 추정 크기가 여유 배수보다 작으면 중단하지 않음"""
        below_progress = EarlyStopJudge(source_size=1000, duration_sec=10.0)
        self.assertIsNone(below_progress.observe(self._sample(out_time=0.5, size=100), 1.0))
        below_margin = EarlyStopJudge(source_size=1000, duration_sec=10.0)
        self.assertIsNone(below_margin.observe(self._sample(out_time=1.0, size=119), 1.0))

    def test_judge_checks_only_at_recheck_interval(self) -> None:
        """재확인 간격 안의 샘플은 조건을 만족해도 판정하지 않고, 간격이 지나면 판정"""
        judge = EarlyStopJudge(source_size=1000, duration_sec=10.0)
        self.assertIsNone(judge.observe(self._sample(out_time=1.0, size=100), 0.0))
        self.assertIsNone(judge.observe(self._sample(out_time=1.0, size=500), 0.5))
        self.assertIsNotNone(judge.observe(self._sample(out_time=1.0, size=500), 1.0))

    def test_judge_parameters_override_defaults(self) -> None:
        """측정 격자 실행을 위해 상수 대신 인자로 기준을 바꿀 수 있음"""
        judge = EarlyStopJudge(source_size=1000, duration_sec=10.0, min_progress=0.5, margin=1.0, recheck_sec=2.0)
        self.assertIsNone(judge.observe(self._sample(out_time=1.0, size=5000), 0.0))
        self.assertIsNone(judge.observe(self._sample(out_time=5.0, size=5000), 1.0))
        self.assertIsNotNone(judge.observe(self._sample(out_time=5.0, size=5000), 2.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
