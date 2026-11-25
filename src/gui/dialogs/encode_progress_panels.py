"""인코딩 진행 창 하단 패널: 전체 속도와 완료분 절약 칸, 결과 목록(상태 필터, 행마다 상태 배지와 크기 줄과 사유 줄)

- 값 문구는 호출자(진행 창)가 만들어 넘기고, 이 모듈은 배치와 표시만 맡음
- 결과 목록은 바뀐 행만 다시 만들어 선택과 스크롤을 유지함. 창 폭이 바뀌면 줄바꿈된 행의 높이를 다시 잼
- 색은 팔레트 토큰만 씀. 배지 색은 메인 표의 인코딩 상태 배지 정의를 재사용함
- 화면 스레드에서만 쓴다
"""

import html
from collections.abc import Callable, Sequence

from PyQt6.QtCore import QSize, Qt, pyqtBoundSignal, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QKeySequence, QResizeEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.encode.encode_batch_book import (
    STATUS_DISCARDED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_REPLACE_PENDING,
    STATUS_SKIPPED,
)
from src.core.encode.encode_progress_values import TONE_GREW, TONE_SAVED, ResultLine
from src.core.encode.encode_progress_view import speed_text
from src.core.settings.shortcuts import COMPARE_ACTION_ID, DEFAULT_SHORTCUTS, normalize_shortcut
from src.gui.dialogs.encode_flow_layout import FlowLayout
from src.gui.dialogs.encode_progress_chips import FilterChip
from src.gui.styles.icons import create_icon_button, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.table.table_column_formats import encode_state_badge

TOTALS_CAPTIONS: tuple[str, str] = ("전체 속도", "완료분 절약")
TOTALS_TIPS: tuple[str, str] = (
    "현재 진행 중인 영상들의 인코딩 속도를 합산한 배속입니다. 예를 들어 2.00배면 영상 1분 분량을 30초 만에 처리하는 속도입니다.",
    "지금까지 인코딩을 마친 영상들의 원본 대비 총 용량 변화입니다. 음수(-)는 용량이 줄었음을 뜻합니다.",
)
FINAL_SPEED_TEMPLATE: str = "평균 {average}"
ACTION_BUTTON_PX: int = 28
ROW_PADDING_PX: int = 10
ROW_GAP_PX: int = 3
ROW_RADIUS_PX: int = 8
BADGE_RADIUS_PX: int = 9
FILTER_ALL_KEY: str = ""
FILTER_ALL_NAME: str = "전체"
# - 상태 칩의 고정 순서. 처음 나타난 순서에 따라 칩 자리가 바뀌지 않게 함. 목록에 없는 상태는 뒤에 둠
RESULT_STATUS_ORDER: tuple[str, ...] = (
    STATUS_REPLACE_PENDING,
    STATUS_FAILED,
    STATUS_DISCARDED,
    STATUS_DONE,
    STATUS_SKIPPED,
)
RETRY_ACCESSIBLE_NAME: str = "재시도"
RETRY_TIP: str = "결과 파일을 원본 자리로 다시 옮깁니다. 실패하면 교체 대기로 남습니다."
FORFEIT_ACCESSIBLE_NAME: str = "교체 포기"
FORFEIT_TIP: str = "이 영상을 실패로 두고 재시도하지 않습니다. 원본과 결과 파일은 그대로 남습니다."
REVEAL_ACCESSIBLE_NAME: str = "위치 열기"
REVEAL_TIP: str = "탐색기에서 이 파일을 선택해 엽니다."
COMPARE_ACCESSIBLE_NAME: str = "화질 비교"
COMPARE_TIP: str = "원본과 결과 파일을 비교합니다."
COMPARE_DISABLED_TIP: str = "원본 파일이 없어 비교할 수 없습니다."
COMPARE_ICON: str = "compare"
TRASH_ACCESSIBLE_NAME: str = "휴지통으로 이동"
TRASH_TIP: str = "이 영상이 쓰려던 임시 파일을 휴지통으로 옮깁니다. 확인 창을 한 번 거칩니다."


