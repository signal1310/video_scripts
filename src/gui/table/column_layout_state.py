"""테이블 열 상태(표시 순서, 폭, 숨김)를 열 키별 JSON으로 저장하고 복원하는 모듈"""

import json
from dataclasses import dataclass
from typing import Any

from src.core.messages import ColumnId
from src.gui.table.table_model import VideoTableModel
from src.gui.table.video_table_view import VideoTableView

COLUMN_STATE_VERSION: int = 2

# 저장 키는 ColumnId 값을 사용. 열 개수나 순서가 바뀌어도 열 단위로 대응해 기존 설정이 어긋나지 않음
COL_INDEX_TO_ID: dict[int, ColumnId] = {
    VideoTableModel.COL_NAME: ColumnId.NAME,
    VideoTableModel.COL_WIDTH: ColumnId.WIDTH,
    VideoTableModel.COL_HEIGHT: ColumnId.HEIGHT,
    VideoTableModel.COL_ROTATE: ColumnId.ROTATE,
    VideoTableModel.COL_CODEC: ColumnId.CODEC,
    VideoTableModel.COL_FPS: ColumnId.FPS,
    VideoTableModel.COL_DURATION: ColumnId.DURATION,
    VideoTableModel.COL_BITRATE: ColumnId.BITRATE,
    VideoTableModel.COL_OPT_BITRATE: ColumnId.OPT_BITRATE,
    VideoTableModel.COL_RATIO: ColumnId.RATIO,
    VideoTableModel.COL_TARGET_RATIO: ColumnId.TARGET_RATIO,
    VideoTableModel.COL_ZONE: ColumnId.ZONE,
    VideoTableModel.COL_KEYFRAME: ColumnId.KEYFRAME,
    VideoTableModel.COL_SIZE: ColumnId.SIZE,
    VideoTableModel.COL_AFTER_SIZE: ColumnId.AFTER_SIZE,
    VideoTableModel.COL_SAVED_SIZE: ColumnId.SAVED_SIZE,
    VideoTableModel.COL_SAVED_PER_MIN: ColumnId.SAVED_PER_MIN,
    VideoTableModel.COL_EST_ENCODE_TIME: ColumnId.EST_ENCODE_TIME,
    VideoTableModel.COL_OUTPUT_RES: ColumnId.OUTPUT_RES,
    VideoTableModel.COL_ACTIVE_FRAME: ColumnId.ACTIVE_FRAME,
    VideoTableModel.COL_DOWNSCALE_CANDIDATE: ColumnId.DOWNSCALE_CANDIDATE,
    VideoTableModel.COL_QUALITY_METRIC: ColumnId.QUALITY_METRIC,
    VideoTableModel.COL_ACTION: ColumnId.ACTION,
    VideoTableModel.COL_REASON: ColumnId.REASON,
    VideoTableModel.COL_OUT_BITRATE: ColumnId.OUT_BITRATE,
    VideoTableModel.COL_ENCODE_STATE: ColumnId.ENCODE_STATE,
}
COL_ID_TO_INDEX: dict[ColumnId, int] = {cid: col for col, cid in COL_INDEX_TO_ID.items()}


@dataclass(frozen=True, slots=True)
class ColumnLayout:
    """열 하나의 표시 순서, 폭, 숨김 상태"""

    order: int
    width: int
    hidden: bool


def capture_column_layouts(table_view: VideoTableView) -> dict[ColumnId, ColumnLayout]:
    """현재 테이블의 열 상태를 열 식별자별로 수집"""
    header = table_view.filter_header
    return {
        cid: ColumnLayout(
            order=header.visualIndex(col),
            width=table_view.columnWidth(col),
            hidden=table_view.isColumnHidden(col),
        )
        for col, cid in COL_INDEX_TO_ID.items()
    }


def encode_column_layouts(layouts: dict[ColumnId, ColumnLayout]) -> str:
    """열 상태를 열 키별 JSON 문자열로 직렬화"""
    columns = {
        cid.value: {"order": lay.order, "width": lay.width, "hidden": lay.hidden}
        for cid, lay in layouts.items()
    }
    return json.dumps(
        {"version": COLUMN_STATE_VERSION, "columns": columns},
        ensure_ascii=True,
        sort_keys=True,
    )


