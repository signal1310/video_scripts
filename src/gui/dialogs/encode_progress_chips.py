"""재인코딩 진행 창의 칩과 전환 막대: 줄바꿈 배치, 진행과 결과 전환, 결과 상태 필터, 진행 중 영상 칩과 카드 쪽 전환

- 칩은 키보드 탭 이동과 스페이스, 엔터로 고를 수 있음. 칩 안의 글자와 막대는 마우스 입력을 받지 않아 칩 전체가 눌림
- 색은 팔레트 토큰만 씀
- 화면 스레드에서만 쓴다
"""

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics, QResizeEvent
from PyQt6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.gui.dialogs.encode_progress_card import JobCard, bar_style
from src.gui.styles.palettes import ThemePalette

CHIP_BAR_HEIGHT_PX: int = 3
CHIP_SPACING_PX: int = 6
CHIP_MARGIN_PX: int = 8
CHIP_RADIUS_PX: int = 8
CHIP_PERCENT_SCALE: int = 100
SEGMENT_PADDING_PX: int = 3
SEGMENT_RADIUS_PX: int = 9
SEGMENT_CHIP_RADIUS_PX: int = 7
SEGMENT_CHIP_PADDING: str = "3px 12px"
CHIP_MIN_WIDTH_PX: int = 130
CHIP_MAX_COLUMNS: int = 3
PERCENT_SAMPLE_TEXT: str = "100%"
SELECTED_NAME_WEIGHT: int = 600


def _chip_style(palette: ThemePalette, selector: str, base: str) -> str:
    """칩 한 종류의 스타일시트. 선택 칩은 선택 바탕과 강조 테두리로 구분"""
    return (
        f"{selector} {{ {base} color: {palette.text_secondary}; border-radius: {CHIP_RADIUS_PX}px; padding: 4px 10px; }}"
        f"{selector}:hover {{ color: {palette.text_primary}; }}"
        f"{selector}:checked {{ color: {palette.text_primary}; font-weight: 600; }}"
    )


class FilterChip(QPushButton):
    """개수를 붙인 선택 칩 하나. 결과 상태 필터에 씀"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setObjectName("EncodeFilterChip")

    def apply_palette(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 칠함"""
        self.setStyleSheet(
            _chip_style(
                palette,
                "QPushButton#EncodeFilterChip",
                f"background: {palette.bg_card}; border: 1px solid {palette.border_subtle};",
            )
            + f"QPushButton#EncodeFilterChip:hover {{ background: {palette.bg_hover}; }}"
            + f"QPushButton#EncodeFilterChip:checked {{ background: {palette.bg_selected};"
            f" border: 1px solid {palette.accent_blue}; }}"
        )