class TotalsPanel(QWidget):
    """전체 속도와 완료분 절약을 가로 2열 카드로 보여 주는 칸. 이름은 흐리게, 값은 진하게 둠"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EncodeTotalsPanel")
        self._captions: tuple[QLabel, QLabel] = (QLabel(TOTALS_CAPTIONS[0], self), QLabel(TOTALS_CAPTIONS[1], self))
        self._speed: QLabel = QLabel(self)
        self._saving: QLabel = QLabel(self)
        for widget, tip in (
            (self._captions[0], TOTALS_TIPS[0]),
            (self._speed, TOTALS_TIPS[0]),
            (self._captions[1], TOTALS_TIPS[1]),
            (self._saving, TOTALS_TIPS[1]),
        ):
            widget.setToolTip(tip)
        for value in (self._speed, self._saving):
            value.setWordWrap(True)
        # - 이름과 값을 카드 하나에 세로로 쌓고 두 카드를 가로로 나란히 두어 진행 탭 안에서도 폭을 지킴
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(ROW_GAP_PX * 2)
        row.addWidget(self._cell(self._captions[0], self._speed), stretch=1)
        row.addWidget(self._cell(self._captions[1], self._saving), stretch=1)

    def _cell(self, caption: QLabel, value: QLabel) -> QFrame:
        """이름과 값을 세로로 담은 카드 하나"""
        cell = QFrame(self)
        cell.setObjectName("EncodeTotalsCell")
        column = QVBoxLayout(cell)
        column.setContentsMargins(12, 8, 12, 8)
        column.setSpacing(1)
        column.addWidget(caption)
        column.addWidget(value)
        return cell

    def set_values(self, speed: str, saving: str) -> None:
        """전체 속도와 완료분 절약 문구를 설정"""
        self._speed.setText(speed)
        self._saving.setText(saving)

    def hasHeightForWidth(self) -> bool:
        """값 문구가 줄바꿈되므로 폭에 따라 필요한 높이가 달라짐"""
        return True

    def heightForWidth(self, width: int) -> int:
        """주어진 폭에서 줄바꿈된 값 문구까지 담는 높이"""
        layout = self.layout()
        if layout is None:
            return super().heightForWidth(width)
        return layout.heightForWidth(width)

    def minimumSizeHint(self) -> QSize:
        """폭에 따른 줄바꿈 높이를 최소 높이에 넣음

        - 기본 구현은 최소 높이를 폭과 무관하게 계산해, 진행 탭 안에서 두 값이 줄바꿈되면
          그만큼의 높이를 카드에서 빼앗아 카드 수치 칸이 눌림
        """
        hint = super().minimumSizeHint()
        return QSize(hint.width(), max(hint.height(), self.heightForWidth(max(self.width(), hint.width()))))

    def speed_text(self) -> str:
        """현재 전체 속도 문구"""
        return self._speed.text()

    def saving_text(self) -> str:
        """현재 완료분 절약 문구"""
        return self._saving.text()

    def apply_palette(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 칠함. 두 지표를 각각 층이 있는 카드로 나눔"""
        self.setStyleSheet(
            f"QWidget#EncodeTotalsCell {{ background: {palette.bg_card}; border: 1px solid {palette.border_subtle};"
            f" border-radius: {ROW_RADIUS_PX}px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        for caption in self._captions:
            caption.setStyleSheet(f"color: {palette.text_muted};")
        for value in (self._speed, self._saving):
            value.setStyleSheet(f"color: {palette.text_primary}; font-weight: 600;")


def finished_speed_text(average: float | None) -> str:
    """배치가 끝난 뒤 전체 속도 칸 문구. 현재 속도는 없으므로 평균만 씀"""
    return FINAL_SPEED_TEMPLATE.format(average=speed_text(average))


def _status_rank(status: str) -> int:
    """상태 칩 정렬 순서. 고정 순서에 없는 상태는 맨 뒤(같은 순위끼리는 나타난 순서 유지)"""
    return RESULT_STATUS_ORDER.index(status) if status in RESULT_STATUS_ORDER else len(RESULT_STATUS_ORDER)


def compare_shortcut_pressed(event: QKeyEvent) -> bool:
    """화질 비교 전환 기본 단축키(Ctrl+B) 입력인지"""
    pressed = normalize_shortcut(QKeySequence(event.keyCombination()).toString())
    return pressed == normalize_shortcut(DEFAULT_SHORTCUTS[COMPARE_ACTION_ID])


class _ResultListWidget(QListWidget):
    """결과 목록. 스페이스바는 미리 보기, 화질 비교 전환 단축키는 화질 비교 요청으로 넘기고, 창 폭이 바뀌면 알림"""

    space_pressed = pyqtSignal(bool)
    resized = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is not None:
            if compare_shortcut_pressed(event):
                self.space_pressed.emit(True)
                event.accept()
                return
            plain_space = not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if event.key() == Qt.Key.Key_Space and plain_space:
                self.space_pressed.emit(False)
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
    """인코딩 결과 목록. 상태 필터 칩으로 보는 상태를 고르고, 행마다 상태 배지, 이름, 크기 줄, 사유 줄을 보여 줌

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
    compare_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lines: list[ResultLine] = []
        self._palette: ThemePalette | None = None
        self._filter: str = FILTER_ALL_KEY
        self._chips: dict[str, FilterChip] = {}
        self._chip_host: QWidget = QWidget(self)
        self._chip_flow: FlowLayout = FlowLayout(self._chip_host)
        self._list: _ResultListWidget = _ResultListWidget(self)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        self._list.space_pressed.connect(self._on_space_pressed)
        self._list.resized.connect(self._refit_rows)
        self._all_chip: FilterChip = self._make_chip(FILTER_ALL_KEY, FILTER_ALL_NAME)
        self._chip_flow.addWidget(self._all_chip)
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
        """원본 경로 기준으로 줄을 맞춤. 사라진 줄은 빼고, 같은 자리의 같은 줄은 그대로 두며, 바뀐 줄은 다시 만들고, 새 줄은 제자리에 끼움. 선택한 원본은 자리가 바뀌어도 계속 선택"""
        selected = self._source_at(self._list.currentRow())
        new_lines = list(lines)
        keep = {line.source_path for line in new_lines}
        for row in reversed(range(len(self._lines))):
            if self._lines[row].source_path not in keep:
                self._remove_row(row)
        for index, line in enumerate(new_lines):
            if index < len(self._lines) and self._lines[index].source_path == line.source_path:
                if force or self._lines[index] != line:
                    self._replace_row(index, line)
                continue
            current = self._row_of_source(line.source_path)
            if current is not None:
                self._remove_row(current)
            self._insert_row(index, line)
        self._refit_rows()
        self._refresh_chips()
        self._apply_filter()
        if selected:
            row = self._row_of_source(selected)
            if row is not None:
                self._list.setCurrentRow(row)

    def _row_of_source(self, source: str) -> int | None:
        """원본 경로가 같은 줄의 번호. 없으면 없음"""
        for row, line in enumerate(self._lines):
            if line.source_path == source:
                return row
        return None

    def _insert_row(self, index: int, line: ResultLine) -> None:
        item = QListWidgetItem()
        self._list.insertItem(index, item)
        self._list.setItemWidget(item, self._row_widget(line))
        self._lines.insert(index, line)

    def _replace_row(self, index: int, line: ResultLine) -> None:
        item = self._list.item(index)
        if item is None:
            return
        old = self._list.itemWidget(item)
        self._list.setItemWidget(item, self._row_widget(line))
        if old is not None:
            old.hide()
            old.deleteLater()
        self._lines[index] = line

    def _remove_row(self, row: int) -> None:
        item = self._list.item(row)
        if item is not None:
            widget = self._list.itemWidget(item)
            self._list.takeItem(row)
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        del self._lines[row]

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
        row = QWidget(self._list.viewport())
        row.setObjectName("EncodeResultRow")
        outer = QVBoxLayout(row)
        outer.setContentsMargins(ROW_PADDING_PX, ROW_PADDING_PX, ROW_PADDING_PX, ROW_PADDING_PX)
        outer.setSpacing(ROW_GAP_PX)
        head = QHBoxLayout()
        head.setSpacing(ROW_GAP_PX * 2)
        badge = QLabel(line.status, row)
        badge.setObjectName("EncodeStatusBadge")
        name = QLabel(line.name, row)
        name.setWordWrap(True)
        name_font = name.font()
        name_font.setBold(True)
        name.setFont(name_font)
        head.addWidget(badge)
        head.addWidget(name, stretch=1)
        if line.replace_pending or line.leftover or line.part_left or line.compare_target:
            head.addWidget(self._action_cell(line, row))
        outer.addLayout(head)
        size = QLabel(self._size_html(line), row)
        size.setTextFormat(Qt.TextFormat.RichText)
        size.setWordWrap(True)
        size.setVisible(bool(line.change))
        outer.addWidget(size)
        reason = QLabel(line.reason, row)
        reason.setWordWrap(True)
        reason.setVisible(bool(line.reason))
        outer.addWidget(reason)
        if self._palette is not None:
            self._style_row(row, badge, reason, line)
        row_tip = "\n".join(part for part in (line.path_text, line.reason) if part)
        row.setToolTip(row_tip)
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

    def _action_cell(self, line: ResultLine, parent: QWidget | None = None) -> QWidget:
        source = line.source_path
        cell = QWidget(parent)
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        if line.compare_target:
            compare = create_icon_button(
                cell,
                COMPARE_TIP if line.compare_ready else COMPARE_DISABLED_TIP,
                _emitter(self.compare_requested, source),
                accessible_name=COMPARE_ACCESSIBLE_NAME,
                fixed_size=ACTION_BUTTON_PX,
            )
            set_button_icon(compare, COMPARE_ICON, pad_right=0)
            compare.setEnabled(line.compare_ready)
            if not line.compare_ready:
                # - 비활성 자식은 마우스 사건을 받지 않으므로 감싼 칸에 같은 사유를 둠
                cell.setToolTip(COMPARE_DISABLED_TIP)
            layout.addWidget(compare)
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
        chip.setToolTip("모든 처리 결과 항목을 봅니다." if key == FILTER_ALL_KEY else f"{name} 상태 항목만 골라 봅니다.")
        chip.clicked.connect(lambda _checked=False, k=key: self.set_filter(k))
        if self._palette is not None:
            chip.apply_palette(self._palette)
        return chip

    def _refresh_chips(self) -> None:
        """전체 칩과 상태별 칩의 개수를 맞추고 고정 순서로 다시 놓음. 사라진 상태의 칩은 지우고, 고른 상태가 사라졌으면 전체로 돌아감"""
        counts: dict[str, int] = {}
        for line in self._lines:
            counts[line.status] = counts.get(line.status, 0) + 1
        if self._filter != FILTER_ALL_KEY and self._filter not in counts:
            self._filter = FILTER_ALL_KEY
        self._all_chip.setText(f"{FILTER_ALL_NAME} {len(self._lines)}")
        for key in list(self._chips):
            if key not in counts:
                chip = self._chips.pop(key)
                self._chip_flow.removeWidget(chip)
                chip.hide()
                chip.deleteLater()
        for chip in self._chips.values():
            self._chip_flow.removeWidget(chip)
        ordered: dict[str, FilterChip] = {}
        for key in sorted(counts, key=_status_rank):
            existing = self._chips.get(key)
            ordered[key] = existing if existing is not None else self._make_chip(key, key)
            ordered[key].setText(f"{key} {counts[key]}")
            self._chip_flow.addWidget(ordered[key])
        self._chips = ordered
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

    def _on_space_pressed(self, compare: bool) -> None:
        source = self._source_at(self._list.currentRow())
        if source:
            (self.compare_requested if compare else self.preview_requested).emit(source)
