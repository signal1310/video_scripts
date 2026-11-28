"""미리 보기 영상 위에 인코딩 때 잘라낼 여백을 표시하는 겹침 위젯과 배치 계산 모듈

- 영상 표면은 네이티브 창이라 투명 도구 창을 화면 전역 좌표로 올려 그 위에 그림
- 잘라낼 여백은 대개 순수한 검정이므로 어둡게 덮지 않고 경고색과 사선 무늬로 표시
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import NamedTuple

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFontMetrics,
    QPainter,
    QPaintEvent,
    QPen,
    QRegion,
)
from PyQt6.QtWidgets import QWidget

from src.core.models import ActiveFrame
from src.gui.preview.crop_edit import (
    content_rect,
)
from src.gui.styles.palettes import DARK_PALETTE, ThemePalette

CROP_LABEL_TEXT: str = "잘림"
_BORDER_WIDTH_PX: int = 2
_FILL_ALPHA: int = 72
_HATCH_ALPHA: int = 190
_HATCH_SPACING_PX: int = 8
_LABEL_MARGIN_PX: int = 4
_LABEL_FONT_PT: float = 8.5
_HANDLE_SIZE_PX: int = 7
_MIN_CONTENT_PX: int = 1
# - 잘림 표기는 HUD와 같은 반투명 알약에 담아 사선 무늬 위에서도 읽히게 함
_PILL_PADDING_X_PX: int = 7
_PILL_PADDING_Y_PX: int = 2
_PILL_RADIUS_PX: float = 7.0
_PILL_BORDER_WIDTH_PX: int = 1
_PILL_EDGE_PADDING_PX: int = 4
# - 겹침 창은 영상 위젯 영역에만 놓이고 모든 표시가 영상 화소(검은 여백 포함) 위에 그려지므로
#   무대 배경 색과 무관하게 두 테마 모두에서 어두운 영상 위 대비가 높은 색을 씀
_OVERLAY_PALETTE: ThemePalette = DARK_PALETTE
OVERLAY_WARN_COLOR: str = _OVERLAY_PALETTE.fg_maxrate_warn
OVERLAY_INK_COLOR: str = _OVERLAY_PALETTE.media_fg
OVERLAY_PILL_BG_COLOR: str = _OVERLAY_PALETTE.media_hud_bg
OVERLAY_PILL_BORDER_COLOR: str = _OVERLAY_PALETTE.media_keycap_border
_CSS_RGBA_RE = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")


def css_rgba_color(value: str) -> QColor:
    """팔레트 색 표기를 그리기 색으로 변환

    - Qt 색 해석은 `rgba(...)` 표기를 읽지 못하므로 괄호 안 값을 직접 풂
    """
    matched = _CSS_RGBA_RE.fullmatch(value.strip().lower())
    if matched is None:
        return QColor(value)
    return QColor(
        int(matched.group(1)),
        int(matched.group(2)),
        int(matched.group(3)),
        int(matched.group(4)),
    )


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
    content = content_rect((view_w, view_h), (video_w, video_h))
    content_w = content.width()
    content_h = content.height()
    content_x0 = content.left()
    content_y0 = content.top()
    content_x1 = content.left() + content_w
    content_y1 = content.top() + content_h
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
        self._size_label: str = ""
        self._hud_rect: QRect | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def set_size_label(self, text: str) -> None:
        """유지 영역 크기 표기를 바꾸고 다시 그림(빈 문자열이면 표기 생략)"""
        if self._size_label == text:
            return
        self._size_label = text
        if self.isVisible():
            self.update()

    def set_crop(self, rects: CropOverlayRects | None, *, hud_rect: QRect | None = None) -> None:
        """표시할 배치를 바꾸고, 없으면 바로 숨김

        - hud_rect는 잘림 표기가 겹치지 않게 피할 도구 모음의 화면 전역 사각형
        """
        self._rects = rects
        self._hud_rect = hud_rect
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

    def _hud_local_rect(self) -> QRect | None:
        """잘림 표기가 피할 도구 모음 사각형을 이 창 좌표로 변환"""
        hud = self._hud_rect
        if hud is None:
            return None
        return QRect(self.mapFromGlobal(hud.topLeft()), hud.size())

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        """잘라낼 여백을 채우고 사선 무늬, 유지 영역 테두리, 잘림 표시를 그림"""
        rects = self._rects
        if rects is None:
            return
        warn_color = OVERLAY_WARN_COLOR
        ink_color = OVERLAY_INK_COLOR
        painter = QPainter(self)
        try:
            fill = QColor(warn_color)
            fill.setAlpha(_FILL_ALPHA)
            hatch = QColor(warn_color)
            hatch.setAlpha(_HATCH_ALPHA)
            for rect in rects.cuts:
                painter.fillRect(rect, fill)
            _draw_hatch(painter, rects.cuts, hatch)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(warn_color), _BORDER_WIDTH_PX))
            # - 테두리 굵기의 절반이 영역 밖으로 나가 잘리지 않게 안쪽으로 반만큼 들임
            half = _BORDER_WIDTH_PX / 2
            painter.drawRect(QRectF(rects.kept).adjusted(half, half, -half, -half))
            _draw_handles(painter, rects.kept, QColor(ink_color))
            _draw_size_label(painter, rects.kept, self._size_label, QColor(ink_color))
            _draw_label(painter, rects, QColor(ink_color), self._hud_local_rect())
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


def _draw_handles(painter: QPainter, kept: QRect, color: QColor) -> None:
    """유지 영역의 네 변과 네 모서리에 끌기 손잡이를 그림"""
    if kept.width() <= 0 or kept.height() <= 0:
        return
    left = kept.left()
    right = kept.left() + kept.width()
    top = kept.top()
    bottom = kept.top() + kept.height()
    mid_x = (left + right) // 2
    mid_y = (top + bottom) // 2
    half = _HANDLE_SIZE_PX // 2
    anchors = (
        (left, top),
        (mid_x, top),
        (right, top),
        (left, mid_y),
        (right, mid_y),
        (left, bottom),
        (mid_x, bottom),
        (right, bottom),
    )
    painter.save()
    painter.setPen(QPen(color, 1))
    for x, y in anchors:
        painter.drawRect(QRect(x - half, y - half, _HANDLE_SIZE_PX, _HANDLE_SIZE_PX))
    painter.restore()


def _draw_size_label(painter: QPainter, kept: QRect, text: str, color: QColor) -> None:
    """유지 영역 왼쪽 위 안쪽에 크기와 좌표를 씀, 자리가 좁으면 생략"""
    if not text:
        return
    font = painter.font()
    font.setPointSizeF(_LABEL_FONT_PT)
    painter.setFont(font)
    metrics = QFontMetrics(font)
    width = metrics.horizontalAdvance(text) + 2 * _LABEL_MARGIN_PX
    height = metrics.height() + 2 * _LABEL_MARGIN_PX
    if kept.width() < width or kept.height() < height:
        return
    painter.setPen(color)
    painter.drawText(
        QRect(kept.left(), kept.top(), width, height),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        f" {text}",
    )


def crop_label_pill_rect(
    cuts: Sequence[QRect], size: QSize, *, hud_rect: QRect | None = None
) -> QRect | None:
    """잘림 표기를 담을 알약 사각형, 자리가 없으면 None

    - 면적이 넓은 여백부터 가운데에 놓고, `hud_rect`와 겹치면 위, 아래, 왼쪽, 오른쪽으로 밀어 봄
    - 밀어도 여백 안에서 겹치지 않는 자리가 없으면 다음 여백을 시도하고, 모두 안 되면 생략
    """
    pad = _PILL_EDGE_PADDING_PX
    for cut in sorted(cuts, key=lambda rect: rect.width() * rect.height(), reverse=True):
        inner = cut.adjusted(pad, pad, -pad, -pad)
        if inner.width() < size.width() or inner.height() < size.height():
            continue
        pill = QRect(QPoint(0, 0), size)
        pill.moveCenter(inner.center())
        left = min(max(pill.left(), inner.left()), inner.right() - size.width() + 1)
        top = min(max(pill.top(), inner.top()), inner.bottom() - size.height() + 1)
        inside = QRect(QPoint(left, top), size)
        if hud_rect is None or not inside.intersects(hud_rect):
            return inside
        # - HUD와 겹치면 HUD 바로 바깥 네 방향으로 밀어 여백 안에 들어오는 자리를 찾음
        shifts = (
            QRect(QPoint(inside.left(), hud_rect.top() - size.height() - pad), size),
            QRect(QPoint(inside.left(), hud_rect.bottom() + 1 + pad), size),
            QRect(QPoint(hud_rect.left() - size.width() - pad, inside.top()), size),
            QRect(QPoint(hud_rect.right() + 1 + pad, inside.top()), size),
        )
        for shifted in shifts:
            if inner.contains(shifted) and not shifted.intersects(hud_rect):
                return shifted
    return None


def _draw_label(
    painter: QPainter, rects: CropOverlayRects, color: QColor, hud_rect: QRect | None = None
) -> None:
    """가장 넓은 잘라낼 여백 가운데에 잘림 표시를 담은 알약을 그림, 겹치거나 자리가 좁으면 생략"""
    if not rects.cuts:
        return
    font = painter.font()
    font.setPointSizeF(_LABEL_FONT_PT)
    painter.setFont(font)
    metrics = QFontMetrics(font)
    size = QSize(
        metrics.horizontalAdvance(CROP_LABEL_TEXT) + 2 * _PILL_PADDING_X_PX,
        metrics.height() + 2 * _PILL_PADDING_Y_PX,
    )
    pill = crop_label_pill_rect(rects.cuts, size, hud_rect=hud_rect)
    if pill is None:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(css_rgba_color(OVERLAY_PILL_BORDER_COLOR), _PILL_BORDER_WIDTH_PX))
    painter.setBrush(QBrush(css_rgba_color(OVERLAY_PILL_BG_COLOR)))
    # - 테두리가 알약 밖으로 나가지 않게 안쪽으로 반만큼 들임
    half = _PILL_BORDER_WIDTH_PX / 2
    painter.drawRoundedRect(
        QRectF(pill).adjusted(half, half, -half, -half), _PILL_RADIUS_PX, _PILL_RADIUS_PX
    )
    painter.setPen(color)
    painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, CROP_LABEL_TEXT)
    painter.restore()
