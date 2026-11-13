"""테이블 셀 배지 렌더링 커스텀 아이템 델리게이트 모듈"""

from PyQt6.QtCore import QModelIndex, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from src.gui.constants import FONT_TABLE_BADGE_PT, FONT_UI_FAMILY
from src.gui.table_model import VideoTableModel
from src.gui.theme import ThemeManager


class PillBadgeDelegate(QStyledItemDelegate):
    """테이블 셀의 상태, 화질 등급, 인코딩 처방을 뱃지 형태로 렌더링하는 델리게이트"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._badge_font = QFont(
            FONT_UI_FAMILY, FONT_TABLE_BADGE_PT, QFont.Weight.Bold
        )
        self._color_cache: dict[str, QColor] = {}

    def _get_color(self, hex_str: str) -> QColor:
        color = self._color_cache.get(hex_str)
        if color is None:
            color = QColor(hex_str)
            self._color_cache[hex_str] = color
        return color

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

        # 선택된 행 또는 호버 상태 배경 처리
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, self._get_color(p.bg_selected))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, self._get_color(p.bg_hover))

        bg_hex, fg_hex, border_hex = badge_info

        painter.setFont(self._badge_font)
        fm = painter.fontMetrics()
        text_width = fm.horizontalAdvance(text)

        pad_x = 10
        badge_h = min(option.rect.height() - 8, 22)
        badge_w = min(text_width + pad_x * 2, option.rect.width() - 8)

        x = option.rect.x() + (option.rect.width() - badge_w) / 2.0
        y = option.rect.y() + (option.rect.height() - badge_h) / 2.0
        pill_rect = QRectF(x, y, badge_w, badge_h)

        # 둥근 뱃지 배경 및 테두리 렌더링
        painter.setBrush(self._get_color(bg_hex))
        painter.setPen(QPen(self._get_color(border_hex), 1.0))
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
