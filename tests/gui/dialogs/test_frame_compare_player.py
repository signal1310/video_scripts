"""화질 비교 재생 엔진 검증: 드리프트 판정, 축소 왕복 이미지, 재생 전이, 프레임 쌍, 위치 보정

- 실제 미디어 대신 합성 QImage와 QVideoFrame, 임시 더미 파일로 확인
"""

import os
import tempfile
import time
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtMultimedia import QMediaPlayer, QVideoFrame
from PyQt6.QtWidgets import QApplication

from src.gui.dialogs.frame_compare_playback_rules import MIN_RENDER_INTERVAL_SEC
from src.gui.dialogs.frame_compare_player import ComparePlaybackEngine
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _image(width: int = 320, height: int = 180, color: str = "#FF0000") -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


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

    def test_loaded_media_applies_the_position_requested_by_load_once(self) -> None:
        """로드 전에 지정한 시점을 미디어를 읽은 뒤 한 번만 적용하는지 검증"""
        with mock.patch.object(self.engine, "seek") as seek:
            self.engine.load(
                original_path=self.original,
                compared_path=self.compared,
                tier_key="720p",
                frame_width=1600,
                frame_height=900,
                position_ms=25000,
            )
            self.engine._on_primary_status(QMediaPlayer.MediaStatus.BufferedMedia)
            self.engine._on_primary_status(QMediaPlayer.MediaStatus.LoadedMedia)
        seek.assert_called_once_with(25000)

    def test_stop_clears_the_playing_flag_before_releasing_the_players(self) -> None:
        """정지 중 위치 신호가 창 시점을 덮어쓰지 않게 재생 상태를 먼저 내리는지 검증"""
        seen: list[bool] = []
        self.engine._playing = True
        with mock.patch.object(
            self.engine, "_release_player", side_effect=lambda _player: seen.append(self.engine._playing)
        ):
            self.engine.stop()
        self.assertEqual(seen, [False, False])
        self.assertIsNone(self.engine._pending_position_ms)

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

