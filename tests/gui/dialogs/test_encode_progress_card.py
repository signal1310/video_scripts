"""재인코딩 진행 창의 영상 카드가 탭 제목을 줄이고, 일시중지 표시와 버튼 상태, 신호를 바르게 주는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.gui.dialogs.encode_progress_card import (
    ITEM_PAUSE_TIP,
    ITEM_RESUME_TIP,
    PROGRESS_STEPS,
    JobCard,
    shorten_middle,
    tab_title,
)

_APP = QApplication.instance() or QApplication([])


class TabTitleTests(unittest.TestCase):
    """탭 제목의 줄임과 순서 번호 표기를 검증"""

    def test_long_names_are_shortened_in_the_middle_and_short_names_are_kept(self) -> None:
        """긴 파일 이름은 가운데를 줄이고 짧은 이름은 그대로 두며, 탭 제목에 순서 번호가 붙는지 검증"""
        self.assertEqual(shorten_middle("video10_normal_phone.mp4", 12), "video1…e.mp4")
        self.assertEqual(shorten_middle("short.mp4", 12), "short.mp4")
        self.assertEqual(shorten_middle("abcdef", 1), "…")
        self.assertEqual(tab_title(3, "video1.mp4"), "3. video1.mp4")


class JobCardTests(unittest.TestCase):
    """카드의 일시중지 표시, 버튼 상태와 신호, 진행 값 설정을 검증"""

    def setUp(self) -> None:
        self.card = JobCard("a.mp4")

    def test_pause_state_changes_icon_tooltip_and_hides_the_notice_on_resume(self) -> None:
        """일시중지 상태에서 툴팁이 재개 문장으로 바뀌고, 재개하면 장시간 안내가 숨겨지는지 검증"""
        self.card.set_long_pause(True)
        self.card.set_paused(True)
        self.assertTrue(self.card.is_paused)
        self.assertEqual(self.card._pause_btn.toolTip(), ITEM_RESUME_TIP)
        self.assertFalse(self.card._notice.isHidden())
        self.card.set_paused(False)
        self.assertFalse(self.card.is_paused)
        self.assertEqual(self.card._pause_btn.toolTip(), ITEM_PAUSE_TIP)
        self.assertTrue(self.card._notice.isHidden())

    def test_pause_button_can_be_disabled_during_a_batch_pause(self) -> None:
        """전체 일시중지에 맞춰 개별 일시중지 버튼을 끄고 켤 수 있는지 검증"""
        self.card.set_pause_enabled(False)
        self.assertFalse(self.card._pause_btn.isEnabled())
        self.card.set_pause_enabled(True)
        self.assertTrue(self.card._pause_btn.isEnabled())

    def test_buttons_and_notice_link_emit_their_signals(self) -> None:
        """일시중지, 중지 버튼과 장시간 안내 링크가 각자의 신호를 내는지 검증"""
        received: list[str] = []
        self.card.pause_toggled.connect(lambda: received.append("pause"))
        self.card.stop_requested.connect(lambda: received.append("stop"))
        self.card.stop_after_requested.connect(lambda: received.append("stop_after"))
        self.card._pause_btn.click()
        self.card._stop_btn.click()
        self.card._notice.linkActivated.emit("stop-after")
        self.assertEqual(received, ["pause", "stop", "stop_after"])

    def test_progress_and_values_show_the_given_text(self) -> None:
        """진행 막대는 비율을 따르고 값이 없으면 0이며, 수치 칸은 캡션 순서대로 채워지는지 검증"""
        self.card.set_progress(0.5)
        self.assertEqual(self.card._bar.value(), PROGRESS_STEPS // 2)
        self.card.set_progress(None)
        self.assertEqual(self.card._bar.value(), 0)
        self.card.set_values("00:30 / 01:00", "1.25배", "00:24", "00:06", "2100kbps")
        self.assertEqual(self.card._values[0].text(), "00:30 / 01:00")
        self.assertEqual(self.card._values[4].text(), "2100kbps")


if __name__ == "__main__":
    unittest.main(verbosity=2)
