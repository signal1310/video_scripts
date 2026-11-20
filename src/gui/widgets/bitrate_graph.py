"""순간 비트레이트 그래프: 격자, 원본과 최대 비트레이트 기준선, 최고와 평균 선, 일시중지 구간, 마우스 위치 툴팁을 그림

- 값은 BitrateSeries의 점을 받고, 배치 시작 기준으로 최근 구간만 그림
- 기준선 라벨은 겹치지 않도록 최소 간격으로 쌓고, 선에 마우스를 올리면 그 선의 뜻을 툴팁으로 보여 줌
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
MARGIN_LEFT_PX: float = 46.0
MARGIN_RIGHT_PX: float = 8.0
MARGIN_TOP_PX: float = 8.0
MARGIN_BOTTOM_PX: float = 14.0
AREA_ALPHA: int = 48
HOVER_RADIUS_PX: float = 3.5
LINE_HIT_PX: float = 3.0
ORIGINAL_TIP: str = "원본 파일의 비트레이트입니다."
MAX_TIP: str = "재인코딩 상한(-maxrate)입니다. 순간 비트레이트가 이 선을 넘지 않도록 설정합니다."
PEAK_TIP: str = "이 영상의 재인코딩 중 기록된 순간 비트레이트의 최고값입니다."
MEAN_TIP: str = "이 영상의 재인코딩 중 기록된 순간 비트레이트의 평균입니다. 일시중지 구간은 뺍니다."


@dataclass(frozen=True, slots=True)
class _Segment:
    """연속으로 측정된 구간의 화면 좌표 목록"""

    xs: tuple[float, ...]
    ys: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class _RefLine:
    """기준선 하나: 값, 라벨 이름, 설명 문장, 선 색과 모양"""

    value: float
    name: str
    tip: str
    color: str
    style: Qt.PenStyle
    width: float


@dataclass(frozen=True, slots=True)
class LegendEntry:
    """범례 한 항목: 문구와 표본 선의 색, 모양, 굵기(그래프 기준선과 같은 값)"""

    text: str
    color: str
    style: Qt.PenStyle
    width: float


class BitrateGraph(QWidget):
    """한 항목의 순간 비트레이트를 고정 높이로 보여 주는 위젯"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(GRAPH_HEIGHT_PX)
        self.setMouseTracking(True)
        self._points: tuple[SeriesPoint, ...] = ()
        self._max_kbps: float | None = None
        self._original_kbps: float | None = None
        self._peak: float | None = None
        self._mean: float | None = None
        self._now_sec: float = 0.0
        self._hover_at: float | None = None

    def set_series(
        self,
        points: tuple[SeriesPoint, ...],
        max_kbps: float | None,
        original_kbps: float | None,
        now_sec: float,
    ) -> None:
        """원본 점, 최대와 원본 비트레이트(없으면 기준선 생략), 화면 기준 현재 시각을 받아 이동 평균 선과 최고값, 평균을 계산하고 다시 그림"""
        visible = tuple(p for p in points if p.at_sec >= now_sec - SERIES_WINDOW_SEC)
        self._mean, self._peak = value_stats(visible)
        self._points = moving_average(points, SMOOTH_WINDOW_SEC)
        self._max_kbps = max_kbps
        self._original_kbps = original_kbps
        self._now_sec = now_sec
        self.update()

    def _plot_rect(self) -> QRectF:
        width = max(1.0, float(self.width()) - MARGIN_LEFT_PX - MARGIN_RIGHT_PX)
        height = max(1.0, float(self.height()) - MARGIN_TOP_PX - MARGIN_BOTTOM_PX)
        return QRectF(MARGIN_LEFT_PX, MARGIN_TOP_PX, width, height)

    def _y_scale_kbps(self) -> float:
        """위쪽 눈금 값. 최고값과 최대 비트레이트 중 큰 쪽에 여유를 더해 올림해서, 최고값 선이 항상 그림 안에 들어가게 함"""
        base = max(self._peak or 0.0, self._max_kbps or 0.0, self._original_kbps or 0.0, float(MIN_SCALE_KBPS))
        return float(math.ceil(base * HEADROOM_RATIO / SCALE_STEP_KBPS) * SCALE_STEP_KBPS)

    def _window_start(self) -> float:
        return self._now_sec - SERIES_WINDOW_SEC

    def _x_of(self, at_sec: float, rect: QRectF) -> float:
        ratio = (at_sec - self._window_start()) / SERIES_WINDOW_SEC
        return rect.left() + rect.width() * min(1.0, max(0.0, ratio))

    def _y_of(self, kbps: float, rect: QRectF, scale: float) -> float:
        return rect.bottom() - rect.height() * min(kbps, scale) / scale

    def paintEvent(self, _event: object) -> None:
        """배경, 격자, 최대 비트레이트 기준선, 값 구간, 일시중지 구간, 마지막 점과 마우스 강조를 순서대로 그림"""
        palette = ThemeManager().current_palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(palette.bg_card))
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

    def _reference_lines(self, palette: ThemePalette) -> list[_RefLine]:
        """그릴 기준선 목록. 원본, 최대, 최고, 평균 순서이며 값이 없는 선은 뺌"""
        lines: list[_RefLine] = []
        if self._original_kbps is not None:
            lines.append(_RefLine(self._original_kbps, "원본", ORIGINAL_TIP, palette.text_muted, Qt.PenStyle.SolidLine, 1.0))
        if self._max_kbps is not None:
            lines.append(_RefLine(self._max_kbps, "최대", MAX_TIP, palette.accent_orange, Qt.PenStyle.DashLine, 1.5))
        if self._peak is not None:
            lines.append(_RefLine(self._peak, "최고", PEAK_TIP, palette.text_secondary, Qt.PenStyle.DotLine, 1.0))
        if self._mean is not None:
            lines.append(_RefLine(self._mean, "평균", MEAN_TIP, palette.progress_fill, Qt.PenStyle.DashDotLine, 1.0))
        return lines

    def _draw_reference_lines(self, painter: QPainter, rect: QRectF, scale: float, palette: ThemePalette) -> None:
        """기준선만 그림. 이름과 값은 그래프 아래 범례(legend_entries)가 맡음"""
        for line in self._reference_lines(palette):
            y = self._y_of(line.value, rect, scale)
            pen = QPen(QColor(line.color))
            pen.setStyle(line.style)
            pen.setWidthF(line.width)
            painter.setPen(pen)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

    def legend_entries(self) -> list[LegendEntry]:
        """그래프 아래 범례 항목: 값이 있는 기준선마다 이름과 값, 그 선과 같은 색과 모양"""
        palette = ThemeManager().current_palette
        return [
            LegendEntry(f"{line.name} {round(line.value)}kbps", line.color, line.style, line.width)
            for line in self._reference_lines(palette)
        ]

    def _reference_tip_at(self, y: float, rect: QRectF, scale: float, palette: ThemePalette) -> str | None:
        """마우스 y 위치가 기준선 가까이 있으면 그 선의 설명 문장"""
        for line in self._reference_lines(palette):
            if abs(self._y_of(line.value, rect, scale) - y) <= LINE_HIT_PX:
                return f"{line.name} {round(line.value)}kbps: {line.tip}"
        return None

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
            if not point.paused:
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
        palette = ThemeManager().current_palette
        line_tip = self._reference_tip_at(event.position().y(), rect, self._y_scale_kbps(), palette)
        if line_tip is not None:
            self.setToolTip(line_tip)
        else:
            self.setToolTip("" if point is None else point_label(point))
        self.update()

    def leaveEvent(self, _event: object) -> None:
        """마우스가 나가면 강조와 툴팁을 지움"""
        self._hover_at = None
        self.setToolTip("")
        self.update()
