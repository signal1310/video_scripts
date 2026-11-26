"""작업 기록 창의 FFmpeg 명령 기록 탭: 실행한 외부 명령 표, 단계 필터, 검색, 복사

- 기록은 코어의 메모리 링 버퍼에서 읽고, 워커 스레드의 변경 알림은 신호로 넘겨 받아 짧은 타이머로 묶어 반영
- 표를 다시 그릴 때마다 표시 행을 새로 만들며, 기록 수는 링 버퍼 상한을 넘지 않음
- 영상 열은 명령이 다룬 원본 영상의 이름만 보이고, 전체 경로는 그 칸의 툴팁에 둠
"""

from __future__ import annotations

import weakref
from collections.abc import Callable, Sequence

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QClipboard
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.analysis.file_manager import is_video_file
from src.core.command_history import (
    COMMAND_HISTORY_LIMIT,
    COMMAND_STAGES,
    CommandRecord,
    add_listener,
    clear,
    input_path_of,
    remove_listener,
    snapshot,
    target_name_of,
)
from src.gui.styles.icons import create_icon_button, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.table.table_column_resize import make_columns_resizable

COLUMN_HEADERS: tuple[str, ...] = ("시각", "단계", "영상", "대상 파일", "소요", "종료 코드", "명령줄")
COLUMN_WIDTHS_PX: tuple[int, ...] = (72, 100, 160, 200, 70, 80, 420)
COMMAND_COLUMN: int = 6
TARGET_COLUMN: int = 3
VIDEO_COLUMN: int = 2
FILTER_ALL_LABEL: str = "전체"
SEARCH_PLACEHOLDER: str = "대상 파일이나 명령줄 검색"
COPY_ACCESSIBLE_NAME: str = "명령 복사"
COPY_TOOLTIP: str = "선택한 명령줄을 복사합니다."
CLEAR_ACCESSIBLE_NAME: str = "기록 지우기"
CLEAR_TOOLTIP: str = "이번 실행에서 기록한 FFmpeg 명령을 모두 지웁니다."
EMPTY_RECORDS_TEXT: str = "아직 실행한 FFmpeg 명령이 없습니다."
EMPTY_FILTERED_TEXT: str = "검색과 단계 조건에 맞는 명령이 없습니다."
RUNNING_TEXT: str = "실행 중"
NO_VIDEO_TEXT: str = "-"
VIDEO_TOOLTIP_TEMPLATE: str = "이 명령이 다룬 영상 파일입니다. 전체 경로: {path}"
NO_VIDEO_TOOLTIP: str = "이 명령에서 영상 파일을 찾지 못했습니다. 영상과 무관한 명령입니다."
TIME_FORMAT: str = "%H:%M:%S"
ACTION_BUTTON_SIZE: int = 34
ICON_SIZE_PX: int = 15
REFRESH_INTERVAL_MS: int = 200
ELAPSED_DECIMALS: int = 1


def video_path_of(record: CommandRecord) -> str:
    """기록이 다룬 영상 파일의 전체 경로. 알 수 없으면 빈 문자열

    - 기록할 때 남긴 원본 경로를 먼저 쓰고, 없으면 명령 인자의 첫 입력 경로가 영상 확장자일 때만 씀
    """
    if record.source_path:
        return record.source_path
    candidate = input_path_of(record.args)
    return candidate if is_video_file(candidate) else ""


def video_text_of(record: CommandRecord) -> str:
    """영상 열 표시. 영상을 알 수 없으면 하이픈"""
    name = target_name_of(video_path_of(record))
    return name or NO_VIDEO_TEXT


def video_tooltip_of(record: CommandRecord) -> str:
    """영상 열 툴팁. 칸이 무엇인지와 전체 경로를 알려 줌"""
    path = video_path_of(record)
    return VIDEO_TOOLTIP_TEMPLATE.format(path=path) if path else NO_VIDEO_TOOLTIP


def elapsed_text(seconds: float | None) -> str:
    """소요 시간 표시. 진행 중이면 실행 중 문구"""
    if seconds is None:
        return RUNNING_TEXT
    return f"{seconds:.{ELAPSED_DECIMALS}f}초"


def exit_code_text(code: int | None) -> str:
    """종료 코드 표시. 아직 끝나지 않았으면 하이픈"""
    return "-" if code is None else str(code)


