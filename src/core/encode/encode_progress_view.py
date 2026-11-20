"""재인코딩 진행 창이 쓰는 계산: 진행 비율, 남은 시간, 표기 문자열, 순간 비트레이트 기록

- 시간 축은 배치 시작 기준 화면 경과 초(일시 정지 포함). 항목의 경과 시간은 이벤트 값(일시 정지 제외)을 그대로 씀
- 일시 정지 지점은 kbps가 None인 기록으로 남김
"""

from collections import deque
from dataclasses import dataclass

from src.core.formatting import format_duration_hms

SERIES_CAPACITY: int = 480
SERIES_WINDOW_SEC: float = 180.0
SMOOTH_WINDOW_SEC: float = 5.0
NO_VALUE_TEXT: str = "정보 없음"
PAUSED_SPAN_TEXT: str = "일시 정지 구간"


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """배치 시작 기준 시각과 순간 비트레이트. kbps가 None이면 일시 정지 지점"""

    at_sec: float
    kbps: float | None


class BitrateSeries:
    """항목 하나의 순간 비트레이트 기록. 용량을 넘으면 가장 오래된 점부터 버림"""

    def __init__(self, capacity: int = SERIES_CAPACITY) -> None:
        self._points: deque[SeriesPoint] = deque(maxlen=capacity)

    def add_sample(self, at_sec: float, kbps: float | None) -> None:
        """순간 비트레이트 한 점 추가. 측정 불가 값도 시각 자리를 남김"""
        self._points.append(SeriesPoint(at_sec, kbps))

    def add_pause(self, at_sec: float) -> None:
        """일시 정지 지점 추가. 연속된 일시 정지 지점은 하나로 합침"""
        if self._points and self._points[-1].kbps is None:
            return
        self._points.append(SeriesPoint(at_sec, None))

    def points(self) -> tuple[SeriesPoint, ...]:
        """기록된 점을 시각 순서대로 반환"""
        return tuple(self._points)


def moving_average(points: tuple[SeriesPoint, ...], window_sec: float) -> tuple[SeriesPoint, ...]:
    """각 값 점을 그 시각 앞 window_sec 동안의 값 평균으로 바꿈. 일시 정지 지점에서 평균 창을 비움"""
    smoothed: list[SeriesPoint] = []
    window: deque[tuple[float, float]] = deque()
    total = 0.0
    for point in points:
        if point.kbps is None:
            window.clear()
            total = 0.0
            smoothed.append(point)
            continue
        window.append((point.at_sec, point.kbps))
        total += point.kbps
        while window and window[0][0] <= point.at_sec - window_sec:
            total -= window.popleft()[1]
        smoothed.append(SeriesPoint(point.at_sec, total / len(window)))
    return tuple(smoothed)


def value_stats(points: tuple[SeriesPoint, ...]) -> tuple[float | None, float | None]:
    """값 점들의 평균과 최고값. 값 점이 없으면 둘 다 없음"""
    values = [point.kbps for point in points if point.kbps is not None]
    if not values:
        return None, None
    return sum(values) / len(values), max(values)


def progress_fraction(out_time_sec: float | None, duration_sec: float) -> float | None:
    """출력 위치를 원본 길이로 나눈 진행 비율(0~1). 값이 없으면 None"""
    if out_time_sec is None or duration_sec <= 0.0:
        return None
    return min(1.0, max(0.0, out_time_sec / duration_sec))


def remaining_sec(elapsed_sec: float, fraction: float | None) -> float | None:
    """항목 경과 시간과 진행 비율로 남은 시간을 추정. 진행이 없으면 None"""
    if fraction is None or fraction <= 0.0 or elapsed_sec <= 0.0:
        return None
    return max(0.0, elapsed_sec * (1.0 - fraction) / fraction)


def overall_remaining_sec(elapsed_sec: float, processed: int, total: int) -> float | None:
    """처리한 항목의 평균 시간으로 전체 남은 시간을 추정. 처리한 항목이 없으면 None"""
    if total <= 0 or processed <= 0 or elapsed_sec <= 0.0:
        return None
    return max(0.0, elapsed_sec * (total - processed) / processed)


def clock_text(seconds: float | None) -> str:
    """초 값을 시각 문자열로 바꿈. 값이 없으면 정보 없음"""
    return NO_VALUE_TEXT if seconds is None else format_duration_hms(seconds)


def speed_text(speed: float | None) -> str:
    """재인코딩 속도를 "1.25배" 형식으로 바꿈. 값이 없으면 정보 없음"""
    return NO_VALUE_TEXT if speed is None else f"{speed:.2f}배"


def kbps_text(kbps: float | None) -> str:
    """비트레이트를 정수 kbps로 바꿈. 값이 없으면 정보 없음"""
    return NO_VALUE_TEXT if kbps is None else f"{round(kbps)}kbps"


def point_label(point: SeriesPoint) -> str:
    """그래프 마우스 위치의 툴팁 문장. 일시 정지 지점은 구간 이름으로 표시"""
    clock = format_duration_hms(point.at_sec)
    if point.kbps is None:
        return f"{clock}, {PAUSED_SPAN_TEXT}"
    return f"{clock}, {kbps_text(point.kbps)}"
