"""작업 기록 창의 내보내기 탭, 저장될 CSV 내용 미리 보기와 파일 저장 위젯 모듈"""

import logging
import os
import threading
from collections.abc import Callable

from PyQt6.QtCore import QAbstractItemModel, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.exceptions import format_user_error
from src.core.messages import format_export_done
from src.gui.constants import CSV_PREVIEW_REFRESH_DEBOUNCE_MS, CSV_PREVIEW_ROW_LIMIT
from src.gui.exporters import (
    CsvSnapshot,
    render_csv_text,
    snapshot_table_rows,
    write_csv_file,
)
from src.gui.styles.icons import create_icon_button, get_active_palette, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.table_model import VideoTableModel

logger = logging.getLogger(__name__)

EXPORT_DEFAULT_FILENAME: str = "video_analysis_report.csv"
_ACTION_BUTTON_SIZE: int = 34
_ICON_SIZE_PX: int = 15
_EXPORT_DIALOG_TITLE: str = "CSV 파일로 내보내기"
_CSV_FILE_FILTER: str = "CSV Files (*.csv)"
_EXPORT_TOOLTIP: str = "현재 표의 결과를 CSV 파일로 내보냅니다."
_EMPTY_NOTE: str = "내보낼 분석 결과가 없습니다. 작업 폴더를 분석한 뒤 다시 확인해 주세요."
_BUSY_NOTE: str = "분석 작업이 끝난 뒤 내보낼 수 있습니다."
_WRITING_NOTE: str = "CSV 파일을 저장하는 중입니다. 완료될 때까지 기다려 주세요."
_ENCODING_NOTE: str = "UTF-8(BOM) CSV 파일로 저장되어 엑셀에서 바로 열 수 있습니다."
_TRUNCATED_NOTE_FMT: str = "처음 {shown:,}행만 미리 봅니다. 저장하면 {total:,}행 전체가 저장됩니다."
_SUMMARY_FMT: str = "{rows:,}행, {cols}열 (현재 필터와 정렬 기준)"
_TABLE_TAB_TITLE: str = "표"
_CSV_TEXT_TAB_TITLE: str = "CSV 원문"


