"""화질 분석 9지점 키프레임 스캔의 Y 평면 계산, CLI 폴백, 회전 전치 검증"""

import tempfile
import threading
import unittest
from collections.abc import Iterator
from types import SimpleNamespace
from typing import TYPE_CHECKING, ClassVar, cast
from unittest import mock

from src.core.analysis.frame_analysis import CropBox, merge_crop_union
from src.core.analysis.frame_scan import (
    BLACK_FRAME_PIXEL_RATIO,
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


def _noisy_row(width: int, luma: int, amplitude: int = 2) -> list[int]:
    """지정 휘도 주변에서 +-amplitude로 번갈아 흔들리는 띠 줄"""
    return [luma + (amplitude if index % 2 == 0 else -amplitude) for index in range(width)]


def _gradient_row(width: int, mean: float, deviation: float) -> list[int]:
    """평균과 평균 편차를 맞춘 그라데이션 줄. 픽셀을 -1, +1로 번갈아 편차만큼 벌림"""
    low = max(0, round(mean - deviation))
    high = min(255, round(mean + deviation))
    return [low if index % 2 == 0 else high for index in range(width)]


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

    def test_dark_edge_without_step_is_picture(self) -> None:
        """그림과 밝기 차가 계단 기준 미만인 어두운 가장자리는 여백이 아님"""
        rows = [[120] * 8 for _ in range(4)] + [[40] * 8, [32] * 8, [26] * 8, [22] * 8, [20] * 8, [18] * 8]
        box, _ratio = luma_frame_stats(_plane(rows), 8, 8, 10)
        self.assertEqual(box, CropBox(8, 10, 0, 0))

    def test_padded_rows_and_invalid_sizes(self) -> None:
        rows = [[16] * 8, [100] * 8, [100] * 8, [16] * 8]
        box, _ratio = luma_frame_stats(_plane(rows, line_size=10), 10, 8, 4)
        self.assertEqual(box, CropBox(8, 2, 0, 1))
        self.assertEqual(luma_frame_stats(b"", 0, 0, 0), (None, 1.0))
        self.assertEqual(luma_frame_stats(b"\x00", 4, 8, 8), (None, 1.0))

    def test_dark_edges_covering_everything_returns_no_box(self) -> None:
        box, _ratio = luma_frame_stats(bytes([25] * 64), 8, 8, 8)
        self.assertIsNone(box)


class TestBarEdgeRules(unittest.TestCase):
    """평탄도, 경계 계단, 띠 안 자막 섬 규칙으로 검은 여백을 판정하는지 검증"""

    def test_four_side_bars_are_cropped(self) -> None:
        """4면 띠가 모두 있으면 각 면의 띠 두께만큼 상자를 줄임"""
        rows = [[16] * 32 for _ in range(3)]
        rows += [[16] * 4 + [120] * 26 + [16] * 2 for _ in range(16)]
        rows += [[16] * 32 for _ in range(5)]
        box, _ratio = luma_frame_stats(_plane(rows), 32, 32, len(rows))
        self.assertEqual(box, CropBox(26, 16, 4, 3))

    def test_asymmetric_side_bars_are_cropped(self) -> None:
        """한쪽에만 있는 띠도 그 두께만큼 자름"""
        rows = [[16] * 6 + [120] * 26 for _ in range(24)]
        box, _ratio = luma_frame_stats(_plane(rows), 32, 32, 24)
        self.assertEqual(box, CropBox(26, 24, 6, 0))

    def test_noisy_bars_are_cropped(self) -> None:
        """띠 위 +-2 노이즈는 평탄도 기준 안이라 띠로 인정"""
        rows = [_noisy_row(32, 8) for _ in range(4)] + [[120] * 32 for _ in range(20)]
        box, _ratio = luma_frame_stats(_plane(rows), 32, 32, 24)
        self.assertEqual(box, CropBox(32, 20, 0, 4))

    def test_limited_range_bar_is_cropped(self) -> None:
        """제한 범위 검은색 16에 노이즈가 있는 아래 띠도 자름"""
        rows = [[120] * 32 for _ in range(18)] + [_noisy_row(32, 16) for _ in range(6)]
        box, _ratio = luma_frame_stats(_plane(rows), 32, 32, 24)
        self.assertEqual(box, CropBox(32, 18, 0, 0))

    def test_vignette_gradient_is_not_a_bar(self) -> None:
        """어두운 장면의 완만한 비네트는 평탄도와 계단 조건을 못 넘어 여백이 아님"""
        width, ramp = 32, 44
        rows = [[120] * width for _ in range(30)]
        for step in range(ramp):
            ratio = step / (ramp - 1)
            rows.append(_gradient_row(width, 27 - 22 * ratio, 15 - 13 * ratio))
        box, _ratio = luma_frame_stats(_plane(rows), width, width, len(rows))
        self.assertEqual(box, CropBox(width, len(rows), 0, 0))

    def test_text_island_inside_top_bar_crops_below_text(self) -> None:
        """띠 안 자막 줄은 섬으로 보고 지나쳐, 자막 아래 띠 경계를 씀"""
        rows = [[16] * 200 for _ in range(40)]
        rows += [_gradient_row(200, 150, 40) for _ in range(12)]
        rows += [[16] * 200 for _ in range(30)]
        rows += [[130] * 200 for _ in range(158)]
        box, _ratio = luma_frame_stats(_plane(rows), 200, 200, len(rows))
        self.assertEqual(box, CropBox(200, 158, 0, 82))

    def test_text_attached_to_picture_is_not_skipped(self) -> None:
        """띠 간격 없이 그림에 붙은 자막은 그림으로 남겨 잘라내지 않음"""
        rows = [[16] * 32 for _ in range(4)]
        rows += [_gradient_row(32, 150, 40) for _ in range(3)]
        rows += [[130] * 32 for _ in range(17)]
        box, _ratio = luma_frame_stats(_plane(rows), 32, 32, len(rows))
        self.assertEqual(box, CropBox(32, 20, 0, 4))

    def test_thick_text_block_is_not_skipped(self) -> None:
        """축 길이 대비 섬 상한보다 두꺼운 비-띠 구간은 그림으로 보고 지나치지 않음"""
        rows = [[16] * 200 for _ in range(6)]
        rows += [[120] * 200 for _ in range(8)]
        rows += [[16] * 200 for _ in range(6)]
        rows += [[120] * 200 for _ in range(40)]
        box, _ratio = luma_frame_stats(_plane(rows), 200, 200, len(rows))
        self.assertEqual(box, CropBox(200, 54, 0, 6))

    def test_dark_scene_without_black_ratio_has_no_bars(self) -> None:
        """검은 화면 기준에 못 미치는 어두운 장면은 띠 없이 전체 프레임"""
        rows = [[20 + step] * 32 for step in range(21)]
        box, ratio = luma_frame_stats(_plane(rows), 32, 32, 21)
        self.assertLess(ratio, BLACK_FRAME_PIXEL_RATIO)
        self.assertEqual(box, CropBox(32, 21, 0, 0))

    def test_text_island_inside_left_pillar_crops_right_of_text(self) -> None:
        """왼쪽 기둥 안 세로 자막 열은 섬으로 보고 지나쳐, 자막 오른쪽 띠 경계를 씀"""
        width, height = 200, 60
        rows = [
            [16] * 20 + [110 if row_index % 2 == 0 else 190] * 8 + [16] * 10 + [130] * 162
            for row_index in range(height)
        ]
        box, _ratio = luma_frame_stats(_plane(rows), width, width, height)
        self.assertEqual(box, CropBox(162, 60, 38, 0))

    def test_text_island_inside_right_pillar_crops_left_of_text(self) -> None:
        """오른쪽 기둥 안 세로 자막 열도 섬으로 보고 지나쳐, 자막 왼쪽 띠 경계를 씀"""
        width, height = 200, 60
        rows = [
            [130] * 162 + [16] * 10 + [110 if row_index % 2 == 0 else 190] * 8 + [16] * 20
            for row_index in range(height)
        ]
        box, _ratio = luma_frame_stats(_plane(rows), width, width, height)
        self.assertEqual(box, CropBox(162, 60, 0, 0))

    def test_horizontal_vignette_is_not_a_column_bar(self) -> None:
        """좌우 어두운 완만한 비네트는 열 평탄도와 계단 조건을 못 넘어 여백이 아님"""
        width, height, ramp, picture = 60, 40, 22, 38
        rows = []
        for row_index in range(height):
            values = []
            for column in range(ramp):
                ratio = column / (ramp - 1)
                mean = 5 + 22 * ratio
                deviation = 2 + 13 * ratio
                values.append(round(mean + (deviation if row_index % 2 == 0 else -deviation)))
            rows.append(values + [120] * picture)
        box, _ratio = luma_frame_stats(_plane(rows), width, width, height)
        self.assertEqual(box, CropBox(width, height, 0, 0))

    def test_union_keeps_the_thickest_bar_of_each_side(self) -> None:
        """프레임별 상자 합집합은 어느 표본에서도 그림을 자르지 않음"""
        frames = [
            _plane([[16] * 32 for _ in range(10)] + [[130] * 32 for _ in range(78)] + [[16] * 32 for _ in range(12)]),
            _plane([[16] * 32 for _ in range(14)] + [[130] * 32 for _ in range(74)] + [[16] * 32 for _ in range(12)]),
        ]
        boxes = [box for data in frames if (box := luma_frame_stats(data, 32, 32, 100)[0]) is not None]
        region = merge_crop_union(boxes, 32, 100)
        self.assertTrue(region.has_black_bars)
        self.assertEqual((region.width, region.height, region.x, region.y), (32, 78, 0, 10))

    def test_union_of_full_frame_boxes_has_no_bars(self) -> None:
        """완만한 그라데이션만 있는 표본에서는 상자가 프레임 전체라 검은 띠 없음"""
        frames = []
        for _ in range(2):
            rows = [[120] * 32 for _ in range(10)]
            rows += [_gradient_row(32, 27 - 22 * step / 21, 15 - 13 * step / 21) for step in range(22)]
            frames.append(_plane(rows))
        boxes = [box for data in frames if (box := luma_frame_stats(data, 32, 32, 32)[0]) is not None]
        region = merge_crop_union(boxes, 32, 32)
        self.assertFalse(region.has_black_bars)
        self.assertEqual((region.width, region.height, region.x, region.y), (32, 32, 0, 0))



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

