"""화질 분석 9지점 키프레임 스캔의 Y 평면 계산, CLI 폴백, 회전 전치 검증"""

import tempfile
import threading
import unittest
from collections.abc import Iterator
from types import SimpleNamespace
from typing import TYPE_CHECKING, ClassVar, cast
from unittest import mock

from src.core.analysis.frame_analysis import CropBox
from src.core.analysis.frame_scan import (
    KeyframeScanResult,
    _declared_rotation,
    _luma_bytes,
    _scan_with_pyav,
    build_scan_command,
    luma_frame_stats,
    parse_blackframe_pixel_ratio,
    scan_keyframe_points,
    scan_with_cli,
    transpose_crop_box,
)
from tests._frame_analysis_fakes import FakeStderrRunner

if TYPE_CHECKING:
    from av.video.frame import VideoFrame
    from av.video.stream import VideoStream


def _plane(rows: list[list[int]], line_size: int | None = None) -> bytes:
    """행 목록을 Y 평면 바이트로 만듦. 행 바이트 수가 더 크면 0으로 채움"""
    width = len(rows[0])
    stride = line_size if line_size is not None else width
    data = bytearray()
    for row in rows:
        data.extend(row)
        data.extend([0] * (stride - width))
    return bytes(data)


class TestLumaStats(unittest.TestCase):
    """Y 평면 검은 픽셀 비율과 검은 여백 상자 계산 검증"""

    def test_black_frame_returns_no_box(self) -> None:
        box, ratio = luma_frame_stats(bytes([16] * 64), 8, 8, 8)
        self.assertIsNone(box)
        self.assertEqual(ratio, 1.0)

    def test_letterbox_rows_are_cropped(self) -> None:
        rows = [[16] * 8, [16] * 8] + [[100] * 8 for _ in range(4)] + [[16] * 8, [16] * 8]
        box, ratio = luma_frame_stats(_plane(rows), 8, 8, 8)
        self.assertEqual(box, CropBox(8, 4, 0, 2))
        self.assertEqual(ratio, 0.5)

    def test_pillar_columns_are_cropped(self) -> None:
        rows = [[16, 16, 100, 100, 100, 100, 16, 16] for _ in range(8)]
        box, _ratio = luma_frame_stats(_plane(rows), 8, 8, 8)
        self.assertEqual(box, CropBox(4, 8, 2, 0))

    def test_full_content_keeps_full_frame(self) -> None:
        rows = [[30] * 8 for _ in range(8)]
        box, _ratio = luma_frame_stats(_plane(rows), 8, 8, 8)
        self.assertEqual(box, CropBox(8, 8, 0, 0))

    def test_padded_rows_and_invalid_sizes(self) -> None:
        rows = [[16] * 8, [100] * 8, [100] * 8, [16] * 8]
        box, _ratio = luma_frame_stats(_plane(rows, line_size=10), 10, 8, 4)
        self.assertEqual(box, CropBox(8, 2, 0, 1))
        self.assertEqual(luma_frame_stats(b"", 0, 0, 0), (None, 1.0))
        self.assertEqual(luma_frame_stats(b"\x00", 4, 8, 8), (None, 1.0))

    def test_dark_edges_covering_everything_returns_no_box(self) -> None:
        box, _ratio = luma_frame_stats(bytes([25] * 64), 8, 8, 8)
        self.assertIsNone(box)


class TestScanCommandAndParsing(unittest.TestCase):
    """CLI 스캔 명령과 blackframe 비율 파싱 검증"""

    def test_scan_command_uses_keyframe_and_metrics(self) -> None:
        args = build_scan_command(
            ffmpeg_executable="ffmpeg", source_path="movie.mp4", start_sec=1.5, fast_keyframe_seek=True
        )
        self.assertEqual(args[args.index("-ss") + 1], "1.500")
        self.assertIn("-noaccurate_seek", args)
        self.assertEqual(args[args.index("-skip_frame") + 1], "nokey")
        self.assertIn("blackframe=amount=0:threshold=24,cropdetect=24:2:0:0", args)
        slow = build_scan_command(
            ffmpeg_executable="ffmpeg", source_path="movie.mp4", start_sec=0.0, fast_keyframe_seek=False
        )
        self.assertNotIn("-noaccurate_seek", slow)

    def test_parse_blackframe_ratio_takes_max(self) -> None:
        text = "pblack:10\n[Parsed_blackframe_0] pblack:100\npblack:200"
        self.assertEqual(parse_blackframe_pixel_ratio(text), 1.0)
        self.assertEqual(parse_blackframe_pixel_ratio("no log"), 0.0)


