"""절전 방지와 완료 후 시스템 동작: 명령 매핑, 카운트다운 취소, 명령 없는 동작의 무동작"""

import threading
import unittest
from collections.abc import Sequence

from src.core.encode.power_state import (
    AFTER_COMPLETE_EXIT,
    AFTER_COMPLETE_NONE,
    AFTER_COMPLETE_SHUTDOWN,
    AFTER_COMPLETE_SLEEP,
    SHUTDOWN_COMMAND,
    SLEEP_COMMAND,
    prevent_sleep,
    run_after_complete,
    system_action_command,
)


class _Recorder:
    """실행 명령을 기록만 하고 실제로는 아무 것도 실행하지 않는 대역"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str]) -> object:
        self.calls.append(tuple(command))
        return None


class TestSystemActionCommand(unittest.TestCase):
    """완료 후 동작별 명령 매핑"""

    def test_sleep_and_shutdown_have_commands(self) -> None:
        self.assertEqual(system_action_command(AFTER_COMPLETE_SLEEP), SLEEP_COMMAND)
        self.assertEqual(system_action_command(AFTER_COMPLETE_SHUTDOWN), SHUTDOWN_COMMAND)

    def test_none_and_exit_have_no_system_command(self) -> None:
        """앱 종료는 화면 계층이 처리하므로 시스템 명령이 없음"""
        self.assertIsNone(system_action_command(AFTER_COMPLETE_NONE))
        self.assertIsNone(system_action_command(AFTER_COMPLETE_EXIT))


class TestRunAfterComplete(unittest.TestCase):
    """카운트다운 후 실행, 카운트다운 중 취소 시 실행하지 않음"""

    def test_runs_command_after_countdown_when_not_cancelled(self) -> None:
        recorder = _Recorder()
        ran = run_after_complete(
            AFTER_COMPLETE_SHUTDOWN, cancel=threading.Event(), countdown_sec=0.0, runner=recorder
        )
        self.assertTrue(ran)
        self.assertEqual(recorder.calls, [SHUTDOWN_COMMAND])

    def test_cancel_during_countdown_skips_command(self) -> None:
        recorder = _Recorder()
        cancel = threading.Event()
        cancel.set()
        ran = run_after_complete(AFTER_COMPLETE_SLEEP, cancel=cancel, countdown_sec=30.0, runner=recorder)
        self.assertFalse(ran)
        self.assertEqual(recorder.calls, [])

    def test_actions_without_command_never_run_anything(self) -> None:
        recorder = _Recorder()
        for action in (AFTER_COMPLETE_NONE, AFTER_COMPLETE_EXIT):
            with self.subTest(action=action):
                self.assertFalse(
                    run_after_complete(action, cancel=threading.Event(), countdown_sec=0.0, runner=recorder)
                )
        self.assertEqual(recorder.calls, [])


class TestPreventSleep(unittest.TestCase):
    """비활성 설정은 아무 일도 하지 않고, 블록 예외가 나도 빠져나옴"""

    def test_disabled_block_runs_body_and_propagates_errors(self) -> None:
        with self.assertRaises(RuntimeError), prevent_sleep(False):
            raise RuntimeError("본문 오류")


if __name__ == "__main__":
    unittest.main(verbosity=2)
