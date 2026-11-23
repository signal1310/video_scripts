"""화질 비교 창의 사용자 지정 단축키 해석, 실행 결과 문구, 앱 키 필터, 동적 툴팁

- 창 전용 `compare.*` 명령이 미리 보기 공용 `preview.*` 키보다 먼저 매칭됨
- 확대, 축소 기본 키는 같은 키의 Shift 기호와 넘패드 표기도 함께 받음
- 단축키 실행 뒤 바뀐 상태를 알림 문구로 만들고, 창이 활성일 때 앱 키 필터가 키를 받음
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QKeyEvent, QKeySequence
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.settings.shortcuts import (
    DEFAULT_SHORTCUTS,
    format_tooltip_with_shortcut,
    normalize_shortcut,
)
from src.core.settings.ui_state import (
    DEFAULT_PREVIEW_SEEK_SECONDS,
    PREVIEW_LARGE_SEEK_MULTIPLIER,
)
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs.frame_compare_canvas import (
    MODE_SIDE,
    MODE_SPLIT,
    MODE_TOGGLE,
    FrameCompareCanvas,
)
from src.gui.dialogs.frame_compare_layout import CompareTimeline
from src.gui.preview.preview_keys import blocks_preview_keys

ZOOM_IN_ACTION: str = "compare.zoom_in"
ZOOM_OUT_ACTION: str = "compare.zoom_out"
_ZOOM_IN_ALIASES: frozenset[str] = frozenset({"=", "+", "Shift+="})
_ZOOM_OUT_ALIASES: frozenset[str] = frozenset({"-", "_", "Shift+-"})
_SHIFT_SPACE: str = "Shift+Space"
_SHIFT_SPACE_IGNORED_MODIFIERS: Qt.KeyboardModifier = (
    Qt.KeyboardModifier.KeypadModifier | Qt.KeyboardModifier.GroupSwitchModifier
)
NOTICE_SPLIT_MODE: str = "분할선 모드"
NOTICE_TOGGLE_MODE: str = "즉시 전환 모드"
NOTICE_SIDE_MODE: str = "좌우 비교 모드"
NOTICE_ORIGINAL_PINNED: str = "원본 고정"
NOTICE_COMPARE_SHOWN: str = "비교 표시"
NOTICE_FIT_TEMPLATE: str = "화면 맞춤 ({percent}%)"
NOTICE_ZOOM_IN_TEMPLATE: str = "확대 {percent}%"
NOTICE_ZOOM_OUT_TEMPLATE: str = "축소 {percent}%"
NOTICE_CONTROLS_COLLAPSED: str = "조작 바 접기"
NOTICE_CONTROLS_EXPANDED: str = "조작 바 펼치기"
NOTICE_PLAYING: str = "재생"
NOTICE_PAUSED: str = "일시중지"
NOTICE_SEEK_TEMPLATE: str = "구간 이동 {offset}초"
NOTICE_FRAME_BACKWARD: str = "이전 프레임"
NOTICE_FRAME_FORWARD: str = "다음 프레임"
NOTICE_RATE_TEMPLATE: str = "재생 배속: {rate:g}배"
NOTICE_MUTED: str = "음소거"
NOTICE_UNMUTED: str = "음소거 해제"
NOTICE_FULLSCREEN: str = "전체화면"
NOTICE_WINDOW_MODE: str = "창 모드"


@dataclass(frozen=True, slots=True)
class CompareNoticeState:
    """단축키 실행 결과 문구를 고르는 화면 상태"""

    mode: str = MODE_SPLIT
    zoom_percent: int = 100
    original_pinned: bool = False
    collapsed: bool = False
    playing: bool = False
    rate: float = 1.0
    muted: bool = False
    fullscreen: bool = False
    seek_seconds: int = DEFAULT_PREVIEW_SEEK_SECONDS


class PlaybackStateSource(Protocol):
    """알림 문구에 필요한 재생 상태만 읽는 재생기"""

    def is_playing(self) -> bool:
        """지금 재생 중인지"""
        ...

    def rate(self) -> float:
        """현재 재생 배속"""
        ...

    def muted(self) -> bool:
        """현재 음소거 여부"""
        ...


class ControlsStateSource(Protocol):
    """알림 문구에 필요한 조작 바 상태만 읽는 상단 바"""

    def is_collapsed(self) -> bool:
        """상단 조작 바가 접혀 있는지"""
        ...


def shortcut_notice_state(
    canvas: FrameCompareCanvas,
    top: ControlsStateSource,
    player: PlaybackStateSource,
    window: QWidget,
    seek_seconds: int,
) -> CompareNoticeState:
    """화면 객체에서 지금 상태를 모아 알림 문구의 입력으로 넘김"""
    return CompareNoticeState(
        mode=canvas.mode(),
        zoom_percent=round(canvas.scale() * 100),
        original_pinned=canvas.original_pinned(),
        collapsed=top.is_collapsed(),
        playing=player.is_playing(),
        rate=player.rate(),
        muted=player.muted(),
        fullscreen=window.isFullScreen(),
        seek_seconds=seek_seconds,
    )


def _no_notice(_text: str) -> None:
    """알림을 쓰지 않는 기본 처리"""


@dataclass(frozen=True, slots=True)
class CompareShortcutHandlers:
    """단축키 하나가 부를 화질 비교 창 동작 묶음과 실행 결과 알림 수단"""

    set_mode: Callable[[str], None]
    toggle_original: Callable[[], None]
    zoom_fit: Callable[[], None]
    zoom_in: Callable[[], None]
    zoom_out: Callable[[], None]
    toggle_controls: Callable[[], None]
    seek: Callable[[int, bool], None]
    step_frame: Callable[[int], None]
    toggle_playback: Callable[[], None]
    rate_step: Callable[[int], None]
    rate_reset: Callable[[], None]
    toggle_mute: Callable[[], None]
    toggle_fullscreen: Callable[[], None]
    request_preview: Callable[[], None]
    notify: Callable[[str], None] = _no_notice
    notice_state: Callable[[], CompareNoticeState] = CompareNoticeState

    def by_action(self) -> dict[str, Callable[[], None]]:
        """명령 식별자별 실행 함수, 키보드로 실행한 명령은 바뀐 상태를 알림"""
        return {
            "compare.mode_split": lambda: self._run(lambda: self.set_mode(MODE_SPLIT), NOTICE_SPLIT_MODE),
            "compare.mode_toggle": lambda: self._run(lambda: self.set_mode(MODE_TOGGLE), NOTICE_TOGGLE_MODE),
            "compare.mode_side": lambda: self._run(lambda: self.set_mode(MODE_SIDE), NOTICE_SIDE_MODE),
            "compare.toggle_original": lambda: self._run_after(self.toggle_original, self._original_notice),
            "compare.zoom_fit": lambda: self._run_after(self.zoom_fit, self._fit_notice),
            "compare.zoom_in": lambda: self._run_after(self.zoom_in, self._zoom_in_notice),
            "compare.zoom_out": lambda: self._run_after(self.zoom_out, self._zoom_out_notice),
            "compare.toggle_controls": lambda: self._run_after(self.toggle_controls, self._controls_notice),
            "classify.compare_selected": self.request_preview,
            "preview.seek_backward": lambda: self._seek(-1, False),
            "preview.seek_forward": lambda: self._seek(1, False),
            "preview.seek_backward_large": lambda: self._seek(-1, True),
            "preview.seek_forward_large": lambda: self._seek(1, True),
            "preview.frame_backward": lambda: self._run(lambda: self.step_frame(-1), NOTICE_FRAME_BACKWARD),
            "preview.frame_forward": lambda: self._run(lambda: self.step_frame(1), NOTICE_FRAME_FORWARD),
            "preview.rate_down": lambda: self._run_after(lambda: self.rate_step(-1), self._rate_notice),
            "preview.rate_up": lambda: self._run_after(lambda: self.rate_step(1), self._rate_notice),
            "preview.rate_reset": lambda: self._run_after(self.rate_reset, self._rate_notice),
            "preview.toggle_playback": self._toggle_playback_notice,
            "preview.toggle_mute": lambda: self._run_after(self.toggle_mute, self._mute_notice),
            "preview.toggle_fullscreen": lambda: self._run_after(
                self.toggle_fullscreen, self._fullscreen_notice
            ),
        }

    def _run(self, action: Callable[[], None], notice: str) -> None:
        """동작을 실행하고 고정 문구를 알림"""
        action()
        self.notify(notice)

    def _run_after(self, action: Callable[[], None], notice: Callable[[], str | None]) -> None:
        """동작을 실행하고 바뀐 상태의 문구를 알림, 문구가 없으면 알리지 않음"""
        action()
        text = notice()
        if text:
            self.notify(text)

    def _seek(self, direction: int, large: bool) -> None:
        """구간을 옮기고 실제로 움직인 초를 알림"""
        self.seek(direction, large)
        seconds = self.notice_state().seek_seconds
        offset = seconds * PREVIEW_LARGE_SEEK_MULTIPLIER if large else seconds
        sign = "+" if direction > 0 else "-"
        self.notify(NOTICE_SEEK_TEMPLATE.format(offset=f"{sign}{offset}"))

    def _original_notice(self) -> str | None:
        """즉시 전환 모드에서만 원본 고정과 비교 표시를 구분해 알림"""
        state = self.notice_state()
        if state.mode != MODE_TOGGLE:
            return None
        return NOTICE_ORIGINAL_PINNED if state.original_pinned else NOTICE_COMPARE_SHOWN

    def _fit_notice(self) -> str:
        return NOTICE_FIT_TEMPLATE.format(percent=self.notice_state().zoom_percent)

    def _zoom_in_notice(self) -> str:
        return NOTICE_ZOOM_IN_TEMPLATE.format(percent=self.notice_state().zoom_percent)

    def _zoom_out_notice(self) -> str:
        return NOTICE_ZOOM_OUT_TEMPLATE.format(percent=self.notice_state().zoom_percent)

    def _controls_notice(self) -> str:
        collapsed = self.notice_state().collapsed
        return NOTICE_CONTROLS_COLLAPSED if collapsed else NOTICE_CONTROLS_EXPANDED

    def _toggle_playback_notice(self) -> None:
        """재생을 전환하고, 전환 전 상태의 반대를 알림"""
        playing = self.notice_state().playing
        self.toggle_playback()
        self.notify(NOTICE_PAUSED if playing else NOTICE_PLAYING)

    def _rate_notice(self) -> str:
        return NOTICE_RATE_TEMPLATE.format(rate=self.notice_state().rate)

    def _mute_notice(self) -> str:
        return NOTICE_MUTED if self.notice_state().muted else NOTICE_UNMUTED

    def _fullscreen_notice(self) -> str:
        return NOTICE_FULLSCREEN if self.notice_state().fullscreen else NOTICE_WINDOW_MODE


def pressed_candidates(event: QKeyEvent) -> tuple[str, ...]:
    """키 사건이 낼 수 있는 단축키 표기 후보, Shift 기호와 넘패드 변형 포함"""
    text = normalize_shortcut(QKeySequence(event.keyCombination()).toString())
    candidates: list[str] = [text] if text else []
    key = event.key()
    modifiers = event.modifiers()
    shifted = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
    if key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
        candidates += ["=", "+"]
        if shifted:
            candidates.append("Shift+=")
    if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
        candidates += ["-", "_"]
        if shifted:
            candidates.append("Shift+-")
    if key == Qt.Key.Key_Space and is_shift_space(modifiers):
        candidates.append(_SHIFT_SPACE)
    return tuple(dict.fromkeys(item for item in candidates if item))


def is_shift_space(modifiers: Qt.KeyboardModifier) -> bool:
    """Shift만 눌린 Space인지, 넘패드와 그룹 전환 보조 플래그는 무시"""
    if not modifiers & Qt.KeyboardModifier.ShiftModifier:
        return False
    others = modifiers & ~(Qt.KeyboardModifier.ShiftModifier | _SHIFT_SPACE_IGNORED_MODIFIERS)
    return not others


def match_action(shortcuts: Mapping[str, str], candidates: tuple[str, ...]) -> str | None:
    """눌린 키 후보와 같은 단축키를 가진 첫 명령, 창 전용 명령이 우선"""
    pressed = set(candidates)
    if not pressed:
        return None
    for action_id in layout.COMPARE_ACTION_ORDER:
        configured = normalize_shortcut(shortcuts.get(action_id, ""))
        if not configured:
            continue
        allowed = {configured}
        if action_id == ZOOM_IN_ACTION and configured == DEFAULT_SHORTCUTS[ZOOM_IN_ACTION]:
            allowed |= _ZOOM_IN_ALIASES
        if action_id == ZOOM_OUT_ACTION and configured == DEFAULT_SHORTCUTS[ZOOM_OUT_ACTION]:
            allowed |= _ZOOM_OUT_ALIASES
        if allowed & pressed:
            return action_id
    return None


class CompareKeyController:
    """화질 비교 창 키 입력을 고정 닫기와 사용자 지정 단축키로 나눠 처리

    - Space, Esc는 메뉴, 단축키 안내, 전체화면, 창 순서로 닫음
    - 배율 입력 칸에 포커스가 있으면 창 단축키를 가로채지 않음
    """

    def __init__(
        self,
        *,
        get_shortcuts: Callable[[], Mapping[str, str]],
        get_handlers: Callable[[], CompareShortcutHandlers],
        focus_in_editor: Callable[[], bool],
        close_menu: Callable[[], bool],
        close_guide: Callable[[], bool],
        is_fullscreen: Callable[[], bool],
        toggle_fullscreen: Callable[[], None],
        close_window: Callable[[], object],
    ) -> None:
        self._get_shortcuts = get_shortcuts
        self._get_handlers = get_handlers
        self._focus_in_editor = focus_in_editor
        self._close_menu = close_menu
        self._close_guide = close_guide
        self._is_fullscreen = is_fullscreen
        self._toggle_fullscreen = toggle_fullscreen
        self._close_window = close_window

    def handle(self, event: QKeyEvent) -> bool:
        """키 사건을 처리했으면 참, 아니면 거짓"""
        if self._focus_in_editor():
            return False
        key = event.key()
        shifted = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Escape) and not shifted:
            self._close_in_order()
            return True
        action_id = match_action(self._get_shortcuts(), pressed_candidates(event))
        if action_id is None:
            return False
        if event.isAutoRepeat() and action_id == "compare.toggle_original":
            return True
        handler = self._get_handlers().by_action().get(action_id)
        if handler is not None:
            handler()
        return True

    def _close_in_order(self) -> None:
        if self._close_menu():
            return
        if self._close_guide():
            return
        if self._is_fullscreen():
            self._toggle_fullscreen()
            return
        self._close_window()


class CompareKeyFilter(QObject):
    """화질 비교 창이 활성일 때 자식 위젯 포커스와 무관하게 창 단축키를 받는 앱 이벤트 필터

    - 창을 표시하는 동안만 등록하고 숨김에서 해제해 다른 창의 키를 가로채지 않음
    - 모달, 팝업, 배율 입력이 키를 직접 받아야 하면 넘기지 않음
    """

    def __init__(self, window: QWidget, keys: CompareKeyController) -> None:
        super().__init__(window)
        self._window = window
        self._keys = keys
        self._installed = False

    def attach(self) -> None:
        """앱 이벤트 필터를 한 번만 등록"""
        app = QApplication.instance()
        if app is not None and not self._installed:
            app.installEventFilter(self)
            self._installed = True

    def detach(self) -> None:
        """앱 이벤트 필터를 해제"""
        app = QApplication.instance()
        if app is not None and self._installed:
            app.removeEventFilter(self)
            self._installed = False

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """창이 활성이고 팝업과 입력 칸이 키를 받지 않을 때만 창 단축키로 처리"""
        if a1 is None or a1.type() != QEvent.Type.KeyPress or not isinstance(a1, QKeyEvent):
            return False
        if not self._window.isVisible() or not self._window.isActiveWindow():
            return False
        if blocks_preview_keys():
            return False
        return self._keys.handle(a1)


def apply_timeline_tooltips(
    timeline: CompareTimeline,
    shortcuts: Mapping[str, str],
    *,
    muted: bool,
    rate: float,
) -> None:
    """하단 재생 바 버튼 툴팁에 현재 단축키를 덧붙임"""
    timeline.prev_button.setToolTip(
        format_tooltip_with_shortcut(
            layout.PREV_TIP, shortcuts.get("preview.frame_backward", "")
        )
    )
    timeline.next_button.setToolTip(
        format_tooltip_with_shortcut(layout.NEXT_TIP, shortcuts.get("preview.frame_forward", ""))
    )
    timeline.toggle_button.setToolTip(
        format_tooltip_with_shortcut(
            layout.PLAY_TOGGLE_TIP, shortcuts.get("preview.toggle_playback", "")
        )
    )
    mute_tip = layout.UNMUTE_TIP if muted else layout.MUTE_TIP
    timeline.mute_button.setToolTip(
        format_tooltip_with_shortcut(mute_tip, shortcuts.get("preview.toggle_mute", ""))
    )
    rate_tip = layout.RATE_TIP_TEMPLATE.format(rate=rate)
    down = shortcuts.get("preview.rate_down", "")
    up = shortcuts.get("preview.rate_up", "")
    timeline.rate_button.setToolTip(
        format_tooltip_with_shortcut(rate_tip, f"{down} / {up}" if down and up else "")
    )
    timeline.fullscreen_button.setToolTip(
        format_tooltip_with_shortcut(
            layout.FULLSCREEN_TIP, shortcuts.get("preview.toggle_fullscreen", "")
        )
    )
