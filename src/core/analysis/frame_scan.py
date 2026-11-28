"""화질 분석 1단계 9지점 키프레임 휘도(Y) 스캔 모듈

- `PyAV` 컨테이너를 한 번만 열어 키프레임의 Y 평면을 직접 읽어 외부 프로세스 없이 검은 화면과 검은 여백을 찾음
- `numpy` 없이 `bytes.translate` 룩업 테이블과 `bytes.count`, 슬라이스 합만으로 픽셀 비율과 줄, 열 평균을 계산
- PyAV 열기나 디코딩이 실패하거나 테스트가 실행기를 주입하면 FFmpeg CLI 9지점 키프레임 스캔으로 폴백
- CLI 폴백도 지점마다 8-bit 회색조 한 장을 받아 같은 `luma_frame_stats` 규칙으로 판정해 두 경로의 여백 정의를 하나로 유지
- HDR 영상은 두 경로 모두 `hdr_tone_map` 체인으로 SDR BT.709을 만든 뒤 회색조로 줄여 PQ 휘도를 그대로 재지 않음
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, cast

from src.core.analysis.frame_analysis import CropBox, EdgeDarkness
from src.core.analysis.frame_margins import (
    EMPTY_FLAT_SIDES,
    FlatSides,
    fix_bright_overshoot,
    measure_flat_sides,
    transpose_flat_sides,
)
from src.core.analysis.hdr_tone_map import (
    TONEMAP_UNAVAILABLE_REASON,
    ToneMapUnavailableError,
    is_tonemap_filter_error,
    missing_tonemap_filters,
    tonemap_filter_chain,
    tonemap_filter_nodes,
)
from src.core.command_history import STAGE_FRAME_ANALYSIS, command_scope
from src.core.file_sharing import open_shared_binary

if TYPE_CHECKING:
    from av.container import InputContainer
    from av.video.frame import VideoFrame
    from av.video.stream import VideoStream

logger = logging.getLogger(__name__)

ScanCommandRunner = Callable[[list[str], str], tuple[int, str]]
PointCallback = Callable[[int, int], None]
LineStats = tuple[float, float]
LineProbe = Callable[[int], LineStats]


@dataclass(frozen=True, slots=True)
class LumaPointStats:
    """Y 평면 한 장의 검은 화면 비율, 여백 상자, 면별 어두운 두께, 네 면 평탄 여백 요약

    - 상자는 밝은 과충격 줄 보정까지 마친 값이라 지점 상자를 그대로 합집합에 넣을 수 있음
    - 평탄 여백 요약은 지점 하나로는 자를 수 없어 합집합 단계에서 9지점 합의로 씀
    """

    box: CropBox | None
    black_ratio: float
    edges: EdgeDarkness
    flat_sides: FlatSides


# - Y 평면 한 장의 판정 함수. 서비스가 프로세스 풀로 넘겨 GUI 프로세스의 GIL 점유를 없앰
FrameStatsRunner = Callable[[bytes, int, int, int], LumaPointStats]


# - 검은 프레임 판정: Y < 26 픽셀 비율 98% 이상(12.19절), 여백 줄, 열 평균 24 이하
BLACK_LUMA_THRESHOLD: int = 26
BLACK_FRAME_PIXEL_RATIO: float = 0.98
BLACK_EDGE_MEAN_THRESHOLD: float = 24.0
# - 실제 검은 띠의 줄은 평탄하다. 어두운 장면의 완만한 비네트는 줄 안 편차가 커서 띠로 보지 않음
BLACK_EDGE_SPREAD_THRESHOLD: float = 8.0
# - 띠와 그림 경계의 계단: 첫 그림 줄 평균이 마지막 띠 줄 평균보다 이만큼 커야 함. 완만한 그라데이션 배제
BLACK_EDGE_STEP_MIN: float = 6.0
# - 경계 계단이 약한 검은 띠를 인정하는 정확히 평탄한 어두운 구간(축 길이 비율, 최소 줄 수, 평균, 편차 상한)
FLAT_BAR_MIN_RATIO: float = 0.06
FLAT_BAR_MIN_LINES: int = 6
FLAT_BAR_MEAN_LIMIT: float = 20.0
FLAT_BAR_SPREAD_LIMIT: float = 1.5
# - 평탄 띠 끝의 부드러운 경사(디더, 압축 잔여)를 띠에 붙이는 한계와 그 끝에서 요구하는 최소 계단
FLAT_RAMP_SPREAD_LIMIT: float = 4.0
FLAT_RAMP_MEAN_DELTA: float = 3.0
FLAT_RAMP_STEP_MIN: float = 3.0
# - 띠 경계 뒤에 남은 얇은 잔여 여백을 붙이는 최대 줄 수와 그 뒤 그림과의 최소 계단
RESIDUAL_MARGIN_MAX_LINES: int = 3
RESIDUAL_MARGIN_MEAN_LIMIT: float = 32.0
RESIDUAL_MARGIN_STEP_MIN: float = 2.0
# - 띠와 그림 사이 반화소 재표본 혼합 줄에서 그림이 차지하는 휘도 몫의 허용 범위(12.21절 실측)
RESIDUAL_BLEND_PICTURE_SHARE_MIN: float = 0.5
RESIDUAL_BLEND_PICTURE_SHARE_MAX: float = 0.7
# - 띠 안에 박힌 자막처럼 얇은 비-띠 구간을 건너뛰는 최대 두께(축 길이 비율, 최소 줄 수)
TEXT_ISLAND_RATIO: float = 0.05
TEXT_ISLAND_MIN_LINES: int = 2
# - 자막과 그림 사이 띠 간격 최소 줄 수. 이보다 얇으면 그림에 붙은 자막으로 보고 건너뛰지 않음
TEXT_ISLAND_MIN_GAP_LINES: int = 2
# - 자막 뒤 그림 블록이 자막 두께의 몇 배 이상이어야 그림으로 인정하는지
TEXT_ISLAND_BLOCK_FACTOR: int = 3
# - 줄 편차 중앙값에 쓰는 표본 상한. 줄이 길어도 계산량을 일정하게 유지
SPREAD_SAMPLE_LIMIT: int = 64
FULL_TURN_DEGREES: int = 360
HALF_TURN_DEGREES: int = 180
QUARTER_TURN_DEGREES: int = 90
FALLBACK_TIME_BASE_SEC: float = 1.0 / 1000.0
# - CLI 폴백이 받는 회색조 한 장의 매직과 최대값. FFmpeg `pgm` 인코더는 8-bit `P5`만 냄
PGM_MAGIC: bytes = b"P5"
PGM_MAX_VALUE: int = 255
PGM_HEADER_TOKEN_LIMIT: int = 4
_EIGHT_BIT_LUMA_FORMATS: frozenset[str] = frozenset(
    {"gray", "yuv420p", "yuv422p", "yuv444p", "yuvj420p", "yuvj422p", "yuvj444p", "nv12", "nv21"}
)
_BLACK_LUMA_TABLE: bytes = bytes(1 if value < BLACK_LUMA_THRESHOLD else 0 for value in range(256))
_CANCEL_MESSAGE: str = "화질 분석이 취소되었습니다"


class ScanDecodeError(RuntimeError):
    """PyAV로 9지점 키프레임을 읽지 못했을 때"""


class KeyframeScanResult:
    """9지점 스캔에서 찾은 검은 여백 상자와 검은 화면 지점, 실패 지점, 지점별 어두운 면 두께, 지점별 평탄 여백 요약"""

    __slots__ = ("black_points", "boxes", "darkness", "failed_points", "flat_sides")

    def __init__(
        self,
        boxes: Sequence[CropBox],
        black_points: Sequence[int],
        darkness: Sequence[EdgeDarkness | None] | None = None,
        failed_points: Sequence[int] = (),
        flat_sides: Sequence[FlatSides] | None = None,
    ) -> None:
        self.boxes: tuple[CropBox, ...] = tuple(boxes)
        self.black_points: frozenset[int] = frozenset(black_points)
        # - 키프레임을 받지 못한 지점. 검은 화면과 구분해야 캐시 재사용과 감지 실패 표시를 정할 수 있음
        self.failed_points: frozenset[int] = frozenset(failed_points)
        self.darkness: tuple[EdgeDarkness | None, ...] = (
            tuple(darkness) if darkness is not None else (None,) * len(self.boxes)
        )
        # - 지점별 네 면 평탄 여백 요약. 9지점 합의로 비검은 여백을 자를 근거이며 `boxes`와 같은 순서
        self.flat_sides: tuple[FlatSides, ...] = (
            tuple(flat_sides) if flat_sides is not None else (EMPTY_FLAT_SIDES,) * len(self.boxes)
        )



def build_scan_command(
    *,
    ffmpeg_executable: str,
    source_path: str,
    start_sec: float,
    fast_keyframe_seek: bool,
    frame_path: str,
    tonemap: bool = False,
) -> list[str]:
    """CLI 9지점 폴백에서 키프레임 한 장을 8-bit 회색조 PGM 파일로 받는 인자 목록

    - PyAV 경로와 같은 판정을 쓰려고 로그 대신 픽셀을 받음. 한 지점에 프로세스 한 번
    - HDR이면 회색조 변환 전에 SDR 톤 매핑을 거쳐 PQ 휘도를 그대로 8비트로 자르지 않음
    """
    tonemap_chain = tonemap_filter_chain(hdr=tonemap)
    grayscale = f"{tonemap_chain},format=gray" if tonemap_chain else "format=gray"
    args = [
        ffmpeg_executable,
        "-hide_banner",
        "-nostdin",
        "-y",
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
            grayscale,
            "-an",
            "-f",
            "image2",
            "-c:v",
            "pgm",
            frame_path,
        ]
    )
    return args


def _pgm_header_tokens(data: bytes) -> tuple[list[bytes], int]:
    """PGM 머리말 토큰과 픽셀 데이터가 시작하는 위치. 주석 줄은 건너뜀"""
    index = 0
    tokens: list[bytes] = []
    length = len(data)
    while len(tokens) < PGM_HEADER_TOKEN_LIMIT and index < length:
        current = data[index : index + 1]
        if current.isspace():
            index += 1
            continue
        if current == b"#":
            while index < length and data[index : index + 1] not in (b"\n", b"\r"):
                index += 1
            continue
        start = index
        while index < length and not data[index : index + 1].isspace():
            index += 1
        tokens.append(data[start:index])
    return (tokens, index + 1)


def parse_gray_frame(data: bytes) -> tuple[bytes, int, int] | None:
    """FFmpeg가 쓴 8-bit 회색조 PGM을 (Y 평면, 너비, 높이)로 해석, 형식이 다르면 None

    - 머리말은 매직, 너비, 높이, 최대값 순이며 주석이 끼어도 됨
    - 픽셀 데이터가 머리말의 너비, 높이보다 짧으면 잘린 파일로 보고 None
    """
    tokens, offset = _pgm_header_tokens(data)
    if len(tokens) < PGM_HEADER_TOKEN_LIMIT or tokens[0] != PGM_MAGIC:
        return None
    try:
        width, height, max_value = (int(token) for token in tokens[1:PGM_HEADER_TOKEN_LIMIT])
    except ValueError:
        return None
    if max_value != PGM_MAX_VALUE or width <= 0 or height <= 0:
        return None
    plane = data[offset : offset + width * height]
    if len(plane) != width * height:
        return None
    return (plane, width, height)


def _median_deviation(samples: Sequence[int], mean: float) -> float:
    """표본의 평균 편차 중앙값

    - 띠 위에 얹힌 소수의 밝은 이물 픽셀에 흔들리지 않는 편차 지표
    """
    if not samples:
        return 0.0
    deviations = sorted(abs(value - mean) for value in samples)
    return float(deviations[len(deviations) // 2])


def _row_stats(plane: bytes, offset: int, width: int) -> LineStats:
    """Y 평면 행 하나의 평균과 편차 중앙값"""
    if width <= 0:
        return (0.0, 0.0)
    row = plane[offset : offset + width]
    mean = sum(row) / width
    stride = max(1, width // SPREAD_SAMPLE_LIMIT)
    return (mean, _median_deviation(row[::stride], mean))


def _column_stats(plane: bytes, x: int, top: int, bottom: int, line_size: int) -> LineStats:
    """Y 평면 열 하나의 평균과 편차 중앙값. `bytes` 확장 슬라이스로 행을 건너뛰며 더함"""
    rows = bottom - top
    if rows <= 0:
        return (0.0, 0.0)
    start = top * line_size + x
    stop = (bottom - 1) * line_size + x + 1
    column = plane[start:stop:line_size]
    if not column:
        return (0.0, 0.0)
    mean = sum(column) / rows
    stride = max(1, rows // SPREAD_SAMPLE_LIMIT) * line_size
    return (mean, _median_deviation(plane[start:stop:stride], mean))


def _is_bar_line(stats: LineStats) -> bool:
    """어둡고 평탄한 줄, 열이면 검은 띠 후보"""
    mean, spread = stats
    return mean <= BLACK_EDGE_MEAN_THRESHOLD and spread <= BLACK_EDGE_SPREAD_THRESHOLD


def _island_max_lines(axis_length: int) -> int:
    """띠 안 자막으로 보고 건너뛸 수 있는 비-띠 줄 최대 두께"""
    return max(TEXT_ISLAND_MIN_LINES, round(axis_length * TEXT_ISLAND_RATIO))


def _dark_flat_run(axis_length: int, probe: LineProbe, line_index: Callable[[int], int]) -> int:
    """축 한 끝에서 안쪽으로 이어진 어둡고 평탄한 줄 수"""
    count = 0
    while count < axis_length and _is_bar_line(probe(line_index(count))):
        count += 1
    return count


def _has_edge_step(probe: LineProbe, line_index: Callable[[int], int], bar_count: int) -> bool:
    """마지막 띠 줄과 그 안쪽 첫 줄의 평균 차가 계단 기준 이상인지

    - 실제 띠는 그림과의 경계에서 밝기가 급격히 바뀌고, 완만한 비네트는 줄마다 조금씩만 바뀜
    """
    return probe(line_index(bar_count))[0] - probe(line_index(bar_count - 1))[0] >= BLACK_EDGE_STEP_MIN


def _flat_bar_end(
    axis_length: int,
    probe: LineProbe,
    line_index: Callable[[int], int],
) -> int | None:
    """경계 계단이 약해도 정확히 평탄한 어두운 긴 구간이면 띠로 보는 끝 줄 수

    - 실제 검은 띠는 줄 안 편차가 0에 가깝게 평탄하고 그림보다 충분히 길게 이어짐
    - 끝에는 디더, 압축 잔여로 생기는 얇은 경사가 붙으므로 평탄 구간 뒤로 그 경사까지 붙임
    - 경사 끝에서 그림과의 차이가 약한 계단 기준 이상일 때만 인정(완만한 비네트 배제)
    """
    flat_required = max(FLAT_BAR_MIN_LINES, round(axis_length * FLAT_BAR_MIN_RATIO))
    end = 0
    bar_mean = 0.0
    while end < axis_length:
        mean, spread = probe(line_index(end))
        if mean > FLAT_BAR_MEAN_LIMIT or spread > FLAT_BAR_SPREAD_LIMIT:
            break
        if end == 0:
            bar_mean = mean
        end += 1
    if end < flat_required:
        return None
    while end < axis_length:
        mean, spread = probe(line_index(end))
        if mean > bar_mean + FLAT_RAMP_MEAN_DELTA or spread > FLAT_RAMP_SPREAD_LIMIT:
            break
        end += 1
    if end >= axis_length:
        return None
    if probe(line_index(end))[0] - probe(line_index(end - 1))[0] < FLAT_RAMP_STEP_MIN:
        return None
    return end


def _residual_margin_end(
    end: int,
    axis_length: int,
    probe: LineProbe,
    line_index: Callable[[int], int],
) -> int:
    """띠 경계 뒤에 남은 얇은 잔여 여백 줄을 붙인 끝

    - 압축 잔여나 디더로 경계 뒤 1~3줄이 어둡고 평탄하게 남으면 함께 잘라냄
    - 다음 줄과의 차이가 작은 어두운 장면은 자르지 않음
    """
    for _ in range(RESIDUAL_MARGIN_MAX_LINES):
        if end + 1 >= axis_length:
            break
        mean, spread = probe(line_index(end))
        if mean > RESIDUAL_MARGIN_MEAN_LIMIT or spread > FLAT_RAMP_SPREAD_LIMIT:
            break
        if probe(line_index(end + 1))[0] - mean < RESIDUAL_MARGIN_STEP_MIN:
            break
        end += 1
    return end


def _blend_edge_end(
    end: int,
    axis_length: int,
    probe: LineProbe,
    line_index: Callable[[int], int],
) -> int:
    """띠 경계의 반화소 혼합 줄을 띠에 붙인 끝

    - 확대, 축소 재표본 경계에는 띠 줄과 그림 줄이 섞인 한 줄이 남음. 그림 몫이 절반을 넘으면
      그 줄이 누출 휘도 때문에 검은 줄로 보이므로 띠에 붙여 잘라냄
    - 그림 몫이 절반 미만이면 실제 띠 끝이라 그대로 두고, 거의 그림인 줄은 그림이라 두지 않음
    - 띠 쪽 이웃이 평탄한 띠 줄일 때만 인정해, 열린 매트처럼 여백 끝이 흐린 경계는 건드리지 않음
    """
    if end <= 0 or end + 1 >= axis_length:
        return end
    if not _is_bar_line(probe(line_index(end - 1))):
        return end
    bar_mean = probe(line_index(end - 1))[0]
    edge_mean = probe(line_index(end))[0]
    picture_mean = probe(line_index(end + 1))[0]
    span = picture_mean - bar_mean
    if span <= 0:
        return end
    share = (edge_mean - bar_mean) / span
    if share < RESIDUAL_BLEND_PICTURE_SHARE_MIN or share > RESIDUAL_BLEND_PICTURE_SHARE_MAX:
        return end
    if edge_mean - bar_mean < BLACK_EDGE_STEP_MIN or picture_mean - edge_mean < BLACK_EDGE_STEP_MIN:
        return end
    return end + 1


def _text_island_end(
    start: int,
    axis_length: int,
    probe: LineProbe,
    line_index: Callable[[int], int],
    island_max_lines: int,
) -> int | None:
    """띠 안 자막 구간을 지나친 뒤의 띠 줄 수, 자막으로 볼 수 없으면 None

    - 자막은 얇고, 뒤에 띠 간격이 있으며, 그 뒤에 자막보다 훨씬 두꺼운 그림 블록이 이어짐
    - 그림에 바로 붙은 자막은 띠 간격이 없어 지나치지 않음(잘라내면 그림 일부가 사라짐)
    """
    text_end = start
    while text_end < axis_length and not _is_bar_line(probe(line_index(text_end))):
        text_end += 1
    text_lines = text_end - start
    if text_lines > island_max_lines:
        return None
    gap_end = text_end
    while gap_end < axis_length and _is_bar_line(probe(line_index(gap_end))):
        gap_end += 1
    if gap_end - text_end < TEXT_ISLAND_MIN_GAP_LINES:
        return None
    needed_picture = text_lines * TEXT_ISLAND_BLOCK_FACTOR
    picture_end = gap_end
    while (
        picture_end < axis_length
        and picture_end - gap_end < needed_picture
        and not _is_bar_line(probe(line_index(picture_end)))
    ):
        picture_end += 1
    if picture_end - gap_end < needed_picture:
        return None
    return gap_end if _has_edge_step(probe, line_index, gap_end) else None


def _bar_scan(
    axis_length: int,
    probe: LineProbe,
    *,
    from_end: bool,
    island_max_lines: int,
) -> tuple[int, int]:
    """축 한 끝에서 안쪽으로 이어진 검은 띠 줄 수와 그 근거 구간 두께

    - 띠는 어둡고 평탄한 줄이며, 그림 첫 줄과의 평균 차가 계단 기준 이상일 때만 인정(완만한 그라데이션 배제)
    - 계단이 약해도 편차가 0에 가까운 긴 평탄 구간이면 띠로 보고 경사까지 붙여 경계를 정함
    - 띠 안의 얇은 비-띠 구간은 자막으로 보고 지나쳐 띠 경계를 더 안쪽으로 옮김
    - 경계 뒤 남은 얇은 잔여 여백 줄과 반화소 혼합 줄도 함께 자름
    - 두 번째 값은 띠 판정과 같은 평탄 조건으로 잰 어두운 구간 두께라, 띠로 인정되지 않은 어두운 그림 구간은
      들어가지 않음. 합집합이 다수 두께를 채택할 때 그 지점이 더하는 구간에 그림이 있는지 판정하는 근거가 되며,
      띠 판정이 이미 계산한 구간을 그대로 내보내 화소를 다시 훑지 않음
    """

    def line_index(offset: int) -> int:
        return axis_length - 1 - offset if from_end else offset

    dark_run = _dark_flat_run(axis_length, probe, line_index)
    if dark_run >= axis_length:
        return (dark_run, dark_run)
    if not _has_edge_step(probe, line_index, dark_run):
        flat_end = _flat_bar_end(axis_length, probe, line_index)
        if flat_end is None:
            return (0, dark_run)
        bar_count = flat_end
    else:
        bar_count = dark_run
    offset = _blend_edge_end(bar_count, axis_length, probe, line_index)
    offset = _residual_margin_end(offset, axis_length, probe, line_index)
    while True:
        island_end = _text_island_end(offset, axis_length, probe, line_index, island_max_lines)
        if island_end is None:
            return (offset, dark_run)
        offset = _blend_edge_end(island_end, axis_length, probe, line_index)
        offset = _residual_margin_end(offset, axis_length, probe, line_index)


def luma_frame_stats(data: bytes, line_size: int, width: int, height: int) -> LumaPointStats:
    """Y 평면 한 장에서 검은 픽셀 비율, 검은 여백 상자, 면별 어두운 구간 두께, 네 면 평탄 여백 요약 계산

    - 검은 픽셀 비율이 기준 이상이면 검은 화면으로 보고 상자를 내지 않음
    - 상/하 줄과 좌/우 열이 어둡고 평탄하며 그림과의 경계가 계단일 때만 여백으로 봄
    - 어두운 장면의 완만한 비네트는 줄 안 편차가 크고 경계 계단이 없어 여백으로 보지 않음
    - 띠 안에 박힌 자막 줄은 얇은 섬으로 보고 건너뛰어, 자막 아래의 띠 경계를 씀
    - 면별 어두운 두께는 띠 줄 조건(어둡고 평탄)으로 잰 구간이라, 띠로 인정되지 않은 어두운 그림 구간은 근거가 되지 않음
    - 검은 띠가 끝난 뒤 첫 유지 줄이 압축 링잉으로 밝으면 한 줄 안쪽으로 옮겨 그림 경계가 남지 않게 함
    - 평탄 여백 요약은 검은 띠 판정과 별개로 재며, 자를지는 지점 상자를 합치는 쪽이 9지점 합의로 정함
    """
    empty_edges = EdgeDarkness(0, 0, 0, 0)
    if width <= 0 or height <= 0 or line_size < width:
        return LumaPointStats(box=None, black_ratio=1.0, edges=empty_edges, flat_sides=EMPTY_FLAT_SIDES)
    plane = bytes(data)
    if len(plane) < line_size * height:
        return LumaPointStats(box=None, black_ratio=1.0, edges=empty_edges, flat_sides=EMPTY_FLAT_SIDES)
    translated = plane.translate(_BLACK_LUMA_TABLE)
    black_pixels = 0
    for row_index in range(height):
        offset = row_index * line_size
        # - 1로 바꾼 자리만 세는 C 루프라 행당 파이썬 반복 대신 한 번의 호출로 끝남
        black_pixels += translated.count(1, offset, offset + width)
    black_ratio = black_pixels / (width * height)
    if black_ratio >= BLACK_FRAME_PIXEL_RATIO:
        return LumaPointStats(box=None, black_ratio=black_ratio, edges=empty_edges, flat_sides=EMPTY_FLAT_SIDES)

    def row_probe(index: int) -> LineStats:
        return _row_stats(plane, index * line_size, width)

    row_island_lines = _island_max_lines(height)
    top, top_dark = _bar_scan(height, row_probe, from_end=False, island_max_lines=row_island_lines)
    bottom_count, bottom_dark = _bar_scan(height, row_probe, from_end=True, island_max_lines=row_island_lines)
    if top + bottom_count >= height:
        return LumaPointStats(box=None, black_ratio=black_ratio, edges=empty_edges, flat_sides=EMPTY_FLAT_SIDES)
    bottom = height - bottom_count

    def column_probe(index: int) -> LineStats:
        return _column_stats(plane, index, top, bottom, line_size)

    column_island_lines = _island_max_lines(width)
    left, left_dark = _bar_scan(width, column_probe, from_end=False, island_max_lines=column_island_lines)
    right_count, right_dark = _bar_scan(
        width, column_probe, from_end=True, island_max_lines=column_island_lines
    )
    if left + right_count >= width:
        return LumaPointStats(box=None, black_ratio=black_ratio, edges=empty_edges, flat_sides=EMPTY_FLAT_SIDES)
    right = width - right_count
    darkness = EdgeDarkness(top=top_dark, bottom=bottom_dark, left=left_dark, right=right_dark)
    box = fix_bright_overshoot(
        plane,
        line_size,
        width,
        height,
        CropBox(width=right - left, height=bottom - top, x=left, y=top),
    )
    flat_sides = measure_flat_sides(
        plane,
        line_size,
        width,
        height,
        box,
        dark_limit=BLACK_EDGE_MEAN_THRESHOLD,
    )
    return LumaPointStats(box=box, black_ratio=black_ratio, edges=darkness, flat_sides=flat_sides)



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


def transpose_edge_darkness(edges: EdgeDarkness, rotation: int) -> EdgeDarkness:
    """면별 어두운 두께를 표시 회전이 적용된 방향으로 전치

    - 상자를 전치한 `transpose_crop_box`와 같은 면 대응을 씀
    """
    turn = rotation % FULL_TURN_DEGREES
    if turn == HALF_TURN_DEGREES:
        return EdgeDarkness(top=edges.bottom, bottom=edges.top, left=edges.right, right=edges.left)
    if turn % HALF_TURN_DEGREES == 0:
        return edges
    if turn == QUARTER_TURN_DEGREES:
        return EdgeDarkness(top=edges.left, bottom=edges.right, left=edges.bottom, right=edges.top)
    return EdgeDarkness(top=edges.right, bottom=edges.left, left=edges.top, right=edges.bottom)


def _luma_bytes(frame: VideoFrame) -> tuple[bytes, int, int, int]:
    """프레임의 8-bit Y 평면 바이트, 행 바이트 수, 너비, 높이. 10-bit 등은 8-bit 회색조로 변환"""
    target = frame if frame.format.name in _EIGHT_BIT_LUMA_FORMATS else frame.reformat(format="gray")
    plane = target.planes[0]
    return (bytes(plane), plane.line_size, target.width, target.height)


@lru_cache(maxsize=1)
def _pyav_filter_names() -> frozenset[str]:
    """PyAV가 쓰는 FFmpeg 빌드의 필터 이름 목록. 한 번만 읽음"""
    from av.filter import filters_available

    return frozenset(filters_available)


def pyav_tonemap_supported() -> bool:
    """PyAV가 쓰는 FFmpeg 빌드에 톤 매핑 필터가 모두 있는지

    - PyAV는 함께 배포한 FFmpeg 라이브러리를 쓰므로 zscale 유무가 시스템 FFmpeg와 다를 수 있음
    """
    return not missing_tonemap_filters(_pyav_filter_names())


def pyav_tonemap_error_text() -> str:
    """PyAV 빌드에 톤 매핑 필터가 없을 때의 사유"""
    missing = missing_tonemap_filters(_pyav_filter_names())
    return f"{TONEMAP_UNAVAILABLE_REASON} (PyAV 빌드에 없는 필터: {', '.join(missing)})"


def _tonemapped_gray_frame(frame: VideoFrame) -> VideoFrame:
    """HDR 프레임을 SDR 톤 매핑 체인과 회색조 변환으로 통과시킨 프레임

    - 스트림 템플릿 대신 디코딩한 프레임을 밀어 넣어, 스트림의 픽셀 형식과 무관하게 같은 체인을 씀
    - 만든 필터 노드를 모아 이어 붙임(PyAV 그래프는 노드 목록 특성을 노출하지 않음)
    """
    from av.filter import Graph

    if missing_tonemap_filters(_pyav_filter_names()):
        raise ToneMapUnavailableError(pyav_tonemap_error_text())
    graph = Graph()
    nodes = [graph.add(name, args) for name, args in tonemap_filter_nodes(hdr=True)]
    nodes.append(graph.add("format", "gray"))
    graph.link_nodes(*nodes)
    graph.configure()
    graph.push(frame)
    return cast("VideoFrame", graph.pull())


def _scan_with_pyav(
    source_path: str,
    points: Sequence[float],
    *,
    cancel_event: threading.Event | None,
    on_point: PointCallback | None,
    tonemap: bool = False,
    stats_runner: FrameStatsRunner = luma_frame_stats,
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
        darkness: list[EdgeDarkness] = []
        flat_sides: list[FlatSides] = []
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
            # - 톤 매핑 결과는 회전 메타데이터가 없는 새 프레임이므로 회전은 디코딩한 프레임에서 읽음
            luma_frame = _tonemapped_gray_frame(frame) if tonemap else frame
            data, line_size, width, height = _luma_bytes(luma_frame)
            point = stats_runner(data, line_size, width, height)
            if point.black_ratio >= BLACK_FRAME_PIXEL_RATIO:
                black_points.append(index)
            elif point.box is not None:
                rotation = _frame_rotation(frame, stream)
                boxes.append(transpose_crop_box(point.box, width, height, rotation))
                darkness.append(transpose_edge_darkness(point.edges, rotation))
                flat_sides.append(transpose_flat_sides(point.flat_sides, rotation))
            if on_point is not None:
                on_point(index + 1, total)
        return KeyframeScanResult(boxes, black_points, darkness, flat_sides=flat_sides)

    finally:
        if container is not None:
            container.close()
        if source is not None:
            source.close()


def _read_gray_frame(
    *,
    source_path: str,
    start_sec: float,
    ffmpeg_executable: str,
    runner: ScanCommandRunner,
    work_dir: str,
    fast_keyframe_seek: bool,
    cancel_event: threading.Event | None,
    tonemap: bool = False,
) -> tuple[bytes, int, int] | None:
    """지점 키프레임 한 장을 CLI로 받아 (Y 평면, 너비, 높이)로 반환, 실패하면 None

    - `work_dir`는 여러 작업이 공유하므로 지점마다 다른 임시 파일 이름을 씀
    - 받은 파일은 읽은 뒤 바로 지움
    - FFmpeg가 0으로 끝나도 출력 파일이 없으면 실패한 지점으로 봄(파일 끝을 넘긴 시크 등)
    - HDR 톤 매핑 필터를 만들지 못한 실패는 `ToneMapUnavailableError`로 올려 측정 불가로 알림
    """
    frame_path = str(Path(work_dir, f"scan_{uuid.uuid4().hex}.pgm"))
    args = build_scan_command(
        ffmpeg_executable=ffmpeg_executable,
        source_path=source_path,
        start_sec=start_sec,
        fast_keyframe_seek=fast_keyframe_seek,
        frame_path=frame_path,
        tonemap=tonemap,
    )
    try:
        with command_scope(
            STAGE_FRAME_ANALYSIS, args, target=source_path, source_path=source_path
        ) as handle:
            returncode, stderr = runner(args, work_dir)
            handle.exit_code = returncode
        if returncode != 0:
            if tonemap and is_tonemap_filter_error(stderr):
                raise ToneMapUnavailableError(TONEMAP_UNAVAILABLE_REASON)
            return None
        if not Path(frame_path).is_file():
            return None
        return parse_gray_frame(Path(frame_path).read_bytes())
    finally:
        Path(frame_path).unlink(missing_ok=True)


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
    tonemap: bool = False,
    stats_runner: FrameStatsRunner = luma_frame_stats,
) -> KeyframeScanResult:
    """FFmpeg CLI 9지점 키프레임 스캔. 키프레임을 받지 못한 지점은 실패 지점으로 따로 모음

    - PyAV 경로와 같은 `luma_frame_stats` 규칙으로 판정하므로 검은 화면, 검은 여백 기준이 하나임
    - FFmpeg가 회전 메타데이터를 반영해 픽셀을 내주므로 표시 방향 전치가 필요 없음
    - 실패 지점을 검은 화면에 섞으면 "모든 지점이 검은 화면"과 "모든 지점 시크 실패"를 구분할 수 없어 따로 반환
    """
    boxes: list[CropBox] = []
    darkness: list[EdgeDarkness] = []
    flat_sides: list[FlatSides] = []
    black_points: list[int] = []
    failed_points: list[int] = []
    total = len(points)
    for index, start_sec in enumerate(points):
        _check_cancel(cancel_event)
        frame = _read_gray_frame(
            source_path=source_path,
            start_sec=start_sec,
            ffmpeg_executable=ffmpeg_executable,
            runner=runner,
            work_dir=work_dir,
            fast_keyframe_seek=fast_keyframe_seek,
            cancel_event=cancel_event,
            tonemap=tonemap,
        )
        if frame is None:
            failed_points.append(index)
        else:
            data, width, height = frame
            point = stats_runner(data, width, width, height)
            if point.black_ratio >= BLACK_FRAME_PIXEL_RATIO:
                black_points.append(index)
            elif point.box is not None:
                boxes.append(point.box)
                darkness.append(point.edges)
                flat_sides.append(point.flat_sides)
        if on_point is not None:
            on_point(index + 1, total)
    return KeyframeScanResult(boxes, black_points, darkness, failed_points, flat_sides)



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
    tonemap: bool = False,
    stats_runner: FrameStatsRunner = luma_frame_stats,
) -> KeyframeScanResult:
    """PyAV 직독을 먼저 시도하고 실패하면 CLI 9지점 스캔으로 폴백

    - stats_runner는 픽셀 판정만 대신하는 함수라 PyAV 디코딩과 CLI 폴백 순서는 그대로임
    - HDR 톤 매핑을 PyAV 빌드가 지원하지 않으면 CLI 경로만 써서 시스템 FFmpeg로 톤 매핑함
    """
    if prefer_pyav and (not tonemap or pyav_tonemap_supported()):
        try:
            return _scan_with_pyav(
                source_path,
                points,
                cancel_event=cancel_event,
                on_point=on_point,
                tonemap=tonemap,
                stats_runner=stats_runner,
            )
        except ToneMapUnavailableError:
            raise
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
        tonemap=tonemap,
        stats_runner=stats_runner,
    )


def luma_stats_task(payload: tuple[bytes, int, int, int]) -> LumaPointStats:
    """프로세스 풀 워커가 부르는 Y 평면 판정. 모듈 수준이어야 spawn 자식이 pickle로 복원할 수 있음"""
    return luma_frame_stats(*payload)



def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError(_CANCEL_MESSAGE)