class TestRotation(unittest.TestCase):
    """회전 각도 읽기와 상자 전치 검증"""

    def test_declared_rotation_reads_metadata(self) -> None:
        class _Stream:
            def __init__(self, metadata: dict[str, str]) -> None:
                self.metadata = metadata

        self.assertEqual(_declared_rotation(cast("VideoStream", _Stream({"rotate": "90"}))), 90)
        self.assertEqual(_declared_rotation(cast("VideoStream", _Stream({"rotate": "bad"}))), 0)
        self.assertEqual(_declared_rotation(cast("VideoStream", _Stream({}))), 0)

    def test_transpose_quarter_turns(self) -> None:
        box = CropBox(600, 720, 100, 200)
        self.assertEqual(transpose_crop_box(box, 1080, 1920, 0), box)
        self.assertEqual(transpose_crop_box(box, 1080, 1920, 90), CropBox(720, 600, 1000, 100))
        self.assertEqual(transpose_crop_box(box, 1080, 1920, 270), CropBox(720, 600, 200, 380))

    def test_transpose_half_turn_mirrors_asymmetric_box(self) -> None:
        """180도 회전에서 비대칭 여백 상자의 x, y를 코딩 크기 기준으로 반전하는지 검증"""
        box = CropBox(600, 720, 100, 200)
        self.assertEqual(transpose_crop_box(box, 1080, 1920, 180), CropBox(600, 720, 380, 1000))

    def test_pyav_scan_uses_frame_rotation(self) -> None:
        """디스플레이 매트릭스 회전(-90, 90, 180)을 시계 방향 전치에 반영하는지 검증"""
        self.assertEqual(self._scan_boxes(-90), (CropBox(40, 70, 0, 10),))
        self.assertEqual(self._scan_boxes(90), (CropBox(40, 70, 0, 0),))
        self.assertEqual(self._scan_boxes(180), (CropBox(70, 40, 0, 0),))

    def test_pyav_scan_falls_back_to_declared_rotation(self) -> None:
        """프레임 회전이 0이면 스트림 메타데이터 회전을 쓰고 -90과 같은 방향인지 검증"""
        self.assertEqual(self._scan_boxes(0, rotate="90"), (CropBox(40, 70, 0, 10),))
        self.assertEqual(self._scan_boxes(0), (CropBox(70, 40, 10, 0),))

    def _scan_boxes(self, rotation: int, *, rotate: str | None = None) -> tuple[CropBox, ...]:
        """왼쪽 10픽셀 검은 기둥이 있는 80x40 합성 프레임을 PyAV 스캔으로 처리한 상자"""
        data = b"".join(bytes([16] * 10 + [100] * 70) for _ in range(40))

        class _Plane:
            line_size = 80

            def __bytes__(self) -> bytes:
                return data

        class _Frame:
            format: object = SimpleNamespace(name="gray")
            planes: ClassVar[list[_Plane]] = [_Plane()]
            width = 80
            height = 40

            def __init__(self) -> None:
                self.rotation = rotation

        class _Stream:
            metadata: ClassVar[dict[str, str]] = {} if rotate is None else {"rotate": rotate}
            time_base = 0.001

        class _Container:
            streams: ClassVar[SimpleNamespace] = SimpleNamespace(video=[_Stream()])
            closed = False

            def seek(self, offset: int, **kwargs: object) -> None:
                del offset, kwargs

            def decode(self, video: int = 0) -> Iterator[_Frame]:
                del video
                return iter([_Frame()])

            def close(self) -> None:
                self.closed = True

        container = _Container()
        with (
            mock.patch("src.core.analysis.frame_scan.open_shared_binary", return_value=None),
            mock.patch("av.open", return_value=container),
        ):
            result = _scan_with_pyav("movie.mp4", [0.0], cancel_event=None, on_point=None)
        self.assertTrue(container.closed)
        return result.boxes

    def test_luma_bytes_uses_plane_or_reformats(self) -> None:
        class _Plane:
            line_size = 4

            def __bytes__(self) -> bytes:
                return b"\x10" * 4

        class _Format:
            name = "yuv420p"

        class _Frame:
            format: object = _Format()
            planes: ClassVar[list[_Plane]] = [_Plane()]
            width = 4
            height = 1
            reformatted = 0

            def reformat(self, format: str) -> "_Frame":
                del format
                self.reformatted += 1
                return self

        frame = _Frame()
        self.assertEqual(_luma_bytes(cast("VideoFrame", frame)), (b"\x10" * 4, 4, 4, 1))
        self.assertEqual(frame.reformatted, 0)
        frame.format = type("F", (), {"name": "yuv420p10le"})()
        self.assertEqual(_luma_bytes(cast("VideoFrame", frame)), (b"\x10" * 4, 4, 4, 1))
        self.assertEqual(frame.reformatted, 1)


