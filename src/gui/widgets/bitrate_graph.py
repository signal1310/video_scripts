"""순간 비트레이트 그래프: 격자, 최대 비트레이트 기준선, 일시 정지 구간, 마우스 위치 툴팁을 그림

- 값은 BitrateSeries의 점을 받고, 배치 시작 기준으로 최근 구간만 그림
- 색은 팔레트 토큰만 사용하고, 테마가 바뀌면 다음 그리기 때 새 토큰을 읽음
"""

import math
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from src.core.encode.encode_progress_view import (
    SERIES_WINDOW_SEC,
    SMOOTH_WINDOW_SEC,
    SeriesPoint,
    moving_average,
    point_label,
    value_stats,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeManager

GRAPH_HEIGHT_PX: int = 96
SCALE_STEP_KBPS: int = 500
MIN_SCALE_KBPS: int = 1000
HEADROOM_RATIO: float = 1.1
LABEL_FONT_PT: float = 8.0
LABEL_HEIGHT_PX: float = 14.0
MARGIN_LEFT_PX: float = 46.0
MARGIN_RIGHT_PX: float = 8.0
MARGIN_TOP_PX: float = 8.0
MARGIN_BOTTOM_PX: float = 14.0
AREA_ALPHA: int = 48
HOVER_RADIUS_PX: float = 3.5


@dataclass(frozen=True, slots=True)
class _Segment:
    """연속으로 측정된 구간의 화면 좌표 목록"""

    xs: tuple[float, ...]
    ys: tuple[float, ...]


class BitrateGraph(QWidget):
    """한 항목의 순간 비트레이트를 고정 높이로 보여 주는 위젯"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(GRAPH_HEIGHT_PX)
        self.setMouseTracking(True)
        self._points: tuple[SeriesPoint, ...] = ()
        self._max_kbps: float | None = None
        self._peak: float | None = None
        self._mean: float | None = None
        self._now_sec: float = 0.0
        self._hover_at: float | None = None

    def set_series(self, points: tuple[SeriesPoint, ...], max_kbps: float | None, now_sec: float) -> None:
        """원본 점, 최대 비트레이트(없으면 기준선 생략), 화면 기준 현재 시각을 받아 이동 평균 선과 최고값, 평균을 계산하고 다시 그림"""
        visible = tuple(p for p in points if p.at_sec >= now_sec - SERIES_WINDOW_SEC)
        self._mean, self._peak = value_stats(visible)
        self._points = moving_average(points, SMOOTH_WINDOW_SEC)
        self._max_kbps = max_kbps
        self._now_sec = now_sec
        self.update()

    def _plot_rect(self) -> QRectF:
        width = max(1.0, float(self.width()) - MARGIN_LEFT_PX - MARGIN_RIGHT_PX)
        height = max(1.0, float(self.height()) - MARGIN_TOP_PX - MARGIN_BOTTOM_PX)
        return QRectF(MARGIN_LEFT_PX, MARGIN_TOP_PX, width, height)

    def _y_scale_kbps(self) -> float:
        """위쪽 눈금 값. 최고값과 최대 비트레이트 중 큰 쪽에 여유를 더해 올림해서, 최고값 선이 항상 그림 안에 들어가게 함"""
        base = max(self._peak or 0.0, self._max_kbps or 0.0, float(MIN_SCALE_KBPS))
        return float(math.ceil(base * HEADROOM_RATIO / SCALE_STEP_KBPS) * SCALE_STEP_KBPS)

    def _window_start(self) -> float:
        return self._now_sec - SERIES_WINDOW_SEC

    def _x_of(self, at_sec: float, rect: QRectF) -> float:
        ratio = (at_sec - self._window_start()) / SERIES_WINDOW_SEC
        return rect.left() + rect.width() * min(1.0, max(0.0, ratio))

    def _y_of(self, kbps: float, rect: QRectF, scale: float) -> float:
        return rect.bottom() - rect.height() * min(kbps, scale) / scale

    def paintEvent(self, _event: object) -> None:
        """배경, 격자, 최대 비트레이트 기준선, 점 구간, 일시 정지 구간, 마지막 점과 마우스 강조를 순서대로 그림"""
        palette = ThemeManager().current_palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(palette.bg_table_alt))
        rect = self._plot_rect()
        scale = self._y_scale_kbps()
        self._draw_grid(painter, rect, scale, palette)
        self._draw_reference_lines(painter, rect, scale, palette)
        for segment in self._value_segments(rect, scale):
            self._draw_segment(painter, rect, segment, palette)
        self._draw_pause_spans(painter, rect, palette)
        self._draw_last_dot(painter, rect, scale, palette)
        self._draw_hover(painter, rect, scale, palette)
        painter.end()

    def _draw_grid(self, painter: QPainter, rect: QRectF, scale: float, palette: ThemePalette) -> None:
        """0, 중간, 위쪽 세 눈금만 그려 축 숫자가 서로 겹치지 않게 함"""
        label_font = QFont(self.font())
        label_font.setPointSizeF(LABEL_FONT_PT)
        painter.setFont(label_font)
        for value in (0.0, scale / 2.0, scale):
            y = self._y_of(value, rect, scale)
            painter.setPen(QPen(QColor(palette.border_subtle)))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QPen(QColor(palette.text_muted)))
            label_rect = QRectF(0.0, y - 7.0, MARGIN_LEFT_PX - 6.0, 14.0)
            painter.drawText(
                label_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                str(round(value)),
            )

    def _draw_reference_lines(self, painter: QPainter, rect: QRectF, scale: float, palette: ThemePalette) -> None:
        """최대 비트레이트, 최고값, 평균 기준선을 각 이름 라벨과 함께 그림. 라벨은 선 오른쪽 끝에 붙임"""
        label_font = QFont(self.font())
        label_font.setPointSizeF(LABEL_FONT_PT)
        painter.setFont(label_font)
        lines: list[tuple[float, str, str, Qt.PenStyle, float, float]] = []
        if self._max_kbps is not None:
            lines.append((self._max_kbps, "최대", palette.accent_orange, Qt.PenStyle.DashLine, 1.5, -4.0))
        if self._peak is not None:
            lines.append((self._peak, "최고", palette.text_secondary, Qt.PenStyle.DotLine, 1.0, -4.0))
        if self._mean is not None:
            lines.append((self._mean, "평균", palette.progress_fill, Qt.PenStyle.DashDotLine, 1.0, LABEL_HEIGHT_PX))
        for value, name, color, style, width, offset in lines:
            y = self._y_of(value, rect, scale)
            pen = QPen(QColor(color))
            pen.setStyle(style)
            pen.setWidthF(width)
            painter.setPen(pen)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            label_rect = QRectF(rect.left(), y + offset - LABEL_HEIGHT_PX / 2.0, rect.width() - 4.0, LABEL_HEIGHT_PX)
            painter.setPen(QPen(QColor(color)))
            painter.drawText(
                label_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{name} {round(value)}kbps",
            )

    def _value_segments(self, rect: QRectF, scale: float) -> list[_Segment]:
        segments: list[_Segment] = []
        xs: list[float] = []
        ys: list[float] = []
        for point in self._points:
            if point.kbps is None:
                if xs:
                    segments.append(_Segment(tuple(xs), tuple(ys)))
                xs, ys = [], []
                continue
            xs.append(self._x_of(point.at_sec, rect))
            ys.append(self._y_of(point.kbps, rect, scale))
        if xs:
            segments.append(_Segment(tuple(xs), tuple(ys)))
        return segments

    def _draw_segment(self, painter: QPainter, rect: QRectF, segment: _Segment, palette: ThemePalette) -> None:
        line = QPainterPath()
        area = QPainterPath()
        line.moveTo(segment.xs[0], segment.ys[0])
        area.moveTo(segment.xs[0], rect.bottom())
        area.lineTo(segment.xs[0], segment.ys[0])
        for x, y in zip(segment.xs[1:], segment.ys[1:], strict=True):
            line.lineTo(x, y)
            area.lineTo(x, y)
        area.lineTo(segment.xs[-1], rect.bottom())
        area.closeSubpath()
        fill = QColor(palette.progress_fill)
        fill.setAlpha(AREA_ALPHA)
        painter.fillPath(area, QBrush(fill))
        pen = QPen(QColor(palette.progress_fill))
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.drawPath(line)

    def _draw_pause_spans(self, painter: QPainter, rect: QRectF, palette: ThemePalette) -> None:
        hatch = QBrush(QColor(palette.text_muted), Qt.BrushStyle.BDiagPattern)
        for index, point in enumerate(self._points):
            if point.kbps is not None:
                continue
            start = self._x_of(point.at_sec, rect)
            following = self._points[index + 1] if index + 1 < len(self._points) else None
            end = self._x_of(self._now_sec if following is None else following.at_sec, rect)
            if end > start:
                painter.fillRect(QRectF(start, rect.top(), end - start, rect.height()), hatch)

    def _draw_last_dot(self, painter: QPainter, rect: QRectF, scale: float, palette: ThemePalette) -> None:
        last = next((p for p in reversed(self._points) if p.kbps is not None), None)
        if last is None:
            return
        painter.setPen(QPen(QColor(palette.progress_fill), 2.0))
        painter.setBrush(QColor(palette.bg_card))
        painter.drawEllipse(
            QPointF(self._x_of(last.at_sec, rect), self._y_of(last.kbps or 0.0, rect, scale)),
            HOVER_RADIUS_PX,
            HOVER_RADIUS_PX,
        )

    def _nearest_point(self, hover_at: float | None) -> SeriesPoint | None:
        if hover_at is None or not self._points:
            return None
        return min(self._points, key=lambda p: abs(p.at_sec - hover_at))

    def _draw_hover(self, painter: QPainter, rect: QRectF, scale: float, palette: ThemePalette) -> None:
        hover_at = self._hover_at
        if hover_at is None:
            return
        x = self._x_of(hover_at, rect)
        painter.setPen(QPen(QColor(palette.text_secondary), 1.0))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        point = self._nearest_point(hover_at)
        if point is not None and point.kbps is not None:
            painter.setPen(QPen(QColor(palette.progress_fill), 2.0))
            painter.setBrush(QColor(palette.bg_card))
            painter.drawEllipse(QPointF(x, self._y_of(point.kbps, rect, scale)), HOVER_RADIUS_PX, HOVER_RADIUS_PX)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        """마우스 x 위치를 시각으로 바꿔 가장 가까운 점을 강조하고, 그 점의 툴팁을 보여 줌"""
        if event is None:
            return
        rect = self._plot_rect()
        ratio = min(1.0, max(0.0, (event.position().x() - rect.left()) / rect.width()))
        self._hover_at = self._window_start() + SERIES_WINDOW_SEC * ratio
        point = self._nearest_point(self._hover_at)
        self.setToolTip("" if point is None else point_label(point))
        self.update()

    def leaveEvent(self, _event: object) -> None:
        """마우스가 나가면 강조와 툴팁을 지움"""
        self._hover_at = None
        self.setToolTip("")
        self.update()
