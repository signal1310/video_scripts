"""내장 미리 보기 영상 위에 겹쳐 보이는 정보 표시와 실행 결과 안내 위젯 모듈

- 영상 표면(QVideoWidget 내부의 별도 네이티브 창)이 같은 창의 일반 위젯을 덮으므로, 투명 도구 창으로 만들어 영상 위에 둠
- 위치와 영역은 모두 화면 전역 좌표
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import NamedTuple

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QLabel, QLayout, QVBoxLayout, QWidget

from src.core.preview_info import (
    DEFAULT_INFO_BOX_HEIGHT,
    DEFAULT_INFO_BOX_WIDTH,
    DEFAULT_INFO_SCALE,
    INFO_ANCHOR_MAX,
    INFO_ANCHOR_MIN,
    INFO_BOX_RATIO_MIN,
    INFO_SCALE_MAX,
    INFO_SCALE_MIN,
    INFO_SCALE_STEP,
)
from src.gui.styles.icons import get_active_palette

EDGE_MARGIN_PX: int = 12
TOAST_DURATION_MS: int = 2500
_ROW_GAP_PX: int = 14
_NBSP: str = "&nbsp;"
# - 이름과 값 한 쌍은 줄바꿈되지 않게 공백을 붙이고, 쌍 사이 구분자의 공백에서만 줄을 바꿈
_SINGLE_LINE_SEPARATOR: str = " / "
_BASE_FONT_PT: float = 9.0
_WHEEL_NOTCH: int = 120
_DRAG_TOOLTIP: str = "끌어서 위치를, 가장자리를 끌어 정보 상자 크기를, 휠로 정보 크기를 바꿀 수 있습니다."
_PAD_X_PX: int = 10
_PAD_Y_PX: int = 6
# - 글자 둘레 번짐: 배경이 연해도 밝은 장면에서 글자가 묻히지 않게 하는 테두리 역할
_OUTLINE_BLUR_PX: int = 5
_RESIZE_MARGIN_PX: int = 7
# - 실제 하한은 fit_to가 내용의 최소 크기로 올리므로 여기서는 양수만 보장
_MIN_BOX_SIZE_PX: int = 1
_RESIZE_CURSORS: dict[Qt.Edge, Qt.CursorShape] = {
    Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
    Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
}


def anchor_to_pos(area: QRect, size: QSize, anchor_x: float, anchor_y: float) -> QPoint:
    """영역 안에서 위젯이 움직일 수 있는 범위를 0~1 비율로 환산한 위치, 위젯이 영역보다 크면 영역 왼쪽 위"""
    free_w = max(area.width() - size.width(), 0)
    free_h = max(area.height() - size.height(), 0)
    return QPoint(area.left() + round(free_w * anchor_x), area.top() + round(free_h * anchor_y))


def pos_to_anchor(area: QRect, size: QSize, pos: QPoint) -> tuple[float, float]:
    """위젯의 왼쪽 위 위치를 영역 안의 0~1 비율로 환산, 움직일 여유가 없는 방향은 0"""
    free_w = area.width() - size.width()
    free_h = area.height() - size.height()
    ratio_x = (pos.x() - area.left()) / free_w if free_w > 0 else INFO_ANCHOR_MIN
    ratio_y = (pos.y() - area.top()) / free_h if free_h > 0 else INFO_ANCHOR_MIN
    return (
        min(max(ratio_x, INFO_ANCHOR_MIN), INFO_ANCHOR_MAX),
        min(max(ratio_y, INFO_ANCHOR_MIN), INFO_ANCHOR_MAX),
    )


def _value_html(value: str, color: str | None) -> str:
    """이미 이스케이프된 값에 상태 색을 입힘, 색이 없으면 기본 글자색 그대로"""
    return value if color is None else f'<span style="color:{color}">{value}</span>'


def build_info_html(
    rows: list[tuple[str, str]],
    *,
    single_line: bool,
    note: str,
    muted_color: str,
    value_colors: list[str | None] | None = None,
) -> str:
    """이름은 흐리게, 값은 상태 색(없으면 기본 색)으로 한 줄 또는 여러 줄로 배치한 정보 표시 HTML"""
    colors = value_colors if value_colors is not None else [None] * len(rows)
    pairs = [
        (html.escape(label), html.escape(value), color)
        for (label, value), color in zip(rows, colors, strict=True)
    ]
    note_html = html.escape(note)
    if single_line:
        parts = [
            f'<span style="color:{muted_color}">{label.replace(" ", _NBSP)}</span>'
            f"{_NBSP}{_value_html(value.replace(' ', _NBSP), color)}"
            for label, value, color in pairs
        ]
        if note_html:
            parts.insert(0, note_html)
        return _SINGLE_LINE_SEPARATOR.join(parts)
    lines: list[str] = []
    if note_html:
        lines.append(f'<tr><td colspan="2">{note_html}</td></tr>')
    lines.extend(
        f'<tr><td style="color:{muted_color}; padding-right:{_ROW_GAP_PX}px">{label}</td>'
        f"<td>{_value_html(value, color)}</td></tr>"
        for label, value, color in pairs
    )
    return f'<table cellspacing="0" cellpadding="1">{"".join(lines)}</table>'


class _FloatingCard(QWidget):
    """미리 보기 창이 소유하고 영상 영역 위에 놓이는 투명 도구 창, 배경은 안쪽 틀이 그림

    - 반투명 최상위 창은 자기 자신의 QSS 배경을 그리지 않으므로 배경을 가진 틀을 자식으로 둠
    - 글자 라벨은 배경과 분리해 둘레 번짐 효과가 배경 상자가 아닌 글자에만 걸리게 함
    """

    def __init__(self, object_name: str, parent: QWidget) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._frame = QFrame(self)
        self._frame.setObjectName(f"{object_name}Frame")
        # - 서식 있는 라벨은 마우스 이동 이벤트를 소비해 바깥 창의 끌기가 끊기므로 틀과 라벨 모두 마우스 입력을 통과시킴
        self._frame.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._label = QLabel(self._frame)
        self._label.setObjectName(object_name)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        outline = QGraphicsDropShadowEffect(self._label)
        outline.setOffset(0, 0)
        outline.setBlurRadius(_OUTLINE_BLUR_PX)
        outline.setColor(QColor(get_active_palette().media_overlay_outline))
        self._label.setGraphicsEffect(outline)
        frame_layout = QVBoxLayout(self._frame)
        frame_layout.setContentsMargins(_PAD_X_PX, _PAD_Y_PX, _PAD_X_PX, _PAD_Y_PX)
        frame_layout.addWidget(self._label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # - 크기는 fit_to가 정하므로 레이아웃이 이전 내용 크기를 창 최소 크기로 붙잡아 줄이지 못하게 하지 않음
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.addWidget(self._frame)
        self.hide()

    def set_text(self, text: str) -> None:
        """카드에 표시할 리치 텍스트 설정"""
        self._label.setText(text)

    def fit_to(self, max_width: int, *, width: int | None = None, height: int | None = None) -> None:
        """내용 크기로 맞추고, 너비나 높이를 지정하면 그 상자 크기를 따르되 내용이 넘치는 높이는 내용만큼 늘림

        - 너비를 지정하면 그 안에서 줄바꿈, 지정하지 않으면 영역에 들어갈 때 줄바꿈하지 않고 넘칠 때만 영역 너비에서 줄바꿈
        """
        limit = max(max_width - 2 * _PAD_X_PX, 1)
        if width is None:
            self._label.setWordWrap(False)
            if self._label.sizeHint().width() > limit:
                self._label.setWordWrap(True)
            inner_width = limit if self._label.wordWrap() else self._label.sizeHint().width()
        else:
            self._label.setWordWrap(True)
            # - 이름과 값 쌍은 줄바꿈되지 않으므로 가장 넓은 쌍보다 좁아지지 않게 함
            inner_width = max(width - 2 * _PAD_X_PX, self._label.minimumSizeHint().width(), 1)
        content_height = self._label.heightForWidth(inner_width)
        if content_height <= 0:
            content_height = self._label.sizeHint().height()
        inner_height = content_height if height is None else max(height - 2 * _PAD_Y_PX, content_height)
        self.resize(inner_width + 2 * _PAD_X_PX, inner_height + 2 * _PAD_Y_PX)


class _ResizeDrag(NamedTuple):
    """가장자리 끌기 시작 시점의 끌린 변과 기준 지오메트리"""

    edges: Qt.Edge
    press_global: QPoint
    geometry: QRect


class PreviewInfoOverlay(_FloatingCard):
    """영상 위에 정보를 보여 주고 끌어서 옮기고, 가장자리를 끌어 상자의 너비와 높이를 따로 바꿀 수 있는 표시

    - 글자 크기는 휠이나 설정 막대로만 바꾸고, 가장자리 끌기는 글상자처럼 상자 크기만 바꿈
    - 놓은 위치와 상자 크기는 영역 대비 비율로 알림
    """

    anchor_changed = pyqtSignal(float, float)
    scale_changed = pyqtSignal(int)
    box_changed = pyqtSignal(float, float)

    def __init__(self, parent: QWidget, get_area: Callable[[], QRect]) -> None:
        super().__init__("PreviewInfoOverlay", parent)
        self._get_area = get_area
        self._anchor: tuple[float, float] = (INFO_ANCHOR_MIN, INFO_ANCHOR_MIN)
        self._box: tuple[float, float] = (DEFAULT_INFO_BOX_WIDTH, DEFAULT_INFO_BOX_HEIGHT)
        self._scale: int = DEFAULT_INFO_SCALE
        self._wheel_remainder: int = 0
        # - 누른 지점과 창 왼쪽 위의 전역 좌표 차이
        self._drag_offset: QPoint | None = None
        self._resize_drag: _ResizeDrag | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip(_DRAG_TOOLTIP)

    def set_content(
        self,
        rows: list[tuple[str, str]],
        *,
        single_line: bool,
        note: str,
        anchor: tuple[float, float],
        scale: int = DEFAULT_INFO_SCALE,
        box: tuple[float, float] = (DEFAULT_INFO_BOX_WIDTH, DEFAULT_INFO_BOX_HEIGHT),
        value_colors: list[str | None] | None = None,
    ) -> None:
        """내용, 위치 비율, 글자 크기 배율, 상자 크기 비율을 반영하고 영상 영역 안으로 배치, 끄는 중에는 끄는 값을 유지"""
        if self._drag_offset is None and self._resize_drag is None:
            self._anchor = anchor
            self._scale = scale
            self._box = box
        font = self._label.font()
        font.setPointSizeF(_BASE_FONT_PT * self._scale / DEFAULT_INFO_SCALE)
        self._label.setFont(font)
        self.set_text(
            build_info_html(
                rows,
                single_line=single_line,
                note=note,
                muted_color=get_active_palette().media_overlay_label,
                value_colors=value_colors,
            )
        )
        self.reposition()

    def reposition(self) -> None:
        """영상 영역이 바뀌었을 때 저장된 비율 위치와 상자 크기로 다시 놓음"""
        area = self._get_area()
        self._fit_box(area)
        self.move(anchor_to_pos(area, self.size(), *self._anchor))

    def _fit_box(self, area: QRect) -> None:
        """저장된 상자 비율을 영역 픽셀로 바꿔 맞춤, 0이면 내용에 맞춘 자동 크기"""
        width_ratio, height_ratio = self._box
        width = round(area.width() * width_ratio) if width_ratio > INFO_BOX_RATIO_MIN else None
        height = round(area.height() * height_ratio) if height_ratio > INFO_BOX_RATIO_MIN else None
        self.fit_to(area.width(), width=width, height=height)

    def _edges_at(self, pos: QPoint) -> Qt.Edge:
        """창 가장자리 여백 안에 든 변, 안쪽이면 빈 값"""
        edges = Qt.Edge(0)
        if pos.x() < _RESIZE_MARGIN_PX:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - _RESIZE_MARGIN_PX:
            edges |= Qt.Edge.RightEdge
        if pos.y() < _RESIZE_MARGIN_PX:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - _RESIZE_MARGIN_PX:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _resize_by(self, drag: _ResizeDrag, global_pos: QPoint) -> None:
        """끈 변의 축만 너비 또는 높이를 바꾸고, 반대쪽 변을 고정해 배치, 끌지 않은 축은 기준 크기 유지"""
        area = self._get_area()
        delta = global_pos - drag.press_global
        base = drag.geometry
        width = base.width()
        if drag.edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            grow = -delta.x() if drag.edges & Qt.Edge.LeftEdge else delta.x()
            width = min(max(base.width() + grow, _MIN_BOX_SIZE_PX), max(area.width(), 1))
        height = base.height()
        if drag.edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            grow = -delta.y() if drag.edges & Qt.Edge.TopEdge else delta.y()
            height = min(max(base.height() + grow, _MIN_BOX_SIZE_PX), max(area.height(), 1))
        self._box = (
            width / max(area.width(), 1),
            height / max(area.height(), 1),
        )
        self._fit_box(area)
        # - 내용 높이가 더 크면 fit_to가 높이를 올리므로 실제 크기 기준으로 반대쪽 변을 고정
        left = base.right() + 1 - self.width() if drag.edges & Qt.Edge.LeftEdge else base.left()
        top = base.bottom() + 1 - self.height() if drag.edges & Qt.Edge.TopEdge else base.top()
        self._anchor = pos_to_anchor(area, self.size(), QPoint(left, top))
        self.move(anchor_to_pos(area, self.size(), *self._anchor))

    def wheelEvent(self, a0: QWheelEvent | None) -> None:
        """휠 한 칸마다 글자 크기 배율을 한 단계씩 바꾸고, 터치패드의 작은 이동량은 모아서 반영"""
        if a0 is None:
            return
        self._wheel_remainder += a0.angleDelta().y()
        notches = int(self._wheel_remainder / _WHEEL_NOTCH)
        self._wheel_remainder -= notches * _WHEEL_NOTCH
        if notches:
            target = self._scale + notches * INFO_SCALE_STEP
            self.scale_changed.emit(min(max(target, INFO_SCALE_MIN), INFO_SCALE_MAX))
        a0.accept()

    def mousePressEvent(self, ev: QMouseEvent | None) -> None:
        """가장자리에서 누르면 상자 크기 조절, 안쪽에서 누르면 이동을 시작"""
        if ev is not None and ev.button() == Qt.MouseButton.LeftButton:
            global_pos = ev.globalPosition().toPoint()
            edges = self._edges_at(ev.position().toPoint())
            if edges:
                self._resize_drag = _ResizeDrag(edges, global_pos, self.geometry())
            else:
                self._drag_offset = global_pos - self.pos()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev: QMouseEvent | None) -> None:
        """끄는 중이면 이동이나 상자 크기 조절을 반영하고, 아니면 가장자리에 맞는 커서로 바꿈"""
        if ev is None:
            return
        global_pos = ev.globalPosition().toPoint()
        if self._resize_drag is not None:
            self._resize_by(self._resize_drag, global_pos)
        elif self._drag_offset is not None:
            area = self._get_area()
            ratio = pos_to_anchor(area, self.size(), global_pos - self._drag_offset)
            self._anchor = ratio
            self.move(anchor_to_pos(area, self.size(), *ratio))
        else:
            self.setCursor(
                _RESIZE_CURSORS.get(self._edges_at(ev.position().toPoint()), Qt.CursorShape.SizeAllCursor)
            )
            super().mouseMoveEvent(ev)
            return
        ev.accept()

    def mouseReleaseEvent(self, ev: QMouseEvent | None) -> None:
        """끌기를 끝내고 놓은 위치 비율과, 상자 크기를 끌었다면 상자 비율을 알림"""
        if ev is not None and (self._drag_offset is not None or self._resize_drag is not None):
            resized = self._resize_drag is not None
            self._drag_offset = None
            self._resize_drag = None
            self.anchor_changed.emit(*self._anchor)
            if resized:
                self.box_changed.emit(*self._box)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


class PreviewToast(_FloatingCard):
    """명령 실행 결과를 영상 위 가운데 위쪽에 잠깐 보여 주고 자동으로 사라지는 안내"""

    def __init__(self, parent: QWidget, get_area: Callable[[], QRect]) -> None:
        super().__init__("PreviewToast", parent)
        self._get_area = get_area
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(TOAST_DURATION_MS)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str) -> None:
        """문구를 표시하고 사라질 시간을 다시 잼"""
        self.set_text(html.escape(text))
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start()

    def dismiss(self) -> None:
        """남은 표시 시간과 상관없이 안내를 바로 닫음"""
        self._timer.stop()
        self.hide()

    def reposition(self) -> None:
        """영상 영역 위쪽 가운데로 다시 놓음"""
        area = self._get_area()
        self.fit_to(area.width())
        self.move(area.left() + (area.width() - self.width()) // 2, area.top())
