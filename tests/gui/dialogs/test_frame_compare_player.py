"""화질 비교 재생 엔진 검증: 드리프트 판정, 축소 왕복 이미지, 재생 전이, 프레임 쌍, 조작 바 동기화

- 실제 미디어 대신 합성 QImage와 QVideoFrame, 임시 더미 파일로 확인
"""

import os
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtMultimedia import QMediaPlayer, QVideoFrame
from PyQt6.QtWidgets import QApplication, QWidget

from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs.frame_compare_playback_rules import (
    MIN_RENDER_INTERVAL_SEC,
    PlaybackSettings,
)
from src.gui.dialogs.frame_compare_player import ComparePlaybackControls, ComparePlaybackEngine
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _image(width: int = 320, height: int = 180, color: str = "#FF0000") -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


class _Settings:
    """저장값을 기억하는 재생 설정 대역"""

    def __init__(self) -> None:
        self.volume = 80
        self.muted = True
        self.rate = 2.0
        self.saved_audio: list[tuple[int, bool]] = []
        self.saved_rates: list[float] = []

    def get_audio(self) -> tuple[int, bool]:
        return self.volume, self.muted

    def set_audio(self, volume: int, muted: bool) -> None:
        self.volume = volume
        self.muted = muted
        self.saved_audio.append((volume, muted))

    def get_rate(self) -> float:
        return self.rate

    def set_rate(self, rate: float) -> None:
        self.rate = rate
        self.saved_rates.append(rate)

    def playback_settings(self) -> PlaybackSettings:
        return PlaybackSettings(
            get_audio=self.get_audio,
            set_audio=self.set_audio,
            get_rate=self.get_rate,
            set_rate=self.set_rate,
        )


def _timeline(parent: QWidget) -> layout.CompareTimeline:
    return layout.build_timeline(
        parent,
        lambda _step: None,
        lambda _percent: None,
        lambda _value: None,
        lambda: None,
        lambda _rate: None,
        lambda: None,
        lambda _value: None,
    )


