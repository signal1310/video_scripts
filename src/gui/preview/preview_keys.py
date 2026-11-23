"""미리 보기 창의 키 입력 차단 판정과 1프레임 이동 계산, 단축키 실행 결과 문구

- 열린 팝업, 모달, 텍스트 입력이 키를 직접 받아야 하는지 판정
- 영상 FPS에서 한 프레임 이동 밀리초를 구하고, 메타데이터 FPS 값도 안전하게 해석
- 단축키로 실행한 명령의 결과 문구는 이 모듈이 만들고 창은 표시만 함
"""

from __future__ import annotations

from collections.abc import Mapping

from PyQt6.QtCore import Qt
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
FEEDBACK_MS: int = 1200
PLAY_NOTICE: str = "재생"
PAUSE_NOTICE: str = "일시중지"
FRAME_BACKWARD_NOTICE: str = "이전 프레임"
FRAME_FORWARD_NOTICE: str = "다음 프레임"
MUTE_NOTICE: str = "음소거"
UNMUTE_NOTICE: str = "음소거 해제"
FULLSCREEN_NOTICE: str = "전체화면"
WINDOW_MODE_NOTICE: str = "창 모드"
PIN_NOTICE: str = "항상 위에 표시"
PIN_OFF_NOTICE: str = "항상 위에 표시 해제"
_SEEK_NOTICE_TEMPLATE: str = "구간 이동 {offset}초"
_RATE_NOTICE_TEMPLATE: str = "재생 배속: {rate:g}배"
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


def seek_notice(delta_ms: int) -> str:
    """구간 이동 폭과 방향을 초 단위 문구로 만듦"""
    sign = "+" if delta_ms > 0 else "-"
    return _SEEK_NOTICE_TEMPLATE.format(offset=f"{sign}{abs(delta_ms) // _MS_PER_SECOND}")


def rate_notice(rate: float) -> str:
    """바뀐 재생 배속의 문구"""
    return _RATE_NOTICE_TEMPLATE.format(rate=rate)


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
