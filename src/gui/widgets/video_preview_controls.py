"""내장 미리 보기 창의 재생 버튼, 재생 위치 막대, 음소거 버튼, 볼륨 조절 바 모듈"""

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QMouseEvent, QShowEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QToolButton,
    QWidget,
)

from src.core.settings.ui_state import PREVIEW_VOLUME_MAX, VALID_PREVIEW_PLAYBACK_RATES
from src.gui.styles.icons import get_active_palette, get_icon

_MS_PER_SECOND: int = 1000
_SECONDS_PER_MINUTE: int = 60
_SECONDS_PER_HOUR: int = 3600
_BUTTON_SIZE: int = 26
_ICON_SIZE: int = 16
_VOLUME_SLIDER_WIDTH: int = 88
_TIME_LABEL_WIDTH: int = 104
_BAR_MARGIN_H: int = 12
_BAR_MARGIN_V: int = 4
_BAR_SPACING: int = 8
_PLAY_TEXT: str = "재생"
_PAUSE_TEXT: str = "일시중지"
_MUTE_TEXT: str = "음소거"
_UNMUTE_TEXT: str = "음소거 해제"
_SETTINGS_TEXT: str = "미리 보기 설정"
_RATE_TEXT: str = "재생 배속"
_RATE_ICON: str = "gauge"
_RATE_TIP_TEMPLATE: str = "재생 배속을 바꿉니다. 현재 {label}입니다."
_RATE_LABEL_TEMPLATE: str = "{rate:g}배"
_SETTINGS_TIP: str = "미리 보기 방식, 구간 이동 간격, 정보 표시를 설정합니다."
_PLAY_ICON: str = "play"
_PAUSE_ICON: str = "pause"
_VOLUME_ICON: str = "volume-2"
_VOLUME_MUTED_ICON: str = "volume-x"
_SETTINGS_ICON: str = "sliders"


def format_playback_time(ms: int) -> str:
    """밀리초를 분:초 표기로 바꾸고 한 시간 이상이면 시 단위를 앞에 붙임"""
    total_seconds = max(ms, 0) // _MS_PER_SECOND
    hours, rest = divmod(total_seconds, _SECONDS_PER_HOUR)
    minutes, seconds = divmod(rest, _SECONDS_PER_MINUTE)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


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


