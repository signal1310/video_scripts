"""재인코딩 진행 창 하단 패널: 전체 속도와 완료분 절약 칸, 결과 목록(상태 필터, 행마다 상태 배지와 크기 줄과 사유 줄)

- 값 문구는 호출자(진행 창)가 만들어 넘기고, 이 모듈은 배치와 표시만 맡음
- 결과 목록은 바뀐 행만 다시 만들어 선택과 스크롤을 유지함. 창 폭이 바뀌면 줄바꿈된 행의 높이를 다시 잼
- 색은 팔레트 토큰만 씀. 배지 색은 메인 표의 재인코딩 상태 배지 정의를 재사용함
- 화면 스레드에서만 쓴다
"""

import html
from collections.abc import Callable, Sequence

from PyQt6.QtCore import QSize, Qt, pyqtBoundSignal, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QResizeEvent
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

from src.core.encode.encode_progress_values import TONE_GREW, TONE_SAVED, ResultLine
from src.gui.dialogs.encode_progress_chips import FilterChip, FlowLayout
from src.gui.styles.icons import create_icon_button, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.table.table_column_formats import encode_state_badge

TOTALS_CAPTIONS: tuple[str, str] = ("전체 속도", "완료분 절약")
ACTION_BUTTON_PX: int = 28
ROW_PADDING_PX: int = 10
ROW_GAP_PX: int = 3
ROW_RADIUS_PX: int = 8
BADGE_RADIUS_PX: int = 9
FILTER_ALL_KEY: str = ""
FILTER_ALL_NAME: str = "전체"
RETRY_ACCESSIBLE_NAME: str = "재시도"
RETRY_TIP: str = "결과 파일을 원본 자리로 다시 옮깁니다. 실패하면 교체 대기로 남습니다."
FORFEIT_ACCESSIBLE_NAME: str = "교체 포기"
FORFEIT_TIP: str = "이 영상을 실패로 두고 재시도하지 않습니다. 원본과 결과 파일은 그대로 남습니다."
REVEAL_ACCESSIBLE_NAME: str = "위치 열기"
REVEAL_TIP: str = "탐색기에서 이 파일을 선택해 엽니다."
TRASH_ACCESSIBLE_NAME: str = "휴지통으로 이동"
TRASH_TIP: str = "이 영상이 쓰려던 임시 파일을 휴지통으로 옮깁니다. 확인 창을 한 번 거칩니다."


