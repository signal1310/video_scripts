"""열 프리셋별 표시 열과 열 순서 정의, 프리셋 기본 배치 적용 모듈"""

from __future__ import annotations

from collections.abc import Iterable

from src.core.settings.ui_state import UiLayoutState
from src.gui.constants import (
    COLUMN_PRESETS,
    PRESET_ALL,
    PRESET_CUSTOM,
    PRESET_DEFAULT,
    PRESET_QUALITY,
    PRESET_SPECS,
)
from src.gui.table.column_layout_state import (
    apply_column_layouts,
    capture_column_layouts,
    decode_column_layouts,
    encode_column_layouts,
)
from src.gui.table.column_sizing import (
    ALL_COLUMNS,
    DEFAULT_COL_ORDER,
    DEFAULT_HIDDEN_COLS,
)
from src.gui.table.table_model import VideoTableModel
from src.gui.table.video_table_view import VideoTableView

# 프리셋마다 보이는 열을 결정 우선, 파이프라인, 기술 사양 순서로 나열
_QUALITY_VISIBLE_ORDER: tuple[int, ...] = (
    VideoTableModel.COL_NAME,
    VideoTableModel.COL_WIDTH,
    VideoTableModel.COL_HEIGHT,
    VideoTableModel.COL_ACTIVE_FRAME,
    VideoTableModel.COL_DOWNSCALE_CANDIDATE,
    VideoTableModel.COL_QUALITY_METRIC,
    VideoTableModel.COL_OUTPUT_RES,
    VideoTableModel.COL_ZONE,
    VideoTableModel.COL_ACTION,
    VideoTableModel.COL_SAVED_SIZE,
    VideoTableModel.COL_QUALITY_VALUE,
    VideoTableModel.COL_REASON,
    VideoTableModel.COL_ENCODE_STATE,
)
_SPECS_VISIBLE_ORDER: tuple[int, ...] = (
    VideoTableModel.COL_NAME,
    VideoTableModel.COL_CODEC,
    VideoTableModel.COL_WIDTH,
    VideoTableModel.COL_HEIGHT,
    VideoTableModel.COL_FPS,
    VideoTableModel.COL_ROTATE,
    VideoTableModel.COL_KEYFRAME,
    VideoTableModel.COL_DURATION,
    VideoTableModel.COL_BITRATE,
    VideoTableModel.COL_OPT_BITRATE,
    VideoTableModel.COL_RATIO,
    VideoTableModel.COL_TARGET_RATIO,
    VideoTableModel.COL_OUT_BITRATE,
    VideoTableModel.COL_SIZE,
    VideoTableModel.COL_AFTER_SIZE,
)


def _order_with_rest(visible_order: tuple[int, ...]) -> tuple[int, ...]:
    """보이는 열을 앞에 두고 나머지 열을 기본 순서대로 뒤에 붙인 전체 열 순서"""
    rest = tuple(col for col in DEFAULT_COL_ORDER if col not in visible_order)
    return visible_order + rest


# - 기본 프리셋의 보이는 열은 기본 숨김 열을 뺀 나머지이며, 순서는 기본 열 순서를 그대로 씀
_DEFAULT_VISIBLE_ORDER: tuple[int, ...] = tuple(
    col for col in DEFAULT_COL_ORDER if col not in DEFAULT_HIDDEN_COLS
)
PRESET_VISIBLE_ORDERS: dict[str, tuple[int, ...]] = {
    PRESET_DEFAULT: _DEFAULT_VISIBLE_ORDER,
    PRESET_QUALITY: _QUALITY_VISIBLE_ORDER,
    PRESET_SPECS: _SPECS_VISIBLE_ORDER,
    PRESET_ALL: DEFAULT_COL_ORDER,
}
# - 모든 프리셋이 28개 열의 전체 순서를 가지므로 순서 적용 때 누락 열이 생기지 않음
PRESET_COL_ORDERS: dict[str, tuple[int, ...]] = {
    PRESET_DEFAULT: DEFAULT_COL_ORDER,
    PRESET_QUALITY: _order_with_rest(_QUALITY_VISIBLE_ORDER),
    PRESET_SPECS: _order_with_rest(_SPECS_VISIBLE_ORDER),
    PRESET_ALL: DEFAULT_COL_ORDER,
}
PRESET_VISIBLE_COLS: dict[str, frozenset[int]] = {
    key: frozenset(order) for key, order in PRESET_VISIBLE_ORDERS.items()
}


