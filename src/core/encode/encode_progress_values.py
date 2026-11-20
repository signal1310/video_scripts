"""재인코딩 진행 창의 수치 칸과 전체 패널, 결과 목록 값을 만드는 순수 계산과 표기 모듈

- 값이 없으면 정보 없음으로 표기하고, 사유는 호출자가 툴팁에 씀
- 프레임 기준 진행 비율은 진행 막대와 남은 시간 표시에만 씀. 조기 중단 판정(encode_progress.progress_ratio)은 프레임으로 대체하지 않음
"""

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from src.core.encode.encode_batch_book import STATUS_BY_STATE
from src.core.encode.encode_progress import BITS_PER_BYTE, KBIT
from src.core.encode.encode_progress_view import NO_VALUE_TEXT, progress_fraction, speed_text
from src.core.encode.encode_state import EncodeState
from src.core.formatting import format_size_mb
from src.core.models import BYTES_PER_KB
from src.core.resolution_tiers import find_source_tier

BYTES_PER_MB: int = BYTES_PER_KB * BYTES_PER_KB
SIZE_DECIMALS: int = 1
QUALITY_DECIMALS: int = 1
NO_RESULT_TEXT: str = "—"
NO_SCALE_TEXT: str = "축소 안 함"
SIZE_CHANGE_ARROW: str = " → "
RESULT_PREFIX: str = "결과: "


@dataclass(frozen=True, slots=True)
class ResultRow:
    """완료 항목 하나의 결과 값. 결과 목록의 경로, 크기 변화, 완료분 절약 합계에 씀"""

    source_path: str
    output_path: str
    source_bytes: int
    output_bytes: int
    expected_delta_mb: float | None


@dataclass(frozen=True, slots=True)
class ResultLine:
    """결과 목록 한 줄의 표시 문구. 결과 경로는 파일 칸 아래 줄에 붙이고, 없으면 빈 문자열"""

    name: str
    path_text: str
    status: str
    change: str
    reason: str
    source_path: str = ""
    replace_pending: bool = False
    leftover: bool = False
    part_left: bool = False


_UNFINISHED_STATES: frozenset[EncodeState] = frozenset({EncodeState.PENDING, EncodeState.RUNNING})


def result_lines(
    items: Iterable[tuple[str, EncodeState, str]],
    results: Mapping[str, ResultRow],
    root_by_path: Mapping[str, str],
    part_left_reason: str = "",
) -> list[ResultLine]:
    """끝난 항목만 골라 결과 줄을 만듦. 완료 결과가 있으면 결과 경로와 크기 변화를 채우고, 없으면 빈 표시

    - 임시 파일이 남은 줄(교체 대기, 결과 임시 파일이 있는 실패, 같은 자리 임시 파일 때문에 실패)은 위치 열기를 받음
    - 같은 자리 임시 파일 때문에 실패한 줄(part_left_reason과 사유가 같음)은 정리 조작도 받음
    """
    lines: list[ResultLine] = []
    for path, state, reason in items:
        if state in _UNFINISHED_STATES:
            continue
        row = results.get(path)
        root = root_by_path.get(path)
        path_text = "" if row is None or root is None else result_path_text(row.output_path, root)
        change = NO_RESULT_TEXT if row is None else size_change_text(row.source_bytes, row.output_bytes)
        part_left = state is EncodeState.FAILED and bool(part_left_reason) and reason == part_left_reason
        leftover = state is EncodeState.REPLACE_PENDING or (state is EncodeState.FAILED and (row is not None or part_left))
        lines.append(
            ResultLine(
                os.path.basename(path),
                path_text,
                STATUS_BY_STATE[state],
                change,
                reason,
                source_path=path,
                replace_pending=state is EncodeState.REPLACE_PENDING,
                leftover=leftover,
                part_left=part_left,
            )
        )
    return lines


def batch_speed_text(
    running_speeds: Sequence[float | None],
    running_positions_sec: Sequence[float],
    processed_durations_sec: Sequence[float],
    elapsed_sec: float,
) -> str:
    """전체 속도 칸 문구. 현재 속도는 진행 중 영상의 배속 평균, 평균 속도는 처리한 영상 길이를 경과 시간으로 나눈 값"""
    processed_sec = sum(processed_durations_sec) + sum(running_positions_sec)
    return speed_summary_text(mean_speed(running_speeds), average_speed(processed_sec, elapsed_sec))


def bytes_to_mb(num_bytes: int) -> float:
    """바이트를 MB 단위로 바꿈. 표기 단위는 용량 표기 설정이 정함"""
    return num_bytes / BYTES_PER_MB


