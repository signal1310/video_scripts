"""미리 보기 창의 키 입력 차단 판정과 1프레임 이동 계산

- 열린 팝업, 모달, 텍스트 입력이 키를 직접 받아야 하는지 판정
- 영상 FPS에서 한 프레임 이동 밀리초를 구하고, 메타데이터 FPS 값도 안전하게 해석
"""

from __future__ import annotations

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
