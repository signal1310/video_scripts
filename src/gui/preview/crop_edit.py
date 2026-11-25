"""미리 보기에서 활성 화면(잘라낸 뒤 남길 영역)을 직접 끄는 순수 계산 모듈

- 위젯 좌표는 영상 내용 영역 기준으로 원본 좌표에 옮기고, 원본 좌표는 짝수로 맞춤
- 내용 영역은 겹침 배치와 같은 `crop_overlay.content_rect` 결과를 받아 씀
"""

from __future__ import annotations

import math
from collections import deque
from typing import Literal

from PyQt6.QtCore import QPoint, QRect, Qt

from src.core.analysis.frame_analysis import MANUAL_FRAME_MIN_SIZE_PX
from src.core.models import ActiveFrame

HANDLE_EDGE_PX: int = 8
MAX_MANUAL_UNDO_STEPS: int = 20

CropHandle = Literal[
    "move",
    "left",
    "right",
    "top",
    "bottom",
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
]

HANDLE_MOVE: CropHandle = "move"

_CORNER_HANDLES: tuple[CropHandle, ...] = (
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
)


def kept_label(frame: ActiveFrame) -> str:
    """겹침 위에 쓸 유지 영역 크기와 원본 좌표 표기"""
    return f"{frame.width} x {frame.height}, 왼쪽 {frame.x}, 위 {frame.y}"


def full_active_frame(video_size: tuple[int, int]) -> ActiveFrame:
    """영상 전체를 유지 영역으로 쓰는 값(짝수 정렬)"""
    return ActiveFrame(
        width=_even_floor(max(video_size[0], 0)),
        height=_even_floor(max(video_size[1], 0)),
        x=0,
        y=0,
    )


