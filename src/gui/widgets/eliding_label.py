"""폭이 모자랄 때 오른쪽을 말줄임으로 줄여 그리는 라벨 모듈"""

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPaintEvent
from PyQt6.QtWidgets import QLabel, QSizePolicy, QWidget

# - 글자 폭 측정 오차를 흡수하는 여유
_TEXT_SPARE_PX: int = 4


class ElidingLabel(QLabel):
    """말줄임을 직접 그리는 라벨

    - 최소 폭을 비워 좁은 자리에서도 줄어들고, 그릴 때 오른쪽을 말줄임으로 자름
    - pin_width를 켜면 최대 폭을 문구 폭으로 묶어 남는 가로 공간을 차지하지 않음
    - 글자색은 QSS 대신 지정한 팔레트 토큰으로 그림
    """

    def __init__(self, parent: QWidget | None = None, *, pin_width: bool = False) -> None:
        super().__init__("", parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._full_text: str = ""
        self._color: str = ""
        self._pin_width: bool = pin_width

    @property
    def full_text(self) -> str:
        """말줄임 전 원래 문구"""
        return self._full_text

    def set_full_text(self, text: str) -> None:
        """말줄임 전 문구를 지정하고 폭 힌트를 다시 계산하게 함"""
        self._full_text = text
        super().setText(text)
        if self._pin_width:
            self.setMaximumWidth(
                self.fontMetrics().horizontalAdvance(text) + _TEXT_SPARE_PX
            )
        self.updateGeometry()
        self.update()

    def set_color(self, color: str) -> None:
        """글자색 토큰을 지정하고 다시 그림"""
        self._color = color
        self.update()

    @property
    def color(self) -> str:
        """지금 사용하는 글자색 토큰. 지정 전에는 빈 문자열"""
        return self._color

    def minimumSizeHint(self) -> QSize:
        """폭이 모자랄 때 말줄임으로 줄어들 수 있도록 최소 폭을 비움"""
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        """말줄임한 문구를 직접 그림"""
        del a0
        if not self._full_text:
            return
        painter = QPainter(self)
        text = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, self.width()
        )
        color = self._color or self.palette().color(self.foregroundRole()).name()
        painter.setPen(QColor(color))
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.end()
