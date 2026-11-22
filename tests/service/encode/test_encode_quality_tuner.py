"""키프레임 교정 품질값 자동 조정 서비스(encode_quality_tuner) 단위 테스트"""

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
from src.core.encode.encode_progress import ProgressSample
from src.core.encode.ffmpeg_command import build_sample_ffmpeg_args
from src.core.encode.stream_layout import parse_stream_layout
from src.service.encode.encode_events import EVENT_PHASE, EVENT_PROGRESS, EncodeEvent
from src.service.encode.encode_job import EncodeJob
from src.service.encode.encode_outcome import EncodeAttempt
from src.service.encode.encode_pause import PauseControl
from src.service.encode.encode_quality_tuner import RetryWatch, tune_and_encode
from src.service.encode.encode_sample_engine import SampleGroupProcess


class _FakeSampleProcess:
    """샘플 출력 파일을 지정 크기로 만들어 주는 가짜 프로세스"""

    def __init__(self, args: Sequence[str], size_by_quality: dict[str, int]) -> None:
        self._args = list(args)
        self._size_by_quality = size_by_quality

    def start(self) -> None:
        out_path = self._args[-1]
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        q_idx = self._args.index("-cq:v:0") + 1
        q_val = self._args[q_idx]
        size = self._size_by_quality.get(q_val, 1_000_000)
        Path(out_path).write_bytes(b"\x00" * size)

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        return ProcessOutcome(returncode=0, cancelled=False, stderr_tail=())

    def cancel(self) -> None:
        pass

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def set_priority(self, name: str) -> None:
        pass

    @property
    def pid(self) -> int | None:
        return None


