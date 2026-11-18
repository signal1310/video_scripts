"""작업 기록 창: 로그 탭 동작, 아이콘 전용 버튼, CSV 내보내기 미리 보기와 저장 일치, 저장 차단 조건을 검증"""

import csv
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QApplication, QPlainTextEdit, QPushButton

from src.core.config import ClassifierConfig
from src.core.evaluator import VideoEvaluator
from src.gui.constants import CSV_PREVIEW_ROW_LIMIT
from src.gui.dialogs.export_panel import ExportPanel
from src.gui.dialogs.log_dialog import LogDialog, LogTab
from src.gui.exporters import snapshot_table_rows
from src.gui.log_file import open_log_file
from src.gui.proxy_model import VideoSortFilterProxyModel
from src.gui.styles.icons import SVG_TEMPLATES
from src.gui.table_model import VideoTableModel
from tests._factories import make_video

_APP = QApplication.instance() or QApplication([])
_COLS: int = len(VideoTableModel.MENU_HEADERS)
_WRITE_WAIT_SECONDS: float = 5.0


def _make_model(rows: int) -> QStandardItemModel:
    model = QStandardItemModel(rows, _COLS)
    for r in range(rows):
        for c in range(_COLS):
            model.setItem(r, c, QStandardItem(f"r{r}c{c}"))
    return model


