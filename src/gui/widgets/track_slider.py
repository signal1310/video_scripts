"""트랙을 누른 지점으로 바로 옮기는 재생 위치, 볼륨 공용 슬라이더"""

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QSlider, QStyle, QStyleOptionSlider, QWidget


class TrackSlider(QSlider):
    """트랙을 누르면 플랫폼 기본의 페이지 단위 이동 대신 누른 지점으로 바로 옮기고, 그 상태에서 끌기를 이어 가는 슬라이더

    기본 스타일의 트랙 클릭 동작은 OS와 스타일 시트에 따라 달라서 누름 위치를 직접 계산함
    """

    track_value_requested = pyqtSignal(int)

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self._dragging_track: bool = False

    def value_at(self, x: int) -> int:
        """트랙 가로 좌표에 대응하는 슬라이더 값, 핸들 중심이 누른 지점에 오도록 보정"""
        groove = self._sub_control_rect(QStyle.SubControl.SC_SliderGroove)
        handle = self._sub_control_rect(QStyle.SubControl.SC_SliderHandle)
        span = groove.width() - handle.width()
        offset = x - groove.x() - handle.width() // 2
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), offset, span)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        """트랙 누름은 누른 지점으로 이동하고 끌기를 시작, 핸들 누름은 기본 동작에 맡김"""
        if event is None:
            return
        if event.button() == Qt.MouseButton.LeftButton and not self._is_on_handle(
            event.position().toPoint()
        ):
            self._dragging_track = True
            self.setSliderDown(True)
            self._apply_track_value(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        """트랙에서 시작한 끌기는 포인터 위치를 따라 값을 갱신"""
        if event is None:
            return
        if self._dragging_track:
            self._apply_track_value(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        """트랙에서 시작한 끌기를 마지막 위치로 확정하고 끝냄"""
        if event is None:
            return
        if self._dragging_track:
            self._apply_track_value(event.position().x())
            self._dragging_track = False
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _apply_track_value(self, x: float) -> None:
        value = self.value_at(int(x))
        self.setValue(value)
        self.track_value_requested.emit(value)

    def _is_on_handle(self, point: QPoint) -> bool:
        return self._sub_control_rect(QStyle.SubControl.SC_SliderHandle).contains(point)

    def _sub_control_rect(self, sub_control: QStyle.SubControl) -> QRect:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        if style is None:
            return QRect()
        return style.subControlRect(QStyle.ComplexControl.CC_Slider, option, sub_control, self)
