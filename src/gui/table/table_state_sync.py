"""표 열 필터와 정렬 우선순위를 저장 형식(열 ID 키)과 프록시 모델 사이에서 변환하는 모듈"""

import logging
from collections.abc import Mapping, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHeaderView

from src.core.config import ClassifierConfig
from src.core.messages import COLUMN_LABELS
from src.core.settings.ui_state import ColumnFilterSnapshot
from src.gui.table.column_filter_values import prune_stale_values
from src.gui.table.column_filters import rule_to_snapshot, snapshot_to_rule
from src.gui.table.column_layout_state import COL_INDEX_TO_ID
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_model import VideoTableModel

logger = logging.getLogger(__name__)

_COLUMN_INDEX_BY_KEY: dict[str, int] = {cid.value: col for col, cid in COL_INDEX_TO_ID.items()}
_COLUMN_LABEL_BY_KEY: dict[str, str] = {
    cid.value: COLUMN_LABELS[cid].full for cid in COL_INDEX_TO_ID.values()
}


def snapshot_column_filters(proxy_model: VideoSortFilterProxyModel) -> dict[str, ColumnFilterSnapshot]:
    """프록시 모델의 활성 열 필터를 열 ID 키 스냅샷 맵으로 변환"""
    return {
        COL_INDEX_TO_ID[col].value: rule_to_snapshot(rule)
        for col, rule in proxy_model.get_active_column_filters().items()
        if col in COL_INDEX_TO_ID
    }


def snapshot_sort_keys(proxy_model: VideoSortFilterProxyModel) -> list[tuple[str, bool]]:
    """프록시 모델의 정렬 우선순위를 (열 ID, 오름차순 여부) 목록으로 변환, 앞쪽이 주 정렬"""
    return [
        (COL_INDEX_TO_ID[col].value, order == Qt.SortOrder.AscendingOrder)
        for col, order in proxy_model.sort_stack
        if col in COL_INDEX_TO_ID
    ]


def apply_saved_filters(
    proxy_model: VideoSortFilterProxyModel, columns: Mapping[str, ColumnFilterSnapshot]
) -> dict[str, ColumnFilterSnapshot | None]:
    """저장된 열 필터 스냅샷으로 프록시의 열 필터를 교체, 알 수 없는 열 ID는 무시

    - 열 값 표기가 바뀐 뒤 복원하면 사라진 선택 값이 남아 표가 비므로, 닫힌 값 집합에 없는 값은 현재 표기로 옮기지 않고 버림
    - 수치 열 구간 키도 현재 설정의 버킷 키 집합으로 걸러, 임계값이 바뀐 뒤 사라진 키가 남지 않게 함
    - 버릴 값만 있던 열은 규칙 자체를 버리되, 함께 저장된 수치 범위나 미측정 포함 여부는 남김
    - 정리한 열은 바뀐 값(버린 열은 None)으로 돌려주어 호출부가 저장 상태를 같은 내용으로 고칠 수 있게 함
    """
    proxy_model.clear_all_column_filters()
    config = _column_filter_config(proxy_model)
    changes: dict[str, ColumnFilterSnapshot | None] = {}
    for key, snapshot in columns.items():
        col = _COLUMN_INDEX_BY_KEY.get(key)
        if col is None:
            continue
        kept = prune_stale_values(snapshot, col, config)
        if kept is not snapshot:
            changes[key] = kept
        if kept is None:
            continue
        proxy_model.set_column_filter(col, snapshot_to_rule(kept))
    if changes:
        logger.info(
            "저장된 열 필터의 사라진 선택 값을 버렸습니다: %s",
            ", ".join(_COLUMN_LABEL_BY_KEY[key] for key in changes),
        )
    return changes


def _column_filter_config(proxy_model: VideoSortFilterProxyModel) -> ClassifierConfig | None:
    """프록시에 연결된 표 모델의 현재 분류 설정, 연결이 없으면 None"""
    model = proxy_model.sourceModel()
    return model.config if isinstance(model, VideoTableModel) else None


def apply_saved_sort(
    proxy_model: VideoSortFilterProxyModel,
    header: QHeaderView,
    sort_keys: Sequence[tuple[str, bool]],
) -> None:
    """저장된 정렬 우선순위를 프록시에 적용하고, 주 정렬 열의 정렬 화살표를 신호 없이 표시"""
    # - 안정 정렬은 마지막에 적용한 열이 주 정렬이 되므로 우선순위의 역순으로 적용
    for key, ascending in reversed(sort_keys):
        if (col := _COLUMN_INDEX_BY_KEY.get(key)) is not None:
            proxy_model.sort(col, _sort_order(ascending))
    primary = _COLUMN_INDEX_BY_KEY.get(sort_keys[0][0]) if sort_keys else None
    if primary is None:
        return
    header.blockSignals(True)
    try:
        header.setSortIndicatorShown(True)
        header.setSortIndicator(primary, _sort_order(sort_keys[0][1]))
    finally:
        header.blockSignals(False)


def _sort_order(ascending: bool) -> Qt.SortOrder:
    return Qt.SortOrder.AscendingOrder if ascending else Qt.SortOrder.DescendingOrder
