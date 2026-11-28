"""비검은 단색 여백 감지 후보 규칙 조사 스크립트

- `_ref`는 읽기만 하고 결과 JSON은 시스템 temp 폴더에만 둠
- 지점 상자는 제품 `luma_frame_stats`와 `merge_crop_union`을 그대로 부르고, 후보 규칙은
  키프레임 Y 평면에 직접 적용
- 줄 평균은 제품 `_row_stats`, `_column_stats`를 그대로 쓰고, 워터마크처럼 한 줄 일부만 밝은
  덧그림에 흔들리지 않도록 위아래 10%를 뺀 편차를 평탄 판정에 씀
- 사용법
  - `--mode margins --only video24`: 지점별 네 면 평탄 구간, 평균, 경계 계단
  - `--mode sweep`: 비검은 평탄 구간이 나타난 영상과 지점 간 일정 여부
  - `--mode suspects`: 6줄 이상 구간이 나타난 모든 면의 지점별 근거
  - `--mode rule`: 후보 규칙 상자와 제품 합집합 상자 비교
  - `--mode edges`: 첫 유지 줄의 링잉 과충격과 한 줄 안쪽 보정 전후 상자
  - `--mode columns --columns 104-110,370-376`: 지정한 열의 지점별 평균
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import av

from src.core.analysis.frame_analysis import (
    DEFAULT_FRAME_ANALYSIS_MODE,
    EDGE_SNAP_MIN_PX,
    EDGE_SNAP_RATIO,
    MARGIN_AGREE_DENOMINATOR,
    MARGIN_AGREE_NUMERATOR,
    CropBox,
    EdgeDarkness,
    frame_analysis_params,
    merge_crop_union,
)
from src.core.analysis.frame_scan import (
    BLACK_EDGE_MEAN_THRESHOLD,
    _column_stats,  # pyright: ignore[reportPrivateUsage]
    _luma_bytes,  # pyright: ignore[reportPrivateUsage]
    _row_stats,  # pyright: ignore[reportPrivateUsage]
    luma_frame_stats,
    transpose_crop_box,
    transpose_edge_darkness,
)
from src.core.sample_points import DEFAULT_SEGMENT_COUNT, sample_segments

# - 후보 규칙 상수. 값의 근거는 조사 결과로 정함
NON_DARK_MIN_LINES: int = 6
NON_DARK_SPREAD_LIMIT: float = 3.0
NON_DARK_LEVEL_TOLERANCE: float = 2.0
NON_DARK_STEP_MIN: float = 6.0
# - 평탄 구간 끝에 붙는 반화소 혼합 줄 최대 수. 혼합 줄은 중앙값이 여백 그대로이고 그림 쪽으로 오름
NON_DARK_TRANSITION_MAX_LINES: int = 2
# - 첫 유지 줄이 안쪽 이웃보다 이만큼 밝으면 압축 링잉 과충격 줄로 보고 한 줄 안쪽으로 옮김
EDGE_OVERSHOOT_LEVELS: float = 2.0
EDGE_ALIGN: int = 2
SAMPLE_STRIDE: int = 2
# - 줄 안 편차를 잴 때 위아래에서 빼는 비율. 한 줄 일부만 밝은 덧그림(방송사 워터마크)은
#   이 비율보다 적게 차지해 편차에 들어오지 않음
TRIM_RATIO: float = 0.10
HDR_TRANSFERS: frozenset[str] = frozenset({"smpte2084", "arib-std-b67"})
VIDEO_SUFFIXES: frozenset[str] = frozenset({".mp4", ".mkv", ".avi", ".wmv", ".ts"})
FFPROBE: str = "ffprobe"
FULL_TURN_DEGREES: int = 360
SIDE_NAMES: tuple[str, str, str, str] = ("top", "bottom", "left", "right")

# - (Y 평면, 행 바이트 수, 너비, 높이, 표시 회전 각도)
Plane = tuple[bytes, int, int, int, int]
# - (너비, 높이, x, y)
BoxRow = tuple[int, int, int, int]
# - (평균, 중앙값, 위아래 10%를 뺀 편차)
LineSample = tuple[float, float, float]
SampleProbe = Callable[[int], LineSample]


@dataclass(frozen=True, slots=True)
class Side:
    """축 한 끝의 비검은 평탄 구간 요약"""

    run: int
    mean: float
    median: float
    spread: float
    step: float
    blend: int


@dataclass(frozen=True, slots=True)
class VideoResult:
    """영상 한 개의 지점별 면 보고와 제품/후보 상자"""

    path: str
    width: int
    height: int
    points: tuple[float, ...]
    reports: tuple[dict[str, Side], ...]
    boxes: tuple[BoxRow | None, ...]
    union: BoxRow
    candidate: BoxRow
    notes: dict[str, str]


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


def _percentile(ordered: Sequence[int], ratio: float) -> float:
    """정렬한 표본의 백분위 값"""
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
    return float(ordered[index])


def row_sample(plane: bytes, offset: int, width: int) -> LineSample:
    """Y 평면 행 하나의 평균, 중앙값, 위아래 5%를 뺀 편차

    - 평균은 제품 `_row_stats`(frame_scan.py 205-212)를 그대로 부름
    - 제품 편차는 평균에서 잰 중앙값이라 워터마크처럼 한 줄 일부만 밝은 덧그림에 평균이 끌림.
      후보 규칙 판정에는 중앙값과 위아래 5%를 뺀 편차를 써서 덧그림을 배제
    """
    mean, _spread = _row_stats(plane, offset, width)
    row = plane[offset : offset + width]
    ordered = sorted(row[::SAMPLE_STRIDE])
    return (mean, _percentile(ordered, 0.5), _percentile(ordered, 1.0 - TRIM_RATIO) - _percentile(ordered, TRIM_RATIO))


def column_sample(plane: bytes, x: int, top: int, bottom: int, line_size: int) -> LineSample:
    """Y 평면 열 하나의 평균, 중앙값, 위아래 5%를 뺀 편차. 평균은 제품 `_column_stats`(frame_scan.py 215-227)"""
    mean, _spread = _column_stats(plane, x, top, bottom, line_size)
    rows = bottom - top
    if rows <= 0:
        return (mean, 0.0, 0.0)
    start = top * line_size + x
    stop = (bottom - 1) * line_size + x + 1
    ordered = sorted(plane[start:stop:line_size][::SAMPLE_STRIDE])
    return (mean, _percentile(ordered, 0.5), _percentile(ordered, 1.0 - TRIM_RATIO) - _percentile(ordered, TRIM_RATIO))


def _line_index(axis_length: int, *, from_end: bool) -> Callable[[int], int]:
    """축 끝 기준 오프셋을 실제 줄 번호로 바꾸는 함수"""

    def index(offset: int) -> int:
        return axis_length - 1 - offset if from_end else offset

    return index


def _is_non_dark_flat(sample: LineSample) -> bool:
    """어둡지 않고 한 줄 안 편차가 작은 줄. 검은 여백과 겹치지 않게 어두운 띠 상한을 넘는 줄만 봄"""
    mean, _median, spread = sample
    return mean > BLACK_EDGE_MEAN_THRESHOLD and spread <= NON_DARK_SPREAD_LIMIT


def edge_step(axis_length: int, probe: SampleProbe, run: int, *, from_end: bool) -> tuple[float, int]:
    """평탄 구간 끝에서 처음 만나는 그림 줄과 여백 밝기의 차, 그 줄까지 건너뛴 줄 수

    - 여백과 그림이 섞인 반화소 혼합 줄은 중앙값이 여백 그대로라 건너뛰고 그림 줄에서 잼
    - `_blend_edge_end`(frame_scan.py 315-343)가 섞인 줄을 여백에 붙이는 근거와 같은 관찰
    """
    index = _line_index(axis_length, from_end=from_end)
    if run <= 0 or run >= axis_length:
        return (0.0, 0)
    level = probe(index(run - 1))[1]
    skipped = 0
    for offset in range(run, min(axis_length, run + NON_DARK_TRANSITION_MAX_LINES + 1)):
        median = probe(index(offset))[1]
        if median - level > NON_DARK_LEVEL_TOLERANCE:
            return (median - level, skipped)
        skipped += 1
    return (0.0, skipped)


def side_report(axis_length: int, probe: SampleProbe, *, from_end: bool) -> Side:
    """축 한 끝의 평탄 구간 길이, 평균, 중앙값, 편차, 경계 계단, 혼합 줄 수"""
    index = _line_index(axis_length, from_end=from_end)
    run = 0
    while run < axis_length and _is_non_dark_flat(probe(index(run))):
        run += 1
    if run <= 0:
        return Side(run=0, mean=0.0, median=0.0, spread=0.0, step=0.0, blend=0)
    samples = [probe(index(offset)) for offset in range(run)]
    step, blend = edge_step(axis_length, probe, run, from_end=from_end)
    return Side(
        run=run,
        mean=sum(sample[0] for sample in samples) / run,
        median=sum(sample[1] for sample in samples) / run,
        spread=max(sample[2] for sample in samples),
        step=step,
        blend=blend,
    )


def four_sides(plane: Plane, box: BoxRow | None) -> dict[str, Side]:
    """지점 한 개의 네 면 보고. 열은 기존 상자의 위아래 안쪽 구간에서 잼

    - 줄 표본을 한 번만 재도록 사전에 담아 같은 줄을 여러 번 정렬하지 않음
    """
    data, line_size, width, height, _rotation = plane
    top_bound = 0 if box is None else box[3]
    bottom_bound = height if box is None else box[3] + box[1]
    row_cache: dict[int, LineSample] = {}
    column_cache: dict[int, LineSample] = {}

    def row_probe(index: int) -> LineSample:
        cached = row_cache.get(index)
        if cached is None:
            cached = row_sample(data, index * line_size, width)
            row_cache[index] = cached
        return cached

    def column_probe(index: int) -> LineSample:
        cached = column_cache.get(index)
        if cached is None:
            cached = column_sample(data, index, top_bound, bottom_bound, line_size)
            column_cache[index] = cached
        return cached

    return {
        "top": side_report(height, row_probe, from_end=False),
        "bottom": side_report(height, row_probe, from_end=True),
        "left": side_report(width, column_probe, from_end=False),
        "right": side_report(width, column_probe, from_end=True),
    }


def agreed_runs(values: Sequence[int], axis_length: int) -> int | None:
    """3/4 이상 지점이 같은 구간 길이를 보이면 그 묶음의 최솟값, 모자라면 None

    - 합집합이 한 지점의 큰 값에 끌려 안쪽으로 잘라내지 않도록 가장 짧은 구간을 씀
    """
    if not values:
        return None
    tolerance = max(EDGE_SNAP_MIN_PX, round(axis_length * EDGE_SNAP_RATIO))
    required = -(-len(values) * MARGIN_AGREE_NUMERATOR // MARGIN_AGREE_DENOMINATOR)
    best: list[int] = []
    for value in values:
        cluster = [other for other in values if abs(other - value) <= tolerance]
        if len(cluster) > len(best):
            best = cluster
    return min(best) if len(best) >= required else None


def candidate_region(
    reports: Sequence[Mapping[str, Side]],
    boxes: Sequence[BoxRow | None],
    width: int,
    height: int,
) -> tuple[list[BoxRow | None], dict[str, str]]:
    """후보 규칙으로 지점별 상자를 다시 정하고 면별 판정 근거를 남김

    - 기존 검은 여백 상자에 비검은 평탄 여백만 더함. 검은 여백 규칙은 그대로 둠
    - (a) 모든 줄 평탄(위아래 10% 뺀 편차), (c) 경계 계단, (d) 3/4 지점 합의를 통과한 면만 자름
    - (b) 합의 지점의 중앙값이 2 이내로 같아야 함. 지점마다 색이 다른 단색 장면을 배제
    - 어느 하나라도 못 넘으면 그 면은 기존 상자 그대로 둠
    - 기존 상자가 없는 지점(검은 화면)은 합집합 근거가 아니므로 그대로 None
    """
    notes: dict[str, str] = {}
    cuts: dict[str, int | None] = {"top": None, "bottom": None, "left": None, "right": None}
    for name in SIDE_NAMES:
        axis_length = width if name in ("left", "right") else height
        values = [report[name].run for report in reports]
        runs = agreed_runs(values, axis_length)
        if runs is None or runs < NON_DARK_MIN_LINES:
            notes[name] = f"합의 없음 runs={values}"
            continue
        agreed = [report[name] for report in reports if report[name].run >= runs]
        steps = [side.step for side in agreed]
        if min(steps) < NON_DARK_STEP_MIN:
            notes[name] = f"계단 부족 steps={[round(value, 1) for value in steps]}"
            continue
        levels = [side.median for side in agreed]
        if max(levels) - min(levels) > NON_DARK_LEVEL_TOLERANCE:
            notes[name] = f"level 불일치 {[round(value, 1) for value in levels]}"
            continue
        edge_values = [min(side.run + side.blend, axis_length) for side in agreed]
        cut = agreed_runs(edge_values, axis_length)
        if cut is None:
            notes[name] = f"경계 합의 없음 edges={edge_values}"
            continue
        cuts[name] = cut
        notes[name] = (
            f"채택 {cut}줄 level {sum(levels) / len(levels):.1f} 계단 {min(steps):+.1f} "
            f"혼합 {[side.blend for side in agreed]}"
        )
    adjusted: list[BoxRow | None] = []
    for report, box in zip(reports, boxes, strict=True):
        if box is None:
            adjusted.append(None)
            continue
        left, top = box[2], box[3]
        right, bottom = box[2] + box[0], box[3] + box[1]
        for name in SIDE_NAMES:
            cut = cuts[name]
            if cut is None or report[name].run < NON_DARK_MIN_LINES:
                continue
            if name == "top":
                top = max(top, cut)
            elif name == "bottom":
                bottom = min(bottom, height - cut)
            elif name == "left":
                left = max(left, cut)
            else:
                right = min(right, width - cut)
        if right <= left or bottom <= top:
            adjusted.append((width, height, 0, 0))
        else:
            adjusted.append((right - left, bottom - top, left, top))
    return (adjusted, notes)


def _column_mean(plane: Plane, top: int, bottom: int) -> Callable[[int], float]:
    """열 번호를 평균 휘도로 바꾸는 함수"""
    data, line_size, _width, _height, _rotation = plane

    def mean_at(column: int) -> float:
        return _column_stats(data, column, top, bottom, line_size)[0]

    return mean_at


def _row_mean(plane: Plane) -> Callable[[int], float]:
    """줄 번호를 평균 휘도로 바꾸는 함수"""
    data, line_size, width, _height, _rotation = plane

    def mean_at(row: int) -> float:
        return _row_stats(data, row * line_size, width)[0]

    return mean_at


def ring_edges(plane: Plane, box: BoxRow | None) -> dict[str, tuple[float, float]]:
    """지점 한 개의 네 면에서 첫 유지 줄과 안쪽 이웃의 평균

    - 첫 유지 줄이 이웃보다 `EDGE_OVERSHOOT_LEVELS` 넘게 밝으면 압축 링잉 과충격 줄로 봄
    - 그림이 섞여 첫 유지 줄이 이웃보다 어두운 쪽은 `_blend_edge_end`(frame_scan.py 315-343)가
      이미 여백에 붙이므로 이 보정은 밝은 쪽만 다룸
    """
    _data, _line_size, width, height, _rotation = plane
    left = 0 if box is None else box[2]
    right = width if box is None else box[2] + box[0]
    top = 0 if box is None else box[3]
    bottom = height if box is None else box[3] + box[1]
    column_mean = _column_mean(plane, top, bottom)
    row_mean = _row_mean(plane)
    pairs = {
        "left": (left, left + 1, width, column_mean),
        "right": (right - 1, right - 2, width, column_mean),
        "top": (top, top + 1, height, row_mean),
        "bottom": (bottom - 1, bottom - 2, height, row_mean),
    }
    checks: dict[str, tuple[float, float]] = {}
    for name, (outer, inner, axis, mean_at) in pairs.items():
        if 0 <= outer < axis and 0 <= inner < axis:
            checks[name] = (mean_at(outer), mean_at(inner))
    return checks


def edge_fixed_boxes(planes: Sequence[Plane], boxes: Sequence[BoxRow | None]) -> tuple[list[BoxRow], list[str]]:
    """첫 유지 줄이 링잉 과충격이나 혼합 줄이면 한 줄 더 안쪽으로 옮기고 짝수로 맞춘 상자

    - 짝수 정렬 때문에 안쪽으로 옮긴 좌표가 다시 한 줄 바깥으로 내려가는 것을 막으려고 올림 정렬
    """
    fixed: list[BoxRow] = []
    notes: list[str] = []
    for plane, box in zip(planes, boxes, strict=True):
        if box is None:
            notes.append("-")
            continue
        left, top = box[2], box[3]
        right, bottom = box[2] + box[0], box[3] + box[1]
        hits: list[str] = []
        moved: set[str] = set()
        for name, (outer_mean, inner_mean) in ring_edges(plane, box).items():
            if outer_mean - inner_mean <= EDGE_OVERSHOOT_LEVELS:
                continue
            hits.append(f"{name} {outer_mean:.0f}>{inner_mean:.0f}")
            moved.add(name)
            if name == "left":
                left = min(left + 1, right - 1)
            elif name == "right":
                right = max(right - 1, left + 1)
            elif name == "top":
                top = min(top + 1, bottom - 1)
            else:
                bottom = max(bottom - 1, top + 1)
        if "left" in moved:
            left = _even_up(left)
        if "top" in moved:
            top = _even_up(top)
        new_width = (right - left) // EDGE_ALIGN * EDGE_ALIGN
        new_height = (bottom - top) // EDGE_ALIGN * EDGE_ALIGN
        if not moved or new_width <= 0 or new_height <= 0:
            fixed.append(box)
            notes.append("; ".join(hits) or "-")
            continue
        fixed.append((new_width, new_height, left, top))
        notes.append("; ".join(hits) or "-")
    return (fixed, notes)


def _even_up(value: int) -> int:
    """짝수로 올림 정렬. 안쪽으로 옮긴 좌표가 짝수 내림으로 다시 바깥으로 내려가는 것을 막음"""
    return value if value % EDGE_ALIGN == 0 else value + EDGE_ALIGN - value % EDGE_ALIGN


def iter_videos(ref_dir: Path) -> list[Path]:
    """`_ref` 바로 아래와 `_ref/clips`의 영상 파일 목록"""
    paths = [path for path in ref_dir.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES]
    clips = ref_dir / "clips"
    if clips.is_dir():
        paths.extend(path for path in clips.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES)
    return sorted(paths)


def scan_points(duration_sec: float) -> list[float]:
    """제품 기본 방식의 9지점 키프레임 시각"""
    params = frame_analysis_params(DEFAULT_FRAME_ANALYSIS_MODE)
    segments = sample_segments(duration_sec, DEFAULT_SEGMENT_COUNT, params.segment_duration_sec)
    return [start for start, _duration in segments]


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


def product_boxes(
    planes: Sequence[Plane],
) -> tuple[list[BoxRow | None], list[EdgeDarkness | None], BoxRow]:
    """제품 `luma_frame_stats`와 `merge_crop_union`으로 지점별 상자, 지점별 어두운 두께, 합집합 상자 계산

    - 지점별 목록은 `planes`와 같은 순서이며 검은 화면, 읽기 실패 지점은 None
    - 회전 영상은 제품과 같이 표시 방향으로 전치한 상자를 합집합에 넣음
    """
    found: list[CropBox] = []
    found_edges: list[EdgeDarkness] = []
    per_point: list[BoxRow | None] = []
    per_point_edges: list[EdgeDarkness | None] = []
    width, height = 0, 0
    for plane in planes:
        data, line_size, plane_width, plane_height, rotation = plane
        width, height = plane_width, plane_height
        box, _ratio, edge = luma_frame_stats(data, line_size, plane_width, plane_height)
        if box is None:
            per_point.append(None)
            per_point_edges.append(None)
            continue
        moved = transpose_crop_box(box, plane_width, plane_height, rotation)
        moved_edge = transpose_edge_darkness(edge, rotation)
        per_point.append((moved.width, moved.height, moved.x, moved.y))
        per_point_edges.append(moved_edge)
        found.append(moved)
        found_edges.append(moved_edge)
    region = merge_crop_union(found, width, height, darkness=found_edges)
    return (per_point, per_point_edges, (region.width, region.height, region.x, region.y))


def analyse(path: Path) -> VideoResult | None:
    """영상 한 개의 지점별 면 보고와 제품/후보 상자. HDR이면 None"""
    width, height, duration, transfer = probe_stream(path)
    if transfer.lower() in HDR_TRANSFERS:
        print(f"{path.name} HDR({transfer}) 건너뜀")
        return None
    points = scan_points(duration)
    planes = read_planes(path, points)
    boxes, edges, union = product_boxes(planes)
    reports = [four_sides(plane, box) for plane, box in zip(planes, boxes, strict=True)]
    candidate_boxes, notes = candidate_region(reports, boxes, width, height)
    region = merge_crop_union(
        [CropBox(width=row[0], height=row[1], x=row[2], y=row[3]) for row in candidate_boxes if row is not None],
        width,
        height,
        darkness=[edge for edge in edges if edge is not None],
    )
    return VideoResult(
        path=str(path),
        width=width,
        height=height,
        points=tuple(points),
        reports=tuple(reports),
        boxes=tuple(boxes),
        union=union,
        candidate=(region.width, region.height, region.x, region.y),
        notes=notes,
    )


def _box_text(row: BoxRow | None) -> str:
    """상자 튜플을 표시 문자열로 만듦"""
    if row is None:
        return "-"
    return f"{row[0]}x{row[1]} at {row[2]},{row[3]}"


def _rel_name(result: VideoResult, ref_dir: Path) -> str:
    """`_ref` 기준 상대 경로. `_ref` 바로 아래와 `clips`의 같은 이름을 구분"""
    try:
        return str(Path(result.path).relative_to(ref_dir))
    except ValueError:
        return Path(result.path).name


def print_margins(result: VideoResult) -> None:
    """`margins`: 지점별 네 면 평탄 구간과 지점 간 level 일치 여부"""
    print(f"\n### {Path(result.path).name} {result.width}x{result.height}")
    for index, (report, box, start) in enumerate(zip(result.reports, result.boxes, result.points, strict=True)):
        print(f"  지점 {index + 1} t={start:.3f} 제품 상자={_box_text(box)}")
        for name in SIDE_NAMES:
            side = report[name]
            print(
                f"    {name:6s} run={side.run:4d} mean={side.mean:7.2f} median={side.median:7.2f} "
                f"spread={side.spread:5.2f} step={side.step:+7.2f} blend={side.blend}"
            )
    for name in SIDE_NAMES:
        picked = [report[name] for report in result.reports if report[name].run >= NON_DARK_MIN_LINES]
        if not picked:
            print(f"  {name:6s} 평탄 구간 없음")
            continue
        levels = [side.median for side in picked]
        lengths = [side.run for side in picked]
        means = [side.mean for side in picked]
        steps = [side.step for side in picked]
        print(
            f"  {name:6s} 지점 {len(picked)}/{len(result.reports)} run {min(lengths)}..{max(lengths)} "
            f"median 폭 {max(levels) - min(levels):.2f} mean 폭 {max(means) - min(means):.2f} "
            f"계단 {min(steps):+.2f}..{max(steps):+.2f}"
        )


def print_sweep(results: Sequence[VideoResult], ref_dir: Path) -> None:
    """`sweep`: 비검은 평탄 구간이 나타난 영상과 지점 간 일정 여부"""
    print("\n| 파일 | 지점 | 비검은 평탄 구간 6줄 이상 | 지점 간 일정 |")
    print("| :--- | :--- | :--- | :--- |")
    for result in results:
        tolerance = max(EDGE_SNAP_MIN_PX, round(result.width * EDGE_SNAP_RATIO))
        hits: list[str] = []
        verdicts: list[str] = []
        for name in SIDE_NAMES:
            runs = [report[name].run for report in result.reports]
            if max(runs) < NON_DARK_MIN_LINES:
                continue
            levels = [report[name].median for report in result.reports]
            steps = [report[name].step for report in result.reports]
            covered = [index + 1 for index, value in enumerate(runs) if value >= NON_DARK_MIN_LINES]
            constant = (
                len(covered) == len(runs)
                and max(levels) - min(levels) <= NON_DARK_LEVEL_TOLERANCE
                and max(runs) - min(runs) <= tolerance
            )
            hits.append(f"{name} {min(runs)}..{max(runs)} @{covered}")
            verdicts.append(
                f"{name} {'일정' if constant else '변동'} "
                f"(level {min(levels):.0f}..{max(levels):.0f} step {min(steps):+.0f}..{max(steps):+.0f})"
            )
        print(
            f"| {_rel_name(result, ref_dir)} | {len(result.reports)} | {'; '.join(hits) or '-'} | "
            f"{'; '.join(verdicts) or '-'} |"
        )


def print_suspects(results: Sequence[VideoResult], ref_dir: Path) -> None:
    """`suspects`: 6줄 이상 비검은 평탄 구간이 나타난 모든 면의 지점별 근거"""
    print("\n| 파일 | 면 | 지점 run | 지점 median | 지점 step | 지점 간 일정 |")
    print("| :--- | :--- | :--- | :--- | :--- | :--- |")
    for result in results:
        required = -(-len(result.reports) * MARGIN_AGREE_NUMERATOR // MARGIN_AGREE_DENOMINATOR)
        for name in SIDE_NAMES:
            runs = [report[name].run for report in result.reports]
            if max(runs) < NON_DARK_MIN_LINES:
                continue
            levels = [report[name].median for report in result.reports]
            steps = [report[name].step for report in result.reports]
            covered = sum(1 for value in runs if value >= NON_DARK_MIN_LINES)
            constant = (
                covered >= required
                and max(levels) - min(levels) <= NON_DARK_LEVEL_TOLERANCE
                and max(runs) - min(runs) <= EDGE_SNAP_MIN_PX
            )
            print(
                f"| {_rel_name(result, ref_dir)} | {name} | {runs} | "
                f"{[round(value) for value in levels]} | {[round(value) for value in steps]} | "
                f"{'일정' if constant else '변동'} ({covered}/{len(runs)}) |"
            )


def print_rule(results: Sequence[VideoResult], ref_dir: Path) -> None:
    """`rule`: 후보 규칙 상자와 제품 합집합 상자 비교"""
    print("\n| 파일 | 원본 | 제품 상자 | 후보 상자 | 변경 |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    changed_count = 0
    for result in results:
        changed = result.candidate != result.union
        changed_count += int(changed)
        print(
            f"| {_rel_name(result, ref_dir)} | {result.width}x{result.height} | {_box_text(result.union)} | "
            f"{_box_text(result.candidate)} | {'예' if changed else '아니오'} |"
        )
        if result.notes:
            print(f"    - 근거: {result.notes}")
    print(f"\n상자가 바뀌는 영상: {changed_count}개")


def _darkness_from_boxes(boxes: Sequence[BoxRow | None], width: int, height: int) -> list[EdgeDarkness | None]:
    """지점별 상자 바깥 두께로 합집합 근거용 어두운 면 두께를 되살림"""
    edges: list[EdgeDarkness | None] = []
    for box in boxes:
        if box is None:
            edges.append(None)
            continue
        edges.append(
            EdgeDarkness(
                top=max(0, box[3]),
                bottom=max(0, height - min(height, box[3] + box[1])),
                left=max(0, box[2]),
                right=max(0, width - min(width, box[2] + box[0])),
            )
        )
    return edges


def print_edges(results: Sequence[VideoResult], ref_dir: Path) -> None:
    """`edges`: 첫 유지 줄 링잉과 한 줄 안쪽 보정 전후 상자 비교

    - 짝수 정렬이 안쪽 보정을 다시 바깥으로 되돌리는지 함께 확인
    """
    print("\n| 파일 | 제품 상자 | 안쪽 보정 상자 | 변경 | 보정 근거 |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    changed_count = 0
    for result in results:
        planes = read_planes(Path(result.path), result.points)
        fixed, notes = edge_fixed_boxes(planes, result.boxes)
        if all(note == "-" for note in notes):
            print(f"| {_rel_name(result, ref_dir)} | {_box_text(result.union)} | 같음 | 아니오 | - |")
            continue
        region = merge_crop_union(
            [CropBox(width=row[0], height=row[1], x=row[2], y=row[3]) for row in fixed],
            result.width,
            result.height,
            darkness=_darkness_from_boxes(result.boxes, result.width, result.height),
        )
        after = (region.width, region.height, region.x, region.y)
        changed = after != result.union
        changed_count += int(changed)
        print(
            f"| {_rel_name(result, ref_dir)} | {_box_text(result.union)} | {_box_text(after)} | "
            f"{'예' if changed else '아니오'} | {' ; '.join(note for note in notes if note != '-')} |"
        )
    print(f"\n보정이 걸린 지점이 있고 상자가 바뀌는 영상: {changed_count}개")


def pick_columns(spec: str, width: int) -> list[int]:
    """`104-110,370-376` 같은 열 범위 목록을 실제 열 번호로 폄"""
    columns: list[int] = []
    for part in spec.split(","):
        token = part.strip()
        if not token:
            continue
        start_text, _, end_text = token.partition("-")
        if end_text:
            columns.extend(range(int(start_text), int(end_text) + 1))
        else:
            columns.append(int(start_text))
    return [column for column in columns if 0 <= column < width]


def print_columns(results: Sequence[VideoResult], spec: str) -> None:
    """`columns`: 지정한 열의 지점별 평균과 이웃 계단

    - 경계 잔여 열이 압축 혼합인지 짝수 반올림인지 판단하는 근거
    """
    for result in results:
        columns = pick_columns(spec, result.width)
        if not columns:
            continue
        print(f"\n### {Path(result.path).name} {result.width}x{result.height} 열 {columns[0]}..{columns[-1]}")
        planes = read_planes(Path(result.path), result.points)
        for index, (plane, start) in enumerate(zip(planes, result.points, strict=True)):
            mean_at = _column_mean(plane, 0, result.height)
            print(f"  지점 {index + 1} t={start:.3f}")
            previous: float | None = None
            for column in columns:
                mean = mean_at(column)
                step = "" if previous is None else f" step={mean - previous:+7.2f}"
                print(f"    x={column:4d} mean={mean:7.2f}{step}")
                previous = mean


def serialise(result: VideoResult) -> dict[str, object]:
    """결과를 JSON으로 쓸 수 있는 사전으로 변환"""
    return {
        "path": result.path,
        "width": result.width,
        "height": result.height,
        "points": list(result.points),
        "reports": [
            {
                name: {
                    "run": side.run,
                    "mean": side.mean,
                    "median": side.median,
                    "spread": side.spread,
                    "step": side.step,
                    "blend": side.blend,
                }
                for name, side in report.items()
            }
            for report in result.reports
        ],
        "boxes": [None if box is None else list(box) for box in result.boxes],
        "union": list(result.union),
        "candidate": list(result.candidate),
        "notes": result.notes,
    }


def _row_ints(raw: object, length: int) -> BoxRow:
    """JSON 목록을 정해진 길이의 정수 네 쌍으로 읽음"""
    if not isinstance(raw, list) or len(raw) != length:
        raise ValueError("상자 목록 형식이 다릅니다")
    values = [int(str(value)) for value in cast("list[object]", raw)]
    return (values[0], values[1], values[2], values[3])


def _side_from(raw: object) -> Side:
    """JSON 면 사전을 `Side`로 복원"""
    if not isinstance(raw, Mapping):
        raise ValueError("면 형식이 다릅니다")
    side = cast("Mapping[str, object]", raw)
    return Side(
        run=int(str(side["run"])),
        mean=float(str(side["mean"])),
        median=float(str(side["median"])),
        spread=float(str(side["spread"])),
        step=float(str(side["step"])),
        blend=int(str(side["blend"])),
    )


def deserialise(raw: Mapping[str, object]) -> VideoResult:
    """JSON 사전을 `VideoResult`로 복원"""
    raw_reports = raw["reports"]
    raw_boxes = raw["boxes"]
    raw_points = raw["points"]
    raw_notes = raw["notes"]
    if not isinstance(raw_reports, list) or not isinstance(raw_boxes, list):
        raise ValueError("목록 형식이 다릅니다")
    if not isinstance(raw_points, list) or not isinstance(raw_notes, Mapping):
        raise ValueError("목록 형식이 다릅니다")
    reports: list[dict[str, Side]] = []
    for item in cast("list[object]", raw_reports):
        if not isinstance(item, Mapping):
            raise ValueError("면 보고 형식이 다릅니다")
        entry = cast("Mapping[str, object]", item)
        reports.append({name: _side_from(entry[name]) for name in SIDE_NAMES})
    boxes = [None if item is None else _row_ints(item, 4) for item in cast("list[object]", raw_boxes)]
    notes = {key: str(value) for key, value in cast("Mapping[str, object]", raw_notes).items()}
    return VideoResult(
        path=str(raw["path"]),
        width=int(str(raw["width"])),
        height=int(str(raw["height"])),
        points=tuple(float(str(value)) for value in cast("list[object]", raw_points)),
        reports=tuple(reports),
        boxes=tuple(boxes),
        union=_row_ints(raw["union"], 4),
        candidate=_row_ints(raw["candidate"], 4),
        notes=notes,
    )


def load_cache(path: Path) -> dict[str, VideoResult]:
    """이전 실행에서 저장한 영상별 결과를 읽음. 없거나 깨졌으면 빈 사전"""
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(loaded, Mapping):
        return {}
    cache: dict[str, VideoResult] = {}
    for key, value in cast("Mapping[str, object]", loaded).items():
        if isinstance(value, Mapping):
            try:
                cache[key] = deserialise(cast("Mapping[str, object]", value))
            except (KeyError, ValueError):
                continue
    return cache


def store_result(path: Path, cache: dict[str, VideoResult], key: str, result: VideoResult) -> None:
    """영상 한 개의 결과를 캐시에 넣고 바로 씀. 실행이 끊겨도 남은 결과를 다시 재지 않음"""
    cache[key] = result
    payload = {name: serialise(item) for name, item in cache.items()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    """인자에 따라 조사 모드를 실행"""
    ref_dir = Path(args.ref_dir)
    videos = iter_videos(ref_dir)
    if args.only:
        videos = [path for path in videos if path.name.startswith(args.only)]
    if args.limit > 0:
        videos = videos[: args.limit]
    cache_path = Path(args.json_out)
    cache: dict[str, VideoResult] = {} if args.force else load_cache(cache_path)
    results: list[VideoResult] = []
    for path in videos:
        key = str(path)
        if key in cache:
            results.append(cache[key])
            continue
        try:
            result = analyse(path)
        except (subprocess.CalledProcessError, KeyError, StopIteration) as error:
            print(f"{path.name} 읽기 실패: {error}", flush=True)
            continue
        if result is None:
            continue
        store_result(cache_path, cache, key, result)
        results.append(result)
        print(f"{path.name} 완료 {len(cache)}/{len(videos)}", flush=True)
    if args.mode == "margins":
        for result in results:
            print_margins(result)
    elif args.mode == "sweep":
        print_sweep(results, ref_dir)
    elif args.mode == "suspects":
        print_suspects(results, ref_dir)
    elif args.mode == "columns":
        print_columns(results, args.columns)
    elif args.mode == "edges":
        print_edges(results, ref_dir)
    else:
        print_rule(results, ref_dir)
    return 0


def main() -> int:
    """조사 모드와 대상 폴더를 받아 실행"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ref-dir",
        default=str(Path(__file__).resolve().parents[1] / "_ref"),
        help="조사할 영상 폴더. 기본은 저장소의 _ref",
    )
    parser.add_argument(
        "--mode",
        choices=("margins", "sweep", "rule", "columns", "edges", "suspects"),
        default="rule",
        help="조사 모드",
    )
    parser.add_argument("--only", default="", help="이름 접두사로 맞는 파일만 조사")
    parser.add_argument("--limit", type=int, default=0, help="조사할 파일 수 상한(0은 전체)")
    parser.add_argument("--columns", default="104-110,370-376", help="columns 모드에서 볼 열 범위")
    parser.add_argument(
        "--json-out",
        default=str(Path(tempfile.gettempdir(), "non_black_margins_results.json")),
        help="영상별 측정 결과를 남길 JSON 경로. 다음 실행이 이어서 씀",
    )
    parser.add_argument("--force", action="store_true", help="저장한 결과를 버리고 다시 잼")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
