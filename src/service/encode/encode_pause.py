"""재인코딩 실행 중 프로세스의 일시중지, 재개, 중지와 장시간 안내 타이머

- 실행 목록(항목 경로 -> 프로세스와 일시중지 제외 시계)을 이 클래스가 소유하고 잠금으로 보호
- 한 프로세스는 같은 상태에서 한 번만 멈춤. Windows는 멈춤 횟수가 쌓여 재개도 그 횟수만큼 필요함
- 장시간 안내 타이머는 동시에 하나만 두고, 일시중지가 모두 풀리면 취소함
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass

from src.core.encode.encode_progress import LONG_RUN_NOTICE_SEC, PausableClock, needs_long_run_notice
from src.service.encode.encode_ports import EncodeProcessLike

NoticeSink = Callable[[int, str], None]


@dataclass(slots=True)
class ActiveItem:
    """실행 중인 항목의 배치 번호, 프로세스, 일시중지 제외 시계"""

    batch_id: int
    process: EncodeProcessLike
    clock: PausableClock


class PauseControl:
    """실행 중인 프로세스 목록과 일시중지 상태를 관리

    - 워커 스레드(시작, 종료)와 화면 스레드(일시중지, 재개, 중지)가 함께 호출하므로 상태는 잠금 안에서만 바꿈
    - 장시간 안내는 notify(배치 번호, 경로)로 보냄. 타이머 스레드에서 호출될 수 있음
    """

    def __init__(self, *, abort: threading.Event, clock: Callable[[], float], notify: NoticeSink) -> None:
        self._abort: threading.Event = abort
        self._clock: Callable[[], float] = clock
        self._notify: NoticeSink = notify
        self._lock: threading.Lock = threading.Lock()
        self._active: dict[str, ActiveItem] = {}
        self._batch_paused: bool = False
        self._item_paused: set[str] = set()
        self._timer: threading.Timer | None = None
        self._generation: int = 0

    def launch(self, source: str, batch_id: int, process: EncodeProcessLike) -> bool:
        """프로세스를 시작해 실행 목록에 넣음. 즉시 중지 중이면 시작하지 않고 거짓. 전체 일시중지 중이면 바로 멈춤"""
        with self._lock:
            if self._abort.is_set():
                return False
            process.start()
            item = ActiveItem(batch_id=batch_id, process=process, clock=PausableClock(now=self._clock()))
            self._active[source] = item
            if self._batch_paused:
                process.pause()
                item.clock.pause(self._clock())
        return True

    def release(self, source: str) -> None:
        """끝난 프로세스를 실행 목록에서 뺌"""
        with self._lock:
            self._active.pop(source, None)
            self._item_paused.discard(source)

    def finish(self) -> None:
        """배치 실행이 끝나면 일시중지 상태를 지우고 안내 타이머를 취소. 다음 배치는 새 실행기에서 시작"""
        with self._lock:
            self._batch_paused = False
            self._item_paused.clear()
            timer = self._stop_timer_if_idle_locked()
        if timer is not None:
            timer.cancel()

    def elapsed(self, source: str) -> float:
        """일시중지 시간을 뺀 항목 경과 시간(초). 실행 중이 아니면 0"""
        with self._lock:
            item = self._active.get(source)
            return 0.0 if item is None else item.clock.elapsed(self._clock())

    def cancel_all(self) -> None:
        """실행 중인 모든 프로세스를 중지. 즉시 중지 표시는 호출자가 abort 이벤트로 먼저 설정"""
        with self._lock:
            for item in self._active.values():
                item.process.cancel()

    def cancel_one(self, source: str) -> None:
        """실행 중인 프로세스 하나를 중지하고 개별 일시중지 표기를 지움"""
        with self._lock:
            self._item_paused.discard(source)
            item = self._active.get(source)
        if item is not None:
            item.process.cancel()

    def cancel_if_running(self, source: str, on_accept: Callable[[], None]) -> bool:
        """일시중지가 아닌 실행 중 항목 하나를 중지. 받아들이면 on_accept를 먼저 부르고 참

        - 전체 또는 개별 일시중지, 즉시 중지 중, 실행 중이 아닌 항목은 거절하고 거짓
        """
        with self._lock:
            item = self._active.get(source)
            if item is None or self._is_suspended_locked(source) or self._abort.is_set():
                return False
            on_accept()
            item.process.cancel()
        return True

    def pause_all(self) -> None:
        """실행 중인 모든 프로세스를 일시중지하고 장시간 안내 타이머를 켬. 이미 개별로 멈춘 프로세스는 다시 멈추지 않음"""
        with self._lock:
            if self._batch_paused:
                return
            self._batch_paused = True
            now = self._clock()
            for source, item in self._active.items():
                if source not in self._item_paused:
                    item.process.pause()
                item.clock.pause(now)
        self._arm_timer()

    def pause_one(self, source: str) -> bool:
        """실행 중인 영상 하나만 일시중지하고 안내 타이머를 켬. 적용하면 참"""
        with self._lock:
            item = self._active.get(source)
            if item is None or self._batch_paused or source in self._item_paused:
                return False
            item.process.pause()
            item.clock.pause(self._clock())
            self._item_paused.add(source)
        self._arm_timer()
        return True

    def resume_all(self) -> None:
        """멈춘 프로세스를 모두 한 번씩 다시 실행. 개별 일시중지도 함께 풀림"""
        with self._lock:
            if not self._is_paused_locked():
                return
            now = self._clock()
            for source, item in self._active.items():
                if self._is_suspended_locked(source):
                    item.process.resume()
                    item.clock.resume(now)
            self._batch_paused = False
            self._item_paused.clear()
            timer = self._stop_timer_if_idle_locked()
        if timer is not None:
            timer.cancel()

    def resume_one(self, source: str) -> bool:
        """개별 일시중지한 영상 하나를 다시 실행. 전체 일시중지 중이거나 멈춘 적이 없으면 거짓"""
        with self._lock:
            item = self._active.get(source)
            if item is None or self._batch_paused or source not in self._item_paused:
                return False
            self._item_paused.discard(source)
            item.process.resume()
            item.clock.resume(self._clock())
            timer = self._stop_timer_if_idle_locked()
        if timer is not None:
            timer.cancel()
        return True

    def check_long_pause(self) -> None:
        """현재 일시중지 지속 시간이 안내 기준을 넘은 항목마다 안내를 보냄"""
        with self._lock:
            if not self._is_paused_locked():
                return
            now = self._clock()
            notices = [
                (item.batch_id, source)
                for source, item in self._active.items()
                if self._is_suspended_locked(source) and needs_long_run_notice(item.clock.current_pause_sec(now))
            ]
        for batch_id, source in notices:
            self._notify(batch_id, source)

    def _arm_timer(self) -> None:
        """일시중지가 있고 타이머가 없을 때만 안내 타이머를 하나 켬"""
        with self._lock:
            if self._timer is not None or not self._is_paused_locked():
                return
            timer = threading.Timer(LONG_RUN_NOTICE_SEC, self._on_timer, args=(self._generation,))
            timer.daemon = True
            self._timer = timer
        timer.start()

    def _on_timer(self, generation: int) -> None:
        """타이머가 울리면 안내를 보내고, 아직 멈춰 있으면 다음 타이머를 켬. 취소된 세대의 타이머는 무시"""
        with self._lock:
            if generation != self._generation:
                return
            self._timer = None
        self.check_long_pause()
        self._arm_timer()

    def _stop_timer_if_idle_locked(self) -> threading.Timer | None:
        """일시중지가 모두 풀렸으면 타이머를 떼어 돌려주고 세대를 올림. 호출자가 밖에서 취소함"""
        if self._is_paused_locked():
            return None
        timer = self._timer
        self._timer = None
        self._generation += 1
        return timer

    def _is_paused_locked(self) -> bool:
        return self._batch_paused or bool(self._item_paused)

    def _is_suspended_locked(self, source: str) -> bool:
        return self._batch_paused or source in self._item_paused
