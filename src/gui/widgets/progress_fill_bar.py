"""평면 단색 채움과 영역별 글자 대비를 그리는 진행 막대 위젯 모듈"""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent
from PyQt6.QtWidgets import QProgressBar, QWidget

from src.gui.styles.icons import get_active_palette
from src.gui.styles.palettes import ThemePalette

_CORNER_RADIUS_PX: float = 4.0
_INDETERMINATE_FILL_RATIO: float = 0.25


class FillProgressBar(QProgressBar):
    """값만큼 단색으로 채워지는 평면 진행 막대

    - 글자는 채움 위에서는 강조 전경색, 빈 트랙 위에서는 본문 색으로 나눠 그려 두 영역 모두 대비를 확보
    - 범위가 0~0이면 무한 진행으로 보고 고정된 짧은 구간만 채움
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette: ThemePalette = get_active_palette()

    def set_palette(self, palette: ThemePalette) -> None:
        """테마 팔레트를 바꾸고 다시 그림"""
        self._palette = palette
        self.update()

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        """트랙, 채움, 글자를 순서대로 그림"""
        palette = self._palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        rect = QRectF(self.rect())
        track = QPainterPath()
        track.addRoundedRect(rect, _CORNER_RADIUS_PX, _CORNER_RADIUS_PX)
        painter.fillPath(track, QColor(palette.progress_track_bg))

        fill_right = rect.left() + rect.width() * self._fill_fraction()
        if fill_right > rect.left():
            painter.save()
            painter.setClipPath(track)
            painter.fillRect(
                QRectF(rect.left(), rect.top(), fill_right - rect.left(), rect.height()),
                QColor(palette.progress_fill),
            )
            painter.restore()

        self._draw_text(painter, rect, fill_right, palette)
        painter.end()

    def _fill_fraction(self) -> float:
        span = self.maximum() - self.minimum()
        if span <= 0:
            return _INDETERMINATE_FILL_RATIO
        ratio = (self.value() - self.minimum()) / span
        return min(max(ratio, 0.0), 1.0)

    def _draw_text(
        self,
        painter: QPainter,
        rect: QRectF,
        fill_right: float,
        palette: ThemePalette,
    ) -> None:
        text = self.text()
        if not self.isTextVisible() or not text:
            return
        painter.save()
        painter.setFont(self.font())
        painter.setClipRect(
            QRectF(rect.left(), rect.top(), fill_right - rect.left(), rect.height())
        )
        painter.setPen(QColor(palette.text_on_accent))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.setClipRect(
            QRectF(fill_right, rect.top(), rect.right() - fill_right, rect.height())
        )
        painter.setPen(QColor(palette.text_primary))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()
