"""테이블 셀 배지 렌더링 커스텀 아이템 델리게이트 모듈"""

from PyQt6.QtCore import QModelIndex, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from src.gui.constants import FONT_TABLE_BADGE_PT, FONT_UI_FAMILY
from src.gui.styles.palettes import ThemePalette
from src.gui.table_model import VideoTableModel
from src.gui.theme import ThemeManager

_ICON_TEXT_GAP_PX: int = 4
_CELL_ICON_PX: int = 12
_CELL_H_PADDING_PX: int = 4
_BADGE_PAD_X_PX: int = 10
_BADGE_MARGIN_PX: int = 8
_BADGE_MAX_HEIGHT_PX: int = 22
_BADGE_BORDER_WIDTH_PX: float = 1.0


class _PaletteDelegateBase(QStyledItemDelegate):
    """선택·호버 배경과 테마 색상을 캐싱해 그리는 테이블 델리게이트 공용 기반"""

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
        palette: ThemePalette,
    ) -> None:
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, self._get_color(palette.bg_selected))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, self._get_color(palette.bg_hover))


class PillBadgeDelegate(_PaletteDelegateBase):
    """테이블 셀의 분류 상태, 화질 등급, 권장 작업 조치를 뱃지 형태로 렌더링하는 델리게이트"""

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
        """셀 뱃지 배경·텍스트 직접 렌더링"""
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
        self._paint_state_background(painter, option, p)

        bg_hex, fg_hex, border_hex = badge_info

        painter.setFont(self._badge_font)
        fm = painter.fontMetrics()
        text_width = fm.horizontalAdvance(text)

        pad_x = _BADGE_PAD_X_PX
        badge_h = min(option.rect.height() - _BADGE_MARGIN_PX, _BADGE_MAX_HEIGHT_PX)
        badge_w = min(text_width + pad_x * 2, option.rect.width() - _BADGE_MARGIN_PX)

        x = option.rect.x() + (option.rect.width() - badge_w) / 2.0
        y = option.rect.y() + (option.rect.height() - badge_h) / 2.0
        pill_rect = QRectF(x, y, badge_w, badge_h)

        # 둥근 뱃지 배경 및 테두리 렌더링
        painter.setBrush(self._get_color(bg_hex))
        painter.setPen(QPen(self._get_color(border_hex), _BADGE_BORDER_WIDTH_PX))
        painter.drawRoundedRect(pill_rect, badge_h / 2.0, badge_h / 2.0)

        # 뱃지 내부 텍스트 렌더링 (너비 초과 시 말줄임 처리)
        elided_text = fm.elidedText(
            text, Qt.TextElideMode.ElideRight, int(badge_w - pad_x)
        )
        painter.setPen(self._get_color(fg_hex))
        painter.drawText(
            pill_rect,
            int(Qt.AlignmentFlag.AlignCenter),
            elided_text,
        )

        painter.restore()


class IconTextDelegate(_PaletteDelegateBase):
    """텍스트와 우측 SVG 아이콘을 셀 중앙에 나란히 배치해 렌더링하는 델리게이트"""

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """셀 텍스트와 DecorationRole SVG 아이콘 중앙 정렬 렌더링"""
        if painter is None:
            return
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        text: str | None = index.data(Qt.ItemDataRole.DisplayRole)
        if not isinstance(icon, QIcon) or icon.isNull() or not text:
            super().paint(painter, option, index)
            return

        p = ThemeManager().current_palette
        painter.save()
        self._paint_state_background(painter, option, p)

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
        total_w = text_w + _ICON_TEXT_GAP_PX + _CELL_ICON_PX

        start_x = option.rect.x() + max((option.rect.width() - total_w) // 2, _CELL_H_PADDING_PX)
        text_rect = QRect(start_x, option.rect.y(), text_w, option.rect.height())
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            elided_text,
        )

        icon_x = start_x + text_w + _ICON_TEXT_GAP_PX
        icon_y = option.rect.y() + (option.rect.height() - _CELL_ICON_PX) // 2
        icon.paint(painter, QRect(icon_x, icon_y, _CELL_ICON_PX, _CELL_ICON_PX))
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
