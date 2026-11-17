"""애플리케이션 라이트, 다크 테마 전환, OS 테마 감지 및 믹스인 관리 모듈"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import sys

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.app_state import load_app_state
from src.core.ui_state import DEFAULT_THEME_MODE, VALID_THEME_MODES
from src.gui.styles.icons import set_active_palette
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE, ThemePalette
from src.gui.styles.stylesheet import build_stylesheet

_DWMWA_USE_IMMERSIVE_DARK_MODE: int = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1: int = 19
_SWP_NOMOVE: int = 0x0002
_SWP_NOSIZE: int = 0x0001
_SWP_NOZORDER: int = 0x0004
_SWP_NOACTIVATE: int = 0x0010
_SWP_FRAMECHANGED: int = 0x0020


def apply_window_title_bar_theme(widget: QWidget, is_dark: bool) -> bool:
    """Windows DWM API로 최상위 창 제목 표시줄의 다크, 라이트 테마를 동기화"""
    if sys.platform != "win32" or not widget.isWindow():
        return False
    if not widget.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
        return False
    app = QApplication.instance()
    if isinstance(app, QApplication) and app.platformName() == "offscreen":
        return False

    try:
        hwnd = int(widget.winId())
    except (TypeError, ValueError, RuntimeError):
        return False
    if hwnd <= 0:
        return False

    try:
        dwmapi = ctypes.windll.dwmapi
    except (AttributeError, OSError):
        return False

    value = ctypes.c_int(1 if is_dark else 0)
    val_size = ctypes.sizeof(value)
    applied = False
    for attr in (
        _DWMWA_USE_IMMERSIVE_DARK_MODE,
        _DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1,
    ):
        try:
            res = int(
                dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd),
                    ctypes.c_uint(attr),
                    ctypes.byref(value),
                    ctypes.c_uint(val_size),
                )
            )
        except (AttributeError, OSError, ctypes.ArgumentError):
            return False
        if res == 0:
            applied = True
            break

    if applied and widget.isVisible():
        with contextlib.suppress(AttributeError, OSError, ctypes.ArgumentError):
            flags = (
                _SWP_NOMOVE
                | _SWP_NOSIZE
                | _SWP_NOZORDER
                | _SWP_NOACTIVATE
                | _SWP_FRAMECHANGED
            )
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, flags
            )
    return applied


def build_qpalette(p: ThemePalette) -> QPalette:
    """테마 토큰으로 네이티브 위젯 팔레트를 만들어 OS 색상 모드와 무관하게 앱 테마를 따르게 함

    - QSS 규칙이 없는 위젯(메시지 상자, 파일 대화상자, 일부 목록)은 이 팔레트로 그려짐
    """
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: p.bg_base,
        QPalette.ColorRole.WindowText: p.text_primary,
        QPalette.ColorRole.Base: p.bg_input,
        QPalette.ColorRole.AlternateBase: p.bg_table_alt,
        QPalette.ColorRole.Text: p.text_primary,
        QPalette.ColorRole.Button: p.btn_bg,
        QPalette.ColorRole.ButtonText: p.text_primary,
        QPalette.ColorRole.Highlight: p.bg_selected,
        QPalette.ColorRole.HighlightedText: p.text_primary,
        QPalette.ColorRole.ToolTipBase: p.bg_card,
        QPalette.ColorRole.ToolTipText: p.text_primary,
        QPalette.ColorRole.PlaceholderText: p.text_muted,
        QPalette.ColorRole.Link: p.accent_blue,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.text_muted))
    return palette


class ThemeManager(QObject):
    """테마 모드('system', 'dark', 'light')를 관리하고 OS 테마 변경을 감지하여 스타일시트를 갱신하는 매니저"""

    MODE_SYSTEM = DEFAULT_THEME_MODE
    MODE_DARK = "dark"
    MODE_LIGHT = "light"

    theme_changed = pyqtSignal(object)

    _instance: ThemeManager | None = None

    def __new__(cls) -> ThemeManager:
        """싱글톤 ThemeManager 인스턴스 생성 및 반환"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        super().__init__()
        self._initialized = True
        try:
            self._mode: str = load_app_state(create_missing=False).ui.theme_mode
        except Exception:
            logging.getLogger(__name__).debug("초기 테마 상태 로드 생략", exc_info=True)
            self._mode = self.MODE_SYSTEM
        if self._mode not in VALID_THEME_MODES:
            self._mode = self.MODE_SYSTEM

        self._active_palette: ThemePalette = self._resolve_palette(self._mode)
        self._event_filter_installed: bool = False
        set_active_palette(self._active_palette)
        self._apply_native_palette()
        self._hook_system_theme_listener()
        self._hook_app_event_filter()

    def _apply_native_palette(self) -> None:
        """앱 전체 네이티브 팔레트를 활성 테마로 맞춤, OS가 다크여도 라이트 테마면 밝게 그려짐"""
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setPalette(build_qpalette(self._active_palette))

    def _hook_app_event_filter(self) -> None:
        if self._event_filter_installed:
            return
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.installEventFilter(self)
            self._event_filter_installed = True

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """최상위 창이 표시될 때 Windows 제목 표시줄 테마를 자동 동기화"""
        if (
            a1 is not None
            and a1.type() == QEvent.Type.Show
            and isinstance(a0, QWidget)
            and a0.isWindow()
        ):
            apply_window_title_bar_theme(a0, self._active_palette.is_dark)
        return super().eventFilter(a0, a1)

    def _sync_all_title_bars(self) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        is_dark = self._active_palette.is_dark
        for widget in app.topLevelWidgets():
            if widget.isWindow():
                apply_window_title_bar_theme(widget, is_dark)

    def _hook_system_theme_listener(self) -> None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            hints = app.styleHints()
            if hints is not None and hasattr(hints, "colorSchemeChanged"):
                with contextlib.suppress(RuntimeError, TypeError):
                    hints.colorSchemeChanged.connect(self._on_system_scheme_changed)

    def _on_system_scheme_changed(self) -> None:
        # - Qt가 OS 색상 모드 변경에 맞춰 네이티브 팔레트를 다시 쓰므로 고정 테마에서도 앱 팔레트를 되돌려 놓음
        self._apply_native_palette()
        if self._mode == self.MODE_SYSTEM:
            new_palette = self._resolve_palette(self.MODE_SYSTEM)
            if new_palette != self._active_palette:
                self._active_palette = new_palette
                set_active_palette(self._active_palette)
                self._apply_native_palette()
                self._sync_all_title_bars()
                self.theme_changed.emit(self._active_palette)

    @staticmethod
    def is_os_dark_mode() -> bool:
        """Qt6 styleHints 및 Windows 레지스트리를 조회하여 OS 시스템 다크 모드 활성화 여부를 판정"""
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

                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                ) as key:
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
        """현재 적용된 테마 모드('system', 'dark', 'light') 반환"""
        return self._mode

    @property
    def current_palette(self) -> ThemePalette:
        """현재 활성 ThemePalette 객체 반환"""
        return self._active_palette

    def set_mode(self, mode: str) -> None:
        """테마 모드를 변경하고 팔레트 갱신 시그널을 방출"""
        if mode not in VALID_THEME_MODES:
            mode = self.MODE_SYSTEM
        self._mode = mode
        self._active_palette = self._resolve_palette(mode)
        self._hook_app_event_filter()
        set_active_palette(self._active_palette)
        self._apply_native_palette()
        self._sync_all_title_bars()
        self.theme_changed.emit(self._active_palette)


class ThemeAwareMixin:
    """ThemeManager의 theme_changed 시그널을 수신하여 위젯 테마를 갱신하는 믹스인"""

    def init_theme_listener(self) -> None:
        """현재 테마 즉시 적용 및 theme_changed 시그널 구독 등록"""
        mgr = ThemeManager()
        self.apply_theme(mgr.current_palette)
        mgr.theme_changed.connect(self.apply_theme)

    def apply_theme(self, palette: ThemePalette) -> None:
        """전달된 팔레트를 위젯 스타일에 반영 (하위 클래스 재정의용 훅)"""
        pass


__all__ = [
    "DARK_PALETTE",
    "LIGHT_PALETTE",
    "ThemeAwareMixin",
    "ThemeManager",
    "ThemePalette",
    "apply_window_title_bar_theme",
    "build_qpalette",
    "build_stylesheet",
]
