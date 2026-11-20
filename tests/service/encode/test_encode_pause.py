"""실행 중 프로세스의 일시중지, 재개, 중지와 장시간 안내 타이머를 가짜 프로세스와 시계로 검증"""

import threading
import unittest
from unittest import mock

from src.core.encode.encode_process import ProcessOutcome
from src.core.encode.encode_progress import LONG_RUN_NOTICE_SEC
from src.service.encode.encode_pause import PauseControl

_SOURCE: str = "C:/work/a.mp4"
_OTHER: str = "C:/work/b.mp4"


class _FakeProcess:
    """멈춤 깊이, 중지 횟수를 세는 가짜 프로세스. Windows처럼 멈춤이 쌓이는 것을 흉내 냄"""

    def __init__(self) -> None:
        self.depth: int = 0
        self.started: int = 0
        self.cancelled: int = 0

    def start(self) -> None:
        self.started += 1

    def pause(self) -> None:
        self.depth += 1

    def resume(self) -> None:
        self.depth -= 1

    def cancel(self) -> None:
        self.cancelled += 1

    def set_priority(self, name: str) -> None:
        """프로세스 우선순위 설정은 이 검증과 무관"""

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        return ProcessOutcome(returncode=0, cancelled=False, stderr_tail=())


class _Harness:
    """시계와 안내 기록을 가진 제어 객체 생성 도우미"""

    def __init__(self) -> None:
        self.now: float = 1000.0
        self.notices: list[tuple[int, str]] = []
        self.abort: threading.Event = threading.Event()
        self.control: PauseControl = PauseControl(
            abort=self.abort,
            clock=lambda: self.now,
            notify=lambda batch_id, source: self.notices.append((batch_id, source)),
        )


class PauseStackingTests(unittest.TestCase):
    """개별, 전체 일시중지가 섞여도 프로세스가 같은 상태에서 한 번만 멈추는지 검증"""

    def test_pause_one_then_pause_all_then_resume_all_leaves_no_suspend_depth(self) -> None:
        """개별 일시중지 뒤 전체 일시중지, 전체 재개를 하면 멈춤 깊이가 0이고 항목이 실행되는지 검증"""
        harness = _Harness()
        process = _FakeProcess()
        self.assertTrue(harness.control.launch(_SOURCE, 1, process))
        self.assertTrue(harness.control.pause_one(_SOURCE))
        harness.control.pause_all()
        harness.control.resume_all()
        self.assertEqual(process.depth, 0)

    def test_pause_all_skips_a_process_that_is_already_paused_one(self) -> None:
        """전체 일시중지가 개별로 멈춘 프로세스를 다시 멈추지 않는지 검증"""
        harness = _Harness()
        process = _FakeProcess()
        harness.control.launch(_SOURCE, 1, process)
        harness.control.launch(_OTHER, 1, _FakeProcess())
        harness.control.pause_one(_SOURCE)
        harness.control.pause_all()
        self.assertEqual(process.depth, 1)

    def test_resume_one_restores_a_process_paused_alone(self) -> None:
        """개별 일시중지 뒤 개별 재개가 멈춤 깊이를 0으로 돌리는지 검증"""
        harness = _Harness()
        process = _FakeProcess()
        harness.control.launch(_SOURCE, 1, process)
        harness.control.pause_one(_SOURCE)
        self.assertTrue(harness.control.resume_one(_SOURCE))
        self.assertEqual(process.depth, 0)
        self.assertFalse(harness.control.resume_one(_SOURCE))

    def test_resume_one_is_refused_while_the_whole_batch_is_paused(self) -> None:
        """전체 일시중지 중에는 개별 재개와 개별 일시중지가 거절되는지 검증"""
        harness = _Harness()
        process = _FakeProcess()
        harness.control.launch(_SOURCE, 1, process)
        harness.control.pause_all()
        self.assertFalse(harness.control.pause_one(_SOURCE))
        self.assertFalse(harness.control.resume_one(_SOURCE))
        self.assertEqual(process.depth, 1)

    def test_process_started_during_a_batch_pause_starts_paused(self) -> None:
        """전체 일시중지 중에 시작한 프로세스는 바로 멈춘 상태가 되는지 검증"""
        harness = _Harness()
        harness.control.pause_all()
        process = _FakeProcess()
        harness.control.launch(_SOURCE, 1, process)
        self.assertEqual(process.depth, 1)


