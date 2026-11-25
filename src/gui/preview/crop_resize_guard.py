"""여백 편집 중 창 테두리 크기 조절이 손잡이 누름을 가로채지 않게 하는 판정 모듈

- Windows는 창 테두리 여백의 `WM_NCHITTEST` 응답으로 크기 조절을 시작하므로, 편집 중 손잡이
  자리에서는 클라이언트 영역으로 답해 누름이 영상 위젯과 손잡이로 오게 함
- 메시지 해석과 판정을 순수 함수로 두어 창 없이 검증
"""

from __future__ import annotations

import ctypes

from PyQt6 import sip
from PyQt6.QtCore import QByteArray, QPoint, QRect

WM_NCHITTEST: int = 0x0084
HTCLIENT: int = 1
WINDOWS_MESSAGE_EVENT: str = "windows_generic_MSG"
EventType = QByteArray | bytes | bytearray | memoryview
_LOW_WORD_MASK: int = 0xFFFF
_WORD_BITS: int = 16
_WORD_SIGN_BIT: int = 0x8000
_SIGN_RANGE: int = 0x10000


class _NativeMessage(ctypes.Structure):
    """Win32 MSG 구조체에서 위치를 읽는 데 필요한 앞부분"""

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("w_param", ctypes.c_size_t),
        ("l_param", ctypes.c_ssize_t),
    ]


def screen_point_from_lparam(l_param: int) -> QPoint:
    """`WM_NCHITTEST`의 lParam(화면 좌표를 담은 두 16비트 값)을 좌표로 바꿈"""
    x = l_param & _LOW_WORD_MASK
    y = (l_param >> _WORD_BITS) & _LOW_WORD_MASK
    return QPoint(_signed_word(x), _signed_word(y))


def message_screen_point(message: sip.voidptr | None) -> QPoint | None:
    """네이티브 메시지에서 화면 좌표를 읽음, 구조체가 비어 있으면 None"""
    header = _message_header(message)
    if header is None:
        return None
    return screen_point_from_lparam(int(header.l_param))


def is_frame_hit_test(event_type: EventType, message: sip.voidptr | None) -> bool:
    """네이티브 사건이 창 테두리 적중 판정(`WM_NCHITTEST`)인지"""
    if bytes(event_type) != WINDOWS_MESSAGE_EVENT.encode():
        return False
    header = _message_header(message)
    if header is None:
        return False
    return int(header.message) == WM_NCHITTEST


def blocks_frame_resize(point: QPoint, kept_global: QRect, *, tolerance: int) -> bool:
    """화면 좌표가 유지 영역 손잡이 판정 띠 안이라 창 크기 조절로 넘기면 안 되는지"""
    if kept_global.width() <= 0 or kept_global.height() <= 0:
        return False
    return kept_global.adjusted(-tolerance, -tolerance, tolerance, tolerance).contains(point)


def should_keep_client_hit_test(
    event_type: EventType,
    message: sip.voidptr | None,
    video_global: QRect,
    *,
    edited_kept: QRect | None,
    tolerance: int,
) -> bool:
    """창 테두리 적중 판정을 클라이언트로 되돌려야 하는지

    - 편집 대상이 없거나 창 테두리 적중 판정이 아니면 창 크기 조절을 그대로 둠
    - 영상 영역 안의 누름과 유지 영역 손잡이 띠는 창 크기 조절로 넘기지 않음
    """
    if edited_kept is None or not is_frame_hit_test(event_type, message):
        return False
    point = message_screen_point(message)
    if point is None:
        return False
    if video_global.contains(point):
        return True
    return blocks_frame_resize(point, edited_kept, tolerance=tolerance)


def _signed_word(value: int) -> int:
    """16비트 값을 부호 있는 좌표로 해석(음수 화면 좌표 보정)"""
    return value - _SIGN_RANGE if value & _WORD_SIGN_BIT else value


def _message_header(message: sip.voidptr | None) -> _NativeMessage | None:
    """네이티브 메시지 주소를 MSG 구조체로 읽음, 주소가 없으면 None"""
    if message is None:
        return None
    address = int(message)
    if address <= 0:
        return None
    return ctypes.cast(address, ctypes.POINTER(_NativeMessage)).contents
