"""표 열 필터와 정렬 우선순위를 저장 형식(열 ID 키)과 프록시 모델 사이에서 변환하는 모듈"""

from collections.abc import Mapping, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHeaderView

from src.core.settings.ui_state import ColumnFilterSnapshot
from src.gui.column_filters import rule_to_snapshot, snapshot_to_rule
from src.gui.column_layout_state import COL_INDEX_TO_ID
from src.gui.proxy_model import VideoSortFilterProxyModel

_COLUMN_INDEX_BY_KEY: dict[str, int] = {cid.value: col for col, cid in COL_INDEX_TO_ID.items()}


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
) -> None:
    """저장된 열 필터 스냅샷으로 프록시의 열 필터를 교체, 알 수 없는 열 ID는 무시"""
    proxy_model.clear_all_column_filters()
    for key, snapshot in columns.items():
        if (col := _COLUMN_INDEX_BY_KEY.get(key)) is not None:
            proxy_model.set_column_filter(col, snapshot_to_rule(snapshot))


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
