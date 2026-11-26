"""열 필터의 선택 값이 현재 표기와 설정에서도 유효한지 판정하는 모듈"""

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import replace

from src.core.config import ClassifierConfig
from src.core.encode.encode_previous_discard import PREVIOUS_DISCARD_LABEL
from src.core.settings.ui_state import ColumnFilterSnapshot
from src.gui.table.column_filters import (
    ColumnDataType,
    ColumnFilterRule,
    ColumnSpec,
    rule_to_snapshot,
    snapshot_to_rule,
)
from src.gui.table.frame_analysis_columns import (
    ACTIVE_FRAME_COLUMN,
    ACTIVE_FRAME_FULL_TEXT,
    ACTIVE_FRAME_VR_TEXT,
    DOWNSCALE_CANDIDATE_COLUMN,
    DOWNSCALE_CANDIDATE_VALUE_SET,
    QUALITY_METRIC_COLUMN,
    QUALITY_METRIC_VALUE_SET,
)
from src.gui.table.numeric_buckets import NumericBucketKind, build_numeric_buckets
from src.gui.table.table_columns import COL_DEF_MAP, ENCODE_STATE_LABELS, no_filter_value
from src.gui.table.table_model import VideoTableModel

logger = logging.getLogger(__name__)

# - 표 모델이 필터 값을 계산하지 않는 열. 값이 데이터에서 오므로 사라진 선택 값을 따질 수 없고,
#   표시 값 집합을 아는 열은 _COLUMN_VALUE_MATCHERS 가 따로 판정
_COLS_WITHOUT_FILTER_VALUE: frozenset[int] = frozenset(
    col for col, col_def in COL_DEF_MAP.items() if col_def.filter_value is no_filter_value
)
# - 활성 화면 크기 표기(예: 1920x800). 분석 결과가 있으면 닫힌 문구나 크기 표기만 표시 값이 됨
_ACTIVE_FRAME_SIZE_PATTERN: re.Pattern[str] = re.compile(r"\d+x\d+")
_ACTIVE_FRAME_VALUE_SET: frozenset[str] = frozenset(
    {"", ACTIVE_FRAME_FULL_TEXT, ACTIVE_FRAME_VR_TEXT}
)
# - 인코딩 상태 열의 표시 값: 상태 문구, 이전 폐기 문구, 배치 대상이 아닐 때의 빈 값
_ENCODE_STATE_VALUE_SET: frozenset[str] = frozenset(
    {"", *ENCODE_STATE_LABELS, PREVIOUS_DISCARD_LABEL}
)


def _is_active_frame_value(value: str) -> bool:
    """활성 화면 열의 현재 표시 값인지: 닫힌 문구이거나 가로x세로 표기"""
    return value in _ACTIVE_FRAME_VALUE_SET or (
        _ACTIVE_FRAME_SIZE_PATTERN.fullmatch(value) is not None
    )


# - 필터 값이 분석 결과에서 오지만 표시 값 집합은 정해진 열의 값 판정. 없는 열은 정리하지 않음
_COLUMN_VALUE_MATCHERS: dict[int, Callable[[str], bool]] = {
    ACTIVE_FRAME_COLUMN: _is_active_frame_value,
    DOWNSCALE_CANDIDATE_COLUMN: DOWNSCALE_CANDIDATE_VALUE_SET.__contains__,
    QUALITY_METRIC_COLUMN: QUALITY_METRIC_VALUE_SET.__contains__,
    VideoTableModel.COL_ENCODE_STATE: _ENCODE_STATE_VALUE_SET.__contains__,
}


def prune_stale_values(
    snapshot: ColumnFilterSnapshot,
    col: int,
    config: ClassifierConfig | None,
) -> ColumnFilterSnapshot | None:
    """열의 닫힌 값 집합에 없는 선택 값을 버린 스냅샷, 남는 조건이 없으면 None

    - 사라진 값은 현재 표기로 옮기지 않고 버림
    - 열거형 열은 스펙의 값 집합으로 판정
    - 필터 값을 계산하지 않는 열은 표시 값 집합을 따로 정의한 열(활성 화면, 축소 후보, 측정 지표, 인코딩 상태)만 판정
    - 수치형 열의 구간 키는 설정에서 만들어지므로 현재 설정의 버킷 키 집합으로 판정
    - 구간 키를 모두 버려도 수치 범위나 미측정 포함 여부가 남아 있으면 선택 값만 지움
    - 문자열, 범주형 열은 값이 데이터에서 오므로 그대로 둠
    """
    spec = VideoTableModel.get_column_spec(col)
    if snapshot.selected_values is None:
        return snapshot
    is_displayed = _display_value_predicate(col, spec, config)
    if is_displayed is None:
        return snapshot
    kept = tuple(value for value in snapshot.selected_values if is_displayed(value))
    if kept == snapshot.selected_values:
        return snapshot
    if not kept:
        # - 구간 키만 버리고, 함께 저장된 수치 조건은 남겨 필터가 통째로 사라지지 않게 함
        if _has_numeric_constraint(snapshot):
            return replace(snapshot, selected_values=None)
        return None
    return replace(snapshot, selected_values=kept)


def prune_stale_filter_rules(
    rules: Mapping[int, ColumnFilterRule],
    config: ClassifierConfig | None,
) -> dict[int, ColumnFilterRule]:
    """활성 열 필터에서 사라진 선택 값을 버린 규칙 맵, 남는 조건이 없는 열은 빠짐

    - 설정이 바뀌면 수치 구간 키가 새로 만들어지므로, 남겨 둔 키가 사라져 표가 비는 것을 막음
    """
    pruned: dict[int, ColumnFilterRule] = {}
    changed: list[int] = []
    for col, rule in rules.items():
        snapshot = rule_to_snapshot(rule)
        kept = prune_stale_values(snapshot, col, config)
        if kept != snapshot:
            changed.append(col)
        if kept is not None:
            pruned[col] = rule if kept == snapshot else snapshot_to_rule(kept)
    if changed:
        logger.info(
            "설정이 바뀌어 활성 열 필터의 사라진 선택 값을 버렸습니다: %s",
            ", ".join(COL_DEF_MAP[col].full_header or COL_DEF_MAP[col].header for col in changed),
        )
    return pruned


def _display_value_predicate(
    col: int, spec: ColumnSpec, config: ClassifierConfig | None
) -> Callable[[str], bool] | None:
    """선택 값이 지금도 열에 표시될 수 있는 값인지 판정하는 함수, 판정할 수 없는 열은 None"""
    matcher = _COLUMN_VALUE_MATCHERS.get(col)
    if matcher is not None:
        return matcher
    if col in _COLS_WITHOUT_FILTER_VALUE:
        return None
    allowed = _closed_value_set(spec, config)
    if not allowed:
        return None
    return lambda value: value in allowed


def _has_numeric_constraint(snapshot: ColumnFilterSnapshot) -> bool:
    """선택 값과 별개로 남는 수치 조건이 있는지"""
    return (
        snapshot.min_value is not None
        or snapshot.max_value is not None
        or snapshot.null_only
        or not snapshot.include_null
    )


def _closed_value_set(spec: ColumnSpec, config: ClassifierConfig | None) -> frozenset[str]:
    """열의 선택 가능한 값 집합, 값 집합이 정해지지 않은 열은 빈 집합"""
    if spec.data_type is ColumnDataType.ENUM:
        return frozenset(spec.preset_values)
    if spec.data_type is ColumnDataType.NUMBER and spec.bucket_kind is not NumericBucketKind.NONE:
        return frozenset(bucket.key for bucket in build_numeric_buckets(spec.bucket_kind, config))
    return frozenset()
