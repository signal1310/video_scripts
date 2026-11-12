from typing import Optional, Tuple
from PyQt6.QtCore import QModelIndex, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from src.gui.models import VideoTableModel
from src.gui.theme import ThemeManager


class PillBadgeDelegate(QStyledItemDelegate):
    """
    - 테이블 셀의 상태, 화질 등급, 인코딩 처방을 뱃지 형태로 렌더링하는 델리게이트.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._badge_font = QFont("Malgun Gothic", 9, QFont.Weight.Bold)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        badge_info: Optional[Tuple[str, str, str]] = index.data(
            VideoTableModel.BADGE_ROLE
        )
        text: Optional[str] = index.data(Qt.ItemDataRole.DisplayRole)

        if not badge_info or not text:
            super().paint(painter, option, index)
            return

        p = ThemeManager().current_palette

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 선택된 행 또는 호버 상태 배경 처리
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(p.bg_selected))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, QColor(p.bg_hover))

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
        painter.setBrush(QColor(bg_hex))
        painter.setPen(QPen(QColor(border_hex), 1.0))
        painter.drawRoundedRect(pill_rect, badge_h / 2.0, badge_h / 2.0)

        # 뱃지 내부 텍스트 렌더링 (너비 초과 시 말줄임 처리)
        elided_text = fm.elidedText(
            text, Qt.TextElideMode.ElideRight, int(badge_w - pad_x)
        )
        painter.setPen(QColor(fg_hex))
        painter.drawText(
            pill_rect,
            int(Qt.AlignmentFlag.AlignCenter),
            elided_text,
        )

        painter.restore()
