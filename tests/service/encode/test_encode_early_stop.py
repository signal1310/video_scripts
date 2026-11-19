"""조기 중단 감시 싱크 검증: 진행 이벤트 전달, 판정 호출 조건, 요청 수락과 거부, 작업 항목에서 감시 생성"""

import unittest

from src.core.encode.encode_gate import GatePurpose
from src.core.encode.encode_progress import EarlyStopHit, EarlyStopJudge, ProgressSample
from src.core.encode.encode_state import EncodeState
from src.service.encode.encode_early_stop import EarlyStopRequest, EarlyStopWatch
from src.service.encode.encode_events import EVENT_PROGRESS, EVENT_STATE, EncodeEvent
from src.service.encode.encode_job import EncodeJob
from src.service.encode.encode_reasons import EARLY_STOP_REASON

SOURCE_SIZE: int = 1000
DURATION_SEC: float = 10.0


def _progress_event(*, out_time: float | None = 1.0, size: int = 120, elapsed: float = 1.0) -> EncodeEvent:
    """out_time 1.0이면 진행 0.10, 크기 120이면 추정 1200(원본 x 1.20과 같음)인 진행 샘플 이벤트"""
    sample = ProgressSample(
        frame=None,
        fps=None,
        quality=None,
        bitrate_kbps=None,
        total_size_bytes=size,
        out_time_sec=out_time,
        speed=None,
        finished=False,
    )
    return EncodeEvent(kind=EVENT_PROGRESS, batch_id=1, source_path="a.mp4", sample=sample, elapsed_sec=elapsed)


def _recording_request(answers: list[bool], hits: list[EarlyStopHit]) -> EarlyStopRequest:
    """요청마다 판정 결과를 기록하고, 미리 정한 응답(없으면 수락)을 돌려주는 요청 함수"""

    def request(hit: EarlyStopHit) -> bool:
        hits.append(hit)
        return answers.pop(0) if answers else True

    return request


def _watch(answers: list[bool], forwarded: list[EncodeEvent], hits: list[EarlyStopHit]) -> EarlyStopWatch:
    judge = EarlyStopJudge(source_size=SOURCE_SIZE, duration_sec=DURATION_SEC)
    return EarlyStopWatch(forward=forwarded.append, judge=judge, request_stop=_recording_request(answers, hits))


class TestEarlyStopWatch(unittest.TestCase):
    """이벤트 전달과 판정 호출, 요청 결과에 따른 재요청 여부"""

    def test_every_event_is_forwarded_and_only_progress_is_judged(self) -> None:
        """상태 이벤트는 전달만 하고, 샘플이 없는 진행 이벤트는 판정하지 않음"""
        forwarded: list[EncodeEvent] = []
        hits: list[EarlyStopHit] = []
        watch = _watch([], forwarded, hits)
        watch(EncodeEvent(kind=EVENT_STATE, batch_id=1, source_path="a.mp4", state=EncodeState.RUNNING))
        watch(EncodeEvent(kind=EVENT_PROGRESS, batch_id=1, source_path="a.mp4"))
        self.assertEqual(len(forwarded), 2)
        self.assertEqual(hits, [])

    def test_frame_only_sample_is_forwarded_but_never_judged(self) -> None:
        """out_time이 없는 샘플은 프레임 수가 있어도 판정하지 않음"""
        forwarded: list[EncodeEvent] = []
        hits: list[EarlyStopHit] = []
        watch = _watch([], forwarded, hits)
        watch(_progress_event(out_time=None, size=5000, elapsed=1.0))
        self.assertEqual(len(forwarded), 1)
        self.assertEqual(hits, [])

    def test_hit_is_requested_once_and_accepted_request_stops_judgement(self) -> None:
        """판정이 중단을 요청하면 한 번 요청하고, 수락되면 이후 샘플은 판정하지 않음"""
        forwarded: list[EncodeEvent] = []
        hits: list[EarlyStopHit] = []
        watch = _watch([True], forwarded, hits)
        watch(_progress_event(elapsed=1.0))
        watch(_progress_event(elapsed=2.0))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].estimate_bytes, 1200)
        self.assertEqual(len(forwarded), 2)

    def test_refused_request_is_judged_again_at_next_hit(self) -> None:
        """중단 요청이 거부되면(일시중지 등) 다음 재확인 시각에 다시 요청"""
        forwarded: list[EncodeEvent] = []
        hits: list[EarlyStopHit] = []
        watch = _watch([False, True], forwarded, hits)
        watch(_progress_event(elapsed=1.0))
        watch(_progress_event(elapsed=2.0))
        watch(_progress_event(elapsed=3.0))
        self.assertEqual(len(hits), 2)

    def test_reason_text_is_the_agreed_phrase(self) -> None:
        """사유 문구는 승인된 문구와 같음"""
        self.assertEqual(EARLY_STOP_REASON, "진행 중 중단, 예상 크기가 원본보다 큼")

    def test_for_job_uses_source_size_and_duration(self) -> None:
        """작업 항목의 원본 크기와 영상 길이로 판정 기준을 만듦"""
        job = EncodeJob(
            filename="a.mp4",
            root_dir="/tmp",
            source_path="/tmp/a.mp4",
            source_size=SOURCE_SIZE,
            source_mtime_ns=1,
            source_duration_sec=DURATION_SEC,
            source_ext="mp4",
            fps=30.0,
            quality=22.0,
            purpose=GatePurpose.SAVING,
            output_size=None,
            scale_filter=None,
            maxrate_kbps=None,
        )
        hits: list[EarlyStopHit] = []
        watch = EarlyStopWatch.for_job(forward=lambda _event: None, job=job, request_stop=_recording_request([], hits))
        watch(_progress_event(out_time=1.0, size=120, elapsed=1.0))
        self.assertEqual(len(hits), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
