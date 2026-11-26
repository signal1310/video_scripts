"""명령 실행 지점마다 기록이 남는지 가짜 프로세스와 실행기로 검증"""

import os
import tempfile
import threading
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.analysis import prober
from src.core.analysis.frame_scan import scan_with_cli
from src.core.command_history import (
    STAGE_FRAME_ANALYSIS,
    STAGE_PROBING,
    STAGE_SAMPLE_ENCODE,
    STAGE_VMAF,
    clear,
    snapshot,
)
from src.core.config import CONTAINER_POLICY_MP4
from src.core.encode.encode_plan import EncodePlan, build_encode_plan
from src.core.encode.encode_process import ProcessOutcome
from src.core.encode.ffmpeg_command import build_sample_ffmpeg_args
from src.core.encode.stream_layout import parse_stream_layout
from src.core.exceptions import DomainRuntimeError
from src.core.quality_probe import METRIC_VMAF, encode_sample_segments, measure_metric
from src.service.encode.encode_sample_engine import run_sample_pass


def _plan(tmp: str) -> EncodePlan:
    layout = parse_stream_layout(
        {"streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc"}]}
    )
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


class ProberRecordingTests(unittest.TestCase):
    """ffprobe 실행이 프로빙 기록을 남기고, 실패한 경우에도 종료 기록이 남는지 검증"""

    def setUp(self) -> None:
        clear()

    def tearDown(self) -> None:
        clear()

    def test_successful_probe_records_command_and_exit_code(self) -> None:
        """성공한 ffprobe 호출이 대상 이름과 종료 코드 0으로 기록되는지 검증"""
        proc = MagicMock()
        proc.communicate.return_value = (b'{"streams": []}', b"")
        proc.returncode = 0
        with patch("src.core.analysis.prober.subprocess.Popen", return_value=proc):
            parsed = prober.run_ffprobe("C:/work/clip.mp4")
        self.assertEqual(parsed, {"streams": []})
        records = snapshot()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].stage, STAGE_PROBING)
        self.assertEqual(records[0].target_name, "clip.mp4")
        self.assertEqual(records[0].source_path, "C:/work/clip.mp4")
        self.assertEqual(records[0].exit_code, 0)
        self.assertEqual(records[0].args[0], "ffprobe")
        self.assertIsNotNone(records[0].elapsed_sec)

    def test_failed_probe_still_records_finish(self) -> None:
        """ffprobe가 실패해 예외가 나도 실행 중이 아닌 종료 코드 기록이 남는지 검증"""
        proc = MagicMock()
        proc.communicate.return_value = (b"", b"invalid data")
        proc.returncode = 1
        with (
            patch("src.core.analysis.prober.subprocess.Popen", return_value=proc),
            self.assertRaises(DomainRuntimeError),
        ):
            prober.run_ffprobe("C:/work/broken.mkv")
        records = snapshot()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].exit_code, 1)
        self.assertEqual(records[0].target_name, "broken.mkv")

    def test_cancelled_probe_records_cancelled_state(self) -> None:
        """취소로 중단한 ffprobe도 종료 기록을 남기고 실행 중으로 남지 않는지 검증"""
        cancel_event = threading.Event()
        cancel_event.set()
        proc = MagicMock()
        proc.poll.return_value = None
        proc.communicate.return_value = (b"", b"")
        with (
            patch("src.core.analysis.prober.subprocess.Popen", return_value=proc),
            self.assertRaises(InterruptedError),
        ):
            prober.run_ffprobe("C:/work/cancel.mp4", cancel_event=cancel_event)
        records = snapshot()
        self.assertEqual(len(records), 1)
        self.assertIsNotNone(records[0].elapsed_sec)
        proc.kill.assert_called_once()


class _FakeProcess:
    """샘플 출력 파일을 만들고 취소 여부를 흉내 내는 가짜 인코딩 프로세스"""

    def __init__(self, args: Sequence[str], returncode: int) -> None:
        self._args = list(args)
        self._returncode = returncode
        self._cancelled = False

    def start(self) -> None:
        Path(self._args[-1]).write_bytes(b"x" * 16)

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


