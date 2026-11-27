"""공용 샘플 구간 실행 엔진(encode_sample_engine) 단위 테스트"""

import os
import threading
import unittest
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from src.core.command_history import STAGE_SAMPLE_ENCODE, clear, snapshot
from src.core.config import CONTAINER_POLICY_MP4
from src.core.encode.encode_plan import build_encode_plan
from src.core.encode.encode_process import ProcessOutcome
from src.core.encode.ffmpeg_command import build_sample_ffmpeg_args
from src.core.encode.stream_layout import parse_stream_layout
from src.service.encode.encode_pause import PauseControl
from src.service.encode.encode_sample_engine import (
    SampleGroupProcess,
    run_sample_pass,
    sample_segment_path,
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


class _StrictFakeProcess(_FakeProcess):
    """시작 전 일시중지를 거부하고 호출 순서를 기록하는 가짜 프로세스"""

    def __init__(self, events: list[str]) -> None:
        super().__init__([], 0, 0)
        self._events = events
        self._started = False

    def start(self) -> None:
        self._started = True
        self._events.append("start")

    def pause(self) -> None:
        if not self._started:
            raise RuntimeError("인코딩 프로세스가 아직 시작되지 않았습니다")
        self._events.append("pause")

    def cancel(self) -> None:
        self._events.append("cancel")

    def resume(self) -> None:
        self._events.append("resume")


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

    def test_sample_segment_path_matches_engine_output(self) -> None:
        """엔진이 산출물을 남기는 경로와 공용 경로 함수가 같은지 검증

        - 지표 측정이 이 함수로 읽으므로 두 이름이 어긋나면 모든 측정이 입력 부재로 실패
        """
        with TemporaryDirectory() as tmp:
            produced: list[str] = []

            def _factory(args: Sequence[str], _on_line: object) -> _FakeProcess:
                produced.append(args[-1])
                return _FakeProcess(args, 1, 0)

            outcome = run_sample_pass(
                batch_id=1,
                source_path=os.path.join(tmp, "in.mp4"),
                plan=_plan(tmp),
                ffmpeg_exec="ffmpeg",
                process_factory=_factory,
                abort=threading.Event(),
                segments=((0.0, 2.0), (4.0, 2.0)),
                quality=22.0,
                build_args=build_sample_ffmpeg_args,
                output_dir=tmp,
            )
            self.assertEqual(outcome.succeeded_segments, 2)
            expected = [sample_segment_path(tmp, index) for index in range(2)]
            self.assertEqual(sorted(produced), sorted(expected))
            self.assertEqual(os.path.basename(expected[0]), "seg_0.mp4")
            for path in expected:
                self.assertTrue(os.path.exists(path))

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

            def start(self) -> None:
                events.append("start")

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
        self.assertTrue(group.launch(proc))
        group.pause()
        group.resume()
        group.cancel()
        self.assertIsNone(group.pid)
        self.assertEqual(group.wait().returncode, 0)
        self.assertEqual(events, ["start", "pause", "resume", "cancel"])
        self.assertFalse(group.launch(_StubProc()))
        self.assertTrue(group.is_cancelled)

    def test_process_started_after_pause_is_paused_once_and_resumed_once(self) -> None:
        """일시중지 중에 시작한 프로세스는 한 번 멈추고, 한 번의 재개로 한 번만 되돌아오는지 검증"""
        events: list[str] = []
        group = SampleGroupProcess()
        group.pause()
        self.assertTrue(group.launch(_StrictFakeProcess(events)))
        self.assertEqual(events, ["start", "pause"])
        group.resume()
        self.assertEqual(events, ["start", "pause", "resume"])
        group.resume()
        self.assertEqual(events, ["start", "pause", "resume"])

    def test_pause_and_resume_cover_only_running_processes(self) -> None:
        """일시중지는 실행 중인 프로세스와 이후 시작한 프로세스 모두에 한 번씩만 적용되는지 검증"""
        events: list[str] = []
        group = SampleGroupProcess()
        first = _StrictFakeProcess(events)
        self.assertTrue(group.launch(first))
        group.pause()
        self.assertTrue(group.launch(_StrictFakeProcess(events)))
        group.resume()
        self.assertEqual(events, ["start", "pause", "start", "pause", "resume", "resume"])

    def test_cancel_before_start_skips_process_start(self) -> None:
        """시작 전에 취소된 묶음은 프로세스를 띄우지 않고 즉시 취소하는지 검증"""
        events: list[str] = []
        group = SampleGroupProcess()
        group.cancel()
        self.assertFalse(group.launch(_StrictFakeProcess(events)))
        self.assertEqual(events, ["cancel"])

    def test_started_segment_records_command_with_exit_code(self) -> None:
        """시작한 구간은 명령 기록에 종료 코드와 함께 한 건 남는지 검증"""
        with TemporaryDirectory() as tmp:
            clear()
            outcome = run_sample_pass(
                batch_id=1,
                source_path=os.path.join(tmp, "in.mp4"),
                plan=_plan(tmp),
                ffmpeg_exec="ffmpeg",
                process_factory=lambda _args, _cb: _FakeProcess(_args, 1, 0),
                abort=threading.Event(),
                segments=((0.0, 2.0),),
                quality=22.0,
                build_args=build_sample_ffmpeg_args,
            )
            records = snapshot()
        self.assertEqual(outcome.succeeded_segments, 1)
        self.assertEqual([(record.stage, record.exit_code) for record in records], [(STAGE_SAMPLE_ENCODE, 0)])

    def test_cancel_in_creation_gap_does_not_start_ffmpeg(self) -> None:
        """프로세스 생성과 시작 사이에 취소가 들어와도 FFmpeg를 띄우거나 명령 기록을 남기지 않는지 검증"""
        events: list[str] = []
        abort = threading.Event()
        control = PauseControl(abort=abort, clock=lambda: 0.0, notify=lambda _batch, _path: None)
        with TemporaryDirectory() as tmp:

            def _factory(_args: Sequence[str], _cb: object) -> _StrictFakeProcess:
                control.cancel_all()
                return _StrictFakeProcess(events)

            clear()
            outcome = run_sample_pass(
                batch_id=1,
                source_path=os.path.join(tmp, "in.mp4"),
                plan=_plan(tmp),
                ffmpeg_exec="ffmpeg",
                process_factory=_factory,
                abort=abort,
                segments=((0.0, 2.0),),
                quality=22.0,
                build_args=build_sample_ffmpeg_args,
                control=control,
            )
        self.assertTrue(outcome.cancelled)
        self.assertEqual(events, ["cancel"])
        self.assertEqual(snapshot(), ())


if __name__ == "__main__":
    unittest.main(verbosity=2)