def size_text(size_mb: float | None) -> str:
    """MB 값을 자동 단위 문자열로 바꿈. 값이 없으면 정보 없음"""
    return NO_VALUE_TEXT if size_mb is None else format_size_mb(size_mb, SIZE_DECIMALS, unit="auto")


def frames_text(frame: int | None) -> str:
    """프레임 수를 천 단위 쉼표로 바꿈. 값이 없으면 정보 없음"""
    return NO_VALUE_TEXT if frame is None else f"{frame:,}"


def quality_text(quality: float | None) -> str:
    """품질 값을 소수 한 자리로 바꿈. 값이 없으면 정보 없음"""
    return NO_VALUE_TEXT if quality is None else f"{quality:.{QUALITY_DECIMALS}f}"


def resolution_text(output_size: tuple[int, int] | None) -> str:
    """출력 해상도 규격 표기. 축소하지 않으면 축소 안 함, 규격에 없으면 정보 없음"""
    if output_size is None:
        return NO_SCALE_TEXT
    tier = find_source_tier(output_size[0], output_size[1])
    return NO_VALUE_TEXT if tier is None else tier.display


def original_kbps(source_bytes: int, duration_sec: float) -> float | None:
    """원본 파일의 평균 비트레이트(kbps). 크기나 길이를 모르면 None"""
    if source_bytes <= 0 or duration_sec <= 0.0:
        return None
    return source_bytes * BITS_PER_BYTE / duration_sec / KBIT


def display_fraction(
    out_time_sec: float | None, frame: int | None, duration_sec: float, fps: float
) -> float | None:
    """표시용 진행 비율. 진행 위치가 있으면 그 값을, 없으면 프레임 수를 길이와 초당 프레임으로 나눈 값을 씀"""
    if out_time_sec is not None:
        return progress_fraction(out_time_sec, duration_sec)
    if frame is None or duration_sec <= 0.0 or fps <= 0.0:
        return None
    return min(1.0, max(0.0, frame / (duration_sec * fps)))


def average_speed(processed_sec: float, elapsed_sec: float) -> float | None:
    """처리한 영상 길이를 경과 시간으로 나눈 배속. 처리한 길이나 경과 시간이 없으면 None"""
    if processed_sec <= 0.0 or elapsed_sec <= 0.0:
        return None
    return processed_sec / elapsed_sec


def mean_speed(speeds: Iterable[float | None]) -> float | None:
    """측정된 배속의 평균. 측정된 값이 없으면 None"""
    values = [speed for speed in speeds if speed is not None]
    return sum(values) / len(values) if values else None


def speed_summary_text(current: float | None, average: float | None) -> str:
    """전체 속도 칸 문구. 현재와 평균을 같은 형식으로 나란히 씀"""
    return f"현재 {speed_text(current)}, 평균 {speed_text(average)}"


def size_change_text(source_bytes: int, output_bytes: int | None) -> str:
    """원본 크기에서 결과 크기로 바뀐 모습과 변화율. 결과가 없으면 빈 표시"""
    if output_bytes is None:
        return NO_RESULT_TEXT
    change = (output_bytes - source_bytes) / source_bytes * 100.0 if source_bytes > 0 else 0.0
    return f"{size_text(bytes_to_mb(source_bytes))}{SIZE_CHANGE_ARROW}{size_text(bytes_to_mb(output_bytes))} ({change:+.0f}%)"


def result_path_text(output_path: str, root_dir: str) -> str:
    """결과 파일의 작업 폴더 기준 상대 경로 문구"""
    return f"{RESULT_PREFIX}{os.path.relpath(output_path, root_dir)}"


def saved_totals(rows: Iterable[ResultRow]) -> tuple[float | None, float | None]:
    """완료분의 실제 절약 합계와 예상 절약 합계(MB). 완료분이 없으면 둘 다 None, 예상 값이 하나도 없으면 예상만 None"""
    actual_mb = 0.0
    expected_mb = 0.0
    done = 0
    expected_count = 0
    for row in rows:
        done += 1
        actual_mb += bytes_to_mb(row.source_bytes - row.output_bytes)
        if row.expected_delta_mb is not None:
            expected_mb += row.expected_delta_mb
            expected_count += 1
    if done == 0:
        return None, None
    return actual_mb, (expected_mb if expected_count > 0 else None)


def saved_summary_text(actual_mb: float | None, expected_mb: float | None) -> str:
    """완료분 절약 칸 문구. 실제와 예상을 같은 형식으로 나란히 씀"""
    return f"실제 {size_text(actual_mb)}, 예상 {size_text(expected_mb)}"
