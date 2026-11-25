"""본 인코딩 실행이 기록되는지 가짜 프로세스로 검증"""

import os
import tempfile
import unittest
from collections.abc import Sequence

from src.core.command_history import STAGE_MAIN_ENCODE, clear, snapshot
from src.core.config import CONTAINER_POLICY_MP4
from src.core.encode.encode_gate import GatePurpose
from src.core.encode.encode_plan import EncodePlan, build_encode_plan
from src.core.encode.encode_process import ProcessOutcome
from src.core.encode.stream_layout import parse_stream_layout
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_job import EncodeJob, EncodeSettings
from src.service.encode.encode_service import EncodeRunner

SOURCE_BYTES: int = 1024


class _FakeProcess:
    """시작과 대기를 흉내 내는 가짜 인코딩 프로세스"""

    def __init__(self, args: Sequence[str], returncode: int, *, cancelled: bool = False) -> None:
        self._args = list(args)
        self._returncode = returncode
        self._cancelled = cancelled

    def start(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        return ProcessOutcome(
            returncode=None if self._cancelled else self._returncode,
            cancelled=self._cancelled,
            stderr_tail=(),
        )

    def cancel(self) -> None:
        pass

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def set_priority(self, name: str) -> None:
        pass


def _settings() -> EncodeSettings:
    return EncodeSettings(
        encoder="libx264",
        container_policy="keep",
        result_policy="archive",
        archive_dirname="_원본보관",
        output_dirname="_인코딩결과",
        name_conflict="skip",
        process_priority="normal",
        ffmpeg_path="",
        ffprobe_path="",
        concurrent_jobs=1,
        prevent_sleep=False,
        after_complete="none",
    )


def _plan(source: str) -> EncodePlan:
    layout = parse_stream_layout(
        {"streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc"}]}
    )
    return build_encode_plan(
        source_path=source,
        part_path=source + ".part",
        output_path=source,
        source_ext="mp4",
        encoder="libx264",
        quality=22.0,
        fps=30.0,
        layout=layout,
        container_policy=CONTAINER_POLICY_MP4,
        tag_value="v=1",
    )


class MainEncodeRecordingTests(unittest.TestCase):
    """본 인코딩 한 번이 본 인코딩 단계 기록으로 남는지 검증"""

    def setUp(self) -> None:
        clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.root: str = os.path.join(self._tmp.name, "work")
        os.makedirs(self.root)
        self.history = EncodeHistory(os.path.join(self._tmp.name, "video_cache.sqlite3"))
        self.source: str = os.path.join(self.root, "clip.mp4")
        with open(self.source, "wb") as handle:
            handle.write(b"s" * SOURCE_BYTES)

    def tearDown(self) -> None:
        self.history.close()
        self._tmp.cleanup()
        clear()

    def _run(self, process: _FakeProcess) -> ProcessOutcome:
        runner = EncodeRunner(
            self.history,
            _settings(),
            probe=lambda _path, _args, _cancel: {},
            process_factory=lambda _args, _on_line: process,
            on_event=lambda _event: None,
            cpu_count=1,
        )
        stat = os.stat(self.source)
        job = EncodeJob(
            filename="clip.mp4",
            root_dir=self.root,
            source_path=self.source,
            source_size=stat.st_size,
            source_mtime_ns=stat.st_mtime_ns,
            source_duration_sec=10.0,
            source_ext="mp4",
            fps=30.0,
            quality=22.0,
            purpose=GatePurpose.SAVING,
            output_size=None,
            scale_filter=None,
            maxrate_kbps=None,
        )
        return runner._run_ffmpeg(1, job, _plan(self.source))

    def test_main_encode_records_stage_target_and_exit_code(self) -> None:
        """본 인코딩이 단계 이름, 대상 파일 이름, 종료 코드로 기록되는지 검증"""
        outcome = self._run(_FakeProcess(["ffmpeg", "-i", self.source], 0))
        self.assertEqual(outcome.returncode, 0)
        record = snapshot()[0]
        self.assertEqual(record.stage, STAGE_MAIN_ENCODE)
        self.assertEqual(record.target_name, "clip.mp4")
        self.assertEqual(record.exit_code, 0)
        self.assertEqual(record.args[0], "ffmpeg")

    def test_cancelled_main_encode_leaves_exit_code_empty(self) -> None:
        """취소된 본 인코딩은 종료 코드 없이 소요 시간만 채워 기록되는지 검증"""
        outcome = self._run(_FakeProcess(["ffmpeg", "-i", self.source], 0, cancelled=True))
        self.assertTrue(outcome.cancelled)
        record = snapshot()[0]
        self.assertIsNone(record.exit_code)
        self.assertIsNotNone(record.elapsed_sec)


if __name__ == "__main__":
    unittest.main()
