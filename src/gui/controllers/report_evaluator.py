"""캐시 재평가를 백그라운드 스레드에서 실행하고 최신 요청의 결과만 GUI 스레드에서 적용하는 모듈"""

import logging
import threading
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from src.core.models import SummaryReport

_logger = logging.getLogger(__name__)


class ReportEvaluator(QObject):
    """재평가 작업을 스레드에서 돌리고, 더 늦게 요청된 작업이 끝나면 앞선 결과는 버림

    워커 스레드는 Qt 객체에 접근하지 않고 결과를 시그널로만 넘김
    """

    _ready = pyqtSignal(int, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._latest: int = 0
        self._on_done: Callable[[SummaryReport], None] | None = None
        self._threads: list[threading.Thread] = []
        self._closed: bool = False
        self._ready.connect(self._on_ready)

    @property
    def pending(self) -> bool:
        """결과 적용을 기다리는 재평가 요청이 있으면 True"""
        return self._on_done is not None

    def submit(
        self,
        job: Callable[[], SummaryReport],
        on_done: Callable[[SummaryReport], None],
    ) -> None:
        """작업을 백그라운드에서 시작하고, 완료 시 최신 요청이면 on_done에 결과를 전달"""
        if self._closed:
            return
        self._latest += 1
        self._on_done = on_done
        self._threads = [t for t in self._threads if t.is_alive()]
        thread = threading.Thread(target=self._run, args=(self._latest, job), daemon=True)
        self._threads.append(thread)
        thread.start()

    def shutdown(self) -> None:
        """창 종료 때 진행 중 작업을 기다리고 결과는 버림

        - 작업이 분석 DB 연결을 쥐고 있으므로, 반환 뒤에야 창이 닫은 저장소를 다시 여는 일이 없음
        - 작업 시간만큼 창 닫기가 기다림
        """
        self._closed = True
        self._on_done = None
        for thread in self._threads:
            thread.join()
        self._threads.clear()

    def _run(self, seq: int, job: Callable[[], SummaryReport]) -> None:
        try:
            report = job()
        except Exception:
            _logger.exception("캐시 재평가 작업 실패")
            return
        self._ready.emit(seq, report)

    def _on_ready(self, seq: int, report: object) -> None:
        if self._closed or seq != self._latest or self._on_done is None or not isinstance(report, SummaryReport):
            return
        callback, self._on_done = self._on_done, None
        callback(report)
