"""행 초기화 전후로 표 선택과 포커스를 파일명으로 이어 주는 도우미 모듈

- 부분 분석 결과 반영이 행 집합을 다시 만들어 초기화할 때 선택이 지워지지 않게 함
- 시작, 폴더 전환 복원 예약이 있으면 그 흐름이 선택을 다시 정하므로 따로 이어 주지 않음
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from PyQt6.QtCore import QItemSelectionModel

__all__ = ["PendingSelectionRestore", "SelectionKeeper"]


class SelectionKeeper:
    """행 초기화 전 선택과 포커스 행의 파일명을 기억해 뒤에 복원"""

    def __init__(self, view: Any) -> None:
        self._view = view
        self.filenames: set[str] | None = None
        self.current: str = ""
        self.dropping: bool = False
        # - 부분 결과 반영이 행 집합을 다시 만들어 초기화할 때 선택과 포커스를 파일명으로 이어 받음
        # - 작업 폴더가 바뀌면 이전 폴더의 선택을 이어 주지 않도록 표 모델에 연결
        # - 생성은 그룹 프록시 reset 연결(그룹 머리글 병합) 뒤에 이루어져야 슬롯 순서가 지켜짐
        view.group_model.modelAboutToBeReset.connect(self.capture)
        view.group_model.modelReset.connect(self.restore)
        view.table_model.root_changed.connect(self.drop)

    def capture(self) -> None:
        """행 초기화 전 선택과 포커스 행의 파일명을 기억"""
        view = self._view
        self.filenames = None
        self.current = ""
        if view.has_pending_selection():
            # - 이번 초기화는 예약 복원이 맡으므로 앞서 남은 해제 표시를 다음 초기화로 넘기지 않음
            self.dropping = False
            return
        selected = view.get_selected_filenames()
        self.filenames = selected or None
        current = view.get_evaluation_at(view.currentIndex())
        if current is not None:
            self.current = current.video.filename

    def drop(self) -> None:
        """작업 폴더가 바뀌었으므로 이번 초기화에서 이전 선택을 이어 주지 않음"""
        self.dropping = True
        self.filenames = None
        self.current = ""

    def restore(self) -> None:
        """행 초기화 뒤 기억한 선택과 포커스를 파일명으로 복원

        - 복원은 선택 기록과 미리 보기 알림을 내지 않도록 보호 안에서 진행
        - 파일명별 표시 행 색인을 직접 만들어 뷰 도우미를 거치지 않음
        """
        view = self._view
        filenames = self.filenames
        current = self.current
        self.filenames = None
        self.current = ""
        dropping = self.dropping
        self.dropping = False
        if dropping or view.has_pending_selection():
            return
        if not filenames and not current:
            return
        group_model = view.group_model
        rows = self.rows_of_filenames()
        view.set_selection_sync_guard(True)
        try:
            sel_model = view.selectionModel()
            if sel_model is None:
                return
            if filenames:
                sel_model.clearSelection()
                for filename, row in rows.items():
                    if filename in filenames:
                        sel_model.select(
                            group_model.index(row, 0),
                            QItemSelectionModel.SelectionFlag.Select
                            | QItemSelectionModel.SelectionFlag.Rows,
                        )
            if current and current in rows:
                sel_model.setCurrentIndex(
                    group_model.index(rows[current], 0),
                    QItemSelectionModel.SelectionFlag.NoUpdate,
                )
        finally:
            view.set_selection_sync_guard(False)

    def select_filenames(self, filenames: set[str]) -> None:
        """파일명이 일치하는 행만 선택 상태로 복원(재평가로 모델이 초기화된 뒤 선택 유지용)"""
        view = self._view
        sel_model = view.selectionModel()
        if sel_model is None:
            return
        group_model = view.group_model
        rows = [
            row for filename, row in self.rows_of_filenames().items() if filename in filenames
        ]
        view.set_selection_sync_guard(True)
        try:
            sel_model.clearSelection()
            for row in rows:
                sel_model.select(
                    group_model.index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        finally:
            view.set_selection_sync_guard(False)

    def rows_of_filenames(self) -> dict[str, int]:
        """파일명별 표시 행 색인, 그룹 머리글 행은 빠짐"""
        rows: dict[str, int] = {}
        group_model = self._view.group_model
        for row in range(group_model.rowCount()):
            if group_model.is_group_row(row):
                continue
            ev = self._view.get_evaluation_at(group_model.index(row, 0))
            if ev is not None:
                rows[ev.video.filename] = row
        return rows


class PendingSelectionRestore:
    """행이 채워질 때 저장해 둔 선택 파일명 집합을 한 번만 복원하는 예약"""

    def __init__(self, view: Any) -> None:
        self._view = view
        self._pending: set[str] | None = None

    def request(self, filenames: Iterable[str]) -> None:
        """다음에 행이 채워질 때 복원할 파일명 집합을 예약"""
        names = {name for name in filenames if name}
        self._pending = names or None

    def take(self) -> set[str] | None:
        """예약된 시작, 폴더 전환 선택 복원 목록을 꺼내 반환하고 비움"""
        pending = self._pending
        self._pending = None
        return pending

    def has_pending(self) -> bool:
        """예약된 시작, 폴더 전환 선택 복원이 남아 있는지"""
        return self._pending is not None

    def apply(self) -> None:
        """행이 생기거나 순서가 바뀐 뒤 남은 예약 복원을 적용"""
        if self._pending is None or self._view.is_selection_syncing() or self._view.group_model.rowCount() == 0:
            return
        if pending := self.take():
            self._view.select_filenames(pending)