class TestScanWithCli(unittest.TestCase):
    """CLI 9지점 스캔 결과와 PyAV 실패 폴백 검증"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work_dir = tmp.name

    def test_cli_scan_collects_boxes_and_black_points(self) -> None:
        runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        seen: list[tuple[int, int]] = []
        result = scan_with_cli(
            source_path="movie.mp4",
            points=[0.0, 5.0, 10.0],
            ffmpeg_executable="ffmpeg",
            runner=runner,
            work_dir=self.work_dir,
            fast_keyframe_seek=True,
            cancel_event=None,
            on_point=lambda done, total: seen.append((done, total)),
        )
        self.assertEqual(result.boxes, (CropBox(720, 1080, 600, 0),) * 3)
        self.assertEqual(result.black_points, frozenset())
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])

    def test_cli_scan_treats_black_or_failed_point_as_invalid(self) -> None:
        black = scan_with_cli(
            source_path="movie.mp4",
            points=[0.0],
            ffmpeg_executable="ffmpeg",
            runner=FakeStderrRunner(black_ratio=0.9),
            work_dir=self.work_dir,
            fast_keyframe_seek=True,
            cancel_event=None,
            on_point=None,
        )
        self.assertEqual(black.black_points, frozenset({0}))
        failed = scan_with_cli(
            source_path="movie.mp4",
            points=[0.0],
            ffmpeg_executable="ffmpeg",
            runner=FakeStderrRunner(black_returncode=1),
            work_dir=self.work_dir,
            fast_keyframe_seek=True,
            cancel_event=None,
            on_point=None,
        )
        self.assertEqual(failed.black_points, frozenset({0}))

    def test_pyav_failure_falls_back_to_cli(self) -> None:
        runner = FakeStderrRunner(boxes=[(1280, 720, 0, 0)])
        result = scan_keyframe_points(
            source_path=str(__file__) + ".missing",
            points=[0.0],
            ffmpeg_executable="ffmpeg",
            runner=runner,
            work_dir=self.work_dir,
            fast_keyframe_seek=True,
            prefer_pyav=True,
        )
        self.assertEqual(result.boxes, (CropBox(1280, 720, 0, 0),))
        self.assertEqual(runner.count_commands("blackframe"), 1)

    def test_prefer_pyav_off_uses_cli_and_cancel_stops(self) -> None:
        runner = FakeStderrRunner()
        scan_keyframe_points(
            source_path="movie.mp4",
            points=[0.0],
            ffmpeg_executable="ffmpeg",
            runner=runner,
            work_dir=self.work_dir,
            fast_keyframe_seek=False,
            prefer_pyav=False,
        )
        self.assertEqual(runner.count_commands("blackframe"), 1)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            scan_keyframe_points(
                source_path="movie.mp4",
                points=[0.0],
                ffmpeg_executable="ffmpeg",
                runner=runner,
                work_dir=self.work_dir,
                fast_keyframe_seek=False,
                cancel_event=cancel,
                prefer_pyav=False,
            )

    def test_scan_result_keeps_order(self) -> None:
        result = KeyframeScanResult([CropBox(1, 1, 0, 0), CropBox(2, 2, 0, 0)], [1, 1, 2])
        self.assertEqual(len(result.boxes), 2)
        self.assertEqual(result.black_points, frozenset({1, 2}))


if __name__ == "__main__":
    unittest.main()

