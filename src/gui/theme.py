from __future__ import annotations

import contextlib
import logging
import sys

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication

from src.gui.constants import get_app_settings
from src.gui.styles.icons import set_active_palette
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE, ThemePalette
from src.gui.styles.stylesheet import build_stylesheet


class ThemeManager(QObject):
    """
    - 테마 모드('system', 'dark', 'light')를 관리하고 OS 테마 변경을 감지하여 스타일시트를 갱신하는 매니저.
    """

    MODE_SYSTEM = "system"
    MODE_DARK = "dark"
    MODE_LIGHT = "light"

    theme_changed = pyqtSignal(object)

    _instance: ThemeManager | None = None

    def __new__(cls) -> ThemeManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        super().__init__()
        self._initialized = True
        self._settings = get_app_settings()
        self._mode: str = str(
            self._settings.value("theme_mode", self.MODE_SYSTEM)
        )
        if self._mode not in (self.MODE_SYSTEM, self.MODE_DARK, self.MODE_LIGHT):
            self._mode = self.MODE_SYSTEM

        self._active_palette: ThemePalette = self._resolve_palette(self._mode)
        set_active_palette(self._active_palette)
        self._hook_system_theme_listener()

    def _hook_system_theme_listener(self) -> None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            hints = app.styleHints()
            if hints is not None and hasattr(hints, "colorSchemeChanged"):
                with contextlib.suppress(RuntimeError, TypeError):
                    hints.colorSchemeChanged.connect(self._on_system_scheme_changed)

    def _on_system_scheme_changed(self) -> None:
        if self._mode == self.MODE_SYSTEM:
            new_palette = self._resolve_palette(self.MODE_SYSTEM)
            if new_palette != self._active_palette:
                self._active_palette = new_palette
                set_active_palette(self._active_palette)
                self.theme_changed.emit(self._active_palette)

    @staticmethod
    def is_os_dark_mode() -> bool:
        """
        - Qt6 styleHints 및 Windows 레지스트리를 조회하여 OS 시스템 다크 모드 활성화 여부를 판정.
        """
        app = QApplication.instance()
        if isinstance(app, QApplication):
            hints = app.styleHints()
            if hints is not None:
                scheme = hints.colorScheme()
                if scheme == Qt.ColorScheme.Dark:
                    return True
                if scheme == Qt.ColorScheme.Light:
                    return False

        if sys.platform == "win32":
            try:
                import winreg

                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                )
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return val == 0
            except OSError:
                logging.getLogger(__name__).debug("Windows 테마 레지스트리 조회 실패", exc_info=True)
        return True

    def _resolve_palette(self, mode: str) -> ThemePalette:
        if mode == self.MODE_DARK:
            return DARK_PALETTE
        if mode == self.MODE_LIGHT:
            return LIGHT_PALETTE
        return DARK_PALETTE if self.is_os_dark_mode() else LIGHT_PALETTE

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def current_palette(self) -> ThemePalette:
        return self._active_palette

    def set_mode(self, mode: str) -> None:
        """
        - 테마 모드를 변경하고 팔레트 갱신 시그널을 방출 및 설정 저장.
        """
        if mode not in (self.MODE_SYSTEM, self.MODE_DARK, self.MODE_LIGHT):
            mode = self.MODE_SYSTEM
        self._mode = mode
        self._settings.setValue("theme_mode", mode)
        self._active_palette = self._resolve_palette(mode)
        set_active_palette(self._active_palette)
        self.theme_changed.emit(self._active_palette)


class ThemeColorsProxy:
    """
    - ThemeColors.BG_BASE, ThemeColors.BADGE_OVER 등을 현재 활성 팔레트 값으로 실시간 반환하는 프록시.
    """

    def __getattr__(self, name: str) -> str | tuple[str, str, str]:
        p = ThemeManager().current_palette
        attr_key = name.lower()
        if hasattr(p, attr_key):
            return getattr(p, attr_key)
        raise AttributeError(f"'ThemeColorsProxy' object has no attribute '{name}'")


class ThemeAwareMixin:
    """
    - ThemeManager의 theme_changed 시그널을 수신하여 위젯 테마를 갱신하는 믹스인.
    """

    def init_theme_listener(self) -> None:
        mgr = ThemeManager()
        self.apply_theme(mgr.current_palette)
        mgr.theme_changed.connect(self.apply_theme)

    def apply_theme(self, palette: ThemePalette) -> None:
        pass


ThemeColors = ThemeColorsProxy()

__all__ = [
    "DARK_PALETTE",
    "LIGHT_PALETTE",
    "ThemeAwareMixin",
    "ThemeColors",
    "ThemeManager",
    "ThemePalette",
    "build_stylesheet",
]
