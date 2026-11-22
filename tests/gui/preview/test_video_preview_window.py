"""내장 미리 보기 창 검증: 화질 비교 요청의 경로와 시점 전달, 볼륨 저장 연결, 구간 제한, 시점 이어받기"""

import os
import tempfile
import unittest
from typing import cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.core.settings.ui_state import UiState
from src.gui.preview.preview_settings_panel import PreviewSettingsPanel, PreviewSettingsPopup
from src.gui.preview.video_preview_overlay import PreviewInfoOverlay, PreviewToast
from src.gui.preview.video_preview_window import VideoPreviewWindow, clamp_seek_position, is_close_key
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.styles.qss_preview import build_preview_qss
from src.gui.theme import ThemeManager
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _make_window(saved: list[tuple[int, bool]] | None = None) -> VideoPreviewWindow:
    state = UiState()
    saved = saved if saved is not None else []

    def set_audio(volume: int, muted: bool) -> None:
        saved.append((volume, muted))

    return VideoPreviewWindow(
        lambda: state.preview_seek_seconds,
        lambda: (state.preview_volume, state.preview_muted),
        set_audio,
        lambda: DEFAULT_SHORTCUTS,
        lambda _text: False,
        lambda: state.preview_info,
        lambda: None,
        lambda: state.preview_playback_rate,
        lambda _rate: None,
        lambda: state.preview_fullscreen,
        lambda _fullscreen: None,
    )


class CompareRequestTests(GuiTestCase):
    """미리 보기 조작 바의 화질 비교 요청"""

    def setUp(self) -> None:
        self.saved: list[tuple[int, bool]] = []
        self.window = _make_window(self.saved)
        self.addCleanup(self.window.deleteLater)
        self.window.show()
        _APP.processEvents()

    def test_current_path_and_position_are_forwarded(self) -> None:
        requests: list[tuple[str, float]] = []
        self.window.compare_requested.connect(lambda path, pos: requests.append((path, pos)))
        self.window._path = "C:/videos/a.mp4"
        with mock.patch.object(cast("object", self.window._player), "position", return_value=1500):
            self.window._controls.compare_button.click()
        self.assertEqual(requests, [("C:/videos/a.mp4", 1.5)])

    def test_no_path_means_no_request(self) -> None:
        requests: list[tuple[str, float]] = []
        self.window.compare_requested.connect(lambda path, pos: requests.append((path, pos)))
        self.window._path = ""
        self.window._controls.compare_button.click()
        self.assertEqual(requests, [])

    def test_volume_change_is_saved_through_the_audio_callback(self) -> None:
        self.window._controls._slider_volume.setValue(42)
        self.window._on_volume_changed(42)
        self.assertEqual(self.saved[-1], (42, False))


