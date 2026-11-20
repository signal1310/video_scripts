"""절전 방지와 완료 후 동작: 명령 매핑, 카운트다운 상태, 실행 분기, 최대 절전 판별

- 실제 절전과 종료 명령은 실행하지 않음. 실행 함수와 종료 함수는 기록용 대역으로 주입
"""

import ctypes
import unittest
from collections.abc import Sequence
from unittest import mock

from src.core.encode import power_state
from src.core.encode.power_state import (
    AFTER_COMPLETE_COUNTDOWN_SEC,
    AFTER_COMPLETE_EXIT,
    AFTER_COMPLETE_NONE,
    AFTER_COMPLETE_SHUTDOWN,
    AFTER_COMPLETE_SLEEP,
    SHUTDOWN_COMMAND,
    SLEEP_COMMAND,
    SYSTEM_POWER_CAPABILITIES_SIZE,
    CompletionCountdown,
    PowerCapabilities,
    execute_after_complete,
    prevent_sleep,
    read_power_capabilities,
    sleep_falls_back_to_hibernate,
    system_action_command,
)


class _Recorder:
    """실행 명령을 기록만 하고 실제로는 아무 것도 실행하지 않는 대역"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str]) -> object:
        self.calls.append(tuple(command))
        return None


class _ExitRecorder:
    """앱 종료 요청을 기록만 하는 대역"""

    def __init__(self) -> None:
        self.count: int = 0

    def __call__(self) -> None:
        self.count += 1


class TestSystemActionCommand(unittest.TestCase):
    """완료 후 동작별 명령 매핑"""

    def test_sleep_and_shutdown_have_commands(self) -> None:
        self.assertEqual(system_action_command(AFTER_COMPLETE_SLEEP), SLEEP_COMMAND)
        self.assertEqual(system_action_command(AFTER_COMPLETE_SHUTDOWN), SHUTDOWN_COMMAND)

    def test_none_and_exit_have_no_system_command(self) -> None:
        """앱 종료는 시스템 명령이 아니라 종료 함수로 처리함"""
        self.assertIsNone(system_action_command(AFTER_COMPLETE_NONE))
        self.assertIsNone(system_action_command(AFTER_COMPLETE_EXIT))


class TestCompletionCountdown(unittest.TestCase):
    """카운트다운 틱은 0에 닿는 틱에서 한 번만 실행을 알리고, 취소 뒤에는 알리지 않음"""

    def test_default_countdown_is_thirty_seconds(self) -> None:
        self.assertEqual(AFTER_COMPLETE_COUNTDOWN_SEC, 30)

    def test_tick_reports_zero_once_then_stays_finished(self) -> None:
        countdown = CompletionCountdown(2)
        self.assertFalse(countdown.tick())
        self.assertEqual(countdown.remaining, 1)
        self.assertTrue(countdown.tick())
        self.assertEqual(countdown.remaining, 0)
        self.assertFalse(countdown.tick())

    def test_cancel_stops_further_ticks_from_reporting_zero(self) -> None:
        countdown = CompletionCountdown(1)
        countdown.cancel()
        self.assertTrue(countdown.cancelled)
        self.assertFalse(countdown.tick())
        self.assertEqual(countdown.remaining, 1)


class TestExecuteAfterComplete(unittest.TestCase):
    """동작별 실행 분기. 명령과 종료 함수는 대역만 호출함"""

    def test_sleep_and_shutdown_run_their_command_only(self) -> None:
        recorder = _Recorder()
        exits = _ExitRecorder()
        self.assertTrue(execute_after_complete(AFTER_COMPLETE_SHUTDOWN, runner=recorder, exit_app=exits))
        self.assertTrue(execute_after_complete(AFTER_COMPLETE_SLEEP, runner=recorder, exit_app=exits))
        self.assertEqual(recorder.calls, [SHUTDOWN_COMMAND, SLEEP_COMMAND])
        self.assertEqual(exits.count, 0)

    def test_exit_calls_exit_function_without_command(self) -> None:
        recorder = _Recorder()
        exits = _ExitRecorder()
        self.assertTrue(execute_after_complete(AFTER_COMPLETE_EXIT, runner=recorder, exit_app=exits))
        self.assertEqual(exits.count, 1)
        self.assertEqual(recorder.calls, [])

    def test_none_runs_nothing(self) -> None:
        recorder = _Recorder()
        exits = _ExitRecorder()
        self.assertFalse(execute_after_complete(AFTER_COMPLETE_NONE, runner=recorder, exit_app=exits))
        self.assertEqual(recorder.calls, [])
        self.assertEqual(exits.count, 0)


class TestSleepFallsBackToHibernate(unittest.TestCase):
    """최대 절전 문장은 S3를 쓸 수 없고 최대 절전 파일이 있을 때만"""

    def test_truth_table(self) -> None:
        cases = {
            (True, True): False,
            (True, False): False,
            (False, True): True,
            (False, False): False,
        }
        for (s3, hibernate_file), expected in cases.items():
            with self.subTest(s3=s3, hibernate_file=hibernate_file):
                caps = PowerCapabilities(standby_s3=s3, hibernate_file=hibernate_file)
                self.assertIs(sleep_falls_back_to_hibernate(caps), expected)

    def test_unknown_capabilities_are_not_hibernate(self) -> None:
        self.assertFalse(sleep_falls_back_to_hibernate(None))


class TestReadPowerCapabilities(unittest.TestCase):
    """능력 조회는 읽기만 하며, 조회 구조체 크기는 API가 쓰는 크기와 같아야 함"""

    def test_structure_matches_windows_size(self) -> None:
        self.assertEqual(ctypes.sizeof(power_state.SystemPowerCapabilitiesStructure), SYSTEM_POWER_CAPABILITIES_SIZE)

    def test_non_windows_returns_none(self) -> None:
        with mock.patch.object(power_state, "IS_WINDOWS", False):
            self.assertIsNone(read_power_capabilities())

    def test_current_platform_returns_capabilities_or_none(self) -> None:
        self.assertIsInstance(read_power_capabilities(), (PowerCapabilities, type(None)))


class TestRunCommand(unittest.TestCase):
    """기본 실행 함수는 명령을 그대로 넘기고 실제 실행은 subprocess 대역으로 막음"""

    def test_passes_the_command_to_subprocess_without_running_it(self) -> None:
        with mock.patch.object(power_state.subprocess, "run") as run:
            power_state.run_command(SLEEP_COMMAND)
        self.assertEqual(run.call_args.args[0], list(SLEEP_COMMAND))


class TestPreventSleep(unittest.TestCase):
    """비활성 설정은 아무 일도 하지 않고, 블록 예외가 나도 빠져나옴"""

    def test_disabled_block_runs_body_and_propagates_errors(self) -> None:
        with self.assertRaises(RuntimeError), prevent_sleep(False):
            raise RuntimeError("본문 오류")


if __name__ == "__main__":
    unittest.main(verbosity=2)
