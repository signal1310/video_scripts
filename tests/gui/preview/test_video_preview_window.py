"""내장 미리 보기 창 검증: 화질 비교 요청의 경로와 시점 전달, 볼륨 저장 연결, 구간 제한, 시점 이어받기"""

import os
import tempfile
import threading
import unittest
from typing import cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QRect, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QApplication, QLabel, QToolButton, QWidget

from src.core.analysis.frame_compare import ComparePlan, FrameSource
from src.core.config import ClassifierConfig
from src.core.settings.preview_info import INFO_MODE_SUMMARY
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.core.settings.ui_state import UiState
from src.gui.dialogs.frame_compare_meta import CompareSession
from src.gui.preview.preview_controller import PreviewPositionMemory
from src.gui.preview.preview_keys import clamp_seek_position, is_close_key, remove_key_filter
from src.gui.preview.preview_settings_panel import PreviewSettingsPanel, PreviewSettingsPopup
from src.gui.preview.video_preview_overlay import PreviewInfoOverlay, PreviewToast
from src.gui.preview.video_preview_window import VideoPreviewWindow
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.styles.qss_preview import build_preview_qss
from src.gui.theme import ThemeManager
from src.service.frame_compare_service import ExtractedFrames
from tests._gui_helpers import GuiTestCase, release_modifier_keys

_APP = QApplication.instance() or QApplication([])


def _press(
    widget: VideoPreviewWindow,
    key: Qt.Key,
    modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    """위젯에 키 누름 사건을 보내 창의 키 처리만 확인"""
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, modifier, ""))
    if modifier != Qt.KeyboardModifier.NoModifier:
        release_modifier_keys()


def _make_window(
    saved: list[tuple[int, bool]] | None = None,
    positions: PreviewPositionMemory | None = None,
    ui: UiState | None = None,
) -> VideoPreviewWindow:
    state = ui if ui is not None else UiState()
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
        lambda: state.preview_always_on_top,
        lambda _pinned: None,
        positions if positions is not None else PreviewPositionMemory(),
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
        _APP.processEvents()
        self.assertEqual(self.saved[-1], (42, False))


class ClampSeekTests(unittest.TestCase):
    """구간 이동 위치 제한"""

    def test_clamp_keeps_position_inside_duration(self) -> None:
        self.assertEqual(clamp_seek_position(1000, 500, 3000), 1500)
        self.assertEqual(clamp_seek_position(1000, -5000, 3000), 0)
        self.assertEqual(clamp_seek_position(1000, 5000, 3000), 3000)
        self.assertEqual(clamp_seek_position(1000, 500, 0), 1000)

    def test_shift_space_is_not_a_close_key(self) -> None:
        """Shift를 누른 Space는 창을 닫는 키로 보지 않는지 검증"""
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
        self.assertEqual(self.window._playback._pending_position_ms, 12500)
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
        self.assertEqual(self.window._playback._pending_position_ms, 5000)


    def test_loaded_media_applies_the_pending_position_once(self) -> None:
        self.window._playback._pending_position_ms = 9000
        with mock.patch.object(cast("object", self.window._player), "setPosition") as set_position:
            self.window._playback._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        self.assertEqual(set_position.call_args.args[0], 9000)
        self.assertIsNone(self.window._playback._pending_position_ms)

    def test_buffered_media_applies_the_pending_position_once(self) -> None:
        """로컬 파일의 BufferedMedia에서도 대기 시점을 한 번만 적용하는지 검증"""
        self.window._playback._pending_position_ms = 9000
        with mock.patch.object(cast("object", self.window._player), "setPosition") as set_position:
            self.window._playback._on_media_status_changed(QMediaPlayer.MediaStatus.BufferedMedia)
        set_position.assert_called_once_with(9000)
        self.assertIsNone(self.window._playback._pending_position_ms)

    def test_fast_load_during_show_path_at_consumes_the_pending_position(self) -> None:
        """영상을 읽는 도중 상태 신호가 와도 지정 시점으로 한 번만 옮기는지 검증"""
        original_show = self.window.show_path

        def show_then_loaded(path: str, *, auto_play: bool = True) -> None:
            original_show(path, auto_play=auto_play)
            self.window._playback._on_media_status_changed(QMediaPlayer.MediaStatus.BufferedMedia)

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
        self.assertIsNone(self.window._playback._pending_position_ms)

    def test_position_sec_reads_the_player_position(self) -> None:
        with mock.patch.object(cast("object", self.window._player), "position", return_value=2500):
            self.assertEqual(self.window.position_sec, 2.5)

    def test_compare_button_tooltip_mentions_the_toggle_shortcut(self) -> None:
        """화질 비교 버튼 툴팁에 전환 단축키 표기가 붙는지 검증"""
        self.assertIn("Ctrl+B", self.window._controls.compare_button.toolTip())