class ClampSeekTests(unittest.TestCase):
    """구간 이동 위치 제한"""

    def test_clamp_keeps_position_inside_duration(self) -> None:
        self.assertEqual(clamp_seek_position(1000, 500, 3000), 1500)
        self.assertEqual(clamp_seek_position(1000, -5000, 3000), 0)
        self.assertEqual(clamp_seek_position(1000, 5000, 3000), 3000)
        self.assertEqual(clamp_seek_position(1000, 500, 0), 1000)

    def test_shift_space_is_not_a_close_key(self) -> None:
        """Shift+Space는 창을 닫지 않고 화질 비교 전환으로 넘기는지 검증"""
        self.assertTrue(is_close_key(Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
        self.assertTrue(is_close_key(Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        self.assertFalse(is_close_key(Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier))
        self.assertFalse(is_close_key(Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier))


class ShowPathAtTests(GuiTestCase):
    """화질 비교 창에서 돌아올 때 시점과 창 영역을 이어받는지"""

    def setUp(self) -> None:
        self.window = _make_window()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.hide)

    def test_show_path_at_keeps_the_requested_time_and_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            self.window.show_path_at(path, 12.5, QRect(30, 40, 640, 360))
        self.assertEqual(self.window.geometry(), QRect(30, 40, 640, 360))
        self.assertEqual(self.window._pending_position_ms, 12500)
        self.assertEqual(self.window.current_path, path)

    def test_show_path_at_does_not_override_the_fullscreen_geometry(self) -> None:
        """전체화면에서 돌아올 때 넘어온 창 영역으로 전체화면 크기를 덮어쓰지 않는지 검증"""
        with (
            mock.patch.object(self.window, "isFullScreen", return_value=True),
            mock.patch.object(self.window, "setGeometry") as set_geometry,
            tempfile.TemporaryDirectory() as root,
        ):
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            self.window.show_path_at(path, 5.0, QRect(30, 40, 640, 360))
        set_geometry.assert_not_called()
        self.assertEqual(self.window._pending_position_ms, 5000)


    def test_loaded_media_applies_the_pending_position_once(self) -> None:
        self.window._pending_position_ms = 9000
        with mock.patch.object(cast("object", self.window._player), "setPosition") as set_position:
            self.window._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        self.assertEqual(set_position.call_args.args[0], 9000)
        self.assertIsNone(self.window._pending_position_ms)

    def test_position_sec_reads_the_player_position(self) -> None:
        with mock.patch.object(cast("object", self.window._player), "position", return_value=2500):
            self.assertEqual(self.window.position_sec, 2.5)

    def test_compare_button_tooltip_mentions_the_toggle_shortcut(self) -> None:
        """화질 비교 버튼 툴팁에 전환 단축키 표기가 붙는지 검증"""
        self.assertIn("Shift+Space", self.window._controls.compare_button.toolTip())


class PreviewWindowThemeTests(GuiTestCase):
    """독립 창이 된 미리 보기 창이 앱 테마 QSS를 직접 받는지"""

    def setUp(self) -> None:
        self.window = _make_window()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.hide)

    def test_independent_window_applies_the_control_bar_and_background_qss(self) -> None:
        """조작 바 어두운 배경과 창 배경, 슬라이더, 배속 메뉴 규칙이 테마별로 적용되는지 검증"""
        self.window.apply_theme(LIGHT_PALETTE)
        light_sheet = self.window.styleSheet()
        self.assertIn(f"QWidget#PreviewControlBar {{\n    background-color: {LIGHT_PALETTE.media_bg};", light_sheet)
        self.assertIn(f"VideoPreviewWindow {{ background-color: {LIGHT_PALETTE.media_bg}; }}", light_sheet)
        self.assertIn("QSlider#PreviewPositionSlider::groove:horizontal", light_sheet)
        self.assertIn("QToolButton#PreviewRateButton::menu-indicator", light_sheet)
        self.assertIn(LIGHT_PALETTE.bg_input, light_sheet)

        self.window.apply_theme(DARK_PALETTE)
        dark_sheet = self.window.styleSheet()
        self.assertIn(f"VideoPreviewWindow {{ background-color: {DARK_PALETTE.media_bg}; }}", dark_sheet)
        self.assertIn(DARK_PALETTE.bg_base, dark_sheet)
        self.assertNotIn(LIGHT_PALETTE.bg_base, dark_sheet)

    def test_theme_signal_updates_the_independent_window(self) -> None:
        """테마 변경 신호를 받아 스타일시트를 다시 적용하는지 검증"""
        ThemeManager().theme_changed.emit(DARK_PALETTE)
        self.assertIn(DARK_PALETTE.bg_base, self.window.styleSheet())
        self.assertIn("QWidget#PreviewControlBar", self.window.styleSheet())


class PreviewWindowStackingTests(GuiTestCase):
    """미리 보기 창과 그 위에 겹치는 오버레이, 설정 팝업이 항상 맨 위에 남는지"""

    def setUp(self) -> None:
        self.window = _make_window()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.hide)

    def test_window_and_overlays_keep_the_stays_on_top_flag(self) -> None:
        """미리 보기 창을 독립 최상위 창으로 두되 항상 맨 위로 유지하고, 정보/토스트도 같은 플래그를 쓰는지 검증"""
        flags = self.window.windowFlags()
        self.assertEqual(flags & Qt.WindowType.WindowType_Mask, Qt.WindowType.Window)
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnTopHint)
        overlays = [*self.window.findChildren(PreviewInfoOverlay), *self.window.findChildren(PreviewToast)]
        self.assertEqual(len(overlays), 2)
        for overlay in overlays:
            self.assertTrue(overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_settings_popup_opened_from_the_window_keeps_the_stays_on_top_flag(self) -> None:
        """미리 보기 창을 부모로 여는 설정 팝업이 창 뒤로 가려지지 않도록 같은 플래그를 쓰는지 검증"""
        popup = PreviewSettingsPopup(PreviewSettingsPanel(lambda: UiState(), ClassifierConfig), self.window)
        self.addCleanup(popup.deleteLater)
        self.assertTrue(popup.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_compare_button_uses_the_transparent_tool_button_rule(self) -> None:
        """화질 비교 버튼에 투명 배경 도구 버튼 규칙이 있어 네이티브 회색 바탕이 깔리지 않는지 검증"""
        sheet = build_preview_qss(LIGHT_PALETTE)
        self.assertIn("QToolButton#PreviewCompareButton {", sheet)
        compare_rule = sheet.split("QToolButton#PreviewCompareButton {", 1)[1].split("}", 1)[0]
        self.assertIn("background-color: transparent;", compare_rule)
        self.assertIn("border: none;", compare_rule)
        self.assertIn("QToolButton#PreviewCompareButton:hover", sheet)
        self.assertIn("QToolButton#PreviewCompareButton:pressed", sheet)
        self.assertIn("QToolButton#PreviewCompareButton", self.window.styleSheet())


if __name__ == "__main__":
    unittest.main(verbosity=2)
