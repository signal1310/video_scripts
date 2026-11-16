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
        self._ready.connect(self._on_ready)

    def submit(
        self,
        job: Callable[[], SummaryReport],
        on_done: Callable[[SummaryReport], None],
    ) -> None:
        """작업을 백그라운드에서 시작하고, 완료 시 최신 요청이면 on_done에 결과를 전달"""
        self._latest += 1
        self._on_done = on_done
        threading.Thread(target=self._run, args=(self._latest, job), daemon=True).start()

    def _run(self, seq: int, job: Callable[[], SummaryReport]) -> None:
        try:
            report = job()
        except Exception:
            _logger.exception("캐시 재평가 작업 실패")
            return
        self._ready.emit(seq, report)

    def _on_ready(self, seq: int, report: object) -> None:
        if seq != self._latest or self._on_done is None or not isinstance(report, SummaryReport):
            return
        callback, self._on_done = self._on_done, None
        callback(report)