class SampleEngineRecordingTests(unittest.TestCase):
    """샘플 인코딩 실행이 샘플 인코딩 단계 기록을 남기는지 검증"""

    def setUp(self) -> None:
        clear()

    def tearDown(self) -> None:
        clear()

    def _run(self, tmp: str, *, returncode: int) -> None:
        run_sample_pass(
            batch_id=1,
            source_path=os.path.join(tmp, "in.mp4"),
            plan=_plan(tmp),
            ffmpeg_exec="ffmpeg",
            process_factory=lambda args, _on_line: _FakeProcess(args, returncode),
            abort=threading.Event(),
            segments=((0.0, 2.0),),
            quality=22.0,
            build_args=build_sample_ffmpeg_args,
        )

    def test_successful_segment_records_stage_and_exit_code(self) -> None:
        """샘플 인코딩이 단계 이름과 종료 코드 0, 대상 이름으로 기록되는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp, returncode=0)
        records = snapshot()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].stage, STAGE_SAMPLE_ENCODE)
        self.assertEqual(records[0].target_name, "in.mp4")
        self.assertEqual(records[0].source_path, os.path.join(tmp, "in.mp4"))
        self.assertEqual(records[0].exit_code, 0)

    def test_failed_segment_records_failure_exit_code(self) -> None:
        """실패한 샘플 인코딩도 종료 코드와 함께 기록되는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp, returncode=2)
        record = snapshot()[0]
        self.assertEqual(record.exit_code, 2)
        self.assertEqual(record.stage, STAGE_SAMPLE_ENCODE)



def _failing_runner(args: list[str], work_dir: str) -> int:
    """실패 종료 코드를 돌려주는 가짜 실행기. 인자 이름을 실행기 서명과 맞춤"""
    return 1


def _failing_stderr_runner(args: list[str], work_dir: str) -> tuple[int, str]:
    """실패 종료 코드와 빈 표준 오류를 돌려주는 가짜 실행기"""
    return 1, ""


class QualityProbeRecordingTests(unittest.TestCase):
    """샘플 구간 인코딩과 지표 측정 실행이 각 단계로 기록되는지 검증"""

    def setUp(self) -> None:
        clear()

    def tearDown(self) -> None:
        clear()

    def test_sample_segments_record_sample_stage(self) -> None:
        """구간 샘플 인코딩이 샘플 인코딩 단계와 실행기 종료 코드로 기록되는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            encode_sample_segments(
                plan=_plan(tmp),
                segments=[(0.0, 2.0)],
                quality=22.0,
                work_dir=tmp,
                runner=_failing_runner,
            )
        record = snapshot()[0]
        self.assertEqual(record.stage, STAGE_SAMPLE_ENCODE)
        self.assertEqual(record.exit_code, 1)
        self.assertEqual(record.target_name, "in.mp4")

    def test_metric_measurement_records_vmaf_stage(self) -> None:
        """VMAF 측정 명령이 VMAF 측정 단계와 종료 코드, 참조 영상 경로로 기록되는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            measure_metric(
                distorted_path=os.path.join(tmp, "out.mp4"),
                reference_path=os.path.join(tmp, "in.mp4"),
                work_dir=tmp,
                metric=METRIC_VMAF,
                runner=_failing_runner,
            )
        record = snapshot()[0]
        self.assertEqual(record.stage, STAGE_VMAF)
        self.assertEqual(record.exit_code, 1)
        self.assertEqual(record.target_name, "out.mp4")
        self.assertEqual(record.source_path, os.path.join(tmp, "in.mp4"))


class FrameScanRecordingTests(unittest.TestCase):
    """CLI 9지점 스캔이 화질 분석 단계 기록을 지점마다 남기는지 검증"""

    def setUp(self) -> None:
        clear()

    def tearDown(self) -> None:
        clear()

    def test_each_scan_point_records_frame_analysis_stage(self) -> None:
        """지점마다 화질 분석 단계 기록이 남고 종료 코드가 실행기 값과 같은지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            scan_with_cli(
                source_path=os.path.join(tmp, "clip.mp4"),
                points=[1.0, 2.0],
                ffmpeg_executable="ffmpeg",
                runner=_failing_stderr_runner,
                work_dir=tmp,
                fast_keyframe_seek=False,
                cancel_event=None,
                on_point=None,
            )
        records = snapshot()
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record.stage, STAGE_FRAME_ANALYSIS)
            self.assertEqual(record.exit_code, 1)
            self.assertEqual(record.target_name, "clip.mp4")
            self.assertEqual(record.source_path, os.path.join(tmp, "clip.mp4"))


if __name__ == "__main__":
    unittest.main()

