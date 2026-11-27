"""화질 비교 캔버스: 분할선, 즉시 전환, 좌우 비교 3모드의 프레임 그리기와 확대, 이동 조작

- 확대 배율과 오프셋 계산은 `frame_compare.py` 순수 함수를 재사용
- 좌우 비교는 같은 배율과 픽셀 위치를 두 화면에 함께 적용해 동기화함
- 색은 팔레트 토큰만 씀
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QPen, QResizeEvent, QWheelEvent
from PyQt6.QtWidgets import QLabel, QWidget

from src.core.analysis.frame_compare import (
    ZOOM_FIT,
    ViewTransform,
    clamp_transform,
    fit_transform,
    format_percent,
    split_ratio_for_x,
    step_zoom,
    wheel_zoom_factor,
    zoom_around,
    zoom_preset_scale,
)
from src.gui.styles.palettes import DARK_PALETTE, ThemePalette

MODE_SPLIT: str = "split"
MODE_TOGGLE: str = "toggle"
MODE_SIDE: str = "side"
MODES: tuple[str, ...] = (MODE_SPLIT, MODE_TOGGLE, MODE_SIDE)
DIVIDER_GRAB_PX: int = 14
GRIP_WIDTH_PX: int = 6
GRIP_HEIGHT_PX: int = 44
SIDE_GAP_PX: int = 1
LABEL_MARGIN_PX: int = 10
GRIP_OPACITY: float = 0.62
LABEL_RADIUS_PX: int = 6
_TRANSPARENT = Qt.WidgetAttribute.WA_TransparentForMouseEvents


class FrameCompareCanvas(QWidget):
    """원본과 비교 대상 프레임을 3모드로 그리는 캔버스

    - 분할선 모드는 선 위 드래그로 나눈 위치를, 그 밖은 좌클릭 드래그로 화면을 옮김
    - 즉시 전환 모드는 마우스를 누르고 있는 동안 원본을 보여 줌
    - 휠은 커서 아래 지점을 고정한 채 배율을 바꿈
    """

    scale_changed = pyqtSignal(float)
    side_changed = pyqtSignal(bool)
    zoomed_manually = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FrameCompareCanvas")
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._palette: ThemePalette = DARK_PALETTE
        self._original: QImage | None = None
        self._compared: QImage | None = None
        self._mode: str = MODE_SPLIT
        self._transform: ViewTransform = ViewTransform(scale=1.0, offset_x=0.0, offset_y=0.0)
        self._fit_mode: bool = True
        self._divider: float = 0.5
        self._mouse_peek: bool = False
        self._pinned_original: bool = False
        self._dragging: str | None = None
        self._pan_origin: tuple[QPointF, float, float] | None = None
        # - 접힌 상단 조작 바가 오버레이로 떠 있을 때 라벨이 겹치지 않도록 내리는 여백
        self._top_inset: int = 0
        self._left_label: QLabel = self._make_label("원본")
        self._right_label: QLabel = self._make_label("")
        self._center_badge: QLabel = self._make_label("원본")
        self._sync_label_visibility()

    def _make_label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setAttribute(_TRANSPARENT, True)
        label.hide()
        return label

    def apply_palette(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 바탕과 라벨 색을 맞춤

        - 라벨은 검은 레터박스 위에서도 읽히도록 미리 보기 토스트와 같은 다크 반투명 HUD로 칠함
        """
        self._palette = palette
        style = (
            f"QLabel {{ background: {palette.media_hud_bg}; border: 1px solid {palette.media_track};"
            f" color: {palette.media_fg}; border-radius: {LABEL_RADIUS_PX}px;"
            f" padding: 3px 10px; font-weight: 600; }}"
        )
        for label in (self._left_label, self._right_label, self._center_badge):
            label.setStyleSheet(style)
        self.update()

    def set_images(
        self, original: QImage | None, compared: QImage | None, *, reset_view: bool = False
    ) -> None:
        """원본과 비교 대상 프레임을 바꿈

        - 새 영상이거나 기준 프레임 해상도가 바뀔 때만 맞춤으로 다시 맞춤
        - 같은 영상의 시점, 대상 전환은 현재 배율과 오프셋을 화면 범위 안에서 유지
        """
        previous = self._primary_image()
        previous_size = None if previous is None else (previous.width(), previous.height())
        new_size = self._reference_size(original, compared)
        self._original = original
        self._compared = compared
        if reset_view or previous_size is None or new_size is None or previous_size != new_size:
            self.fit()
            return
        view_width, view_height = self._view_size()
        self._transform = clamp_transform(
            self._transform, new_size[0], new_size[1], view_width, view_height
        )
        self.update()

    def _reference_size(
        self, original: QImage | None, compared: QImage | None
    ) -> tuple[int, int] | None:
        """두 프레임이 겹쳐 그려질 기준 크기. 원본을 먼저 쓰고 없으면 비교 이미지"""
        image = original if original is not None else compared
        return None if image is None else (image.width(), image.height())

    def set_labels(self, original: str, compared: str, compared_tooltip: str) -> None:
        """좌우 비교 라벨 문구와 축소 비교 풀이 툴팁"""
        self._left_label.setText(original)
        self._right_label.setText(compared)
        self._right_label.setToolTip(compared_tooltip)
        self._sync_label_visibility()

    def set_mode(self, mode: str) -> None:
        """비교 모드를 바꾸고 임시 표시와 드래그를 끝냄"""
        if mode not in MODES or mode == self._mode:
            return
        self._mode = mode
        self._dragging = None
        self._pan_origin = None
        self._set_mouse_peek(False)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if mode == MODE_TOGGLE else Qt.CursorShape.OpenHandCursor
        )
        self._sync_label_visibility()
        self.update()

    def mode(self) -> str:
        """현재 비교 모드"""
        return self._mode

    def set_original_pinned(self, pinned: bool) -> None:
        """즉시 전환 모드에서 C 키로 고정한 원본 표시 상태"""
        self._pinned_original = pinned
        self._emit_side()

    def original_pinned(self) -> bool:
        """C 키로 원본을 고정해 보는 중인지"""
        return self._pinned_original

    def set_peek(self, show_original: bool) -> None:
        """마우스를 누르고 있는 동안의 원본 표시 상태"""
        self._set_mouse_peek(show_original)

    def showing_original(self) -> bool:
        """지금 원본을 보여 주는지"""
        return self._mouse_peek or self._pinned_original

    def split_ratio(self) -> float:
        """분할선 위치 비율"""
        return self._divider

    def set_split_ratio(self, ratio: float) -> None:
        """분할선 위치 비율을 범위 안으로 맞춤"""
        width = max(self.width(), 1)
        self._divider = split_ratio_for_x(ratio * width, width)
        self.update()

    def scale(self) -> float:
        """현재 배율"""
        return self._transform.scale

    def has_image(self) -> bool:
        """표시 중인 프레임이 있는지"""
        return self._original is not None or self._compared is not None

    def clear_images(self) -> None:
        """표시 중인 두 프레임을 지워 빈 화면으로 되돌림

        - 새 영상을 열 때 이전 영상의 프레임이 새 제목 아래 남지 않게 함
        """
        if self._original is None and self._compared is None:
            return
        self._original = None
        self._compared = None
        self.update()

    def fit(self) -> None:
        """영상 전체가 보이도록 맞춤. 이후 창 크기 변경에도 맞춤을 유지"""
        self._fit_mode = True
        self._recompute_fit(emit=True)

    def apply_zoom_preset(self, preset: str) -> None:
        """배율 버튼의 배율을 화면 가운데 기준으로 적용"""
        image = self._primary_image()
        if image is None:
            return
        view_width, view_height = self._view_size()
        target = zoom_preset_scale(preset, view_width, view_height, image.width(), image.height())
        self._fit_mode = preset == ZOOM_FIT
        if preset == ZOOM_FIT:
            self._recompute_fit(emit=True)
            return
        self._zoom_to(target)

    def apply_zoom_scale(self, scale: float) -> bool:
        """지정 배율을 화면 가운데 기준으로 적용. 표시할 프레임이 없으면 거짓"""
        if self._primary_image() is None:
            return False
        self._zoom_to(scale)
        return True

    def zoom_step(self, direction: int) -> None:
        """+/- 단계 확대, 축소"""
        current = self._transform.scale if self._transform.scale > 0 else 1.0
        self._zoom_to(step_zoom(current, direction))

    def percent_text(self) -> str:
        """현재 배율 퍼센트 문구"""
        return format_percent(self._transform.scale)

    def _zoom_to(self, target: float) -> None:
        image = self._primary_image()
        if image is None:
            return
        view_width, view_height = self._view_size()
        current = self._transform.scale if self._transform.scale > 0 else 1.0
        self._fit_mode = False
        self._transform = zoom_around(
            self._transform,
            target / current,
            cursor_x=view_width / 2,
            cursor_y=view_height / 2,
            image_width=image.width(),
            image_height=image.height(),
            view_width=view_width,
            view_height=view_height,
        )
        self.scale_changed.emit(self._transform.scale)
        self.update()

    def _primary_image(self) -> QImage | None:
        return self._original if self._original is not None else self._compared

    def _view_size(self) -> tuple[float, float]:
        """영상 한 장이 그려지는 화면 크기. 좌우 비교는 한쪽 폭"""
        if self._mode == MODE_SIDE:
            half = (self.width() - SIDE_GAP_PX) / 2
            return max(half, 0.0), float(self.height())
        return float(self.width()), float(self.height())

    def _recompute_fit(self, *, emit: bool) -> None:
        image = self._primary_image()
        if image is None:
            return
        view_width, view_height = self._view_size()
        self._transform = fit_transform(view_width, view_height, image.width(), image.height())
        if emit:
            self.scale_changed.emit(self._transform.scale)
        self.update()

    def _set_mouse_peek(self, show_original: bool) -> None:
        if self._mouse_peek == show_original:
            return
        self._mouse_peek = show_original
        self._emit_side()

    def _emit_side(self) -> None:
        self.side_changed.emit(self.showing_original())
        self._sync_label_visibility()
        self.update()

    def _sync_label_visibility(self) -> None:
        split = self._mode == MODE_SPLIT
        side = self._mode == MODE_SIDE
        toggle = self._mode == MODE_TOGGLE
        self._left_label.setVisible(split or side)
        self._right_label.setVisible(split or side)
        self._center_badge.setVisible(toggle)

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        """창 크기가 바뀌면 맞춤 배율을 다시 계산하거나 현재 배율을 화면 안으로 제한"""
        super().resizeEvent(event)
        image = self._primary_image()
        if image is None:
            return
        view_width, view_height = self._view_size()
        if self._fit_mode:
            self._transform = fit_transform(view_width, view_height, image.width(), image.height())
            self.scale_changed.emit(self._transform.scale)
        else:
            self._transform = clamp_transform(
                self._transform, image.width(), image.height(), view_width, view_height
            )
        self.update()

    def set_top_inset(self, inset_px: int) -> None:
        """상단 오버레이가 보일 때 원본, 비교 라벨을 그만큼 아래로 비켜 놓음"""
        inset = max(0, inset_px)
        if inset == self._top_inset:
            return
        self._top_inset = inset
        self._layout_labels()
        self.update()

    def top_inset(self) -> int:
        """상단 오버레이가 라벨과 알림을 내려 놓은 여백"""
        return self._top_inset

    def _layout_labels(self) -> None:
        """모드별 라벨 자리를 잡음

        - 즉시 전환 배지는 1, 3번 모드의 원본 라벨과 같은 좌상단에 두어 상단 가운데 토스트와 겹치지 않게 함
        """
        margin = LABEL_MARGIN_PX
        top = margin + self._top_inset
        for label in (self._left_label, self._right_label, self._center_badge):
            label.adjustSize()
        self._left_label.move(margin, top)
        self._center_badge.move(margin, top)
        if self._mode == MODE_SIDE:
            self._right_label.move(int(self.width() / 2 + SIDE_GAP_PX) + margin, top)
            return
        self._right_label.move(max(margin, self.width() - self._right_label.width() - margin), top)

    def _update_badge_text(self) -> None:
        self._center_badge.setText("원본" if self.showing_original() else self._right_label.text())

    def paintEvent(self, event: QPaintEvent | None) -> None:
        """현재 모드에 맞춰 원본과 비교 대상 프레임, 분할선, 라벨을 그림"""
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self._palette.media_bg))
        if self._mode == MODE_SIDE:
            self._paint_side(painter)
        elif self._mode == MODE_TOGGLE:
            image = self._original if self.showing_original() else self._compared
            self._paint_image(painter, image, 0.0)
        else:
            self._paint_split(painter)
        self._update_badge_text()
        self._layout_labels()
        painter.end()

    def _paint_side(self, painter: QPainter) -> None:
        half = (self.width() - SIDE_GAP_PX) / 2
        painter.save()
        painter.setClipRect(0, 0, int(half), self.height())
        self._paint_image(painter, self._original, 0.0)
        painter.restore()
        right_x = half + SIDE_GAP_PX
        painter.save()
        painter.setClipRect(int(right_x), 0, self.width(), self.height())
        self._paint_image(painter, self._compared, right_x)
        painter.restore()
        painter.fillRect(int(half), 0, SIDE_GAP_PX, self.height(), QColor(self._palette.border_subtle))

    def _paint_split(self, painter: QPainter) -> None:
        divider_x = self._divider * self.width()
        self._paint_image(painter, self._original, 0.0)
        painter.save()
        painter.setClipRect(int(divider_x), 0, self.width(), self.height())
        self._paint_image(painter, self._compared, 0.0)
        painter.restore()
        self._paint_divider(painter, divider_x)

    def _paint_image(self, painter: QPainter, image: QImage | None, origin_x: float) -> None:
        if image is None:
            return
        reference_width, reference_height = self._reference_size(self._original, self._compared) or (
            image.width(),
            image.height(),
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._transform.scale < 1.0)
        painter.translate(origin_x + self._transform.offset_x, self._transform.offset_y)
        painter.scale(self._transform.scale, self._transform.scale)
        painter.drawImage(QRectF(0.0, 0.0, float(reference_width), float(reference_height)), image)
        painter.restore()

    def _paint_divider(self, painter: QPainter, divider_x: float) -> None:
        pen = QPen(QColor(self._palette.split_divider))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawLine(QPointF(divider_x, 0), QPointF(divider_x, self.height()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._palette.media_fg))
        painter.setOpacity(GRIP_OPACITY)
        painter.drawRoundedRect(
            int(divider_x - GRIP_WIDTH_PX / 2),
            int(self.height() / 2 - GRIP_HEIGHT_PX / 2),
            GRIP_WIDTH_PX,
            GRIP_HEIGHT_PX,
            3,
            3,
        )
        painter.setOpacity(1.0)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        """분할선 위면 분할선 드래그를, 그 밖은 이동을 시작하고 즉시 전환 모드는 원본을 보여 줌"""
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        point = event.position()
        if self._mode == MODE_SPLIT and abs(point.x() - self._divider * self.width()) <= DIVIDER_GRAB_PX:
            self._dragging = "divider"
            event.accept()
            return
        if self._mode == MODE_TOGGLE:
            self._set_mouse_peek(True)
        self._dragging = "pan"
        self._pan_origin = (point, self._transform.offset_x, self._transform.offset_y)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        """분할선 위치나 화면 오프셋을 갱신하고 분할선 근처에서 커서를 바꿈"""
        if event is None:
            return
        point = event.position()
        if self._dragging == "divider":
            self._divider = split_ratio_for_x(point.x(), float(self.width()))
            self.update()
            return
        if self._dragging == "pan" and self._pan_origin is not None:
            image = self._primary_image()
            if image is None:
                return
            origin, start_x, start_y = self._pan_origin
            view_width, view_height = self._view_size()
            self._fit_mode = False
            self._transform = clamp_transform(
                ViewTransform(
                    scale=self._transform.scale,
                    offset_x=start_x + (point.x() - origin.x()),
                    offset_y=start_y + (point.y() - origin.y()),
                ),
                image.width(),
                image.height(),
                view_width,
                view_height,
            )
            self.update()
            return
        if self._mode == MODE_SPLIT:
            near = abs(point.x() - self._divider * self.width()) <= DIVIDER_GRAB_PX
            self.setCursor(Qt.CursorShape.SizeHorCursor if near else Qt.CursorShape.OpenHandCursor)

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        """즉시 전환 임시 표시를 끝내고 드래그 상태를 푸는 커서로 되돌림"""
        if event is None:
            return
        if self._mouse_peek:
            self._set_mouse_peek(False)
        self._dragging = None
        self._pan_origin = None
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if self._mode == MODE_TOGGLE else Qt.CursorShape.OpenHandCursor
        )

    def wheelEvent(self, event: QWheelEvent | None) -> None:
        """커서 아래 지점을 고정한 채 배율을 바꿈"""
        if event is None:
            return
        image = self._primary_image()
        if image is None:
            return
        cursor_x, cursor_y = self._local_view_point(event.position())
        view_width, view_height = self._view_size()
        self._fit_mode = False
        self._transform = zoom_around(
            self._transform,
            wheel_zoom_factor(event.angleDelta().y()),
            cursor_x=cursor_x,
            cursor_y=cursor_y,
            image_width=image.width(),
            image_height=image.height(),
            view_width=view_width,
            view_height=view_height,
        )
        self.scale_changed.emit(self._transform.scale)
        self.zoomed_manually.emit()
        self.update()
        event.accept()

    def _local_view_point(self, point: QPointF) -> tuple[float, float]:
        """좌우 비교에서 오른쪽 화면 좌표를 그 화면 기준으로 옮김"""
        if self._mode == MODE_SIDE:
            half = (self.width() - SIDE_GAP_PX) / 2
            if point.x() > half:
                return point.x() - half - SIDE_GAP_PX, point.y()
        return point.x(), point.y()
