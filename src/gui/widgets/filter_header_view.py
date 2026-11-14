"""열별 동적 필터 아이콘 오버레이 및 클릭 트리거를 지원하는 수평 헤더 위젯 모듈"""

from PyQt6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QHeaderView, QStyle, QStyleOptionHeader, QWidget

from src.gui.constants import FONT_TABLE_HEADER_PT
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette

_FILTER_BTN_MAX_SIZE: int = 15
_FILTER_BTN_MIN_SIZE: int = 12
_FILTER_BTN_EDGE_MARGIN: int = 6
_HEADER_BORDER_RIGHT: int = 1
_HEADER_BORDER_BOTTOM: int = 2
_HEADER_H_PADDING: int = 14
_HEADER_V_PADDING: int = 20
_HEADER_TEXT_H_INSET: int = 6
_HEADER_TEXT_V_INSET: int = 4
_HEADER_MIN_WRAP_WIDTH: int = 24
_HEADER_MAX_BOUNDING_HEIGHT: int = 1000
_HEADER_MIN_FONT_PT: float = 7.5
_HEADER_FONT_STEP_PT: float = 0.5
_SORT_INDICATOR_RESERVE: int = 16
_FILTER_BTN_RADIUS: float = 4.5
_RESIZE_EDGE_MARGIN: int = 6
_MIN_SECTION_FOR_BUTTON: int = 38
_NARROW_SECTION_MAX_WIDTH: int = 56
_FILTER_ICON_INSET: int = 3
_ACTIVE_BAR_HEIGHT: int = 2
_HALO_SPREAD_PX: float = 4.0
_HALO_ALPHA_DEFAULT: int = 42
_HALO_ALPHA_ACTIVE: int = 72
_HALO_ALPHA_HOVER: int = 60


