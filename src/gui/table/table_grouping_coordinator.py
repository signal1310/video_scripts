"""행 묶기 드롭다운 선택을 표 그룹 프록시, 필터 바 버튼, 저장 상태에 함께 반영하는 조율자 모듈

- 묶기 키 선택, 접힘 기억, 그룹 수 계산 시점을 한 곳에서 정함
- 그룹 수는 `groups_rebuilt` 뒤 메뉴를 처음 열 때 한 번만 세어 메뉴 열기 지연을 줄임
"""

from __future__ import annotations

from typing import Any

from src.gui.table.group_collapse_state import CollapsedGroupStore
from src.gui.table.table_grouping import (
    group_label,
    group_option_counts,
    group_options,
)

__all__ = ["RowGroupingCoordinator", "attach_row_grouping"]


class RowGroupingCoordinator:
    """행 묶기 선택을 표, 필터 바 버튼, 저장 상태에 함께 반영"""

    def __init__(
        self,
        table_view: Any,
        filter_bar: Any,
        ui_state: Any,
        persist: Any,
    ) -> None:
        self._view = table_view
        self._filter_bar = filter_bar
        self._ui = ui_state
        self._persist = persist
        # - 그룹 구성이 바뀌면 메뉴를 열 때 그룹 수를 한 번 다시 셈
        self._counts_dirty: bool = True
        filter_bar.row_group_selected.connect(self.apply_group_by)
        filter_bar.row_group_menu_opening.connect(self._on_menu_opening)
        filter_bar.expand_all_groups_requested.connect(self._view.group_model.expand_all_groups)
        filter_bar.collapse_all_groups_requested.connect(self._view.group_model.collapse_all_groups)
        # - 필터, 검색, 정렬, 데이터 변경으로 그룹 구성이 다시 만들어지면 버튼의 그룹 수를 갱신
        self._view.group_model.groups_rebuilt.connect(self._on_groups_rebuilt)
        self._view.group_model.collapsed_changed.connect(self._on_collapsed_changed)
        # - 작업 폴더가 바뀌면 이전 폴더의 그룹 이름은 의미가 없으므로 접힘 기억을 함께 놓음
        self._view.table_model.root_changed.connect(self._reset_collapsed)
        self._collapsed_store = CollapsedGroupStore(self._view.group_model, self._ui)
        self.sync_from_state()

    @property
    def group_by(self) -> str:
        """지금 적용 중인 묶기 키"""
        return self._view.group_model.group_by

    def apply_group_by(self, group_by: str) -> None:
        """사용자가 고른 묶기 키를 표와 저장 상태에 적용

        - 묶기 키를 바꾸면 그룹 모델이 접힘을 버리므로 먼저 지금 키의 접힘을 저장해야 기억이 남음
        - 새 키에 저장된 접힘이 있으면 그 그룹 체계에서만 다시 적용
        """
        self._collapsed_store.store()
        self._view.group_model.set_group_by(group_by)
        self._ui.filters.group_by = self._view.group_model.group_by
        self._collapsed_store.restore()
        self.sync_options()
        self._persist()

    def sync_from_state(self) -> None:
        """저장된 묶기 키와 그 키의 접힌 그룹을 표에 적용, 숨김 열의 묶기도 그대로 유지"""
        self._view.group_model.set_group_by(self._ui.filters.group_by)
        self._collapsed_store.restore()
        self.sync_options()

    def _on_collapsed_changed(self) -> None:
        """사용자가 그룹을 접거나 펼쳤으면 저장 상태에 반영하고 즉시 기록"""
        self._collapsed_store.store()
        self._persist()

    def _reset_collapsed(self) -> None:
        """작업 폴더 전환 시 접힘 상태와 저장값을 비워 새 폴더를 모두 펼친 상태로 보여 줌"""
        self._collapsed_store.reset()
        self._persist()

    def sync_options(self) -> None:
        """묶기 메뉴 항목과 버튼 글자를 지금 상태에 맞춤

        - 그룹 수는 `groups_rebuilt` 뒤 메뉴를 처음 열 때 한 번 계산해 재사용
        """
        self._filter_bar.set_row_group_options(group_options())
        self._counts_dirty = True
        self._refresh_button()

    def _on_groups_rebuilt(self) -> None:
        """그룹 구성이 바뀌면 버튼 글자를 맞추고 다음 메뉴 열기에서 그룹 수를 다시 세게 함"""
        self._counts_dirty = True
        self._refresh_button()

    def _on_menu_opening(self) -> None:
        """메뉴를 열 때 그룹 구성이 바뀐 뒤 처음이면 그룹 수를 한 번 계산해 넘김"""
        if not self._counts_dirty:
            return
        self._counts_dirty = False
        self._filter_bar.set_row_group_counts(group_option_counts(self._view.proxy_model))

    def _refresh_button(self) -> None:
        """지금 묶기 키와 그룹 수만 버튼에 반영, 메뉴는 다음에 열 때 다시 만듦"""
        self._filter_bar.set_row_group(
            self._view.group_model.group_by,
            label=group_label(self._view.group_model.group_by),
            count=self._view.group_model.group_count,
        )


def attach_row_grouping(
    table_view: Any,
    filter_bar: Any,
    ui_state: Any,
    persist: Any,
) -> RowGroupingCoordinator:
    """필터 바의 행 묶기 드롭다운을 표 그룹 프록시에 연결"""
    return RowGroupingCoordinator(table_view, filter_bar, ui_state, persist)
