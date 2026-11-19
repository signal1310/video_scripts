"""하단 바 바쁨 상태 문구가 바쁨 종료 뒤 남지 않고, 다른 작업이 설정한 문구는 보존되는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.gui.widgets.footer_bar import FooterBar
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])

_BUSY_TEXT: str = "작업 진행 중..."
_DONE_TEXT: str = "완료 문구"


class FooterBusyStatusTests(GuiTestCase):
    def setUp(self) -> None:
        self.footer = FooterBar()

    def tearDown(self) -> None:
        self.footer.deleteLater()

    def test_busy_text_is_cleared_when_busy_ends_without_message(self) -> None:
        """빈 문구로 바쁨을 끄면 바쁨 중에 띄운 문구가 지워지는지 검증"""
        self.footer.set_busy(True, _BUSY_TEXT)
        self.assertEqual(self.footer.lbl_status.text(), _BUSY_TEXT)
        self.footer.set_busy(False)
        self.assertEqual(self.footer.lbl_status.text(), "")

    def test_completion_text_set_during_busy_is_kept(self) -> None:
        """바쁨 중 다른 작업이 완료 문구를 설정했다면 빈 문구로 바쁨을 꺼도 그 문구가 남는지 검증"""
        self.footer.set_busy(True, _BUSY_TEXT)
        self.footer.set_status_text(_DONE_TEXT)
        self.footer.set_busy(False)
        self.assertEqual(self.footer.lbl_status.text(), _DONE_TEXT)

    def test_busy_end_with_message_replaces_busy_text(self) -> None:
        """바쁨 종료 때 문구를 주면 그 문구로 바뀌는지 검증"""
        self.footer.set_busy(True, _BUSY_TEXT)
        self.footer.set_busy(False, _DONE_TEXT)
        self.assertEqual(self.footer.lbl_status.text(), _DONE_TEXT)


if __name__ == "__main__":
    unittest.main()
