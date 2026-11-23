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
from src.gui.preview.preview_keys import clamp_seek_position, is_close_key
from src.gui.preview.preview_settings_panel import PreviewSettingsPanel, PreviewSettingsPopup
from src.gui.preview.video_preview_overlay import PreviewInfoOverlay, PreviewToast
from src.gui.preview.video_preview_window import VideoPreviewWindow
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

    def test_buffered_media_applies_the_pending_position_once(self) -> None:
        """로컬 파일의 BufferedMedia에서도 대기 시점을 한 번만 적용하는지 검증"""
        self.window._pending_position_ms = 9000
        with mock.patch.object(cast("object", self.window._player), "setPosition") as set_position:
            self.window._on_media_status_changed(QMediaPlayer.MediaStatus.BufferedMedia)
        set_position.assert_called_once_with(9000)
        self.assertIsNone(self.window._pending_position_ms)

    def test_fast_load_during_show_path_at_consumes_the_pending_position(self) -> None:
        """영상을 읽는 도중 상태 신호가 와도 지정 시점으로 한 번만 옮기는지 검증"""
        original_show = self.window.show_path

        def show_then_loaded(path: str, *, auto_play: bool = True) -> None:
            original_show(path, auto_play=auto_play)
            self.window._on_media_status_changed(QMediaPlayer.MediaStatus.BufferedMedia)

        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            with (
                mock.patch.object(self.window, "show_path", side_effect=show_then_loaded),
                mock.patch.object(
                    cast("object", self.window._player), "setPosition"
                ) as set_position,
            ):
                self.window.show_path_at(path, 5.0)
        self.assertEqual([args.args[0] for args in set_position.call_args_list], [5000])
        self.assertIsNone(self.window._pending_position_ms)

    def test_position_sec_reads_the_player_position(self) -> None:
        with mock.patch.object(cast("object", self.window._player), "position", return_value=2500):
            self.assertEqual(self.window.position_sec, 2.5)

    def test_compare_button_tooltip_mentions_the_toggle_shortcut(self) -> None:
        """화질 비교 버튼 툴팁에 전환 단축키 표기가 붙는지 검증"""
        self.assertIn("Shift+Space", self.window._controls.compare_button.toolTip())


class PlaybackSyncTests(GuiTestCase):
    """저장된 재생 옵션 재적용, 재생 상태 읽기, 일시중지 상태로 열기"""

    def setUp(self) -> None:
        self.window = _make_window()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.hide)

    def test_sync_playback_settings_reapplies_saved_values(self) -> None:
        """창을 다시 열 때 저장된 음량, 음소거, 배속을 조작 바와 재생기에 반영하는지 검증"""
        state = UiState()
        state.preview_volume = 35
        state.preview_muted = True
        state.preview_playback_rate = 2.5
        window = VideoPreviewWindow(
            lambda: state.preview_seek_seconds,
            lambda: (state.preview_volume, state.preview_muted),
            lambda _volume, _muted: None,
            lambda: DEFAULT_SHORTCUTS,
            lambda _text: False,
            lambda: state.preview_info,
            lambda: None,
            lambda: state.preview_playback_rate,
            lambda _rate: None,
            lambda: state.preview_fullscreen,
            lambda _fullscreen: None,
        )
        self.addCleanup(window.deleteLater)
        window.sync_playback_settings()
        self.assertEqual(window._controls.audio_state(), (35, True))
        self.assertAlmostEqual(window._player.playbackRate(), 2.5)

    def test_is_playing_reads_the_player_state(self) -> None:
        """재생 상태를 비교 창으로 넘길 수 있도록 재생기 상태를 그대로 읽는지 검증"""
        with mock.patch.object(
            cast("object", self.window._player),
            "playbackState",
            return_value=QMediaPlayer.PlaybackState.PlayingState,
        ):
            self.assertTrue(self.window.is_playing)
        with mock.patch.object(
            cast("object", self.window._player),
            "playbackState",
            return_value=QMediaPlayer.PlaybackState.PausedState,
        ):
            self.assertFalse(self.window.is_playing)

    def test_show_path_at_can_stay_paused(self) -> None:
        """일시중지 상태로 돌아올 때 재생을 시작하지 않고 시점만 맞추는지 검증"""
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            with (
                mock.patch.object(cast("object", self.window._player), "play") as play,
                mock.patch.object(cast("object", self.window._player), "pause") as pause,
            ):
                self.window.show_path_at(path, 7.5, auto_play=False)
        play.assert_not_called()
        self.assertTrue(pause.called)
        self.assertEqual(self.window._pending_position_ms, 7500)


