"""하단 바 상태 문구가 왼쪽 여백에서 시작하고, 로그 버튼과 겹치지 않으며, 문구 유무에 따라 바 높이가 변하지 않는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from src.gui.footer_summary import FooterSummaryGroup, FooterSummarySpec
from src.gui.styles.icons import get_active_palette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.widgets.footer_bar import FooterBar
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])

_NOTICE_TEXT: str = "분석 형식이 바뀌어 4개를 다시 분석해야 합니다(예상 약 1분)"
_HOST_WIDTH: int = 900
_HOST_HEIGHT: int = 500
_BODY_MIN_HEIGHT: int = 300
_CENTER_TOLERANCE_PX: int = 1


class FooterLayoutTests(GuiTestCase):
    """실제 창과 같이 바 위에 본문을 두고 바를 표시한 뒤 위젯 위치와 높이를 측정"""

    def setUp(self) -> None:
        self.host = QWidget()
        self.addCleanup(self.host.close)
        # - 실제 창과 같이 스타일 시트를 적용해야 글꼴 크기에 따른 줄 높이가 반영됨
        self.host.setStyleSheet(build_stylesheet(get_active_palette()))
        root = QVBoxLayout(self.host)
        root.setContentsMargins(0, 0, 0, 0)
        body = QWidget(self.host)
        body.setMinimumHeight(_BODY_MIN_HEIGHT)
        root.addWidget(body, stretch=1)
        self.footer = FooterBar(self.host)
        root.addWidget(self.footer)
        self.host.resize(_HOST_WIDTH, _HOST_HEIGHT)
        self.host.show()
        _APP.processEvents()

    def _rect_in_footer(self, widget: QWidget) -> QRect:
        """바 좌표계에서 위젯이 차지하는 사각형"""
        return QRect(widget.mapTo(self.footer, widget.rect().topLeft()), widget.size())

    def _content_center_y(self) -> int:
        """바의 여백을 뺀 내용 영역의 세로 중심"""
        layout = self.footer.layout()
        assert layout is not None
        rect = layout.contentsRect()
        return rect.top() + rect.height() // 2

    @staticmethod
    def _summary_spec() -> FooterSummarySpec:
        return FooterSummarySpec(
            groups=(FooterSummaryGroup("선택", (("영상", "5개"),)),),
            plain_text="",
            tooltip="",
        )

    def test_logs_button_is_in_layout_and_does_not_overlap_status_text(self) -> None:
        """로그 버튼이 바 레이아웃에 들어 있고 상태 문구와 겹치지 않는지 검증"""
        self.footer.set_status_text(_NOTICE_TEXT)
        _APP.processEvents()
        layout = self.footer.layout()
        assert layout is not None
        self.assertGreaterEqual(layout.indexOf(self.footer.btn_logs), 0)
        status_rect = self._rect_in_footer(self.footer.lbl_status)
        button_rect = self._rect_in_footer(self.footer.btn_logs)
        self.assertFalse(status_rect.intersects(button_rect))
        self.assertTrue(self.footer.rect().contains(button_rect))

    def test_status_text_starts_at_left_margin(self) -> None:
        """상태 문구가 바의 왼쪽 여백에서 시작하고 왼쪽 정렬인지 검증"""
        self.footer.set_status_text(_NOTICE_TEXT)
        _APP.processEvents()
        layout = self.footer.layout()
        assert layout is not None
        self.assertEqual(self._rect_in_footer(self.footer.lbl_status).left(), layout.contentsRect().left())
        self.assertEqual(
            self.footer.lbl_status.alignment() & Qt.AlignmentFlag.AlignHorizontal_Mask,
            Qt.AlignmentFlag.AlignLeft,
        )

    def test_tooltip_is_set_with_status_text_and_cleared_with_it(self) -> None:
        """상태 문구와 함께 툴팁이 설정되고, 문구를 비울 때 툴팁도 지워지는지 검증"""
        self.footer.set_status_text(_NOTICE_TEXT, "사유별 영상 수")
        self.assertEqual(self.footer.lbl_status.toolTip(), "사유별 영상 수")
        self.footer.set_status_text("")
        self.assertEqual(self.footer.lbl_status.toolTip(), "")

    def test_footer_height_does_not_change_with_status_text(self) -> None:
        """상태 문구 유무와 무관하게 바 높이가 같은지 검증(요약 표시 중에도 동일)"""
        self.footer.set_summary_spec(self._summary_spec())
        heights: list[int] = []
        for text in ("", _NOTICE_TEXT, ""):
            self.footer.set_status_text(text)
            _APP.processEvents()
            heights.append(self.footer.height())
        self.assertEqual(len(set(heights)), 1, heights)

    def test_summary_is_vertically_centered_when_status_is_empty(self) -> None:
        """상태 문구가 없을 때 요약 줄이 바의 세로 가운데에 오는지 검증"""
        self.footer.set_summary_spec(self._summary_spec())
        self.footer.set_status_text("")
        _APP.processEvents()
        summary = self._rect_in_footer(self.footer.lbl_summary)
        self.assertLessEqual(abs(summary.center().y() - self._content_center_y()), _CENTER_TOLERANCE_PX)

    def test_summary_and_status_block_is_vertically_centered_with_status(self) -> None:
        """상태 문구가 있을 때 요약과 상태 줄의 묶음이 바의 세로 가운데에 오는지 검증"""
        self.footer.set_summary_spec(self._summary_spec())
        self.footer.set_status_text(_NOTICE_TEXT)
        _APP.processEvents()
        block = self._rect_in_footer(self.footer.lbl_summary).united(self._rect_in_footer(self.footer.lbl_status))
        self.assertLessEqual(abs(block.center().y() - self._content_center_y()), _CENTER_TOLERANCE_PX)


if __name__ == "__main__":
    unittest.main()
