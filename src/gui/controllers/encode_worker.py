"""인코딩 배치를 실행하는 전용 워커 스레드와 큐 항목 값(쟁점 C)

- 워커는 Qt 객체에 접근하지 않고 큐에만 항목을 넣음. 큐는 화면 스레드의 타이머가 비움
- 워커 본문은 run_batch_body로 분리해 스레드 없이 직접 테스트
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PyQt6.QtCore import QObject, QThread

from src.service.encode.encode_events import EncodeEvent

logger = logging.getLogger(__name__)


class BatchRunner(Protocol):
    """배치 한 건을 끝까지 실행하는 실행기 인터페이스"""

    def run(self, batch_id: int) -> None:
        """배치를 끝까지 실행"""


@dataclass(frozen=True, slots=True)
class BatchFinished:
    """배치 실행이 끝났음을 알리는 큐 항목. error가 빈 문자열이면 예외 없이 끝남"""

    batch_id: int
    error: str


QueueItem = EncodeEvent | BatchFinished
QueueSink = Callable[[QueueItem], None]


def run_batch_body(runner: BatchRunner, batch_id: int, sink: QueueSink) -> None:
    """배치를 실행하고 종료 항목을 보냄

    - 스레드 경계에서 예외를 잡지 않으면 화면이 종료 항목을 받지 못해 실행 중 상태에 머무르므로 모두 잡음
    """
    try:
        runner.run(batch_id)
    except Exception as error:
        logger.exception("인코딩 배치 실행이 중단되었습니다: %s", batch_id)
        sink(BatchFinished(batch_id, str(error)))
        return
    sink(BatchFinished(batch_id, ""))


class EncodeWorker(QThread):
    """인코딩 배치를 실행하는 전용 스레드"""

    def __init__(self, runner: BatchRunner, batch_id: int, sink: QueueSink, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner: BatchRunner = runner
        self._batch_id: int = batch_id
        self._sink: QueueSink = sink

    def run(self) -> None:
        """워커 스레드 본문: 배치를 실행하고 종료 항목을 큐에 넣음"""
        run_batch_body(self._runner, self._batch_id, self._sink)
