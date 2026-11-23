"""화질 분석 1단계 9지점 키프레임 휘도(Y) 스캔 모듈

- `PyAV` 컨테이너를 한 번만 열어 키프레임의 Y 평면을 직접 읽어 외부 프로세스 없이 검은 화면과 검은 여백을 찾음
- `numpy` 없이 `bytes.translate` 룩업 테이블과 `memoryview`/`bytes` 슬라이스 합만으로 픽셀 비율과 줄, 열 평균을 계산
- PyAV 열기나 디코딩이 실패하거나 테스트가 실행기를 주입하면 FFmpeg CLI 9지점 키프레임 스캔으로 폴백
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, BinaryIO, cast

from src.core.analysis.frame_analysis import (
    CROP_DETECT_LIMIT,
    CROP_DETECT_ROUND,
    CropBox,
    parse_cropdetect_boxes,
)
from src.core.file_sharing import open_shared_binary

if TYPE_CHECKING:
    from av.container import InputContainer
    from av.video.frame import VideoFrame
    from av.video.stream import VideoStream

logger = logging.getLogger(__name__)

ScanCommandRunner = Callable[[list[str], str], tuple[int, str]]
PointCallback = Callable[[int, int], None]

# - 검은 프레임 판정: Y < 26 픽셀 비율 98% 이상(12.19절), 여백 줄, 열 평균 24 이하
BLACK_LUMA_THRESHOLD: int = 26
BLACK_FRAME_PIXEL_RATIO: float = 0.98
BLACK_EDGE_MEAN_THRESHOLD: float = 24.0
SCAN_BLACKFRAME_THRESHOLD: int = 24
FULL_TURN_DEGREES: int = 360
HALF_TURN_DEGREES: int = 180
QUARTER_TURN_DEGREES: int = 90
FALLBACK_TIME_BASE_SEC: float = 1.0 / 1000.0
BLACKFRAME_PBLACK_RE: re.Pattern[str] = re.compile(r"pblack:\s*(\d+)")
_EIGHT_BIT_LUMA_FORMATS: frozenset[str] = frozenset(
    {"gray", "yuv420p", "yuv422p", "yuv444p", "yuvj420p", "yuvj422p", "yuvj444p", "nv12", "nv21"}
)
_BLACK_LUMA_TABLE: bytes = bytes(1 if value < BLACK_LUMA_THRESHOLD else 0 for value in range(256))
_CANCEL_MESSAGE: str = "화질 분석이 취소되었습니다"


class ScanDecodeError(RuntimeError):
    """PyAV로 9지점 키프레임을 읽지 못했을 때"""


class KeyframeScanResult:
    """9지점 스캔에서 찾은 검은 여백 상자와 검은 화면 지점"""

    __slots__ = ("black_points", "boxes")

    def __init__(self, boxes: Sequence[CropBox], black_points: Sequence[int]) -> None:
        self.boxes: tuple[CropBox, ...] = tuple(boxes)
        self.black_points: frozenset[int] = frozenset(black_points)


def build_scan_command(
    *,
    ffmpeg_executable: str,
    source_path: str,
    start_sec: float,
    fast_keyframe_seek: bool,
) -> list[str]:
    """CLI 9지점 폴백에서 키프레임 한 장의 검은 화면과 활성 화면을 재는 인자 목록"""
    args = [
        ffmpeg_executable,
        "-hide_banner",
        "-nostdin",
        "-ss",
        f"{start_sec:.3f}",
    ]
    if fast_keyframe_seek:
        args.append("-noaccurate_seek")
    args.extend(
        [
            "-skip_frame",
            "nokey",
            "-i",
            source_path,
            "-frames:v",
            "1",
            "-vf",
            (
                f"blackframe=amount=0:threshold={SCAN_BLACKFRAME_THRESHOLD},"
                f"cropdetect={CROP_DETECT_LIMIT}:{CROP_DETECT_ROUND}:0:0"
            ),
            "-an",
            "-f",
            "null",
            "-",
        ]
    )
    return args


def parse_blackframe_pixel_ratio(stderr_text: str) -> float:
    """`blackframe` 로그의 검은 픽셀 비율(0.0 ~ 1.0), 로그가 없으면 0"""
    ratios = [min(100, int(raw)) / 100.0 for raw in BLACKFRAME_PBLACK_RE.findall(stderr_text)]
    return max(ratios, default=0.0)


def _row_mean(view: memoryview, offset: int, width: int) -> float:
    """Y 평면 행 하나의 평균"""
    if width <= 0:
        return 0.0
    return sum(view[offset : offset + width]) / width


def _column_mean(plane: bytes, x: int, top: int, bottom: int, line_size: int) -> float:
    """Y 평면 열 하나의 평균. `bytes` 확장 슬라이스로 행을 건너뛰며 더함"""
    rows = bottom - top
    if rows <= 0:
        return 0.0
    start = top * line_size + x
    stop = (bottom - 1) * line_size + x + 1
    column = plane[start:stop:line_size]
    return sum(column) / rows if column else 0.0

def luma_frame_stats(data: bytes, line_size: int, width: int, height: int) -> tuple[CropBox | None, float]:
    """Y 평면 한 장에서 검은 픽셀 비율과 검은 여백 상자 계산

    - 검은 픽셀 비율이 기준 이상이면 검은 화면으로 보고 상자를 내지 않음
    - 상/하 줄 평균과 좌/우 열 평균이 기준 이하인 구간을 검은 여백으로 봄
    """
    if width <= 0 or height <= 0 or line_size < width:
        return (None, 1.0)
    plane = bytes(data)
    if len(plane) < line_size * height:
        return (None, 1.0)
    translated = plane.translate(_BLACK_LUMA_TABLE)
    black_view = memoryview(translated)
    view = memoryview(plane)
    black_pixels = 0
    for row_index in range(height):
        offset = row_index * line_size
        black_pixels += sum(black_view[offset : offset + width])
    black_ratio = black_pixels / (width * height)
    if black_ratio >= BLACK_FRAME_PIXEL_RATIO:
        return (None, black_ratio)

    top = 0
    while top < height and _row_mean(view, top * line_size, width) <= BLACK_EDGE_MEAN_THRESHOLD:
        top += 1
    bottom = height
    while bottom > top and _row_mean(view, (bottom - 1) * line_size, width) <= BLACK_EDGE_MEAN_THRESHOLD:
        bottom -= 1
    left = 0
    while left < width and _column_mean(plane, left, top, bottom, line_size) <= BLACK_EDGE_MEAN_THRESHOLD:
        left += 1
    right = width
    while right > left and _column_mean(plane, right - 1, top, bottom, line_size) <= BLACK_EDGE_MEAN_THRESHOLD:
        right -= 1
    if right <= left or bottom <= top:
        return (None, black_ratio)
    return (CropBox(width=right - left, height=bottom - top, x=left, y=top), black_ratio)


def _declared_rotation(stream: VideoStream) -> int:
    """스트림 메타데이터의 회전 각도, 없거나 읽지 못하면 0"""
    rotate = stream.metadata.get("rotate")
    if rotate is None:
        return 0
    try:
        return int(rotate)
    except ValueError:
        return 0


def _frame_rotation(frame: VideoFrame, stream: VideoStream) -> int:
    """프레임 디스플레이 회전을 시계 방향 각도로 환산, 프레임 값이 0이면 스트림 메타데이터 사용

    - PyAV의 `frame.rotation`은 FFmpeg 규약대로 반시계 방향 각도이므로 부호를 뒤집음
    """
    rotation = frame.rotation
    if rotation != 0:
        return (-rotation) % FULL_TURN_DEGREES
    return _declared_rotation(stream) % FULL_TURN_DEGREES


def transpose_crop_box(box: CropBox, coded_width: int, coded_height: int, rotation: int) -> CropBox:
    """코딩 방향 상자를 표시 회전이 적용된 방향으로 전치

    - 90도: 시계 방향 회전 결과 기준으로 좌표를 옮김
    - 180도: 코딩 크기 기준으로 x, y를 반전
    - 270도: 반시계 방향 회전 결과 기준으로 좌표를 옮김
    """
    turn = rotation % FULL_TURN_DEGREES
    if turn == HALF_TURN_DEGREES:
        return CropBox(
            width=box.width,
            height=box.height,
            x=max(0, coded_width - (box.x + box.width)),
            y=max(0, coded_height - (box.y + box.height)),
        )
    if turn % HALF_TURN_DEGREES == 0:
        return box
    if turn == QUARTER_TURN_DEGREES:
        return CropBox(
            width=box.height,
            height=box.width,
            x=max(0, coded_height - (box.y + box.height)),
            y=max(0, box.x),
        )
    return CropBox(
        width=box.height,
        height=box.width,
        x=max(0, box.y),
        y=max(0, coded_width - (box.x + box.width)),
    )


def _luma_bytes(frame: VideoFrame) -> tuple[bytes, int, int, int]:
    """프레임의 8-bit Y 평면 바이트, 행 바이트 수, 너비, 높이. 10-bit 등은 8-bit 회색조로 변환"""
    target = frame if frame.format.name in _EIGHT_BIT_LUMA_FORMATS else frame.reformat(format="gray")
    plane = target.planes[0]
    return (bytes(plane), plane.line_size, target.width, target.height)

def _scan_with_pyav(
    source_path: str,
    points: Sequence[float],
    *,
    cancel_event: threading.Event | None,
    on_point: PointCallback | None,
) -> KeyframeScanResult:
    """PyAV 컨테이너를 한 번 열고 9지점 키프레임의 Y 평면을 직접 스캔"""
    import av

    source: BinaryIO | None = open_shared_binary(source_path)
    container: InputContainer | None = None
    try:
        opened = av.open(source_path) if source is None else av.open(source)
        container = cast("InputContainer", opened)
        streams = container.streams.video
        if not streams:
            raise ScanDecodeError("영상 스트림이 없어 키프레임을 읽지 못했습니다.")
        stream = streams[0]
        time_base = stream.time_base
        seconds_per_tick = FALLBACK_TIME_BASE_SEC if time_base is None else float(time_base)
        boxes: list[CropBox] = []
        black_points: list[int] = []
        total = len(points)
        for index, start_sec in enumerate(points):
            _check_cancel(cancel_event)
            offset = max(0, round(start_sec / seconds_per_tick))
            container.seek(offset, backward=True, any_frame=False, stream=stream)
            frames = container.decode(video=0)
            try:
                frame = next(frames)
            except StopIteration as error:
                raise ScanDecodeError("요청한 시점의 키프레임이 없습니다.") from error
            data, line_size, width, height = _luma_bytes(frame)
            box, black_ratio = luma_frame_stats(data, line_size, width, height)
            if black_ratio >= BLACK_FRAME_PIXEL_RATIO:
                black_points.append(index)
            elif box is not None:
                boxes.append(transpose_crop_box(box, width, height, _frame_rotation(frame, stream)))
            if on_point is not None:
                on_point(index + 1, total)
        return KeyframeScanResult(boxes, black_points)
    finally:
        if container is not None:
            container.close()
        if source is not None:
            source.close()


def scan_with_cli(
    *,
    source_path: str,
    points: Sequence[float],
    ffmpeg_executable: str,
    runner: ScanCommandRunner,
    work_dir: str,
    fast_keyframe_seek: bool,
    cancel_event: threading.Event | None,
    on_point: PointCallback | None,
) -> KeyframeScanResult:
    """FFmpeg CLI 9지점 키프레임 통합 스캔. 실패한 지점은 검은 화면으로 봄"""
    boxes: list[CropBox] = []
    black_points: list[int] = []
    total = len(points)
    for index, start_sec in enumerate(points):
        _check_cancel(cancel_event)
        returncode, stderr = runner(
            build_scan_command(
                ffmpeg_executable=ffmpeg_executable,
                source_path=source_path,
                start_sec=start_sec,
                fast_keyframe_seek=fast_keyframe_seek,
            ),
            work_dir,
        )
        if returncode != 0:
            black_points.append(index)
        else:
            black_ratio = parse_blackframe_pixel_ratio(stderr)
            if black_ratio >= BLACK_FRAME_PIXEL_RATIO:
                black_points.append(index)
            else:
                boxes.extend(parse_cropdetect_boxes(stderr))
        if on_point is not None:
            on_point(index + 1, total)
    return KeyframeScanResult(boxes, black_points)


def scan_keyframe_points(
    *,
    source_path: str,
    points: Sequence[float],
    ffmpeg_executable: str,
    runner: ScanCommandRunner,
    work_dir: str,
    fast_keyframe_seek: bool,
    cancel_event: threading.Event | None = None,
    on_point: PointCallback | None = None,
    prefer_pyav: bool = True,
) -> KeyframeScanResult:
    """PyAV 직독을 먼저 시도하고 실패하면 CLI 9지점 스캔으로 폴백"""
    if prefer_pyav:
        try:
            return _scan_with_pyav(source_path, points, cancel_event=cancel_event, on_point=on_point)
        except InterruptedError:
            raise
        except Exception:
            logger.warning("PyAV 키프레임 스캔 실패, CLI 9지점 스캔으로 폴백", exc_info=True)
    return scan_with_cli(
        source_path=source_path,
        points=points,
        ffmpeg_executable=ffmpeg_executable,
        runner=runner,
        work_dir=work_dir,
        fast_keyframe_seek=fast_keyframe_seek,
        cancel_event=cancel_event,
        on_point=on_point,
    )


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError(_CANCEL_MESSAGE)
