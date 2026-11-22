"""공용 샘플 구간 실행 엔진(encode_sample_engine) 단위 테스트"""

import os
import threading
import unittest
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from src.core.config import CONTAINER_POLICY_MP4
from src.core.encode.encode_plan import build_encode_plan
from src.core.encode.encode_process import ProcessOutcome
from src.core.encode.ffmpeg_command import build_sample_ffmpeg_args
from src.core.encode.stream_layout import parse_stream_layout
from src.service.encode.encode_sample_engine import (
    SampleGroupProcess,
    run_sample_pass,
)


class _FakeProcess:
    """샘플 출력 파일을 지정 크기로 만들고 실패 여부를 흉내 내는 가짜 프로세스"""

    def __init__(self, args: Sequence[str], size: int, returncode: int) -> None:
        self._args = list(args)
        self._size = size
        self._returncode = returncode
        self._cancelled = False

    def start(self) -> None:
        Path(self._args[-1]).write_bytes(b"x" * self._size)

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        if self._cancelled:
            return ProcessOutcome(returncode=None, cancelled=True, stderr_tail=())
        return ProcessOutcome(returncode=self._returncode, cancelled=False, stderr_tail=())

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


def _plan(tmp: str):
    layout = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc"}]})
    return build_encode_plan(
        source_path=os.path.join(tmp, "in.mp4"),
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


class TestSampleEngine(unittest.TestCase):
    """샘플 구간 병렬 실행의 합계, 실패 구간, 취소, 일시중지 전파를 검증"""

    def test_successful_segments_sum_bytes_and_report_progress(self) -> None:
        with TemporaryDirectory() as tmp:
            progress: list[int] = []
            outcome = run_sample_pass(
                batch_id=1,
                source_path=os.path.join(tmp, "in.mp4"),
                plan=_plan(tmp),
                ffmpeg_exec="ffmpeg",
                process_factory=lambda _args, _cb: _FakeProcess(_args, 7, 0),
                abort=threading.Event(),
                segments=((0.0, 2.0), (4.0, 2.0), (8.0, 2.0)),
                quality=22.0,
                build_args=build_sample_ffmpeg_args,
                on_segment_done=progress.append,
            )
            self.assertEqual(outcome.total_bytes, 21)
            self.assertEqual(outcome.succeeded_segments, 3)
            self.assertEqual(outcome.segment_count, 3)
            self.assertFalse(outcome.cancelled)
            self.assertEqual(sorted(progress), [1, 2, 3])

    def test_failed_segment_is_not_counted_as_success(self) -> None:
        with TemporaryDirectory() as tmp:
            outcome = run_sample_pass(
                batch_id=1,
                source_path=os.path.join(tmp, "in.mp4"),
                plan=_plan(tmp),
                ffmpeg_exec="ffmpeg",
                process_factory=lambda _args, _cb: _FakeProcess(_args, 5, 1),
                abort=threading.Event(),
                segments=((0.0, 2.0), (4.0, 2.0)),
                quality=22.0,
                build_args=build_sample_ffmpeg_args,
            )
            self.assertEqual(outcome.total_bytes, 0)
            self.assertEqual(outcome.succeeded_segments, 0)
            self.assertEqual(outcome.segment_count, 2)

    def test_abort_returns_cancelled_without_running(self) -> None:
        with TemporaryDirectory() as tmp:
            abort = threading.Event()
            abort.set()
            calls: list[int] = []
            outcome = run_sample_pass(
                batch_id=1,
                source_path=os.path.join(tmp, "in.mp4"),
                plan=_plan(tmp),
                ffmpeg_exec="ffmpeg",
                process_factory=lambda _args, _cb: calls.append(1) or _FakeProcess(_args, 1, 0),
                abort=abort,
                segments=((0.0, 2.0),),
                quality=22.0,
                build_args=build_sample_ffmpeg_args,
            )
            self.assertTrue(outcome.cancelled)
            self.assertEqual(calls, [])

    def test_sample_group_process_propagates_pause_resume_and_cancel(self) -> None:
        """SampleGroupProcess가 등록된 프로세스에 일시중지, 재개, 취소를 전파하는지 검증"""
        events: list[str] = []

        class _StubProc(_FakeProcess):
            def __init__(self) -> None:
                super().__init__(["python", "out.mp4"], 0, 0)

            def cancel(self) -> None:
                events.append("cancel")

            def pause(self) -> None:
                events.append("pause")

            def resume(self) -> None:
                events.append("resume")

        group = SampleGroupProcess()
        proc = _StubProc()
        group.start()
        group.set_priority("normal")
        self.assertTrue(group.register(proc))
        group.pause()
        group.resume()
        group.cancel()
        self.assertIsNone(group.pid)
        self.assertEqual(group.wait().returncode, 0)
        self.assertEqual(events, ["pause", "resume", "cancel"])
        self.assertFalse(group.register(_StubProc()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