class ExportPanelTests(unittest.TestCase):
    """미리 보기 표가 실제 저장 파일의 앞부분과 같고, 저장을 막아야 할 상태에서 막히는지 검증"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp: str = tmp.name
        self.status: list[str] = []
        self.panel = ExportPanel(on_status=self.status.append)
        self.addCleanup(self.panel.close)

    def _wait_for_write(self) -> None:
        """워커 스레드 저장 결과(상태 문구)가 GUI 스레드에 전달될 때까지 이벤트를 처리"""
        deadline = time.monotonic() + _WRITE_WAIT_SECONDS
        while not self.status and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)

    def _preview_rows(self) -> list[list[str]]:
        table = self.panel.table
        rows: list[list[str]] = []
        for r in range(table.rowCount()):
            row: list[str] = []
            for c in range(table.columnCount()):
                item = table.item(r, c)
                assert item is not None, f"미리 보기 셀 누락: ({r}, {c})"
                row.append(item.text())
            rows.append(row)
        return rows

    def test_preview_matches_saved_csv_prefix(self) -> None:
        """미리 보기가 저장 파일의 헤더와 처음 행 수만큼 같고, 저장 행 수는 전체인지 검증"""
        self.panel.bind_source(_make_model(150), lambda: self.tmp)
        self.assertEqual(self.panel.table.rowCount(), CSV_PREVIEW_ROW_LIMIT)
        path = os.path.join(self.tmp, "out.csv")
        with (
            patch("src.gui.dialogs.export_panel.QFileDialog.getSaveFileName", return_value=(path, "")),
            self.assertLogs("src.gui.dialogs.export_panel", level="INFO") as logs,
        ):
            self.panel.btn_export.click()
            self._wait_for_write()

        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], list(VideoTableModel.MENU_HEADERS))
        self.assertEqual(len(rows) - 1, 150)
        self.assertEqual(rows[1 : 1 + CSV_PREVIEW_ROW_LIMIT], self._preview_rows())
        self.assertIn("CSV 파일로 내보내기 완료: 150행", logs.output[0])
        self.assertEqual(self.status, ["CSV 파일로 내보내기 완료: 150행 저장됨"])

    def test_raw_csv_preview_matches_saved_file_lines(self) -> None:
        """CSV 원문 탭이 저장 파일의 헤더와 처음 행 수만큼 줄 단위로 같은지 검증"""
        self.panel.bind_source(_make_model(150), lambda: self.tmp)
        path = os.path.join(self.tmp, "raw.csv")
        with patch("src.gui.dialogs.export_panel.QFileDialog.getSaveFileName", return_value=(path, "")):
            self.panel.btn_export.click()
            self._wait_for_write()

        with open(path, encoding="utf-8-sig", newline="") as f:
            saved_lines = f.read().splitlines()
        preview_lines = self.panel.csv_text.toPlainText().splitlines()
        self.assertEqual(preview_lines, saved_lines[: 1 + CSV_PREVIEW_ROW_LIMIT])

    def test_raw_csv_preview_does_not_soft_wrap_long_rows(self) -> None:
        """CSV 원문 탭이 긴 행을 화면 너비에 맞춰 접지 않고 한 줄로 유지하는지 검증"""
        self.assertEqual(self.panel.csv_text.lineWrapMode(), QPlainTextEdit.LineWrapMode.NoWrap)

    def test_empty_model_disables_export_and_explains_next_step(self) -> None:
        """행이 없으면 저장 버튼을 끄고 분석을 먼저 하라는 안내를 보여 주는지 검증"""
        self.panel.bind_source(_make_model(0), lambda: self.tmp)
        self.assertFalse(self.panel.btn_export.isEnabled())
        self.assertIn("작업 폴더를 분석한 뒤", self.panel.lbl_note.text())

    def test_busy_state_blocks_export_until_work_finishes(self) -> None:
        """분석 작업 중에는 저장을 막고 작업이 끝나면 다시 허용하는지 검증"""
        self.panel.bind_source(_make_model(3), lambda: self.tmp)
        self.assertTrue(self.panel.btn_export.isEnabled())
        self.panel.set_busy(True)
        self.assertFalse(self.panel.btn_export.isEnabled())
        self.assertIn("끝난 뒤", self.panel.lbl_note.text())
        self.panel.set_busy(False)
        self.assertTrue(self.panel.btn_export.isEnabled())

    def test_model_changes_refresh_preview_summary(self) -> None:
        """모델 행이 바뀌면 미리 보기 요약이 갱신되는지 검증"""
        model = _make_model(2)
        self.panel.bind_source(model, lambda: self.tmp)
        model.appendRow([QStandardItem("new") for _ in range(_COLS)])
        self.panel.refresh_preview()
        self.assertIn("3행", self.panel.lbl_summary.text())


class CsvSnapshotEquivalenceTests(unittest.TestCase):
    """저장 스냅샷의 행 문자열이 정렬된 화면 표시 값과 모든 열에서 같고, 미리 보기 행 제한을 지키는지 검증"""

    def test_snapshot_rows_match_display_role_for_every_column(self) -> None:
        """정렬된 프록시의 스냅샷 행이 DisplayRole 값과 열마다 같고 행 제한이 앞쪽만 자르는지 확인"""
        names = ["clip10.mp4", "clip2.mp4", "a.mp4", "b.mp4"]
        videos = [make_video(filename=name) for name in names]
        evals = VideoEvaluator.evaluate_all("C:/work", videos, ClassifierConfig()).evaluations
        model = VideoTableModel()
        model.set_data_source(evals, root_dir="C:/work")
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.sort(VideoTableModel.COL_NAME, Qt.SortOrder.DescendingOrder)
        headers = list(VideoTableModel.MENU_HEADERS)
        expected = [
            [
                str(proxy.index(row, col).data(Qt.ItemDataRole.DisplayRole) or "")
                for col in range(len(headers))
            ]
            for row in range(proxy.rowCount())
        ]
        self.assertEqual(snapshot_table_rows(proxy, headers).render_rows(), expected)
        self.assertEqual(snapshot_table_rows(proxy, headers, limit=2).render_rows(), expected[:2])


class LogDialogTests(unittest.TestCase):
    """작업 기록 창의 탭 구성, 아이콘 전용 버튼, 로그 동작을 검증"""

    def setUp(self) -> None:
        self.on_clear = MagicMock()
        self.on_open = MagicMock()
        self.dialog = LogDialog(
            logs=["[INFO] 테스트 로그"],
            on_clear=self.on_clear,
            on_open_log_file=self.on_open,
            on_status=lambda _text: None,
            busy=False,
        )
        self.addCleanup(self.dialog.close)

    def test_tabs_are_log_and_export_and_selectable(self) -> None:
        """탭 이름이 로그와 내보내기이고 지정한 탭으로 전환되는지 검증"""
        self.assertEqual(self.dialog.windowTitle(), "작업 기록")
        self.assertEqual(self.dialog.tabs.tabText(0), "로그")
        self.assertEqual(self.dialog.tabs.tabText(1), "내보내기")
        self.dialog.select_tab(LogTab.EXPORT)
        self.assertEqual(self.dialog.tabs.currentIndex(), 1)

    def test_buttons_are_icon_only_with_accessible_names_and_tooltips(self) -> None:
        """대화상자 안 모든 버튼이 텍스트 없이 접근성 이름과 툴팁을 가지며 닫기 버튼이 없는지 검증"""
        buttons = self.dialog.findChildren(QPushButton)
        self.assertGreaterEqual(len(buttons), 4)
        for btn in buttons:
            with self.subTest(button=btn.accessibleName()):
                self.assertFalse(btn.text())
                self.assertTrue(btn.accessibleName())
                self.assertTrue(btn.toolTip())
        self.assertNotIn("닫기", [btn.accessibleName() for btn in buttons])

    def test_clear_button_clears_screen_and_calls_owner_but_keeps_file_message(self) -> None:
        """비우기 버튼이 화면 로그와 보관 목록을 비우고, 툴팁이 로그 파일 유지를 알리는지 검증"""
        self.assertIn("로그 파일은 유지됩니다", self.dialog.log_panel.btn_clear.toolTip())
        self.dialog.log_panel.btn_clear.click()
        self.on_clear.assert_called_once()
        self.assertIn("작업을 시작하면", self.dialog.text_edit.toPlainText())

    def test_open_log_file_button_calls_owner(self) -> None:
        """로그 파일 열기 버튼이 소유자 콜백을 호출하는지 검증"""
        self.dialog.log_panel.btn_open_file.click()
        self.on_open.assert_called_once()

    def test_trash_icon_is_registered(self) -> None:
        """비우기 아이콘이 SVG 템플릿에 등록되어 있는지 검증"""
        self.assertIn("trash", SVG_TEMPLATES)


class OpenLogFileTests(unittest.TestCase):
    """로그 파일이 없을 때 빈 창 대신 안내를 보여 주는지 검증"""

    def test_missing_log_file_shows_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "logs", "app.log")
            with (
                patch("src.gui.log_file.default_log_file_path", return_value=missing),
                patch("src.gui.log_file.QMessageBox.information") as info,
            ):
                open_log_file(None)
        info.assert_called_once()
        self.assertIn("로그 파일이 없습니다", info.call_args[0][2])


if __name__ == "__main__":
    unittest.main()