class FrameStepTests(GuiTestCase):
    """미리 보기 1프레임 이동과 조작 바 단축키 툴팁"""

    def setUp(self) -> None:
        self.window = _make_window()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.hide)
        self.window.show()
        _APP.processEvents()

    def test_step_frame_pauses_and_moves_by_one_frame(self) -> None:
        """재생 중이면 멈추고 10fps 기준 100ms만큼 옮기는지 검증"""
        player = cast("object", self.window._player)
        with (
            mock.patch.object(self.window, "_frame_rate", return_value=10.0),
            mock.patch.object(
                player, "playbackState", return_value=QMediaPlayer.PlaybackState.PlayingState
            ),
            mock.patch.object(player, "position", return_value=5000),
            mock.patch.object(player, "duration", return_value=10000),
            mock.patch.object(player, "pause") as pause,
            mock.patch.object(player, "setPosition") as set_position,
        ):
            self.window._step_frame(1)
            self.window._step_frame(-1)
        self.assertEqual(pause.call_count, 2)
        self.assertEqual([call.args[0] for call in set_position.call_args_list], [5100, 4900])

    def test_frame_rate_falls_back_to_metadata_then_default(self) -> None:
        self.window._video_info = None
        metadata = mock.Mock()
        metadata.value.return_value = 24.0
        player = cast("object", self.window._player)
        with mock.patch.object(player, "metaData", return_value=metadata):
            self.assertEqual(self.window._frame_rate(), 24.0)
        metadata.value.return_value = None
        with mock.patch.object(player, "metaData", return_value=metadata):
            self.assertEqual(self.window._frame_rate(), 30.0)

    def test_sync_shortcuts_updates_control_tooltips(self) -> None:
        """단축키 변경이 재생 버튼 툴팁에 반영되는지 검증"""
        shortcuts = dict(DEFAULT_SHORTCUTS)
        shortcuts["preview.toggle_playback"] = "X"
        self.window.sync_shortcuts(shortcuts)
        self.assertIn("[X]", self.window._controls._btn_toggle.toolTip())

    def test_hide_closes_the_open_shortcut_guide(self) -> None:
        """창을 숨길 때 열린 단축키 안내와 대기 타이머를 정리하는지 검증"""
        guide = self.window._controls.guide_button
        guide.open_guide()
        _APP.processEvents()
        self.assertTrue(guide.is_guide_open())
        self.window.hide()
        _APP.processEvents()
        self.assertFalse(guide.is_guide_open())
        self.assertFalse(guide._open_timer.isActive())
        self.assertFalse(guide._hide_timer.isActive())

    def test_guide_button_is_the_last_control(self) -> None:
        guide = self.window._controls.guide_button
        self.assertEqual(guide.accessibleName(), "단축키 안내")
        self.assertTrue(guide.isVisible())


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

    def test_toast_frame_uses_the_dark_hud_tokens(self) -> None:
        """단축키 알림 배경이 정보 표시보다 진한 HUD 토큰과 테두리를 쓰는지 검증"""
        for palette in (LIGHT_PALETTE, DARK_PALETTE):
            sheet = build_preview_qss(palette)
            block = sheet.split("QFrame#PreviewToastFrame {", 1)[1].split("}", 1)[0]
            self.assertIn(f"background-color: {palette.media_hud_bg};", block)
            self.assertIn(f"border: 1px solid {palette.media_track};", block)
            overlay = sheet.split("QFrame#PreviewInfoOverlayFrame {", 1)[1].split("}", 1)[0]
            self.assertIn(f"background-color: {palette.media_overlay_bg};", overlay)


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