class EngineTests(GuiTestCase):
    """재생 엔진의 프레임 쌍, 상태 전이, 위치 보정, 공유 핸들 정리"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.original = os.path.join(self.root, "a.mp4")
        self.compared = os.path.join(self.root, "b.mp4")
        for path in (self.original, self.compared):
            with open(path, "wb") as handle:
                handle.write(b"x")
        self.engine = ComparePlaybackEngine()
        self.addCleanup(self.engine.deleteLater)
        self.frames: list[tuple[QImage, QImage]] = []
        self.engine.frames_ready.connect(
            lambda original, compared: self.frames.append((original, compared))
        )

    def _load(self, tier_key: str | None = "720p") -> None:
        self.engine.load(
            original_path=self.original,
            compared_path=self.compared,
            tier_key=tier_key,
            frame_width=1600,
            frame_height=900,
            position_ms=0,
        )
        self.engine._last_render = 0.0

    def test_downscale_mode_emits_the_roundtrip_pair(self) -> None:
        self._load()
        self.engine._playing = True
        self.engine._on_primary_frame(QVideoFrame(_image(1600, 900)))
        self.assertEqual(len(self.frames), 1)
        self.assertEqual(self.frames[0][0].size(), self.frames[0][1].size())

    def test_reentrant_frame_keeps_only_the_latest(self) -> None:
        self._load()
        self.engine._playing = True
        sizes: list[tuple[int, int]] = []

        def collect(original: QImage, compared: QImage) -> None:
            del compared
            sizes.append((original.width(), original.height()))
            if len(sizes) == 1:
                self.engine._last_render = 0.0
                self.engine._on_primary_frame(QVideoFrame(_image(800, 450, "#0000FF")))

        self.engine.frames_ready.connect(collect)
        self.engine._on_primary_frame(QVideoFrame(_image(1600, 900)))
        self.assertEqual(sizes, [(1600, 900), (800, 450)])

    def test_frames_are_throttled_to_the_render_interval(self) -> None:
        """직전 변환에서 30fps 간격이 지나지 않은 프레임은 변환 없이 버리는지 검증"""
        self._load()
        self.engine._playing = True
        self.engine._last_render = time.monotonic()
        self.engine._on_primary_frame(QVideoFrame(_image(1600, 900)))
        self.assertEqual(self.frames, [])

    def test_frames_are_rendered_again_after_the_interval(self) -> None:
        """간격이 지난 뒤의 프레임은 다시 화면에 넘기는지 검증"""
        self._load()
        self.engine._playing = True
        self.engine._last_render = time.monotonic() - MIN_RENDER_INTERVAL_SEC * 2
        self.engine._on_primary_frame(QVideoFrame(_image(1600, 900)))
        self.assertEqual(len(self.frames), 1)

    def test_secondary_frame_conversion_is_throttled_too(self) -> None:
        """두 파일 모드에서도 같은 간격 안의 종 프레임을 버리는지 검증"""
        self._load(None)
        self.engine._playing = True
        self.engine._last_render = 0.0
        self.engine._on_primary_frame(QVideoFrame(_image(1600, 900)))
        self.engine._last_render = time.monotonic()
        self.engine._on_secondary_frame(QVideoFrame(_image(1280, 720, "#0000FF")))
        self.assertEqual(self.frames, [])

    def test_dual_mode_pairs_primary_and_secondary_frames(self) -> None:
        self._load(None)
        self.engine._playing = True
        self.engine._on_primary_frame(QVideoFrame(_image(1600, 900)))
        self.assertEqual(self.frames, [])
        self.engine._last_render = 0.0
        self.engine._on_secondary_frame(QVideoFrame(_image(1280, 720, "#0000FF")))
        self.assertEqual(len(self.frames), 1)
        self.engine._on_secondary_frame(QVideoFrame())
        self.assertEqual(len(self.frames), 1)
        self.engine._last_render = 0.0
        self.engine._on_primary_frame(QVideoFrame(_image(1600, 900)))
        self.assertEqual(len(self.frames), 2)

    def test_frames_are_ignored_while_paused_or_invalid(self) -> None:
        self._load()
        self.engine._on_primary_frame(QVideoFrame(_image(1600, 900)))
        self.engine._on_secondary_frame(QVideoFrame(_image(1600, 900)))
        self.engine._playing = True
        self.engine._on_primary_frame(QVideoFrame())
        self.assertEqual(self.frames, [])

    def test_paused_raw_frames_are_skipped_in_dual_mode(self) -> None:
        self._load(None)
        self.engine._playing = True
        self.engine._on_secondary_frame(QVideoFrame(_image(1280, 720)))
        self.assertEqual(self.frames, [])
        self.engine._playing = False
        self.engine._on_secondary_frame(QVideoFrame(_image(1280, 720)))
        self.assertEqual(self.frames, [])

    def test_playback_state_and_position_signals(self) -> None:
        positions: list[int] = []
        states: list[bool] = []
        self.engine.position_changed.connect(positions.append)
        self.engine.playing_changed.connect(states.append)
        self.engine._ready = True
        self.engine._on_playback_state(QMediaPlayer.PlaybackState.PlayingState)
        self.engine._on_primary_position(1200)
        self.engine._on_playback_state(QMediaPlayer.PlaybackState.PausedState)
        self.engine._ready = False
        self.engine._on_primary_position(1300)
        self.assertEqual(states, [True, False])
        self.assertEqual(positions, [1200])
        self.assertFalse(self.engine.is_playing())

    def test_end_of_media_and_secondary_status(self) -> None:
        ends: list[bool] = []
        self.engine.end_reached.connect(lambda: ends.append(True))
        self.engine._on_primary_status(QMediaPlayer.MediaStatus.LoadedMedia)
        self.engine._on_primary_status(QMediaPlayer.MediaStatus.EndOfMedia)
        self.engine._on_secondary_status(QMediaPlayer.MediaStatus.EndOfMedia)
        self.engine._on_secondary_status(QMediaPlayer.MediaStatus.LoadedMedia)
        self.assertEqual(ends, [True])

    def test_sync_secondary_needs_dual_playback(self) -> None:
        self.engine._dual = True
        self.engine._playing = True
        self.engine._sync_secondary()
        self.engine._dual = False
        self.engine._sync_secondary()
        self.engine._dual = True
        self.engine._playing = False
        self.engine._sync_secondary()
        self.assertFalse(self.engine.is_playing())

    def test_secondary_sync_waits_for_the_cooldown(self) -> None:
        """보정 시크 뒤 쿨다운 동안에는 다시 시크하지 않는지 검증"""
        self.engine._dual = True
        self.engine._playing = True
        with (
            mock.patch.object(self.engine._primary, "position", return_value=5000),
            mock.patch.object(self.engine._secondary, "position", return_value=0),
            mock.patch.object(self.engine._secondary, "setPosition") as set_position,
            mock.patch(
                "src.gui.dialogs.frame_compare_player.time.monotonic", side_effect=[100.0, 100.1]
            ),
        ):
            self.engine._sync_secondary()
            self.engine._sync_secondary()
        set_position.assert_called_once_with(5000)

    def test_audio_rate_and_getters(self) -> None:
        self.engine.apply_audio(50, True)
        self.engine.set_rate(2.5)
        self.assertEqual(self.engine.volume_percent(), 50)
        self.assertTrue(self.engine.muted())
        self.assertEqual(self.engine.rate(), 2.5)
        self.assertEqual(self.engine.position_ms(), 0)

    def test_play_pause_toggle_seek_and_stop(self) -> None:
        self._load(None)
        self.engine.seek(1500)
        self.engine.play()
        self.engine._playing = True
        self.engine.toggle()
        self.engine._playing = False
        self.engine.toggle()
        self.engine.pause()
        self.engine.stop()
        self.assertFalse(self.engine.is_playing())
        self.assertEqual(self.engine._source_files, [])

    def test_unready_play_is_ignored(self) -> None:
        self.engine.play()
        self.assertFalse(self.engine.is_playing())

    def test_shared_handle_fallback_when_open_fails(self) -> None:
        with mock.patch(
            "src.gui.dialogs.frame_compare_player.open_shared_read_file", return_value=None
        ):
            self._load()
        self.assertEqual(self.engine._source_files, [])
        self.engine.stop()

    def test_stop_closes_open_shared_handles(self) -> None:
        self._load()
        self.engine.stop()
        self.assertEqual(self.engine._source_files, [])


class ControlsTests(GuiTestCase):
    """조작 바 동기화와 저장값 연결, 신호 전달"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.timeline = _timeline(self.parent)
        self.addCleanup(self.timeline.slider.deleteLater)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.original = os.path.join(tmp.name, "a.mp4")
        self.compared = os.path.join(tmp.name, "b.mp4")
        for path in (self.original, self.compared):
            with open(path, "wb") as handle:
                handle.write(b"x")

    def _controls(self, settings: _Settings | None = None) -> ComparePlaybackControls:
        playback = None if settings is None else settings.playback_settings()
        controls = ComparePlaybackControls(self.timeline, playback)
        self.addCleanup(controls.deleteLater)
        return controls

    def test_saved_audio_and_rate_are_applied_to_the_bar(self) -> None:
        controls = self._controls(_Settings())
        self.assertEqual(controls.volume_percent(), 80)
        self.assertTrue(controls.muted())
        self.assertEqual(controls.rate(), 2.0)
        self.assertEqual(self.timeline.volume_slider.value(), 80)
        self.assertEqual(self.timeline.mute_button.accessibleName(), "음소거 해제")
        self.assertTrue(self.timeline.rate_actions[2.0].isChecked())

    def test_rate_controls_save_to_the_shared_settings(self) -> None:
        settings = _Settings()
        controls = self._controls(settings)
        controls.select_rate(4.0)
        controls.step_rate(1)
        controls.step_rate(-1)
        controls.reset_rate()
        self.assertEqual(settings.saved_rates, [4.0, 4.5, 4.0, 1.0])
        self.assertEqual(controls.rate(), 1.0)

    def test_mute_and_volume_save_to_the_shared_settings(self) -> None:
        settings = _Settings()
        controls = self._controls(settings)
        controls.toggle_mute()
        self.assertFalse(controls.muted())
        self.assertEqual(settings.saved_audio[-1], (80, False))
        controls.set_volume(30)
        self.assertEqual(controls.volume_percent(), 30)
        self.assertEqual(settings.saved_audio[-1], (30, False))
        self.assertEqual(self.timeline.volume_slider.value(), 30)

    def test_defaults_apply_without_shared_settings(self) -> None:
        controls = self._controls()
        self.assertEqual(controls.volume_percent(), 100)
        self.assertFalse(controls.muted())
        self.assertEqual(controls.rate(), 1.0)
        controls.toggle_mute()
        controls.set_volume(20)
        controls.select_rate(1.5)
        controls.save_audio()
        controls.reload_settings()
        self.assertEqual(controls.volume_percent(), 20)
        self.assertTrue(controls.muted())
        self.assertEqual(controls.rate(), 1.5)

    def test_reload_settings_reads_the_latest_saved_values(self) -> None:
        """창을 다시 열 때 미리 보기에서 바뀐 음량, 음소거, 배속을 다시 읽는지 검증"""
        settings = _Settings()
        controls = self._controls(settings)
        settings.volume = 25
        settings.muted = False
        settings.rate = 4.0
        controls.reload_settings()
        self.assertEqual(controls.volume_percent(), 25)
        self.assertFalse(controls.muted())
        self.assertEqual(controls.rate(), 4.0)
        self.assertEqual(self.timeline.volume_slider.value(), 25)
        self.assertTrue(self.timeline.rate_actions[4.0].isChecked())

    def test_controls_delegate_and_forward_signals(self) -> None:
        controls = self._controls()
        frames: list[tuple[QImage, QImage]] = []
        positions: list[int] = []
        states: list[bool] = []
        ends: list[bool] = []
        controls.frames_ready.connect(lambda original, compared: frames.append((original, compared)))
        controls.position_changed.connect(positions.append)
        controls.playing_changed.connect(states.append)
        controls.end_reached.connect(lambda: ends.append(True))
        controls.load(
            original_path=self.original,
            compared_path=self.compared,
            tier_key=None,
            frame_width=320,
            frame_height=180,
            position_ms=0,
        )
        controls.start()
        controls.pause()
        controls.seek(500)
        controls.stop()
        self.assertFalse(controls.is_playing())
        self.assertEqual(controls.position_ms(), 0)
        controls._engine.frames_ready.emit(_image(), _image())
        controls._engine.position_changed.emit(700)
        controls._engine.end_reached.emit()
        controls._engine._on_playback_state(QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(len(frames), 1)
        self.assertEqual(positions, [700])
        self.assertEqual(ends, [True])
        self.assertIn(True, states)


if __name__ == "__main__":
    unittest.main(verbosity=2)



