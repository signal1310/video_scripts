"""미리 보기 영상 위젯을 영상비로 가운데 두고 바깥을 고른 배경 색으로 칠하는 무대 위젯

- 색 키와 점선 계산의 공용 기준은 `gui/media_stage.py`에 있고 이 모듈은 무대 위젯만 맡음
- 점선은 영상 위젯 바깥에 그려 영상 화소를 가리지 않음
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPaintEvent, QResizeEvent
from PyQt6.QtWidgets import QWidget

from src.core.settings.ui_state import DEFAULT_PREVIEW_BACKGROUND
from src.gui.media_stage import make_outline_pen, stage_color, stage_outline_rect
from src.gui.preview.crop_edit import content_rect
from src.gui.styles.palettes import DARK_PALETTE, ThemePalette


class PreviewStage(QWidget):
    """영상 위젯을 영상비로 가운데 두고 영상 바깥을 고른 배경 색으로 칠하는 무대

    - `QVideoWidget`은 자기 안에 검은 레터박스를 직접 그리므로, 영상비와 같은 크기로 맞춰
      레터박스를 없애고 바깥 여백을 이 위젯이 고른 색으로 채움
    - 점선은 영상 위젯 바깥에 그려 영상 화소를 가리지 않음
    """

    # - 영상 위젯이 새 영역에 놓일 때마다 알려, 화면 전역 좌표로 놓인 겹침 창이 같은 순간에 따라오게 함
    video_moved = pyqtSignal()

    def __init__(
        self,
        video: QWidget,
        get_frame_size: Callable[[], tuple[int, int] | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._video = video
        self._get_frame_size = get_frame_size
        self._background: str = DEFAULT_PREVIEW_BACKGROUND
        self._outline: bool = False
        self._palette: ThemePalette = DARK_PALETTE
        video.setParent(self)
        video.setGeometry(self.rect())

    def apply_stage_style(self, palette: ThemePalette, background: str, outline: bool) -> None:
        """팔레트, 배경 색, 점선 표시를 한 번에 맞추고 다시 그림"""
        self._palette = palette
        self._background = background
        self._outline = outline
        self.update_geometry()

    def background_color(self) -> str:
        """지금 칠하는 배경 색 값"""
        return stage_color(self._palette, self._background)

    def video_geometry(self) -> QRect:
        """영상 위젯이 놓이는 영상비 사각형, 영상 크기를 모르면 무대 전체"""
        size = self._get_frame_size()
        if size is None or size[0] <= 0 or size[1] <= 0:
            return self.rect()
        return content_rect((self.width(), self.height()), size)

    def update_geometry(self) -> None:
        """영상비 사각형으로 영상 위젯을 다시 놓고, 자리가 바뀌었으면 함께 알림"""
        rect = self.video_geometry()
        if self._video.geometry() != rect:
            self._video.setGeometry(rect)
            self.video_moved.emit()
        self.update()

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        """무대 크기가 바뀌면 영상 위젯을 영상비로 다시 놓음"""
        super().resizeEvent(event)
        self.update_geometry()

    def paintEvent(self, event: QPaintEvent | None) -> None:
        """영상 바깥을 배경 색으로 칠하고 켜져 있으면 영상 바깥 경계에 점선을 그림"""
        del event
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor(self.background_color()))
            if not self._outline:
                return
            painter.setPen(make_outline_pen(self._palette.media_stage_outline))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(stage_outline_rect(QRectF(self.video_geometry())))
        finally:
            painter.end()
