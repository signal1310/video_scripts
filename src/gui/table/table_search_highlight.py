"""표 셀 텍스트에서 필터 바 검색어와 일치하는 구간을 팔레트 토큰으로 강조해 그리는 모듈

- 표 뷰가 검색어를 뷰 속성으로 들고, 델리게이트가 `option.widget`에서 같은 값을 읽음
- 일치 구간만 배경과 글자색을 바꾸고 나머지 구간은 원래 글자색을 유지
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from PyQt6.QtCore import QModelIndex, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QIcon, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeManager

# - 표 뷰가 검색 강조 대상을 담아 두는 속성 이름, 델리게이트는 이 값을 읽어 강조를 그림
SEARCH_QUERY_ATTRIBUTE: str = "search_highlight_query"
# - 셀 아이콘과 글자 사이 간격, 글자 좌우 여백. 표 델리게이트와 같은 값을 씀
_CELL_ICON_PX: int = 12
_CELL_ICON_GAP_PX: int = 4
_CELL_H_PADDING_PX: int = 4


def search_query_of(widget: QWidget | None) -> str:
    """뷰가 들고 있는 지금 검색어, 강조가 없으면 빈 문자열"""
    value = getattr(widget, SEARCH_QUERY_ATTRIBUTE, "")
    return value if isinstance(value, str) else ""


def split_search_segments(text: str, query: str) -> list[tuple[str, bool]]:
    """문자열을 (구간, 일치 여부) 목록으로 나눔, 대소문자 구분 없이 일치 구간만 표시

    - 검색어가 비었거나 일치 구간이 없으면 전체를 일치하지 않는 한 구간으로 돌려줌
    """
    normalized = query.strip().lower()
    if not normalized or not text:
        return [(text, False)]
    lowered = text.lower()
    segments: list[tuple[str, bool]] = []
    start = 0
    position = lowered.find(normalized)
    while position >= 0:
        if position > start:
            segments.append((text[start:position], False))
        segments.append((text[position : position + len(normalized)], True))
        start = position + len(normalized)
        position = lowered.find(normalized, start)
    if start < len(text):
        segments.append((text[start:], False))
    return segments


@dataclass(frozen=True, slots=True)
class _SegmentBox:
    """강조 그리기에 필요한 구간의 시작 x, 폭, 일치 여부"""

    start: float
    width: int
    matched: bool


def _horizontal_start(alignment: int, rect: QRectF, total_width: int) -> float:
    """정렬 플래그에 따른 첫 구간의 시작 x"""
    if alignment & int(Qt.AlignmentFlag.AlignRight):
        return rect.right() - total_width
    if alignment & int(Qt.AlignmentFlag.AlignHCenter):
        return rect.center().x() - total_width / 2.0
    return rect.left()


def _segment_boxes(
    metrics: QFontMetrics, rect: QRectF, segments: list[tuple[str, bool]], alignment: int
) -> list[_SegmentBox]:
    """정렬 플래그에 맞춰 구간별 시작 x와 폭을 계산"""
    widths = [metrics.horizontalAdvance(segment) for segment, _matched in segments]
    start = _horizontal_start(alignment, rect, sum(widths))
    boxes: list[_SegmentBox] = []
    for width, (_segment, matched) in zip(widths, segments, strict=True):
        boxes.append(_SegmentBox(start, width, matched))
        start += width
    return boxes


def draw_text_with_search(
    painter: QPainter,
    rect: QRectF,
    text: str,
    *,
    query: str,
    palette: ThemePalette,
    color: QColor,
    alignment: int = int(Qt.AlignmentFlag.AlignCenter),
) -> None:
    """이미 말줄임한 문자열을 그리면서 일치 구간만 강조 배경과 글자색으로 바꿈

    - 일치 구간이 없으면 원래 글자색으로 한 번에 그려 평소 렌더링 비용을 늘리지 않음
    """
    segments = split_search_segments(text, query)
    if not any(matched for _segment, matched in segments):
        painter.setPen(color)
        painter.drawText(rect, alignment, text)
        return
    metrics = painter.fontMetrics()
    boxes = _segment_boxes(metrics, rect, segments, alignment)
    baseline = rect.top() + (rect.height() + metrics.ascent() - metrics.descent()) / 2.0
    highlight_bg = QColor(palette.badge_pending[0])
    highlight_fg = QColor(palette.badge_pending[1])
    top = baseline - metrics.ascent()
    for (segment, _matched), box in zip(segments, boxes, strict=True):
        if box.matched:
            painter.fillRect(
                QRectF(box.start, top, float(box.width), float(metrics.height())),
                highlight_bg,
            )
            painter.setPen(highlight_fg)
        else:
            painter.setPen(color)
        painter.drawText(QPointF(box.start, baseline), segment)


def draw_cell_text(
    painter: QPainter,
    rect: QRectF,
    text: str,
    option: QStyleOptionViewItem,
    *,
    palette: ThemePalette,
    color: QColor,
    alignment: int = int(Qt.AlignmentFlag.AlignCenter),
) -> None:
    """델리게이트가 그리는 글자를 표 뷰의 지금 검색어로 강조해 그림

    - 검색어가 없거나 일치하지 않으면 평소처럼 한 번에 그림
    """
    query = search_query_of(cast("QWidget | None", option.widget))
    draw_text_with_search(
        painter, rect, text, query=query, palette=palette, color=color, alignment=alignment
    )


def paint_plain_cell_with_search(
    painter: QPainter,
    option: QStyleOptionViewItem,
    index: QModelIndex,
    *,
    query: str,
    palette: ThemePalette,
    elide: bool = True,
) -> bool:
    """기본 스타일로 배경과 아이콘만 그리고 글자만 검색 강조로 다시 그림

    - 일치하는 값이 없으면 False를 돌려 호출자가 평소 기본 렌더링을 쓰게 함
    - `elide`가 거짓이면 말줄임 없이 잘라 그림(파일명 열)
    """
    text = index.data(Qt.ItemDataRole.DisplayRole)
    if not isinstance(text, str) or not text:
        return False
    segments = split_search_segments(text, query)
    if not any(matched for _segment, matched in segments):
        return False

    widget = cast("QWidget | None", option.widget)
    style = widget.style() if widget is not None else QApplication.style()
    styled = QStyleOptionViewItem(option)
    styled.text = ""
    # - 델리게이트에 들어온 option은 initStyleOption 전이라 모델 행 배경이 비어 있으므로, 일치 셀도 이웃 셀과 같은 행 색을 쓰게 채움
    background = index.data(Qt.ItemDataRole.BackgroundRole)
    if isinstance(background, (QColor, QBrush)):
        styled.backgroundBrush = QBrush(background)
    if style is not None:
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, widget)

    painter.save()
    painter.setClipRect(option.rect)
    font_val = index.data(Qt.ItemDataRole.FontRole)
    painter.setFont(font_val if isinstance(font_val, QFont) else option.font)
    rect = QRectF(option.rect)
    icon = index.data(Qt.ItemDataRole.DecorationRole)
    if isinstance(icon, QIcon) and not icon.isNull():
        icon_rect = QRect(
            option.rect.x() + _CELL_H_PADDING_PX,
            option.rect.y() + (option.rect.height() - _CELL_ICON_PX) // 2,
            _CELL_ICON_PX,
            _CELL_ICON_PX,
        )
        icon.paint(painter, icon_rect)
        rect.setLeft(rect.left() + _CELL_ICON_PX + _CELL_ICON_GAP_PX)
    rect = rect.adjusted(_CELL_H_PADDING_PX, 0, -_CELL_H_PADDING_PX, 0)

    align_val = index.data(Qt.ItemDataRole.TextAlignmentRole)
    alignment = int(align_val) if isinstance(align_val, int) else int(option.displayAlignment)
    if elide:
        text = painter.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, max(int(rect.width()), 1)
        )
    fg_val = index.data(Qt.ItemDataRole.ForegroundRole)
    color = fg_val if isinstance(fg_val, QColor) else QColor(palette.text_primary)
    draw_text_with_search(
        painter, rect, text, query=query, palette=palette, color=color, alignment=alignment
    )
    painter.restore()
    return True


def paint_search_cell(
    painter: QPainter,
    option: QStyleOptionViewItem,
    index: QModelIndex,
    palette: ThemePalette,
) -> bool:
    """표 뷰가 들고 있는 검색어와 값이 일치하면 강조 렌더링으로 대체하고 True를 돌려줌

    - 검색어가 없거나 값이 일치하지 않으면 False를 돌려 호출자가 평소 그리기를 이어 감
    """
    query = search_query_of(cast("QWidget | None", option.widget))
    if not query:
        return False
    return paint_plain_cell_with_search(painter, option, index, query=query, palette=palette)


class SearchHighlightDelegate(QStyledItemDelegate):
    """사용자 지정 델리게이트가 없는 열에서 검색 일치 구간만 강조해 그리는 기본 델리게이트"""

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """검색어와 일치하는 값은 강조 렌더링으로, 나머지는 기본 렌더링으로 그림"""
        if painter is not None and paint_search_cell(
            painter, option, index, ThemeManager().current_palette
        ):
            return
        super().paint(painter, option, index)




