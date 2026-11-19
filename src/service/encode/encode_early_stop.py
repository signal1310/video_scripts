"""절약 목적 항목의 진행 중 조기 중단 감시(결정 29 A)

- 진행 이벤트를 다음 싱크로 넘기면서 추정 크기를 판정하고, 중단이 필요하면 요청 함수를 한 번 호출
- 워커 스레드에서 호출되므로 화면 객체에 접근하지 않음
"""

from collections.abc import Callable

from src.core.encode.encode_progress import EarlyStopHit, EarlyStopJudge
from src.service.encode.encode_events import EVENT_PROGRESS, EncodeEvent, EventSink
from src.service.encode.encode_job import EncodeJob

EARLY_STOP_REASON: str = "진행 중 중단, 예상 크기가 원본보다 큼"
EarlyStopRequest = Callable[[EarlyStopHit], bool]


class EarlyStopWatch:
    """진행 이벤트를 전달하면서 조기 중단 조건을 확인하는 감시 싱크

    - 요청이 수락되면 이후 이벤트는 판정하지 않음
    - 요청이 거부되면(일시중지, 즉시 중지, 항목 없음) 다음 재확인 시각에 다시 판정
    """

    def __init__(self, *, forward: EventSink, judge: EarlyStopJudge, request_stop: EarlyStopRequest) -> None:
        self._forward: EventSink = forward
        self._judge: EarlyStopJudge = judge
        self._request_stop: EarlyStopRequest = request_stop
        self._stop_accepted: bool = False

    @classmethod
    def for_job(cls, *, forward: EventSink, job: EncodeJob, request_stop: EarlyStopRequest) -> "EarlyStopWatch":
        """절약 목적 항목의 감시 싱크를 만듦. 판정 기준은 원본 크기와 영상 길이"""
        judge = EarlyStopJudge(source_size=job.source_size, duration_sec=job.source_duration_sec)
        return cls(forward=forward, judge=judge, request_stop=request_stop)

    def __call__(self, event: EncodeEvent) -> None:
        """이벤트를 전달한 뒤 진행 샘플이면 판정"""
        self._forward(event)
        if self._stop_accepted or event.kind != EVENT_PROGRESS or event.sample is None:
            return
        hit = self._judge.observe(event.sample, event.elapsed_sec)
        if hit is not None:
            self._stop_accepted = self._request_stop(hit)