def decode_column_layouts(text: str) -> dict[ColumnId, ColumnLayout] | None:
    """저장된 JSON을 열 상태로 역직렬화하며 형식이 맞지 않으면 None 반환

    - 알 수 없는 열 키는 무시해 열 추가, 삭제 후에도 나머지 열 설정을 보존
    - 버전이 다르거나 열 항목이 손상되면 None을 반환해 호출부가 기본값으로 복원
    """
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("version") != COLUMN_STATE_VERSION:
        return None
    raw_columns: Any = payload.get("columns")
    if not isinstance(raw_columns, dict):
        return None
    layouts: dict[ColumnId, ColumnLayout] = {}
    for key, raw in raw_columns.items():
        try:
            cid = ColumnId(key)
        except ValueError:
            continue
        layout = _parse_column_layout(raw)
        if layout is None:
            return None
        layouts[cid] = layout
    return layouts


def _parse_column_layout(raw: object) -> ColumnLayout | None:
    if not isinstance(raw, dict):
        return None
    order: Any = raw.get("order")
    width: Any = raw.get("width")
    hidden: Any = raw.get("hidden")
    if not (_is_plain_int(order) and _is_plain_int(width) and isinstance(hidden, bool)):
        return None
    if order < 0 or width < 0:
        return None
    return ColumnLayout(order=order, width=width, hidden=hidden)


def _is_plain_int(value: object) -> bool:
    # bool은 int의 하위 타입이므로 별도 제외
    return isinstance(value, int) and not isinstance(value, bool)


def capture_column_state(table_view: VideoTableView) -> str:
    """현재 테이블의 열 상태를 되돌리기 비교용 JSON 문자열로 수집"""
    return encode_column_layouts(capture_column_layouts(table_view))


def restore_column_state(table_view: VideoTableView, text: str) -> None:
    """capture_column_state가 만든 문자열을 테이블에 복원, 정렬 상태는 유지

    - 해석할 수 없는 문자열은 무시
    """
    layouts = decode_column_layouts(text)
    if layouts is not None:
        apply_column_layouts(table_view, layouts, reset_sort=False)


def apply_column_layouts(
    table_view: VideoTableView,
    layouts: dict[ColumnId, ColumnLayout],
    *,
    reset_sort: bool = True,
) -> None:
    """저장된 열 상태를 테이블에 적용하고 저장되지 않은 열은 기본값으로 둠

    - 기본값 초기화를 먼저 수행해 누락된 열의 순서, 숨김, 폭을 채움
    - 복원한 폭은 사용자 조절 폭으로 간주하고 이름 열 정책을 재적용
    - reset_sort가 참이면 정렬도 기본값으로 되돌림, 되돌리기는 정렬을 건드리지 않도록 거짓으로 호출
    """
    table_view.reset_columns()
    header = table_view.filter_header
    for target_visual, col in enumerate(_ordered_columns(layouts)):
        current_visual = header.visualIndex(col)
        if current_visual != target_visual:
            header.moveSection(current_visual, target_visual)

    saved_cols = [
        (col, layouts[cid]) for col, cid in COL_INDEX_TO_ID.items() if cid in layouts
    ]
    for col, layout in saved_cols:
        table_view.setColumnHidden(col, layout.hidden)
    # 다른 열 폭 변경 시 이름 열 자동 맞춤이 재계산되므로 이름 열 폭은 마지막에 적용
    for col, layout in sorted(saved_cols, key=lambda item: item[0] == VideoTableModel.COL_NAME):
        table_view.setColumnWidth(col, layout.width)

    header.setSectionsMovable(True)
    header.setSectionsClickable(True)
    if reset_sort:
        table_view.reset_table_sort()
    table_view.sync_name_column_user_resize_from_current()
    table_view.keep_restored_column_widths()


def _ordered_columns(layouts: dict[ColumnId, ColumnLayout]) -> list[int]:
    saved: list[tuple[int, int]] = sorted(
        (lay.order, COL_ID_TO_INDEX[cid]) for cid, lay in layouts.items()
    )
    ordered = [col for _, col in saved]
    missing = [col for col in VideoTableView.DEFAULT_COL_ORDER if col not in ordered]
    return ordered + missing
