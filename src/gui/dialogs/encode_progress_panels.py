"""재인코딩 진행 창 하단 패널: 전체 속도와 완료분 절약 칸, 결과 목록

- 값 문구는 호출자(진행 창)가 만들어 넘기고, 이 모듈은 배치와 표시만 맡음
- 화면 스레드에서만 쓴다
"""

from collections.abc import Sequence

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.encode.encode_progress_values import ResultLine

RESULT_HEADERS: tuple[str, ...] = ("파일", "상태", "크기 변화", "사유")
RESULT_CAPTION: str = "결과"
TOTALS_CAPTIONS: tuple[str, str] = ("전체 속도", "완료분 절약")


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


class ResultList(QWidget):
    """재인코딩 결과 목록. 파일 칸은 이름과 결과 경로를 두 줄로 보여 줌"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._table: QTableWidget = QTableWidget(0, len(RESULT_HEADERS))
        self._table.setHorizontalHeaderLabels(list(RESULT_HEADERS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        vertical = self._table.verticalHeader()
        horizontal = self._table.horizontalHeader()
        if vertical is not None:
            vertical.setVisible(False)
        if horizontal is not None:
            horizontal.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(RESULT_CAPTION))
        layout.addWidget(self._table, stretch=1)

    def set_lines(self, lines: Sequence[ResultLine]) -> None:
        """결과 줄을 다시 채움. 파일 칸은 이름 아래 결과 경로를 붙임"""
        self._table.setRowCount(len(lines))
        for index, line in enumerate(lines):
            name = line.name if not line.path_text else f"{line.name}\n{line.path_text}"
            self._table.setItem(index, 0, QTableWidgetItem(name))
            self._table.setItem(index, 1, QTableWidgetItem(line.status))
            self._table.setItem(index, 2, QTableWidgetItem(line.change))
            self._table.setItem(index, 3, QTableWidgetItem(line.reason))
        self._table.resizeRowsToContents()

    def row_count(self) -> int:
        """표시 중인 결과 줄 수"""
        return self._table.rowCount()

    def cell_text(self, row: int, column: int) -> str:
        """결과 표의 칸 문구. 칸이 없으면 빈 문자열"""
        item = self._table.item(row, column)
        return "" if item is None else item.text()
