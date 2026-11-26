"""단계 소요 시간을 개발 로그에 남기는 측정 모듈

- 시계를 주입할 수 있어 테스트가 실제 시간을 기다리지 않고 경계 동작을 검증함
- 단계 이름과 경과 초만 남기고 영상 이름은 남기지 않아 사용자 폴더 구조를 노출하지 않음
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

APP_LOGGER_NAME: str = "src"
TIMING_LOG_TEMPLATE: str = "단계 소요 {stage}: {elapsed:.3f}초"


def _default_clock() -> float:
    """단조 증가 시계 값 반환"""
    return time.perf_counter()


class StageClock:
    """단계 진입 시각을 잡아 두고 완료 때 걸린 시간을 DEBUG로 남기는 측정기

    - `observe`는 단계 이름이 바뀔 때만 이전 구간을 마감하고 새로 시작함
    - 측정기 하나는 스레드 하나에서만 씀(단계 진행 신호는 메인 스레드에서 옴)
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = _default_clock,
        logger: logging.Logger | None = None,
    ) -> None:
        self._clock = clock
        self._logger = logger or logging.getLogger(APP_LOGGER_NAME)
        self._stage: str | None = None
        self._started: float = 0.0

    @property
    def current_stage(self) -> str | None:
        """측정 중인 단계 이름. 없으면 None"""
        return self._stage

    def observe(self, stage: str) -> float | None:
        """단계가 바뀔 때만 이전 단계 소요를 남기고 새 단계 측정을 시작

        - 같은 단계가 거듭 들어와도 처음 시작 시각을 유지해 단계 전체 소요를 한 번만 남김
        - 단계 진행은 화면 갱신 한 번에 여러 번 오므로 같은 단계마다 기록하면 로그가 부풀어 오름
        - 돌려주는 값은 마감한 이전 단계의 경과 초이며, 첫 단계이거나 같은 단계면 None
        """
        name = stage or None
        if name is None and self._stage is None:
            return None
        if name is not None and name == self._stage:
            return None
        now = self._clock()
        finished = self._finish(now)
        self._stage = name
        self._started = now
        return finished

    def finish(self) -> float | None:
        """측정 중인 단계를 마감하고 남은 소요를 남김. 측정 중이 아니면 None"""
        finished = self._finish(self._clock())
        self._stage = None
        return finished

    def _finish(self, now: float) -> float | None:
        """측정 중인 단계가 있으면 소요를 계산해 남기고 경과 초 반환"""
        if self._stage is None:
            return None
        elapsed = max(0.0, now - self._started)
        self._logger.debug(TIMING_LOG_TEMPLATE.format(stage=self._stage, elapsed=elapsed))
        return elapsed
