"""재인코딩 종료 확인 창이 세 버튼의 선택을 돌려주고, 일시중지 여부에 따라 본문이 달라지며, 기본과 Esc가 계속인지 검증"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from src.core.encode.encode_close import CloseChoice
from src.gui.dialogs.encode_confirm_dialogs import (
    AFTER_CURRENT_BUTTON_TEXT,
    CLOSE_BODY,
    CLOSE_BODY_PAUSED,
    IMMEDIATE_BUTTON_TEXT,
    KEEP_BUTTON_TEXT,
    ask_close,
)

_APP = QApplication.instance() or QApplication([])


class _Clicker:
    """exec 대신 실행되어 정해진 버튼을 누른 것처럼 만드는 가짜. 누르기 전 본문을 기록함"""

    def __init__(self, button_text: str | None) -> None:
        self.button_text: str | None = button_text
        self.bodies: list[str] = []

    def __call__(self, box: QMessageBox) -> int:
        self.bodies.append(box.text())
        if self.button_text is not None:
            for button in box.buttons():
                if button.text() == self.button_text:
                    button.click()
        return 0


def _ask(button_text: str | None, paused: bool) -> tuple[CloseChoice, _Clicker]:
    clicker = _Clicker(button_text)
    with mock.patch.object(QMessageBox, "exec", new=lambda box: clicker(box)):
        choice = ask_close(QWidget(), paused)
    return choice, clicker


class AskCloseTests(unittest.TestCase):
    """세 버튼의 선택 분기와 본문 문구, 기본과 Esc 동작을 검증"""

    def test_each_button_maps_to_its_choice(self) -> None:
        """즉시 중지 후 종료, 현재 영상 후 중지하고 종료, 계속이 각각 해당 선택을 돌려주는지 검증"""
        self.assertIs(_ask(IMMEDIATE_BUTTON_TEXT, paused=False)[0], CloseChoice.IMMEDIATE)
        self.assertIs(_ask(AFTER_CURRENT_BUTTON_TEXT, paused=False)[0], CloseChoice.AFTER_CURRENT)
        self.assertIs(_ask(KEEP_BUTTON_TEXT, paused=False)[0], CloseChoice.KEEP)

    def test_closing_without_a_choice_keeps_running(self) -> None:
        """버튼 없이 닫히면(Esc 포함) 계속으로 보아 닫기를 무시하는지 검증"""
        self.assertIs(_ask(None, paused=False)[0], CloseChoice.KEEP)

    def test_body_changes_when_some_item_is_paused(self) -> None:
        """일시중지 영상이 있으면 일시중지 본문을, 없으면 일반 본문을 보이는지 검증"""
        self.assertEqual(_ask(KEEP_BUTTON_TEXT, paused=False)[1].bodies, [CLOSE_BODY])
        self.assertEqual(_ask(KEEP_BUTTON_TEXT, paused=True)[1].bodies, [CLOSE_BODY_PAUSED])


if __name__ == "__main__":
    unittest.main(verbosity=2)
