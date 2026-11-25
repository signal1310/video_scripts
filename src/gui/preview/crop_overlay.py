"""미리 보기 영상 위에 인코딩 때 잘라낼 여백을 표시하는 겹침 위젯과 배치 계산 모듈

- 영상 표면은 네이티브 창이라 투명 도구 창을 화면 전역 좌표로 올려 그 위에 그림
- 잘라낼 여백은 대개 순수한 검정이므로 어둡게 덮지 않고 경고색과 사선 무늬로 표시
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPaintEvent, QPen, QRegion
from PyQt6.QtWidgets import QWidget

from src.core.models import ActiveFrame
from src.gui.styles.palettes import DARK_PALETTE, ThemePalette

CROP_LABEL_TEXT: str = "잘림"
_BORDER_WIDTH_PX: int = 2
_FILL_ALPHA: int = 72
_HATCH_ALPHA: int = 190
_HATCH_SPACING_PX: int = 8
_LABEL_MARGIN_PX: int = 4
_LABEL_FONT_PT: float = 8.5
_MIN_CONTENT_PX: int = 1
# - 영상 표면은 두 테마 모두에서 어두운 고정 배경이므로 검정 위 대비가 높은 다크 팔레트 경고색을 씀
_OVERLAY_PALETTE: ThemePalette = DARK_PALETTE


class CropOverlayRects(NamedTuple):
    """영상 위에 그릴 내용 영역, 유지할 영역, 잘라낼 여백 영역들"""

    content: QRect
    kept: QRect
    cuts: tuple[QRect, ...]


def crop_overlay_rects(
    view_size: tuple[int, int],
    video_size: tuple[int, int],
    active_frame: ActiveFrame,
) -> CropOverlayRects:
    """영상 위젯 안의 내용 영역과 그 안의 유지 영역, 잘라낼 여백 영역을 계산

    - 영상 위젯은 영상비를 유지해 배치하므로 위아래나 양옆에 플레이어 레터박스가 생김
    - 잘라낼 여백은 비어 있는 변을 빼고 최대 네 개이며, 여백과 유지 영역 넓이의 합이 내용 영역과 같음
    - 활성 화면 좌표는 표시 회전이 적용된 영상 크기 기준
    """
    view_w, view_h = (max(int(value), _MIN_CONTENT_PX) for value in view_size)
    video_w, video_h = (max(int(value), _MIN_CONTENT_PX) for value in video_size)
    scale = min(view_w / video_w, view_h / video_h)
    content_w = max(round(video_w * scale), _MIN_CONTENT_PX)
    content_h = max(round(video_h * scale), _MIN_CONTENT_PX)
    content_x0 = (view_w - content_w) // 2
    content_y0 = (view_h - content_h) // 2
    content_x1 = content_x0 + content_w
    content_y1 = content_y0 + content_h
    scale_x = content_w / video_w
    scale_y = content_h / video_h
    # - 반올림한 변을 내용 영역 안으로 가둬 활성 화면이 영상 밖을 가리켜도 넘치지 않게 함
    kept_x0 = min(max(content_x0 + round(active_frame.x * scale_x), content_x0), content_x1)
    kept_y0 = min(max(content_y0 + round(active_frame.y * scale_y), content_y0), content_y1)
    kept_x1 = min(
        max(content_x0 + round((active_frame.x + active_frame.width) * scale_x), kept_x0),
        content_x1,
    )
    kept_y1 = min(
        max(content_y0 + round((active_frame.y + active_frame.height) * scale_y), kept_y0),
        content_y1,
    )
    cuts: list[QRect] = []
    if kept_y0 > content_y0:
        cuts.append(QRect(content_x0, content_y0, content_w, kept_y0 - content_y0))
    if kept_y1 < content_y1:
        cuts.append(QRect(content_x0, kept_y1, content_w, content_y1 - kept_y1))
    if kept_x0 > content_x0:
        cuts.append(QRect(content_x0, kept_y0, kept_x0 - content_x0, kept_y1 - kept_y0))
    if kept_x1 < content_x1:
        cuts.append(QRect(kept_x1, kept_y0, content_x1 - kept_x1, kept_y1 - kept_y0))
    return CropOverlayRects(
        content=QRect(content_x0, content_y0, content_w, content_h),
        kept=QRect(kept_x0, kept_y0, kept_x1 - kept_x0, kept_y1 - kept_y0),
        cuts=tuple(cuts),
    )


class CropOverlay(QWidget):
    """영상 위에 잘라낼 여백을 경고색과 사선 무늬로, 유지할 영역을 밝은 테두리로 표시하는 겹침 창

    - 입력을 받지 않는 투명 도구 창이라 재생 조작을 가로채지 않음
    - 창 위치는 영상 위젯의 지금 화면 전역 영역을 그대로 따름
    """

    def __init__(self, parent: QWidget | None, get_view_rect: Callable[[], QRect]) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput,
        )
        self._get_view_rect = get_view_rect
        self._rects: CropOverlayRects | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def set_crop(self, rects: CropOverlayRects | None) -> None:
        """표시할 배치를 바꾸고, 없으면 바로 숨김"""
        self._rects = rects
        if rects is None:
            self.hide()
            return
        self.reposition()
        if not self.isVisible():
            self.show()
        self.raise_()

    def reposition(self) -> None:
        """영상 위젯의 지금 전역 영역으로 창을 다시 맞추고 다시 그림"""
        self.setGeometry(self._get_view_rect())
        if self.isVisible():
            self.update()

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        """잘라낼 여백을 채우고 사선 무늬, 유지 영역 테두리, 잘림 표시를 그림"""
        rects = self._rects
        if rects is None:
            return
        palette = _OVERLAY_PALETTE
        painter = QPainter(self)
        try:
            fill = QColor(palette.fg_maxrate_warn)
            fill.setAlpha(_FILL_ALPHA)
            hatch = QColor(palette.fg_maxrate_warn)
            hatch.setAlpha(_HATCH_ALPHA)
            for rect in rects.cuts:
                painter.fillRect(rect, fill)
            _draw_hatch(painter, rects.cuts, hatch)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(palette.fg_maxrate_warn), _BORDER_WIDTH_PX))
            # - 테두리 굵기의 절반이 영역 밖으로 나가 잘리지 않게 안쪽으로 반만큼 들임
            half = _BORDER_WIDTH_PX / 2
            painter.drawRect(QRectF(rects.kept).adjusted(half, half, -half, -half))
            _draw_label(painter, rects, QColor(palette.media_fg))
        finally:
            painter.end()


def _draw_hatch(painter: QPainter, cuts: tuple[QRect, ...], color: QColor) -> None:
    """잘라낼 여백 안에서만 보이도록 잘라 대각선 무늬를 그림"""
    if not cuts:
        return
    region = QRegion()
    for rect in cuts:
        region = region.united(QRegion(rect))
    area = region.boundingRect()
    painter.save()
    painter.setClipRegion(region)
    painter.setPen(QPen(color, 1))
    x = area.left() - area.height()
    while x <= area.right():
        painter.drawLine(QPoint(x, area.bottom()), QPoint(x + area.height(), area.top()))
        x += _HATCH_SPACING_PX
    painter.restore()


def _draw_label(painter: QPainter, rects: CropOverlayRects, color: QColor) -> None:
    """가장 넓은 잘라낼 여백 가운데에 잘림 표시를 씀, 자리가 좁으면 생략"""
    if not rects.cuts:
        return
    largest = max(rects.cuts, key=lambda rect: rect.width() * rect.height())
    font = painter.font()
    font.setPointSizeF(_LABEL_FONT_PT)
    painter.setFont(font)
    metrics = QFontMetrics(font)
    if largest.width() < metrics.horizontalAdvance(CROP_LABEL_TEXT) + 2 * _LABEL_MARGIN_PX:
        return
    if largest.height() < metrics.height() + 2 * _LABEL_MARGIN_PX:
        return
    painter.setPen(color)
    painter.drawText(largest, Qt.AlignmentFlag.AlignCenter, CROP_LABEL_TEXT)