class PlaybackSyncTests(GuiTestCase):
    """저장된 재생 옵션 재적용, 재생 상태 읽기, 일시중지 상태로 열기"""

    def setUp(self) -> None:
        self.window = _make_window()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.hide)

    def test_show_path_reapplies_saved_playback_values(self) -> None:
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
            lambda: state.preview_always_on_top,
            lambda _pinned: None,
            PreviewPositionMemory(),

        )
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.hide)
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            window.show_path(path)
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
        self.assertEqual(self.window._playback._pending_position_ms, 7500)


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
            self.window._actions.step_frame(1)
            self.window._actions.step_frame(-1)
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
    """미리 보기 창과 그 위에 겹치는 오버레이, 설정 팝업이 OS 전역 최상위 플래그를 쓰지 않는지"""

    def setUp(self) -> None:
        self.window = _make_window()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.hide)

    def test_window_and_overlays_do_not_use_the_global_stays_on_top_flag(self) -> None:
        """미리 보기 창과 정보/토스트 도구 창이 WindowStaysOnTopHint 없이 만들어지는지 검증"""
        flags = self.window.windowFlags()
        self.assertEqual(flags & Qt.WindowType.WindowType_Mask, Qt.WindowType.Window)
        self.assertFalse(flags & Qt.WindowType.WindowStaysOnTopHint)
        overlays = [*self.window.findChildren(PreviewInfoOverlay), *self.window.findChildren(PreviewToast)]
        self.assertEqual(len(overlays), 2)
        for overlay in overlays:
            overlay_flags = overlay.windowFlags()
            self.assertFalse(overlay_flags & Qt.WindowType.WindowStaysOnTopHint)
            self.assertTrue(overlay_flags & Qt.WindowType.WindowDoesNotAcceptFocus)
            self.assertTrue(overlay_flags & Qt.WindowType.Tool)

    def test_settings_popup_does_not_inherit_a_global_stays_on_top_flag(self) -> None:
        """미리 보기 창을 부모로 여는 설정 팝업이 전역 최상위 플래그를 받지 않는지 검증"""
        popup = PreviewSettingsPopup(PreviewSettingsPanel(lambda: UiState(), ClassifierConfig), self.window)
        self.addCleanup(popup.deleteLater)
        self.assertFalse(popup.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(popup.windowFlags() & Qt.WindowType.Tool)

    def test_compare_button_uses_the_transparent_tool_button_rule(self) -> None:
        """화질 비교 버튼에 투명 배경 도구 버튼 규칙이 있어 네이티브 회색 바탕이 깔리지 않는지 검증"""
        sheet = build_preview_qss(LIGHT_PALETTE)
        self.assertIn("QToolButton#PreviewCompareButton,", sheet)
        compare_rule = sheet.split("QToolButton#PreviewSettingsButton,", 1)[1].split("}", 1)[0]
        self.assertIn("background-color: transparent;", compare_rule)
        self.assertIn("border: none;", compare_rule)
        self.assertIn("QToolButton#PreviewCompareButton:hover", sheet)
        self.assertIn("QToolButton#PreviewCompareButton:pressed", sheet)
        self.assertIn("QToolButton#PreviewCompareButton", self.window.styleSheet())


class PinToggleTests(GuiTestCase):
    """항상 위에 표시 핀 전환: 재생 유지, 정보 표시 플래그 동기화, 상태 저장"""

    def _window(self, on_pinned: list[bool]) -> VideoPreviewWindow:
        state = UiState()
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
            lambda: state.preview_always_on_top,
            on_pinned.append,
            PreviewPositionMemory(),

        )
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.hide)
        return window

    def _pin_button(self, window: VideoPreviewWindow) -> QToolButton:
        button = window.findChild(QToolButton, "PreviewPinButton")
        assert button is not None
        return button

    def test_default_pin_has_no_global_stays_on_top_flag(self) -> None:
        """기본 핀 켜짐이어도 OS 전역 최상위 플래그 없이 만들어지는지 검증"""
        window = self._window([])
        self.assertFalse(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertEqual(self._pin_button(window).accessibleName(), "항상 위에 표시 해제")

    def test_unpin_keeps_playback_and_the_session(self) -> None:
        """핀을 끌 때 창을 다시 만들지 않아 재생과 파일 핸들이 그대로인지 검증"""
        window = self._window([])
        window.show()
        _APP.processEvents()
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            window.show_path(path)
            self.assertIsNotNone(window._playback.source_file)
            with (
                mock.patch.object(cast("object", window._player), "stop") as stop,
                mock.patch.object(cast("object", window._player), "setSource") as set_source,
                mock.patch.object(
                    cast("object", window._player), "playbackState",
                    return_value=QMediaPlayer.PlaybackState.PlayingState,
                ),
            ):
                self._pin_button(window).click()
                _APP.processEvents()
            stop.assert_not_called()
            set_source.assert_not_called()
            self.assertIsNotNone(window._playback.source_file)
            self.assertEqual(window.current_path, path)
            self.assertFalse(
                window._info._overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            )
            self.assertFalse(window._info._toast.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            self.assertEqual(self._pin_button(window).accessibleName(), "항상 위에 표시")
            toast = window.findChild(QLabel, "PreviewToast")
            assert toast is not None
            self.assertEqual(toast.text(), "항상 위에 표시 해제")
            window.hide()

    def test_unpinned_state_is_used_when_the_window_is_created(self) -> None:
        """저장된 핀 값을 읽어 처음부터 전역 최상위 플래그 없이 만들고 핀 버튼 표시를 맞추는지 검증"""
        state = UiState()
        state.preview_always_on_top = False
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
            lambda: state.preview_always_on_top,
            lambda _pinned: None,
            PreviewPositionMemory(),

        )
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.hide)
        self.assertFalse(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertEqual(self._pin_button(window).accessibleName(), "항상 위에 표시")
        self.assertEqual(
            self._pin_button(window).toolTip(),
            "영상 창을 앱의 다른 창보다 위에 계속 띄워 둡니다.",
        )

    def test_set_owner_keeps_the_session_and_geometry(self) -> None:
        """소유자를 바꿔도 보던 영상과 창 위치가 그대로 남는지 검증"""
        window = self._window([])
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            window.show()
            window.show_path(path)
            _APP.processEvents()
            window.move(35, 45)
            window.resize(700, 400)
            _APP.processEvents()
            geometry = window.geometry()
            window.set_owner(owner)
            _APP.processEvents()
            self.assertIs(window.parentWidget(), owner)
            self.assertEqual(window.current_path, path)
            self.assertEqual(window.geometry(), geometry)
            window.set_owner(None)
            _APP.processEvents()
            self.assertIsNone(window.parentWidget())
            self.assertEqual(window.current_path, path)
            self.assertEqual(window.geometry(), geometry)
            window.hide()

    def test_set_owner_restores_fullscreen(self) -> None:
        """전체화면이던 창의 소유자를 바꿔도 전체화면을 유지하는지 검증"""
        window = self._window([])
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        window.showFullScreen()
        _APP.processEvents()
        self.assertTrue(window.isFullScreen())
        window.set_owner(owner)
        _APP.processEvents()
        self.assertTrue(window.isFullScreen())
        window.showNormal()
        window.hide()

    def test_minimize_hide_pauses_only_and_keeps_the_session(self) -> None:
        """최소화로 숨겨질 때는 재생만 멈추고 경로와 비교 세션을 해제하지 않는지 검증"""
        window = self._window([])
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            window.show_path(path)
            with (
                mock.patch.object(window, "isMinimized", return_value=True),
                mock.patch.object(window._playback, "pause") as pause,
                mock.patch.object(window._modes, "pause") as modes_pause,
            ):
                window.hideEvent(None)
            pause.assert_called_once()
            modes_pause.assert_called_once()
            self.assertEqual(window.current_path, path)
            self.assertTrue(window._key_filter_installed)
            window.hide()

    def test_pin_change_is_reported_for_saving(self) -> None:
        window = self._window([])
        saved: list[bool] = []
        window._on_always_on_top_changed = saved.append
        self._pin_button(window).click()
        self.assertEqual(saved, [False])
        self._pin_button(window).click()
        self.assertEqual(saved, [False, True])

    def test_pin_toggle_keeps_the_visible_info_and_toast(self) -> None:
        """표시 중이던 정보 표시와 토스트가 핀 전환 뒤에도 숨지 않고 남는지 검증"""
        state = UiState()
        state.preview_info.mode = INFO_MODE_SUMMARY
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
            lambda: state.preview_always_on_top,
            lambda _pinned: None,
            PreviewPositionMemory(),

        )
        self.addCleanup(window.deleteLater)
        window.show()
        _APP.processEvents()
        overlay = window._info._overlay
        toast = window._info._toast
        self.assertTrue(overlay.isVisible())
        toast.show_message("테스트")
        _APP.processEvents()
        window._apply_always_on_top(False)
        _APP.processEvents()
        self.assertTrue(overlay.isVisible())
        self.assertTrue(toast.isVisible())
        self.assertFalse(overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertFalse(toast.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        window.hide()


class FullscreenRestoreTests(GuiTestCase):
    """저장된 전체화면 상태로 다시 표시할 때 하단 바 아이콘을 맞추는지"""

    def test_show_path_updates_the_fullscreen_icon_after_restoring(self) -> None:
        """전체화면으로 복원한 직후 전체화면 해제 아이콘과 이름으로 바뀌는지 검증"""
        state = UiState()
        state.preview_fullscreen = True
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
            lambda: state.preview_always_on_top,
            lambda _pinned: None,
            PreviewPositionMemory(),

        )
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.hide)
        button = window.findChild(QToolButton, "PreviewFullscreenButton")
        assert button is not None
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            with (
                mock.patch.object(window, "showFullScreen"),
                mock.patch.object(window, "isFullScreen", return_value=True),
            ):
                window.show_path(path)
        self.assertEqual(button.accessibleName(), "전체화면 해제")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class _Extractor:
    """비교 뷰가 쓰는 프레임 추출기 대역"""

    def __init__(self) -> None:
        self.released: int = 0

    def extract_pair(
        self,
        original: "FrameSource",
        compared: "FrameSource",
        *,
        time_sec: float,
        frame_width: int,
        frame_height: int,
        cancel_event: threading.Event,
    ) -> ExtractedFrames:
        del original, compared, time_sec, frame_width, frame_height, cancel_event
        return ExtractedFrames(original_bytes=b"", compared_bytes=b"")

    def release_decoders(self) -> None:
        self.released += 1


