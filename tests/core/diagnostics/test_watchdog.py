"""메인 스레드 멈춤 감시: 주입 시계로 임계값 경계와 1회 보고, 스레드 정지 검증"""

import threading
import time
import unittest
from unittest.mock import patch

from src.core.diagnostics.watchdog import (
    NO_STACK_TEXT,
    STALL_LOG_TEMPLATE,
    STALL_THRESHOLD_SEC,
    MainThreadWatchdog,
    StallDetector,
    default_sink,
    main_thread_stack,
)


class _FakeClock:
    """호출할 때마다 지정한 값을 차례로 돌려주는 시계 대역"""

    def __init__(self, values: list[float]) -> None:
        self._values = values
        self._index = 0

    def __call__(self) -> float:
        value = self._values[min(self._index, len(self._values) - 1)]
        self._index += 1
        return value


class StallDetectorTests(unittest.TestCase):
    """임계값 경계에서 멈춤을 한 번만 보고하는지 검증"""

    def setUp(self) -> None:
        self.reports: list[tuple[float, str]] = []

    def _detector(self, clock: _FakeClock, *, threshold: float = STALL_THRESHOLD_SEC) -> StallDetector:
        return StallDetector(
            threshold=threshold,
            clock=clock,
            stack_provider=lambda: "스택 내용",
            sink=lambda elapsed, stack: self.reports.append((elapsed, stack)),
        )

    def test_below_threshold_reports_nothing(self) -> None:
        """임계값보다 짧은 간격은 멈춤으로 보지 않는지 검증"""
        detector = self._detector(_FakeClock([0.0, 0.0, 0.3]))
        detector.beat()
        self.assertIsNone(detector.check())
        self.assertEqual(self.reports, [])

    def test_threshold_crossed_reports_once_with_elapsed_and_stack(self) -> None:
        """임계값을 넘기면 경과와 스택을 한 번만 남기는지 검증"""
        detector = self._detector(_FakeClock([0.0, 0.0, 0.8, 1.9]))
        detector.beat()
        first = detector.check()
        self.assertIsNotNone(first)
        assert first is not None
        self.assertIn("0.80", first)
        self.assertIn("스택 내용", first)
        self.assertEqual(first, STALL_LOG_TEMPLATE.format(elapsed=0.8, stack="스택 내용"))
        self.assertIsNone(detector.check())
        self.assertEqual(len(self.reports), 1)

    def test_beat_resets_report_state_for_the_next_stall(self) -> None:
        """하트비트가 오면 다음 멈춤을 다시 보고하는지 검증"""
        detector = self._detector(_FakeClock([0.0, 1.0, 2.0, 5.0, 9.0]))
        detector.beat()
        self.assertIsNotNone(detector.check())
        detector.beat()
        self.assertFalse(detector.is_reported)
        self.assertIsNotNone(detector.check())
        self.assertEqual(len(self.reports), 2)

    def test_no_report_before_the_first_heartbeat(self) -> None:
        """첫 하트비트 전에는 시간이 흘러도 멈춤으로 보고하지 않는지 검증"""
        detector = self._detector(_FakeClock([0.0, 5.0, 9.0]))
        self.assertIsNone(detector.check())
        self.assertIsNone(detector.check())
        self.assertEqual(self.reports, [])

    def test_report_after_a_beat_and_reset_after_the_next_beat(self) -> None:
        """하트비트 뒤 간격이 임계값을 넘으면 한 번 보고하고, 다음 하트비트로 되돌리는지 검증"""
        detector = self._detector(_FakeClock([0.0, 0.0, 0.8, 1.9, 2.0, 3.0]))
        detector.beat()
        self.assertIsNotNone(detector.check())
        self.assertIsNone(detector.check())
        detector.beat()
        self.assertFalse(detector.is_reported)
        self.assertIsNotNone(detector.check())
        self.assertEqual(len(self.reports), 2)

    def test_defaults_use_the_wall_clock_and_real_stack(self) -> None:
        """시계를 주지 않으면 실제 단조 시계를, 스택 공급자를 주지 않으면 메인 스레드 스택을 쓰는지 검증"""
        detector = StallDetector(threshold=100.0, sink=lambda _elapsed, _stack: None)
        self.assertIsNone(detector.check())
        self.assertFalse(detector.is_reported)
        self.assertTrue(main_thread_stack().strip())

    def test_stack_falls_back_when_main_thread_frame_is_missing(self) -> None:
        """메인 스레드 프레임을 얻지 못하면 안내 문구를 돌려주는지 검증"""
        with patch("src.core.diagnostics.watchdog._current_frames", return_value={}):
            self.assertEqual(main_thread_stack(), NO_STACK_TEXT)

    def test_default_sink_writes_a_warning_to_the_app_logger(self) -> None:
        """기본 기록 함수가 감시 로거에 WARNING으로 남기는지 검증"""
        with self.assertLogs("src", level="WARNING") as logs:
            default_sink(0.75, "스택")
        self.assertIn(STALL_LOG_TEMPLATE.format(elapsed=0.75, stack="스택"), "\n".join(logs.output))


class MainThreadWatchdogTests(unittest.TestCase):
    """감시 스레드 시작, 주기 확인, 정지 검증"""

    def test_start_runs_checks_and_stop_returns(self) -> None:
        """감시 스레드가 확인을 반복하고 정지하면 끝나는지 검증"""
        checked = threading.Event()

        class _RecordingDetector(StallDetector):
            """확인이 불렸음을 알리는 대역 감시기"""

            def check(self) -> str | None:
                checked.set()
                return None

        watchdog = MainThreadWatchdog(
            _RecordingDetector(threshold=100.0, clock=time.perf_counter),
            poll_interval=0.001,
        )
        watchdog.start()
        try:
            self.assertTrue(checked.wait(timeout=2.0))
            self.assertTrue(watchdog.is_running)
        finally:
            watchdog.stop()
        self.assertFalse(watchdog.is_running)

    def test_start_twice_and_stop_twice_are_safe(self) -> None:
        """시작과 정지를 두 번 해도 예외가 없는지 검증"""
        watchdog = MainThreadWatchdog(
            StallDetector(threshold=100.0, sink=lambda _e, _s: None), poll_interval=0.001
        )
        watchdog.start()
        watchdog.start()
        watchdog.stop()
        watchdog.stop()
        self.assertFalse(watchdog.is_running)

    def test_beat_forwards_to_the_detector(self) -> None:
        """하트비트 전달이 감시기를 새 구간으로 되돌리는지 검증"""
        detector = StallDetector(threshold=0.0, clock=_FakeClock([0.0, 0.0, 1.0, 5.0]), sink=lambda _e, _s: None)
        watchdog = MainThreadWatchdog(detector, poll_interval=0.001)
        detector.beat()
        self.assertIsNotNone(detector.check())
        watchdog.beat()
        self.assertFalse(detector.is_reported)

    def test_stop_on_never_started_watchdog_is_safe(self) -> None:
        """시작하지 않은 감시자를 정지해도 예외가 없는지 검증"""
        watchdog = MainThreadWatchdog(
            StallDetector(threshold=100.0, sink=lambda _e, _s: None)
        )
        watchdog.stop()
        self.assertFalse(watchdog.is_running)


if __name__ == "__main__":
    unittest.main()
