"""표 맨 위 영상 복원 기준을 행 재구성이 끝난 뒤에도 다시 맞추는 모듈

- 복원 기준을 한 번만 적용하면, 뒤이어 도착한 분석 결과가 위쪽에 행을 넣거나 빼면서 같은
  스크롤 값을 다른 영상에 대응시켜 저장했던 영상이 화면 맨 위에서 밀려남
- 기준이 살아 있는 동안에는 그룹 모델이 행을 다시 만들 때마다 기준 영상을 지금 행에서 다시
  찾아 맨 위에 놓고, 사용자가 직접 스크롤하거나 정착 시간 동안 재구성이 없으면 기준을 놓음
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QAbstractItemView

from src.gui.table.table_grouping import nearest_video_row

# - 화질 분석 결과는 300ms 간격으로 묶어 반영하므로, 마지막 행 재구성 뒤 이만큼 조용하면
#   시작 복원 흐름이 끝난 것으로 보고 기준을 놓음(진행 중인 분석은 300ms마다 타이머를 되풀이 갱신)
ANCHOR_SETTLE_MS: int = 5000
# - 표의 맨 위를 바꾸는 키보드 이동 키, 이 키 입력은 사용자가 위치를 고른 것으로 봄
# - 키 이벤트가 정수를 주므로 비교용 집합도 열거자의 정숫값을 담음
_NAVIGATION_KEY_CODES: frozenset[int] = frozenset(
    int(key)
    for key in (
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_PageUp,
        Qt.Key.Key_PageDown,
        Qt.Key.Key_Home,
        Qt.Key.Key_End,
    )
)


class TableTopRestoreController(QObject):
    """저장한 맨 위 영상 기준을 그룹 모델이 행을 다시 만들 때마다 다시 맞추는 컨트롤러"""

    def __init__(self, view: Any) -> None:
        super().__init__(view)
        # - 표 뷰와의 순환 import를 피하려고 뷰 형식은 느슨하게 받음(table_grouping의 도우미와 같은 방식)
        self._view: Any = view
        self._key: tuple[str, int] | None = None
        # - 기준을 한 번이라도 적용했는지, 적용 뒤 표가 비면 다른 폴더로 바뀐 것으로 보고 기준을 놓음
        self._applied = False
        # - 프로그램이 실행한 스크롤을 사용자 조작으로 오인해 기준을 놓지 않도록 잠그는 표시
        self._programmatic_scroll = False
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(ANCHOR_SETTLE_MS)
        self._settle_timer.timeout.connect(self.settle)
        # - 그룹 프록시는 install_group_proxy가 같은 reset에 그룹 머리글 병합(sync_group_spans)을
        #   먼저 연결하므로, 이 슬롯은 병합이 끝난 뒤 실행되어 늘어난 머리글 행까지 반영해 스크롤함
        view.group_model.modelReset.connect(self._reapply)
        view.group_model.layoutChanged.connect(self._reapply)
        scroll_bar = view.verticalScrollBar()
        if scroll_bar is not None:
            # - actionTriggered는 휠, 페이지 클릭, 홈/엔드 키처럼 스크롤바를 통한 이동에만 발생
            scroll_bar.actionTriggered.connect(self._on_scrollbar_acted)
            # - 손잡이 끌기는 actionTriggered를 내지 않으므로 누르는 순간을 따로 받음
            scroll_bar.sliderPressed.connect(self.note_user_scroll)

    @property
    def active(self) -> bool:
        """복원 기준이 살아 있는지"""
        return self._key is not None

    def request(self, filename: str, row: int) -> None:
        """다음 행 재구성 때 filename을 맨 위에 놓도록 기준을 잡음, 파일명이 없으면 기준을 놓음

        - 첫 적용 전에는 정착 시간을 세지 않음. 시작 복원 분석이 끝날 때까지 기다리기 위함
        """
        if not filename:
            self.release()
            return
        self._settle_timer.stop()
        self._key = (filename, max(row, 0))
        self._applied = False

    def current_key(self) -> tuple[str, int] | None:
        """기준 영상의 지금 행, 기준이 없거나 표가 비었으면 None

        - 기준이 살아 있는 동안에는 화면에 보이는 행이 아니라 기준 영상을 저장해야 하므로
          중간 재구성으로 스크롤이 어긋나도 저장 값이 다른 영상으로 바뀌지 않음
        """
        if self._key is None or self._view.group_model.rowCount() == 0:
            return None
        filename, row = self._key
        return filename, nearest_video_row(self._view, filename, row)

    def release(self) -> None:
        """복원 기준을 놓아 이후 행 재구성이 스크롤을 옮기지 않게 함"""
        self._settle_timer.stop()
        self._key = None

    def settle(self) -> None:
        """정착 시간 동안 행 재구성이 없었으므로 기준을 놓음"""
        self.release()

    def note_user_scroll(self) -> None:
        """사용자가 직접 스크롤했으면 기준을 놓음, 프로그램 스크롤 중에는 무시"""
        if self._programmatic_scroll:
            return
        self.release()

    def note_key(self, key: int) -> None:
        """키보드 이동 키 입력이면 사용자가 위치를 고른 것으로 보고 기준을 놓음"""
        if key in _NAVIGATION_KEY_CODES:
            self.release()

    def _on_scrollbar_acted(self, _action: int) -> None:
        """스크롤바를 직접 끌거나 눌렀으면 사용자 스크롤로 보고 기준을 놓음"""
        self.note_user_scroll()

    def _reapply(self) -> None:
        """행 구성이 바뀌었으면 기준 영상을 지금 행에서 다시 찾아 맨 위에 놓음"""
        if self._key is None:
            return
        model = self._view.group_model
        if model.rowCount() == 0:
            # - 기준을 한 번 적용한 뒤 표가 비었다면 다른 폴더로 바뀐 것이므로 이전 기준을 쓰지 않음
            if self._applied:
                self.release()
            return
        filename, row = self._key
        target = nearest_video_row(self._view, filename, row)
        self._key = (filename, target)
        self._applied = True
        self._settle_timer.start()
        # - 재구성 직후에는 스크롤 범위와 행 위치가 아직 갱신되지 않았을 수 있어 레이아웃을 먼저 맞춤
        self._programmatic_scroll = True
        try:
            self._view.updateGeometries()
            self._view.scrollTo(model.index(target, 0), QAbstractItemView.ScrollHint.PositionAtTop)
        finally:
            self._programmatic_scroll = False


__all__ = ["ANCHOR_SETTLE_MS", "TableTopRestoreController"]
