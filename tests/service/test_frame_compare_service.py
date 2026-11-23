"""화질 비교 서비스 검증: 비교 쌍 조회, 프로빙 오류 구분, 프레임 추출 실행과 취소, 메모리 디코더 연동

- FFmpeg는 가짜 실행기로 대체하고, 실행기 자체는 시스템 Python으로 확인
- 메모리 디코더는 가짜 디코더를 주입해 파일 핸들 정리와 FFmpeg 대체를 확인
"""

import os
import sys
import tempfile
import threading
import unittest
from collections.abc import Callable
from typing import cast
from unittest import mock

from src.core.analysis.frame_compare import FrameSource
from src.core.encode.encode_state import EncodeState
from src.core.models import ActiveFrame, VideoProps
from src.service import frame_compare_service as module
from src.service.encode.encode_history import EncodeHistory, NewItem
from src.service.frame_compare_decoder import DecodedFrame, PersistentFrameDecoder
from src.service.frame_compare_service import (
    FILE_MISSING_TEXT,
    FrameCompareExecutionError,
    FrameCompareFileMissing,
    FrameCompareService,
    resolve_comparable_result,
    run_ffmpeg_frame,
)

_FRAME = b"BMfake-frame"
_WAIT_SECONDS: float = 5.0


def _props(path: str) -> VideoProps:
    return VideoProps(
        filename=os.path.basename(path),
        width=3840,
        height=2160,
        rotate_type=0,
        fps=30.0,
        vid_kbps=1000,
        aud_kbps=128,
        vid_size_MB=1.0,
        duration=754.567,
        codec="hevc",
    )