def hidden_columns_for_preset(preset_key: str) -> frozenset[int]:
    """프리셋 키에 해당하는 숨김 열 집합, 알 수 없는 키는 기본 프리셋으로 처리"""
    visible = PRESET_VISIBLE_COLS.get(preset_key)
    if visible is None:
        return DEFAULT_HIDDEN_COLS
    return ALL_COLUMNS - visible


def ordered_columns_for_preset(preset_key: str) -> tuple[int, ...]:
    """프리셋 키에 해당하는 전체 열 순서, 알 수 없는 키는 기본 프리셋으로 처리"""
    return PRESET_COL_ORDERS.get(preset_key, DEFAULT_COL_ORDER)


def detect_column_preset(hidden_cols: Iterable[int]) -> str:
    """숨김 열 집합이 프리셋과 정확히 같으면 그 키, 다르면 사용자 지정 키"""
    hidden = frozenset(hidden_cols)
    for preset_key in COLUMN_PRESETS:
        if hidden_columns_for_preset(preset_key) == hidden:
            return preset_key
    return PRESET_CUSTOM


def is_preset_default_layout(table_view: VideoTableView, preset_key: str) -> bool:
    """현재 열 순서와 표시가 프리셋 기본 배치와 같은지 판별

    - 열 폭은 프리셋마다 따로 정하지 않으므로 판별에서 빼, 폭만 바꾼 상태를 사용자 배치로 보지 않음
    """
    header = table_view.filter_header
    visual = [header.logicalIndex(index) for index in range(table_view.table_model.columnCount())]
    if visual != list(ordered_columns_for_preset(preset_key)):
        return False
    hidden = hidden_columns_for_preset(preset_key)
    return all(
        table_view.isColumnHidden(col) == (col in hidden)
        for col in range(table_view.table_model.columnCount())
    )


def apply_preset_default(table_view: VideoTableView, preset_key: str) -> None:
    """프리셋의 기본 열 순서와 표시 열을 표에 적용하고 열 폭은 그대로 둠"""
    hidden = hidden_columns_for_preset(preset_key)
    header = table_view.filter_header
    for target_visual, col in enumerate(ordered_columns_for_preset(preset_key)):
        current_visual = header.visualIndex(col)
        if current_visual != target_visual:
            header.moveSection(current_visual, target_visual)
    for col in range(table_view.table_model.columnCount()):
        table_view.setColumnHidden(col, col in hidden)


def load_preset_layout(
    table_view: VideoTableView, layout: UiLayoutState, preset_key: str
) -> None:
    """프리셋에 저장된 사용자 배치를 적용하고, 없으면 프리셋 기본 배치를 적용

    - 프리셋 배치는 순서와 표시만 담으므로 열 폭은 지금 화면의 공용 폭을 그대로 둠
    """
    stored = layout.column_preset_layouts.get(preset_key)
    layouts = decode_column_layouts(stored) if stored else None
    if layouts is None:
        apply_preset_default(table_view, preset_key)
        return
    apply_column_layouts(table_view, layouts, reset_sort=False, apply_widths=False)


def store_preset_layout(
    table_view: VideoTableView, layout: UiLayoutState, preset_key: str
) -> None:
    """현재 열 배치를 활성 프리셋 키 아래에 저장하고 이전 형식 호환용 열 상태도 갱신

    - 순서와 표시가 프리셋 기본 배치와 같으면 사용자 배치를 지워, 기본 배치로 되돌린 뒤 저장 키가 되살아나지 않게 함
    - 열 폭은 프리셋마다 따로 두지 않고 이전 형식 호환용 열 상태에만 남김
    """
    layouts = capture_column_layouts(table_view)
    layout.column_preset = preset_key
    layout.table_header_state = encode_column_layouts(layouts)
    if is_preset_default_layout(table_view, preset_key):
        layout.column_preset_layouts.pop(preset_key, None)
        return
    layout.column_preset_layouts[preset_key] = encode_column_layouts(
        layouts, include_widths=False
    )

