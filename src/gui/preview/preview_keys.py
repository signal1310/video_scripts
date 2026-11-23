"""미리 보기 창의 키 입력 차단 판정과 1프레임 이동 계산, 단축키 실행 결과 문구

- 열린 팝업, 모달, 텍스트 입력이 키를 직접 받아야 하는지 판정
- 영상 FPS에서 한 프레임 이동 밀리초를 구하고, 메타데이터 FPS 값도 안전하게 해석
- 단축키로 실행한 명령의 결과 문구는 이 모듈이 만들고 창은 표시만 함
- 키 사건을 닫기와 사용자 지정 명령으로 나눠 창 동작 묶음에 넘기는 라우터를 둠
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from src.core.analysis.frame_compare import frame_step_seconds
from src.core.settings.shortcuts import normalize_shortcut
from src.core.settings.ui_state import (
    DEFAULT_PREVIEW_PLAYBACK_RATE,
    PREVIEW_LARGE_SEEK_MULTIPLIER,
    shift_playback_rate,
)
from src.gui.constants import (
    FRAME_BACKWARD_NOTICE as FRAME_BACKWARD_NOTICE,
)
from src.gui.constants import (
    FRAME_FORWARD_NOTICE as FRAME_FORWARD_NOTICE,
)
from src.gui.constants import (
    MUTE_NOTICE as MUTE_NOTICE,
)
from src.gui.constants import (
    PAUSE_NOTICE as PAUSE_NOTICE,
)
from src.gui.constants import (
    PLAY_NOTICE as PLAY_NOTICE,
)
from src.gui.constants import (
    UNMUTE_NOTICE as UNMUTE_NOTICE,
)
from src.gui.constants import (
    rate_notice as rate_notice,
)
from src.gui.constants import (
    seek_notice as seek_notice,
)
from src.gui.widgets.floating_popup import FloatingPopupFrame

DEFAULT_FRAME_RATE: float = 30.0
ACTION_SEEK_BACKWARD: str = "preview.seek_backward"
ACTION_SEEK_FORWARD: str = "preview.seek_forward"
ACTION_SEEK_BACKWARD_LARGE: str = "preview.seek_backward_large"
ACTION_SEEK_FORWARD_LARGE: str = "preview.seek_forward_large"
ACTION_RATE_DOWN: str = "preview.rate_down"
ACTION_RATE_UP: str = "preview.rate_up"
ACTION_RATE_RESET: str = "preview.rate_reset"
ACTION_PREVIOUS: str = "preview.previous"
ACTION_NEXT: str = "preview.next"
ACTION_TOGGLE_PLAYBACK: str = "preview.toggle_playback"
ACTION_TOGGLE_MUTE: str = "preview.toggle_mute"
ACTION_TOGGLE_FULLSCREEN: str = "preview.toggle_fullscreen"
ACTION_TOGGLE_INFO: str = "preview.toggle_info"
ACTION_FRAME_BACKWARD: str = "preview.frame_backward"
ACTION_FRAME_FORWARD: str = "preview.frame_forward"
COMPARE_ACTION_ID: str = "classify.compare_selected"
FEEDBACK_MS: int = 1200
FULLSCREEN_NOTICE: str = "전체화면"
WINDOW_MODE_NOTICE: str = "창 모드"
PIN_NOTICE: str = "항상 위에 표시"
PIN_OFF_NOTICE: str = "항상 위에 표시 해제"
_MS_PER_SECOND: int = 1000
_TEXT_INPUT_TYPES: tuple[type[QWidget], ...] = (
    QLineEdit,
    QAbstractSpinBox,
    QTextEdit,
    QPlainTextEdit,
)


def blocks_preview_keys() -> bool:
    """텍스트 입력, 열린 팝업, 설정 팝업, 모달 대화상자가 키를 직접 받아야 하면 참"""
    if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
        return True
    if isinstance(QApplication.activeWindow(), FloatingPopupFrame):
        return True
    return isinstance(QApplication.focusWidget(), _TEXT_INPUT_TYPES)


def clamp_seek_position(current_ms: int, delta_ms: int, duration_ms: int) -> int:
    """현재 위치에서 delta_ms만큼 옮긴 위치를 영상 범위 안으로 제한, 길이를 모르면 현재 위치 유지"""
    if duration_ms <= 0:
        return current_ms
    return min(max(current_ms + delta_ms, 0), duration_ms)


def is_close_key(key: int, modifiers: Qt.KeyboardModifier) -> bool:
    """Space, Esc 가운데 창을 닫는 키인지 판정, Shift+Space는 화질 비교 전환에 넘김"""
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        return False
    return key in (Qt.Key.Key_Space, Qt.Key.Key_Escape)


def frame_step_ms(fps: float) -> int:
    """한 프레임 이동 밀리초, FPS를 모르면 30fps 기준"""
    return round(frame_step_seconds(fps) * _MS_PER_SECOND)


def coerce_frame_rate(value: object) -> float:
    """재생기 메타데이터 값에서 양수 FPS를 꺼내고, 없거나 잘못된 값이면 기본 30fps"""
    if isinstance(value, (int, float)):
        fps = float(value)
    elif isinstance(value, str):
        try:
            fps = float(value)
        except ValueError:
            return DEFAULT_FRAME_RATE
    else:
        return DEFAULT_FRAME_RATE
    return fps if fps > 0 else DEFAULT_FRAME_RATE


def resolve_frame_rate(info_fps: float, metadata_value: object) -> float:
    """표 FPS가 양수면 그 값을, 아니면 재생기 메타데이터, 그것도 없으면 30fps"""
    if info_fps > 0:
        return info_fps
    return coerce_frame_rate(metadata_value)


def pin_notice(pinned: bool) -> str:
    """핀 상태에 맞는 문구"""
    return PIN_NOTICE if pinned else PIN_OFF_NOTICE


def preview_action_for_key(shortcuts: Mapping[str, str], pressed: str) -> str | None:
    """눌린 키 표기와 같은 단축키를 가진 미리 보기 명령 식별자, 없으면 None"""
    normalized = normalize_shortcut(pressed)
    if not normalized:
        return None
    for action_id, key in shortcuts.items():
        if key and action_id.startswith("preview.") and normalize_shortcut(key) == normalized:
            return action_id
    return None


def install_key_filter(window: QWidget) -> bool:
    """미리 보기 창을 앱 이벤트 필터로 등록하고, 앱이 없으면 거짓"""
    app = QApplication.instance()
    if app is None:
        return False
    app.installEventFilter(window)
    return True


def remove_key_filter(window: QWidget) -> None:
    """미리 보기 창을 앱 이벤트 필터에서 해제"""
    app = QApplication.instance()
    if app is not None:
        app.removeEventFilter(window)


def playback_notice(playing: bool) -> str:
    """재생과 일시중지 가운데 지금 상태의 문구"""
    return PLAY_NOTICE if playing else PAUSE_NOTICE


def frame_notice(direction: int) -> str:
    """한 프레임 이동 방향의 문구"""
    return FRAME_FORWARD_NOTICE if direction > 0 else FRAME_BACKWARD_NOTICE


def mute_notice(muted: bool) -> str:
    """음소거 상태에 맞는 문구"""
    return MUTE_NOTICE if muted else UNMUTE_NOTICE


def fullscreen_notice(fullscreen: bool) -> str:
    """전체화면과 창 모드 가운데 지금 상태의 문구"""
    return FULLSCREEN_NOTICE if fullscreen else WINDOW_MODE_NOTICE


def reached_end(position_ms: int, duration_ms: int) -> bool:
    """재생 위치가 영상 길이에 도달했는지, 길이를 모르면 거짓"""
    return duration_ms > 0 and position_ms >= duration_ms


@dataclass(frozen=True, slots=True)
class PreviewPlaybackPorts:
    """미리 보기 재생 동작이 쓰는 재생기 접근자와 결과 알림"""

    position_ms: Callable[[], int]
    duration_ms: Callable[[], int]
    is_playing: Callable[[], bool]
    frame_rate: Callable[[], float]
    seek: Callable[[int], None]
    pause: Callable[[], None]
    play: Callable[[], None]
    set_rate: Callable[[float], None]
    notify: Callable[[str], None]


class PreviewPlaybackActions:
    """미리 보기 재생 명령을 재생기 접근자로 실행하고 결과 문구를 알림

    - 조작 바와 단축키가 같은 동작을 써서 구간 이동, 프레임 이동, 배속, 재생 문구가 한 곳에서 만들어짐
    """

    def __init__(self, ports: PreviewPlaybackPorts) -> None:
        self._ports = ports

    def seek_by(self, delta_ms: int) -> None:
        """현재 위치에서 delta_ms만큼 이동하고 실제 이동 폭을 알림"""
        target = clamp_seek_position(
            self._ports.position_ms(), delta_ms, self._ports.duration_ms()
        )
        self._ports.seek(target)
        self._ports.notify(seek_notice(delta_ms))

    def step_frame(self, direction: int) -> None:
        """재생을 멈추고 영상 FPS 기준 한 프레임만 앞뒤로 옮김"""
        if self._ports.is_playing():
            self._ports.pause()
        target = clamp_seek_position(
            self._ports.position_ms(),
            frame_step_ms(self._ports.frame_rate()) * direction,
            self._ports.duration_ms(),
        )
        self._ports.seek(target)
        self._ports.notify(frame_notice(direction))

    def set_rate(self, rate: float) -> None:
        """재생 배속을 바꾸고 바뀐 배속을 알림"""
        self._ports.set_rate(rate)
        self._ports.notify(rate_notice(rate))

    def toggle_playback(self) -> None:
        """재생과 일시중지를 전환하고 바뀐 상태를 알림, 끝까지 본 상태면 처음부터 재생"""
        if self._ports.is_playing():
            self._ports.pause()
            self._ports.notify(PAUSE_NOTICE)
            return
        if reached_end(self._ports.position_ms(), self._ports.duration_ms()):
            self._ports.seek(0)
        self._ports.play()
        self._ports.notify(PLAY_NOTICE)


@dataclass(frozen=True, slots=True)
class PreviewActionHandlers:
    """미리 보기 명령 하나가 부를 재생기와 창 동작 묶음"""

    seek_by: Callable[[int], None]
    step_frame: Callable[[int], None]
    toggle_playback: Callable[[], None]
    toggle_mute: Callable[[], None]
    set_rate: Callable[[float], None]
    toggle_fullscreen: Callable[[], None]
    cycle_info: Callable[[], None]
    request_step: Callable[[int], None]
    rate: Callable[[], float]
    hide: Callable[[], None]
    toggle_compare: Callable[[], None]


class PreviewKeyRouter:
    """미리 보기 창 키 입력을 닫기와 사용자 지정 명령으로 나눠 창 동작에 넘김

    - 창을 표시하는 동안만 앱 이벤트 필터가 키를 받고, 텍스트 입력과 팝업, 모달은 가로채지 않음
    - 닫기와 앱 명령 전달은 창이 활성일 때만 하고, 동작은 비활성에서도 실행
    """

    def __init__(
        self,
        *,
        get_shortcuts: Callable[[], Mapping[str, str]],
        forward_key: Callable[[str], bool],
        get_actions: Callable[[], PreviewActionHandlers],
        is_visible: Callable[[], bool],
        is_active: Callable[[], bool],
        seek_seconds: Callable[[], int],
    ) -> None:
        self._get_shortcuts = get_shortcuts
        self._forward_key = forward_key
        self._get_actions = get_actions
        self._is_visible = is_visible
        self._is_active = is_active
        self._seek_seconds = seek_seconds

    def handle_key(self, event: QKeyEvent) -> bool:
        """창의 keyPressEvent가 받은 키를 처리했으면 참"""
        if is_close_key(event.key(), event.modifiers()):
            self._get_actions().hide()
            return True
        if self._compare_action(event):
            return True
        action_id = preview_action_for_key(
            self._get_shortcuts(), QKeySequence(event.keyCombination()).toString()
        )
        if action_id is None:
            return False
        self._run(action_id)
        return True

    def filter_event(self, event: QEvent | None) -> bool:
        """앱 키 사건을 처리했으면 참, 텍스트 입력과 팝업, 숨김 상태는 넘김"""
        if event is None or event.type() != QEvent.Type.KeyPress or not isinstance(event, QKeyEvent):
            return False
        if not self._is_visible() or blocks_preview_keys():
            return False
        key = event.key()
        if self._is_active() and is_close_key(key, event.modifiers()):
            self._get_actions().hide()
            return True
        if self._compare_action(event):
            return True
        action_id = preview_action_for_key(
            self._get_shortcuts(), QKeySequence(event.keyCombination()).toString()
        )
        if action_id is not None:
            self._run(action_id)
            return True
        # - 활성 창일 때만 앱 명령으로 넘김, 비활성이면 활성 창이 이미 그 단축키를 처리함
        return self._is_active() and self._forward_key(
            QKeySequence(event.keyCombination()).toString()
        )

    def _compare_action(self, event: QKeyEvent) -> bool:
        """화질 비교 전환 단축키면 같은 창의 모드 전환을 요청"""
        configured = self._get_shortcuts().get(COMPARE_ACTION_ID, "")
        if not configured:
            return False
        pressed = normalize_shortcut(QKeySequence(event.keyCombination()).toString())
        if not pressed or pressed != normalize_shortcut(configured):
            return False
        self._get_actions().toggle_compare()
        return True

    def _run(self, action_id: str) -> None:
        """명령 식별자에 맞는 창 동작을 실행"""
        actions = self._get_actions()
        if action_id in (
            ACTION_SEEK_BACKWARD,
            ACTION_SEEK_FORWARD,
            ACTION_SEEK_BACKWARD_LARGE,
            ACTION_SEEK_FORWARD_LARGE,
        ):
            large = action_id in (ACTION_SEEK_BACKWARD_LARGE, ACTION_SEEK_FORWARD_LARGE)
            direction = -1 if action_id in (ACTION_SEEK_BACKWARD, ACTION_SEEK_BACKWARD_LARGE) else 1
            factor = PREVIEW_LARGE_SEEK_MULTIPLIER if large else 1
            actions.seek_by(direction * self._seek_seconds() * _MS_PER_SECOND * factor)
        elif action_id == ACTION_RATE_DOWN:
            actions.set_rate(shift_playback_rate(actions.rate(), -1))
        elif action_id == ACTION_RATE_UP:
            actions.set_rate(shift_playback_rate(actions.rate(), 1))
        elif action_id == ACTION_RATE_RESET:
            actions.set_rate(DEFAULT_PREVIEW_PLAYBACK_RATE)
        elif action_id == ACTION_PREVIOUS:
            actions.request_step(-1)
        elif action_id == ACTION_NEXT:
            actions.request_step(1)
        elif action_id == ACTION_TOGGLE_PLAYBACK:
            actions.toggle_playback()
        elif action_id == ACTION_TOGGLE_MUTE:
            actions.toggle_mute()
        elif action_id == ACTION_TOGGLE_FULLSCREEN:
            actions.toggle_fullscreen()
        elif action_id == ACTION_TOGGLE_INFO:
            actions.cycle_info()
        elif action_id == ACTION_FRAME_BACKWARD:
            actions.step_frame(-1)
        elif action_id == ACTION_FRAME_FORWARD:
            actions.step_frame(1)