class _Base(unittest.TestCase):
    """임시 폴더와 기록 저장소를 준비하는 기반"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.history = EncodeHistory(os.path.join(self.root, "cache.sqlite3"), sleep=lambda _s: None)
        self.addCleanup(self.history.close)

    def make(self, name: str) -> str:
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(b"x")
        return path

    def add_item(self, source: str, **fields: object) -> int:
        batch = self.history.create_batch(
            self.root,
            '{"encoder": "libx264"}',
            [NewItem(source_path=source, source_size=10, source_mtime_ns=1, plan_json='{"quality": 22}')],
        )
        if fields:
            self.history.update_item(batch, source, fields)
        return batch


class ComparableResultTests(_Base):
    """완료, 교체 대기, 그 밖의 상태에서 비교 쌍 조회"""

    def test_done_prefers_archive_and_requires_both_files(self) -> None:
        source = self.make("source.mp4")
        archive = self.make("source_원본보관.mp4")
        output = self.make("out.mp4")
        self.add_item(source, state=EncodeState.DONE, output_path=output, archive_path=archive)
        self.assertEqual(resolve_comparable_result(self.history, source), (archive, output))

    def test_done_without_archive_uses_original_source(self) -> None:
        source = self.make("source.mp4")
        output = self.make("out.mp4")
        self.add_item(source, state=EncodeState.DONE, output_path=output)
        self.assertEqual(resolve_comparable_result(self.history, source), (source, output))

    def test_replace_pending_uses_part_file(self) -> None:
        source = self.make("source.mp4")
        part = self.make("source.mp4.part")
        self.add_item(source, state=EncodeState.REPLACE_PENDING, part_path=part)
        self.assertEqual(resolve_comparable_result(self.history, source), (source, part))

    def test_other_states_and_missing_files_are_not_comparable(self) -> None:
        failed = self.make("failed.mp4")
        self.add_item(failed, state=EncodeState.FAILED, part_path=self.make("failed.part"))
        self.assertIsNone(resolve_comparable_result(self.history, failed))

        source = self.make("source.mp4")
        self.add_item(source, state=EncodeState.DONE, output_path=os.path.join(self.root, "gone.mp4"))
        self.assertIsNone(resolve_comparable_result(self.history, source))
        self.assertIsNone(resolve_comparable_result(self.history, self.make("other.mp4")))


class ProbeTests(_Base):
    """프로빙 성공과 파일 부재, 실행 오류 구분"""

    def test_probe_returns_metadata(self) -> None:
        path = self.make("video.mp4")
        service = FrameCompareService(ffmpeg_executable="ffmpeg", ffprobe_executable="ffprobe", work_dir=self.root)
        with mock.patch.object(module, "extract_video_props", return_value=_props(path)):
            info = service.probe(path)
        self.assertEqual((info.width, info.height, info.fps), (3840, 2160, 30.0))
        self.assertEqual((info.filename, info.size_bytes), ("video.mp4", 1))

    def test_missing_file_and_os_error_are_file_missing(self) -> None:
        service = FrameCompareService(ffmpeg_executable="ffmpeg", ffprobe_executable="ffprobe", work_dir=self.root)
        with self.assertRaises(FrameCompareFileMissing):
            service.probe(os.path.join(self.root, "gone.mp4"))

        path = self.make("video.mp4")
        with (
            mock.patch.object(module, "extract_video_props", side_effect=OSError("gone")),
            self.assertRaises(FrameCompareFileMissing),
        ):
            service.probe(path)

    def test_probe_execution_error_keeps_reason(self) -> None:
        path = self.make("video.mp4")
        service = FrameCompareService(ffmpeg_executable="ffmpeg", ffprobe_executable="ffprobe", work_dir=self.root)
        with (
            mock.patch.object(module, "extract_video_props", side_effect=RuntimeError("ffprobe 실패\n자세히")),
            self.assertRaises(FrameCompareExecutionError) as raised,
        ):
            service.probe(path)
        self.assertIn("ffprobe 실패", str(raised.exception))


class ExtractPairTests(_Base):
    """가짜 실행기로 프레임 쌍 추출, 파일 부재와 실행 오류"""

    def setUp(self) -> None:
        super().setUp()
        self.calls: list[list[str]] = []

    def runner(
        self, returncode: int = 0, stderr: str = ""
    ) -> Callable[[list[str], threading.Event], tuple[int, str]]:
        def _run(args: list[str], _cancel: threading.Event) -> tuple[int, str]:
            self.calls.append(list(args))
            if returncode == 0:
                for arg in args:
                    if arg.endswith(".bmp"):
                        with open(arg, "wb") as handle:
                            handle.write(_FRAME)
            return returncode, stderr

        return _run

    def _sources(self, tier_key: str | None = None) -> tuple[FrameSource, FrameSource]:
        original = FrameSource(key="original", path=self.make("a.mp4"), label="원본")
        compared = FrameSource(
            key=tier_key or "result", path=self.make("b.mp4"), label="비교", tier_key=tier_key
        )
        return original, compared

    def test_extract_pair_runs_both_commands_and_reads_bmp(self) -> None:
        original, compared = self._sources()
        service = FrameCompareService(
            ffmpeg_executable="ffmpeg",
            ffprobe_executable="ffprobe",
            work_dir=os.path.join(self.root, "work"),
            runner=self.runner(),
        )
        frames = service.extract_pair(
            original,
            compared,
            time_sec=83.4,
            frame_width=3840,
            frame_height=2160,
            cancel_event=threading.Event(),
        )
        self.assertEqual(frames.original_bytes, _FRAME)
        self.assertEqual(frames.compared_bytes, _FRAME)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(
            {self.calls[0][-1].rsplit(os.sep, 1)[-1], self.calls[1][-1].rsplit(os.sep, 1)[-1]},
            {"original.bmp", "compared.bmp"},
        )
        self.assertIn("bmp", self.calls[0])

    def test_downscale_comparison_decodes_one_file_once(self) -> None:
        original = FrameSource(key="original", path=self.make("a.mp4"), label="원본")
        compared = FrameSource(
            key="720p", path=original.path, label="720p 축소 비교", tier_key="720p"
        )
        service = FrameCompareService(
            ffmpeg_executable="ffmpeg",
            ffprobe_executable="ffprobe",
            work_dir=os.path.join(self.root, "work"),
            runner=self.runner(),
        )
        frames = service.extract_pair(
            original,
            compared,
            time_sec=10.0,
            frame_width=3840,
            frame_height=2160,
            cancel_event=threading.Event(),
        )
        self.assertEqual(len(self.calls), 1)
        self.assertIn("-filter_complex", self.calls[0])
        self.assertEqual(frames.original_bytes, _FRAME)
        self.assertEqual(frames.compared_bytes, _FRAME)

    def test_parallel_failure_cancels_the_other_extraction(self) -> None:
        original, compared = self._sources()
        cancelled: list[bool] = []

        def runner(args: list[str], cancel: threading.Event) -> tuple[int, str]:
            self.calls.append(list(args))
            if args[-1].endswith("original.bmp"):
                raise FrameCompareExecutionError("FFmpeg로 프레임을 추출하지 못했습니다: boom")
            cancel.wait(_WAIT_SECONDS)
            cancelled.append(cancel.is_set())
            return 1, "cancelled"

        service = FrameCompareService(
            ffmpeg_executable="ffmpeg",
            ffprobe_executable="ffprobe",
            work_dir=os.path.join(self.root, "work"),
            runner=runner,
        )
        with self.assertRaises(FrameCompareExecutionError):
            service.extract_pair(
                original,
                compared,
                time_sec=0,
                frame_width=1,
                frame_height=1,
                cancel_event=threading.Event(),
            )
        self.assertEqual(cancelled, [True])

    def test_extract_pair_reports_missing_file(self) -> None:
        original, compared = self._sources()
        os.remove(compared.path)
        service = FrameCompareService(
            ffmpeg_executable="ffmpeg", ffprobe_executable="ffprobe", work_dir=self.root, runner=self.runner()
        )
        with self.assertRaises(FrameCompareFileMissing) as raised:
            service.extract_pair(
                original,
                compared,
                time_sec=0,
                frame_width=1,
                frame_height=1,
                cancel_event=threading.Event(),
            )
        self.assertEqual(str(raised.exception), FILE_MISSING_TEXT)

    def test_extract_pair_wraps_ffmpeg_failure_reason(self) -> None:
        original, compared = self._sources()
        service = FrameCompareService(
            ffmpeg_executable="ffmpeg",
            ffprobe_executable="ffprobe",
            work_dir=self.root,
            runner=self.runner(returncode=1, stderr="Invalid data found\nmore"),
        )
        with self.assertRaises(FrameCompareExecutionError) as raised:
            service.extract_pair(
                original,
                compared,
                time_sec=0,
                frame_width=1,
                frame_height=1,
                cancel_event=threading.Event(),
            )
        self.assertIn("Invalid data found", str(raised.exception))

    def test_service_without_history_has_no_comparable_result(self) -> None:
        service = FrameCompareService(ffmpeg_executable="ffmpeg", ffprobe_executable="ffprobe", work_dir=self.root)
        self.assertIsNone(service.comparable_result(self.make("a.mp4")))


class RunnerTests(_Base):
    """실제 자식 프로세스로 취소, 제한 시간과 표준 오류 수집 확인"""

    def _write_command(self, name: str) -> list[str]:
        target = os.path.join(self.root, name)
        code = f"open({target!r}, 'wb').write({_FRAME!r})"
        return [sys.executable, "-c", code]

    def test_successful_run_writes_output_and_returns_code(self) -> None:
        output = os.path.join(self.root, "out.png")
        returncode, stderr = run_ffmpeg_frame(self._write_command("out.png"), threading.Event())
        self.assertEqual((returncode, stderr), (0, ""))
        self.assertTrue(os.path.isfile(output))

    def test_cancel_before_wait_raises_interrupted(self) -> None:
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            run_ffmpeg_frame(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                cancel,
                timeout_sec=5.0,
            )

    def test_timeout_raises(self) -> None:
        with self.assertRaises(TimeoutError):
            run_ffmpeg_frame(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                threading.Event(),
                timeout_sec=0.1,
            )

    def test_stderr_is_returned_with_failure_code(self) -> None:
        code = "import sys; sys.stderr.write('boom'); sys.exit(2)"
        returncode, stderr = run_ffmpeg_frame([sys.executable, "-c", code], threading.Event())
        self.assertEqual(returncode, 2)
        self.assertEqual(stderr, "boom")


class _FakeDecoder:
    """호출과 닫힘을 기록하고 정해진 프레임이나 오류를 돌려주는 가짜 디코더"""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.frame = DecodedFrame(width=2, height=1, bytes_per_line=6, data=b"\x01" * 6)
        self.decode_times: list[float] = []
        self.pair_times: list[float] = []
        self.pair_crops: list[object] = []
        self.closed = False

    def decode(self, time_sec: float, *, cancel_event: threading.Event) -> DecodedFrame:
        del cancel_event
        self.decode_times.append(time_sec)
        if self.error is not None:
            raise self.error
        return self.frame

    def decode_pair(
        self,
        time_sec: float,
        *,
        tier: object,
        frame_width: int,
        frame_height: int,
        cancel_event: threading.Event,
        crop: object = None,
    ) -> tuple[DecodedFrame, DecodedFrame]:
        del tier, frame_width, frame_height, cancel_event
        self.pair_times.append(time_sec)
        self.pair_crops.append(crop)
        if self.error is not None:
            raise self.error
        return self.frame, self.frame

    def close(self) -> None:
        self.closed = True


def _decoder_factory(decoders: dict[str, _FakeDecoder]) -> Callable[[str], PersistentFrameDecoder]:
    def build(path: str) -> PersistentFrameDecoder:
        return cast("PersistentFrameDecoder", decoders[path])

    return build


class DecoderPathTests(_Base):
    """메모리 디코더 우선 사용, FFmpeg 대체, 디코더 재사용과 정리"""

    def setUp(self) -> None:
        super().setUp()
        self.original_path = self.make("a.mp4")
        self.compared_path = self.make("b.mp4")
        self.decoders = {
            self.original_path: _FakeDecoder(),
            self.compared_path: _FakeDecoder(),
        }

    def _service(self) -> FrameCompareService:
        return FrameCompareService(
            ffmpeg_executable="ffmpeg",
            ffprobe_executable="ffprobe",
            work_dir=os.path.join(self.root, "work"),
            decoder_factory=_decoder_factory(self.decoders),
        )

    def _sources(self, tier_key: str | None = None) -> tuple[FrameSource, FrameSource]:
        original = FrameSource(key="original", path=self.original_path, label="원본")
        compared = FrameSource(
            key=tier_key or "result",
            path=self.original_path if tier_key is not None else self.compared_path,
            label="비교",
            tier_key=tier_key,
        )
        return original, compared

    def _extract(
        self, service: FrameCompareService, original: FrameSource, compared: FrameSource
    ) -> module.ExtractedFrames:
        return service.extract_pair(
            original,
            compared,
            time_sec=1.5,
            frame_width=3840,
            frame_height=2160,
            cancel_event=threading.Event(),
        )

    def test_decoder_path_returns_frames_without_ffmpeg(self) -> None:
        """메모리 디코더가 있으면 FFmpeg를 실행하지 않고 프레임을 돌려주는지 검증"""
        service = self._service()
        original, compared = self._sources()
        with mock.patch.object(
            module, "run_ffmpeg_frame", side_effect=AssertionError("FFmpeg 호출됨")
        ):
            frames = self._extract(service, original, compared)
        self.assertIs(frames.original_frame, self.decoders[self.original_path].frame)
        self.assertIs(frames.compared_frame, self.decoders[self.compared_path].frame)
        self.assertEqual(self.decoders[self.original_path].decode_times, [1.5])
        service.close()
        self.assertTrue(self.decoders[self.original_path].closed)
        self.assertTrue(self.decoders[self.compared_path].closed)

    def test_same_file_downscale_uses_one_decode_pair(self) -> None:
        """같은 파일 축소 비교가 디코더 한 번의 왕복 변환으로 끝나는지 검증"""
        service = self._service()
        original, compared = self._sources(tier_key="720p")
        frames = self._extract(service, original, compared)
        self.assertEqual(self.decoders[self.original_path].pair_times, [1.5])
        self.assertIs(frames.original_frame, frames.compared_frame)
        service.close()

    def test_same_file_downscale_passes_the_active_region_crop(self) -> None:
        """잘라내기 확정 영상의 축소 비교가 활성 화면 크롭을 디코더에 넘기는지 검증"""
        service = self._service()
        active = ActiveFrame(1600, 1000, 260, 40)
        original = FrameSource(key="original", path=self.original_path, label="원본")
        compared = FrameSource(
            key="720p",
            path=self.original_path,
            label="720p 축소 비교",
            tier_key="720p",
            crop=active,
        )
        self._extract(service, original, compared)
        self.assertEqual(self.decoders[self.original_path].pair_crops, [active])
        service.close()

    def test_same_path_without_tier_decodes_once(self) -> None:
        """다른 대상이지만 경로가 같으면 한 번만 디코딩하는지 검증"""
        service = self._service()
        original = FrameSource(key="original", path=self.original_path, label="원본")
        compared = FrameSource(key="result", path=self.original_path, label="결과")
        frames = self._extract(service, original, compared)
        self.assertEqual(self.decoders[self.original_path].decode_times, [1.5])
        self.assertIs(frames.original_frame, frames.compared_frame)
        service.close()


    def test_decoder_error_falls_back_to_ffmpeg(self) -> None:
        """디코더가 실패하면 닫고 FFmpeg 경로로 다시 추출하는지 검증"""

        def fake_run(
            args: list[str], _cancel: threading.Event, *, timeout_sec: float = 60.0
        ) -> tuple[int, str]:
            del timeout_sec
            for arg in args:
                if arg.endswith(".bmp"):
                    with open(arg, "wb") as handle:
                        handle.write(_FRAME)
            return 0, ""

        service = self._service()
        self.decoders[self.original_path].error = RuntimeError("코덱 미지원")
        original, compared = self._sources()
        with mock.patch.object(module, "run_ffmpeg_frame", side_effect=fake_run):
            frames = self._extract(service, original, compared)
        self.assertEqual(frames.original_bytes, _FRAME)
        self.assertTrue(self.decoders[self.original_path].closed)
        self.assertTrue(self.decoders[self.compared_path].closed)
        service.close()

    def test_interrupted_decoder_error_is_not_retried_with_ffmpeg(self) -> None:
        """취소로 중단된 경우 FFmpeg로 다시 시도하지 않고 그대로 올리는지 검증"""
        service = self._service()
        self.decoders[self.original_path].error = InterruptedError("취소")
        original, compared = self._sources()
        with (
            mock.patch.object(
                module, "run_ffmpeg_frame", side_effect=AssertionError("FFmpeg 호출됨")
            ),
            self.assertRaises(InterruptedError),
        ):
            self._extract(service, original, compared)
        service.close()

    def test_decoder_is_reused_until_the_file_changes(self) -> None:
        """같은 파일은 디코더를 재사용하고 파일이 바뀌면 닫고 새로 여는지 검증"""
        created: list[_FakeDecoder] = []

        def factory(_path: str) -> PersistentFrameDecoder:
            decoder = _FakeDecoder()
            created.append(decoder)
            return cast("PersistentFrameDecoder", decoder)

        service = FrameCompareService(
            ffmpeg_executable="ffmpeg",
            ffprobe_executable="ffprobe",
            work_dir=os.path.join(self.root, "work"),
            decoder_factory=factory,
        )
        first = service._decoder_for(self.original_path)
        self.assertIs(service._decoder_for(self.original_path), first)
        with open(self.original_path, "ab") as handle:
            handle.write(b"changed")
        second = service._decoder_for(self.original_path)
        self.assertIsNot(second, first)
        self.assertEqual(len(created), 2)
        self.assertTrue(created[0].closed)
        service.close()

    def test_missing_path_raises_file_missing(self) -> None:
        """디코더를 만들 파일이 없으면 파일 부재 예외를 올리는지 검증"""
        service = self._service()
        with self.assertRaises(FrameCompareFileMissing) as raised:
            service._decoder_for(os.path.join(self.root, "gone.mp4"))
        self.assertEqual(str(raised.exception), FILE_MISSING_TEXT)
        service.close()

    def test_close_without_decoders_is_safe(self) -> None:
        """연 디코더가 없어도 정리를 두 번 불러도 안전한지 검증"""
        service = FrameCompareService(
            ffmpeg_executable="ffmpeg", ffprobe_executable="ffprobe", work_dir=self.root
        )
        service.close()
        service.close()

    def test_injected_runner_skips_the_decoder(self) -> None:
        """가짜 실행기를 주입하면 메모리 디코더를 만들지 않고 FFmpeg 경로를 쓰는지 검증"""
        calls: list[list[str]] = []

        def runner(args: list[str], _cancel: threading.Event) -> tuple[int, str]:
            calls.append(list(args))
            for arg in args:
                if arg.endswith(".bmp"):
                    with open(arg, "wb") as handle:
                        handle.write(_FRAME)
            return 0, ""

        def factory(_path: str) -> PersistentFrameDecoder:
            raise AssertionError("디코더를 만들면 안 됨")

        service = FrameCompareService(
            ffmpeg_executable="ffmpeg",
            ffprobe_executable="ffprobe",
            work_dir=os.path.join(self.root, "work"),
            runner=runner,
            decoder_factory=factory,
        )
        original, compared = self._sources()
        frames = service.extract_pair(
            original,
            compared,
            time_sec=1.5,
            frame_width=3840,
            frame_height=2160,
            cancel_event=threading.Event(),
        )
        self.assertEqual(frames.original_bytes, _FRAME)
        self.assertEqual(len(calls), 2)
        service.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
