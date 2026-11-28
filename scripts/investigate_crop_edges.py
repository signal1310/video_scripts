"""화면 여백 경계 잔여 열, 줄 조사와 합집합 활성 화면 비교 스크립트

- `_ref`는 읽기만 하고 임시 파일은 시스템 temp 폴더에만 둠
- 판정은 제품 `luma_frame_stats`, `merge_crop_union`, `scan_keyframe_points`를 그대로 부름
- 경계 진단은 `_bar_scan`과 같은 순서로 제품 규칙 함수를 직접 불러 어느 규칙에서 멈췄는지 남김
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import av

from src.core.analysis.frame_analysis import (
    DEFAULT_FRAME_ANALYSIS_MODE,
    ActiveRegion,
    CropBox,
    EdgeDarkness,
    frame_analysis_params,
    merge_crop_union,
)
from src.core.analysis.frame_scan import (
    FLAT_RAMP_SPREAD_LIMIT,
    RESIDUAL_MARGIN_MAX_LINES,
    RESIDUAL_MARGIN_MEAN_LIMIT,
    RESIDUAL_MARGIN_STEP_MIN,
    _bar_scan,  # pyright: ignore[reportPrivateUsage]
    _blend_edge_end,  # pyright: ignore[reportPrivateUsage]
    _column_stats,  # pyright: ignore[reportPrivateUsage]
    _dark_flat_run,  # pyright: ignore[reportPrivateUsage]
    _flat_bar_end,  # pyright: ignore[reportPrivateUsage]
    _has_edge_step,  # pyright: ignore[reportPrivateUsage]
    _is_bar_line,  # pyright: ignore[reportPrivateUsage]
    _island_max_lines,  # pyright: ignore[reportPrivateUsage]
    _luma_bytes,  # pyright: ignore[reportPrivateUsage]
    _residual_margin_end,  # pyright: ignore[reportPrivateUsage]
    _row_stats,  # pyright: ignore[reportPrivateUsage]
    _text_island_end,  # pyright: ignore[reportPrivateUsage]
    luma_frame_stats,
    scan_keyframe_points,
)
from src.core.sample_points import DEFAULT_SEGMENT_COUNT, sample_segments

HDR_TRANSFERS: frozenset[str] = frozenset({"smpte2084", "arib-std-b67"})
EDGE_WINDOW: int = 6
VIDEO_SUFFIXES: frozenset[str] = frozenset({".mp4", ".mkv", ".avi", ".wmv", ".ts"})
FFPROBE: str = "ffprobe"
FFMPEG: str = "ffmpeg"
STDERR_KEEP_CHARS: int = 2000
HALF_TURN_DEGREES: int = 180
FULL_TURN_DEGREES: int = 360

LineStats = tuple[float, float]
Probe = Callable[[int], LineStats]
Plane = tuple[bytes, int, int, int, int]


def probe_stream(path: Path) -> tuple[int, int, float, str]:
    """ffprobe로 너비, 높이, 길이, 색 전달 함수를 읽음"""
    args = [
        FFPROBE,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,color_transfer",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1",
        str(path),
    ]
    text = subprocess.run(args, check=True, capture_output=True, text=True).stdout
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return (
        int(values["width"]),
        int(values["height"]),
        float(values["duration"]),
        values.get("color_transfer", ""),
    )


def run_command(args: list[str], work_dir: str) -> tuple[int, str]:
    """CLI 폴백용 명령 실행기"""
    done = subprocess.run(args, cwd=work_dir, capture_output=True, check=False)
    return (done.returncode, done.stderr.decode("utf-8", errors="replace")[-STDERR_KEEP_CHARS:])


def scan_points(duration_sec: float) -> list[float]:
    """제품 기본 방식의 9지점 키프레임 시각"""
    params = frame_analysis_params(DEFAULT_FRAME_ANALYSIS_MODE)
    segments = sample_segments(duration_sec, DEFAULT_SEGMENT_COUNT, params.segment_duration_sec)
    return [start for start, _duration in segments]


def merged_region(
    path: Path, points: Sequence[float], work_dir: str
) -> tuple[ActiveRegion, tuple[CropBox, ...], tuple[EdgeDarkness | None, ...]]:
    """제품 스캔과 합집합으로 활성 화면 영역, 지점 상자, 지점별 어두운 면 두께를 구함"""
    width, height, _duration, _transfer = probe_stream(path)
    params = frame_analysis_params(DEFAULT_FRAME_ANALYSIS_MODE)
    scan = scan_keyframe_points(
        source_path=str(path),
        points=list(points),
        ffmpeg_executable=FFMPEG,
        runner=run_command,
        work_dir=work_dir,
        fast_keyframe_seek=params.fast_keyframe_seek,
        cancel_event=None,
        on_point=None,
        prefer_pyav=True,
    )
    region = merge_crop_union(list(scan.boxes), width, height, darkness=list(scan.darkness))
    return region, scan.boxes, scan.darkness


def read_planes(path: Path, points: Sequence[float]) -> list[Plane]:
    """지점별 키프레임 Y 평면, 행 바이트 수, 너비, 높이, 표시 회전 각도"""
    planes: list[Plane] = []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        time_base = stream.time_base
        seconds_per_tick = 0.001 if time_base is None else float(time_base)
        for start_sec in points:
            offset = max(0, round(start_sec / seconds_per_tick))
            container.seek(offset, backward=True, any_frame=False, stream=stream)
            frame = next(container.decode(video=0))
            data, line_size, width, height = _luma_bytes(frame)
            rotation = (-frame.rotation) % FULL_TURN_DEGREES if frame.rotation != 0 else 0
            planes.append((data, line_size, width, height, rotation))
    return planes


def vertical_bounds(height: int, row_probe: Probe) -> tuple[int, int, int, int]:
    """제품과 같은 줄 스캔으로 위, 아래 여백 줄 수와 근거 두께"""
    island_lines = _island_max_lines(height)
    top, top_dark = _bar_scan(height, row_probe, from_end=False, island_max_lines=island_lines)
    bottom_count, bottom_dark = _bar_scan(height, row_probe, from_end=True, island_max_lines=island_lines)
    return top, bottom_count, top_dark, bottom_dark


def trace_axis(axis_length: int, probe: Probe, *, from_end: bool) -> tuple[int, str]:
    """`_bar_scan`과 같은 순서로 축 한 끝 판정을 따라가며 멈춘 규칙 이름을 남김"""

    def line_index(offset: int) -> int:
        return axis_length - 1 - offset if from_end else offset

    dark_run = _dark_flat_run(axis_length, probe, line_index)
    if dark_run >= axis_length:
        return (dark_run, "_dark_flat_run 이 축 전체")
    if not _has_edge_step(probe, line_index, dark_run):
        flat_end = _flat_bar_end(axis_length, probe, line_index)
        if flat_end is None:
            return (0, "_flat_bar_end(None)")
        bar_count = flat_end
        rule = "_flat_bar_end"
    else:
        bar_count = dark_run
        rule = "_dark_flat_run(계단 통과)"
    offset = _blend_edge_end(bar_count, axis_length, probe, line_index)
    reasons: list[str] = []
    if offset > bar_count:
        reasons.append(f"_blend_edge_end(+{offset - bar_count})")
    after_residual = _residual_margin_end(offset, axis_length, probe, line_index)
    if after_residual > offset:
        reasons.append(f"_residual_margin_end(+{after_residual - offset})")
    offset = after_residual
    for _ in range(RESIDUAL_MARGIN_MAX_LINES):
        island_end = _text_island_end(offset, axis_length, probe, line_index, _island_max_lines(axis_length))
        if island_end is None:
            break
        offset = _blend_edge_end(island_end, axis_length, probe, line_index)
        offset = _residual_margin_end(offset, axis_length, probe, line_index)
        reasons.append(f"_text_island_end->{offset}")
    return (offset, rule + ("" if not reasons else " " + " ".join(reasons)))


def residual_stop_reason(axis_length: int, probe: Probe, *, from_end: bool, start: int) -> str:
    """잔여 여백 규칙이 멈춘 이유를 조건별로 남김"""

    def line_index(offset: int) -> int:
        return axis_length - 1 - offset if from_end else offset

    mean, spread = probe(line_index(start))
    next_mean = probe(line_index(start + 1))[0]
    if mean > RESIDUAL_MARGIN_MEAN_LIMIT:
        return f"mean {mean:.2f} > RESIDUAL_MARGIN_MEAN_LIMIT {RESIDUAL_MARGIN_MEAN_LIMIT:g}"
    if spread > FLAT_RAMP_SPREAD_LIMIT:
        return f"spread {spread:.2f} > FLAT_RAMP_SPREAD_LIMIT {FLAT_RAMP_SPREAD_LIMIT:g}"
    if next_mean - mean < RESIDUAL_MARGIN_STEP_MIN:
        return f"step {next_mean - mean:.2f} < RESIDUAL_MARGIN_STEP_MIN {RESIDUAL_MARGIN_STEP_MIN:g}"
    return "규칙 통과(다음 반복 없음)"


def print_edge_window(
    label: str,
    axis_length: int,
    probe: Probe,
    edge_offset: int,
    *,
    from_end: bool,
) -> None:
    """검출 경계 양옆 열의 평균, 편차, 이웃과의 계단을 출력"""

    def line_index(offset: int) -> int:
        return axis_length - 1 - offset if from_end else offset

    first = max(0, edge_offset - EDGE_WINDOW)
    last = min(axis_length, edge_offset + EDGE_WINDOW)
    print(f"    {label} 경계 offset={edge_offset} (축 길이 {axis_length})")
    print("      offset   mean  spread     step  bar")
    previous: float | None = None
    for offset in range(first, last):
        mean, spread = probe(line_index(offset))
        step = "      -" if previous is None else f"{mean - previous:+7.2f}"
        bar = "Y" if _is_bar_line((mean, spread)) else "."
        print(f"      {offset:6d} {mean:6.2f} {spread:6.2f} {step:>8}  {bar}")
        previous = mean


def trace_point(index: int, plane: Plane) -> dict[str, object]:
    """지점 한 개의 상자, 위아래 범위, 좌우 판정 근거를 계산해 출력"""
    data, line_size, width, height, rotation = plane
    box, black_ratio, edges = luma_frame_stats(data, line_size, width, height)
    print(f"\n  지점 {index + 1}: {width}x{height} 회전={rotation} 검은픽셀={black_ratio:.3f} 상자={box}")
    if box is None:
        return {"point": index + 1, "box": None}

    def row_probe(row: int) -> LineStats:
        return _row_stats(data, row * line_size, width)

    top, bottom_count, top_dark, bottom_dark = vertical_bounds(height, row_probe)
    bottom = height - bottom_count

    def column_probe(column: int) -> LineStats:
        return _column_stats(data, column, top, bottom, line_size)

    left, left_rule = trace_axis(width, column_probe, from_end=False)
    right_count, right_rule = trace_axis(width, column_probe, from_end=True)
    print(f"    위/아래 여백={top}/{bottom_count} (근거 {top_dark}/{bottom_dark})")
    print(f"    왼쪽 {left} [{left_rule}] / 오른쪽 {width - right_count} [{right_rule}]")
    print_edge_window("왼쪽", width, column_probe, left, from_end=False)
    print_edge_window("오른쪽", width, column_probe, right_count, from_end=True)
    print(f"    왼쪽 잔여 정지 사유: {residual_stop_reason(width, column_probe, from_end=False, start=left)}")
    print(f"    오른쪽 잔여 정지 사유: {residual_stop_reason(width, column_probe, from_end=True, start=right_count)}")
    return {
        "point": index + 1,
        "box": box,
        "edges": edges,
        "left": left,
        "right": width - right_count,
    }


def format_region(region: ActiveRegion) -> str:
    """합집합 영역을 표에 쓰는 문자열로 만듦"""
    bars = "여백" if region.has_black_bars else "전체"
    return f"{region.width}x{region.height} at {region.x},{region.y} {bars}"


def iter_videos(ref_dir: Path) -> list[Path]:
    """`_ref` 바로 아래와 `_ref/clips`의 영상 파일 목록"""
    paths = [
        path for path in ref_dir.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    ]
    clips = ref_dir / "clips"
    if clips.is_dir():
        paths.extend(
            path for path in clips.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
        )
    return sorted(paths)


def run_report(args: argparse.Namespace) -> int:
    """합집합 상자 표와 경계 진단 본문을 stdout으로 출력"""
    ref_dir = Path(args.ref_dir)
    work_dir = tempfile.mkdtemp(prefix="crop_edges_")
    videos = iter_videos(ref_dir)
    if args.only:
        videos = [path for path in videos if path.name.startswith(args.only)]
    if args.limit > 0:
        videos = videos[: args.limit]

    print(f"임시 작업 폴더: {work_dir}")
    print("\n| 파일 | 원본 | 활성 화면 | 지점 상자 |")
    print("| :--- | :--- | :--- | :--- |")
    trace_planes: list[Plane] = []
    trace_points: list[float] = []
    dumped: list[dict[str, object]] = []
    for path in videos:
        try:
            width, height, duration, transfer = probe_stream(path)
        except (subprocess.CalledProcessError, KeyError):
            print(f"| {path.name} | 읽기 실패 | - | - |")
            continue
        if transfer.lower() in HDR_TRANSFERS:
            print(f"| {path.name} | {width}x{height} HDR({transfer}) 건너뜀 | - | - |")
            continue
        points = scan_points(duration)
        region, boxes, darkness = merged_region(path, points, work_dir)
        distinct = sorted({(box.width, box.height, box.x, box.y) for box in boxes})
        summary = ", ".join(f"{w}x{h}+{x}+{y}" for w, h, x, y in distinct) if distinct else "-"
        print(f"| {path.name} | {width}x{height} | {format_region(region)} | {summary} |")
        dumped.append(
            {
                "path": str(path),
                "width": width,
                "height": height,
                "boxes": [[box.width, box.height, box.x, box.y] for box in boxes],
                "darkness": [
                    None if edge is None else [edge.top, edge.bottom, edge.left, edge.right]
                    for edge in darkness
                ],
            }
        )
        if not args.boxes_only and path.name.startswith(args.trace):
            trace_planes = read_planes(path, points)
            trace_points = points

    if args.json:
        Path(args.json).write_text(json.dumps(dumped, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n지점 상자 JSON: {args.json}")

    if not trace_planes:
        return 0
    print(
        f"\n=== {args.trace} 경계 진단 "
        "(규칙 순서: _dark_flat_run -> _flat_bar_end -> _blend_edge_end -> _residual_margin_end -> _text_island_end) ==="
    )
    limit = len(trace_planes) if args.max_points <= 0 else min(len(trace_planes), args.max_points)
    for index in range(limit):
        print(f"  지점 시각 {trace_points[index]:.3f}s")
        trace_point(index, trace_planes[index])
    return 0


def main() -> int:
    """인자에 따라 합집합 상자 표와 경계 진단을 출력"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref-dir", required=True, help="_ref 폴더 경로")
    parser.add_argument("--trace", default="video23.mp4", help="경계를 진단할 파일 이름 접두사")
    parser.add_argument("--boxes-only", action="store_true", help="합집합 상자 표만 출력")
    parser.add_argument("--limit", type=int, default=0, help="표에 넣을 파일 수 상한(0은 전체)")
    parser.add_argument("--only", default="", help="이름 접두사로 맞는 파일만 표에 넣음")
    parser.add_argument("--json", default="", help="지점 상자를 JSON으로 저장할 경로")
    parser.add_argument("--max-points", type=int, default=0, help="경계 진단을 찍을 지점 수 상한(0은 전체)")
    parser.add_argument("--out", default="", help="보고서를 저장할 파일 경로(비우면 표준 출력)")
    args = parser.parse_args()
    if not args.out:
        return run_report(args)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = run_report(args)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(buffer.getvalue(), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
