"""테이블 셀 배지 렌더링 커스텀 아이템 델리게이트 모듈"""

from typing import cast

from PyQt6.QtCore import QModelIndex, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from src.gui.constants import (
    COMPACT_PROPERTY,
    COMPACT_VALUE_ON,
    FONT_TABLE_BADGE_PT,
    FONT_UI_FAMILY,
    FONT_UI_PT,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.table.frame_analysis_columns import (
    ACTIVE_FRAME_KEEP_FULL_TEXT,
    ACTIVE_FRAME_VR_TEXT,
)
from src.gui.table.table_model import VideoTableModel
from src.gui.table.table_search_highlight import (
    draw_cell_text,
    paint_plain_cell_with_search,
    paint_search_cell,
    search_query_of,
)
from src.gui.theme import ThemeManager

_ICON_TEXT_GAP_PX: int = 4
_CELL_ICON_PX: int = 12
_CELL_H_PADDING_PX: int = 4
_BADGE_PAD_X_PX: int = 10
_BADGE_MARGIN_PX: int = 8
_BADGE_MAX_HEIGHT_PX: int = 22
_BADGE_BORDER_WIDTH_PX: float = 1.0
_QUIET_DOT_DIAMETER_PX: int = 8
_QUIET_DOT_GAP_PX: int = 6
_CHIP_PAD_X_PX: int = 8
_CHIP_MARGIN_PX: int = 6
_CHIP_MAX_HEIGHT_PX: int = 18
_CHIP_BORDER_WIDTH_PX: float = 1.0
_ROW_ACCENT_WIDTH_PX: int = 4
# - 좁게 보기에서는 배지, 칩의 위아래 여백을 줄여 행 높이를 낮춤
_COMPACT_MARGIN_FACTOR: int = 2


def _is_compact(option: QStyleOptionViewItem) -> bool:
    """표 뷰의 compact QSS 속성이 켜져 있는지 판정, 뷰 없이 그리는 옵션이면 꺼짐"""
    # - PyQt6 스텁은 widget을 항상 있는 것으로 선언하지만, 직접 만든 옵션은 비어 있을 수 있음
    widget = cast("QWidget | None", option.widget)
    return widget is not None and widget.property(COMPACT_PROPERTY) == COMPACT_VALUE_ON

def _margin_px(base_px: int, option: QStyleOptionViewItem) -> int:
    """좁게 보기에서는 배지, 칩의 위아래 여백을 절반으로 줄임"""
    return base_px // _COMPACT_MARGIN_FACTOR if _is_compact(option) else base_px


class _PaletteDelegateBase(QStyledItemDelegate):
    """선택, 호버 배경과 테마 색상을 캐싱해 그리는 테이블 델리게이트 공용 기반"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color_cache: dict[str, QColor] = {}

    def _get_color(self, hex_str: str) -> QColor:
        color = self._color_cache.get(hex_str)
        if color is None:
            color = QColor(hex_str)
            self._color_cache[hex_str] = color
        return color

    def _paint_state_background(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
        palette: ThemePalette,
    ) -> None:
        """선택, 호버, 인코딩 진행 행 배경을 칠함

        - 선택 행은 테마 선택색을 그대로 써서 진행 행 강조와 겹치지 않게 함
        """
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, self._get_color(palette.bg_selected))
            return
        if option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, self._get_color(palette.bg_hover))
            return
        row_bg = index.data(Qt.ItemDataRole.BackgroundRole)
        if isinstance(row_bg, QColor):
            painter.fillRect(option.rect, row_bg)


class PillBadgeDelegate(_PaletteDelegateBase):
    """테이블 셀의 권장 작업 조치, 인코딩 상태를 채운 알약 배지로 렌더링하는 델리게이트"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._badge_font = QFont(
            FONT_UI_FAMILY, FONT_TABLE_BADGE_PT, QFont.Weight.Bold
        )

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """셀 뱃지 배경, 아이콘, 텍스트 직접 렌더링"""
        if painter is None:
            return
        badge_info: tuple[str, str, str] | None = index.data(
            VideoTableModel.BADGE_ROLE
        )
        text: str | None = index.data(Qt.ItemDataRole.DisplayRole)

        if not badge_info or not text:
            super().paint(painter, option, index)
            return

        p = ThemeManager().current_palette

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_state_background(painter, option, index, p)

        bg_hex, fg_hex, border_hex = badge_info
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        has_icon = isinstance(icon, QIcon) and not icon.isNull()
        icon_w = _CELL_ICON_PX + _ICON_TEXT_GAP_PX if has_icon else 0

        painter.setFont(self._badge_font)
        fm = painter.fontMetrics()
        text_width = fm.horizontalAdvance(text)

        pad_x = _BADGE_PAD_X_PX
        badge_h = min(
            option.rect.height() - _margin_px(_BADGE_MARGIN_PX, option), _BADGE_MAX_HEIGHT_PX
        )
        badge_w = min(
            text_width + icon_w + pad_x * 2, option.rect.width() - _BADGE_MARGIN_PX
        )

        x = option.rect.x() + (option.rect.width() - badge_w) / 2.0
        y = option.rect.y() + (option.rect.height() - badge_h) / 2.0
        pill_rect = QRectF(x, y, badge_w, badge_h)

        # 둥근 뱃지 배경 및 테두리 렌더링
        painter.setBrush(self._get_color(bg_hex))
        painter.setPen(QPen(self._get_color(border_hex), _BADGE_BORDER_WIDTH_PX))
        painter.drawRoundedRect(pill_rect, badge_h / 2.0, badge_h / 2.0)

        painter.setPen(self._get_color(fg_hex))
        if not has_icon:
            # 뱃지 내부 텍스트 렌더링 (너비 초과 시 말줄임 처리)
            elided_text = fm.elidedText(
                text, Qt.TextElideMode.ElideRight, int(badge_w - pad_x)
            )
            draw_cell_text(
                painter,
                QRectF(pill_rect),
                elided_text,
                option,
                palette=p,
                color=self._get_color(fg_hex),
            )
            painter.restore()
            return

        # 아이콘과 텍스트를 한 묶음으로 중앙에 배치
        elided_text = fm.elidedText(
            text, Qt.TextElideMode.ElideRight, max(int(badge_w - pad_x - icon_w), 1)
        )
        content_w = fm.horizontalAdvance(elided_text) + icon_w
        content_x = pill_rect.x() + (pill_rect.width() - content_w) / 2.0
        icon_y = pill_rect.y() + (pill_rect.height() - _CELL_ICON_PX) / 2.0
        icon.paint(
            painter,
            QRect(round(content_x), round(icon_y), _CELL_ICON_PX, _CELL_ICON_PX),
        )
        draw_cell_text(
            painter,
            QRectF(content_x + icon_w, pill_rect.y(), content_w - icon_w, pill_rect.height()),
            elided_text,
            option,
            palette=p,
            color=self._get_color(fg_hex),
        )
        painter.restore()