class ExportPanel(QWidget):
    """현재 표 결과의 CSV 내용을 미리 보여 주고 파일로 저장하는 패널

    미리 보기와 저장이 같은 스냅샷 경로를 사용해 두 결과가 어긋나지 않게 함
    """

    # - 워커 스레드의 저장 결과(경로, 행 수, 오류)를 GUI 스레드 처리 함수로 전달
    _write_finished = pyqtSignal(str, int, object)

    def __init__(self, on_status: Callable[[str], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_status: Callable[[str], None] = on_status
        self._model: QAbstractItemModel | None = None
        self._base_dir: Callable[[], str] = lambda: ""
        self._busy: bool = False
        self._writing: bool = False
        self._write_finished.connect(self._on_write_finished)
        self._total: int = 0
        self._headers: list[str] = list(VideoTableModel.MENU_HEADERS)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(CSV_PREVIEW_REFRESH_DEBOUNCE_MS)
        self._refresh_timer.timeout.connect(self.refresh_preview)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.lbl_summary = QLabel("", self)
        self.lbl_summary.setObjectName("FooterSummaryText")
        layout.addWidget(self.lbl_summary)

        self.preview_tabs = QTabWidget(self)
        self.preview_tabs.setObjectName("SettingsTabs")
        self.table = QTableWidget(0, len(self._headers), self)
        self.table.setObjectName("ExportPreviewTable")
        self.table.setHorizontalHeaderLabels(self._headers)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        vertical_header = self.table.verticalHeader()
        if vertical_header is not None:
            vertical_header.setVisible(False)
        self.csv_text = QPlainTextEdit(self)
        self.csv_text.setObjectName("LogViewer")
        self.csv_text.setReadOnly(True)
        self.csv_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview_tabs.addTab(self.table, _TABLE_TAB_TITLE)
        self.preview_tabs.addTab(self.csv_text, _CSV_TEXT_TAB_TITLE)
        layout.addWidget(self.preview_tabs, stretch=1)

        footer = QHBoxLayout()
        footer.setSpacing(12)
        self.lbl_note = QLabel("", self)
        self.lbl_note.setObjectName("StatusText")
        self.lbl_note.setWordWrap(True)
        footer.addWidget(self.lbl_note, stretch=1)
        self.btn_export = create_icon_button(
            self,
            _EXPORT_TOOLTIP,
            self._export,
            accessible_name="CSV 파일로 내보내기",
            fixed_size=_ACTION_BUTTON_SIZE,
        )
        footer.addWidget(self.btn_export)
        layout.addLayout(footer)

        self.apply_theme(get_active_palette())

    def bind_source(self, model: QAbstractItemModel, base_dir: Callable[[], str]) -> None:
        """표 모델과 저장 기본 폴더 제공자를 연결하고 미리 보기를 갱신

        - 모델 인스턴스는 한 번 연결하면 바뀌지 않는다고 가정(메인 윈도우의 프록시 모델 기준)
        """
        self._base_dir = base_dir
        if self._model is not model:
            self._model = model
            for signal in (
                model.rowsInserted,
                model.rowsRemoved,
                model.layoutChanged,
                model.modelReset,
                model.dataChanged,
            ):
                signal.connect(lambda *_args: self._refresh_timer.start())
        self.refresh_preview()

    def set_busy(self, busy: bool) -> None:
        """분석 작업 중에는 저장을 막고 안내 문구를 바꿈"""
        self._busy = busy
        self._apply_state()

    def refresh_preview(self) -> None:
        """모델의 현재 행으로 미리 보기 표와 요약 문구를 다시 채움"""
        if self._model is None:
            return
        self._total = self._model.rowCount()
        rows = snapshot_table_rows(
            self._model, self._headers, limit=CSV_PREVIEW_ROW_LIMIT
        ).render_rows()
        self.table.setRowCount(len(rows))
        for row_idx, values in enumerate(rows):
            for col_idx, text in enumerate(values):
                self.table.setItem(row_idx, col_idx, QTableWidgetItem(text))
        self.csv_text.setPlainText(render_csv_text(self._headers, rows))
        self.lbl_summary.setText(
            _SUMMARY_FMT.format(rows=self._total, cols=len(self._headers)) if self._total else ""
        )
        self._apply_state()

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 저장 버튼 아이콘을 갱신"""
        set_button_icon(self.btn_export, "download", palette=palette, size=_ICON_SIZE_PX, pad_right=0)

    def _apply_state(self) -> None:
        self.btn_export.setEnabled(self._total > 0 and not self._busy and not self._writing)
        self.lbl_note.setText(self._note_text())

    def _note_text(self) -> str:
        if self._writing:
            return _WRITING_NOTE
        if self._busy:
            return _BUSY_NOTE
        if self._total == 0:
            return _EMPTY_NOTE
        if self._total > CSV_PREVIEW_ROW_LIMIT:
            return _TRUNCATED_NOTE_FMT.format(shown=CSV_PREVIEW_ROW_LIMIT, total=self._total)
        return _ENCODING_NOTE

    def _export(self) -> None:
        if self._model is None:
            return
        default_dir = self._base_dir().strip() or os.path.expanduser("~")
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            _EXPORT_DIALOG_TITLE,
            os.path.join(default_dir, EXPORT_DEFAULT_FILENAME),
            _CSV_FILE_FILTER,
        )
        if not filepath:
            return
        self._start_write(filepath, snapshot_table_rows(self._model, self._headers))

    def _start_write(self, filepath: str, snapshot: CsvSnapshot) -> None:
        """파일 쓰기를 워커 스레드에서 시작하고 끝날 때까지 저장 버튼을 막음"""
        self._writing = True
        self._apply_state()
        threading.Thread(
            target=self._write_in_background, args=(filepath, snapshot), daemon=True
        ).start()

    def _write_in_background(self, filepath: str, snapshot: CsvSnapshot) -> None:
        """워커 스레드에서 행 문자열 생성과 파일 쓰기를 수행하고 결과 시그널로 전달"""
        try:
            count = write_csv_file(filepath, snapshot)
        except Exception as exc:
            self._write_finished.emit(filepath, 0, exc)
            return
        self._write_finished.emit(filepath, count, None)

    def _on_write_finished(self, filepath: str, count: int, error: object) -> None:
        """저장 결과를 로그, 상태 표시, 오류 대화상자로 전달하고 저장 버튼을 다시 연다"""
        self._writing = False
        self._apply_state()
        if isinstance(error, Exception):
            logger.error("CSV 파일로 내보내기에 실패했습니다: %s", filepath, exc_info=error)
            QMessageBox.critical(
                self,
                "내보내기 오류",
                f"CSV 저장 중 오류가 발생했습니다.\n\n{format_user_error(error)}",
            )
            return
        logger.info("%s", format_export_done(count, filepath))
        self._on_status(f"CSV 파일로 내보내기 완료: {count:,}행 저장됨")
