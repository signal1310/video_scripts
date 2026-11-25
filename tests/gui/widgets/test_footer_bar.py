"""하단 막대의 재인코딩 상태 칩이 상태별 문구, 진행 막대, 클릭 요청을 갖는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.encode.encode_batch_book import (
    CHIP_STATE_FINISHED,
    CHIP_STATE_PAUSED,
    CHIP_STATE_RUNNING,
    EncodeChipStatus,
)
from src.gui.styles.palettes import DARK_PALETTE
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.widgets.footer_bar import (
    _CHIP_BAR_HEIGHT_PX,
    CHIP_STATE_PROPERTY,
    EncodeFooterChip,
    FooterBar,
)

_APP = QApplication.instance() or QApplication([])


def _status(state: str, done: int = 3, total: int = 12, tooltip: str = "") -> EncodeChipStatus:
    """칩 상태 묶음 하나를 만드는 도우미"""
    percent = round(done * 100 / total) if total else 0
    return EncodeChipStatus(state=state, done=done, total=total, percent=percent, tooltip=tooltip)


def _press(widget: QWidget, button: Qt.MouseButton) -> None:
    """위젯에 마우스 누름 이벤트를 직접 보내는 도우미"""
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(1.0, 1.0),
        QPointF(1.0, 1.0),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(event)


class EncodeFooterChipStateTests(unittest.TestCase):
    """칩의 실행 중, 일시중지, 완료, 숨김 상태와 진행 막대 표시를 검증"""

    def setUp(self) -> None:
        self.host = FooterBar()
        self.addCleanup(self.host.deleteLater)
        self.chip = self.host.encode_chip

    def test_chip_is_hidden_until_a_batch_gives_a_status(self) -> None:
        """시작 전에는 칩이 보이지 않는지 검증"""
        self.assertFalse(self.chip.isVisibleTo(self.host))

    def test_running_state_shows_progress_bar_and_percent_text(self) -> None:
        """실행 중에는 미니 진행 막대와 처리 수, 퍼센트를 함께 보이는지 검증"""
        self.host.set_encode_status(_status(CHIP_STATE_RUNNING))
        self.assertTrue(self.chip.isVisibleTo(self.host))
        self.assertEqual(self.chip.lbl_text.text(), "재인코딩 중 3/12 (25%)")
        self.assertEqual(self.chip.state, CHIP_STATE_RUNNING)
        self.assertEqual(self.chip.progress_bar.value(), 25)
        self.assertFalse(self.chip.progress_bar.isHidden())
        self.assertEqual(self.chip.property(CHIP_STATE_PROPERTY), CHIP_STATE_RUNNING)

    def test_paused_state_keeps_the_bar_and_uses_the_paused_wording(self) -> None:
        """일시중지 상태는 호박색 배색 속성과 일시중지 문구를 쓰는지 검증"""
        self.chip.set_status(_status(CHIP_STATE_PAUSED, done=6, total=12))
        self.assertEqual(self.chip.lbl_text.text(), "재인코딩 일시중지 6/12 (50%)")
        self.assertEqual(self.chip.progress_bar.value(), 50)
        self.assertFalse(self.chip.progress_bar.isHidden())
        self.assertEqual(self.chip.property(CHIP_STATE_PROPERTY), CHIP_STATE_PAUSED)

    def test_finished_state_hides_the_bar_and_drops_the_percent(self) -> None:
        """완료 상태는 미니 진행 막대를 숨기고 처리 수만 보이는지 검증"""
        self.chip.set_status(_status(CHIP_STATE_FINISHED, done=12, total=12))
        self.assertEqual(self.chip.lbl_text.text(), "재인코딩 완료 12/12")
        self.assertTrue(self.chip.progress_bar.isHidden())
        self.assertEqual(self.chip.property(CHIP_STATE_PROPERTY), CHIP_STATE_FINISHED)

    def test_tooltip_falls_back_to_the_state_text(self) -> None:
        """툴팁이 비면 상태 문구를 그대로 쓰는지 검증"""
        self.chip.set_status(_status(CHIP_STATE_RUNNING, tooltip=""))
        self.assertEqual(self.chip.toolTip(), "재인코딩 중 3/12 (25%)")
        self.chip.set_status(_status(CHIP_STATE_RUNNING, tooltip="상태별 개수"))
        self.assertEqual(self.chip.toolTip(), "상태별 개수")

    def test_theme_change_keeps_the_state_property(self) -> None:
        """테마를 바꿔도 칩 상태 속성과 진행 막대 값이 유지되는지 검증"""
        self.chip.set_status(_status(CHIP_STATE_RUNNING))
        self.chip.apply_theme(DARK_PALETTE)
        self.assertEqual(self.chip.property(CHIP_STATE_PROPERTY), CHIP_STATE_RUNNING)
        self.assertEqual(self.chip.progress_bar.value(), 25)

    def test_applied_stylesheet_keeps_the_mini_bar_height(self) -> None:
        """전역 진행 막대 규칙을 적용해도 칩의 미니 진행 막대 높이가 6px인지 검증"""
        self.host.setStyleSheet(build_stylesheet(DARK_PALETTE))
        self.host.show()
        self.chip.set_status(_status(CHIP_STATE_RUNNING))
        _APP.processEvents()
        self.assertEqual(self.chip.progress_bar.height(), _CHIP_BAR_HEIGHT_PX)
        self.assertEqual(self.chip.progress_bar.height(), 6)


class FooterEncodeChipClickTests(unittest.TestCase):
    """칩의 어느 부분을 왼쪽 버튼으로 눌러도 재인코딩 창 요청이 나오는지 검증"""

    def setUp(self) -> None:
        self.chip = EncodeFooterChip()
        self.addCleanup(self.chip.deleteLater)
        self.chip.set_status(_status(CHIP_STATE_RUNNING))
        self.requests: list[bool] = []
        self.chip.encode_window_requested.connect(lambda: self.requests.append(True))

    def test_left_press_on_the_chip_emits_the_window_request(self) -> None:
        """칩 본체의 왼쪽 버튼 누름은 요청을 내고, 오른쪽 버튼은 내지 않는지 검증"""
        _press(self.chip, Qt.MouseButton.RightButton)
        self.assertEqual(self.requests, [])
        _press(self.chip, Qt.MouseButton.LeftButton)
        self.assertEqual(self.requests, [True])

    def test_press_on_the_inner_label_is_forwarded_as_a_request(self) -> None:
        """안쪽 글자를 누르면 이벤트 필터가 같은 요청을 내는지 검증"""
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(1.0, 1.0),
            QPointF(1.0, 1.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertTrue(self.chip.eventFilter(self.chip.lbl_text, event))
        self.assertEqual(self.requests, [True])

    def test_other_events_are_not_swallowed(self) -> None:
        """누름이 아닌 이벤트는 칩이 가로채지 않는지 검증"""
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(1.0, 1.0),
            QPointF(1.0, 1.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertFalse(self.chip.eventFilter(self.chip.progress_bar, event))
        self.assertEqual(self.requests, [])


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
            "화질 분석 진행 중... (1/3) a.mp4 / 축소 후보 선별 (2/9)",
        )
        self.assertEqual(self.footer.progress_bar.format(), "%p%, 약 10초 남음")
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
            "검은 여백 감지 진행 중... (1/3) a.mp4",
        )
        self.assertEqual(self.footer.progress_bar.format(), "%p%, 약 10초 남음")
        self.assertEqual(self.footer.lbl_status.toolTip(), "임시 작업 폴더: C:/tmp/work")
        self.assertEqual(self.footer.btn_frame_cancel.accessibleName(), "검은 여백 감지 중단")
        self.assertEqual(self.footer.btn_frame_cancel.toolTip(), "진행 중인 검은 여백 감지를 중단합니다.")
        self.footer.clear_frame_analysis_progress()
        self.assertEqual(self.footer.btn_frame_cancel.accessibleName(), "화질 분석 중단")
        self.assertEqual(self.footer.btn_frame_cancel.toolTip(), "진행 중인 화질 분석을 중단합니다.")


class FooterEncodeStatusTests(unittest.TestCase):
    """FooterBar가 칩 상태를 그대로 넘기는지 검증"""

    def setUp(self) -> None:
        self.footer = FooterBar()
        self.addCleanup(self.footer.deleteLater)

    def test_status_is_forwarded_to_the_chip(self) -> None:
        """set_encode_status가 칩의 문구와 진행 막대를 바꾸는지 검증"""
        self.footer.set_encode_status(_status(CHIP_STATE_RUNNING, done=1, total=4))
        self.assertEqual(self.footer.encode_chip.lbl_text.text(), "재인코딩 중 1/4 (25%)")
        self.assertEqual(self.footer.encode_chip.progress_bar.value(), 25)
        self.footer.set_encode_status(_status(CHIP_STATE_FINISHED, done=4, total=4))
        self.assertEqual(self.footer.encode_chip.lbl_text.text(), "재인코딩 완료 4/4")

    def test_chip_request_is_forwarded_as_the_footer_signal(self) -> None:
        """칩 클릭 요청이 FooterBar의 재인코딩 창 열기 신호로 이어지는지 검증"""
        requests: list[bool] = []
        self.footer.encode_window_requested.connect(lambda: requests.append(True))
        _press(self.footer.encode_chip, Qt.MouseButton.LeftButton)
        self.assertEqual(requests, [True])


if __name__ == "__main__":
    unittest.main(verbosity=2)
