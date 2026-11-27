"""내장 미리 보기 창의 소유 창 전환, 소멸 대비, 숨김 뒤 소유 창 올리기

- 소유 창은 앱 범위 고정에만 쓰며, 미리 보기 창을 숨길 때 뒤에 있던 진행 창이 가려지지 않게 다시 올림
- 부모 변경은 창을 숨기므로, 숨김 사건 동안 재생과 소스를 건드리지 않도록 전환 플래그를 함께 세움
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import QWidget


class PreviewOwnerTracker(QObject):
    """미리 보기 창의 소유 창을 바꾸고, 소유 창이 지워질 때 부모를 풀어 창을 지키는 도우미"""

    def __init__(self, window: QWidget, set_switching: Callable[[bool], None]) -> None:
        super().__init__(window)
        self._window = window
        self._set_switching = set_switching
        self._owner: QWidget | None = None
        self._raise_suppressed: bool = False

    @property
    def owner(self) -> QWidget | None:
        """지금 소유 창, 없으면 None"""
        return self._owner

    def forget(self) -> None:
        """소유 창 기억만 지움(창 부모는 그대로)"""
        self._owner = None

    def set_raise_suppressed(self, suppressed: bool) -> None:
        """숨김이 소유 창을 앞으로 올릴지 정함. 창이 함께 닫히는 동안 올리지 않게 함"""
        self._raise_suppressed = suppressed

    def raise_owner(self) -> None:
        """미리 보기를 숨길 때 소유 창을 다시 앞으로 올림

        - 숨았거나 지웠거나 최소화한 소유 창은 올리지 않아, 보이지 않는 창이 활성화를 가로채지 않게 함
        - 앱 종료 정리처럼 창이 함께 닫히는 동안에는 올리지 않음
        """
        if self._raise_suppressed:
            return
        owner = self._owner
        if owner is None or sip.isdeleted(owner):
            return
        if not owner.isVisible() or owner.isMinimized():
            return
        owner.raise_()

    def set_owner(self, owner: QWidget | None) -> None:
        """창 소유자를 바꿔 앱 범위 고정을 적용하고 위치와 전체화면, 표시 상태를 유지

        - 표시 중이던 창이면 소유자 변경 직후 이전 위치와 전체화면 상태로 다시 표시
        - 이미 지워진 창은 소유자로 두지 않아, 부모 변경이 죽은 객체를 가리키지 않게 함
        - 활성 창이던 경우 포커스도 되돌려 새 소유자 아래에서 바로 조작할 수 있게 함
        """
        if owner is not None and sip.isdeleted(owner):
            owner = None
        window = self._window
        if window.parentWidget() is owner:
            self._track(owner)
            return
        was_visible = window.isVisible()
        was_active = window.isActiveWindow()
        geometry = window.geometry()
        fullscreen = window.isFullScreen()
        self._set_switching(True)
        try:
            window.setParent(owner, Qt.WindowType.Window)
        finally:
            self._set_switching(False)
        self._track(owner)
        if not was_visible:
            return
        if fullscreen:
            window.showFullScreen()
        else:
            window.show()
            window.setGeometry(geometry)
        if was_active:
            window.raise_()
            window.activateWindow()

    def _track(self, owner: QWidget | None) -> None:
        """소유 창을 기억하고, 소유 창이 지워질 때 부모를 미리 풀어 미리 보기 창을 지키게 함"""
        current = self._owner
        if current is owner:
            return
        if current is not None and not sip.isdeleted(current):
            current.destroyed.disconnect(self._on_owner_destroyed)
        self._owner = owner
        if owner is not None:
            owner.destroyed.connect(self._on_owner_destroyed)

    def _on_owner_destroyed(self, _owner: QObject | None = None) -> None:
        """소유 창이 지워지기 직전에 부모를 풀어, 미리 보기 창이 함께 지워지지 않게 함

        - 지워지는 동안 부모를 그대로 두면 위젯이 소유 창과 함께 지워짐
        - 부모 변경은 소유 창의 소멸자가 도는 중에도 안전하며, 위치와 표시 상태는 그대로 옮김
        """
        self._owner = None
        window = self._window
        if sip.isdeleted(window) or window.parentWidget() is None:
            return
        was_visible = window.isVisible()
        geometry = window.geometry()
        fullscreen = window.isFullScreen()
        self._set_switching(True)
        try:
            window.setParent(None, Qt.WindowType.Window)
        finally:
            self._set_switching(False)
        if not was_visible:
            return
        if fullscreen:
            window.showFullScreen()
        else:
            window.show()
            window.setGeometry(geometry)
