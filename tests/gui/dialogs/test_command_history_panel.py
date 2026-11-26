"""FFmpeg 명령 기록 탭: 열 구성, 필터와 검색, 복사, 지우기, 빈 상태, 신호 갱신을 검증"""

import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QPushButton, QTableWidgetItem

from src.core.command_history import (
    COMMAND_HISTORY_LIMIT,
    STAGE_CAPABILITY,
    STAGE_MAIN_ENCODE,
    STAGE_PROBING,
    CommandRecord,
    record_finish,
    record_start,
)
from src.gui.dialogs.command_history_panel import (
    COLUMN_HEADERS,
    COMMAND_COLUMN,
    EMPTY_FILTERED_TEXT,
    EMPTY_RECORDS_TEXT,
    NO_VIDEO_TEXT,
    NO_VIDEO_TOOLTIP,
    TARGET_COLUMN,
    VIDEO_COLUMN,
    VIDEO_TOOLTIP_TEMPLATE,
    CommandHistoryPanel,
    elapsed_text,
    exit_code_text,
    filter_records,
    video_path_of,
    video_text_of,
    video_tooltip_of,
)
from src.gui.dialogs.log_dialog import LogDialog, LogTab
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])
_BASE_TIME = datetime(2026, 11, 25, 12, 0, 0)


def _record(
    seq: int,
    *,
    stage: str = STAGE_MAIN_ENCODE,
    target: str = "clip.mp4",
    exit_code: int | None = 0,
    elapsed: float | None = 1.5,
    source_path: str | None = None,
) -> CommandRecord:
    args = ("ffmpeg", "-i", f"C:/work/{target}", f"C:/work/{target}.part")
    if source_path is None:
        source_path = f"C:/work/{target}"
    return CommandRecord(
        seq=seq,
        timestamp=_BASE_TIME + timedelta(seconds=seq),
        stage=stage,
        target_name=target,
        args=args,
        display=" ".join(args),
        elapsed_sec=elapsed,
        exit_code=exit_code,
        source_path=source_path,
    )