def filter_records(
    records: Sequence[CommandRecord], *, stage: str = FILTER_ALL_LABEL, query: str = ""
) -> list[CommandRecord]:
    """단계와 검색어로 기록을 걸러 냄. 검색어는 대상 파일, 명령줄, 단계 이름을 대상으로 함"""
    needle = query.strip().lower()
    filtered: list[CommandRecord] = []
    for record in records:
        if stage != FILTER_ALL_LABEL and record.stage != stage:
            continue
        if needle and not any(
            needle in text.lower() for text in (record.target_name, record.display, record.stage)
        ):
            continue
        filtered.append(record)
    return filtered


class CommandHistoryPanel(QWidget):
    """FFmpeg 명령 기록 표, 단계 필터, 검색, 복사와 지우기를 제공하는 패널"""

    # - 워커 스레드의 변경 알림을 화면 스레드 처리로 넘기는 신호
    _records_changed = pyqtSignal()

    def __init__(
        self,
        *,
        loader: Callable[[], Sequence[CommandRecord]] = snapshot,
        on_clear: Callable[[], None] = clear,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._loader: Callable[[], Sequence[CommandRecord]] = loader
        self._on_clear: Callable[[], None] = on_clear
        self._signature: tuple[tuple[int, int | None, float | None], ...] | None = None

        self._records_changed.connect(self._arm_refresh)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self.refresh)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        toolbar, self.stage_combo, self.search_edit, self.btn_copy, self.btn_clear = (
            self._build_toolbar()
        )
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(COLUMN_HEADERS), self)
        self.table.setObjectName("CommandHistoryTable")
        self.table.setHorizontalHeaderLabels(list(COLUMN_HEADERS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setWordWrap(False)
        # - 처음에는 기록 순서를 유지하고, 열 머리글을 누르면 그 열 기준으로 정렬
        self.table.setSortingEnabled(True)
        vertical_header = self.table.verticalHeader()
        if vertical_header is not None:
            vertical_header.setVisible(False)
        header = self.table.horizontalHeader()
        if header is not None:
            header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
            make_columns_resizable(header, COLUMN_WIDTHS_PX)
        self.table.itemDoubleClicked.connect(lambda _item: self._copy_selected())
        layout.addWidget(self.table, stretch=1)

        self.lbl_empty = QLabel(EMPTY_RECORDS_TEXT, self)
        self.lbl_empty.setObjectName("StatusText")
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty.setVisible(False)
        layout.addWidget(self.lbl_empty)

        self._listener = self._make_listener()
        add_listener(self._listener)
        self.destroyed.connect(self._detach_listener)
        self.refresh()

    def _make_listener(self) -> Callable[[], None]:
        """패널이 지워진 뒤에도 안전한 변경 리스너. 신호 방출만 맡고 화면 작업은 타이머가 함"""
        ref = weakref.ref(self)

        def _listener() -> None:
            panel = ref()
            if panel is None:
                return
            try:
                panel._records_changed.emit()
            except RuntimeError:
                # - C++ 위젯이 먼저 지워진 패널은 더 알릴 곳이 없으므로 리스너를 스스로 뗌
                remove_listener(_listener)

        return _listener

    def _build_toolbar(
        self,
    ) -> tuple[QHBoxLayout, QComboBox, QLineEdit, QPushButton, QPushButton]:
        """단계 필터, 검색 상자, 복사와 지우기 버튼 줄을 만들고 위젯을 함께 돌려줌"""
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        stage_combo = QComboBox(self)
        stage_combo.setObjectName("CommandStageFilter")
        stage_combo.addItem(FILTER_ALL_LABEL)
        for stage in COMMAND_STAGES:
            stage_combo.addItem(stage)
        stage_combo.currentIndexChanged.connect(lambda _index: self.refresh())
        toolbar.addWidget(stage_combo)
        search_edit = QLineEdit(self)
        search_edit.setObjectName("CommandHistorySearch")
        search_edit.setPlaceholderText(SEARCH_PLACEHOLDER)
        search_edit.setClearButtonEnabled(True)
        search_edit.textChanged.connect(lambda _text: self.refresh())
        toolbar.addWidget(search_edit, stretch=1)
        btn_copy = create_icon_button(
            self,
            COPY_TOOLTIP,
            self._copy_selected,
            accessible_name=COPY_ACCESSIBLE_NAME,
            fixed_size=ACTION_BUTTON_SIZE,
        )
        btn_clear = create_icon_button(
            self,
            CLEAR_TOOLTIP,
            self._clear_records,
            accessible_name=CLEAR_ACCESSIBLE_NAME,
            fixed_size=ACTION_BUTTON_SIZE,
        )
        toolbar.addWidget(btn_copy)
        toolbar.addWidget(btn_clear)
        return toolbar, stage_combo, search_edit, btn_copy, btn_clear


    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 동작 버튼 아이콘을 갱신"""
        for btn, icon_name in ((self.btn_copy, "copy"), (self.btn_clear, "trash")):
            set_button_icon(btn, icon_name, palette=palette, size=ICON_SIZE_PX, pad_right=0)

    def set_records(self, records: Sequence[CommandRecord]) -> None:
        """표시할 기록을 받아 표를 다시 그림. 표시 내용이 그대로면 건너뜀

        - 표시 행은 링 버퍼 상한까지만 두어 오래된 기록부터 버림
        """
        capped = list(records)[-COMMAND_HISTORY_LIMIT:]
        filtered = filter_records(
            capped, stage=self.stage_combo.currentText(), query=self.search_edit.text()
        )
        signature = tuple(
            (record.seq, record.exit_code, record.elapsed_sec) for record in filtered
        )
        if signature == self._signature:
            return
        self._signature = signature
        self._fill_table(filtered)
        self._update_empty_label(len(capped), len(filtered))

    def refresh(self) -> None:
        """현재 버퍼 내용을 읽어 표에 반영"""
        self.set_records(self._loader())

    def _update_empty_label(self, total: int, shown: int) -> None:
        """기록이 없을 때와 조건에 맞는 기록이 없을 때의 안내 문구"""
        if total == 0:
            self.lbl_empty.setText(EMPTY_RECORDS_TEXT)
        elif shown == 0:
            self.lbl_empty.setText(EMPTY_FILTERED_TEXT)
        self.lbl_empty.setVisible(shown == 0)

    def _fill_table(self, records: Sequence[CommandRecord]) -> None:
        """기록 행을 다시 채움. 영상, 명령줄, 대상 파일은 전체 값을 툴팁으로 보여 줌

        - 채우는 동안 정렬을 끄지 않으면 셀을 넣을 때마다 행이 움직여 값이 다른 행에 섞임
        """
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        try:
            self.table.setRowCount(len(records))
            for row, record in enumerate(records):
                values = (
                    record.timestamp.strftime(TIME_FORMAT),
                    record.stage,
                    video_text_of(record),
                    record.target_name,
                    elapsed_text(record.elapsed_sec),
                    exit_code_text(record.exit_code),
                    record.display,
                )
                for column, text in enumerate(values):
                    item = QTableWidgetItem(text)
                    if column == COMMAND_COLUMN:
                        item.setToolTip(record.display)
                    elif column == VIDEO_COLUMN:
                        item.setToolTip(video_tooltip_of(record))
                    elif column == TARGET_COLUMN and record.target_name:
                        item.setToolTip(record.target_name)
                    self.table.setItem(row, column, item)
        finally:
            self.table.setSortingEnabled(sorting)

    def selected_command_lines(self) -> list[str]:
        """선택한 행의 전체 명령줄. 행 번호 순서로 돌려줌"""
        selection = self.table.selectionModel()
        if selection is None:
            return []
        rows = sorted({index.row() for index in selection.selectedRows()})
        lines: list[str] = []
        for row in rows:
            item = self.table.item(row, COMMAND_COLUMN)
            if item is not None:
                lines.append(item.text())
        return lines

    def _copy_selected(self) -> None:
        """선택한 행의 명령줄을 한 줄씩 클립보드에 복사"""
        lines = self.selected_command_lines()
        if not lines:
            return
        clipboard: QClipboard | None = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText("\n".join(lines))

    def _clear_records(self) -> None:
        """버퍼를 비우고 표를 빈 상태로 되돌림"""
        self._on_clear()
        self._signature = None
        self.refresh()

    def _detach_listener(self, _obj: object | None = None) -> None:
        """패널이 지워질 때 변경 리스너 해제"""
        remove_listener(self._listener)

    def _arm_refresh(self) -> None:
        """변경 알림을 짧은 타이머로 묶어 한 번만 다시 그림"""
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

