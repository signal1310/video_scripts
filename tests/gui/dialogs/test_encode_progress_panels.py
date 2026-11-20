"""재인코딩 진행 창 하단 패널 검증: 전체 속도 칸, 결과 목록의 행 내용과 필터, 바뀐 행만 갈기, 폭에 따른 행 높이, 조작 버튼과 신호

- 화면은 띄우지 않고 목록 위젯의 항목과 위젯 속성으로 확인. 폭 변화는 목록 크기를 바꾸고 이벤트를 보내 확인
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from src.core.encode.encode_progress_values import TONE_GREW, TONE_SAVED, ResultLine
from src.gui.dialogs.encode_progress_panels import ResultList, TotalsPanel
from src.gui.styles.palettes import LIGHT_PALETTE

_APP = QApplication.instance() or QApplication([])


def _done(name: str = "a.mp4") -> ResultLine:
    return ResultLine(name, "결과: _재인코딩결과/" + name, "완료", "100.0 MB → 40.0 MB", "", delta="-60.0 MB (-60%)", tone=TONE_SAVED)


class TotalsPanelTests(unittest.TestCase):
    """전체 속도와 완료분 절약 칸이 받은 문구를 그대로 보여 주고, 이름과 값 색을 팔레트 토큰으로 칠함"""

    def test_values_are_shown_as_given(self) -> None:
        panel = TotalsPanel()
        panel.set_values("현재 1.00배, 평균 0.50배", "실제 1.0 GB, 예상 1.2 GB")
        self.assertEqual(panel.speed_text(), "현재 1.00배, 평균 0.50배")
        self.assertEqual(panel.saving_text(), "실제 1.0 GB, 예상 1.2 GB")

    def test_palette_colors_captions_muted_and_values_primary(self) -> None:
        panel = TotalsPanel()
        panel.apply_palette(LIGHT_PALETTE)
        self.assertIn(f"color: {LIGHT_PALETTE.text_muted}", panel._captions[0].styleSheet())
        self.assertIn(f"color: {LIGHT_PALETTE.text_primary}", panel._saving.styleSheet())


class ResultListTests(unittest.TestCase):
    """결과 목록의 행 내용: 이름, 상태 배지 글자, 크기 줄(변화 부분 강조), 사유 줄, 결과 경로 툴팁"""

    def setUp(self) -> None:
        self.results = ResultList()
        self.addCleanup(self.results.deleteLater)

    def test_rows_show_name_status_size_and_reason_and_keep_the_result_path_in_the_tooltip(self) -> None:
        self.results.set_lines(
            [
                _done(),
                ResultLine("b.mp4", "", "실패", "", "인코딩 실패(종료 코드 1)"),
            ]
        )
        self.assertEqual(self.results.row_count(), 2)
        self.assertEqual(self.results.name_text(0), "a.mp4")
        self.assertEqual(self.results.status_text(0), "완료")
        self.assertEqual(self.results.size_text(0), "100.0 MB → 40.0 MB -60.0 MB (-60%)")
        self.assertEqual(self.results.reason_text(0), "")
        self.assertEqual(self.results.name_text(1), "b.mp4")
        self.assertEqual(self.results.size_text(1), "")
        self.assertEqual(self.results.reason_text(1), "인코딩 실패(종료 코드 1)")
        item = self.results._list.item(0)
        assert item is not None
        widget = self.results._list.itemWidget(item)
        assert widget is not None
        self.assertEqual(widget.toolTip(), "결과: _재인코딩결과/a.mp4")
        self.results.set_lines([])
        self.assertEqual(self.results.row_count(), 0)
        self.assertEqual(self.results.name_text(0), "")

    def test_size_line_marks_the_change_with_the_tone_color_on_the_palette(self) -> None:
        """절약은 절약 색, 증가는 증가 색으로 변화 부분만 칠함"""
        self.results.apply_palette(LIGHT_PALETTE)
        self.results.set_lines(
            [
                _done(),
                ResultLine("c.mp4", "", "결과 폐기", "판정 예상 22.2 MB, 결과 81.3 MB", "", delta="(원본보다 6.3 MB 큼)", tone=TONE_GREW),
            ]
        )
        self.assertIn(f'color: {LIGHT_PALETTE.fg_saved_pos}', self._size_label(0).text())
        self.assertIn(f'color: {LIGHT_PALETTE.fg_saved_neg}', self._size_label(1).text())

    def test_reason_line_is_hidden_when_empty_and_shown_when_present(self) -> None:
        self.results.set_lines([_done(), ResultLine("b.mp4", "", "건너뜀", "", "같은 이름의 파일이 있음")])
        self.assertTrue(self._reason_label(0).isHidden())
        self.assertFalse(self._reason_label(1).isHidden())

    def _size_label(self, row: int) -> QLabel:
        return self._row_labels(row)[2]

    def _reason_label(self, row: int) -> QLabel:
        return self._row_labels(row)[3]

    def _row_labels(self, row: int) -> list[QLabel]:
        """행 위젯의 글자 칸: 배지, 이름, 크기 줄, 사유 줄 순서"""
        item = self.results._list.item(row)
        assert item is not None
        widget = self.results._list.itemWidget(item)
        assert widget is not None
        return widget.findChildren(QLabel)


class ResultRefreshTests(unittest.TestCase):
    """갱신 때 바뀐 행만 다시 만들어 선택과 스크롤을 지키고, 폭이 바뀌면 행 높이를 다시 잼"""

    def setUp(self) -> None:
        self.results = ResultList()
        self.addCleanup(self.results.deleteLater)
        self.lines = [_done("a.mp4"), _done("b.mp4"), _done("c.mp4")]
        self.results.set_lines(self.lines)

    def _widget(self, row: int) -> QWidget | None:
        item = self.results._list.item(row)
        assert item is not None
        return self.results._list.itemWidget(item)

    def test_unchanged_rows_keep_their_widgets_and_the_selection(self) -> None:
        """같은 자리의 같은 줄은 위젯을 바꾸지 않고, 선택한 행도 그대로 남음"""
        self.results._list.setCurrentRow(1)
        before = self._widget(0)
        changed = [self.lines[0], ResultLine("b.mp4", "", "실패", "", "사유"), self.lines[2]]
        self.results.set_lines(changed)
        self.assertIs(self._widget(0), before)
        self.assertEqual(self.results._list.currentRow(), 1)
        self.assertEqual(self.results.status_text(1), "실패")

    def test_adding_and_removing_rows_at_the_end_keeps_the_leading_rows(self) -> None:
        first = self._widget(0)
        self.results.set_lines(self.lines[:1])
        self.assertEqual(self.results.row_count(), 1)
        self.assertIs(self._widget(0), first)
        self.results.set_lines([*self.lines, _done("d.mp4")])
        self.assertEqual(self.results.row_count(), 4)
        self.assertEqual(self.results.name_text(3), "d.mp4")

    def test_narrow_width_gives_wrapped_rows_a_taller_height(self) -> None:
        """창 폭이 줄면 줄바꿈된 사유가 더 많은 높이를 차지하도록 행 높이를 다시 잼"""
        long_reason = "인코딩 실패(종료 코드 1), " * 12
        self.results.set_lines([ResultLine("long.mp4", "", "실패", "", long_reason)])
        self.results.resize(640, 400)
        self.results.show()
        self.results._refit_rows()
        wide = self._row_height(0)
        self.results.resize(220, 400)
        QApplication.processEvents()
        self.results._refit_rows()
        narrow = self._row_height(0)
        self.assertGreater(narrow, wide)

    def _row_height(self, row: int) -> int:
        item = self.results._list.item(row)
        assert item is not None
        return item.sizeHint().height()


class ResultFilterTests(unittest.TestCase):
    """상태 칩은 전체와 상태별 개수를 보여 주고, 고른 상태의 행만 보이게 함. 없는 상태를 고르면 전체"""

    def setUp(self) -> None:
        self.results = ResultList()
        self.addCleanup(self.results.deleteLater)
        self.results.set_lines(
            [
                _done("a.mp4"),
                ResultLine("b.mp4", "", "실패", "", "사유"),
                _done("c.mp4"),
            ]
        )

    def test_chips_show_all_then_each_status_with_its_count(self) -> None:
        self.assertEqual(self.results.filter_chip_texts(), ["전체 3", "완료 2", "실패 1"])

    def test_choosing_a_status_hides_the_other_rows_and_all_shows_everything_again(self) -> None:
        self.results.set_filter("완료")
        self.assertEqual(self.results.visible_rows(), [0, 2])
        self.results.set_filter("")
        self.assertEqual(self.results.visible_rows(), [0, 1, 2])

    def test_unknown_status_falls_back_to_all(self) -> None:
        self.results.set_filter("교체 대기")
        self.assertEqual(self.results.visible_rows(), [0, 1, 2])

    def test_chip_click_applies_the_filter(self) -> None:
        self.results._chips["실패"].click()
        self.assertEqual(self.results.visible_rows(), [1])


class ResultActionTests(unittest.TestCase):
    """교체 대기 행의 재시도와 교체 포기 버튼, 행 더블클릭과 스페이스바의 원본 경로 전달"""

    def setUp(self) -> None:
        self.results = ResultList()
        self.addCleanup(self.results.deleteLater)
        self.pending = os.path.join("work", "a.mp4")
        self.done = os.path.join("work", "b.mp4")
        self.results.set_lines(
            [
                ResultLine("a.mp4", "", "교체 대기", "", "교체 실패", source_path=self.pending, replace_pending=True),
                ResultLine("b.mp4", "", "완료", "", "", source_path=self.done),
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
        self.results.set_lines([ResultLine("a.mp4", "", "실패", "", "", source_path=self.pending)])
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
