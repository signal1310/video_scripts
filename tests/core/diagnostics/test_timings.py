"""단계 소요 측정: 주입 시계로 경계 동작과 DEBUG 기록 검증"""

import logging
import unittest

from src.core.diagnostics.timings import TIMING_LOG_TEMPLATE, StageClock

LOGGER_NAME = "src.diagnostics.timings.tests"


def _enabled() -> bool:
    """기록을 남기는 측정 조건"""
    return True


class _FakeClock:
    """호출할 때마다 지정한 값을 차례로 돌려주는 시계 대역"""

    def __init__(self, values: list[float]) -> None:
        self._values = values
        self._index = 0

    def __call__(self) -> float:
        value = self._values[min(self._index, len(self._values) - 1)]
        self._index += 1
        return value


class StageClockTests(unittest.TestCase):
    """단계 전환과 마감이 소요를 한 번씩 남기는지 검증"""

    def setUp(self) -> None:
        self.logger = logging.getLogger(LOGGER_NAME)

    def test_first_stage_starts_without_a_finished_duration(self) -> None:
        """첫 단계는 마감할 이전 구간이 없어 None을 돌려주는지 검증"""
        clock = _FakeClock([10.0])
        stage_clock = StageClock(clock=clock, logger=self.logger, enabled=_enabled)
        with self.assertNoLogs(self.logger, level="DEBUG"):
            self.assertIsNone(stage_clock.observe("scan"))
        self.assertEqual(stage_clock.current_stage, "scan")

    def test_stage_change_logs_previous_duration(self) -> None:
        """단계가 바뀌면 이전 단계 소요를 DEBUG로 남기고 경과 초를 돌려주는지 검증"""
        stage_clock = StageClock(
            clock=_FakeClock([1.0, 2.5]), logger=self.logger, enabled=_enabled
        )
        stage_clock.observe("scan")
        with self.assertLogs(self.logger, level="DEBUG") as logs:
            elapsed = stage_clock.observe("ssim")
        self.assertAlmostEqual(elapsed or 0.0, 1.5)
        self.assertIn(
            TIMING_LOG_TEMPLATE.format(stage="scan", elapsed=1.5),
            "\n".join(logs.output),
        )
        self.assertEqual(stage_clock.current_stage, "ssim")

    def test_same_stage_repeated_does_not_log_and_keeps_the_start(self) -> None:
        """같은 단계가 거듭 와도 기록하지 않고 처음 시작 시각을 유지하는지 검증"""
        stage_clock = StageClock(
            clock=_FakeClock([1.0, 4.0]), logger=self.logger, enabled=_enabled
        )
        stage_clock.observe("scan")
        with self.assertNoLogs(self.logger, level="DEBUG"):
            self.assertIsNone(stage_clock.observe("scan"))
        self.assertEqual(stage_clock.current_stage, "scan")
        with self.assertLogs(self.logger, level="DEBUG") as logs:
            elapsed = stage_clock.observe("ssim")
        self.assertAlmostEqual(elapsed or 0.0, 3.0)
        self.assertIn(TIMING_LOG_TEMPLATE.format(stage="scan", elapsed=3.0), "\n".join(logs.output))

    def test_empty_stage_name_without_a_measurement_is_silent(self) -> None:
        """측정 중이 아닐 때 빈 단계 이름이 와도 기록하지 않는지 검증"""
        stage_clock = StageClock(clock=_FakeClock([1.0]), logger=self.logger, enabled=_enabled)
        with self.assertNoLogs(self.logger, level="DEBUG"):
            self.assertIsNone(stage_clock.observe(""))
        self.assertIsNone(stage_clock.current_stage)

    def test_finish_logs_and_clears_the_current_stage(self) -> None:
        """마감하면 남은 단계 소요를 남기고 측정 상태를 비우는지 검증"""
        stage_clock = StageClock(
            clock=_FakeClock([0.0, 0.75]), logger=self.logger, enabled=_enabled
        )
        stage_clock.observe("vmaf")
        with self.assertLogs(self.logger, level="DEBUG") as logs:
            self.assertAlmostEqual(stage_clock.finish() or 0.0, 0.75)
        self.assertIn("vmaf", "\n".join(logs.output))
        self.assertIsNone(stage_clock.current_stage)

    def test_finish_without_a_stage_is_silent(self) -> None:
        """측정 중인 단계가 없으면 마감이 아무 기록도 남기지 않는지 검증"""
        stage_clock = StageClock(clock=_FakeClock([5.0]), logger=self.logger, enabled=_enabled)
        with self.assertNoLogs(self.logger, level="DEBUG"):
            self.assertIsNone(stage_clock.finish())

    def test_empty_stage_name_clears_measurement(self) -> None:
        """빈 단계 이름은 측정 중이 아님으로 다루는지 검증"""
        stage_clock = StageClock(
            clock=_FakeClock([1.0, 2.0]), logger=self.logger, enabled=_enabled
        )
        stage_clock.observe("scan")
        with self.assertLogs(self.logger, level="DEBUG"):
            stage_clock.observe("")
        self.assertIsNone(stage_clock.current_stage)

    def test_clock_going_backwards_never_reports_negative_time(self) -> None:
        """시계가 뒤로 가도 음수 소요를 남기지 않는지 검증"""
        stage_clock = StageClock(
            clock=_FakeClock([5.0, 1.0]), logger=self.logger, enabled=_enabled
        )
        stage_clock.observe("scan")
        with self.assertLogs(self.logger, level="DEBUG") as logs:
            self.assertEqual(stage_clock.finish(), 0.0)
        self.assertIn("0.000", "\n".join(logs.output))

    def test_enabled_keyword_is_required(self) -> None:
        """기록 조건을 빠뜨리면 기본값으로 켜지지 않고 TypeError가 나는지 검증"""
        with self.assertRaises(TypeError):
            StageClock(clock=_FakeClock([1.0]), logger=self.logger)  # pyright: ignore[reportCallIssue]

    def test_silent_when_disabled(self) -> None:
        """기록 조건이 거짓이면 소요를 계산만 하고 DEBUG를 남기지 않는지 검증"""
        stage_clock = StageClock(
            clock=_FakeClock([1.0, 2.5]), logger=self.logger, enabled=lambda: False
        )
        stage_clock.observe("scan")
        with self.assertNoLogs(self.logger, level="DEBUG"):
            elapsed = stage_clock.observe("ssim")
        self.assertAlmostEqual(elapsed or 0.0, 1.5)
        self.assertEqual(stage_clock.current_stage, "ssim")


if __name__ == "__main__":
    unittest.main()