class CommandHistoryPanelTests(GuiTestCase):
    """표 열, 행 값, 툴팁, 단계 필터, 검색, 복사, 지우기, 빈 상태 문구를 검증"""

    def setUp(self) -> None:
        self.records: list[CommandRecord] = [
            _record(1),
            _record(2, stage=STAGE_PROBING, target="probe.mkv", elapsed=None),
            _record(3, target="다른 영상.mp4", exit_code=1),
        ]
        self.cleared: int = 0

        def on_clear() -> None:
            self.cleared += 1
            self.records.clear()

        self.panel = CommandHistoryPanel(
            loader=lambda: tuple(self.records), on_clear=on_clear
        )
        self.addCleanup(self.panel.close)
        self.panel.set_records(self.records)

    def _item(self, row: int, column: int) -> QTableWidgetItem:
        """표의 셀 항목. 없으면 실패로 알림"""
        item = self.panel.table.item(row, column)
        assert item is not None, f"셀 항목 누락: ({row}, {column})"
        return item

    def _header_text(self, index: int) -> str:
        """열 제목 문자열. 없으면 실패로 알림"""
        item = self.panel.table.horizontalHeaderItem(index)
        assert item is not None, f"열 제목 누락: {index}"
        return item.text()

    def test_columns_and_row_values_match_records(self) -> None:
        """열 제목과 행 값이 기록의 시각, 단계, 영상, 대상, 소요, 종료 코드, 명령줄과 같은지 검증"""
        header_count = len(COLUMN_HEADERS)
        self.assertEqual([self._header_text(i) for i in range(header_count)], list(COLUMN_HEADERS))
        self.assertEqual(self.panel.table.columnCount(), header_count)
        self.assertEqual(self.panel.table.rowCount(), 3)
        self.assertEqual(self._item(0, 0).text(), "12:00:01")
        self.assertEqual(self._item(0, 1).text(), STAGE_MAIN_ENCODE)
        self.assertEqual(self._item(0, VIDEO_COLUMN).text(), "clip.mp4")
        self.assertEqual(self._item(0, TARGET_COLUMN).text(), "clip.mp4")
        self.assertEqual(self._item(0, 4).text(), "1.5초")
        self.assertEqual(self._item(0, 5).text(), "0")
        self.assertEqual(self._item(0, COMMAND_COLUMN).text(), " ".join(self.records[0].args))
        self.assertEqual(self._item(1, 4).text(), "실행 중")
        self.assertEqual(self._item(2, 5).text(), "1")

    def test_fill_keeps_row_values_together_while_sorted(self) -> None:
        """정렬이 켜진 상태로 다시 채워도 한 행 안의 값이 섞이지 않는지 검증"""
        self.panel.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.panel.set_records(
            [_record(7, target="첫째.mp4"), _record(8, target="둘째.mp4")]
        )
        self.assertEqual(self.panel.table.rowCount(), 2)
        for row in range(self.panel.table.rowCount()):
            name = self._item(row, VIDEO_COLUMN).text()
            self.assertEqual(self._item(row, TARGET_COLUMN).text(), name)
            self.assertIn(f"C:/work/{name}", self._item(row, COMMAND_COLUMN).text())

    def test_command_and_target_cells_have_full_tooltips(self) -> None:
        """명령줄 칸과 대상 칸이 전체 값을 툴팁으로 보여 주는지 검증"""
        self.assertEqual(self._item(0, COMMAND_COLUMN).toolTip(), self.records[0].display)
        self.assertEqual(self._item(0, TARGET_COLUMN).toolTip(), "clip.mp4")

    def test_video_column_shows_basename_and_full_path_tooltip(self) -> None:
        """영상 열이 파일 이름만 보이고 전체 경로는 툴팁에 두는지 검증"""
        item = self._item(0, VIDEO_COLUMN)
        self.assertEqual(item.text(), "clip.mp4")
        self.assertNotIn("C:/work", item.text())
        self.assertEqual(
            item.toolTip(), VIDEO_TOOLTIP_TEMPLATE.format(path="C:/work/clip.mp4")
        )

    def test_video_column_shows_hyphen_without_source(self) -> None:
        """원본 경로와 영상 입력이 모두 없는 명령은 영상 열이 하이픈과 안내 툴팁인지 검증"""
        self.panel.set_records([_record(9, stage=STAGE_CAPABILITY, target="", source_path="")])
        self.assertEqual(self._item(0, VIDEO_COLUMN).text(), NO_VIDEO_TEXT)
        self.assertEqual(self._item(0, VIDEO_COLUMN).toolTip(), NO_VIDEO_TOOLTIP)

    def test_video_column_sorts_by_name(self) -> None:
        """영상 열 머리글을 눌러 정렬하면 이름 순서가 유지된 채 새 기록까지 다시 정렬되는지 검증"""
        self.panel.set_records([_record(4, target="나중.mp4"), _record(5, target="가장.mp4")])
        header = self.panel.table.horizontalHeader()
        assert header is not None
        header.setSortIndicator(VIDEO_COLUMN, Qt.SortOrder.AscendingOrder)
        self.panel.table.sortItems(VIDEO_COLUMN, Qt.SortOrder.AscendingOrder)
        self.assertEqual(self._item(0, VIDEO_COLUMN).text(), "가장.mp4")
        self.assertEqual(self._item(1, VIDEO_COLUMN).text(), "나중.mp4")
        self.panel.set_records(
            [
                _record(4, target="나중.mp4"),
                _record(5, target="가장.mp4"),
                _record(6, target="가운데.mp4"),
            ]
        )
        self.assertEqual(
            [self._item(row, VIDEO_COLUMN).text() for row in range(3)],
            ["가운데.mp4", "가장.mp4", "나중.mp4"],
        )

    def test_stage_filter_and_search_narrow_rows(self) -> None:
        """단계 필터와 검색어가 행을 줄이고 맞는 기록이 없으면 안내를 보여 주는지 검증"""
        self.panel.stage_combo.setCurrentText(STAGE_PROBING)
        self.assertEqual(self.panel.table.rowCount(), 1)
        self.assertEqual(self._item(0, 2).text(), "probe.mkv")
        self.panel.stage_combo.setCurrentText("전체")
        self.panel.search_edit.setText("다른")
        self.assertEqual(self.panel.table.rowCount(), 1)
        self.assertEqual(self._item(0, 2).text(), "다른 영상.mp4")
        self.panel.search_edit.setText("없는검색어")
        self.assertEqual(self.panel.table.rowCount(), 0)
        self.assertEqual(self.panel.lbl_empty.text(), EMPTY_FILTERED_TEXT)

    def test_empty_state_text_when_no_records(self) -> None:
        """기록이 없을 때 빈 상태 안내 문구를 보여 주는지 검증"""
        self.panel.search_edit.setText("")
        self.panel.set_records([])
        self.assertEqual(self.panel.table.rowCount(), 0)
        self.assertEqual(self.panel.lbl_empty.text(), EMPTY_RECORDS_TEXT)

    def test_copy_button_copies_selected_command_lines(self) -> None:
        """행을 선택하고 복사하면 선택한 명령줄만 줄바꿈으로 이어 붙여 복사되는지 검증"""
        self.panel.table.selectRow(0)
        selection = self.panel.table.selectionModel()
        assert selection is not None
        model = self.panel.table.model()
        assert model is not None
        selection.select(
            model.index(2, 0),
            selection.SelectionFlag.Select | selection.SelectionFlag.Rows,
        )
        self.assertEqual(len(self.panel.selected_command_lines()), 2)
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        clipboard.setText("")
        self.panel.btn_copy.click()
        self.assertEqual(
            clipboard.text(), "\n".join([self.records[0].display, self.records[2].display])
        )

    def test_copy_button_without_selection_keeps_clipboard(self) -> None:
        """선택이 없으면 복사 버튼이 클립보드를 바꾸지 않는지 검증"""
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        clipboard.setText("기존 내용")
        self.panel.btn_copy.click()
        self.assertEqual(clipboard.text(), "기존 내용")

    def test_clear_button_clears_records_and_table(self) -> None:
        """지우기 버튼이 소유자 콜백을 부르고 표를 빈 상태로 만드는지 검증"""
        self.panel.btn_clear.click()
        self.assertEqual(self.cleared, 1)
        self.assertEqual(self.panel.table.rowCount(), 0)
        self.assertEqual(self.panel.lbl_empty.text(), EMPTY_RECORDS_TEXT)

    def test_buttons_are_icon_only_with_accessible_names_and_tooltips(self) -> None:
        """패널의 모든 버튼이 텍스트 없이 접근성 이름과 존댓말 툴팁을 가지는지 검증"""
        buttons = self.panel.findChildren(QPushButton)
        self.assertGreaterEqual(len(buttons), 2)
        for btn in buttons:
            with self.subTest(button=btn.accessibleName()):
                self.assertFalse(btn.text())
                self.assertTrue(btn.accessibleName())
                self.assertTrue(btn.toolTip())
        self.assertEqual(self.panel.btn_copy.toolTip(), "선택한 명령줄을 복사합니다.")
        self.assertEqual(self.panel.btn_copy.accessibleName(), "명령 복사")
        self.assertEqual(self.panel.btn_clear.accessibleName(), "기록 지우기")

    def test_row_count_capped_to_buffer_limit(self) -> None:
        """표시 행 수가 링 버퍼 상한을 넘지 않는지 검증"""
        self.panel.set_records([_record(seq) for seq in range(1, COMMAND_HISTORY_LIMIT + 10)])
        self.assertEqual(self.panel.table.rowCount(), COMMAND_HISTORY_LIMIT)

    def test_refresh_only_redraws_when_values_change(self) -> None:
        """같은 기록을 다시 넣으면 표를 다시 만들지 않고, 바뀐 값만 다시 그리는지 검증"""
        first_item = self.panel.table.item(0, 0)
        self.panel.set_records(self.records)
        self.assertIs(self.panel.table.item(0, 0), first_item)
        self.records[1] = _record(2, stage=STAGE_PROBING, target="probe.mkv", exit_code=0)
        self.panel.set_records(self.records)
        self.assertEqual(self._item(1, 5).text(), "0")

    def test_detach_listener_stops_further_notifications(self) -> None:
        """패널이 지워질 때 호출되는 해제 함수가 리스너를 뗐는지 검증"""
        seen: list[int] = []
        self.panel._records_changed.connect(lambda: seen.append(1))
        self.panel._detach_listener()
        record_finish(record_start(STAGE_PROBING, ["ffprobe", "x.mkv"], target="x.mkv"), 0, 0.1)
        self.assertEqual(seen, [])

    def test_changed_signal_schedules_one_batched_refresh(self) -> None:
        """변경 신호를 받으면 짧은 타이머를 한 번만 걸어 묶어 갱신하는지 검증"""
        self.panel._records_changed.emit()
        self.panel._records_changed.emit()
        self.assertTrue(self.panel._refresh_timer.isActive())
        self.assertGreater(self.panel._refresh_timer.remainingTime(), 0)