class PlaybackControls(QWidget):
    """재생과 일시중지, 재생 위치 이동, 음소거 전환, 볼륨 변경 요청을 신호로 내보내는 조작 바

    방향키는 창이 받아야 하므로 자식 위젯은 포커스를 받지 않음
    """

    toggle_requested = pyqtSignal()
    seek_requested = pyqtSignal(int)
    volume_changed = pyqtSignal(int)
    mute_toggled = pyqtSignal(bool)
    settings_requested = pyqtSignal()
    rate_changed = pyqtSignal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PreviewControlBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._duration_ms: int = 0
        self._playing: bool = False
        self._muted: bool = False
        self._volume_percent: int = PREVIEW_VOLUME_MAX

        self._btn_toggle = QToolButton(self)
        self._btn_toggle.setObjectName("PreviewToggleButton")
        self._btn_toggle.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._btn_toggle.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._slider_position = TrackSlider(Qt.Orientation.Horizontal, self)
        self._slider_position.setObjectName("PreviewPositionSlider")
        self._slider_position.setToolTip("재생 위치 막대")
        self._slider_position.setRange(0, 0)
        self._label_time = QLabel(self)
        self._label_time.setObjectName("PreviewTimeLabel")
        self._label_time.setMinimumWidth(_TIME_LABEL_WIDTH)
        self._label_time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._btn_rate = QToolButton(self)
        self._btn_rate.setObjectName("PreviewRateButton")
        self._btn_rate.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._btn_rate.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._btn_rate.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_rate.setAccessibleName(_RATE_TEXT)
        self._btn_rate.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._rate_actions: dict[float, QAction] = {}
        self._playback_rate: float = VALID_PREVIEW_PLAYBACK_RATES[0]
        rate_menu = QMenu(self._btn_rate)
        rate_group = QActionGroup(self)
        rate_group.setExclusive(True)
        for rate in VALID_PREVIEW_PLAYBACK_RATES:
            action = QAction(_RATE_LABEL_TEMPLATE.format(rate=rate), rate_menu)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, value=rate: self._on_rate_triggered(value))
            rate_group.addAction(action)
            rate_menu.addAction(action)
            self._rate_actions[rate] = action
        self._btn_rate.setMenu(rate_menu)
        self._btn_volume = QToolButton(self)
        self._btn_volume.setObjectName("PreviewVolumeButton")
        self._btn_volume.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._btn_volume.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._btn_volume.setCursor(Qt.CursorShape.PointingHandCursor)
        self._slider_volume = TrackSlider(Qt.Orientation.Horizontal, self)
        self._slider_volume.setObjectName("PreviewVolumeSlider")
        self._slider_volume.setToolTip("볼륨")
        self._slider_volume.setRange(0, PREVIEW_VOLUME_MAX)
        self._slider_volume.setValue(PREVIEW_VOLUME_MAX)
        self._slider_volume.setFixedWidth(_VOLUME_SLIDER_WIDTH)
        self._btn_settings = QToolButton(self)
        self._btn_settings.setObjectName("PreviewSettingsButton")
        self._btn_settings.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._btn_settings.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_settings.setToolTip(_SETTINGS_TIP)
        self._btn_settings.setAccessibleName(_SETTINGS_TEXT)
        self._update_time_label(0)
        self._refresh_toggle()
        self._refresh_volume_button()
        self._refresh_settings_button()
        self._refresh_rate_button()

        for widget in (
            self._btn_toggle,
            self._btn_volume,
            self._btn_settings,
            self._slider_position,
            self._slider_volume,
            self._btn_rate,
        ):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._btn_toggle.clicked.connect(lambda _checked=False: self.toggle_requested.emit())
        self._btn_volume.clicked.connect(lambda _checked=False: self.toggle_mute())
        self._btn_settings.clicked.connect(lambda _checked=False: self.settings_requested.emit())
        # - 트랙 클릭과 핸들 끌기 모두 이동 요청으로 전달, 재생 중 자동 갱신은 set_position에서 신호 없이 반영
        self._slider_position.track_value_requested.connect(self.seek_requested)
        self._slider_position.sliderMoved.connect(self.seek_requested)
        self._slider_volume.valueChanged.connect(self._on_volume_slider_changed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(_BAR_MARGIN_H, _BAR_MARGIN_V, _BAR_MARGIN_H, _BAR_MARGIN_V)
        layout.setSpacing(_BAR_SPACING)
        layout.addWidget(self._btn_toggle)
        layout.addWidget(self._slider_position, 1)
        layout.addWidget(self._label_time)
        layout.addWidget(self._btn_rate)
        layout.addWidget(self._btn_volume)
        layout.addWidget(self._slider_volume)
        layout.addWidget(self._btn_settings)

    @property
    def settings_button(self) -> QToolButton:
        """미리 보기 설정 팝업을 이 버튼 옆에 맞추기 위해 노출하는 설정 버튼"""
        return self._btn_settings

    def showEvent(self, event: QShowEvent | None) -> None:
        """테마가 바뀌었을 수 있어 표시될 때마다 아이콘을 현재 팔레트로 다시 입힘"""
        self._refresh_toggle()
        self._refresh_volume_button()
        self._refresh_settings_button()
        self._refresh_rate_button()
        super().showEvent(event)

    def set_playing(self, playing: bool) -> None:
        """재생 중이면 일시중지 아이콘으로, 멈춘 상태면 재생 아이콘으로 표시"""
        self._playing = playing
        self._refresh_toggle()

    def set_duration(self, duration_ms: int) -> None:
        """재생 위치 막대의 최대값을 영상 길이에 맞춤"""
        self._duration_ms = max(duration_ms, 0)
        self._slider_position.setRange(0, self._duration_ms)
        self._update_time_label(self._slider_position.value())

    def set_position(self, position_ms: int) -> None:
        """재생 위치를 막대와 시간 표기에 반영, 사용자가 막대를 끄는 중에는 덮어쓰지 않음"""
        if not self._slider_position.isSliderDown():
            self._slider_position.setValue(position_ms)
        self._update_time_label(position_ms)

    def set_audio_state(self, volume_percent: int, muted: bool) -> None:
        """저장된 음량과 음소거 상태를 표시에 반영, 복원 과정이므로 변경 신호는 내보내지 않음"""
        self._muted = muted
        self._volume_percent = volume_percent
        self._slider_volume.blockSignals(True)
        self._slider_volume.setValue(volume_percent)
        self._slider_volume.blockSignals(False)
        self._refresh_volume_button()

    def set_playback_rate(self, rate: float) -> None:
        """저장된 배속을 메뉴 선택 상태에 반영, 체크 설정은 triggered를 내보내지 않으므로 변경 신호도 없음"""
        self._playback_rate = rate
        action = self._rate_actions.get(rate)
        if action is not None:
            action.setChecked(True)
        self._refresh_rate_button()

    def _on_rate_triggered(self, rate: float) -> None:
        self._playback_rate = rate
        self._refresh_rate_button()
        self.rate_changed.emit(rate)

    def _refresh_rate_button(self) -> None:
        palette = get_active_palette()
        self._btn_rate.setIcon(get_icon(_RATE_ICON, color=palette.media_fg, size=_ICON_SIZE))
        label = _RATE_LABEL_TEMPLATE.format(rate=self._playback_rate)
        self._btn_rate.setToolTip(_RATE_TIP_TEMPLATE.format(label=label))

    def audio_state(self) -> tuple[int, bool]:
        """현재 음량 퍼센트와 음소거 여부"""
        return self._volume_percent, self._muted

    def toggle_mute(self) -> None:
        """음소거와 이전 음량 상태를 번갈아 전환하고 바뀐 상태를 알림, 음량 막대 값은 그대로 둠"""
        self._muted = not self._muted
        self._refresh_volume_button()
        self.mute_toggled.emit(self._muted)

    def _on_volume_slider_changed(self, value: int) -> None:
        self._volume_percent = value
        self._refresh_volume_button()
        self.volume_changed.emit(value)

    def _refresh_toggle(self) -> None:
        palette = get_active_palette()
        icon_name = _PAUSE_ICON if self._playing else _PLAY_ICON
        self._btn_toggle.setIcon(get_icon(icon_name, color=palette.media_fg, size=_ICON_SIZE))
        self._btn_toggle.setToolTip(_PAUSE_TEXT if self._playing else _PLAY_TEXT)

    def _refresh_volume_button(self) -> None:
        palette = get_active_palette()
        silent = self._muted or self._volume_percent == 0
        icon_name = _VOLUME_MUTED_ICON if silent else _VOLUME_ICON
        self._btn_volume.setIcon(get_icon(icon_name, color=palette.media_fg, size=_ICON_SIZE))
        self._btn_volume.setToolTip(_UNMUTE_TEXT if self._muted else _MUTE_TEXT)

    def _refresh_settings_button(self) -> None:
        palette = get_active_palette()
        self._btn_settings.setIcon(get_icon(_SETTINGS_ICON, color=palette.media_fg, size=_ICON_SIZE))

    def _update_time_label(self, position_ms: int) -> None:
        self._label_time.setText(
            f"{format_playback_time(position_ms)} / {format_playback_time(self._duration_ms)}"
        )
