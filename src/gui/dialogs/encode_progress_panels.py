"""재인코딩 진행 창 하단 패널: 전체 속도와 완료분 절약 칸, 결과 목록

- 값 문구는 호출자(진행 창)가 만들어 넘기고, 이 모듈은 배치와 표시만 맡음
- 결과 목록은 행마다 파일 이름, 상태 줄, 조작 아이콘을 두는 두 행 목록. 교체 대기 행에는 재시도와 교체 포기 아이콘 버튼을 두고, 행 더블클릭과 스페이스는 영상 열기와 미리 보기로 넘김
- 화면 스레드에서만 쓴다
"""

from collections.abc import Callable, Sequence

from PyQt6.QtCore import Qt, pyqtBoundSignal, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.encode.encode_progress_values import ResultLine
from src.gui.styles.icons import create_icon_button, set_button_icon

TOTALS_CAPTIONS: tuple[str, str] = ("전체 속도", "완료분 절약")
ACTION_BUTTON_PX: int = 28
ROW_PADDING_PX: int = 6
ROW_GAP_PX: int = 2
DETAIL_SEPARATOR: str = ", "
RETRY_ACCESSIBLE_NAME: str = "재시도"
RETRY_TIP: str = "결과 파일을 원본 자리로 다시 옮깁니다. 실패하면 교체 대기로 남습니다."
FORFEIT_ACCESSIBLE_NAME: str = "교체 포기"
FORFEIT_TIP: str = "이 영상을 실패로 두고 재시도하지 않습니다. 원본과 결과 파일은 그대로 남습니다."
REVEAL_ACCESSIBLE_NAME: str = "위치 열기"
REVEAL_TIP: str = "탐색기에서 이 파일을 선택해 엽니다."
TRASH_ACCESSIBLE_NAME: str = "휴지통으로 이동"
TRASH_TIP: str = "이 영상이 쓰려던 임시 파일을 휴지통으로 옮깁니다. 확인 창을 한 번 거칩니다."


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


class _ResultListWidget(QListWidget):
    """결과 목록. 스페이스바는 미리 보기 요청으로 넘김"""

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


def detail_text(line: ResultLine) -> str:
    """행의 둘째 줄 문구: 상태, 크기 변화, 사유를 쉼표로 이음. 빈 값은 뺌"""
    return DETAIL_SEPARATOR.join(part for part in (line.status, line.change, line.reason) if part)


class ResultList(QWidget):
    """재인코딩 결과 목록. 행마다 파일 이름과 상태 줄을 보여 주고, 결과 경로는 행 툴팁으로 보임

    - activated: 행 더블클릭, preview_requested: 스페이스바. 두 신호는 원본 경로를 넘김
    - retry_requested, forfeit_requested: 교체 대기 행의 아이콘 버튼
    - reveal_requested: 임시 파일이 남은 행의 위치 열기, trash_requested: 같은 자리 임시 파일 때문에 실패한 행의 휴지통 이동
    """

    activated = pyqtSignal(str)
    preview_requested = pyqtSignal(str)
    retry_requested = pyqtSignal(str)
    forfeit_requested = pyqtSignal(str)
    reveal_requested = pyqtSignal(str)
    trash_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lines: list[ResultLine] = []
        self._list: _ResultListWidget = _ResultListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        self._list.space_pressed.connect(self._on_space_pressed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._list, stretch=1)

    def set_lines(self, lines: Sequence[ResultLine]) -> None:
        """결과 줄을 다시 채움. 교체 대기, 임시 파일이 남은 행에만 조작 버튼을 둠"""
        self._lines = list(lines)
        self._list.clear()
        for line in self._lines:
            item = QListWidgetItem()
            row = self._row_widget(line)
            item.setSizeHint(row.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row)

    def row_count(self) -> int:
        """표시 중인 결과 줄 수"""
        return len(self._lines)

    def name_text(self, row: int) -> str:
        """행의 파일 이름. 행이 없으면 빈 문자열"""
        return self._lines[row].name if 0 <= row < len(self._lines) else ""

    def detail_text(self, row: int) -> str:
        """행의 둘째 줄 문구. 행이 없으면 빈 문자열"""
        return detail_text(self._lines[row]) if 0 <= row < len(self._lines) else ""

    def action_buttons(self, row: int) -> list[QPushButton]:
        """행의 조작 버튼. 교체 대기 행은 재시도, 교체 포기, 위치 열기 순이고, 임시 파일 실패 행은 위치 열기와 휴지통 이동"""
        item = self._list.item(row)
        if item is None:
            return []
        widget = self._list.itemWidget(item)
        return [] if widget is None else widget.findChildren(QPushButton)

    def _row_widget(self, line: ResultLine) -> QWidget:
        row = QWidget()
        outer = QHBoxLayout(row)
        outer.setContentsMargins(ROW_PADDING_PX, ROW_PADDING_PX, ROW_PADDING_PX, ROW_PADDING_PX)
        text = QVBoxLayout()
        text.setSpacing(ROW_GAP_PX)
        name = QLabel(line.name)
        name_font = name.font()
        name_font.setBold(True)
        name.setFont(name_font)
        detail = QLabel(detail_text(line))
        detail.setWordWrap(True)
        text.addWidget(name)
        text.addWidget(detail)
        outer.addLayout(text, stretch=1)
        if line.replace_pending or line.leftover or line.part_left:
            outer.addWidget(self._action_cell(line))
        row.setToolTip(line.path_text)
        return row

    def _action_cell(self, line: ResultLine) -> QWidget:
        source = line.source_path
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        if line.replace_pending:
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
        if line.leftover:
            reveal = create_icon_button(
                cell,
                REVEAL_TIP,
                _emitter(self.reveal_requested, source),
                accessible_name=REVEAL_ACCESSIBLE_NAME,
                fixed_size=ACTION_BUTTON_PX,
            )
            set_button_icon(reveal, "folder-open", pad_right=0)
            layout.addWidget(reveal)
        if line.part_left:
            trash = create_icon_button(
                cell,
                TRASH_TIP,
                _emitter(self.trash_requested, source),
                accessible_name=TRASH_ACCESSIBLE_NAME,
                fixed_size=ACTION_BUTTON_PX,
            )
            set_button_icon(trash, "trash", pad_right=0)
            layout.addWidget(trash)
        return cell

    def _source_at(self, row: int) -> str:
        return self._lines[row].source_path if 0 <= row < len(self._lines) else ""

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        source = self._source_at(self._list.row(item))
        if source:
            self.activated.emit(source)

    def _on_space_pressed(self) -> None:
        source = self._source_at(self._list.currentRow())
        if source:
            self.preview_requested.emit(source)
