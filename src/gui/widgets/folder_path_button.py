"""작업 경로를 폭에 맞춰 구성 요소와 꺾쇠 구분자로 그리는 경로 버튼 모듈"""

import os
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRect, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
)
from PyQt6.QtWidgets import QPushButton, QWidget

from src.core.workspace_paths import parse_root_dirs
from src.gui.constants import PATH_EMPTY_TEXT, make_path_font
from src.gui.styles.icons import get_active_palette
from src.gui.styles.palettes import ThemePalette

_PATH_TEXT_PAD_X_PX: int = 8
_PATH_ELLIPSIS: str = "…"
_EXTRA_ROOTS_TEMPLATE: str = "외 {count}개"
_EXTRA_ROOTS_GAP_PX: int = 10
_SEP_WIDTH_PX: int = 18
_CHEVRON_HALF_WIDTH_PX: float = 2.2
_CHEVRON_HALF_HEIGHT_PX: float = 3.4
_CHEVRON_STROKE_PX: float = 1.9
_CHEVRON_OFFSET_Y_PX: float = 1.5


@dataclass(frozen=True)
class _PathItem:
    """경로 버튼에 그릴 한 조각. 구분자는 텍스트 없이 is_separator로 표시"""

    text: str
    bold: bool = False
    is_separator: bool = False


_SEPARATOR_ITEM = _PathItem("", is_separator=True)


def split_path_parts(path: str) -> list[str]:
    """정규화한 경로를 구분자 기준 구성 요소로 나눔. 빈 요소는 제외해 드라이브 이름만 첫 요소로 남김"""
    return [part for part in os.path.normpath(path).split(os.sep) if part]


def fit_path_items(
    parts: list[str], width: int, normal: QFontMetrics, bold: QFontMetrics
) -> list[_PathItem]:
    """폭에 맞춰 뒤쪽 구성 요소부터 채우고, 생략된 앞쪽이 있으면 말줄임 표시를 앞에 둠

    폴더 이름은 항상 남기며, 앞쪽 생략 여부는 말줄임 자리를 미리 남겨 두고 판정
    """
    leaf = bold.elidedText(parts[-1], Qt.TextElideMode.ElideRight, width)
    used = bold.horizontalAdvance(leaf)
    ellipsis_cost = _SEP_WIDTH_PX + normal.horizontalAdvance(_PATH_ELLIPSIS)
    shown: list[_PathItem] = [_PathItem(leaf, bold=True)]
    hidden_count = len(parts) - 1
    for index in range(len(parts) - 2, -1, -1):
        reserve = ellipsis_cost if index > 0 else 0
        cost = _SEP_WIDTH_PX + normal.horizontalAdvance(parts[index])
        if used + cost + reserve > width:
            break
        used += cost
        shown.insert(0, _PathItem(parts[index]))
        hidden_count -= 1

    items: list[_PathItem] = []
    if hidden_count > 0:
        items.extend([_PathItem(_PATH_ELLIPSIS), _SEPARATOR_ITEM])
    for position, item in enumerate(shown):
        if position > 0:
            items.append(_SEPARATOR_ITEM)
        items.append(item)
    return items