class FilterFunctionTests(unittest.TestCase):
    """필터 함수와 표시 문자열 도우미의 순수 동작 검증"""

    def test_filter_matches_target_display_and_stage(self) -> None:
        """검색어가 대상 이름, 명령줄, 단계 이름 어디에 있어도 걸리는지 검증"""
        records = [_record(1), _record(2, stage=STAGE_PROBING, target="probe.mkv")]
        self.assertEqual([r.seq for r in filter_records(records, query="clip")], [1])
        self.assertEqual([r.seq for r in filter_records(records, query="프로빙")], [2])
        self.assertEqual([r.seq for r in filter_records(records, query="ffmpeg")], [1, 2])
        self.assertEqual(filter_records(records, query="  "), records)

    def test_exit_code_text_and_running_label(self) -> None:
        """종료 코드 표시는 값이 없으면 하이픈, 소요는 진행 중 문구인지 검증"""
        self.assertEqual(exit_code_text(None), "-")
        self.assertEqual(exit_code_text(0), "0")
        self.assertEqual(exit_code_text(1), "1")
        self.assertEqual(elapsed_text(None), "실행 중")
        self.assertEqual(elapsed_text(2.25), "2.2초")

    def test_video_helpers_use_recorded_source_path(self) -> None:
        """기록에 남은 원본 경로로 영상 이름과 전체 경로 툴팁을 만드는지 검증"""
        record = _record(1, target="clip.mp4")
        self.assertEqual(video_path_of(record), "C:/work/clip.mp4")
        self.assertEqual(video_text_of(record), "clip.mp4")
        self.assertEqual(video_tooltip_of(record), VIDEO_TOOLTIP_TEMPLATE.format(path="C:/work/clip.mp4"))

    def test_video_helpers_fall_back_to_input_argument(self) -> None:
        """원본 경로가 없으면 첫 입력 인자가 영상 파일일 때만 그 경로를 쓰는지 검증"""
        record = _record(1, target="clip.mp4", source_path="")
        self.assertEqual(video_path_of(record), "C:/work/clip.mp4")
        self.assertEqual(video_text_of(record), "clip.mp4")
        self.assertEqual(
            video_tooltip_of(record), VIDEO_TOOLTIP_TEMPLATE.format(path="C:/work/clip.mp4")
        )

    def test_video_helpers_ignore_non_video_input(self) -> None:
        """원본 경로가 없고 입력이 영상이 아니면 하이픈과 안내 툴팁을 쓰는지 검증"""
        args = ("ffmpeg", "-hide_banner", "-encoders")
        record = CommandRecord(
            seq=3,
            timestamp=_BASE_TIME,
            stage=STAGE_CAPABILITY,
            target_name="",
            args=args,
            display=" ".join(args),
            elapsed_sec=0.4,
            exit_code=0,
        )
        self.assertEqual(video_path_of(record), "")
        self.assertEqual(video_text_of(record), NO_VIDEO_TEXT)
        self.assertEqual(video_tooltip_of(record), NO_VIDEO_TOOLTIP)