def content_rect(view_size: tuple[int, int], video_size: tuple[int, int]) -> QRect:
    """영상 위젯 안에서 영상비를 지켜 가운데 놓은 내용 영역

    - 위젯이 영상비와 다르면 위아래나 양옆에 플레이어 레터박스가 남음
    - 크기가 0 이하인 입력은 1픽셀로 올려 나눗셈과 빈 영역을 막음
    """
    view_w, view_h = (max(int(value), 1) for value in view_size)
    video_w, video_h = (max(int(value), 1) for value in video_size)
    scale = min(view_w / video_w, view_h / video_h)
    content_w = max(round(video_w * scale), 1)
    content_h = max(round(video_h * scale), 1)
    return QRect((view_w - content_w) // 2, (view_h - content_h) // 2, content_w, content_h)


def to_source(
    content: QRect, video_size: tuple[int, int], point: QPoint
) -> tuple[float, float]:
    """영상 내용 영역 안의 위젯 좌표를 원본 픽셀 좌표로 바꿈

    - 내용 영역 밖 좌표도 비례해 계산하므로 끌기 중 경계 보정은 호출자가 함
    - 내용 영역 크기가 0이면 원본 좌표를 알 수 없어 0으로 둠
    """
    video_w = max(int(video_size[0]), 1)
    video_h = max(int(video_size[1]), 1)
    if content.width() <= 0 or content.height() <= 0:
        return (0.0, 0.0)
    scale_x = video_w / content.width()
    scale_y = video_h / content.height()
    return (
        (point.x() - content.left()) * scale_x,
        (point.y() - content.top()) * scale_y,
    )


def hit_test(
    kept: QRect, point: QPoint, *, tolerance: int = HANDLE_EDGE_PX
) -> CropHandle | None:
    """유지 영역 위 좌표가 닿는 손잡이, 어디에도 닿지 않으면 None

    - 모서리가 변보다, 변이 안쪽 이동보다 우선함
    - 크기가 0인 영역은 손잡이가 없음
    """
    if kept.width() <= 0 or kept.height() <= 0:
        return None
    left = kept.left()
    right = kept.left() + kept.width()
    top = kept.top()
    bottom = kept.top() + kept.height()
    x = point.x()
    y = point.y()
    near_left = abs(x - left) <= tolerance
    near_right = abs(x - right) <= tolerance
    near_top = abs(y - top) <= tolerance
    near_bottom = abs(y - bottom) <= tolerance
    within_x = left - tolerance <= x <= right + tolerance
    within_y = top - tolerance <= y <= bottom + tolerance
    corners: tuple[tuple[bool, bool, CropHandle], ...] = (
        (near_left, near_top, "top-left"),
        (near_right, near_top, "top-right"),
        (near_left, near_bottom, "bottom-left"),
        (near_right, near_bottom, "bottom-right"),
    )
    for hit_x, hit_y, handle in corners:
        if hit_x and hit_y:
            return handle
    if not (within_x and within_y):
        return None
    if near_left:
        return "left"
    if near_right:
        return "right"
    if near_top:
        return "top"
    if near_bottom:
        return "bottom"
    if kept.contains(point):
        return HANDLE_MOVE
    return None



def drag_frame(
    handle: CropHandle,
    start: ActiveFrame,
    start_point: QPoint,
    point: QPoint,
    content: QRect,
    video_size: tuple[int, int],
    *,
    min_size: int = MANUAL_FRAME_MIN_SIZE_PX,
) -> ActiveFrame:
    """끌기 시작 좌표와 지금 좌표의 차이를 원본 좌표에 적용한 유지 영역

    - 영상 프레임 안으로 가두고 최소 크기를 지키며, 좌표와 크기는 짝수로 맞춤
    - 종횡비는 고정하지 않음
    """
    max_w = _even_floor(video_size[0])
    max_h = _even_floor(video_size[1])
    if max_w <= 0 or max_h <= 0:
        return start
    start_source = to_source(content, video_size, start_point)
    now_source = to_source(content, video_size, point)
    delta_x = now_source[0] - start_source[0]
    delta_y = now_source[1] - start_source[1]
    if handle == HANDLE_MOVE:
        # - 이동은 크기를 그대로 두고 좌상단만 옮기므로 크기는 시작 값에서 다시 계산함
        width = min(_even_floor(start.width), max_w)
        height = min(_even_floor(start.height), max_h)
        moved_left = _clamp(start.x + delta_x, 0.0, float(max_w - width))
        moved_top = _clamp(start.y + delta_y, 0.0, float(max_h - height))
        return ActiveFrame(
            width=width,
            height=height,
            x=min(max(_even_floor(moved_left), 0), max_w - width),
            y=min(max(_even_floor(moved_top), 0), max_h - height),
        )
    left = float(start.x)
    top = float(start.y)
    right = float(start.x + start.width)
    bottom = float(start.y + start.height)
    if "left" in handle:
        left = _clamp(left + delta_x, 0.0, right - min_size)
    if "right" in handle:
        right = _clamp(right + delta_x, left + min_size, float(max_w))
    if "top" in handle:
        top = _clamp(top + delta_y, 0.0, bottom - min_size)
    if "bottom" in handle:
        bottom = _clamp(bottom + delta_y, top + min_size, float(max_h))
    box_left = _even_floor(left)
    box_top = _even_floor(top)
    width = _even_floor(right) - box_left
    height = _even_floor(bottom) - box_top
    if width < min_size:
        width = min_size
        if "left" in handle:
            box_left = _even_floor(right) - min_size
    if height < min_size:
        height = min_size
        if "top" in handle:
            box_top = _even_floor(bottom) - min_size
    width = min(width, max_w)
    height = min(height, max_h)
    box_left = min(max(box_left, 0), max_w - width)
    box_top = min(max(box_top, 0), max_h - height)
    return ActiveFrame(width=width, height=height, x=box_left, y=box_top)


def cursor_for(handle: CropHandle | None) -> Qt.CursorShape:
    """손잡이에 맞는 마우스 커서 모양"""
    if handle == HANDLE_MOVE:
        return Qt.CursorShape.SizeAllCursor
    if handle in _CORNER_HANDLES:
        diagonal = handle in ("top-left", "bottom-right")
        return Qt.CursorShape.SizeFDiagCursor if diagonal else Qt.CursorShape.SizeBDiagCursor
    if handle in ("left", "right"):
        return Qt.CursorShape.SizeHorCursor
    if handle in ("top", "bottom"):
        return Qt.CursorShape.SizeVerCursor
    return Qt.CursorShape.ArrowCursor


class FrameHistory:
    """창 세션 안에서 바꾼 활성 화면의 이전 값 되돌리기 스택"""

    def __init__(self, limit: int = MAX_MANUAL_UNDO_STEPS) -> None:
        self._limit = max(int(limit), 1)
        self._frames: deque[ActiveFrame | None] = deque(maxlen=self._limit)

    @property
    def limit(self) -> int:
        """보관하는 최대 단계 수"""
        return self._limit

    def can_undo(self) -> bool:
        """되돌릴 값이 남아 있는지"""
        return bool(self._frames)

    def push(self, frame: ActiveFrame | None) -> None:
        """바꾸기 전 값을 쌓고, 상한을 넘으면 가장 오래된 값부터 버림"""
        self._frames.append(frame)

    def pop(self) -> ActiveFrame | None:
        """가장 최근에 쌓은 바꾸기 전 값, 비어 있으면 None"""
        if not self._frames:
            return None
        return self._frames.pop()

    def clear(self) -> None:
        """쌓인 값을 모두 버림"""
        self._frames.clear()


def _even_floor(value: float) -> int:
    """짝수로 내림 정렬한 정수(인코더 crop 필터는 짝수 값을 요구)"""
    whole = math.floor(value)
    return whole - whole % 2


def _clamp(value: float, low: float, high: float) -> float:
    """하한이 상한보다 클 때도 하한을 지키는 범위 제한"""
    return min(max(value, low), max(low, high))
