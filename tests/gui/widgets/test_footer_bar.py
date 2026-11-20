"""하단 막대의 재인코딩 요약 칸이 문구가 있을 때만 보이고 툴팁을 가지는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from src.gui.widgets.footer_bar import FooterBar

_APP = QApplication.instance() or QApplication([])


class FooterEncodeSummaryTests(unittest.TestCase):
    """재인코딩 요약 칸의 표시, 숨김과 툴팁을 검증"""

    def setUp(self) -> None:
        self.footer = FooterBar()
        self.addCleanup(self.footer.deleteLater)

    def test_summary_is_hidden_until_a_batch_gives_text(self) -> None:
        """시작 전에는 요약 칸이 숨겨져 있는지 검증"""
        self.assertFalse(self.footer.lbl_encode.isVisibleTo(self.footer))

    def test_summary_shows_the_text_and_its_tooltip(self) -> None:
        """요약 문구와 툴팁을 넘기면 칸이 보이고 내용이 바뀌는지 검증"""
        self.footer.set_encode_summary("재인코딩 (3/12 처리)", "상태별 개수\n완료 3개")
        self.assertEqual(self.footer.lbl_encode.text(), "재인코딩 (3/12 처리)")
        self.assertEqual(self.footer.lbl_encode.toolTip(), "상태별 개수\n완료 3개")
        self.assertTrue(self.footer.lbl_encode.isVisibleTo(self.footer))

    def test_empty_text_hides_the_summary_again(self) -> None:
        """빈 문구를 넘기면 요약 칸을 다시 숨기는지 검증"""
        self.footer.set_encode_summary("재인코딩 (0/1 처리)")
        self.footer.set_encode_summary("")
        self.assertFalse(self.footer.lbl_encode.isVisibleTo(self.footer))


class FooterSummaryClickTests(unittest.TestCase):
    """하단 재인코딩 요약 칸을 왼쪽 버튼으로 누르면 진행 창 열기 신호가 나오는지 검증"""

    def test_left_press_on_the_encode_summary_emits_clicked(self) -> None:
        """왼쪽 버튼 누름은 clicked를 보내고, 오른쪽 버튼 누름은 보내지 않는지 검증"""
        footer = FooterBar()
        clicks: list[bool] = []
        footer.lbl_encode.clicked.connect(lambda: clicks.append(True))
        footer.lbl_encode.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(1.0, 1.0),
                QPointF(1.0, 1.0),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        footer.lbl_encode.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(1.0, 1.0),
                QPointF(1.0, 1.0),
                Qt.MouseButton.RightButton,
                Qt.MouseButton.RightButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        self.assertEqual(clicks, [True])


if __name__ == "__main__":
    unittest.main(verbosity=2)
