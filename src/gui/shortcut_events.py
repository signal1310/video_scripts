"""단축키 설정 변경을 이미 열려 있는 미리 보기 창과 화질 비교 창에 알리는 공용 신호 허브

- 단축키 설정 화면은 메인 창이 관리하지만 대상 창은 별도 최상위 창이라 컨트롤러가 직접 참조하지 않음
- 창은 만들 때 허브를 구독하고, 컨트롤러는 설정 적용이 끝나면 유효 단축키를 한 번 내보냄
"""

from __future__ import annotations

from collections.abc import Mapping

from PyQt6.QtCore import QObject, pyqtSignal


class ShortcutEvents(QObject):
    """유효 단축키 사전을 담아 내보내는 앱 공용 신호 허브"""

    changed = pyqtSignal(object)

    _instance: ShortcutEvents | None = None

    def __new__(cls) -> ShortcutEvents:
        """앱 공용 인스턴스를 한 번만 만듦"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        """싱글톤 인스턴스의 초기화를 한 번만 수행"""
        if getattr(self, "_initialized", False):
            return
        super().__init__()
        self._initialized = True

    def publish(self, shortcuts: Mapping[str, str]) -> None:
        """적용된 유효 단축키 사본을 구독한 창에 알림"""
        self.changed.emit(dict(shortcuts))


def shortcut_events() -> ShortcutEvents:
    """앱 공용 단축키 변경 허브"""
    return ShortcutEvents()
