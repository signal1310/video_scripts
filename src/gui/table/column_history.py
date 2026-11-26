"""열 너비, 표시, 순서 변경을 되돌리기 기록 단위로 묶어 연결하는 모듈"""

from collections.abc import Callable

from src.core.settings.undo_history import UndoableSnapshot
from src.gui.table.column_layout_state import (
    capture_column_state,
    decode_column_layouts,
)
from src.gui.table.video_table_view import VideoTableView

COLUMN_WIDTH_HISTORY_LABEL: str = "열 너비 조정"
COLUMN_ORDER_HISTORY_LABEL: str = "열 순서 변경"
COLUMN_VISIBILITY_HISTORY_LABEL: str = "열 표시 변경"
COLUMN_RESET_HISTORY_LABEL: str = "기본 열 너비, 표시 복원"
COLUMN_PRESET_HISTORY_LABEL: str = "열 프리셋 변경"
COLUMN_PRESET_RESET_HISTORY_LABEL: str = "프리셋 기본 배치로 되돌리기"
COLUMN_FIT_HISTORY_LABEL: str = "모든 열 크기 맞춤"
_COLUMN_FALLBACK_HISTORY_LABEL: str = "열 너비, 표시, 순서 변경"


def describe_column_change(before: str, after: str) -> str:
    """열 상태 전후 비교로 되돌리기 라벨을 결정

    - 표시 여부 변경이 있으면 열 표시, 없고 순서가 다르면 열 순서, 그 외는 열 너비로 판정
    - 표시 변경은 이름 열 자동 맞춤으로 폭도 함께 바뀌므로 표시를 우선
    """
    before_layouts = decode_column_layouts(before)
    after_layouts = decode_column_layouts(after)
    if before_layouts is None or after_layouts is None:
        return _COLUMN_FALLBACK_HISTORY_LABEL
    shared = before_layouts.keys() & after_layouts.keys()
    if any(before_layouts[cid].hidden != after_layouts[cid].hidden for cid in shared):
        return COLUMN_VISIBILITY_HISTORY_LABEL
    if any(before_layouts[cid].order != after_layouts[cid].order for cid in shared):
        return COLUMN_ORDER_HISTORY_LABEL
    return COLUMN_WIDTH_HISTORY_LABEL


class ColumnHistoryRecorder:
    """헤더 조작과 열 메뉴 명령을 한 단계의 열 편집으로 묶어 되돌리기 기록에 전달

    - 헤더 마우스 조작은 누름 시점의 열 상태를 보관했다가 뗄 때 전후를 비교해 기록
    - 변화가 없는 조작(정렬 클릭 등)은 기록 단계에서 걸러짐
    """

    def __init__(
        self,
        table_view: VideoTableView,
        capture_state: Callable[[], UndoableSnapshot],
        record_change: Callable[[str, UndoableSnapshot], None],
    ) -> None:
        self._table_view = table_view
        self._capture_state = capture_state
        self._record_change = record_change
        self._pending: UndoableSnapshot | None = None
        header = table_view.filter_header
        header.layout_edit_started.connect(self._begin_edit)
        header.layout_edit_finished.connect(self._finish_edit)

    def run_command(self, label: str, action: Callable[[], None]) -> None:
        """메뉴 명령처럼 한 번에 끝나는 열 변경을 실행하고 지정한 라벨로 기록"""
        before = self._capture_state()
        action()
        self._record_change(label, before)

    def _begin_edit(self) -> None:
        # - 이미 진행 중인 편집이 있으면 가장 이른 시점을 전 상태로 유지
        if self._pending is None:
            self._pending = self._capture_state()

    def _finish_edit(self) -> None:
        before, self._pending = self._pending, None
        if before is None:
            return
        label = describe_column_change(
            before.column_layout, capture_column_state(self._table_view)
        )
        self._record_change(label, before)
