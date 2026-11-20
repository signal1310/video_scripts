"""완료 후 동작 흐름: 동작 없음은 안내를 만들지 않고, 절전과 종료는 카운트다운 0에서 한 번 실행, 취소하면 실행하지 않음

- 실제 절전과 시스템 종료는 실행하지 않음. 명령 실행 함수, 앱 종료 함수, 전원 능력 조회는 모두 기록용 대역
"""

import os
import unittest
from collections.abc import Sequence

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from src.core.encode.power_state import (
    AFTER_COMPLETE_COUNTDOWN_SEC,
    AFTER_COMPLETE_EXIT,
    AFTER_COMPLETE_NONE,
    AFTER_COMPLETE_SHUTDOWN,
    AFTER_COMPLETE_SLEEP,
    SHUTDOWN_COMMAND,
    SLEEP_COMMAND,
    PowerCapabilities,
)
from src.gui.controllers.encode_completion import CompletionFlow
from src.gui.dialogs.encode_countdown_dialog import HIBERNATE_SENTENCE

_APP = QApplication.instance() or QApplication([])


class _Recorder:
    """명령 실행과 앱 종료 요청을 기록만 하는 대역"""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.exits: int = 0
        self.caps_reads: int = 0

    def run(self, command: Sequence[str]) -> object:
        self.commands.append(tuple(command))
        return None

    def exit_app(self) -> None:
        self.exits += 1

    def caps(self) -> PowerCapabilities | None:
        self.caps_reads += 1
        return PowerCapabilities(standby_s3=False, hibernate_file=True)


class CompletionFlowTests(unittest.TestCase):
    """카운트다운을 끝까지 흘려 보내 실행 여부와 대체 조건 문구를 확인"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.recorder = _Recorder()

    def _flow(self) -> CompletionFlow:
        return CompletionFlow(
            self.parent,
            exit_app=self.recorder.exit_app,
            runner=self.recorder.run,
            read_caps=self.recorder.caps,
        )

    def _run_out(self, flow: CompletionFlow) -> None:
        prompt = flow.prompt
        self.assertIsNotNone(prompt)
        for _ in range(AFTER_COMPLETE_COUNTDOWN_SEC):
            assert prompt is not None
            prompt.tick()

    def test_none_action_shows_nothing(self) -> None:
        flow = self._flow()
        flow.start(AFTER_COMPLETE_NONE)
        self.assertIsNone(flow.prompt)
        self.assertEqual((self.recorder.commands, self.recorder.exits), ([], 0))

    def test_sleep_runs_its_command_once_after_the_countdown(self) -> None:
        flow = self._flow()
        flow.start(AFTER_COMPLETE_SLEEP)
        self._run_out(flow)
        self.assertEqual(self.recorder.commands, [SLEEP_COMMAND])

    def test_shutdown_runs_its_command_after_the_countdown(self) -> None:
        flow = self._flow()
        flow.start(AFTER_COMPLETE_SHUTDOWN)
        self._run_out(flow)
        self.assertEqual(self.recorder.commands, [SHUTDOWN_COMMAND])

    def test_exit_quits_through_the_exit_function_and_never_runs_a_command(self) -> None:
        flow = self._flow()
        flow.start(AFTER_COMPLETE_EXIT)
        self._run_out(flow)
        self.assertEqual((self.recorder.commands, self.recorder.exits), ([], 1))

    def test_cancel_during_countdown_runs_nothing(self) -> None:
        flow = self._flow()
        flow.start(AFTER_COMPLETE_SHUTDOWN)
        assert flow.prompt is not None
        flow.prompt.cancel()
        flow.prompt.tick()
        self.assertEqual((self.recorder.commands, self.recorder.exits), ([], 0))

    def test_only_sleep_reads_power_capabilities_and_adds_the_hibernate_sentence(self) -> None:
        flow = self._flow()
        flow.start(AFTER_COMPLETE_SHUTDOWN)
        self.assertEqual(self.recorder.caps_reads, 0)
        flow.start(AFTER_COMPLETE_SLEEP)
        self.assertEqual(self.recorder.caps_reads, 1)
        prompt = flow.prompt
        assert prompt is not None
        self.assertEqual(prompt.remaining, AFTER_COMPLETE_COUNTDOWN_SEC)
        self.assertIn(HIBERNATE_SENTENCE, prompt._box.text())
