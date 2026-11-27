"""표 가로 스크롤 위치와 포커스 행을 저장 상태에서 복원하는 모듈

- 가로 스크롤 범위는 열 폭과 뷰포트 너비가 정해진 뒤에 생기므로 범위가 생길 때까지 예약을 유지
- 포커스 행은 파일명으로 찾아 선택을 건드리지 않고 포커스만 옮기며, 표가 키보드 포커스를 가져가지 않음
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QItemSelectionModel, QObject, QTimer

from src.gui.table.table_grouping import filename_at_row

# - 행이 처음 만들어진 뒤 이만큼 지나도 가로 스크롤 범위가 없으면 저장값을 버림.
#   범위가 계속 0이면 저장값이 의미가 없고, 나중에 사용자가 열 폭을 바꿀 때 화면을 가로채지 않기 위함
VIEWPORT_RESTORE_DEADLINE_MS: int = 1500
# - 저장한 포커스 영상이 없는 채로 행 재구성이 이만큼 조용하면 복원이 끝난 것으로 보고 예약을 버림.
#   화질 분석 결과는 묶어서 반영되므로, 재구성이 이어지는 동안에는 시계를 다시 돌려 기다림
FOCUS_RESTORE_SETTLE_MS: int = 5000

__all__ = [
    "FOCUS_RESTORE_SETTLE_MS",
    "VIEWPORT_RESTORE_DEADLINE_MS",
    "TableViewportRestoreController",
    "attach_viewport_restore",
    "capture_viewport_state",
]


def capture_viewport_state(view: Any) -> tuple[int, str]:
    """현재 표의 가로 스크롤 위치(픽셀)와 포커스 행 영상 파일명"""
    bar = view.horizontalScrollBar()
    left = 0 if bar is None else max(bar.value(), 0)
    return left, filename_at_row(view, view.currentIndex().row())


class TableViewportRestoreController(QObject):
    """저장한 가로 스크롤 위치와 포커스 행을 행 구성이 준비된 뒤 한 번 복원"""

    def __init__(self, view: Any) -> None:
        super().__init__(view)
        # - 표 뷰와의 순환 import를 피하려고 뷰 형식은 느슨하게 받음(table_top_restore와 같은 방식)
        self._view: Any = view
        self._left: int | None = None
        self._filename: str = ""
        # - 복원이 직접 옮기는 포커스 변경을 사용자 조작으로 오인하지 않도록 잠그는 표시
        self._applying: bool = False
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(VIEWPORT_RESTORE_DEADLINE_MS)
        self._deadline.timeout.connect(self.settle)
        self._focus_deadline = QTimer(self)
        self._focus_deadline.setSingleShot(True)
        self._focus_deadline.setInterval(FOCUS_RESTORE_SETTLE_MS)
        self._focus_deadline.timeout.connect(self.drop_focus)
        view.group_model.modelReset.connect(self._apply)
        view.group_model.layoutChanged.connect(self._apply)
        sel_model = view.selectionModel()
        if sel_model is not None:
            # - 사용자가 포커스를 직접 옮기면 남은 포커스 예약으로 화면을 되돌리지 않음
            sel_model.currentChanged.connect(self._on_current_changed)
        bar = view.horizontalScrollBar()
        if bar is not None:
            bar.rangeChanged.connect(lambda _min, _max: self._apply())
            # - 사용자가 직접 움직인 뒤에는 저장값으로 화면을 되돌리지 않음
            bar.actionTriggered.connect(self._drop_left)
            bar.sliderPressed.connect(self._drop_left)

    @property
    def pending_left(self) -> int | None:
        """아직 적용하지 못한 가로 스크롤 위치, 예약이 없으면 None"""
        return self._left

    def request(self, left: int, filename: str) -> None:
        """행 구성이 준비되면 적용할 가로 스크롤 위치와 포커스 파일명을 예약

        - 표에 이미 행이 있으면 바로 적용
        """
        self._left = left if left > 0 else None
        self._filename = filename
        self._deadline.stop()
        self._focus_deadline.stop()
        self._apply()

    def release(self) -> None:
        """남은 복원 예약을 버려 이후 행 재구성이 화면을 옮기지 않게 함"""
        self._deadline.stop()
        self._focus_deadline.stop()
        self._left = None
        self._filename = ""

    def drop_focus(self) -> None:
        """포커스 행 예약만 버림

        - 저장한 영상을 찾지 못한 채 행 재구성이 잠잠해졌거나 사용자가 직접 포커스, 스크롤을 옮겼을 때 호출
        """
        self._focus_deadline.stop()
        self._filename = ""

    def settle(self) -> None:
        """가로 스크롤 범위가 끝내 생기지 않았으므로 그 예약만 버림"""
        self._deadline.stop()
        self._left = None

    def _drop_left(self, *_args: int) -> None:
        self.settle()

    def _on_current_changed(self, current: object, _previous: object) -> None:
        """사용자가 포커스 행을 직접 옮겼으면 남은 포커스 예약을 버림

        - 모델 재구성은 포커스를 무효 인덱스로 비우므로 그 신호는 사용자 조작으로 보지 않음
        """
        if self._applying:
            return
        is_valid = getattr(current, "isValid", None)
        if callable(is_valid) and not is_valid():
            return
        self.drop_focus()

    def _apply(self) -> None:
        """행이 준비되었으면 남은 예약을 적용

        - 가로 스크롤 예약은 적용 전에 비워, 적용 중에 나오는 재구성 신호가 같은 값을 다시 적용하지 않게 함
        - 포커스 예약은 저장한 영상을 찾아 포커스를 옮겼을 때만 비움. 첫 행 집합에 없으면 뒤이은
          재구성에서 다시 찾아야 하므로, 찾지 못한 동안에는 정착 시간을 되풀이해 기다림
        """
        if self._view.group_model.rowCount() == 0:
            return
        left, self._left = self._left, None
        # - 포커스 행을 옮기면 그 행이 보이도록 가로 스크롤도 움직이므로 저장한 가로 위치는 그 뒤에 적용
        if self._filename and self._apply_current(self._filename):
            self.drop_focus()
        elif self._filename:
            self._focus_deadline.start()
        if left is not None:
            self._apply_left(left)

    def _apply_left(self, left: int) -> None:
        bar = self._view.horizontalScrollBar()
        if bar is None:
            return
        if bar.maximum() <= bar.minimum():
            # - 열 폭 계산 전에는 스크롤 범위가 없으므로 범위가 생길 때까지 예약을 유지
            self._left = left
            if not self._deadline.isActive():
                self._deadline.start()
            return
        self._view.updateGeometries()
        bar.setValue(min(left, bar.maximum()))

    def _apply_current(self, filename: str) -> bool:
        """저장한 파일명 행을 찾아 포커스만 옮김, 찾았으면 True

        - 저장한 영상이 표에 없으면 포커스를 다른 행으로 옮기지 않음
        """
        row = _find_filename_row(self._view, filename)
        if row is None:
            return False
        sel_model = self._view.selectionModel()
        if sel_model is None:
            return False
        self._applying = True
        try:
            sel_model.setCurrentIndex(
                self._view.group_model.index(row, 0),
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        finally:
            self._applying = False
        return True


def _find_filename_row(view: Any, filename: str) -> int | None:
    """표시 행 중 파일명이 같은 영상 행, 없으면 None"""
    model = view.group_model
    for row in range(model.rowCount()):
        if filename_at_row(view, row) == filename:
            return row
    return None


def attach_viewport_restore(view: Any, layout: Any) -> TableViewportRestoreController:
    """저장된 가로 스크롤 위치와 포커스 파일명을 표에 다시 적용하도록 연결

    - 레이아웃 형식은 호출부가 정하므로 느슨하게 받음
    - 표마다 컨트롤러를 하나만 두어, 저장 상태를 다시 적용해도 신호 연결이 쌓이지 않게 함
    """
    controller = getattr(view, "_viewport_restore", None)
    if not isinstance(controller, TableViewportRestoreController):
        controller = TableViewportRestoreController(view)
        view._viewport_restore = controller
    controller.request(int(layout.table_left_scroll), str(layout.table_current_filename))
    return controller