class QuietDotDelegate(_PaletteDelegateBase):
    """작은 색 점과 굵은 글자로 값만 조용히 강조하는 델리게이트

    - 채운 알약 대신 점과 글자를 써서 1단계 배지와 시각적 무게를 구분
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text_font = QFont(FONT_UI_FAMILY, FONT_UI_PT, QFont.Weight.Bold)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """색 점과 글자색을 함께 그리는 셀 렌더링"""
        if painter is None:
            return
        text: str | None = index.data(Qt.ItemDataRole.DisplayRole)
        badge_info: tuple[str, str, str] | None = index.data(
            VideoTableModel.BADGE_ROLE
        )
        if not text or badge_info is None:
            super().paint(painter, option, index)
            return

        p = ThemeManager().current_palette
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_state_background(painter, option, index, p)
        painter.setFont(self._text_font)
        fm = painter.fontMetrics()

        dot = _QUIET_DOT_DIAMETER_PX
        text_w = min(
            fm.horizontalAdvance(text),
            max(option.rect.width() - dot - _QUIET_DOT_GAP_PX - _CELL_H_PADDING_PX * 2, 0),
        )
        total_w = text_w + dot + _QUIET_DOT_GAP_PX
        start_x = option.rect.x() + max(
            (option.rect.width() - total_w) // 2, _CELL_H_PADDING_PX
        )
        dot_y = option.rect.y() + (option.rect.height() - dot) // 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._get_color(badge_info[1]))
        painter.drawEllipse(QRect(start_x, dot_y, dot, dot))

        painter.setPen(self._get_color(badge_info[1]))
        elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, text_w)
        draw_cell_text(
            painter,
            QRectF(
                start_x + dot + _QUIET_DOT_GAP_PX,
                option.rect.y(),
                text_w,
                option.rect.height(),
            ),
            elided,
            option,
            palette=p,
            color=self._get_color(badge_info[1]),
            alignment=int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        )
        painter.restore()


class PlainTextDelegate(_PaletteDelegateBase):
    """알약 없이 배지 글자색만 빌려 쓰는 일반 글자 델리게이트"""

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """셀 배경과 색이 있는 일반 글자를 그리는 렌더링"""
        if painter is None:
            return
        text: str | None = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            super().paint(painter, option, index)
            return

        p = ThemeManager().current_palette
        painter.save()
        self._paint_state_background(painter, option, index, p)

        font_val = index.data(Qt.ItemDataRole.FontRole)
        painter.setFont(font_val if isinstance(font_val, QFont) else option.font)
        badge_info: tuple[str, str, str] | None = index.data(
            VideoTableModel.BADGE_ROLE
        )
        fg_val = index.data(Qt.ItemDataRole.ForegroundRole)
        if isinstance(fg_val, QColor):
            color = fg_val
        elif badge_info is not None:
            color = self._get_color(badge_info[1])
        else:
            color = self._get_color(p.text_secondary)
        painter.setPen(color)

        fm = painter.fontMetrics()
        text_rect = option.rect.adjusted(_CELL_H_PADDING_PX, 0, -_CELL_H_PADDING_PX, 0)
        elided = fm.elidedText(
            text, Qt.TextElideMode.ElideRight, max(text_rect.width(), 0)
        )
        draw_cell_text(
            painter,
            QRectF(text_rect),
            elided,
            option,
            palette=p,
            color=color,
        )
        painter.restore()


class OutlineChipDelegate(_PaletteDelegateBase):
    """가는 테두리만 있는 마이크로 칩으로 짧은 값을 표시하는 델리게이트"""

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """테두리 칩과 값 글자를 그리는 셀 렌더링"""
        if painter is None:
            return
        text: str | None = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            super().paint(painter, option, index)
            return

        p = ThemeManager().current_palette
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_state_background(painter, option, index, p)

        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()

        chip_h = min(
            option.rect.height() - _margin_px(_CHIP_MARGIN_PX, option) * 2, _CHIP_MAX_HEIGHT_PX
        )
        chip_w = min(
            fm.horizontalAdvance(text) + _CHIP_PAD_X_PX * 2,
            option.rect.width() - _CHIP_MARGIN_PX * 2,
        )
        chip_rect = QRectF(
            option.rect.x() + (option.rect.width() - chip_w) / 2.0,
            option.rect.y() + (option.rect.height() - chip_h) / 2.0,
            chip_w,
            chip_h,
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self._get_color(p.text_secondary), _CHIP_BORDER_WIDTH_PX))
        painter.drawRoundedRect(chip_rect, chip_h / 2.0, chip_h / 2.0)

        painter.setPen(self._get_color(p.text_secondary))
        elided = fm.elidedText(
            text, Qt.TextElideMode.ElideRight, max(int(chip_w - _CHIP_PAD_X_PX), 1)
        )
        draw_cell_text(
            painter,
            QRectF(chip_rect),
            elided,
            option,
            palette=p,
            color=self._get_color(p.text_secondary),
        )
        painter.restore()


class IconTextDelegate(_PaletteDelegateBase):
    """텍스트 왼쪽 SVG 아이콘을 셀 중앙에 나란히 배치해 렌더링하는 델리게이트

    - 표 셀 아이콘은 모두 글자 왼쪽에 둠(TERMINOLOGY 6.17절)
    """

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """셀 DecorationRole SVG 아이콘과 텍스트 중앙 정렬 렌더링"""
        if painter is None:
            return
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        text: str | None = index.data(Qt.ItemDataRole.DisplayRole)
        if not isinstance(icon, QIcon) or icon.isNull() or not text:
            # - 아이콘 없는 값(크기 글자, 감지 실패 등)도 다른 열처럼 검색어를 강조
            if not paint_search_cell(painter, option, index, ThemeManager().current_palette):
                super().paint(painter, option, index)
            return

        p = ThemeManager().current_palette
        painter.save()
        self._paint_state_background(painter, option, index, p)

        font_val = index.data(Qt.ItemDataRole.FontRole)
        font = font_val if isinstance(font_val, QFont) else option.font
        painter.setFont(font)

        fg_val = index.data(Qt.ItemDataRole.ForegroundRole)
        fg_color = fg_val if isinstance(fg_val, QColor) else self._get_color(p.text_primary)
        painter.setPen(fg_color)

        fm = painter.fontMetrics()
        max_text_w = max(option.rect.width() - _CELL_ICON_PX - _ICON_TEXT_GAP_PX - _CELL_H_PADDING_PX * 2, 0)
        elided_text = fm.elidedText(text, Qt.TextElideMode.ElideRight, max_text_w)
        text_w = fm.horizontalAdvance(elided_text)
        total_w = _CELL_ICON_PX + _ICON_TEXT_GAP_PX + text_w

        start_x = option.rect.x() + max((option.rect.width() - total_w) // 2, _CELL_H_PADDING_PX)
        icon_y = option.rect.y() + (option.rect.height() - _CELL_ICON_PX) // 2
        icon.paint(painter, QRect(start_x, icon_y, _CELL_ICON_PX, _CELL_ICON_PX))

        text_rect = QRect(
            start_x + _CELL_ICON_PX + _ICON_TEXT_GAP_PX,
            option.rect.y(),
            text_w,
            option.rect.height(),
        )
        draw_cell_text(
            painter,
            QRectF(text_rect),
            elided_text,
            option,
            palette=p,
            color=fg_color,
            alignment=int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        )
        painter.restore()


class ActiveFrameDelegate(IconTextDelegate):
    """활성 화면 열에서 VR 보호와 전체 화면 유지 표기를 작은 알약으로 강조하는 델리게이트

    - 나머지 값은 아이콘을 글자 왼쪽에 두는 글자 렌더링을 그대로 씀(사용자 지정 연필, 여백 확인 필요 경고)
    """

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """알약 표기는 알약으로, 나머지 값은 글자와 왼쪽 아이콘으로 그림"""
        if painter is None:
            return
        text: str | None = index.data(Qt.ItemDataRole.DisplayRole)
        p = ThemeManager().current_palette
        pill_tones = {
            ACTIVE_FRAME_VR_TEXT: p.badge_optimal,
            ACTIVE_FRAME_KEEP_FULL_TEXT: p.badge_pass,
        }
        tones = pill_tones.get(text or "")
        if tones is None:
            super().paint(painter, option, index)
            return

        bg_hex, fg_hex, border_hex = tones
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        has_icon = isinstance(icon, QIcon) and not icon.isNull()
        icon_w = _CELL_ICON_PX + _ICON_TEXT_GAP_PX if has_icon else 0

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_state_background(painter, option, index, p)
        painter.setFont(QFont(FONT_UI_FAMILY, FONT_TABLE_BADGE_PT, QFont.Weight.Bold))
        fm = painter.fontMetrics()

        pill_h = min(
            option.rect.height() - _margin_px(_CHIP_MARGIN_PX, option) * 2, _CHIP_MAX_HEIGHT_PX
        )
        pill_w = min(
            fm.horizontalAdvance(text or "") + icon_w + _CHIP_PAD_X_PX * 2,
            option.rect.width() - _CHIP_MARGIN_PX * 2,
        )
        pill_rect = QRectF(
            option.rect.x() + (option.rect.width() - pill_w) / 2.0,
            option.rect.y() + (option.rect.height() - pill_h) / 2.0,
            pill_w,
            pill_h,
        )
        painter.setBrush(self._get_color(bg_hex))
        painter.setPen(QPen(self._get_color(border_hex), _BADGE_BORDER_WIDTH_PX))
        painter.drawRoundedRect(pill_rect, pill_h / 2.0, pill_h / 2.0)
        painter.setPen(self._get_color(fg_hex))

        elided = fm.elidedText(
            text or "", Qt.TextElideMode.ElideRight, max(int(pill_w - _CHIP_PAD_X_PX - icon_w), 1)
        )
        content_w = fm.horizontalAdvance(elided) + icon_w
        content_x = pill_rect.x() + (pill_rect.width() - content_w) / 2.0
        if isinstance(icon, QIcon) and has_icon:
            icon_y = pill_rect.y() + (pill_rect.height() - _CELL_ICON_PX) / 2.0
            icon.paint(
                painter,
                QRect(round(content_x), round(icon_y), _CELL_ICON_PX, _CELL_ICON_PX),
            )
        draw_cell_text(
            painter,
            QRectF(content_x + icon_w, pill_rect.y(), content_w - icon_w, pill_rect.height()),
            elided,
            option,
            palette=p,
            color=self._get_color(fg_hex),
            alignment=int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        )
        painter.restore()


class ClipTextDelegate(QStyledItemDelegate):
    """말줄임표(...) 없이 단일 행 경계에서 텍스트를 잘라 렌더링하는 델리게이트"""

    def initStyleOption(
        self,
        option: QStyleOptionViewItem | None,
        index: QModelIndex,
    ) -> None:
        """텍스트 말줄임과 자동 줄바꿈을 비활성화해 열 경계에서 그대로 잘리도록 설정"""
        super().initStyleOption(option, index)
        if option is not None:
            option.textElideMode = Qt.TextElideMode.ElideNone
            option.features &= ~QStyleOptionViewItem.ViewItemFeature.WrapText


class NameAccentDelegate(ClipTextDelegate):
    """파일명 셀 텍스트를 자르지 않고 그린 뒤 인코딩 진행 행 왼쪽 띠를 덧그리는 델리게이트"""

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """파일명과 배경을 칠한 뒤 진행 행 왼쪽 띠를 그림

        - 선택 행에서도 띠를 보이도록 기본 렌더링 뒤에 덧그림
        """
        if painter is None:
            return
        palette = ThemeManager().current_palette
        query = search_query_of(cast("QWidget | None", option.widget))
        if not (
            query
            and paint_plain_cell_with_search(
                painter, option, index, query=query, palette=palette, elide=False
            )
        ):
            super().paint(painter, option, index)
        accent: QColor | None = index.data(VideoTableModel.ACCENT_ROLE)
        if not isinstance(accent, QColor):
            return
        painter.save()
        painter.fillRect(
            QRect(
                option.rect.x(),
                option.rect.y(),
                _ROW_ACCENT_WIDTH_PX,
                option.rect.height(),
            ),
            accent,
        )
        painter.restore()