def _compare_session(path: str) -> CompareSession:
    plan = ComparePlan(
        original=FrameSource(key="원본", path=path, label="원본"),
        targets=(),
        default_key=None,
        reason="비교할 대상이 없습니다.",
    )
    return CompareSession(
        plan=plan, duration_sec=100.0, fps=30.0, filename="clip.mp4", position_sec=12.5,
        source_path=path,
    )


class SingleWindowCompareTests(GuiTestCase):
    """같은 창 안에서 일반 미리 보기와 화질 비교 모드를 오가는지"""

    def setUp(self) -> None:
        self.window = _make_window()
        self.addCleanup(self.window.deleteLater)
        self.window.set_compare_extractor(_Extractor())
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "clip.mp4")
        with open(self.path, "wb") as handle:
            handle.write(b"not a real video")
        self.window.show_path(self.path)
        _APP.processEvents()

    def test_compare_session_uses_the_same_window_and_page(self) -> None:
        window = self.window
        window.show_compare_session(_compare_session(self.path))
        _APP.processEvents()
        self.assertIs(window._stack.currentWidget(), window._compare_page)
        self.assertTrue(window.compare_mode)
        self.assertTrue(window._controls.compare_button.isChecked())
        self.assertIn("일반 미리 보기로 돌아갑니다.", window._controls.compare_button.toolTip())
        self.assertFalse(window._info._overlay.isVisible())
        view = window._modes.view
        assert view is not None
        self.assertEqual(view.source_path, self.path)

    def test_leaving_compare_keeps_the_same_window_and_the_time(self) -> None:
        window = self.window
        window.show_compare_session(_compare_session(self.path))
        _APP.processEvents()
        window.leave_compare()
        _APP.processEvents()
        self.assertIs(window._stack.currentWidget(), window._normal_page)
        self.assertFalse(window.compare_mode)
        self.assertFalse(window._controls.compare_button.isChecked())
        self.assertIn(
            "현재 재생 시점으로 화질 비교 창으로 전환합니다.",
            window._controls.compare_button.toolTip(),
        )
        self.assertEqual(window.current_path, self.path)
        self.assertEqual(window._playback._pending_position_ms, 12500)

    def test_ctrl_b_toggles_within_the_same_window(self) -> None:
        """Ctrl+B가 창을 숨기지 않고 같은 창 안에서 비교 모드로 바꾸는지 검증"""
        window = self.window
        requests: list[tuple[str, float]] = []
        window.compare_requested.connect(lambda path, pos: requests.append((path, pos)))
        _press(self.window, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(requests, [(self.path, window.position_sec)])
        self.assertTrue(window.isVisible())

    def test_compare_notice_uses_the_single_shared_toast(self) -> None:
        window = self.window
        window.show_compare_session(_compare_session(self.path))
        _APP.processEvents()
        with mock.patch.object(window, "isActiveWindow", return_value=True):
            event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_2, Qt.KeyboardModifier.NoModifier)
            handled = window.eventFilter(window, event)
        self.assertTrue(handled)
        toasts = window.findChildren(PreviewToast)
        self.assertEqual(len(toasts), 1)
        label = window.findChild(QLabel, "PreviewToast")
        assert label is not None
        self.assertEqual(label.text(), "즉시 전환 모드")

    def test_compare_mode_keeps_the_pin_and_fullscreen_buttons(self) -> None:
        window = self.window
        window.show_compare_session(_compare_session(self.path))
        _APP.processEvents()
        _press(window, Qt.Key.Key_F)
        self.assertTrue(window._controls.findChild(QToolButton, "PreviewFullscreenButton") is not None)
        pin = window._controls.findChild(QToolButton, "PreviewPinButton")
        assert pin is not None
        self.assertTrue(pin.isEnabled())
        window._apply_always_on_top(False)
        self.assertFalse(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(window.compare_mode)

    def test_control_bar_seek_frame_step_and_compare_rate_sync(self) -> None:
        """하단 바 위치 이동, 한 프레임 이동, 비교 뷰 배속 변경이 현재 모드에 맞게 전달되는지 검증"""
        window = self.window
        with mock.patch.object(window._player, "setPosition") as set_pos:
            window._controls.seek_requested.emit(4200)
            set_pos.assert_called_once_with(4200)
        with mock.patch.object(window._actions, "step_frame") as step_normal:
            window._controls.frame_step_requested.emit(1)
            step_normal.assert_called_once_with(1)

        window.show_compare_session(_compare_session(self.path))
        _APP.processEvents()
        view = window._modes.view
        assert view is not None
        with mock.patch.object(view, "set_time") as set_time:
            window._controls.seek_requested.emit(7500)
            set_time.assert_called_once_with(7.5, immediate=True)
        with mock.patch.object(view, "step_frame") as step_compare:
            window._controls.frame_step_requested.emit(-1)
            step_compare.assert_called_once_with(-1)
        window._modes.rate_changed.emit(1.5)
        self.assertEqual(window._controls._playback_rate, 1.5)
        self.assertTrue(window._controls._rate_actions[1.5].isChecked())


class MinimizeLifecycleTests(GuiTestCase):
    """최소화 상태에서 명시적으로 숨길 때의 정리와, 다시 열 때 최소화를 푸는지"""

    def setUp(self) -> None:
        self.window = _make_window()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.hide)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "clip.mp4")
        with open(self.path, "wb") as handle:
            handle.write(b"not a real video")

    def _show_minimized(self) -> None:
        """영상을 보여 준 뒤 실제 최소화 사건까지 처리"""
        self.window.show_path(self.path)
        _APP.processEvents()
        self.window.showMinimized()
        _APP.processEvents()
        self.assertTrue(self.window.isMinimized())

    def test_minimize_then_hide_runs_the_full_cleanup(self) -> None:
        """최소화 뒤 명시적으로 숨기면 재생과 비교를 멈추고 경로와 키 필터를 정리하는지 검증"""
        self._show_minimized()
        self.assertEqual(self.window.current_path, self.path)
        self.assertTrue(self.window._key_filter_installed)
        with (
            mock.patch.object(self.window._playback, "stop") as stop,
            mock.patch.object(self.window._modes, "stop") as modes_stop,
        ):
            self.window.hide()
            _APP.processEvents()
        stop.assert_called_once()
        modes_stop.assert_called_once()
        self.assertEqual(self.window.current_path, "")
        self.assertFalse(self.window._key_filter_installed)

    def test_minimize_alone_pauses_and_keeps_the_session(self) -> None:
        """최소화만 하면 재생만 멈추고 경로, 키 필터, 재생 세션은 남기는지 검증"""
        self.window.show_path(self.path)
        _APP.processEvents()
        with (
            mock.patch.object(self.window._playback, "pause") as pause,
            mock.patch.object(self.window._modes, "pause") as modes_pause,
            mock.patch.object(self.window._playback, "stop") as stop,
        ):
            self.window.showMinimized()
            _APP.processEvents()
        pause.assert_called_once()
        modes_pause.assert_called_once()
        stop.assert_not_called()
        self.assertEqual(self.window.current_path, self.path)
        self.assertTrue(self.window._key_filter_installed)

    def test_show_path_restores_a_minimized_window(self) -> None:
        """최소화한 창을 숨긴 뒤 다시 열면 최소화가 풀린 보통 창으로 보이는지 검증"""
        self._show_minimized()
        self.window.hide()
        _APP.processEvents()
        self.window.show_path(self.path)
        _APP.processEvents()
        self.assertFalse(self.window.isMinimized())
        self.assertTrue(self.window.isVisible())

    def test_compare_session_restores_a_minimized_window(self) -> None:
        """최소화된 창에서 화질 비교를 열면 최소화를 풀고 비교 모드로 보이는지 검증"""
        self.window.set_compare_extractor(_Extractor())
        self._show_minimized()
        self.window.show_compare_session(_compare_session(self.path))
        _APP.processEvents()
        self.assertFalse(self.window.isMinimized())
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.compare_mode)