class LongPauseTimerTests(unittest.TestCase):
    """장시간 안내 타이머가 하나만 돌고, 같은 안내를 중복으로 보내지 않는지 검증"""

    def test_repeated_pauses_arm_one_timer_and_fire_one_notice_per_item(self) -> None:
        """개별과 전체 일시중지를 겹쳐도 타이머는 하나이고, 울릴 때 항목마다 안내가 한 번인지 검증"""
        harness = _Harness()
        harness.control.launch(_SOURCE, 7, _FakeProcess())
        harness.control.launch(_OTHER, 7, _FakeProcess())
        with mock.patch("threading.Timer") as timer_cls:
            harness.control.pause_one(_SOURCE)
            harness.control.pause_all()
            self.assertEqual(timer_cls.call_count, 1)
            harness.now += LONG_RUN_NOTICE_SEC + 1.0
            timer_cls.call_args.args[1](*timer_cls.call_args.kwargs["args"])
            self.assertEqual(sorted(harness.notices), [(7, _SOURCE), (7, _OTHER)])
            self.assertEqual(timer_cls.call_count, 2)

    def test_resume_all_cancels_the_timer_when_nothing_is_paused(self) -> None:
        """전체 재개로 일시중지가 모두 풀리면 타이머를 취소하는지 검증"""
        harness = _Harness()
        harness.control.launch(_SOURCE, 1, _FakeProcess())
        with mock.patch("threading.Timer") as timer_cls:
            harness.control.pause_all()
            harness.control.resume_all()
            timer_cls.return_value.cancel.assert_called_once()

    def test_stale_timer_from_a_cancelled_generation_sends_nothing(self) -> None:
        """재개로 취소된 이전 세대의 타이머가 뒤늦게 울려도 안내를 보내지 않는지 검증"""
        harness = _Harness()
        harness.control.launch(_SOURCE, 1, _FakeProcess())
        with mock.patch("threading.Timer") as timer_cls:
            harness.control.pause_one(_SOURCE)
            stale = timer_cls.call_args
            harness.control.resume_one(_SOURCE)
            harness.now += LONG_RUN_NOTICE_SEC + 1.0
            stale.args[1](*stale.kwargs["args"])
        self.assertEqual(harness.notices, [])

    def test_notice_is_not_sent_before_the_limit(self) -> None:
        """멈춘 시간이 기준 이하이면 안내를 보내지 않는지 검증"""
        harness = _Harness()
        harness.control.launch(_SOURCE, 1, _FakeProcess())
        harness.control.pause_one(_SOURCE)
        harness.control.check_long_pause()
        self.assertEqual(harness.notices, [])


class ItemRunStateTests(unittest.TestCase):
    """실행 목록의 경과 시간, 즉시 중지 거절, 항목별 중지가 상태에 맞게 동작하는지 검증"""

    def test_elapsed_excludes_the_paused_time(self) -> None:
        """일시중지 구간을 뺀 경과 시간을 주고, 실행 목록에 없으면 0인지 검증"""
        harness = _Harness()
        harness.control.launch(_SOURCE, 1, _FakeProcess())
        harness.now += 10.0
        harness.control.pause_one(_SOURCE)
        harness.now += 100.0
        self.assertEqual(harness.control.elapsed(_SOURCE), 10.0)
        self.assertEqual(harness.control.elapsed(_OTHER), 0.0)

    def test_launch_is_refused_after_an_abort(self) -> None:
        """즉시 중지 표시가 있으면 새 프로세스를 시작하지 않는지 검증"""
        harness = _Harness()
        harness.abort.set()
        process = _FakeProcess()
        self.assertFalse(harness.control.launch(_SOURCE, 1, process))
        self.assertEqual(process.started, 0)

    def test_cancel_if_running_is_refused_while_paused_or_absent(self) -> None:
        """일시중지 중이거나 실행 중이 아닌 항목은 중지 요청을 거절하고 받아들이는 동작을 부르지 않는지 검증"""
        harness = _Harness()
        process = _FakeProcess()
        harness.control.launch(_SOURCE, 1, process)
        accepted: list[bool] = []
        self.assertFalse(harness.control.cancel_if_running(_OTHER, lambda: accepted.append(True)))
        harness.control.pause_one(_SOURCE)
        self.assertFalse(harness.control.cancel_if_running(_SOURCE, lambda: accepted.append(True)))
        self.assertEqual(accepted, [])
        self.assertEqual(process.cancelled, 0)

    def test_cancel_if_running_accepts_a_running_item(self) -> None:
        """실행 중인 항목은 받아들이고 받아들이는 동작을 먼저 부른 뒤 중지하는지 검증"""
        harness = _Harness()
        process = _FakeProcess()
        harness.control.launch(_SOURCE, 1, process)
        accepted: list[bool] = []
        self.assertTrue(harness.control.cancel_if_running(_SOURCE, lambda: accepted.append(True)))
        self.assertEqual(accepted, [True])
        self.assertEqual(process.cancelled, 1)

    def test_cancel_one_clears_the_item_pause_so_the_next_resume_is_refused(self) -> None:
        """개별 중지는 개별 일시중지 표시를 지워 이후 재개를 거절하는지 검증"""
        harness = _Harness()
        process = _FakeProcess()
        harness.control.launch(_SOURCE, 1, process)
        harness.control.pause_one(_SOURCE)
        harness.control.cancel_one(_SOURCE)
        self.assertEqual(process.cancelled, 1)
        self.assertFalse(harness.control.resume_one(_SOURCE))


if __name__ == "__main__":
    unittest.main(verbosity=2)
