"""테이블 열 필터 팝오버가 쓰는 값 집계(고유 값 수, 수치 범위, 구간 버킷) 순수 함수 모듈

- 표 모델이 행 목록과 값 조회 함수를 넘겨 호출하며, Qt에 의존하지 않음
"""

from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from src.core.config import ClassifierConfig
from src.core.models import VideoEvaluation
from src.core.sorting import natural_sort_key
from src.gui.table.column_filters import ColumnDataType, ColumnSpec
from src.gui.table.numeric_buckets import build_numeric_buckets, resolve_numeric_bucket
from src.gui.table.table_columns import COL_DEF_MAP

FilterValueGetter = Callable[[VideoEvaluation, int], str | float | None]
FilterValuesGetter = Callable[[VideoEvaluation, int], Sequence[str | float | None]]
SpecialValuesGetter = Callable[[VideoEvaluation, int], Sequence[str]]


def collect_special_value_counts(
    evaluations: Sequence[VideoEvaluation],
    col: int,
    get_special_values: SpecialValuesGetter,
    sort_key: Callable[[str], Any] | None = None,
) -> list[tuple[str, int]]:
    """필터 전용 특수 항목별 행 수 목록

    - 셀 값 집합에는 섞지 않고, 팝오버에서 특수 항목으로 따로 보여 줄 때만 씀
    """
    counts: Counter[str] = Counter()
    for ev in evaluations:
        counts.update(get_special_values(ev, col))
    key = sort_key or natural_sort_key
    return sorted(counts.items(), key=lambda item: key(item[0]))


def collect_value_counts(
    evaluations: Sequence[VideoEvaluation],
    col: int,
    spec: ColumnSpec,
    get_values: FilterValuesGetter,
) -> list[tuple[str, int]]:
    """열거형, 범주형 열의 고유 값별 행 수 목록

    - 한 행이 값 여러 개를 가지면(활성 화면의 크기와 여백 확인 필요) 값마다 한 번씩 셈
    """
    counts: Counter[str] = Counter()
    for ev in evaluations:
        for value in dict.fromkeys(str(v or "") for v in get_values(ev, col)):
            counts[value] += 1
    if spec.data_type is ColumnDataType.ENUM and spec.preset_values:
        ordered = [(val, counts.get(val, 0)) for val in spec.preset_values]
        ordered.extend((key, counts[key]) for key in sorted(counts) if key not in spec.preset_values)
        return ordered
    col_def = COL_DEF_MAP.get(col)
    sort_key = col_def.value_sort_key if col_def is not None else natural_sort_key
    return sorted(counts.items(), key=lambda item: sort_key(item[0]))


def collect_numeric_bounds(
    evaluations: Sequence[VideoEvaluation],
    col: int,
    get_value: FilterValueGetter,
) -> tuple[float | None, float | None, int]:
    """수치형 열의 최솟값, 최댓값과 미측정(None) 행 수"""
    values: list[float] = []
    null_count = 0
    for ev in evaluations:
        val = get_value(ev, col)
        if val is None:
            null_count += 1
        elif isinstance(val, int | float):
            values.append(float(val))
    return (min(values), max(values), null_count) if values else (None, None, null_count)


def collect_numeric_bucket_counts(
    evaluations: Sequence[VideoEvaluation],
    col: int,
    spec: ColumnSpec,
    config: ClassifierConfig,
    get_value: FilterValueGetter,
) -> list[tuple[str, str, int]]:
    """수치형 열의 측정값을 도메인 구간 버킷별로 집계한 목록"""
    if spec.data_type is not ColumnDataType.NUMBER:
        return []
    buckets = build_numeric_buckets(spec.bucket_kind, config)
    if not buckets:
        return []
    counts: Counter[str] = Counter()
    for ev in evaluations:
        val = get_value(ev, col)
        if isinstance(val, int | float):
            bucket = resolve_numeric_bucket(float(val), buckets)
            if bucket is not None:
                counts[bucket.key] += 1
    return [
        (bucket.key, bucket.label, counts[bucket.key])
        for bucket in buckets
        if counts[bucket.key] > 0
    ]
