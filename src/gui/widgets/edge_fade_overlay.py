"""가로 스크롤 경계에 테마 배경색 페이드 아웃을 렌더링하는 공용 오버레이 모듈"""

from collections.abc import Callable

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent
from PyQt6.QtWidgets import QScrollBar, QWidget

from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin

EDGE_FADE_WIDTH_PX = 64
_FADE_STOP_NEAR = 0.28
_FADE_STOP_MID = 0.62
_FADE_ALPHA_OPAQUE = 255
_FADE_ALPHA_NEAR = 230
_FADE_ALPHA_MID = 115


class HorizontalEdgeFadeOverlay(QWidget, ThemeAwareMixin):
    """가로 스크롤 가능한 양쪽 경계에 배경색 페이드 아웃을 그리는 오버레이 위젯"""

    def __init__(
        self,
        parent: QWidget,
        color_picker: Callable[[ThemePalette], str],
        *,
        fade_width: int = EDGE_FADE_WIDTH_PX,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._color_picker = color_picker
        self._fade_width = fade_width
        self._fade_color = QColor()
        self._can_scroll_left = False
        self._can_scroll_right = False
        self.init_theme_listener()

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에서 추출한 색상으로 페이드 그라디언트 갱신"""
        self._fade_color = QColor(self._color_picker(palette))
        self.update()

    def set_scroll_edges(self, can_left: bool, can_right: bool) -> None:
        """좌우 스크롤 가능 상태를 갱신하고 변경 시 다시 그리기 예약"""
        if self._can_scroll_left == can_left and self._can_scroll_right == can_right:
            return
        self._can_scroll_left = can_left
        self._can_scroll_right = can_right
        self.update()

    def _edge_color(self, alpha: int) -> QColor:
        color = QColor(self._fade_color)
        color.setAlpha(alpha)
        return color

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        """잘리는 경계에 다단 그라디언트로 깊고 긴 페이드 아웃 렌더링"""
        if not (self._can_scroll_left or self._can_scroll_right):
            return
        fade_w = min(self._fade_width, self.width() // 2)
        if fade_w <= 0 or self.height() <= 0:
            return

        painter = QPainter(self)
        if self._can_scroll_left:
            grad_left = QLinearGradient(0.0, 0.0, float(fade_w), 0.0)
            grad_left.setColorAt(0.0, self._edge_color(_FADE_ALPHA_OPAQUE))
            grad_left.setColorAt(_FADE_STOP_NEAR, self._edge_color(_FADE_ALPHA_NEAR))
            grad_left.setColorAt(_FADE_STOP_MID, self._edge_color(_FADE_ALPHA_MID))
            grad_left.setColorAt(1.0, self._edge_color(0))
            painter.fillRect(QRect(0, 0, fade_w, self.height()), grad_left)

        if self._can_scroll_right:
            right_x = self.width() - fade_w
            grad_right = QLinearGradient(float(right_x), 0.0, float(self.width()), 0.0)
            grad_right.setColorAt(0.0, self._edge_color(0))
            grad_right.setColorAt(1.0 - _FADE_STOP_MID, self._edge_color(_FADE_ALPHA_MID))
            grad_right.setColorAt(1.0 - _FADE_STOP_NEAR, self._edge_color(_FADE_ALPHA_NEAR))
            grad_right.setColorAt(1.0, self._edge_color(_FADE_ALPHA_OPAQUE))
            painter.fillRect(QRect(right_x, 0, fade_w, self.height()), grad_right)


def sync_edge_overlay_from_scrollbar(
    overlay: HorizontalEdgeFadeOverlay | None,
    h_bar: QScrollBar | None,
    *,
    enabled: bool = True,
) -> None:
    """가로 스크롤바 상태에 맞춰 좌우 경계 페이드 표시 여부를 동기화"""
    if overlay is not None:
        # - 뷰포트 스크롤은 자식 위젯까지 함께 이동시키므로 매번 부모 영역에 다시 고정
        parent = overlay.parentWidget()
        if parent is not None and overlay.geometry() != parent.rect():
            overlay.setGeometry(parent.rect())
    if overlay is None or h_bar is None or not enabled:
        if overlay is not None:
            overlay.set_scroll_edges(False, False)
        return
    has_range = h_bar.maximum() > h_bar.minimum()
    overlay.set_scroll_edges(
        has_range and h_bar.value() > h_bar.minimum(),
        has_range and h_bar.value() < h_bar.maximum(),
    )