class SegmentSwitch(QWidget):
    """진행과 결과 전환 막대. QTabBar와 같은 이름의 메서드를 씀. 선택한 칸은 카드 바탕으로 올라와 보임"""

    currentChanged = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group: QButtonGroup = QButtonGroup(self)
        self._group.setExclusive(True)
        self._chips: list[FilterChip] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SEGMENT_PADDING_PX, SEGMENT_PADDING_PX, SEGMENT_PADDING_PX, SEGMENT_PADDING_PX)
        layout.setSpacing(SEGMENT_PADDING_PX)
        self._layout: QHBoxLayout = layout
        self.setObjectName("EncodeSegmentSwitch")
        # - Qt6는 스타일시트로 지정한 위젯 배경과 테두리를 직접 그리지 않으므로 트랙 테두리를 직접 칠하게 함
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def addTab(self, text: str) -> int:
        """칸을 끝에 추가하고 번호를 돌려줌. 첫 칸은 처음부터 선택됨"""
        chip = FilterChip(text, self)
        index = len(self._chips)
        self._chips.append(chip)
        self._group.addButton(chip, index)
        self._layout.addWidget(chip)
        chip.toggled.connect(lambda checked, i=index: checked and self.currentChanged.emit(i))
        if index == 0:
            chip.setChecked(True)
        return index

    def tabText(self, index: int) -> str:
        """칸의 글자"""
        return self._chips[index].text()

    def setTabText(self, index: int, text: str) -> None:
        """칸의 글자를 바꿈"""
        self._chips[index].setText(text)

    def currentIndex(self) -> int:
        """선택한 칸의 번호"""
        return self._group.checkedId()

    def setCurrentIndex(self, index: int) -> None:
        """번호의 칸을 선택함"""
        if 0 <= index < len(self._chips):
            self._chips[index].setChecked(True)

    def apply_palette(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 칠함. 트랙은 테두리로 경계를 세우고 선택 탭은 바탕 강조와 굵은 테두리로 구분"""
        self.setStyleSheet(
            f"QWidget#EncodeSegmentSwitch {{ background: {palette.bg_elevated};"
            f" border: 1px solid {palette.border_subtle}; border-radius: {SEGMENT_RADIUS_PX}px; }}"
        )
        for chip in self._chips:
            chip.setStyleSheet(
                f"QPushButton#EncodeFilterChip {{ background: transparent; border: 1px solid transparent;"
                f" border-radius: {SEGMENT_CHIP_RADIUS_PX}px; padding: {SEGMENT_CHIP_PADDING};"
                f" color: {palette.text_secondary}; }}"
                f"QPushButton#EncodeFilterChip:hover {{ color: {palette.text_primary};"
                f" background: {palette.bg_hover}; }}"
                f"QPushButton#EncodeFilterChip:checked {{ background: {palette.bg_selected};"
                f" border: 1px solid {palette.action_primary_border};"
                f" color: {palette.accent_blue}; font-weight: 600; }}"
            )


def chip_columns(available_px: int, spacing_px: int, min_chip_px: int, max_columns: int) -> int:
    """폭 안에 최소 칩 폭과 간격으로 들어가는 칩 수. 최소 1, 최대 max_columns"""
    fit = (available_px + spacing_px) // (min_chip_px + spacing_px)
    return max(1, min(max_columns, fit))


class JobChip(QPushButton):
    """진행 중 영상 하나의 칩: 순번과 줄임 이름, 진행률, 얇은 진행 막대. 전체 이름은 툴팁"""

    def __init__(self, order: int, file_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("EncodeJobChip")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(f"{order}. {file_name}")
        self.setAccessibleName(f"{order}. {file_name}")
        self._full_title: str = f"{order}. {file_name}"
        self._phase_badge: str = ""
        self._pre_encode: bool = False
        self._applied_pre_encode: bool | None = None
        self._palette: ThemePalette | None = None
        self._name: QLabel = QLabel(self._full_title, self)
        self._name.setObjectName("EncodeJobChipName")
        # - 이름 칸은 크기 힌트를 무시해야 칩 칸이 같은 최소 폭을 가지고 남는 폭을 나눠 가짐
        self._name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._percent: QLabel = QLabel("0%", self)
        self._percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._percent.setMinimumWidth(self._percent.fontMetrics().horizontalAdvance(PERCENT_SAMPLE_TEXT))
        self._bar: QProgressBar = QProgressBar(self)
        self._bar.setRange(0, CHIP_PERCENT_SCALE)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(CHIP_BAR_HEIGHT_PX)
        # - 막대의 권장 폭을 따르면 글꼴이 클 때 칩 최소 폭이 칸 폭을 넘어 칩이 눌리므로 폭은 칸을 따름
        self._bar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        for child in (self._name, self._percent, self._bar):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._selected_font: QFont = QFont(self._name.font())
        self._selected_font.setWeight(QFont.Weight.DemiBold)
        head = QHBoxLayout()
        head.setSpacing(CHIP_SPACING_PX)
        head.addWidget(self._name, stretch=1)
        head.addWidget(self._percent)
        self._body: QVBoxLayout = QVBoxLayout(self)
        self._body.setContentsMargins(CHIP_MARGIN_PX, CHIP_MARGIN_PX, CHIP_MARGIN_PX, CHIP_MARGIN_PX)
        self._body.setSpacing(CHIP_SPACING_PX // 2)
        self._body.addLayout(head)
        self._body.addWidget(self._bar)
        self.toggled.connect(lambda _checked: self._elide_name())

    def sizeHint(self) -> QSize:
        """권장 크기는 안쪽 레이아웃을 따름. 버튼 글자 기준 크기는 줄바꿈 칸 계산에 맞지 않음"""
        return self._body.sizeHint()

    def minimumSizeHint(self) -> QSize:
        """최소 크기도 안쪽 레이아웃을 따름"""
        return self._body.minimumSize()

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        """칩 폭이 바뀌면 이름을 다시 줄임"""
        super().resizeEvent(event)
        self._elide_name()

    def _elide_name(self) -> None:
        """이름 칸 폭에 맞춰 가운데를 줄임표로 줄임. 앞의 순번과 뒤의 확장자를 남김. 선택한 칩은 굵은 글자 폭으로 계산해 넘치지 않게 함"""
        room = self.width() - 2 * CHIP_MARGIN_PX - self._percent.minimumWidth() - CHIP_SPACING_PX
        metrics = QFontMetrics(self._selected_font if self.isChecked() else self._name.font())
        self._name.setText(metrics.elidedText(self._full_title, Qt.TextElideMode.ElideMiddle, max(room, 0)))

    def set_fraction(self, fraction: float | None) -> None:
        """진행 비율(0부터 1)을 퍼센트와 막대에 맞춤. 값이 없으면 0으로 표시"""
        value = 0.0 if fraction is None else min(max(fraction, 0.0), 1.0)
        if not self._phase_badge:
            self._percent.setText(f"{round(value * CHIP_PERCENT_SCALE)}%")
        self._bar.setValue(round(value * CHIP_PERCENT_SCALE))

    def set_pre_encode(self, pre_encode: bool) -> None:
        """본 인코딩 전 준비 단계 여부에 따라 칩 진행 막대 채움색을 주황과 기본색 사이에서 바꿈

        - 같은 값으로 이미 칠해 두었으면 스타일시트를 다시 파싱하지 않음. 진행 갱신마다 불림
        """
        self._pre_encode = pre_encode
        if self._palette is None or pre_encode == self._applied_pre_encode:
            return
        self._bar.setStyleSheet(bar_style(self._palette, pre_encode=pre_encode))
        self._applied_pre_encode = pre_encode

    def set_phase(self, badge: str, tip: str = "") -> None:
        """샘플 확인이나 재시도 배지를 표시하고 칩 툴팁을 갱신. 빈 문자열이면 퍼센트 표시로 복귀"""
        self._phase_badge = badge
        if badge:
            self._percent.setText(badge)
            self.setToolTip(f"{self._full_title}\n{tip}" if tip else self._full_title)
        else:
            self.setToolTip(f"{self._full_title}\n{tip}" if tip else self._full_title)

    def apply_palette(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 칠함. 선택한 칩은 이름 글자를 기본 글자 색과 굵기로 강조"""
        self._palette = palette
        self.setStyleSheet(
            _chip_style(palette, "QPushButton#EncodeJobChip", f"background: {palette.bg_card};"
                        f" border: 1px solid {palette.border_subtle};")
            + f"QPushButton#EncodeJobChip:checked {{ background: {palette.bg_selected};"
            f" border: 1px solid {palette.accent_blue}; }}"
            + f"QPushButton#EncodeJobChip:checked QLabel#EncodeJobChipName {{ color: {palette.text_primary};"
            f" font-weight: {SELECTED_NAME_WEIGHT}; }}"
            + f"QLabel {{ color: {palette.text_muted}; background: transparent; }}"
        )
        self._bar.setStyleSheet(bar_style(palette, pre_encode=self._pre_encode))
        self._applied_pre_encode = self._pre_encode


class CardChips(QWidget):
    """진행 중 영상의 칩 줄과 선택한 칩의 카드. 칩을 누르면 그 카드가 보이고, 칩 줄은 동시 실행이 1개면 숨김"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette: ThemePalette | None = None
        self._entries: list[tuple[JobCard, JobChip]] = []
        self._layout_key: tuple[int, tuple[JobChip, ...]] | None = None
        self._group: QButtonGroup = QButtonGroup(self)
        self._group.setExclusive(True)
        self._chip_host: QWidget = QWidget(self)
        self._grid: QGridLayout = QGridLayout(self._chip_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(CHIP_SPACING_PX)
        self._stack: QStackedWidget = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._chip_host)
        layout.addWidget(self._stack, stretch=1)
        self.setVisible(False)

    def set_tab_bar_visible(self, visible: bool) -> None:
        """동시 실행이 1개면 칩 줄을 숨김. 카드 영역은 카드 수에 따라 따로 정함"""
        self._chip_host.setVisible(visible)

    def add_card(self, card: JobCard, order: int, file_name: str) -> None:
        """카드와 칩을 추가. 카드가 처음이면 그 칩을 고름"""
        first = not self._entries
        chip = JobChip(order, file_name, self._chip_host)
        if self._palette is not None:
            chip.apply_palette(self._palette)
            card.apply_palette(self._palette)
        self._entries.append((card, chip))
        self._group.addButton(chip)
        self._stack.addWidget(card)
        chip.toggled.connect(lambda checked, c=card: checked and self._stack.setCurrentWidget(c))
        self._relayout_chips()
        if first:
            chip.setChecked(True)
        self.setVisible(True)

    def remove_card(self, card: JobCard) -> None:
        """카드와 칩을 제거. 보고 있던 카드를 지웠으면 남은 첫 칩을 고르고, 카드가 없으면 전체를 숨김"""
        was_current = self._stack.currentWidget() is card
        for index, (entry_card, chip) in enumerate(self._entries):
            if entry_card is card:
                del self._entries[index]
                self._group.removeButton(chip)
                self._grid.removeWidget(chip)
                chip.hide()
                chip.deleteLater()
                break
        self._stack.removeWidget(card)
        card.hide()
        card.deleteLater()
        self._relayout_chips()
        if was_current and self._entries:
            self._entries[0][1].setChecked(True)
        self.setVisible(bool(self._entries))

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        """칩 줄 폭이 바뀌면 한 줄에 놓을 칩 수를 다시 정함"""
        super().resizeEvent(event)
        self._relayout_chips()

    def _relayout_chips(self) -> None:
        """칩을 폭에 맞는 칸 수로 놓음. 쓰는 칸마다 같은 늘림 비율을 줘서 칩 폭을 같게 나눔"""
        columns = chip_columns(self.width(), CHIP_SPACING_PX, CHIP_MIN_WIDTH_PX, CHIP_MAX_COLUMNS)
        key = (columns, tuple(chip for _, chip in self._entries))
        if key == self._layout_key:
            return
        self._layout_key = key
        for _, chip in self._entries:
            self._grid.removeWidget(chip)
        for column in range(CHIP_MAX_COLUMNS):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        for index, (_, chip) in enumerate(self._entries):
            row, column = divmod(index, columns)
            self._grid.addWidget(chip, row, column)

    def set_progress(self, card: JobCard, fraction: float | None) -> None:
        """카드의 진행 비율을 칩에 반영. 칩이 없으면 아무 일도 하지 않음"""
        for entry_card, chip in self._entries:
            if entry_card is card:
                chip.set_fraction(fraction)
                return

    def set_phase(self, card: JobCard, badge: str, tip: str = "") -> None:
        """카드의 샘플/재시도 단계 배지와 툴팁을 칩에 반영"""
        for entry_card, chip in self._entries:
            if entry_card is card:
                chip.set_phase(badge, tip)
                return

    def set_pre_encode(self, card: JobCard, pre_encode: bool) -> None:
        """카드의 준비 단계 여부를 칩 진행 막대 색에 반영"""
        for entry_card, chip in self._entries:
            if entry_card is card:
                chip.set_pre_encode(pre_encode)
                return

    def chip_count(self) -> int:
        """칩 개수. 카드 수와 같음"""
        return len(self._entries)

    def current_card(self) -> JobCard | None:
        """지금 보이는 진행 카드, 없으면 None"""
        widget = self._stack.currentWidget()
        return widget if isinstance(widget, JobCard) else None

    def apply_palette(self, palette: ThemePalette) -> None:
        """칩과 카드에 팔레트를 적용하고, 나중에 추가되는 카드와 칩에도 같은 팔레트를 씀"""
        self._palette = palette
        for card, chip in self._entries:
            chip.apply_palette(palette)
            card.apply_palette(palette)
