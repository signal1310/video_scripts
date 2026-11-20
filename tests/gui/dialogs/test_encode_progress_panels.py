"""재인코딩 진행 창 하단 패널의 전체 속도 칸과 결과 목록 표시를 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QPushButton

from src.core.encode.encode_progress_values import ResultLine
from src.gui.dialogs.encode_progress_panels import ResultList, TotalsPanel

_APP = QApplication.instance() or QApplication([])


class TotalsPanelTests(unittest.TestCase):
    """전체 속도와 완료분 절약 칸이 받은 문구를 그대로 보여 주는지 검증"""

    def test_values_are_shown_as_given(self) -> None:
        panel = TotalsPanel()
        panel.set_values("현재 1.00배, 평균 0.50배", "실제 1.0 GB, 예상 1.2 GB")
        self.assertEqual(panel.speed_text(), "현재 1.00배, 평균 0.50배")
        self.assertEqual(panel.saving_text(), "실제 1.0 GB, 예상 1.2 GB")


class ResultListTests(unittest.TestCase):
    """결과 목록의 행 수, 파일 이름과 상태 줄, 결과 경로 툴팁을 검증"""

    def test_rows_show_the_name_and_a_status_line_and_keep_the_result_path_in_the_tooltip(self) -> None:
        results = ResultList()
        results.set_lines(
            [
                ResultLine("a.mp4", "결과: _재인코딩결과/a.mp4", "완료", "100.0 MB → 40.0 MB (-60%)", ""),
                ResultLine("b.mp4", "", "실패", "—", "인코딩 실패(종료 코드 1)"),
            ]
        )
        self.assertEqual(results.row_count(), 2)
        self.assertEqual(results.name_text(0), "a.mp4")
        self.assertEqual(results.detail_text(0), "완료, 100.0 MB → 40.0 MB (-60%)")
        self.assertEqual(results.name_text(1), "b.mp4")
        self.assertEqual(results.detail_text(1), "실패, —, 인코딩 실패(종료 코드 1)")
        item = results._list.item(0)
        assert item is not None
        widget = results._list.itemWidget(item)
        assert widget is not None
        self.assertEqual(widget.toolTip(), "결과: _재인코딩결과/a.mp4")
        results.set_lines([])
        self.assertEqual(results.row_count(), 0)
        self.assertEqual(results.name_text(0), "")


class ResultActionTests(unittest.TestCase):
    """교체 대기 행의 재시도와 교체 포기 버튼, 행 더블클릭과 스페이스바의 원본 경로 전달"""

    def setUp(self) -> None:
        self.results = ResultList()
        self.pending = os.path.join("work", "a.mp4")
        self.done = os.path.join("work", "b.mp4")
        self.results.set_lines(
            [
                ResultLine("a.mp4", "", "교체 대기", "—", "교체 실패", source_path=self.pending, replace_pending=True),
                ResultLine("b.mp4", "", "완료", "—", "", source_path=self.done),
            ]
        )

    def test_only_replace_pending_rows_have_retry_and_forfeit_buttons(self) -> None:
        self.assertEqual(len(self.results.action_buttons(0)), 2)
        self.assertEqual(self.results.action_buttons(1), [])

    def test_buttons_emit_the_source_path_of_their_row(self) -> None:
        retried: list[str] = []
        forfeited: list[str] = []
        self.results.retry_requested.connect(retried.append)
        self.results.forfeit_requested.connect(forfeited.append)
        retry, forfeit = self.results.action_buttons(0)
        retry.click()
        forfeit.click()
        self.assertEqual((retried, forfeited), ([self.pending], [self.pending]))

    def test_refreshing_without_replace_pending_removes_the_buttons(self) -> None:
        self.results.set_lines([ResultLine("a.mp4", "", "실패", "—", "", source_path=self.pending)])
        self.assertEqual(self.results.action_buttons(0), [])

    def test_double_click_and_space_hand_the_row_source_to_the_window(self) -> None:
        activated: list[str] = []
        previews: list[str] = []
        self.results.activated.connect(activated.append)
        self.results.preview_requested.connect(previews.append)
        listing = self.results._list
        listing.itemDoubleClicked.emit(listing.item(1))
        listing.setCurrentRow(1)
        QApplication.sendEvent(listing, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
        self.assertEqual((activated, previews), ([self.done], [self.done]))



class ResultLeftoverActionTests(unittest.TestCase):
    """임시 파일이 남은 행은 위치 열기, 같은 자리 임시 파일 실패 행은 휴지통 이동 버튼을 주고, 누르면 원본 경로를 보냄"""

    def setUp(self) -> None:
        self.list = ResultList()
        self.addCleanup(self.list.deleteLater)
        self.reveal: list[str] = []
        self.trash: list[str] = []
        self.list.reveal_requested.connect(self.reveal.append)
        self.list.trash_requested.connect(self.trash.append)

    def _button(self, row: int, accessible_name: str) -> QPushButton:
        matches = [b for b in self.list.action_buttons(row) if b.accessibleName() == accessible_name]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_part_left_row_offers_reveal_and_trash_and_sends_the_source(self) -> None:
        self.list.set_lines(
            [ResultLine("a.mp4", "", "실패", "", "임시 파일이 남아 있음", source_path="/r/a.mp4", leftover=True, part_left=True)]
        )
        self.assertEqual(len(self.list.action_buttons(0)), 2)
        self._button(0, "휴지통으로 이동").click()
        self._button(0, "위치 열기").click()
        self.assertEqual(self.trash, ["/r/a.mp4"])
        self.assertEqual(self.reveal, ["/r/a.mp4"])

    def test_replace_row_adds_reveal_after_retry_and_forfeit_and_plain_rows_have_none(self) -> None:
        self.list.set_lines(
            [
                ResultLine("b.mp4", "", "교체 대기", "", "사유", source_path="/r/b.mp4", replace_pending=True, leftover=True),
                ResultLine("c.mp4", "", "완료", "", "", source_path="/r/c.mp4"),
            ]
        )
        self.assertEqual([b.accessibleName() for b in self.list.action_buttons(0)], ["재시도", "교체 포기", "위치 열기"])
        self.assertEqual(self.list.action_buttons(1), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
