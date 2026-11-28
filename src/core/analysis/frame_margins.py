"""비검은 평탄 여백과 밝은 경계 과충격 보정 규칙 모듈

- 검은 띠 규칙(`frame_scan.luma_frame_stats`)이 찾은 상자에 회색, 흰색, 단색 여백을 더함
- 줄 표본의 위아래 10%를 뺀 편차로 평탄함을 재, 방송사 워터마크처럼 줄 일부만 밝은 덧그림에 흔들리지 않음
- 표본은 이미 검은 띠 규칙이 자른 상자 안쪽에서 재, 평탄 구간은 그 상자 끝에서 시작하고 길이는 프레임 끝 기준으로 환산
- 면마다 지점 요약만 내고, 자를 길이는 지점 상자를 합치는 쪽이 9지점 합의로 정함
- 값의 근거는 12.23절 실측이며 조사 스크립트 `scripts/investigate_non_black_margins.py`와 같은 규칙
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.core.analysis.frame_analysis import (
    EDGE_SNAP_MIN_PX,
    EDGE_SNAP_RATIO,
    MARGIN_AGREE_DENOMINATOR,
    MARGIN_AGREE_NUMERATOR,
    CropBox,
)

# - 비검은 평탄 여백 판정: 최소 줄 수, 줄 안 편차 상한, 지점 간 같은 밝기 허용 폭, 그림과의 경계 계단
NON_DARK_MIN_LINES: int = 6
NON_DARK_SPREAD_LIMIT: float = 3.0
NON_DARK_LEVEL_TOLERANCE: float = 2.0
NON_DARK_STEP_MIN: float = 6.0
# - 평탄 여백으로 인정하는 최소 합의 지점 수. 상자를 낸 지점이 적을 때 한두 지점의 결과로 자르지 않음
NON_DARK_AGREE_MIN_POINTS: int = 3
# - 평탄 구간 끝에 붙는 반화소 혼합 줄 최대 수. 혼합 줄은 중앙값이 여백 그대로이고 그림 쪽으로 오름
NON_DARK_TRANSITION_MAX_LINES: int = 2
# - 줄 안 편차를 잴 때 위아래에서 빼는 비율. 한 줄 일부만 밝은 덧그림은 이 비율보다 적게 차지해 편차에 들어오지 않음
NON_DARK_TRIM_RATIO: float = 0.10
NON_DARK_SAMPLE_STRIDE: int = 2
# - 첫 유지 줄이 안쪽 이웃보다 이만큼 밝으면 압축 링잉 과충격 줄로 보고 한 줄 안쪽으로 옮김
EDGE_OVERSHOOT_LEVELS: float = 2.0
# - 인코더 crop 필터가 요구하는 짝수 정렬 단위
EDGE_ALIGN: int = 2
FULL_TURN_DEGREES: int = 360
HALF_TURN_DEGREES: int = 180
QUARTER_TURN_DEGREES: int = 90
SIDE_NAMES: tuple[str, str, str, str] = ("top", "bottom", "left", "right")

# - (평균, 중앙값, 위아래 10%를 뺀 편차)
LineSample = tuple[float, float, float]
SampleProbe = Callable[[int], LineSample]


@dataclass(frozen=True, slots=True)
class FlatSide:
    """축 한 끝의 비검은 평탄 구간 요약"""

    run: int
    mean: float
    median: float
    spread: float
    step: float
    blend: int


@dataclass(frozen=True, slots=True)
class FlatSides:
    """프레임 네 면의 비검은 평탄 구간 요약"""

    top: FlatSide
    bottom: FlatSide
    left: FlatSide
    right: FlatSide


_EMPTY_SIDE = FlatSide(run=0, mean=0.0, median=0.0, spread=0.0, step=0.0, blend=0)
EMPTY_FLAT_SIDES: FlatSides = FlatSides(
    top=_EMPTY_SIDE,
    bottom=_EMPTY_SIDE,
    left=_EMPTY_SIDE,
    right=_EMPTY_SIDE,
)


def _percentile(ordered: Sequence[int], ratio: float) -> float:
    """정렬한 표본의 백분위 값"""
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
    return float(ordered[index])


def _trimmed_spread(ordered: Sequence[int]) -> float:
    """정렬한 표본에서 위아래 `NON_DARK_TRIM_RATIO`를 뺀 편차"""
    return _percentile(ordered, 1.0 - NON_DARK_TRIM_RATIO) - _percentile(ordered, NON_DARK_TRIM_RATIO)


def _row_sample(plane: bytes, offset: int, left: int, right: int) -> LineSample:
    """Y 평면 행 하나의 평균, 중앙값, 위아래 10%를 뺀 편차. 가로 구간은 상자 안쪽만 봄

    - 검은 기둥 안쪽의 레터박스를 재도록 행 전체가 아니라 상자 가로 구간만 표본으로 씀
    - 중앙값과 편차는 일부만 밝은 덧그림을 배제하도록 표본을 건너뛰며 정렬해 잼
    """
    width = right - left
    if width <= 0:
        return (0.0, 0.0, 0.0)
    row = plane[offset + left : offset + right]
    ordered = sorted(row[::NON_DARK_SAMPLE_STRIDE])
    return (sum(row) / width, _percentile(ordered, 0.5), _trimmed_spread(ordered))


def _column_sample(plane: bytes, x: int, top: int, bottom: int, line_size: int) -> LineSample:
    """Y 평면 열 하나의 평균, 중앙값, 위아래 10%를 뺀 편차. 세로 구간은 상자 안쪽만 봄"""
    rows = bottom - top
    if rows <= 0 or x < 0:
        return (0.0, 0.0, 0.0)
    start = top * line_size + x
    stop = (bottom - 1) * line_size + x + 1
    column = plane[start:stop:line_size]
    if not column:
        return (0.0, 0.0, 0.0)
    ordered = sorted(column[::NON_DARK_SAMPLE_STRIDE])
    return (sum(column) / rows, _percentile(ordered, 0.5), _trimmed_spread(ordered))


def _line_index(axis_length: int, *, from_end: bool) -> Callable[[int], int]:
    """축 끝 기준 오프셋을 실제 줄 번호로 바꾸는 함수"""

    def index(offset: int) -> int:
        return axis_length - 1 - offset if from_end else offset

    return index


def _is_non_dark_flat(sample: LineSample, dark_limit: float) -> bool:
    """어둡지 않고 한 줄 안 편차가 작은 줄. 검은 여백과 겹치지 않게 어두운 띠 상한을 넘는 줄만 봄"""
    mean, _median, spread = sample
    return mean > dark_limit and spread <= NON_DARK_SPREAD_LIMIT


def _edge_step(axis_length: int, probe: SampleProbe, run: int, *, from_end: bool) -> tuple[float, int]:
    """평탄 구간 끝에서 처음 만나는 그림 줄과 여백 밝기의 차, 그 줄까지 건너뛴 줄 수

    - 여백과 그림이 섞인 반화소 혼합 줄은 중앙값이 여백 그대로라 건너뛰고 그림 줄에서 잼
    - 계단은 밝기 차의 절댓값이라 여백보다 어두운 그림도, 밝은 여백 앞의 어두운 그림도 근거가 됨
    - 검은 띠 규칙이 섞인 줄을 띠에 붙이는 근거와 같은 관찰
    """
    index = _line_index(axis_length, from_end=from_end)
    if run <= 0 or run >= axis_length:
        return (0.0, 0)
    level = probe(index(run - 1))[1]
    skipped = 0
    for offset in range(run, min(axis_length, run + NON_DARK_TRANSITION_MAX_LINES + 1)):
        step = abs(probe(index(offset))[1] - level)
        if step > NON_DARK_LEVEL_TOLERANCE:
            return (step, skipped)
        skipped += 1
    return (0.0, skipped)


def _side_report(
    axis_length: int,
    probe: SampleProbe,
    *,
    start: int,
    stop: int,
    from_end: bool,
    dark_limit: float,
) -> FlatSide:
    """축 한 끝의 평탄 구간 길이, 평균, 중앙값, 편차, 경계 계단, 혼합 줄 수

    - 평탄 구간은 검은 띠 규칙이 자른 상자 끝(`start`)에서 시작하고, 길이는 프레임 끝 기준으로 환산
    - 상자의 반대 끝(`stop`)까지 평탄하면 평탄한 그림이 상자를 다 채운 것이라 계단을 0으로 두어 자르지 않음
    """
    index = _line_index(axis_length, from_end=from_end)
    run = start
    while run < stop and _is_non_dark_flat(probe(index(run)), dark_limit):
        run += 1
    if run - start < NON_DARK_MIN_LINES:
        return _EMPTY_SIDE
    samples = [probe(index(offset)) for offset in range(start, run)]
    step, blend = (0.0, 0) if run >= stop else _edge_step(axis_length, probe, run, from_end=from_end)
    return FlatSide(
        run=run,
        mean=sum(sample[0] for sample in samples) / len(samples),
        median=sum(sample[1] for sample in samples) / len(samples),
        spread=max(sample[2] for sample in samples),
        step=step,
        blend=blend,
    )


def measure_flat_sides(
    data: bytes,
    line_size: int,
    width: int,
    height: int,
    box: CropBox | None,
    *,
    dark_limit: float,
) -> FlatSides:
    """Y 평면 한 장의 네 면 평탄 구간 요약. 표본은 기존 상자 안쪽 구간에서 잼

    - 줄 표본은 상자 가로 구간, 열 표본은 상자 세로 구간만 봐 검은 띠 안쪽의 평탄 여백도 잼
    - 평탄 구간은 검은 띠 규칙이 이미 자른 면의 상자 끝에서 시작해 그 바깥 여백을 잃지 않음
    - 줄 표본을 한 번만 재도록 사전에 담아 같은 줄을 여러 번 정렬하지 않음
    - 상자가 없으면(검은 화면) 빈 요약을 내 합집합 근거로 쓰지 않게 함
    """
    if box is None or width <= 0 or height <= 0 or line_size < width or len(data) < line_size * height:
        return EMPTY_FLAT_SIDES
    left_bound = box.x
    right_bound = box.x + box.width
    top_bound = box.y
    bottom_bound = box.y + box.height
    row_cache: dict[int, LineSample] = {}
    column_cache: dict[int, LineSample] = {}

    def row_probe(index: int) -> LineSample:
        cached = row_cache.get(index)
        if cached is None:
            cached = _row_sample(data, index * line_size, left_bound, right_bound)
            row_cache[index] = cached
        return cached

    def column_probe(index: int) -> LineSample:
        cached = column_cache.get(index)
        if cached is None:
            cached = _column_sample(data, index, top_bound, bottom_bound, line_size)
            column_cache[index] = cached
        return cached

    return FlatSides(
        top=_side_report(
            height,
            row_probe,
            start=top_bound,
            stop=bottom_bound,
            from_end=False,
            dark_limit=dark_limit,
        ),
        bottom=_side_report(
            height,
            row_probe,
            start=height - bottom_bound,
            stop=height - top_bound,
            from_end=True,
            dark_limit=dark_limit,
        ),
        left=_side_report(
            width,
            column_probe,
            start=left_bound,
            stop=right_bound,
            from_end=False,
            dark_limit=dark_limit,
        ),
        right=_side_report(
            width,
            column_probe,
            start=width - right_bound,
            stop=width - left_bound,
            from_end=True,
            dark_limit=dark_limit,
        ),
    )



def _even_up(value: int) -> int:
    """짝수로 올림 정렬. 안쪽으로 옮긴 좌표가 짝수 내림으로 다시 바깥으로 내려가는 것을 막음"""
    return value if value % EDGE_ALIGN == 0 else value + EDGE_ALIGN - value % EDGE_ALIGN


def _row_mean(data: bytes, y: int, width: int, line_size: int) -> float:
    """Y 평면 행 하나의 평균 휘도"""
    if width <= 0 or y < 0:
        return 0.0
    offset = y * line_size
    row = data[offset : offset + width]
    return sum(row) / width if row else 0.0


def _column_mean(data: bytes, x: int, top: int, bottom: int, line_size: int) -> float:
    """Y 평면 열 하나의 평균 휘도. 세로 구간은 상자 안쪽만 봄"""
    rows = bottom - top
    if rows <= 0 or x < 0:
        return 0.0
    start = top * line_size + x
    stop = (bottom - 1) * line_size + x + 1
    column = data[start:stop:line_size]
    return sum(column) / rows


def fix_bright_overshoot(data: bytes, line_size: int, width: int, height: int, box: CropBox) -> CropBox:
    """첫 유지 줄이 압축 링잉 과충격으로 밝으면 그 면을 한 줄 안쪽으로 옮긴 상자

    - 첫 유지 줄이 안쪽 이웃보다 `EDGE_OVERSHOOT_LEVELS` 넘게 밝으면 압축 링잉 과충격 줄로 봄
    - 그림이 섞여 첫 유지 줄이 이웃보다 어두운 쪽은 검은 띠 규칙이 이미 여백에 붙이므로 밝은 쪽만 다룸
    - 좌, 상은 옮긴 좌표를 짝수로 올림해 짝수 내림이 다시 바깥으로 되돌리지 않게 하고 크기는 짝수로 내림
    """
    if box.width <= 0 or box.height <= 0 or width <= 0 or height <= 0 or line_size < width:
        return box
    if len(data) < line_size * height:
        return box
    left, top = box.x, box.y
    right, bottom = box.x + box.width, box.y + box.height
    # - 첫 유지 줄은 여백을 자른 면에만 생김. 상자가 프레임 끝에 닿은 면은 그림 첫 줄이라 밝기 비교를 하지 않음
    checks: tuple[tuple[str, float, float], ...] = tuple(
        (name, outer_mean, inner_mean)
        for name, outer_mean, inner_mean, at_frame_edge in (
            (
                "left",
                _column_mean(data, left, top, bottom, line_size),
                _column_mean(data, left + 1, top, bottom, line_size),
                left <= 0,
            ),
            (
                "right",
                _column_mean(data, right - 1, top, bottom, line_size),
                _column_mean(data, right - 2, top, bottom, line_size),
                right >= width,
            ),
            (
                "top",
                _row_mean(data, top, width, line_size),
                _row_mean(data, top + 1, width, line_size),
                top <= 0,
            ),
            (
                "bottom",
                _row_mean(data, bottom - 1, width, line_size),
                _row_mean(data, bottom - 2, width, line_size),
                bottom >= height,
            ),
        )
        if not at_frame_edge
    )

    moved: set[str] = set()
    for name, outer_mean, inner_mean in checks:
        if outer_mean - inner_mean <= EDGE_OVERSHOOT_LEVELS:
            continue
        moved.add(name)
        if name == "left":
            left = min(left + 1, right - 1)
        elif name == "right":
            right = max(right - 1, left + 1)
        elif name == "top":
            top = min(top + 1, bottom - 1)
        else:
            bottom = max(bottom - 1, top + 1)
    if not moved:
        return box
    if "left" in moved:
        left = _even_up(left)
    if "top" in moved:
        top = _even_up(top)
    new_width = (right - left) // EDGE_ALIGN * EDGE_ALIGN
    new_height = (bottom - top) // EDGE_ALIGN * EDGE_ALIGN
    if new_width <= 0 or new_height <= 0:
        return box
    return CropBox(width=new_width, height=new_height, x=left, y=top)


def transpose_flat_sides(sides: FlatSides, rotation: int) -> FlatSides:
    """네 면 평탄 여백 요약을 표시 회전이 적용된 방향으로 전치

    - 면 대응은 `frame_scan.transpose_edge_darkness`와 같게 씀
    """
    turn = rotation % FULL_TURN_DEGREES
    if turn == HALF_TURN_DEGREES:
        return FlatSides(top=sides.bottom, bottom=sides.top, left=sides.right, right=sides.left)
    if turn % HALF_TURN_DEGREES == 0:
        return sides
    if turn == QUARTER_TURN_DEGREES:
        return FlatSides(top=sides.left, bottom=sides.right, left=sides.bottom, right=sides.top)
    return FlatSides(top=sides.right, bottom=sides.left, left=sides.top, right=sides.bottom)


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


def _flat_cut(reports: Sequence[FlatSide], axis_length: int) -> int | None:
    """한 면에서 자를 길이. 경계 계단과 같은 밝기를 넘은 지점 묶음이 3/4 이상일 때만 채택

    - 계단이 없는 지점(평탄 구간이 평탄한 그림으로 이어짐)은 합의 판정에서 빼, 한 지점이 여럿의 합의를 막지 않게 함
    - 합의 지점 수는 상자를 낸 지점의 3/4 이상이면서 `NON_DARK_AGREE_MIN_POINTS` 이상이어야 함
    """
    tolerance = max(EDGE_SNAP_MIN_PX, round(axis_length * EDGE_SNAP_RATIO))
    required = max(
        NON_DARK_AGREE_MIN_POINTS,
        -(-len(reports) * MARGIN_AGREE_NUMERATOR // MARGIN_AGREE_DENOMINATOR),
    )
    usable = [
        report
        for report in reports
        if report.run >= NON_DARK_MIN_LINES and report.step >= NON_DARK_STEP_MIN
    ]
    best: list[FlatSide] = []
    for candidate in usable:
        cluster = [
            report
            for report in usable
            if abs(report.run - candidate.run) <= tolerance
            and abs(report.median - candidate.median) <= NON_DARK_LEVEL_TOLERANCE
        ]
        if len(cluster) > len(best):
            best = cluster
    if len(best) < required:
        return None
    edges = [min(report.run + report.blend, axis_length) for report in best]
    return agreed_runs(edges, axis_length)


def margin_cuts(sides: Sequence[FlatSides], frame_width: int, frame_height: int) -> dict[str, int]:
    """지점별 네 면 요약에서 면마다 자를 길이를 정함. 채택하지 않은 면은 목록에 없음"""
    picks: tuple[tuple[str, list[FlatSide], int], ...] = (
        ("top", [point.top for point in sides], frame_height),
        ("bottom", [point.bottom for point in sides], frame_height),
        ("left", [point.left for point in sides], frame_width),
        ("right", [point.right for point in sides], frame_width),
    )
    cuts: dict[str, int] = {}
    for name, reports, axis_length in picks:
        cut = _flat_cut(reports, axis_length)
        if cut is not None:
            cuts[name] = cut
    return cuts


def side_of(sides: FlatSides, name: str) -> FlatSide:
    """면 이름에 해당하는 평탄 구간 요약"""
    if name == "top":
        return sides.top
    if name == "bottom":
        return sides.bottom
    if name == "left":
        return sides.left
    return sides.right


def apply_flat_margin_cuts(
    boxes: Sequence[CropBox],
    sides: Sequence[FlatSides],
    frame_width: int,
    frame_height: int,
    cuts: dict[str, int] | None = None,
) -> list[CropBox]:
    """기존 검은 여백 상자에 9지점이 합의한 비검은 평탄 여백을 더한 지점 상자 목록

    - (a) 모든 줄 평탄, (c) 경계 계단, (b) 같은 밝기, (d) 3/4 지점 합의를 통과한 면만 자름
    - 어느 하나라도 못 넘으면 그 면은 기존 상자 그대로 두고, 검은 여백 규칙은 바꾸지 않음
    - 합의한 면은 지점 상자를 합치는 쪽이 다시 넓히지 않도록 모든 상자에 적용(3/4 규칙이 판정을 지배)
    - 자기 구간이 합의한 길이보다 짧은 지점은 자기 구간까지만 잘라 그림과 구간별 차이를 보존함
    - 자기 평탄 구간이 최소 줄 수에 못 미치는 지점은 자기 값이 못 믿을 만하므로 합의한 길이를 그대로 씀
    """
    if not boxes or len(sides) != len(boxes) or frame_width <= 0 or frame_height <= 0:
        return list(boxes)
    picked = margin_cuts(sides, frame_width, frame_height) if cuts is None else cuts
    if not picked:
        return list(boxes)
    adjusted: list[CropBox] = []
    for box, point in zip(boxes, sides, strict=True):
        left, top = box.x, box.y
        right, bottom = box.x + box.width, box.y + box.height
        for name in SIDE_NAMES:
            cut = picked.get(name)
            if cut is None:
                continue
            report = side_of(point, name)
            if report.run >= NON_DARK_MIN_LINES:
                cut = min(cut, report.run + report.blend)
            if name == "top":
                top = max(top, cut)
            elif name == "bottom":
                bottom = min(bottom, frame_height - cut)
            elif name == "left":
                left = max(left, cut)
            else:
                right = min(right, frame_width - cut)
        if right <= left or bottom <= top:
            adjusted.append(CropBox(width=frame_width, height=frame_height, x=0, y=0))
        else:
            adjusted.append(CropBox(width=right - left, height=bottom - top, x=left, y=top))
    return adjusted
