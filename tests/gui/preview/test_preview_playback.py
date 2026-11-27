"""일반 미리 보기 재생 세션 검증: 공유 핸들 로드, 읽은 뒤 시점 적용, 끝 도달, 신호, 정리"""

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import QApplication

from src.gui.preview.preview_playback import PreviewPlayback
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


class PreviewPlaybackTests(GuiTestCase):
    """재생기 세션의 소스, 시점, 신호, 정리 동작"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "clip.mp4")
        with open(self.path, "wb") as handle:
            handle.write(b"not a real video")
        self.video = QVideoWidget()
        self.addCleanup(self.video.deleteLater)
        self.frame_rate = 30.0
        self.playback = PreviewPlayback(self.video, frame_rate=lambda: self.frame_rate)
        self.addCleanup(self.playback.deleteLater)
        # - 소스를 쥔 채 재생기를 파괴하면 Qt FFmpeg 백엔드가 접근 위반으로 죽으므로 먼저 비움
        self.addCleanup(self.playback.stop)

    def test_load_opens_a_shared_handle_and_release_closes_it(self) -> None:
        self.playback.load(self.path, auto_play=False)
        self.assertIsNotNone(self.playback.source_file)
        self.assertIs(self.playback.player.parent(), self.playback)
        self.playback.release_source()
        self.assertIsNone(self.playback.source_file)
        self.assertEqual(self.playback.player.position(), 0)

    def test_stop_and_release_clear_the_source(self) -> None:
        self.playback.load(self.path, auto_play=True)
        self.playback.stop()
        self.assertIsNone(self.playback.source_file)
        self.assertEqual(self.playback.player.playbackState(), QMediaPlayer.PlaybackState.StoppedState)

    def test_pending_position_is_applied_once_after_media_is_loaded(self) -> None:
        self.playback.load(self.path, auto_play=False)
        self.playback.queue_position(9000)
        self.assertEqual(self.playback.pending_position_ms, 9000)
        self.playback._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        self.assertIsNone(self.playback.pending_position_ms)
        self.playback.queue_position(1000)
        self.playback.clear_pending_position()
        self.assertIsNone(self.playback.pending_position_ms)
        self.playback._on_media_status_changed(QMediaPlayer.MediaStatus.BufferedMedia)

    def test_end_of_media_holds_the_last_frame_inside_the_end_margin(self) -> None:
        with (
            mock.patch.object(self.playback.player, "pause") as pause,
            mock.patch.object(self.playback.player, "setPosition") as set_position,
            mock.patch.object(self.playback.player, "duration", return_value=5000),
        ):
            self.playback._on_media_status_changed(QMediaPlayer.MediaStatus.EndOfMedia)
        pause.assert_called_once()
        parked = set_position.call_args.args[0]
        self.assertLess(parked, 5000)
        self.assertGreaterEqual(parked, 5000 - 34)

    def test_position_is_clamped_one_frame_before_the_end(self) -> None:
        with mock.patch.object(self.playback.player, "duration", return_value=5000):
            self.assertEqual(self.playback.clamp_position(9000), 4967)
            self.assertEqual(self.playback.clamp_position(-10), 0)
        with mock.patch.object(self.playback.player, "duration", return_value=0):
            self.assertEqual(self.playback.clamp_position(9000), 9000)

    def test_clamp_uses_the_window_frame_rate_for_the_end_margin(self) -> None:
        """60fps 영상을 표 FPS로 알면 끝 여유도 60fps 한 프레임(17ms)으로 줄이는지 검증"""
        self.frame_rate = 60.0
        with mock.patch.object(self.playback.player, "duration", return_value=5000):
            self.assertEqual(self.playback.clamp_position(9000), 4983)

    def test_state_and_error_are_reported(self) -> None:
        states: list[bool] = []
        errors: list[bool] = []
        self.playback.playing_changed.connect(states.append)
        self.playback.error_changed.connect(errors.append)
        self.playback._on_state_changed(QMediaPlayer.PlaybackState.PlayingState)
        self.playback._on_state_changed(QMediaPlayer.PlaybackState.PausedState)
        self.playback._on_error(QMediaPlayer.Error.NoError, "")
        self.playback._on_error(QMediaPlayer.Error.FormatError, "boom")
        self.assertEqual(states, [True, False])
        self.assertEqual(errors, [False, True])

    def test_position_rate_and_audio_are_forwarded(self) -> None:
        self.playback.set_rate(2.0)
        self.playback.set_audio(35, True)
        self.playback.set_position(0)
        self.playback.pause()
        self.playback.play()
        self.assertEqual(self.playback.player.playbackRate(), 2.0)
        self.assertGreaterEqual(self.playback.duration_ms(), 0)
        self.assertGreaterEqual(self.playback.position_ms(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