class LogDialogCommandTabTests(GuiTestCase):
    """작업 기록 창의 두 번째 탭이 FFmpeg 명령 기록이고 선택할 수 있는지 검증"""

    def setUp(self) -> None:
        self.dialog = LogDialog(
            logs=["[INFO] 테스트 로그"],
            on_clear=None,
            on_open_log_file=lambda: None,
            on_status=lambda _text: None,
            busy=False,
        )
        self.addCleanup(self.dialog.close)

    def test_tab_titles_and_command_tab_selectable(self) -> None:
        """탭 이름이 로그, FFmpeg 명령 기록, 내보내기 순이고 지정 탭으로 전환되는지 검증"""
        self.assertEqual(self.dialog.tabs.tabText(0), "로그")
        self.assertEqual(self.dialog.tabs.tabText(1), "FFmpeg 명령 기록")
        self.assertEqual(self.dialog.tabs.tabText(2), "내보내기")
        self.dialog.select_tab(LogTab.COMMAND)
        self.assertEqual(self.dialog.tabs.currentIndex(), 1)
        self.assertIsInstance(self.dialog.command_panel, CommandHistoryPanel)

    def test_command_tab_buttons_are_icon_only(self) -> None:
        """명령 기록 탭 버튼도 텍스트 없이 접근성 이름과 툴팁을 가지는지 검증"""
        for btn in (self.dialog.command_panel.btn_copy, self.dialog.command_panel.btn_clear):
            with self.subTest(button=btn.accessibleName()):
                self.assertFalse(btn.text())
                self.assertTrue(btn.accessibleName())
                self.assertTrue(btn.toolTip())

    def test_existing_log_panel_untouched(self) -> None:
        """기존 로그 탭 위젯과 동작이 그대로인지 검증"""
        self.assertIn("테스트 로그", self.dialog.text_edit.toPlainText())
        self.dialog.select_tab(LogTab.EXPORT)
        self.assertEqual(self.dialog.tabs.currentIndex(), 2)


if __name__ == "__main__":
    unittest.main()

