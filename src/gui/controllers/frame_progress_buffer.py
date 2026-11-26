"""단계 진행 이벤트를 묶음 주기마다 한 번만 반영하는 타이머 보조

- 단계 이벤트는 영상 하나에 여러 번 도착하므로 매번 하단 위젯을 다시 배치하면 화면이 끊김
- 묶는 동안에는 마지막 값만 남기고, 더 새로운 진행 값이나 종료가 오면 대기 값을 버려 낡은 표시가 남지 않게 함
- 개발자 모드의 단계 소요 측정은 같은 단계 이벤트를 받아 쓰므로 여기서 함께 기록(DEBUG 로그는 개발자 모드에서만 파일에 남음)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QTimer

from src.core.diagnostics.timings import StageClock

TIMINGS_LOGGER_NAME: str = "src.diagnostics.timings"


@dataclass(frozen=True, slots=True)
class StageProgress:
    """단계 이벤트 하나의 완료 수, 전체 수, 파일 이름, 단계 이름, 단계 진행, 단계 전체 수"""

    completed: int
    total: int
    filename: str
    stage: str
    stage_done: int
    stage_total: int


class FrameProgressBuffer:
    """마지막 단계 진행 값을 묶음 주기마다 한 번만 싱크로 넘기는 단발 타이머"""

    def __init__(self, sink: Callable[[StageProgress], None], interval_ms: int) -> None:
        self._sink = sink
        self._pending: StageProgress | None = None
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.flush)
        self._stage_clock = StageClock(logger=logging.getLogger(TIMINGS_LOGGER_NAME))

    @property
    def pending(self) -> StageProgress | None:
        """아직 반영하지 않고 묶어 둔 값, 없으면 None"""
        return self._pending

    @property
    def interval_ms(self) -> int:
        """묶음 주기"""
        return self._timer.interval()

    def is_bundling(self) -> bool:
        """묶음 타이머가 돌고 있는지 여부"""
        return self._timer.isActive()

    def submit(self, progress: StageProgress) -> None:
        """마지막 값을 기억하고, 타이머가 돌고 있으면 다시 걸지 않아 주기를 지킴"""
        self._pending = progress
        self._stage_clock.observe(progress.stage)
        if not self._timer.isActive():
            self._timer.start()

    def flush(self) -> None:
        """묶어 둔 값이 있으면 싱크로 넘기고 비움"""
        pending = self._pending
        if pending is None:
            return
        self._timer.stop()
        self._pending = None
        self._sink(pending)

    def drop(self) -> None:
        """묶어 둔 값을 버려 더 새로운 진행 값이 덮이지 않게 함"""
        self._timer.stop()
        self._pending = None
        self._stage_clock.finish()


__all__ = ["FrameProgressBuffer", "StageProgress"]
