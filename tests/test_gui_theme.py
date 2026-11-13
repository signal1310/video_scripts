import ctypes
import importlib
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QWidget

import src.gui.styles.icons as icons_mod
from src.gui.dialogs.exception_rules_dialog import ExceptionRulesDialog
from src.gui.styles.icons import build_svg_xml, get_active_palette, get_icon
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.theme import ThemeManager, apply_window_title_bar_theme, build_stylesheet

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestGUIThemeModule(unittest.TestCase):
    """ThemeManager 팔레트·QSS 전환, SVG 아이콘 동적 색칠·캐시 폴백 및 레지스트리 핸들 해제를 검증"""

    def test_theme_manager_modes_and_palette_switch(self) -> None:
        """ThemeManager의 다크(무채색), 라이트, 시스템 모드 전환 및 QSS 생성을 검증"""
        mgr = ThemeManager()
        self.addCleanup(mgr.set_mode, mgr.mode)
        # 1. 다크 모드 (무채색 계열) 검증
        mgr.set_mode(ThemeManager.MODE_DARK)
        self.assertEqual(mgr.mode, ThemeManager.MODE_DARK)
        self.assertTrue(mgr.current_palette.is_dark)
        self.assertEqual(mgr.current_palette.bg_base, "#121212")

        # 2. 라이트 모드 (고대비 깔끔한 톤) 검증
        mgr.set_mode(ThemeManager.MODE_LIGHT)
        self.assertEqual(mgr.mode, ThemeManager.MODE_LIGHT)
        self.assertFalse(mgr.current_palette.is_dark)
        self.assertEqual(mgr.current_palette.bg_base, "#F5F6F8")

        # 3. QSS 스타일시트 렌더링 무결성 검증
        qss_dark = build_stylesheet(DARK_PALETTE)
        self.assertIn("#121212", qss_dark)
        self.assertIn("QMenuBar", qss_dark)
        self.assertIn("QListWidget#ExceptionRuleList", qss_dark)
        qss_light = build_stylesheet(LIGHT_PALETTE)
        self.assertIn("#F5F6F8", qss_light)
        self.assertIn("QMenuBar", qss_light)
        self.assertIn("QListWidget#ExceptionRuleList", qss_light)

        # 3-1. 예외 규칙 편집기 다크/라이트 테마 적용 검증
        rules_dlg = ExceptionRulesDialog(["sample"], ["disabled_tag"])
        self.addCleanup(rules_dlg.close)
        rules_dlg.apply_theme(LIGHT_PALETTE)
        self.assertIn("#F5F6F8", rules_dlg.styleSheet())
        rules_dlg.apply_theme(DARK_PALETTE)
        self.assertIn("#121212", rules_dlg.styleSheet())

        # 4. 시스템 적응형 모드 설정 검증
        mgr.set_mode(ThemeManager.MODE_SYSTEM)
        self.assertEqual(mgr.mode, ThemeManager.MODE_SYSTEM)
        self.assertIn(mgr.current_palette, (DARK_PALETTE, LIGHT_PALETTE))

    def test_svg_icon_rendering(self) -> None:
        """SVG 템플릿으로부터 QIcon 인스턴스가 인메모리에서 정상 생성되고 유효한 픽스맵을 갖는지 검증"""
        svg_xml = build_svg_xml("settings", color="#2563EB")
        self.assertIn("<svg", svg_xml)
        self.assertIn("#2563EB", svg_xml)

        icon = get_icon("settings", size=16)
        self.assertFalse(icon.isNull())
        pixmap = icon.pixmap(16, 16)
        self.assertEqual(pixmap.width(), 16)
        self.assertEqual(pixmap.height(), 16)

    def test_icon_theme_adaptation_and_contrast(self) -> None:
        """라이트/다크 테마에 따라 get_icon 기본 색상이 고대비로 자동 전환되는지 검증"""
        mgr = ThemeManager()
        self.addCleanup(mgr.set_mode, mgr.mode)

        # 라이트 모드 강제 적용
        mgr.set_mode(ThemeManager.MODE_LIGHT)
        self.assertEqual(get_active_palette().name, "light")
        icon_light = get_icon("folder", size=16)
        img_l = icon_light.pixmap(16, 16).toImage()
        # 불투명 픽셀 색상 수집 (라이트 모드는 진한 차콜 톤이어야 함)
        cols_l = {
            img_l.pixelColor(x, y).name()
            for x in range(16)
            for y in range(16)
            if img_l.pixelColor(x, y).alpha() > 100
        }
        self.assertTrue(any(c.startswith("#0e") or c.startswith("#0f") for c in cols_l))

        # 다크 모드로 전환
        mgr.set_mode(ThemeManager.MODE_DARK)
        self.assertEqual(get_active_palette().name, "dark")
        icon_dark = get_icon("folder", size=16)
        img_d = icon_dark.pixmap(16, 16).toImage()
        cols_d = {
            img_d.pixelColor(x, y).name()
            for x in range(16)
            for y in range(16)
            if img_d.pixelColor(x, y).alpha() > 100
        }
        self.assertTrue(any(c.startswith("#ed") or c.startswith("#ee") for c in cols_d))

    def test_icons_no_import_disk_write_and_cache_fallback(self) -> None:
        """icons 모듈 임포트 시 소스 트리에 파일 쓰기를 하지 않고 누락 시 캐시 폴더로 우회 생성하는지 검증"""
        with (
            patch("os.makedirs") as mock_makedirs,
            patch("builtins.open") as mock_open,
        ):
            importlib.reload(icons_mod)
            mock_makedirs.assert_not_called()
            mock_open.assert_not_called()

        with tempfile.TemporaryDirectory() as tmpdir:
            missing_assets = os.path.join(tmpdir, "readonly_missing")
            cache_dir = os.path.join(tmpdir, "user_cache")
            with (
                patch.object(icons_mod, "ASSETS_DIR", missing_assets),
                patch.object(icons_mod, "_get_icon_cache_dir", return_value=cache_dir),
            ):
                resolved = icons_mod.resolve_qss_icon_path("chevron-up", DARK_PALETTE)
                self.assertTrue(resolved.startswith(cache_dir.replace("\\", "/")))
                self.assertTrue(os.path.isfile(resolved))
                self.assertFalse(os.path.exists(missing_assets))

    def test_theme_manager_closes_windows_registry_key(self) -> None:
        """OS 다크 모드 레지스트리 조회 시 HKEY 핸들을 컨텍스트 매니저로 즉시 닫는지 검증"""
        mock_key = MagicMock()
        mock_key.__enter__.return_value = mock_key
        mock_key.__exit__.return_value = False
        mock_winreg = SimpleNamespace(
            HKEY_CURRENT_USER=object(),
            OpenKey=MagicMock(return_value=mock_key),
            QueryValueEx=MagicMock(return_value=(0, 4)),
        )

        with (
            patch.object(sys, "platform", "win32"),
            patch.dict(sys.modules, {"winreg": mock_winreg}),
            patch("src.gui.theme.QApplication.instance", return_value=None),
        ):
            self.assertTrue(ThemeManager.is_os_dark_mode())

        mock_key.__enter__.assert_called_once()
        mock_key.__exit__.assert_called_once()

    def test_windows_title_bar_theme_sync_and_fallback(self) -> None:
        """Windows DWM 제목 표시줄 테마 동기화, Win10 속성 폴백 및 offscreen 안전 차단을 검증"""
        win = QWidget()
        self.addCleanup(win.close)

        # 창 핸들 생성 전 / offscreen 환경에서는 안전하게 False 반환
        self.assertFalse(apply_window_title_bar_theme(win, is_dark=True))
        win.show()
        _APP.processEvents()
        self.assertFalse(apply_window_title_bar_theme(win, is_dark=True))

        recorded: list[tuple[int, int]] = []

        def _make_dwm_stub(*, fail_attr: int | None = None) -> Any:
            def _stub(
                _hwnd: Any, attr: ctypes.c_uint, val_ptr: Any, _size: Any
            ) -> int:
                val = ctypes.cast(val_ptr, ctypes.POINTER(ctypes.c_int)).contents.value
                recorded.append((attr.value, val))
                return 1 if fail_attr is not None and attr.value == fail_attr else 0

            return _stub

        fake_dwmapi = SimpleNamespace(
            DwmSetWindowAttribute=MagicMock(side_effect=_make_dwm_stub())
        )
        fake_user32 = SimpleNamespace(SetWindowPos=MagicMock())
        fake_windll = SimpleNamespace(dwmapi=fake_dwmapi, user32=fake_user32)

        with (
            patch.object(sys, "platform", "win32"),
            patch.object(_APP, "platformName", return_value="windows"),
            patch("src.gui.theme.ctypes.windll", fake_windll, create=True),
        ):
            self.assertTrue(apply_window_title_bar_theme(win, is_dark=True))
            self.assertEqual(recorded[-1], (20, 1))
            fake_user32.SetWindowPos.assert_called()

            # ThemeManager 모드 전환 시 열려 있는 최상위 창 제목 표시줄까지 동기화
            mgr = ThemeManager()
            prev_mode = mgr.mode
            self.addCleanup(mgr.set_mode, prev_mode)
            mgr.set_mode(ThemeManager.MODE_LIGHT)
            self.assertIn((20, 0), recorded)

            # Win10 구형 빌드(attr=20 실패) -> attr=19 폴백 검증
            recorded.clear()
            fake_dwmapi.DwmSetWindowAttribute.side_effect = _make_dwm_stub(fail_attr=20)
            self.assertTrue(apply_window_title_bar_theme(win, is_dark=True))
            self.assertEqual(recorded, [(20, 1), (19, 1)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
