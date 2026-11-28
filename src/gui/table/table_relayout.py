"""행 재구성 신호를 레이아웃 변경으로 바꿔 알리는 도우미 모듈

- 원본이 같은 행 집합의 순서만 바꿨을 때 초기화 대신 레이아웃 신호로 알리면
  뷰의 선택과 포커스가 영구 인덱스를 따라 그대로 유지됨
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QModelIndex

_EMPTY_INDEX: QModelIndex = QModelIndex()

__all__ = ["LayoutRemapController", "remap_persistent_rows"]


def remap_persistent_rows(
    model: Any,
    old_filenames: list[str],
    old_label_of_header_row: dict[int, str],
) -> None:
    """새 표시 행 구성에 맞춰 영구 인덱스를 옮기고 레이아웃 변경을 마침

    - 영상 행은 파일명으로, 머리글 행은 그룹 이름으로 찾고, 사라진 행은 무효 인덱스를 줌
    """
    new_filenames, new_label_of_header_row = model.display_row_keys()
    new_row_of_filename = {
        filename: row
        for row, filename in enumerate(new_filenames)
        if filename
    }
    new_row_of_label = {
        label: row for row, label in new_label_of_header_row.items()
    }
    persistent = model.persistentIndexList()
    moved: list[QModelIndex] = []
    for index in persistent:
        row = index.row()
        new_row = None
        if 0 <= row < len(old_filenames):
            filename = old_filenames[row]
            if filename:
                new_row = new_row_of_filename.get(filename)
            else:
                label = old_label_of_header_row.get(row)
                if label is not None:
                    new_row = new_row_of_label.get(label)
        moved.append(
            model.index(new_row, index.column()) if new_row is not None else _EMPTY_INDEX
        )
    model.changePersistentIndexList(persistent, moved)


class LayoutRemapController:
    """원본 행 변경을 초기화 대신 레이아웃 변경으로 다시 만들어 선택과 포커스를 지키는 도우미

    - 재정렬 전 표시 행 기준을 모아 두고 새 구성에 맞춰 영구 인덱스를 옮김
    - 원본이 재평가 묶음을 여는 동안에는 변경을 모아 두고 끝날 때 한 번만 다시 만듦
    - 묶음 안에서 행 집합까지 바뀌었으면 초기화로 다시 만들 수밖에 없음
    """

    def __init__(
        self,
        model: Any,
        build_rows: Callable[[], None],
        rebuild_rows: Callable[[], None],
    ) -> None:
        self._model = model
        self._build_rows = build_rows
        self._rebuild_rows = rebuild_rows
        self._batch_depth = 0
        self._pending_layout = False
        self._pending_structure = False
        self._old_filenames: list[str] = []
        self._old_label_of_header_row: dict[int, str] = {}

    def remember(self) -> None:
        """다시 만들기 전 표시 행의 이어 주기 기준을 기억

        - 묶음 안에서는 시작할 때 모아 둔 기준을 그대로 쓰고 덮어쓰지 않음
        - 묶음 중에는 행 집합이 이미 바뀌어 있어 지금 읽은 기준이 이전 위치와 어긋남
        """
        if self._batch_depth > 0:
            return
        self._old_filenames, self._old_label_of_header_row = (
            self._model.display_row_keys()
        )

    def apply(self) -> None:
        """기억한 기준으로 행 구성을 다시 만들고 레이아웃 변경으로 알림"""
        model = self._model
        model.layoutAboutToBeChanged.emit()
        try:
            self._build_rows()
            remap_persistent_rows(model, self._old_filenames, self._old_label_of_header_row)
        finally:
            model.layoutChanged.emit()
        model.groups_rebuilt.emit()

    def defer_layout(self) -> bool:
        """묶음 안이면 순서 변경을 모아 두고 True, 밖이면 False"""
        if self._batch_depth == 0:
            return False
        self._pending_layout = True
        return True

    def defer_structure(self) -> bool:
        """묶음 안이면 행 집합 변경을 모아 두고 True, 밖이면 False"""
        if self._batch_depth == 0:
            return False
        self._pending_structure = True
        return True

    def start_batch(self) -> None:
        """묶음 시작, 바뀌기 전 표시 행 기준을 모아 둠"""
        if self._batch_depth == 0:
            self.remember()
        self._batch_depth += 1

    def finish_batch(self) -> None:
        """묶음 종료, 모아 둔 변경을 한 번으로 다시 만듦

        - 행 집합이 그대로고 순서만 바뀌었으면 초기화 없이 레이아웃 변경으로 마무리
        """
        if self._batch_depth == 0:
            return
        self._batch_depth -= 1
        if self._batch_depth > 0:
            return
        structure, layout = self._pending_structure, self._pending_layout
        self._pending_structure = self._pending_layout = False
        if structure:
            self._rebuild_rows()
        elif layout:
            self.apply()

    def reset(self) -> None:
        """원본 연결이 끊겨 모아 둔 묶음 상태를 버림"""
        self._batch_depth = 0
        self._pending_structure = self._pending_layout = False

