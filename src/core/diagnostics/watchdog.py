"""메인 스레드가 멈출 때 그 순간의 호출 스택을 남기는 감시 모듈

- 메인 스레드는 100ms마다 `beat()`로 살아 있음을 알리고, 감시 스레드는 그 간격이 임계값을 넘으면 스택을 한 번 남김
- 감시 스레드는 Qt에 접근하지 않음. 파일과 표준 logging만 씀
- 시계, 스택 공급자, 기록 함수를 주입할 수 있어 테스트가 실제로 멈추지 않고 경계를 검증함
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import traceback
from collections.abc import Callable

APP_LOGGER_NAME: str = "src"
STALL_THRESHOLD_SEC: float = 0.5
WATCHDOG_POLL_INTERVAL_SEC: float = 0.1
STALL_LOG_TEMPLATE: str = (
    "화면 스레드가 {elapsed:.2f}초 동안 응답하지 않았습니다. 그때의 호출 스택:\n{stack}"
)
NO_STACK_TEXT: str = "호출 스택을 읽지 못했습니다."
WATCHDOG_THREAD_NAME: str = "video_scripts_stall_watchdog"
STOP_JOIN_TIMEOUT_SEC: float = 1.0

_current_frames = sys._current_frames  # pyright: ignore[reportPrivateUsage]

# - 감시 스레드가 임계값을 넘겼다고 판단할 때 부르는 기록 함수
StallSink = Callable[[float, str], None]


def default_sink(elapsed_sec: float, stack_text: str) -> None:
    """멈춤 한 건을 개발 로그 WARNING으로 남김"""
    logging.getLogger(APP_LOGGER_NAME).warning(
        STALL_LOG_TEMPLATE.format(elapsed=elapsed_sec, stack=stack_text)
    )


def main_thread_stack() -> str:
    """메인 스레드의 현재 호출 스택 문구 반환. 프레임을 얻지 못하면 안내 문구"""
    ident = threading.main_thread().ident
    frame = _current_frames().get(ident) if ident is not None else None
    if frame is None:
        return NO_STACK_TEXT
    return "".join(traceback.format_stack(frame))


class StallDetector:
    """하트비트 간격을 재어 임계값을 넘은 구간을 한 번만 보고하는 감시기

    - 첫 하트비트 전에는 시간을 세지 않음(감시를 켠 시점의 준비 시간을 멈춤으로 오인하지 않게 함)
    - 한 번 멈춤을 보고한 뒤 하트비트가 다시 오면 새 구간으로 되돌림
    - 임계값 이하로 유지되는 동안에는 아무 기록도 하지 않음
    """

    def __init__(
        self,
        *,
        threshold: float = STALL_THRESHOLD_SEC,
        clock: Callable[[], float] | None = None,
        stack_provider: Callable[[], str] = main_thread_stack,
        sink: StallSink = default_sink,
    ) -> None:
        self._threshold = max(0.0, threshold)
        self._clock = clock or time.perf_counter
        self._stack_provider = stack_provider
        self._sink = sink
        self._last_beat: float = self._clock()
        self._started = False
        self._reported = False

    @property
    def is_reported(self) -> bool:
        """진행 중인 멈춤을 이미 보고했는지 여부"""
        return self._reported

    def beat(self) -> None:
        """메인 스레드가 살아 있음을 알리고 보고 상태를 새 구간으로 되돌림"""
        self._last_beat = self._clock()
        self._started = True
        self._reported = False

    def check(self) -> str | None:
        """임계값을 넘긴 첫 확인이면 스택을 남기고 남긴 문구 반환. 그 밖에는 None"""
        if not self._started:
            return None
        elapsed = self._clock() - self._last_beat
        if elapsed < self._threshold or self._reported:
            return None
        stack_text = self._stack_provider()
        self._reported = True
        self._sink(elapsed, stack_text)
        return STALL_LOG_TEMPLATE.format(elapsed=elapsed, stack=stack_text)


class MainThreadWatchdog:
    """감시 스레드를 띄워 `StallDetector.check`를 주기적으로 부르는 감시자

    - 종료는 `threading.Event`로 알리고 스레드를 데몬으로 두어 앱 종료를 막지 않음
    - 감시 스레드는 Qt 객체를 만지지 않음
    """

    def __init__(
        self,
        detector: StallDetector,
        *,
        poll_interval: float = WATCHDOG_POLL_INTERVAL_SEC,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._detector = detector
        self._poll_interval = max(0.001, poll_interval)
        self._stop_event = stop_event or threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        """감시 스레드가 살아 있는지 여부"""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """감시 스레드를 한 번만 띄움"""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run, name=WATCHDOG_THREAD_NAME, daemon=True
        )
        self._thread.start()

    def run(self) -> None:
        """감시 스레드 본문: 정지 신호 전까지 주기적으로 확인"""
        while not self._stop_event.wait(self._poll_interval):
            self._detector.check()

    def beat(self) -> None:
        """메인 스레드의 하트비트를 감시기에 전달"""
        self._detector.beat()

    def stop(self) -> None:
        """정지 신호를 보내고 스레드 종료를 기다림. 두 번 호출해도 안전"""
        self._stop_event.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=STOP_JOIN_TIMEOUT_SEC)
