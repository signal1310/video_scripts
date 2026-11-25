"""행 묶기 접힘 상태를 저장 상태와 표 그룹 모델 사이에서 주고받는 모듈

- 묶기 키마다 그룹 체계가 달라 접힌 이름을 키별로 나눠 두고 지금 키만 읽고 씀
- 작업 폴더가 바뀌면 이전 폴더의 그룹 이름은 의미가 없어 함께 비움
"""

from __future__ import annotations

from typing import Any

from src.core.settings.ui_state import GROUP_BY_NONE

__all__ = ["CollapsedGroupStore"]


class CollapsedGroupStore:
    """표 그룹 모델의 접힌 이름과 저장 상태(묶기 키별 이름 목록)를 동기화"""

    def __init__(self, group_model: Any, ui_state: Any) -> None:
        # - 그룹 모델과 UiState 형식은 이 모듈을 가져다 쓰는 쪽이 정하므로 느슨하게 받음
        self._model: Any = group_model
        self._ui: Any = ui_state

    def restore(self) -> None:
        """지금 묶기 키에 저장된 접힌 그룹 이름을 표에 적용, 없는 이름은 모델이 무시"""
        self._model.set_collapsed_labels(
            self._ui.filters.collapsed_groups.get(self._model.group_by, [])
        )

    def store(self) -> None:
        """지금 묶기 키의 접힌 그룹 이름만 갱신하고 다른 키의 이름은 그대로 둠"""
        filters = self._ui.filters
        key = self._model.group_by
        labels = [] if key == GROUP_BY_NONE else self._model.collapsed_labels
        store = {group_key: list(names) for group_key, names in filters.collapsed_groups.items()}
        if labels:
            store[key] = labels
        else:
            store.pop(key, None)
        filters.collapsed_groups = store

    def reset(self) -> None:
        """표와 저장 상태의 접힘을 모두 버려 모든 그룹을 펼친 상태로 되돌림"""
        self._model.reset_collapsed()
        self._ui.filters.collapsed_groups = {}