class FilterHeaderView(QHeaderView):
    """줄바꿈 중앙 정렬 열 제목을 유지하며 호버·활성 열 우하단에 반투명 필터 버튼을 표시하는 헤더 뷰"""

    filter_clicked = pyqtSignal(int, QPoint)

    def __init__(
        self,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(orientation, parent)
        self.setSectionsClickable(True)
        self.setMouseTracking(True)
        self._hovered_section: int = -1
        self._hovered_button: bool = False
        self._pressed_filter_section: int = -1
        self._active_filter_cols: set[int] = set()
        self.sectionResized.connect(self._on_section_resized)

    def _on_section_resized(self, _logical_col: int, _old_w: int, _new_w: int) -> None:
        self.updateGeometry()

    def _header_font(
        self,
        *,
        bold: bool = True,
        point_size: float = FONT_TABLE_HEADER_PT,
    ) -> QFont:
        header_font = QFont(self.font())
        header_font.setPointSizeF(point_size)
        header_font.setBold(bold)
        return header_font

    def _fit_label_font(self, text: str, avail_w: int) -> QFont:
        """단일 어절이 좁은 열 폭을 넘을 때 좌우 대칭 잘림 대신 폰트 크기를 단계적으로 축소"""
        words = text.split()
        if not words or avail_w <= 0:
            return self._header_font()
        pt = float(FONT_TABLE_HEADER_PT)
        while pt > _HEADER_MIN_FONT_PT:
            font = self._header_font(point_size=pt)
            metrics = QFontMetrics(font)
            if max(metrics.horizontalAdvance(w) for w in words) <= avail_w:
                return font
            pt -= _HEADER_FONT_STEP_PT
        return self._header_font(point_size=_HEADER_MIN_FONT_PT)

    def _header_text(self, logical_index: int) -> str:
        model = self.model()
        if model is None:
            return ""
        raw = model.headerData(
            logical_index, self.orientation(), Qt.ItemDataRole.DisplayRole
        )
        return "" if raw is None else str(raw)

    def sectionSizeFromContents(self, logicalIndex: int) -> QSize:
        """현재 열 너비 기준 TextWordWrap 줄 수를 반영해 필요한 헤더 높이를 계산"""
        base = super().sectionSizeFromContents(logicalIndex)
        if self.orientation() != Qt.Orientation.Horizontal:
            return base
        text = self._header_text(logicalIndex)
        if not text:
            return base
        sec_w = self.sectionSize(logicalIndex)
        effective_w = sec_w if sec_w > 0 else base.width()
        h_reserve = _HEADER_H_PADDING
        if self.isSortIndicatorShown() and self.sortIndicatorSection() == logicalIndex:
            h_reserve += _SORT_INDICATOR_RESERVE
        avail_w = max(_HEADER_MIN_WRAP_WIDTH, effective_w - h_reserve)
        metrics = QFontMetrics(self._header_font())
        text_rect = metrics.boundingRect(
            QRect(0, 0, avail_w, _HEADER_MAX_BOUNDING_HEIGHT),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            text,
        )
        return QSize(base.width(), max(base.height(), text_rect.height() + _HEADER_V_PADDING))

    def set_active_filter_columns(self, cols: set[int]) -> None:
        """활성 동적 필터가 걸린 논리 열 인덱스 집합을 갱신"""
        new_cols = set(cols)
        if new_cols == self._active_filter_cols:
            return
        self._active_filter_cols = new_cols
        vp = self.viewport()
        if vp is not None:
            vp.update()

    def active_filter_columns(self) -> set[int]:
        """현재 필터가 활성화된 열 인덱스 집합 사본 반환"""
        return set(self._active_filter_cols)

    def _section_rect(self, logical_col: int) -> QRect:
        if logical_col < 0 or self.isSectionHidden(logical_col):
            return QRect()
        x = self.sectionViewportPosition(logical_col)
        w = self.sectionSize(logical_col)
        return QRect(x, 0, w, self.height())

    def _filter_button_rect_in_section(self, section_rect: QRect) -> QRect:
        if not section_rect.isValid() or section_rect.width() < _MIN_SECTION_FOR_BUTTON:
            return QRect()
        if section_rect.width() < _NARROW_SECTION_MAX_WIDTH:
            btn_size = _FILTER_BTN_MIN_SIZE
        else:
            btn_size = min(
                _FILTER_BTN_MAX_SIZE,
                max(_FILTER_BTN_MIN_SIZE, section_rect.height() - 10),
            )
        left = (
            section_rect.right()
            - _HEADER_BORDER_RIGHT
            - _FILTER_BTN_EDGE_MARGIN
            - btn_size
            + 1
        )
        top = max(
            section_rect.top() + (section_rect.height() - btn_size) // 2,
            section_rect.bottom()
            - _HEADER_BORDER_BOTTOM
            - _FILTER_BTN_EDGE_MARGIN
            - btn_size
            + 1,
        )
        return QRect(left, top, btn_size, btn_size)

    def filter_button_rect(self, logical_col: int) -> QRect:
        """지정 열의 뷰포트 기준 필터 아이콘 버튼 영역 반환"""
        return self._filter_button_rect_in_section(self._section_rect(logical_col))

    def filter_button_global_rect(self, logical_col: int) -> QRect:
        """지정 열의 전역 좌표계 기준 필터 아이콘 버튼 영역 반환"""
        btn_rect = self.filter_button_rect(logical_col)
        if not btn_rect.isValid():
            return QRect()
        return QRect(self.mapToGlobal(btn_rect.topLeft()), btn_rect.size())

    def section_anchor_global(self, logical_col: int) -> QPoint:
        """지정 열 필터 팝오버를 띄울 전역 좌측 하단 기준 좌표 반환"""
        sec_rect = self._section_rect(logical_col)
        if not sec_rect.isValid():
            return self.mapToGlobal(QPoint(0, self.height()))
        return self.mapToGlobal(QPoint(sec_rect.left(), self.height()))

    def _is_on_resize_grip(self, pos: QPoint) -> bool:
        if self.cursor().shape() == Qt.CursorShape.SplitHCursor or pos.x() < _RESIZE_EDGE_MARGIN:
            return True
        col = self.logicalIndexAt(pos)
        if col < 0:
            return False
        sec_rect = self._section_rect(col)
        return (
            pos.x() - sec_rect.left() < _RESIZE_EDGE_MARGIN
            or sec_rect.right() - pos.x() < _RESIZE_EDGE_MARGIN
        )

    def _hit_filter_button(self, pos: QPoint) -> int:
        if self._is_on_resize_grip(pos):
            return -1
        col = self.logicalIndexAt(pos)
        if col < 0:
            return -1
        btn_rect = self.filter_button_rect(col)
        return col if btn_rect.isValid() and btn_rect.contains(pos) else -1

    def _paint_button_halo(
        self,
        painter: QPainter,
        btn_rect_f: QRectF,
        p: ThemePalette,
        *,
        is_active: bool,
        is_button_hovered: bool,
    ) -> None:
        halo_rect = btn_rect_f.adjusted(
            -_HALO_SPREAD_PX, -_HALO_SPREAD_PX, _HALO_SPREAD_PX, _HALO_SPREAD_PX
        )
        halo_color = QColor(
            p.accent_blue
            if is_active
            else (p.bg_elevated if p.is_dark else p.bg_card)
        )
        halo_alpha = (
            _HALO_ALPHA_ACTIVE
            if is_active
            else (_HALO_ALPHA_HOVER if is_button_hovered else _HALO_ALPHA_DEFAULT)
        )
        halo_grad = QRadialGradient(
            btn_rect_f.center(),
            max(halo_rect.width(), halo_rect.height()) * 0.55,
        )
        halo_center = QColor(halo_color)
        halo_center.setAlpha(halo_alpha)
        halo_mid = QColor(halo_color)
        halo_mid.setAlpha(halo_alpha // 3)
        halo_edge = QColor(halo_color)
        halo_edge.setAlpha(0)
        halo_grad.setColorAt(0.0, halo_center)
        halo_grad.setColorAt(0.58, halo_mid)
        halo_grad.setColorAt(1.0, halo_edge)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo_grad)
        painter.drawRoundedRect(halo_rect, _FILTER_BTN_RADIUS + 3.0, _FILTER_BTN_RADIUS + 3.0)

    def _resolve_button_glass_colors(
        self,
        *,
        is_active: bool,
        is_button_hovered: bool,
    ) -> tuple[QColor, QColor, QColor, str]:
        p = get_active_palette()
        base_surface = QColor(p.bg_elevated if p.is_dark else p.bg_card)
        if is_active:
            tint = QColor(p.accent_blue)
            top_col = QColor(
                (base_surface.red() * 3 + tint.red()) // 4,
                (base_surface.green() * 3 + tint.green()) // 4,
                (base_surface.blue() * 3 + tint.blue()) // 4,
                195,
            )
            bot_col = QColor(
                (base_surface.red() * 2 + tint.red()) // 3,
                (base_surface.green() * 2 + tint.green()) // 3,
                (base_surface.blue() * 2 + tint.blue()) // 3,
                150,
            )
            border_color = QColor(p.accent_blue)
            border_color.setAlpha(185)
            return top_col, bot_col, border_color, p.accent_blue
        if is_button_hovered:
            top_col = QColor(base_surface)
            top_col.setAlpha(205)
            bot_col = QColor(p.btn_hover)
            bot_col.setAlpha(165)
            border_color = QColor(p.border_focus)
            border_color.setAlpha(185)
            return top_col, bot_col, border_color, p.text_primary
        top_col = QColor(base_surface)
        top_col.setAlpha(160)
        bot_col = QColor(base_surface)
        bot_col.setAlpha(108)
        border_color = QColor(p.border_subtle)
        border_color.setAlpha(125)
        return top_col, bot_col, border_color, p.text_secondary

    def _paint_frosted_filter_button(
        self,
        painter: QPainter,
        btn_rect: QRect,
        *,
        is_active: bool,
        is_button_hovered: bool,
    ) -> None:
        p = get_active_palette()
        btn_rect_f = QRectF(btn_rect)

        self._paint_button_halo(
            painter,
            btn_rect_f,
            p,
            is_active=is_active,
            is_button_hovered=is_button_hovered,
        )

        top_col, bot_col, border_color, icon_color = self._resolve_button_glass_colors(
            is_active=is_active,
            is_button_hovered=is_button_hovered,
        )
        glass_grad = QLinearGradient(btn_rect_f.topLeft(), btn_rect_f.bottomLeft())
        glass_grad.setColorAt(0.0, top_col)
        glass_grad.setColorAt(1.0, bot_col)
        painter.setBrush(glass_grad)
        painter.setPen(QPen(border_color, 1.0))
        painter.drawRoundedRect(btn_rect_f, _FILTER_BTN_RADIUS, _FILTER_BTN_RADIUS)

        inner_rect = btn_rect_f.adjusted(0.8, 0.8, -0.8, -0.8)
        inner_glow = QColor(255, 255, 255, 50 if p.is_dark else 115)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(inner_glow, 0.8))
        painter.drawRoundedRect(
            inner_rect, max(1.0, _FILTER_BTN_RADIUS - 0.8), max(1.0, _FILTER_BTN_RADIUS - 0.8)
        )

        icon_rect = btn_rect.adjusted(
            _FILTER_ICON_INSET,
            _FILTER_ICON_INSET,
            -_FILTER_ICON_INSET,
            -_FILTER_ICON_INSET,
        )
        icon_size = max(9, icon_rect.width())
        painter.save()
        if not is_active and not is_button_hovered:
            painter.setOpacity(0.84)
        get_icon("filter", color=icon_color, size=icon_size).paint(painter, icon_rect)
        painter.restore()

    def paintSection(
        self,
        painter: QPainter | None,
        rect: QRect,
        logicalIndex: int,
    ) -> None:
        """줄바꿈·중앙 정렬 열 제목 위에 호버·활성 우하단 반투명 필터 버튼을 렌더링"""
        if (
            painter is None
            or not rect.isValid()
            or self.orientation() != Qt.Orientation.Horizontal
        ):
            super().paintSection(painter, rect, logicalIndex)
            return

        is_active = logicalIndex in self._active_filter_cols
        is_hovered = logicalIndex == self._hovered_section
        btn_rect = (
            self._filter_button_rect_in_section(rect)
            if (is_active or is_hovered)
            else QRect()
        )

        p = get_active_palette()
        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        self.initStyleOptionForIndex(opt, logicalIndex)
        opt.rect = rect
        if is_hovered:
            opt.state |= QStyle.StateFlag.State_MouseOver

        full_text = opt.text
        style = self.style()
        if style is not None:
            # CE_Header에는 빈 텍스트만 넘겨 배경·정렬 화살표만 그리고 라벨은 TextWordWrap으로 직접 렌더
            opt.text = ""
            style.drawControl(QStyle.ControlElement.CE_Header, opt, painter, self)
        else:
            super().paintSection(painter, rect, logicalIndex)

        painter.save()
        painter.setClipRect(rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if full_text:
            label_rect = (
                style.subElementRect(QStyle.SubElement.SE_HeaderLabel, opt, self)
                if style is not None
                else QRect()
            )
            if not label_rect.isValid():
                label_rect = rect.adjusted(
                    _HEADER_TEXT_H_INSET,
                    _HEADER_TEXT_V_INSET,
                    -_HEADER_TEXT_H_INSET,
                    -_HEADER_TEXT_V_INSET,
                )
            painter.setFont(self._fit_label_font(full_text, label_rect.width()))
            painter.setPen(QColor(p.accent_blue if is_active else p.header_text))
            painter.drawText(
                label_rect,
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                full_text,
            )

        if is_active:
            accent_rect = QRect(
                rect.left() + 1,
                rect.bottom() - _ACTIVE_BAR_HEIGHT + 1,
                max(0, rect.width() - 2),
                _ACTIVE_BAR_HEIGHT,
            )
            painter.fillRect(accent_rect, QColor(p.accent_blue))

        if btn_rect.isValid():
            self._paint_frosted_filter_button(
                painter,
                btn_rect,
                is_active=is_active,
                is_button_hovered=is_hovered and self._hovered_button,
            )
        painter.restore()

    def mouseMoveEvent(self, e: QMouseEvent | None) -> None:
        """마우스 이동에 따라 호버된 헤더 섹션과 필터 버튼 강조 상태 갱신"""
        super().mouseMoveEvent(e)
        if e is None:
            return
        pos = e.position().toPoint()
        col = self.logicalIndexAt(pos)
        on_btn = self._hit_filter_button(pos) >= 0
        if col != self._hovered_section or on_btn != self._hovered_button:
            self._hovered_section = col
            self._hovered_button = on_btn
            vp = self.viewport()
            if vp is not None:
                vp.update()

    def leaveEvent(self, a0: QEvent | None) -> None:
        """헤더 이탈 시 호버 필터 버튼 강조 해제"""
        super().leaveEvent(a0)
        if self._hovered_section != -1 or self._hovered_button:
            self._hovered_section = -1
            self._hovered_button = False
            vp = self.viewport()
            if vp is not None:
                vp.update()

    def mousePressEvent(self, e: QMouseEvent | None) -> None:
        """필터 아이콘 영역 좌클릭 시 정렬 대신 팝오버 트리거 준비"""
        if e is not None and e.button() == Qt.MouseButton.LeftButton:
            hit_col = self._hit_filter_button(e.position().toPoint())
            if hit_col >= 0:
                self._pressed_filter_section = hit_col
                e.accept()
                return
        self._pressed_filter_section = -1
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent | None) -> None:
        """필터 아이콘 영역에서 뗄 때 해당 열의 filter_clicked 시그널 방출"""
        if e is not None and self._pressed_filter_section >= 0:
            pressed_col = self._pressed_filter_section
            self._pressed_filter_section = -1
            hit_col = self._hit_filter_button(e.position().toPoint())
            if e.button() == Qt.MouseButton.LeftButton and hit_col == pressed_col:
                self.filter_clicked.emit(
                    pressed_col, self.section_anchor_global(pressed_col)
                )
            e.accept()
            return
        super().mouseReleaseEvent(e)