class TestEncodeQualityTuner(unittest.TestCase):
    """샘플 확인 2회 조정, 단계 이벤트, 본 인코딩 재시도 감시를 검증"""

    def test_tune_and_encode_runs_two_passes_and_retries_once(self) -> None:
        layout = parse_stream_layout(
            {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "bit_rate": "6000000"}]}
        )
        with TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.mp4")
            Path(src).write_bytes(b"x" * 50_000_000)
            job = EncodeJob(
                filename="in.mp4",
                root_dir=tmp,
                source_path=src,
                source_size=50_000_000,
                source_mtime_ns=1,
                source_duration_sec=60.0,
                source_ext="mp4",
                fps=30.0,
                quality=22.0,
                purpose=GatePurpose.KEYFRAME_FIX,
                output_size=None,
                scale_filter=None,
                maxrate_kbps=None,
                keyframe_increase_ratio=0.05,
            )
            plan = build_encode_plan(
                source_path=src,
                part_path=os.path.join(tmp, "out.mp4.part"),
                output_path=os.path.join(tmp, "out.mp4"),
                source_ext="mp4",
                encoder="hevc_nvenc",
                quality=22.0,
                fps=30.0,
                layout=layout,
                container_policy=CONTAINER_POLICY_MP4,
                tag_value="v=1;enc=hevc_nvenc;q=22.0",
            )
            events: list[EncodeEvent] = []
            # 1차(22.0) -> 5개 합계 11.5MB(전체 69MB 추정), 2차 -> 합계 6.25MB(전체 37.5MB 추정)
            size_map = {"22.0": 2_300_000}
            for q in [f"{x / 10:.1f}" for x in range(235, 281)]:
                size_map[q] = 1_250_000
            main_qualities: list[float] = []
            removed: list[str] = []

            def _run_main(active_plan: EncodePlan, watch: RetryWatch | None) -> EncodeAttempt:
                main_qualities.append(active_plan.quality)
                if watch is not None and len(main_qualities) == 1:
                    watch(
                        EncodeEvent(
                            kind=EVENT_PROGRESS,
                            batch_id=1,
                            source_path=src,
                            sample=ProgressSample(
                            frame=720,
                            fps=30.0,
                            quality=active_plan.quality,
                            bitrate_kbps=5000.0,
                            total_size_bytes=30_000_000,
                            out_time_sec=24.0,
                            speed=2.0,
                            finished=False,
                        ),
                        )
                    )
                    return EncodeAttempt(cancelled=True)
                return EncodeAttempt()

            final_plan, attempt = tune_and_encode(
                batch_id=1,
                job=job,
                plan=plan,
                layout=layout,
                ffmpeg_exec="ffmpeg",
                process_factory=lambda args, _cb: _FakeSampleProcess(args, size_map),
                abort=threading.Event(),
                emit=events.append,
                build_args=build_sample_ffmpeg_args,
                run_main=_run_main,
                cancel_for_retry=lambda: True,
                remove_part=removed.append,
            )
            self.assertEqual(len(main_qualities), 2)
            self.assertGreater(final_plan.quality, main_qualities[0])
            self.assertIn(f"q={final_plan.quality:.1f}", final_plan.tag_value)
            self.assertEqual(attempt.tune_retry_count, 1)
            self.assertEqual(len(removed), 1)
            self.assertIn("재시도 1회", attempt.tuning_reason)
            phase_stages = [e.phase.stage for e in events if e.kind == EVENT_PHASE and e.phase is not None]
            self.assertIn("sampling", phase_stages)
            self.assertIn("main", phase_stages)
            self.assertIn("retry", phase_stages)

    def test_sample_pass_cancels_running_processes_on_abort_and_cancel_one(self) -> None:
        """샘플 실행 중 전체 즉시 중지나 개별 중지를 받으면 실행 중인 샘플 프로세스를 취소하고 즉시 반환하는지 검증"""
        layout = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}]})
        for use_cancel_one in (False, True):
            with self.subTest(use_cancel_one=use_cancel_one), TemporaryDirectory() as tmp:
                src = os.path.join(tmp, "in.mp4")
                Path(src).write_bytes(b"x" * 10_000_000)
                job = EncodeJob(
                    filename="in.mp4", root_dir=tmp, source_path=src, source_size=10_000_000,
                    source_mtime_ns=1, source_duration_sec=60.0, source_ext="mp4", fps=30.0,
                    quality=22.0, purpose=GatePurpose.KEYFRAME_FIX, output_size=None,
                    scale_filter=None, maxrate_kbps=None,
                )
                plan = build_encode_plan(
                    source_path=src, part_path=os.path.join(tmp, "out.mp4.part"),
                    output_path=os.path.join(tmp, "out.mp4"), source_ext="mp4",
                    encoder="hevc_nvenc", quality=22.0, fps=30.0, layout=layout,
                    container_policy=CONTAINER_POLICY_MP4, tag_value="v=1;q=22.0",
                )
                abort = threading.Event()
                control = PauseControl(abort=abort, clock=lambda: 0.0, notify=lambda _b, _p: None)
                cancelled_procs: list[int] = []
                started_gate = threading.Event()

                class _BlockingSampleProcess:
                    def __init__(self, gate: threading.Event, cancelled: list[int]) -> None:
                        self._stop = threading.Event()
                        self._gate = gate
                        self._cancelled = cancelled

                    def start(self) -> None:
                        self._gate.set()

                    def wait(self, timeout: float | None = None) -> ProcessOutcome:
                        self._stop.wait(2.0)
                        return ProcessOutcome(returncode=None, cancelled=self._stop.is_set(), stderr_tail=())

                    def cancel(self) -> None:
                        self._cancelled.append(1)
                        self._stop.set()

                    def pause(self) -> None:
                        pass

                    def resume(self) -> None:
                        pass

                    def set_priority(self, name: str) -> None:
                        pass

                    @property
                    def pid(self) -> int | None:
                        return None

                def _trigger_stop(
                    gate: threading.Event = started_gate,
                    cancel_one: bool = use_cancel_one,
                    ctrl: PauseControl = control,
                    source: str = src,
                    ev: threading.Event = abort,
                ) -> None:
                    gate.wait(1.0)
                    if cancel_one:
                        ctrl.cancel_one(source)
                    else:
                        ev.set()
                        ctrl.cancel_all()

                stopper = threading.Thread(target=_trigger_stop, daemon=True)
                stopper.start()
                _plan, attempt = tune_and_encode(
                    batch_id=1, job=job, plan=plan, layout=layout, ffmpeg_exec="ffmpeg",
                    process_factory=lambda _args, _cb, g=started_gate, c=cancelled_procs: _BlockingSampleProcess(g, c),
                    abort=abort, emit=lambda _e: None, build_args=build_sample_ffmpeg_args,
                    run_main=lambda _p, _w: EncodeAttempt(), cancel_for_retry=lambda: True,
                    remove_part=lambda _p: None, control=control,
                )
                stopper.join(1.0)
                self.assertTrue(attempt.cancelled)
                self.assertGreaterEqual(len(cancelled_procs), 1)

    def test_sample_group_process_propagates_pause_and_resume(self) -> None:
        """SampleGroupProcess가 등록된 프로세스에 일시중지와 재개를 전파하는지 검증"""
        events: list[str] = []

        class _StubProc:
            def start(self) -> None:
                pass

            def wait(self, timeout: float | None = None) -> ProcessOutcome:
                return ProcessOutcome(returncode=0, cancelled=False, stderr_tail=())

            def cancel(self) -> None:
                events.append("cancel")

            def pause(self) -> None:
                events.append("pause")

            def resume(self) -> None:
                events.append("resume")

            def set_priority(self, name: str) -> None:
                pass

            @property
            def pid(self) -> int | None:
                return None

        group = SampleGroupProcess()
        proc = _StubProc()
        self.assertTrue(group.register(proc))
        group.pause()
        group.resume()
        self.assertIsNone(group.pid)
        self.assertEqual(group.wait().returncode, 0)
        self.assertEqual(events, ["pause", "resume"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