class CompareSessionWindowStateTests(GuiTestCase):
    """닫힌 창에서 비교 세션을 바로 올릴 때 창 상태를 세션과 저장값에 맞추는지"""

    def setUp(self) -> None:
        self.state = UiState()
        self.state.preview_volume = 30
        self.state.preview_muted = True
        self.state.preview_playback_rate = 1.5
        self.window = _make_window(ui=self.state)
        self.window.set_compare_extractor(_Extractor())
        self.addCleanup(self._release_window)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "clip.mp4")
        with open(self.path, "wb") as handle:
            handle.write(b"not a real video")

    def _release_window(self) -> None:
        if self.window._key_filter_installed:
            remove_key_filter(self.window)
            self.window._key_filter_installed = False
        self.window.close()

    def test_closed_window_opening_a_compare_session_syncs_path_title_and_controls(self) -> None:
        """숨겨진 창에서 비교 세션을 올리면 경로, 제목, 오디오, 배속, 키 필터가 함께 맞는지 검증"""
        self.window.show_compare_session(_compare_session(self.path))
        _APP.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.compare_mode)
        self.assertEqual(self.window.current_path, self.path)
        self.assertEqual(self.window.windowTitle(), "화질 비교 - clip.mp4")
        self.assertEqual(self.window._controls.audio_state(), (30, True))
        self.assertEqual(self.window._controls._playback_rate, 1.5)
        self.assertTrue(self.window._key_filter_installed)

    def test_closed_window_compare_session_restores_fullscreen_and_updates_the_icon(self) -> None:
        """저장된 전체화면 상태로 열고 하단 바 전체화면 아이콘을 맞추는지 검증"""
        self.state.preview_fullscreen = True
        button = self.window.findChild(QToolButton, "PreviewFullscreenButton")
        assert button is not None
        with (
            mock.patch.object(self.window, "showFullScreen") as show_fullscreen,
            mock.patch.object(self.window, "isFullScreen", return_value=True),
        ):
            self.window.show_compare_session(_compare_session(self.path))
        show_fullscreen.assert_called_once()
        self.assertEqual(button.accessibleName(), "전체화면 해제")


