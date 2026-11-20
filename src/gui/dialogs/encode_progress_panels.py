"""재인코딩 진행 창 하단 패널: 전체 속도와 완료분 절약 칸, 결과 목록

- 값 문구는 호출자(진행 창)가 만들어 넘기고, 이 모듈은 배치와 표시만 맡음
- 결과 목록의 교체 대기 행에는 재시도와 교체 포기 아이콘 버튼을 두고, 행 더블클릭과 스페이스는 영상 열기와 미리 보기로 넘김
- 화면 스레드에서만 쓴다
"""

from collections.abc import Callable, Sequence

from PyQt6.QtCore import QModelIndex, Qt, pyqtBoundSignal, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.encode.encode_progress_values import ResultLine
from src.gui.styles.icons import create_icon_button, set_button_icon

RESULT_HEADERS: tuple[str, ...] = ("파일", "상태", "크기 변화", "사유", "조작")
RESULT_CAPTION: str = "결과"
TOTALS_CAPTIONS: tuple[str, str] = ("전체 속도", "완료분 절약")
ACTION_COLUMN: int = 4
ACTION_BUTTON_PX: int = 28
RETRY_ACCESSIBLE_NAME: str = "재시도"
RETRY_TIP: str = "결과 파일을 원본 자리로 다시 옮깁니다. 실패하면 교체 대기로 남습니다."
FORFEIT_ACCESSIBLE_NAME: str = "교체 포기"
FORFEIT_TIP: str = "이 영상을 실패로 두고 재시도하지 않습니다. 원본과 결과 파일은 그대로 남습니다."


class TotalsPanel(QWidget):
    """전체 속도와 완료분 절약을 캡션과 값 두 줄로 보여 주는 칸"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._speed: QLabel = QLabel()
        self._saving: QLabel = QLabel()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(QLabel(TOTALS_CAPTIONS[0]), 0, 0)
        grid.addWidget(self._speed, 0, 1)
        grid.addWidget(QLabel(TOTALS_CAPTIONS[1]), 1, 0)
        grid.addWidget(self._saving, 1, 1)
        grid.setColumnStretch(1, 1)

    def set_values(self, speed: str, saving: str) -> None:
        """전체 속도와 완료분 절약 문구를 설정"""
        self._speed.setText(speed)
        self._saving.setText(saving)

    def speed_text(self) -> str:
        """현재 전체 속도 문구"""
        return self._speed.text()

    def saving_text(self) -> str:
        """현재 완료분 절약 문구"""
        return self._saving.text()


class _ResultTable(QTableWidget):
    """결과 표. 스페이스바는 미리 보기 요청으로 넘김"""

    space_pressed = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is not None and event.key() == Qt.Key.Key_Space:
            self.space_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def _emitter(signal: pyqtBoundSignal, source: str) -> Callable[[], None]:
    """버튼 클릭이 원본 경로를 함께 넘기도록 신호 발신 함수를 만듦"""
    return lambda: signal.emit(source)


class ResultList(QWidget):
    """재인코딩 결과 목록. 파일 칸은 이름과 결과 경로를 두 줄로 보여 줌

    - activated: 행 더블클릭, preview_requested: 스페이스바. 두 신호는 원본 경로를 넘김
    - retry_requested, forfeit_requested: 교체 대기 행의 아이콘 버튼
    """

    activated = pyqtSignal(str)
    preview_requested = pyqtSignal(str)
    retry_requested = pyqtSignal(str)
    forfeit_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sources: list[str] = []
        self._table: _ResultTable = _ResultTable(0, len(RESULT_HEADERS))
        self._table.setHorizontalHeaderLabels(list(RESULT_HEADERS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(self._on_double_clicked)
        self._table.space_pressed.connect(self._on_space_pressed)
        vertical = self._table.verticalHeader()
        horizontal = self._table.horizontalHeader()
        if vertical is not None:
            vertical.setVisible(False)
        if horizontal is not None:
            horizontal.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            horizontal.setSectionResizeMode(ACTION_COLUMN, QHeaderView.ResizeMode.ResizeToContents)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(RESULT_CAPTION))
        layout.addWidget(self._table, stretch=1)

    def set_lines(self, lines: Sequence[ResultLine]) -> None:
        """결과 줄을 다시 채움. 파일 칸은 이름 아래 결과 경로를 붙이고, 교체 대기 행에만 조작 버튼을 둠"""
        self._table.setRowCount(len(lines))
        self._sources = [line.source_path for line in lines]
        for index, line in enumerate(lines):
            name = line.name if not line.path_text else f"{line.name}\n{line.path_text}"
            self._table.setItem(index, 0, QTableWidgetItem(name))
            self._table.setItem(index, 1, QTableWidgetItem(line.status))
            self._table.setItem(index, 2, QTableWidgetItem(line.change))
            self._table.setItem(index, 3, QTableWidgetItem(line.reason))
            self._table.removeCellWidget(index, ACTION_COLUMN)
            if line.replace_pending:
                self._table.setCellWidget(index, ACTION_COLUMN, self._action_cell(line.source_path))
        self._table.resizeRowsToContents()

    def row_count(self) -> int:
        """표시 중인 결과 줄 수"""
        return self._table.rowCount()

    def cell_text(self, row: int, column: int) -> str:
        """결과 표의 칸 문구. 칸이 없으면 빈 문자열"""
        item = self._table.item(row, column)
        return "" if item is None else item.text()

    def action_buttons(self, row: int) -> list[QPushButton]:
        """행의 조작 버튼(재시도, 교체 포기 순). 교체 대기 행이 아니면 빈 목록"""
        cell = self._table.cellWidget(row, ACTION_COLUMN)
        return [] if cell is None else cell.findChildren(QPushButton)

    def _action_cell(self, source: str) -> QWidget:
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        retry = create_icon_button(
            cell,
            RETRY_TIP,
            _emitter(self.retry_requested, source),
            accessible_name=RETRY_ACCESSIBLE_NAME,
            fixed_size=ACTION_BUTTON_PX,
        )
        set_button_icon(retry, "rotate-cw", pad_right=0)
        forfeit = create_icon_button(
            cell,
            FORFEIT_TIP,
            _emitter(self.forfeit_requested, source),
            accessible_name=FORFEIT_ACCESSIBLE_NAME,
            fixed_size=ACTION_BUTTON_PX,
        )
        set_button_icon(forfeit, "x", pad_right=0)
        layout.addWidget(retry)
        layout.addWidget(forfeit)
        return cell

    def _on_double_clicked(self, index: QModelIndex) -> None:
        if 0 <= index.row() < len(self._sources) and self._sources[index.row()]:
            self.activated.emit(self._sources[index.row()])

    def _on_space_pressed(self) -> None:
        row = self._table.currentRow()
        if 0 <= row < len(self._sources) and self._sources[row]:
            self.preview_requested.emit(self._sources[row])
