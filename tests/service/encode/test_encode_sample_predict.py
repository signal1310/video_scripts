"""결정 29 B 절약 목적 영상 샘플 크기 예측(encode_sample_predict) 단위 테스트"""

import os
import threading
import unittest
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from src.core.config import CONTAINER_POLICY_MP4
from src.core.encode.encode_gate import GatePurpose
from src.core.encode.encode_plan import EncodePlan, build_encode_plan
from src.core.encode.encode_process import ProcessOutcome
from src.core.encode.ffmpeg_command import build_sample_ffmpeg_args
from src.core.encode.stream_layout import StreamLayout, parse_stream_layout
from src.service.encode.encode_events import EVENT_PHASE, PHASE_SAMPLE_PREDICT, EncodeEvent, EventSink
from src.service.encode.encode_job import EncodeJob
from src.service.encode.encode_sample_predict import (
    SAMPLE_SAVING_DISCARD_FACTOR,
    predict_saving_size,
)

_SOURCE_BYTES = 30_000_000
_SOURCE_KBPS = 4_000
_DURATION_SEC = 60.0


class _FakeProcess:
    """샘플 출력 파일을 지정 크기로 만들고 지정한 구간만 실패시키는 가짜 프로세스"""

    def __init__(self, args: Sequence[str], size: int, fail_names: frozenset[str] = frozenset()) -> None:
        self._args = list(args)
        self._size = size
        self._fail = os.path.basename(self._args[-1]) in fail_names
        self._cancelled = False

    def start(self) -> None:
        Path(self._args[-1]).write_bytes(b"x" * self._size)

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        if self._cancelled:
            return ProcessOutcome(returncode=None, cancelled=True, stderr_tail=())
        return ProcessOutcome(returncode=1 if self._fail else 0, cancelled=False, stderr_tail=())

    def cancel(self) -> None:
        self._cancelled = True

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def set_priority(self, name: str) -> None:
        pass

    @property
    def pid(self) -> int | None:
        return None


def _inputs(tmp: str) -> tuple[EncodeJob, StreamLayout, EncodePlan]:
    layout = parse_stream_layout(
        {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "bit_rate": str(_SOURCE_KBPS * 1000)}]}
    )
    source = os.path.join(tmp, "in.mp4")
    job = EncodeJob(
        filename="in.mp4",
        root_dir=tmp,
        source_path=source,
        source_size=_SOURCE_BYTES,
        source_mtime_ns=1,
        source_duration_sec=_DURATION_SEC,
        source_ext="mp4",
        fps=30.0,
        quality=22.0,
        purpose=GatePurpose.SAVING,
        output_size=None,
        scale_filter=None,
        maxrate_kbps=None,
    )
    plan = build_encode_plan(
        source_path=source,
        part_path=os.path.join(tmp, "out.mp4.part"),
        output_path=os.path.join(tmp, "out.mp4"),
        source_ext="mp4",
        encoder="hevc_nvenc",
        quality=22.0,
        fps=30.0,
        layout=layout,
        container_policy=CONTAINER_POLICY_MP4,
        tag_value="v=1",
    )
    return job, layout, plan


class TestSampleSizePrediction(unittest.TestCase):
    """샘플 합계로 전체 크기를 예측하고, 초과, 실패, 취소를 보수적으로 판정하는지 검증"""

    def _predict(self, tmp: str, size: int, *, fail_names: frozenset[str] = frozenset(), abort: threading.Event | None = None, emit: EventSink | None = None):
        job, layout, plan = _inputs(tmp)
        return predict_saving_size(
            batch_id=1,
            job=job,
            plan=plan,
            layout=layout,
            ffmpeg_exec="ffmpeg",
            process_factory=lambda args, _cb: _FakeProcess(args, size, fail_names),
            abort=abort if abort is not None else threading.Event(),
            build_args=build_sample_ffmpeg_args,
            emit=emit,
        )

    def test_prediction_below_factor_keeps_encoding(self) -> None:
        with TemporaryDirectory() as tmp:
            prediction = self._predict(tmp, 1_000_000)
            assert prediction is not None
            self.assertEqual(prediction.succeeded_segments, 5)
            self.assertEqual(prediction.total_segments, 5)
            self.assertAlmostEqual(prediction.sample_duration_sec, 10.0)
            self.assertEqual(prediction.predicted_video_bytes, _SOURCE_BYTES)
            self.assertEqual(prediction.predicted_total_bytes, _SOURCE_BYTES)
            self.assertFalse(prediction.exceeds_original)

    def test_prediction_over_discard_factor_marks_exceeds(self) -> None:
        with TemporaryDirectory() as tmp:
            prediction = self._predict(tmp, 1_200_000)
            assert prediction is not None
            expected_total = 6_000_000 * int(_DURATION_SEC / 10.0)
            self.assertEqual(prediction.predicted_total_bytes, expected_total)
            self.assertGreaterEqual(expected_total, round(_SOURCE_BYTES * SAMPLE_SAVING_DISCARD_FACTOR))
            self.assertTrue(prediction.exceeds_original)

    def test_partial_sample_failure_proceeds_conservatively(self) -> None:
        with TemporaryDirectory() as tmp:
            prediction = self._predict(tmp, 1, fail_names=frozenset({"seg_0.mp4"}))
            assert prediction is not None
            self.assertEqual(prediction.succeeded_segments, 4)
            self.assertEqual(prediction.total_segments, 5)
            self.assertEqual(prediction.predicted_total_bytes, _SOURCE_BYTES)
            self.assertFalse(prediction.exceeds_original)

    def test_cancelled_sample_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            abort = threading.Event()
            abort.set()
            self.assertIsNone(self._predict(tmp, 1, abort=abort))

    def test_emit_reports_sample_predict_phase_per_segment(self) -> None:
        """샘플 확인 단계 이벤트가 구간 완료 수와 목표 품질값을 담아 나가는지 검증"""
        events: list[EncodeEvent] = []
        with TemporaryDirectory() as tmp:
            prediction = self._predict(tmp, 1_000_000, emit=events.append)
        assert prediction is not None
        self.assertEqual(len(events), 5)
        phase = events[0].phase
        assert phase is not None
        self.assertEqual(events[0].kind, EVENT_PHASE)
        self.assertEqual(phase.stage, PHASE_SAMPLE_PREDICT)
        self.assertEqual(phase.segment_total, 5)
        self.assertEqual(phase.baseline_quality, 22.0)
        self.assertEqual(phase.current_quality, 22.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
