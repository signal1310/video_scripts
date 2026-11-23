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

    def test_window_button_shows_with_the_summary_and_emits_request(self) -> None:
        """재인코딩 요약이 있을 때만 진행 창 열기 버튼이 보이고, 누르면 요청 신호가 나오는지 검증"""
        footer = FooterBar()
        requests: list[bool] = []
        footer.encode_window_requested.connect(lambda: requests.append(True))
        footer.set_encode_summary("")
        self.assertTrue(footer.btn_encode_window.isHidden())
        footer.set_encode_summary("재인코딩 (0/1 처리)")
        self.assertFalse(footer.btn_encode_window.isHidden())
        footer.btn_encode_window.click()
        self.assertEqual(requests, [True])


class FooterFrameAnalysisTests(unittest.TestCase):
    """화질 분석 진행 상태 줄, 진행 막대, 아이콘 전용 중단 버튼 검증"""

    def setUp(self) -> None:
        self.footer = FooterBar()
        self.addCleanup(self.footer.deleteLater)

    def test_progress_text_range_button_and_cancel_signal(self) -> None:
        requests: list[bool] = []
        self.footer.frame_analysis_cancel_requested.connect(lambda: requests.append(True))
        self.footer.set_frame_analysis_progress(2, 5, "a.mp4")
        self.assertEqual(self.footer.lbl_status.text(), "화질 분석 진행 중... (2/5) a.mp4")
        self.assertEqual(self.footer.progress_bar.value(), 2)
        self.assertFalse(self.footer.btn_frame_cancel.isHidden())
        self.assertEqual(self.footer.btn_frame_cancel.accessibleName(), "화질 분석 중단")
        self.assertEqual(self.footer.btn_frame_cancel.toolTip(), "진행 중인 화질 분석을 중단합니다.")
        self.footer.btn_frame_cancel.click()
        self.assertEqual(requests, [True])

    def test_progress_line_shows_detail_estimate_and_work_dir_tooltip(self) -> None:
        """하단 진행 줄이 세부 단계, 남은 시간과 임시 작업 폴더 툴팁을 표시하는지 검증"""
        self.footer.set_frame_analysis_progress(
            1,
            3,
            "a.mp4",
            detail="축소 후보 선별 (2/9)",
            remaining="약 10초 남음",
            work_dir="C:/tmp/work",
        )
        self.assertEqual(
            self.footer.lbl_status.text(),
            "화질 분석 진행 중... (1/3) a.mp4 / 축소 후보 선별 (2/9), 약 10초 남음",
        )
        self.assertEqual(self.footer.lbl_status.toolTip(), "임시 작업 폴더: C:/tmp/work")
        self.assertEqual(self.footer.progress_bar.toolTip(), "임시 작업 폴더: C:/tmp/work")
        self.footer.clear_frame_analysis_progress()
        self.assertEqual(self.footer.progress_bar.toolTip(), "")

    def test_clear_only_removes_hidden_frame_progress_text(self) -> None:
        self.footer.set_frame_analysis_progress(1, 2, "a.mp4")
        self.footer.clear_frame_analysis_progress()
        self.assertTrue(self.footer.btn_frame_cancel.isHidden())
        self.assertEqual(self.footer.lbl_status.text(), "")
        self.footer.set_status_text("다른 안내")
        self.footer.set_frame_analysis_progress(1, 2, "a.mp4")
        self.footer.set_status_text("다른 안내")
        self.footer.clear_frame_analysis_progress()
        self.assertEqual(self.footer.lbl_status.text(), "다른 안내")

    def test_crop_scan_progress_uses_the_approved_prefix_and_cancel_name(self) -> None:
        """검은 여백 감지 진행 줄과 중단 버튼이 승인 문구로 바뀌고 정리 뒤 기본값으로 돌아오는지 검증"""
        self.footer.set_frame_analysis_progress(
            1,
            3,
            "a.mp4",
            remaining="약 10초 남음",
            work_dir="C:/tmp/work",
            crop_only=True,
        )
        self.assertEqual(
            self.footer.lbl_status.text(),
            "검은 여백 감지 진행 중... (1/3) a.mp4, 약 10초 남음",
        )
        self.assertEqual(self.footer.lbl_status.toolTip(), "임시 작업 폴더: C:/tmp/work")
        self.assertEqual(self.footer.btn_frame_cancel.accessibleName(), "검은 여백 감지 중단")
        self.assertEqual(self.footer.btn_frame_cancel.toolTip(), "진행 중인 검은 여백 감지를 중단합니다.")
        self.footer.clear_frame_analysis_progress()
        self.assertEqual(self.footer.btn_frame_cancel.accessibleName(), "화질 분석 중단")
        self.assertEqual(self.footer.btn_frame_cancel.toolTip(), "진행 중인 화질 분석을 중단합니다.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