class SessionPositionMemoryTests(GuiTestCase):
    """앱 실행 중에만 영상별 재생 위치를 기억하고 경계에서 지우는지"""

    def setUp(self) -> None:
        self.memory = PreviewPositionMemory()
        self.window = _make_window(positions=self.memory)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.hide)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = self._video("clip0.mp4")
        self.other = self._video("clip1.mp4")

    def _video(self, name: str) -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as handle:
            handle.write(b"not a real video")
        return path

    def _play(self) -> None:
        self.window.show_path(self.path)
        _APP.processEvents()

    def test_hide_remembers_the_normal_position(self) -> None:
        """일반 모드에서 창을 닫으면 그 시점을 기억하는지 검증"""
        self._play()
        player = cast("object", self.window._player)
        with (
            mock.patch.object(player, "position", return_value=5000),
            mock.patch.object(player, "duration", return_value=60000),
        ):
            self.window.hide()
        _APP.processEvents()
        self.assertEqual(self.memory.recall(self.path), 5.0)

    def test_reopening_restores_the_remembered_position(self) -> None:
        """같은 영상을 다시 열면 기억한 시점을 읽기 뒤 적용할 값으로 넘기는지 검증"""
        self.memory.remember(self.path, 7.5)
        self.window.load_path(self.path)
        self.assertEqual(self.window._playback.pending_position_ms, 7500)

    def test_short_position_is_forgotten_when_closing(self) -> None:
        """로드가 끝난 뒤 0.5초 미만으로 되감고 닫으면 기존 기록을 지우는지 검증"""
        self.memory.remember(self.path, 9.0)
        self._play()
        self.window._playback.clear_pending_position()
        player = cast("object", self.window._player)
        with (
            mock.patch.object(player, "position", return_value=200),
            mock.patch.object(player, "duration", return_value=60000),
        ):
            self.window.hide()
        self.assertIsNone(self.memory.recall(self.path))

    def test_near_end_position_is_forgotten_when_closing(self) -> None:
        """끝에서 1초 안쪽이면 본 것으로 보고 기록을 지우는지 검증"""
        self.memory.remember(self.path, 9.0)
        self._play()
        self.window._playback.clear_pending_position()
        player = cast("object", self.window._player)
        with (
            mock.patch.object(player, "position", return_value=59500),
            mock.patch.object(player, "duration", return_value=60000),
        ):
            self.window.hide()
        self.assertIsNone(self.memory.recall(self.path))

    def test_end_of_media_forgets_the_position(self) -> None:
        """일반 모드에서 끝까지 재생되면 기록을 지워 다음에 처음부터 여는지 검증"""
        self.memory.remember(self.path, 9.0)
        self.window._path = self.path
        self.window._on_media_status_changed(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertIsNone(self.memory.recall(self.path))

    def test_unloaded_media_keeps_the_remembered_position(self) -> None:
        """빠른 영상 교체로 아직 재생 시간을 모를 때 기존 기록을 0초로 덮지 않는지 검증"""
        self.memory.remember(self.path, 8.0)
        self.window._path = self.path
        player = cast("object", self.window._player)
        with mock.patch.object(player, "duration", return_value=0):
            self.window.load_path(self.other)
        self.assertEqual(self.memory.recall(self.path), 8.0)

    def test_pending_seek_keeps_the_remembered_position(self) -> None:
        """기억 시점을 적용하기 전에 영상을 바꿔도 기존 기록을 유지하는지 검증"""
        self.memory.remember(self.path, 8.0)
        self.window._path = self.path
        self.window._playback.queue_position(1000)
        player = cast("object", self.window._player)
        with (
            mock.patch.object(player, "position", return_value=0),
            mock.patch.object(player, "duration", return_value=60000),
        ):
            self.window.load_path(self.other)
        self.assertEqual(self.memory.recall(self.path), 8.0)

    def test_hiding_in_compare_mode_remembers_the_compare_position(self) -> None:
        """비교 뷰를 보다가 닫으면 그 시점을 기억하는지 검증"""
        self.window.set_compare_extractor(_Extractor())
        self._play()
        self.window.show_compare_session(_compare_session(self.path))
        _APP.processEvents()
        self.window.hide()
        self.assertEqual(self.memory.recall(self.path), 12.5)

    def test_replacing_the_video_before_restoring_drops_the_old_pending_position(self) -> None:
        """기억 시점을 적용하기 전에 다음 영상으로 넘어가도 그 시점을 다음 영상에 쓰지 않는지 검증"""
        self.memory.remember(self.path, 42.0)
        self.window.show_path(self.path)
        self.assertEqual(self.window._playback.pending_position_ms, 42000)
        self.window.show_path(self.other)
        self.assertIsNone(self.window._playback.pending_position_ms)
        self.assertEqual(self.memory.recall(self.path), 42.0)

    def test_switching_to_compare_remembers_the_normal_position(self) -> None:
        """일반 모드에서 비교 모드로 바꾸기 직전 시점을 기억하는지 검증"""
        self.window.set_compare_extractor(_Extractor())
        self._play()
        player = cast("object", self.window._player)
        with (
            mock.patch.object(player, "position", return_value=3000),
            mock.patch.object(player, "duration", return_value=60000),
        ):
            self.window.show_compare_session(_compare_session(self.path))
        self.assertEqual(self.memory.recall(self.path), 3.0)

