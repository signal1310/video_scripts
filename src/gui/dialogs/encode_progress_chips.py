"""재인코딩 진행 창의 칩과 전환 막대: 줄바꿈 배치, 진행과 결과 전환, 결과 상태 필터, 진행 중 영상 칩과 카드 쪽 전환

- 칩은 키보드 탭 이동과 스페이스, 엔터로 고를 수 있음. 칩 안의 글자와 막대는 마우스 입력을 받지 않아 칩 전체가 눌림
- 색은 팔레트 토큰만 씀
- 화면 스레드에서만 쓴다
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.gui.dialogs.encode_flow_layout import FlowLayout
from src.gui.dialogs.encode_progress_card import JobCard, shorten_middle, tab_title
from src.gui.styles.palettes import ThemePalette

CHIP_MAX_TITLE_CHARS: int = 18
CHIP_BAR_HEIGHT_PX: int = 3
CHIP_SPACING_PX: int = 6
CHIP_MARGIN_PX: int = 8
CHIP_RADIUS_PX: int = 8
CHIP_PERCENT_SCALE: int = 100
SEGMENT_PADDING_PX: int = 3


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
        """팔레트 토큰으로 칠함"""
        self.setStyleSheet(f"QWidget#EncodeSegmentSwitch {{ background: {palette.bg_elevated}; border-radius: 8px; }}")
        for chip in self._chips:
            chip.setStyleSheet(
                _chip_style(palette, "QPushButton#EncodeFilterChip", "background: transparent; border: none;")
                + f"QPushButton#EncodeFilterChip:checked {{ background: {palette.bg_card}; }}"
            )


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
        self._name: QLabel = QLabel(tab_title(order, shorten_middle(file_name, CHIP_MAX_TITLE_CHARS)))
        self._percent: QLabel = QLabel("0%")
        self._bar: QProgressBar = QProgressBar()
        self._bar.setRange(0, CHIP_PERCENT_SCALE)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(CHIP_BAR_HEIGHT_PX)
        for child in (self._name, self._percent, self._bar):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        head = QHBoxLayout()
        head.addWidget(self._name, stretch=1)
        head.addWidget(self._percent)
        body = QVBoxLayout(self)
        body.setContentsMargins(CHIP_MARGIN_PX, CHIP_MARGIN_PX, CHIP_MARGIN_PX, CHIP_MARGIN_PX)
        body.setSpacing(CHIP_SPACING_PX // 2)
        body.addLayout(head)
        body.addWidget(self._bar)

    def set_fraction(self, fraction: float | None) -> None:
        """진행 비율(0부터 1)을 퍼센트와 막대에 맞춤. 값이 없으면 0으로 표시"""
        value = 0.0 if fraction is None else min(max(fraction, 0.0), 1.0)
        self._percent.setText(f"{round(value * CHIP_PERCENT_SCALE)}%")
        self._bar.setValue(round(value * CHIP_PERCENT_SCALE))

    def apply_palette(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 칠함"""
        self.setStyleSheet(
            _chip_style(palette, "QPushButton#EncodeJobChip", f"background: {palette.bg_card};"
                        f" border: 1px solid {palette.border_subtle};")
            + f"QPushButton#EncodeJobChip:checked {{ background: {palette.bg_selected};"
            f" border: 1px solid {palette.accent_blue}; }}"
            + f"QProgressBar {{ background: {palette.progress_track_bg}; border: none; border-radius: 1px; }}"
            + f"QProgressBar::chunk {{ background: {palette.progress_fill}; border-radius: 1px; }}"
            + f"QLabel {{ color: {palette.text_muted}; background: transparent; }}"
        )


class CardChips(QWidget):
    """진행 중 영상의 칩 줄과 선택한 칩의 카드. 칩을 누르면 그 카드가 보이고, 칩 줄은 동시 실행이 1개면 숨김"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette: ThemePalette | None = None
        self._entries: list[tuple[JobCard, JobChip]] = []
        self._group: QButtonGroup = QButtonGroup(self)
        self._group.setExclusive(True)
        self._chip_host: QWidget = QWidget()
        self._flow: FlowLayout = FlowLayout(self._chip_host)
        self._stack: QStackedWidget = QStackedWidget()
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
        self._flow.addWidget(chip)
        self._stack.addWidget(card)
        chip.toggled.connect(lambda checked, c=card: checked and self._stack.setCurrentWidget(c))
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
                self._flow.removeWidget(chip)
                chip.deleteLater()
                break
        self._stack.removeWidget(card)
        card.deleteLater()
        if was_current and self._entries:
            self._entries[0][1].setChecked(True)
        self.setVisible(bool(self._entries))

    def set_progress(self, card: JobCard, fraction: float | None) -> None:
        """카드의 진행 비율을 칩에 반영. 칩이 없으면 아무 일도 하지 않음"""
        for entry_card, chip in self._entries:
            if entry_card is card:
                chip.set_fraction(fraction)
                return

    def chip_count(self) -> int:
        """칩 개수. 카드 수와 같음"""
        return len(self._entries)

    def apply_palette(self, palette: ThemePalette) -> None:
        """칩과 카드에 팔레트를 적용하고, 나중에 추가되는 카드와 칩에도 같은 팔레트를 씀"""
        self._palette = palette
        for card, chip in self._entries:
            chip.apply_palette(palette)
            card.apply_palette(palette)
