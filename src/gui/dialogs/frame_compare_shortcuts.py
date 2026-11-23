"""화질 비교 창의 사용자 지정 단축키 해석과 동적 툴팁

- 창 전용 `compare.*` 명령이 미리 보기 공용 `preview.*` 키보다 먼저 매칭됨
- 확대, 축소 기본 키는 같은 키의 Shift 기호와 넘패드 표기도 함께 받음
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent, QKeySequence

from src.core.settings.shortcuts import (
    DEFAULT_SHORTCUTS,
    format_tooltip_with_shortcut,
    normalize_shortcut,
)
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs.frame_compare_canvas import MODE_SIDE, MODE_SPLIT, MODE_TOGGLE
from src.gui.dialogs.frame_compare_layout import CompareTimeline

ZOOM_IN_ACTION: str = "compare.zoom_in"
ZOOM_OUT_ACTION: str = "compare.zoom_out"
_ZOOM_IN_ALIASES: frozenset[str] = frozenset({"=", "+", "Shift+="})
_ZOOM_OUT_ALIASES: frozenset[str] = frozenset({"-", "_", "Shift+-"})


@dataclass(frozen=True, slots=True)
class CompareShortcutHandlers:
    """단축키 하나가 부를 화질 비교 창 동작 묶음"""

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

    def by_action(self) -> dict[str, Callable[[], None]]:
        """명령 식별자별 실행 함수"""
        return {
            "compare.mode_split": lambda: self.set_mode(MODE_SPLIT),
            "compare.mode_toggle": lambda: self.set_mode(MODE_TOGGLE),
            "compare.mode_side": lambda: self.set_mode(MODE_SIDE),
            "compare.toggle_original": self.toggle_original,
            "compare.zoom_fit": self.zoom_fit,
            "compare.zoom_in": self.zoom_in,
            "compare.zoom_out": self.zoom_out,
            "compare.toggle_controls": self.toggle_controls,
            "classify.compare_selected": self.request_preview,
            "preview.seek_backward": lambda: self.seek(-1, False),
            "preview.seek_forward": lambda: self.seek(1, False),
            "preview.seek_backward_large": lambda: self.seek(-1, True),
            "preview.seek_forward_large": lambda: self.seek(1, True),
            "preview.frame_backward": lambda: self.step_frame(-1),
            "preview.frame_forward": lambda: self.step_frame(1),
            "preview.rate_down": lambda: self.rate_step(-1),
            "preview.rate_up": lambda: self.rate_step(1),
            "preview.rate_reset": self.rate_reset,
            "preview.toggle_playback": self.toggle_playback,
            "preview.toggle_mute": self.toggle_mute,
            "preview.toggle_fullscreen": self.toggle_fullscreen,
        }


def pressed_candidates(event: QKeyEvent) -> tuple[str, ...]:
    """키 사건이 낼 수 있는 단축키 표기 후보, Shift 기호와 넘패드 변형 포함"""
    text = normalize_shortcut(QKeySequence(event.keyCombination()).toString())
    candidates: list[str] = [text] if text else []
    key = event.key()
    shifted = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
    if key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
        candidates += ["=", "+"]
        if shifted:
            candidates.append("Shift+=")
    if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
        candidates += ["-", "_"]
        if shifted:
            candidates.append("Shift+-")
    return tuple(dict.fromkeys(item for item in candidates if item))


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
