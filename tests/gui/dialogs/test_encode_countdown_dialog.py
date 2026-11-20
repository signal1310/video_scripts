"""완료 후 동작 카운트다운 안내: 문구의 최대 절전 조건, 0에 닿을 때 실행 신호 한 번, 취소와 닫기 뒤에는 실행 신호 없음

- 타이머를 기다리지 않고 tick을 직접 불러 결정적으로 검증
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from src.core.encode.power_state import AFTER_COMPLETE_EXIT, AFTER_COMPLETE_SHUTDOWN, AFTER_COMPLETE_SLEEP
from src.gui.dialogs.encode_countdown_dialog import HIBERNATE_SENTENCE, CountdownPrompt, countdown_text

_APP = QApplication.instance() or QApplication([])


class CountdownTextTests(unittest.TestCase):
    """본문 문구: 최대 절전 문장은 대체 조건이 참일 때만, 종료와 앱 종료는 같은 틀"""

    def test_sleep_mentions_hibernate_only_when_sleep_falls_back(self) -> None:
        plain = countdown_text(AFTER_COMPLETE_SLEEP, 30, False)
        fallback = countdown_text(AFTER_COMPLETE_SLEEP, 30, True)
        self.assertIn("30초 뒤 PC를 절전합니다", plain)
        self.assertNotIn(HIBERNATE_SENTENCE, plain)
        self.assertTrue(fallback.endswith(HIBERNATE_SENTENCE))

    def test_shutdown_and_exit_name_their_action_and_cancel_path(self) -> None:
        self.assertIn("PC를 끕니다", countdown_text(AFTER_COMPLETE_SHUTDOWN, 12, False))
        self.assertIn("12초 뒤 앱을 끝냅니다", countdown_text(AFTER_COMPLETE_EXIT, 12, False))
        self.assertNotIn(HIBERNATE_SENTENCE, countdown_text(AFTER_COMPLETE_SHUTDOWN, 12, True))


class CountdownPromptTests(unittest.TestCase):
    """카운트다운 창의 신호: 실행은 한 번만, 취소는 실행을 막음"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.due: list[int] = []
        self.cancelled: list[int] = []

    def _prompt(self, seconds: int = 2) -> CountdownPrompt:
        prompt = CountdownPrompt(AFTER_COMPLETE_SLEEP, seconds, hibernate_fallback=False, parent=self.parent)
        prompt.due.connect(lambda: self.due.append(1))
        prompt.cancelled.connect(lambda: self.cancelled.append(1))
        return prompt

    def test_due_is_sent_once_when_the_countdown_reaches_zero(self) -> None:
        prompt = self._prompt(seconds=2)
        prompt.tick()
        self.assertEqual((self.due, prompt.remaining), ([], 1))
        prompt.tick()
        prompt.tick()
        self.assertEqual(self.due, [1])
        self.assertEqual(self.cancelled, [])

    def test_cancel_before_zero_sends_cancelled_and_never_due(self) -> None:
        prompt = self._prompt(seconds=2)
        prompt.tick()
        prompt.cancel()
        prompt.cancel()
        prompt.tick()
        self.assertEqual((self.due, self.cancelled), ([], [1]))

    def test_cancel_after_due_does_nothing(self) -> None:
        prompt = self._prompt(seconds=1)
        prompt.tick()
        prompt.cancel()
        self.assertEqual((self.due, self.cancelled), ([1], []))

    def test_closing_the_box_counts_as_cancel(self) -> None:
        prompt = self._prompt(seconds=2)
        prompt._box.done(0)
        self.assertEqual((self.due, self.cancelled), ([], [1]))
