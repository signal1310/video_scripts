"""미리 보기 창과 화질 비교 창이 함께 쓰는 하단 재생 바 모듈"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QShowEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QWidget,
)

from src.core.settings.shortcuts import format_tooltip_with_shortcut
from src.core.settings.ui_state import PREVIEW_VOLUME_MAX, VALID_PREVIEW_PLAYBACK_RATES
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.widgets.shortcut_guide_popover import ShortcutGuideButton
from src.gui.widgets.track_slider import TrackSlider

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
_DIVIDER_WIDTH: int = 1
_DIVIDER_HEIGHT: int = 18
_PLAY_TEXT: str = "재생"
_PAUSE_TEXT: str = "일시중지"
_MUTE_TEXT: str = "음소거"
_UNMUTE_TEXT: str = "음소거 해제"
_SETTINGS_TEXT: str = "미리 보기 설정"
_COMPARE_TEXT: str = "화질 비교"
_FRAME_BACK_TEXT: str = "이전 프레임"
_FRAME_FORWARD_TEXT: str = "다음 프레임"
_FULLSCREEN_TEXT: str = "전체화면 전환"
_FULLSCREEN_EXIT_TEXT: str = "전체화면 해제"
_PIN_TEXT: str = "항상 위에 표시"
_PIN_EXIT_TEXT: str = "항상 위에 표시 해제"
_COMPARE_TIP: str = "현재 재생 시점으로 화질 비교 창으로 전환합니다."
_COMPARE_EXIT_TIP: str = "일반 미리 보기로 돌아갑니다."
_CROP_TEXT: str = "여백 미리 보기"
_CROP_EXIT_TEXT: str = "여백 미리 보기 해제"
_CROP_TIP: str = "인코딩할 때 잘라낼 검은 여백을 영상 위에 표시합니다."
_CROP_EXIT_TIP: str = "여백 표시를 끕니다."
_CROP_ICON: str = "crop"
_CROP_OFF_ICON: str = "crop-off"
_FRAME_BACK_TIP: str = "한 프레임 뒤로 이동합니다."
_FRAME_FORWARD_TIP: str = "한 프레임 앞으로 이동합니다."
_FULLSCREEN_TIP: str = "창을 전체화면으로 전환합니다."
_FULLSCREEN_EXIT_TIP: str = "전체화면을 해제하고 이전 창 크기로 되돌립니다."
_PIN_TIP: str = "영상 창을 앱의 다른 창보다 위에 계속 띄워 둡니다."
_PIN_EXIT_TIP: str = "영상 창이 앱의 다른 창 뒤로 갈 수 있게 항상 위에 표시를 해제합니다."
_COMPARE_ICON: str = "compare"
_RATE_TEXT: str = "재생 배속"
_RATE_ICON: str = "gauge"
_RATE_TIP_TEMPLATE: str = "재생 배속을 바꿉니다. 현재 {label}입니다."
_RATE_LABEL_TEMPLATE: str = "{rate:g}배"
_SETTINGS_TIP: str = "구간 이동 간격과 정보 표시를 설정합니다."
_PLAY_ICON: str = "play"
_PAUSE_ICON: str = "pause"
_VOLUME_ICON: str = "volume-2"
_VOLUME_MUTED_ICON: str = "volume-x"
_SETTINGS_ICON: str = "sliders"
_PIN_ICON: str = "pin"
_PIN_OFF_ICON: str = "pin-off"
GUIDE_TITLE: str = "미리 보기 창 단축키"
# - 미리 보기에서 쓸 수 있는 명령을 안내 표 순서대로 나열
PREVIEW_GUIDE_ACTIONS: tuple[str, ...] = (
    "preview.toggle_playback",
    "preview.seek_backward",
    "preview.seek_forward",
    "preview.seek_backward_large",
    "preview.seek_forward_large",
    "preview.frame_backward",
    "preview.frame_forward",
    "preview.rate_down",
    "preview.rate_up",
    "preview.rate_reset",
    "preview.toggle_mute",
    "preview.toggle_fullscreen",
    "preview.toggle_info",
    "preview.toggle_crop",
    "preview.previous",
    "preview.next",
    "classify.compare_selected",
)



def format_playback_time(ms: int, *, show_ms: bool = False) -> str:
    """밀리초를 분:초 표기로 바꾸고, show_ms면 밀리초를 붙이고 한 시간 이상이면 시 단위를 앞에 붙임"""
    total_ms = max(ms, 0)
    total_seconds, millis = divmod(total_ms, _MS_PER_SECOND)
    hours, rest = divmod(total_seconds, _SECONDS_PER_HOUR)
    minutes, seconds = divmod(rest, _SECONDS_PER_MINUTE)
    body = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
    return f"{body}.{millis:03d}" if show_ms else body


class PlaybackControls(QWidget):
    """재생과 일시중지, 재생 위치와 한 프레임 이동, 음소거, 볼륨, 전체화면, 핀 요청을 신호로 내보내는 하단 재생 바

    방향키는 창이 받아야 하므로 자식 위젯은 포커스를 받지 않음
    """

    toggle_requested = pyqtSignal()
    seek_requested = pyqtSignal(int)
    frame_step_requested = pyqtSignal(int)
    volume_changed = pyqtSignal(int)
    mute_toggled = pyqtSignal(bool)
    settings_requested = pyqtSignal()
    compare_requested = pyqtSignal()
    rate_changed = pyqtSignal(float)
    fullscreen_requested = pyqtSignal()
    always_on_top_toggled = pyqtSignal(bool)
    crop_preview_toggled = pyqtSignal(bool)

    def __init__(
        self,
        parent: QWidget | None = None,
        get_shortcuts: Callable[[], Mapping[str, str]] | None = None,
        *,
        show_ms: bool = False,
        guide_title: str = GUIDE_TITLE,
        guide_actions: Sequence[str] = PREVIEW_GUIDE_ACTIONS,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PreviewControlBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._show_ms = show_ms
        self._compare_mode = False
        self._get_shortcuts = get_shortcuts
        self._effective_shortcuts: dict[str, str] = {}
        self._duration_ms: int = 0
        self._playing: bool = False
        self._muted: bool = False
        self._volume_percent: int = PREVIEW_VOLUME_MAX
        self._playback_rate: float = VALID_PREVIEW_PLAYBACK_RATES[0]
        self._fullscreen: bool = False
        self._always_on_top: bool = True

        self._btn_toggle = self._icon_button("PreviewToggleButton", "", _PLAY_TEXT)
        self._btn_prev = self._icon_button("PreviewPrevButton", _FRAME_BACK_TIP, _FRAME_BACK_TEXT)
        self._btn_next = self._icon_button(
            "PreviewNextButton", _FRAME_FORWARD_TIP, _FRAME_FORWARD_TEXT
        )
        self._slider_position = TrackSlider(Qt.Orientation.Horizontal, self)
        self._slider_position.setObjectName("PreviewPositionSlider")
        self._slider_position.setToolTip("재생 위치 막대")
        self._slider_position.setRange(0, 0)
        self._slider_position.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._label_time = QLabel(self)
        self._label_time.setObjectName("PreviewTimeLabel")
        self._label_time.setMinimumWidth(_TIME_LABEL_WIDTH)
        self._label_time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._btn_rate = self._icon_button("PreviewRateButton", "", _RATE_TEXT)
        self._btn_rate.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._rate_actions: dict[float, QAction] = {}
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
        self._btn_volume = self._icon_button("PreviewVolumeButton", "", _MUTE_TEXT)
        self._slider_volume = TrackSlider(Qt.Orientation.Horizontal, self)
        self._slider_volume.setObjectName("PreviewVolumeSlider")
        self._slider_volume.setToolTip("볼륨")
        self._slider_volume.setRange(0, PREVIEW_VOLUME_MAX)
        self._slider_volume.setValue(PREVIEW_VOLUME_MAX)
        self._slider_volume.setFixedWidth(_VOLUME_SLIDER_WIDTH)
        self._slider_volume.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_compare = self._icon_button("PreviewCompareButton", _COMPARE_TIP, _COMPARE_TEXT)
        self._btn_compare.setCheckable(True)
        self._btn_crop = self._icon_button("PreviewCropButton", _CROP_TIP, _CROP_TEXT)
        self._btn_crop.setCheckable(True)
        self._btn_crop.hide()
        self._btn_settings = self._icon_button("PreviewSettingsButton", _SETTINGS_TIP, _SETTINGS_TEXT)
        self._btn_pin = self._icon_button("PreviewPinButton", _PIN_EXIT_TIP, _PIN_EXIT_TEXT)
        self._btn_fullscreen = self._icon_button(
            "PreviewFullscreenButton", _FULLSCREEN_TIP, _FULLSCREEN_TEXT
        )
        shortcuts_getter = get_shortcuts if get_shortcuts is not None else (lambda: {})
        self._btn_guide = ShortcutGuideButton(
            self,
            shortcuts_getter,
            guide_actions,
            title=guide_title,
            button_size=_BUTTON_SIZE,
            icon_size=_ICON_SIZE,
        )
        self._btn_guide.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._apply_compare_tip()
        self._update_time_label(0)
        self.refresh_icons()

        self._btn_toggle.clicked.connect(lambda _checked=False: self.toggle_requested.emit())
        self._btn_prev.clicked.connect(lambda _checked=False: self.frame_step_requested.emit(-1))
        self._btn_next.clicked.connect(lambda _checked=False: self.frame_step_requested.emit(1))
        self._btn_volume.clicked.connect(lambda _checked=False: self.toggle_mute())
        self._btn_settings.clicked.connect(lambda _checked=False: self.settings_requested.emit())
        self._btn_compare.clicked.connect(lambda _checked=False: self.compare_requested.emit())
        self._btn_fullscreen.clicked.connect(lambda _checked=False: self.fullscreen_requested.emit())
        self._btn_pin.clicked.connect(lambda _checked=False: self._toggle_pin())
        self._btn_crop.toggled.connect(self._on_crop_button_toggled)
        # - 트랙 클릭과 핸들 끌기 모두 이동 요청으로 전달, 재생 중 자동 갱신은 set_position에서 신호 없이 반영
        self._slider_position.track_value_requested.connect(self.seek_requested)
        self._slider_position.sliderMoved.connect(self.seek_requested)
        self._slider_volume.valueChanged.connect(self._on_volume_slider_changed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(_BAR_MARGIN_H, _BAR_MARGIN_V, _BAR_MARGIN_H, _BAR_MARGIN_V)
        layout.setSpacing(_BAR_SPACING)
        for widget in (self._btn_toggle, self._btn_prev, self._btn_next):
            layout.addWidget(widget)
        layout.addWidget(self._slider_position, 1)
        layout.addWidget(self._label_time)
        layout.addWidget(self._btn_rate)
        layout.addWidget(self._btn_volume)
        layout.addWidget(self._slider_volume)
        layout.addWidget(self._bar_divider())
        layout.addWidget(self._btn_compare)
        layout.addWidget(self._btn_settings)
        layout.addWidget(self._btn_pin)
        layout.addWidget(self._btn_crop)
        layout.addWidget(self._btn_fullscreen)
        layout.addWidget(self._btn_guide)

    def _icon_button(self, name: str, tip: str, accessible_name: str) -> QToolButton:
        """아이콘만 있는 조작 버튼, 포커스와 커서, 크기를 재생 바 기준으로 맞춤"""
        button = QToolButton(self)
        button.setObjectName(name)
        button.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if tip:
            button.setToolTip(tip)
        button.setAccessibleName(accessible_name)
        return button

    @property
    def settings_button(self) -> QToolButton:
        """미리 보기 설정 팝업을 이 버튼 옆에 맞추기 위해 노출하는 설정 버튼"""
        return self._btn_settings

    @property
    def compare_button(self) -> QToolButton:
        """화질 비교 요청을 보내는 아이콘 버튼"""
        return self._btn_compare

    @property
    def crop_button(self) -> QToolButton:
        """여백 미리 보기를 켜고 끄는 아이콘 버튼"""
        return self._btn_crop

    @property
    def guide_button(self) -> ShortcutGuideButton:
        """하단 바 가장 오른쪽 단축키 안내 버튼"""
        return self._btn_guide

    def set_shortcut_tooltips(self, shortcuts: Mapping[str, str]) -> None:
        """버튼 툴팁에 현재 사용자 지정 단축키를 덧붙이고, 이후 상태 갱신에도 유지되게 보관"""
        self._effective_shortcuts = dict(shortcuts)
        self._apply_toggle_text()
        self._apply_frame_tooltips()
        self._apply_volume_text()
        self._apply_rate_tooltip()
        self._apply_fullscreen_text()
        self._apply_compare_tip()
        self._refresh_crop_button()
        self._btn_guide.refresh_entries()

    def set_compare_mode(
        self, enabled: bool, *, guide_title: str, guide_actions: Sequence[str]
    ) -> None:
        """비교 모드에 맞춰 밀리초 표기, 비교 버튼 체크와 툴팁, 단축키 안내를 한 번에 바꿈"""
        self._show_ms = enabled
        self._compare_mode = enabled
        self._btn_compare.setChecked(enabled)
        self._apply_compare_tip()
        self._btn_guide.set_guide_mode(guide_title, guide_actions)
        self._update_time_label(self._slider_position.value())

    def set_fullscreen(self, fullscreen: bool) -> None:
        """전체화면 여부에 맞춰 해제 아이콘과 접근성 이름, 툴팁을 바꿈"""
        self._fullscreen = fullscreen
        self._refresh_fullscreen()

    def set_always_on_top(self, pinned: bool) -> None:
        """핀 상태에 맞춰 아이콘과 접근성 이름, 툴팁을 바꿈"""
        self._always_on_top = pinned
        self._refresh_pin()

    def set_crop_available(self, available: bool) -> None:
        """이 영상이 잘라내기 대상이고 일반 모드일 때만 여백 미리 보기 버튼을 보여 줌

        - 숨길 때는 체크도 함께 풀어 버튼이 켜진 채 사라지지 않게 함
        """
        self._btn_crop.setVisible(available)
        if not available:
            self._btn_crop.setChecked(False)

    def _on_crop_button_toggled(self, checked: bool) -> None:
        """켜고 끈 상태를 아이콘과 이름, 툴팁에 반영하고 창에 상태 변경을 알림"""
        self._refresh_crop_button()
        self.crop_preview_toggled.emit(checked)

    def set_guide_mode(self, title: str, action_ids: Sequence[str]) -> None:
        """단축키 안내의 제목과 표시할 명령 목록을 바꿈, 다음 단계의 단일 창 전환에 대비"""
        self._btn_guide.set_guide_mode(title, action_ids)

    def _shortcut(self, action_id: str) -> str:
        return self._effective_shortcuts.get(action_id, "")

    def _bar_divider(self) -> QFrame:
        divider = QFrame(self)
        divider.setObjectName("PreviewBarDivider")
        divider.setFixedSize(_DIVIDER_WIDTH, _DIVIDER_HEIGHT)
        return divider

    def refresh_icons(self) -> None:
        """테마가 바뀐 뒤 모든 버튼 아이콘과 단축키 툴팁을 현재 팔레트로 다시 입힘"""
        self._refresh_toggle()
        self._refresh_frame_buttons()
        self._refresh_volume_button()
        self._refresh_rate_button()
        self._refresh_settings_button()
        self._refresh_compare_button()
        self._refresh_crop_button()
        self._refresh_fullscreen()
        self._refresh_pin()
        self._btn_guide.refresh_icon()

    def showEvent(self, event: QShowEvent | None) -> None:
        """테마가 바뀌었을 수 있어 표시될 때마다 아이콘을 현재 팔레트로 다시 입힘"""
        self.refresh_icons()
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
        self._apply_rate_tooltip()

    def _apply_rate_tooltip(self) -> None:
        label = _RATE_LABEL_TEMPLATE.format(rate=self._playback_rate)
        down = self._shortcut("preview.rate_down")
        up = self._shortcut("preview.rate_up")
        self._btn_rate.setToolTip(
            format_tooltip_with_shortcut(
                _RATE_TIP_TEMPLATE.format(label=label), f"{down} / {up}" if down and up else ""
            )
        )

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
        self._apply_toggle_text()

    def _apply_toggle_text(self) -> None:
        base = _PAUSE_TEXT if self._playing else _PLAY_TEXT
        self._btn_toggle.setAccessibleName(base)
        self._btn_toggle.setToolTip(
            format_tooltip_with_shortcut(base, self._shortcut("preview.toggle_playback"))
        )

    def _refresh_frame_buttons(self) -> None:
        palette = get_active_palette()
        self._btn_prev.setIcon(get_icon("chevron-left", color=palette.media_fg, size=_ICON_SIZE))
        self._btn_next.setIcon(get_icon("chevron-right", color=palette.media_fg, size=_ICON_SIZE))
        self._apply_frame_tooltips()

    def _apply_frame_tooltips(self) -> None:
        self._btn_prev.setToolTip(
            format_tooltip_with_shortcut(_FRAME_BACK_TIP, self._shortcut("preview.frame_backward"))
        )
        self._btn_next.setToolTip(
            format_tooltip_with_shortcut(
                _FRAME_FORWARD_TIP, self._shortcut("preview.frame_forward")
            )
        )

    def _refresh_fullscreen(self) -> None:
        palette = get_active_palette()
        icon_name = "minimize-2" if self._fullscreen else "maximize-2"
        self._btn_fullscreen.setIcon(get_icon(icon_name, color=palette.media_fg, size=_ICON_SIZE))
        self._apply_fullscreen_text()

    def _apply_fullscreen_text(self) -> None:
        base = _FULLSCREEN_EXIT_TEXT if self._fullscreen else _FULLSCREEN_TEXT
        tip = _FULLSCREEN_EXIT_TIP if self._fullscreen else _FULLSCREEN_TIP
        self._btn_fullscreen.setAccessibleName(base)
        self._btn_fullscreen.setToolTip(
            format_tooltip_with_shortcut(tip, self._shortcut("preview.toggle_fullscreen"))
        )

    def _apply_compare_tip(self) -> None:
        """지금 모드에 맞는 화질 비교 버튼 툴팁과 전환 단축키 표기"""
        tip = _COMPARE_EXIT_TIP if self._compare_mode else _COMPARE_TIP
        self._btn_compare.setToolTip(
            format_tooltip_with_shortcut(
                tip, self._shortcut("classify.compare_selected"), bracket="round"
            )
        )

    def _refresh_pin(self) -> None:
        palette = get_active_palette()
        icon_name = _PIN_ICON if self._always_on_top else _PIN_OFF_ICON
        base = _PIN_EXIT_TEXT if self._always_on_top else _PIN_TEXT
        tip = _PIN_EXIT_TIP if self._always_on_top else _PIN_TIP
        self._btn_pin.setIcon(get_icon(icon_name, color=palette.media_fg, size=_ICON_SIZE))
        self._btn_pin.setAccessibleName(base)
        self._btn_pin.setToolTip(tip)

    def _toggle_pin(self) -> None:
        """핀 상태를 뒤집어 표시를 맞추고 바뀐 상태를 알림"""
        self.set_always_on_top(not self._always_on_top)
        self.always_on_top_toggled.emit(self._always_on_top)

    def _refresh_volume_button(self) -> None:
        palette = get_active_palette()
        silent = self._muted or self._volume_percent == 0
        icon_name = _VOLUME_MUTED_ICON if silent else _VOLUME_ICON
        self._btn_volume.setIcon(get_icon(icon_name, color=palette.media_fg, size=_ICON_SIZE))
        self._apply_volume_text()

    def _apply_volume_text(self) -> None:
        base = _UNMUTE_TEXT if self._muted else _MUTE_TEXT
        self._btn_volume.setAccessibleName(base)
        self._btn_volume.setToolTip(
            format_tooltip_with_shortcut(base, self._shortcut("preview.toggle_mute"))
        )

    def _refresh_settings_button(self) -> None:
        palette = get_active_palette()
        self._btn_settings.setIcon(get_icon(_SETTINGS_ICON, color=palette.media_fg, size=_ICON_SIZE))

    def _refresh_compare_button(self) -> None:
        palette = get_active_palette()
        self._btn_compare.setIcon(get_icon(_COMPARE_ICON, color=palette.media_fg, size=_ICON_SIZE))

    def _refresh_crop_button(self) -> None:
        """여백 미리 보기 상태에 맞춰 아이콘, 접근성 이름, 툴팁을 바꿈"""
        palette = get_active_palette()
        checked = self._btn_crop.isChecked()
        icon_name = _CROP_OFF_ICON if checked else _CROP_ICON
        base = _CROP_EXIT_TEXT if checked else _CROP_TEXT
        tip = _CROP_EXIT_TIP if checked else _CROP_TIP
        self._btn_crop.setIcon(get_icon(icon_name, color=palette.media_fg, size=_ICON_SIZE))
        self._btn_crop.setAccessibleName(base)
        self._btn_crop.setToolTip(
            format_tooltip_with_shortcut(tip, self._shortcut("preview.toggle_crop"))
        )

    def _update_time_label(self, position_ms: int) -> None:
        self._label_time.setText(
            f"{format_playback_time(position_ms, show_ms=self._show_ms)}"
            f" / {format_playback_time(self._duration_ms, show_ms=self._show_ms)}"
        )