class TotalsPanel(QWidget):
    """전체 속도와 완료분 절약을 이름과 값 두 줄로 보여 주는 칸. 이름은 흐리게, 값은 진하게 둠"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EncodeTotalsPanel")
        self._captions: tuple[QLabel, QLabel] = (QLabel(TOTALS_CAPTIONS[0]), QLabel(TOTALS_CAPTIONS[1]))
        self._speed: QLabel = QLabel()
        self._saving: QLabel = QLabel()
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 8, 12, 8)
        grid.addWidget(self._captions[0], 0, 0)
        grid.addWidget(self._speed, 0, 1)
        grid.addWidget(self._captions[1], 1, 0)
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

    def apply_palette(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 칠함"""
        self.setStyleSheet(
            f"QWidget#EncodeTotalsPanel {{ background: {palette.bg_card}; border: 1px solid {palette.border_subtle};"
            f" border-radius: {ROW_RADIUS_PX}px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        for caption in self._captions:
            caption.setStyleSheet(f"color: {palette.text_muted};")
        for value in (self._speed, self._saving):
            value.setStyleSheet(f"color: {palette.text_primary};")


class _ResultListWidget(QListWidget):
    """결과 목록. 스페이스바는 미리 보기 요청으로 넘기고, 창 폭이 바뀌면 알림"""

    space_pressed = pyqtSignal()
    resized = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is not None and event.key() == Qt.Key.Key_Space:
            self.space_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        super().resizeEvent(event)
        self.resized.emit()


def _emitter(signal: pyqtBoundSignal, source: str) -> Callable[[], None]:
    """버튼 클릭이 원본 경로를 함께 넘기도록 신호 발신 함수를 만듦"""
    return lambda: signal.emit(source)


class ResultList(QWidget):
    """재인코딩 결과 목록. 상태 필터 칩으로 보는 상태를 고르고, 행마다 상태 배지, 이름, 크기 줄, 사유 줄을 보여 줌

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
        self._palette: ThemePalette | None = None
        self._filter: str = FILTER_ALL_KEY
        self._chips: dict[str, FilterChip] = {}
        self._chip_host: QWidget = QWidget()
        self._chip_flow: FlowLayout = FlowLayout(self._chip_host)
        self._list: _ResultListWidget = _ResultListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        self._list.space_pressed.connect(self._on_space_pressed)
        self._list.resized.connect(self._refit_rows)
        self._all_chip: FilterChip = self._make_chip(FILTER_ALL_KEY, FILTER_ALL_NAME)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._chip_host)
        layout.addWidget(self._list, stretch=1)

    def set_lines(self, lines: Sequence[ResultLine]) -> None:
        """결과 줄을 맞춤. 같은 자리에서 바뀌지 않은 줄은 그대로 두어 선택과 스크롤을 지키고, 바뀐 줄과 끝의 추가, 삭제만 다시 만듦"""
        self._update(lines, force=False)

    def row_count(self) -> int:
        """표시 중인 결과 줄 수"""
        return len(self._lines)

    def name_text(self, row: int) -> str:
        """행의 파일 이름. 행이 없으면 빈 문자열"""
        return self._lines[row].name if 0 <= row < len(self._lines) else ""

    def status_text(self, row: int) -> str:
        """행의 상태 표기(배지 글자). 행이 없으면 빈 문자열"""
        return self._lines[row].status if 0 <= row < len(self._lines) else ""

    def size_text(self, row: int) -> str:
        """행의 크기 줄 문구: 크기 값과 변화 부분을 한 칸 띄어 이음. 값이 없으면 빈 문자열"""
        if not 0 <= row < len(self._lines):
            return ""
        line = self._lines[row]
        return " ".join(part for part in (line.change, line.delta) if part)

    def reason_text(self, row: int) -> str:
        """행의 사유 줄 문구. 행이 없으면 빈 문자열"""
        return self._lines[row].reason if 0 <= row < len(self._lines) else ""

    def action_buttons(self, row: int) -> list[QPushButton]:
        """행의 조작 버튼. 교체 대기 행은 재시도, 교체 포기, 위치 열기 순이고, 임시 파일 실패 행은 위치 열기와 휴지통 이동"""
        item = self._list.item(row)
        if item is None:
            return []
        widget = self._list.itemWidget(item)
        return [] if widget is None else widget.findChildren(QPushButton)

    def visible_rows(self) -> list[int]:
        """현재 필터에 보이는 행 번호"""
        return [row for row in range(self._list.count()) if not self._list.isRowHidden(row)]

    def set_filter(self, status: str) -> None:
        """상태 필터를 바꿈. 빈 문자열이면 전체. 없는 상태를 고르면 전체로 돌아감"""
        known = {line.status for line in self._lines}
        self._filter = status if status in known else FILTER_ALL_KEY
        self._apply_filter()
        self._sync_chip_checks()

    def filter_chip_texts(self) -> list[str]:
        """필터 칩 문구 목록(전체 먼저, 그다음 상태 순서)"""
        return [chip.text() for chip in self._chip_order()]

    def apply_palette(self, palette: ThemePalette) -> None:
        """팔레트를 저장하고 칩과 목록 바탕을 맞춘 뒤, 배지와 글자색을 새로 맞추려고 행을 다시 만듦"""
        self._palette = palette
        self._list.setStyleSheet(
            f"QListWidget {{ background: {palette.bg_card}; border: 1px solid {palette.border_subtle};"
            f" border-radius: {ROW_RADIUS_PX}px; }}"
        )
        self._all_chip.apply_palette(palette)
        for chip in self._chips.values():
            chip.apply_palette(palette)
        self._update(self._lines, force=True)

    def _update(self, lines: Sequence[ResultLine], *, force: bool) -> None:
        new_lines = list(lines)
        for index, line in enumerate(new_lines):
            if not force and index < len(self._lines) and self._lines[index] == line:
                continue
            widget = self._row_widget(line)
            if index < self._list.count():
                item = self._list.item(index)
                old = self._list.itemWidget(item)
                self._list.setItemWidget(item, widget)
                if old is not None:
                    old.deleteLater()
            else:
                item = QListWidgetItem()
                self._list.addItem(item)
                self._list.setItemWidget(item, widget)
        while self._list.count() > len(new_lines):
            last = self._list.item(self._list.count() - 1)
            widget = self._list.itemWidget(last)
            self._list.takeItem(self._list.row(last))
            if widget is not None:
                widget.deleteLater()
        self._lines = new_lines
        self._refit_rows()
        self._refresh_chips()
        self._apply_filter()

    def _refit_rows(self) -> None:
        """행 높이를 목록 폭에 맞춰 다시 잼. 줄바꿈된 사유와 이름이 잘리지 않게 함"""
        viewport = self._list.viewport()
        width = 0 if viewport is None else viewport.width()
        for index in range(self._list.count()):
            item = self._list.item(index)
            widget = None if item is None else self._list.itemWidget(item)
            if item is None or widget is None:
                continue
            height = widget.heightForWidth(width) if widget.hasHeightForWidth() else widget.sizeHint().height()
            item.setSizeHint(QSize(width, max(height, 0)))

    def _row_widget(self, line: ResultLine) -> QWidget:
        row = QWidget()
        row.setObjectName("EncodeResultRow")
        outer = QVBoxLayout(row)
        outer.setContentsMargins(ROW_PADDING_PX, ROW_PADDING_PX, ROW_PADDING_PX, ROW_PADDING_PX)
        outer.setSpacing(ROW_GAP_PX)
        head = QHBoxLayout()
        head.setSpacing(ROW_GAP_PX * 2)
        badge = QLabel(line.status)
        badge.setObjectName("EncodeStatusBadge")
        name = QLabel(line.name)
        name.setWordWrap(True)
        name_font = name.font()
        name_font.setBold(True)
        name.setFont(name_font)
        head.addWidget(badge)
        head.addWidget(name, stretch=1)
        if line.replace_pending or line.leftover or line.part_left:
            head.addWidget(self._action_cell(line))
        outer.addLayout(head)
        size = QLabel(self._size_html(line))
        size.setTextFormat(Qt.TextFormat.RichText)
        size.setWordWrap(True)
        size.setVisible(bool(line.change))
        outer.addWidget(size)
        reason = QLabel(line.reason)
        reason.setWordWrap(True)
        reason.setVisible(bool(line.reason))
        outer.addWidget(reason)
        if self._palette is not None:
            self._style_row(row, badge, reason, line)
        row.setToolTip(line.path_text)
        return row

    def _style_row(self, row: QWidget, badge: QLabel, reason: QLabel, line: ResultLine) -> None:
        palette = self._palette
        if palette is None:
            return
        colors = encode_state_badge(line.status, palette)
        if colors is not None:
            background, foreground, border = colors
            badge.setStyleSheet(
                f"QLabel#EncodeStatusBadge {{ background: {background}; color: {foreground};"
                f" border: 1px solid {border}; border-radius: {BADGE_RADIUS_PX}px; padding: 1px 8px; font-weight: 600; }}"
            )
        reason.setStyleSheet(f"color: {palette.text_muted};")
        row.setStyleSheet(f"QWidget#EncodeResultRow {{ border-bottom: 1px solid {palette.border_subtle}; }}")

    def _size_html(self, line: ResultLine) -> str:
        """크기 줄 글: 크기 값은 본문 색, 변화 부분은 절약이면 절약 색, 증가면 증가 색"""
        text = html.escape(line.change)
        if not line.delta:
            return text
        delta = html.escape(line.delta)
        if self._palette is None or line.tone not in (TONE_SAVED, TONE_GREW):
            return f"{text} {delta}" if text else delta
        color = self._palette.fg_saved_pos if line.tone == TONE_SAVED else self._palette.fg_saved_neg
        return f'{text} <span style="color: {color}; font-weight: 600;">{delta}</span>'.strip()

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

    def _make_chip(self, key: str, name: str) -> FilterChip:
        chip = FilterChip(name, self._chip_host)
        chip.setCheckable(True)
        chip.clicked.connect(lambda _checked=False, k=key: self.set_filter(k))
        self._chip_flow.addWidget(chip)
        if self._palette is not None:
            chip.apply_palette(self._palette)
        return chip

    def _refresh_chips(self) -> None:
        """전체 칩과 상태별 칩의 개수를 맞춤. 상태가 사라진 칩은 지움"""
        counts: dict[str, int] = {}
        for line in self._lines:
            counts[line.status] = counts.get(line.status, 0) + 1
        self._all_chip.setText(f"{FILTER_ALL_NAME} {len(self._lines)}")
        for key in list(self._chips):
            if key not in counts:
                chip = self._chips.pop(key)
                self._chip_flow.removeWidget(chip)
                chip.deleteLater()
        for key, count in counts.items():
            if key not in self._chips:
                self._chips[key] = self._make_chip(key, key)
            self._chips[key].setText(f"{key} {count}")
        self._sync_chip_checks()
        self._chip_host.setVisible(len(self._lines) > 0)

    def _chip_order(self) -> list[FilterChip]:
        return [self._all_chip, *self._chips.values()]

    def _sync_chip_checks(self) -> None:
        self._all_chip.setChecked(self._filter == FILTER_ALL_KEY)
        for key, chip in self._chips.items():
            chip.setChecked(key == self._filter)

    def _apply_filter(self) -> None:
        for row, line in enumerate(self._lines):
            self._list.setRowHidden(row, self._filter != FILTER_ALL_KEY and line.status != self._filter)

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
