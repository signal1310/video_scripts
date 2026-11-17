"""분석 진행 콜백의 완료 수와 경과 시간으로 분석 속도와 남은 시간을 추정하는 모듈

- 시계는 생성자에서 주입받아 테스트에서 실제 대기 없이 검증
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil

from src.core.formatting import format_duration_ko

Clock = Callable[[], float]

MIN_SAMPLE_COUNT: int = 3
MIN_SAMPLE_ELAPSED_SEC: float = 2.0
APPROX_ROUND_STEP_SEC: int = 5
APPROX_SECONDS_ONLY_LIMIT_SEC: float = 60.0
RATE_DECIMALS: int = 1


@dataclass(frozen=True, slots=True)
class ProgressEstimate:
    """추정 시점의 완료 수, 시작 이후 경과 시간, 분석 속도, 남은 시간

    속도와 남은 시간은 표본이 부족하면 None
    """

    done: int
    total: int
    elapsed_sec: float
    rate_per_sec: float | None
    remaining_sec: float | None


class ProgressEstimator:
    """기준점 이후 완료분의 평균 속도로 남은 시간을 추정

    - 첫 관측 시점을 기준점으로 삼아, 그 이전에 한꺼번에 보고된 완료분(키프레임 보강의 건너뜀 항목 등)을 속도에서 제외
    - 병렬 워커는 완료 간격이 들쭉날쭉하므로 순간 속도 대신 기준점 이후 누적 평균 속도를 사용
    - 표본이 적은 초반에는 값이 크게 흔들리므로 추정을 보류
    """

    def __init__(self, clock: Clock = time.monotonic) -> None:
        self._clock: Clock = clock
        self._started_at: float = clock()
        self._anchor: tuple[int, float] | None = None

    def start(self) -> None:
        """추정 시작 시각과 기준점을 초기화"""
        self._started_at = self._clock()
        self._anchor = None

    def elapsed_sec(self) -> float:
        """시작 이후 경과 초, 완료 여부와 관계없이 현재 시점 기준"""
        return max(self._clock() - self._started_at, 0.0)

    def estimate(self, done: int, total: int) -> ProgressEstimate:
        """완료 수와 전체 수로 현재 시점의 분석 속도와 남은 시간 추정"""
        now = self._clock()
        if self._anchor is None:
            self._anchor = (done, now)
        anchor_done, anchored_at = self._anchor
        elapsed = max(now - self._started_at, 0.0)
        gained = done - anchor_done
        if gained < MIN_SAMPLE_COUNT or now - anchored_at < MIN_SAMPLE_ELAPSED_SEC:
            return ProgressEstimate(done, total, elapsed, None, None)
        rate = gained / (now - anchored_at)
        remaining = max(total - done, 0) / rate
        return ProgressEstimate(done, total, elapsed, rate, remaining)

    def seconds_per_item(self, total: int) -> float | None:
        """기준점 이후 항목당 평균 소요 시간, 표본이 부족하면 None

        분석 이력 기록용이며, 기준점 이전 완료분은 항목 수와 시간 모두에서 뺌
        """
        if self._anchor is None:
            return None
        anchor_done, anchored_at = self._anchor
        items = total - anchor_done
        if items < MIN_SAMPLE_COUNT:
            return None
        return (self._clock() - anchored_at) / items


def format_approx_duration(seconds: float) -> str:
    """초 단위 추정값을 '약 N초' 또는 '약 N분' 형식으로 변환

    - 60초 미만은 5초 단위로 올림해 숫자가 매 갱신마다 흔들리지 않게 함
    """
    rounded = max(ceil(seconds / APPROX_ROUND_STEP_SEC) * APPROX_ROUND_STEP_SEC, APPROX_ROUND_STEP_SEC)
    if rounded < APPROX_SECONDS_ONLY_LIMIT_SEC:
        return f"약 {rounded}초"
    return f"약 {format_duration_ko(seconds)}"


def format_rate(rate_per_sec: float) -> str:
    """분석 속도를 '개/초' 단위 문자열로 변환"""
    return f"{rate_per_sec:.{RATE_DECIMALS}f}개/초"


def format_remaining(remaining_sec: float | None) -> str:
    """남은 시간 추정 문구를 '약 N초 남음' 형식으로 변환, 추정 전에는 '남은 시간 계산 중'"""
    if remaining_sec is None:
        return "남은 시간 계산 중"
    return f"{format_approx_duration(remaining_sec)} 남음"


def format_estimate_label(pending_count: int, expected_sec: float | None) -> str:
    """분석 시작 전 예상값을 '31개, 약 40초' 형식으로 변환

    - 분석할 영상이 없으면 '분석할 영상 없음', 이력이 없으면 시간 대신 '측정 전'
    """
    if pending_count <= 0:
        return "분석할 영상 없음"
    count_text = f"{pending_count:,}개"
    if expected_sec is None:
        return f"{count_text}, 측정 전"
    return f"{count_text}, {format_approx_duration(expected_sec)}"
