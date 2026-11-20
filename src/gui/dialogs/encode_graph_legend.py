"""재인코딩 진행 카드의 그래프 범례: 기준선마다 그래프와 같은 색과 모양의 표본 선, 이름과 값을 줄바꿈 흐름으로 나열

- 항목 문구와 표본 값은 BitrateGraph.legend_entries가 정하고, 이 모듈은 배치와 글자 색만 맡음
- 색은 팔레트 토큰만 쓴다. 표본 색은 항목을 받을 때의 값을 쓰므로 카드가 매번 새 값을 넘김
"""

from collections.abc import Sequence

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from src.gui.dialogs.encode_flow_layout import FlowLayout
from src.gui.styles.palettes import ThemePalette
from src.gui.widgets.bitrate_graph import LegendEntry

SWATCH_WIDTH_PX: int = 18
SWATCH_HEIGHT_PX: int = 8
ITEM_GAP_PX: int = 4
FLOW_SPACING_PX: int = 10
TEXT_OBJECT_NAME: str = "EncodeGraphLegendText"


class _Swatch(QWidget):
    """표본 선: 그래프 기준선과 같은 색, 모양, 굵기로 가운데에 가로선 하나를 그림"""

    def __init__(self, entry: LegendEntry, parent: QWidget) -> None:
        super().__init__(parent)
        self._entry: LegendEntry = entry
        self.setFixedSize(SWATCH_WIDTH_PX, SWATCH_HEIGHT_PX)

    def paintEvent(self, _event: QPaintEvent | None) -> None:
        """가운데 높이에 표본 선 하나를 그림"""
        painter = QPainter(self)
        pen = QPen(QColor(self._entry.color))
        pen.setStyle(self._entry.style)
        pen.setWidthF(self._entry.width)
        painter.setPen(pen)
        y = self.height() / 2
        painter.drawLine(QPointF(0.0, y), QPointF(float(self.width()), y))
        painter.end()


class GraphLegend(QWidget):
    """범례 줄. 항목이 폭을 넘으면 다음 줄로 넘기고, 항목이 없으면 숨김"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._flow: FlowLayout = FlowLayout(self, spacing=FLOW_SPACING_PX)
        self._items: list[QWidget] = []
        self._entries: tuple[LegendEntry, ...] = ()
        # - 항목이 없을 때 빈 줄이 카드 세로 공간을 차지하면 수치 칸이 1px씩 눌리므로 처음부터 숨김
        self.setVisible(False)

    def entries(self) -> tuple[LegendEntry, ...]:
        """마지막으로 받은 범례 항목"""
        return self._entries

    def set_entries(self, entries: Sequence[LegendEntry]) -> None:
        """범례 항목을 다시 채움. 값이 없는 기준선은 호출자가 빼서 넘김"""
        for item in self._items:
            self._flow.removeWidget(item)
            item.deleteLater()
        self._entries = tuple(entries)
        self._items = [self._item_for(entry) for entry in self._entries]
        for item in self._items:
            self._flow.addWidget(item)
        self.setVisible(bool(self._items))

    def apply_palette(self, palette: ThemePalette) -> None:
        """설명 글자는 흐린 글자 토큰으로 칠함"""
        self.setStyleSheet(
            f"QLabel#{TEXT_OBJECT_NAME} {{ color: {palette.text_muted}; background: transparent; }}"
        )

    def _item_for(self, entry: LegendEntry) -> QWidget:
        item = QWidget(self)
        row = QHBoxLayout(item)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(ITEM_GAP_PX)
        label = QLabel(entry.text)
        label.setObjectName(TEXT_OBJECT_NAME)
        row.addWidget(_Swatch(entry, item))
        row.addWidget(label)
        return item
