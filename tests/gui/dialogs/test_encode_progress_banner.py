"""인코딩 진행 창 종료 예약 배너가 숨김 기본값과 두 버튼의 신호 전달을 올바르게 다루는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from src.gui.dialogs.encode_progress_banner import CANCEL_CLOSE_TIP, CLOSE_PENDING_TEXT, STOP_NOW_TIP, CloseBanner

_APP = QApplication.instance() or QApplication([])


class CloseBannerTests(unittest.TestCase):
    """배너는 기본으로 숨겨지고, 각 버튼은 대응하는 신호만 한 번 보내는지 검증"""

    def setUp(self) -> None:
        self.banner = CloseBanner()
        self.stop_now: list[bool] = []
        self.cancelled: list[bool] = []
        self.banner.stop_now_requested.connect(lambda: self.stop_now.append(True))
        self.banner.cancel_requested.connect(lambda: self.cancelled.append(True))

    def tearDown(self) -> None:
        self.banner.deleteLater()

    def _button(self, accessible_name: str) -> QPushButton:
        buttons = [b for b in self.banner.findChildren(QPushButton) if b.accessibleName() == accessible_name]
        self.assertEqual(len(buttons), 1)
        return buttons[0]

    def test_banner_starts_hidden_with_the_guidance_text_and_tooltips(self) -> None:
        """배너는 시작 때 숨겨지고, 두 버튼은 안내 툴팁을 가지며 본문 문구는 승인 문구와 같은지 검증"""
        self.assertTrue(self.banner.isHidden())
        self.assertEqual(self._button("즉시 중지로 바꾸기").toolTip(), STOP_NOW_TIP)
        self.assertEqual(self._button("종료 예약 취소").toolTip(), CANCEL_CLOSE_TIP)
        self.assertIn(CLOSE_PENDING_TEXT, [label.text() for label in self.banner.findChildren(QLabel)])

    def test_stop_now_button_emits_only_the_stop_now_signal(self) -> None:
        """즉시 중지로 바꾸기 버튼은 즉시 중지 신호만 보내는지 검증"""
        self._button("즉시 중지로 바꾸기").click()
        self.assertEqual(self.stop_now, [True])
        self.assertEqual(self.cancelled, [])

    def test_cancel_button_emits_only_the_cancel_signal(self) -> None:
        """종료 예약 취소 버튼은 취소 신호만 보내는지 검증"""
        self._button("종료 예약 취소").click()
        self.assertEqual(self.cancelled, [True])
        self.assertEqual(self.stop_now, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