class FolderPathButton(QPushButton):
    """작업 폴더 경로를 그리는 버튼. 상위 경로는 흐리게, 폴더 이름은 굵게 그리고 폭이 모자라면 앞쪽을 생략

    텍스트를 직접 그리므로 QSS의 글자 속성 대신 팔레트 토큰을 사용
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FolderNameButton")
        self._path: str = ""
        self._palette: ThemePalette | None = None

    @property
    def path(self) -> str:
        """표시 중인 작업 폴더 경로 반환"""
        return self._path

    def set_path(self, path: str) -> None:
        """표시할 경로를 지정하고 다시 그리기"""
        self._path = path
        self.update()

    def set_palette(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트를 지정하고 다시 그리기"""
        self._palette = palette
        self.update()

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        """배경과 상태 표시는 기본 그리기에 맡기고, 경로 구성 요소와 구분자만 직접 그림"""
        super().paintEvent(a0)
        palette = self._palette or get_active_palette()
        text_rect = self.rect().adjusted(_PATH_TEXT_PAD_X_PX, 0, -_PATH_TEXT_PAD_X_PX, 0)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._path:
            self._draw_text(
                painter, text_rect, PATH_EMPTY_TEXT, self._font(bold=False), palette.text_muted
            )
        else:
            self._draw_path(painter, text_rect, palette)
        painter.end()

    def _font(self, *, bold: bool) -> QFont:
        """버튼 점 크기를 따르는 경로 글꼴의 일반 또는 굵은 글꼴"""
        return make_path_font(self.font().pointSize(), bold=bold)

    def _draw_path(self, painter: QPainter, text_rect: QRect, palette: ThemePalette) -> None:
        roots = parse_root_dirs(self._path)
        primary = roots[0] if roots else self._path
        parts = split_path_parts(primary)
        normal_font = self._font(bold=False)
        bold_font = self._font(bold=True)
        if not parts:
            self._draw_text(
                painter, text_rect, os.path.normpath(primary), normal_font, palette.text_muted
            )
            return

        extra_text = _EXTRA_ROOTS_TEMPLATE.format(count=len(roots) - 1) if len(roots) > 1 else ""
        # 추가 작업 폴더 수는 경로 구성 요소가 아니므로 구분자 없이 흐린 보조 문구로 뒤에 붙임
        extra_width = (
            _EXTRA_ROOTS_GAP_PX + QFontMetrics(normal_font).horizontalAdvance(extra_text)
            if extra_text
            else 0
        )

        if self.isEnabled():
            ancestor_color, leaf_color, sep_color = (
                palette.text_secondary,
                palette.text_primary,
                palette.text_muted,
            )
        else:
            ancestor_color = leaf_color = sep_color = palette.text_muted

        items = fit_path_items(
            parts,
            text_rect.width() - extra_width,
            QFontMetrics(normal_font),
            QFontMetrics(bold_font),
        )
        center_y = text_rect.center().y() + _CHEVRON_OFFSET_Y_PX
        x = text_rect.left()
        for item in items:
            if item.is_separator:
                self._draw_chevron(painter, x + _SEP_WIDTH_PX / 2, center_y, sep_color)
                x += _SEP_WIDTH_PX
                continue
            font = bold_font if item.bold else normal_font
            width = QFontMetrics(font).horizontalAdvance(item.text)
            color = leaf_color if item.bold else ancestor_color
            self._draw_text(painter, QRect(x, text_rect.top(), width, text_rect.height()), item.text, font, color)
            x += width
        if extra_text:
            extra_x = x + _EXTRA_ROOTS_GAP_PX
            extra_width_px = QFontMetrics(normal_font).horizontalAdvance(extra_text)
            self._draw_text(
                painter,
                QRect(extra_x, text_rect.top(), extra_width_px, text_rect.height()),
                extra_text,
                normal_font,
                sep_color,
            )

    @staticmethod
    def _draw_text(
        painter: QPainter, rect: QRect, text: str, font: QFont, color: str
    ) -> None:
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(
            rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text
        )

    @staticmethod
    def _draw_chevron(painter: QPainter, center_x: float, center_y: float, color: str) -> None:
        """탐색기 주소 표시줄처럼 구성 요소 사이에 얇은 꺾쇠 기호를 그림"""
        pen = QPen(QColor(color))
        pen.setWidthF(_CHEVRON_STROKE_PX)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(
            QPolygonF(
                [
                    QPointF(center_x - _CHEVRON_HALF_WIDTH_PX, center_y - _CHEVRON_HALF_HEIGHT_PX),
                    QPointF(center_x + _CHEVRON_HALF_WIDTH_PX, center_y),
                    QPointF(center_x - _CHEVRON_HALF_WIDTH_PX, center_y + _CHEVRON_HALF_HEIGHT_PX),
                ]
            )
        )

