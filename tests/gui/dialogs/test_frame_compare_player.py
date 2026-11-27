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

from src.core.models import ActiveFrame
from src.gui.dialogs.frame_compare_playback_rules import (
    MIN_RENDER_INTERVAL_SEC,
    PAIR_BUFFER_FRAMES,
)
from src.gui.dialogs.frame_compare_player import ComparePlaybackEngine
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])

# - 30fps 원본 한 프레임의 마이크로초. 합성 프레임 시각을 실제 재생과 같게 맞춤
_FRAME_US = 33_333


def _image(width: int = 320, height: int = 180, color: str = "#FF0000") -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


def _counter_frame(index: int, width: int = 64, height: int = 36) -> QVideoFrame:
    """프레임 번호를 좌상단 색에 실은 합성 프레임. 시작 시각도 번호에 맞춤"""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(index % 256, index // 256, 0))
    frame = QVideoFrame(image)
    frame.setStartTime(index * _FRAME_US)
    return frame


def _counter(image: QImage) -> int:
    """합성 프레임에 실린 번호를 읽음"""
    color = image.pixelColor(0, 0)
    return color.red() + color.green() * 256


class _Clock:
    """time.monotonic을 대신하는 시험용 시계"""

    def __init__(self, start: float = 100.0) -> None:
        self.value = start

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


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
        # - 소스를 쥔 채 재생기를 파괴하면 Qt FFmpeg 백엔드가 접근 위반으로 죽으므로 먼저 비움
        self.addCleanup(self.engine.stop)
        self.frames: list[tuple[QImage, QImage]] = []
        self.engine.frames_ready.connect(
            lambda original, compared: self.frames.append((original, compared))
        )

    def _load(
        self,
        tier_key: str | None = "720p",
        *,
        crop: ActiveFrame | None = None,
        frame_width: int = 1600,
        frame_height: int = 900,
    ) -> None:
        self.engine.load(
            original_path=self.original,
            compared_path=self.compared,
            tier_key=tier_key,
            frame_width=frame_width,
            frame_height=frame_height,
            position_ms=0,
            crop=crop,
        )
        self.engine._last_render = 0.0

    def test_downscale_mode_emits_the_roundtrip_pair(self) -> None:
        self._load()
        self.engine._playing = True
        self.engine._on_primary_frame(QVideoFrame(_image(1600, 900)))
        self.assertEqual(len(self.frames), 1)
        self.assertEqual(self.frames[0][0].size(), self.frames[0][1].size())

    def test_downscale_mode_crops_the_active_region_before_the_roundtrip(self) -> None:
        """축소 재생도 잘라낸 활성 화면에서 왕복 프레임을 만드는지 검증"""
        self._load(crop=ActiveFrame(800, 450, 400, 200), frame_width=800, frame_height=450)
        self.engine._playing = True
        self.engine._on_primary_frame(QVideoFrame(_image(1600, 900)))
        self.assertEqual(len(self.frames), 1)
        original, compared = self.frames[0]
        self.assertEqual((original.width(), original.height()), (800, 450))
        self.assertEqual((compared.width(), compared.height()), (800, 450))

    def test_dual_mode_crops_only_the_primary_frame(self) -> None:
        """결과 파일과의 재생 비교는 원본 프레임에만 활성 화면 크롭을 적용하는지 검증"""
        self._load(None, crop=ActiveFrame(800, 450, 400, 200), frame_width=800, frame_height=450)
        self.engine._playing = True
        self.engine._on_primary_frame(QVideoFrame(_image(1600, 900)))
        self.engine._last_render = 0.0
        self.engine._on_secondary_frame(QVideoFrame(_image(1216, 2160, "#00FF00")))
        self.assertEqual(len(self.frames), 1)
        original, compared = self.frames[0]
        self.assertEqual((original.width(), original.height()), (800, 450))
        self.assertEqual((compared.width(), compared.height()), (1216, 2160))

    def test_out_of_range_crop_keeps_the_original_frame_size(self) -> None:
        """범위를 벗어난 활성 화면 크롭이 들어와도 원본 프레임을 그대로 쓰는지 검증"""
        self._load(crop=ActiveFrame(800, 450, 1000, 700), frame_width=800, frame_height=450)
        self.engine._playing = True
        self.engine._on_primary_frame(QVideoFrame(_image(1600, 900)))
        self.assertEqual(len(self.frames), 1)
        original, compared = self.frames[0]
        self.assertEqual((original.width(), original.height()), (1600, 900))
        self.assertEqual((compared.width(), compared.height()), (800, 450))

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

    def test_burst_of_one_side_does_not_starve_the_other_side(self) -> None:
        """5배속처럼 양쪽이 계속 프레임을 보내면 어느 쪽도 멈추지 않는지 검증

        - 수정 전에는 두 재생기가 같은 30fps 게이트를 다투어, 게이트를 이긴 쪽만 갱신되고
          반대쪽은 직전 이미지를 되풀이해 한쪽만 움직였다
        """
        self._load(None)
        self.engine._playing = True
        clock = _Clock()
        with mock.patch("src.gui.dialogs.frame_compare_player.time.monotonic", side_effect=clock.now):
            for tick in range(5):
                for offset in range(5):
                    index = tick * 5 + offset
                    self.engine._on_primary_frame(_counter_frame(index))
                    self.engine._on_secondary_frame(_counter_frame(index))
                clock.advance(MIN_RENDER_INTERVAL_SEC * 1.5)
        counters = [(_counter(original), _counter(compared)) for original, compared in self.frames]
        self.assertEqual(len(counters), 5)
        for index, (original, compared) in enumerate(counters):
            self.assertLessEqual(abs(original - compared), 1)
            if index == 0:
                continue
            previous = counters[index - 1]
            self.assertGreater(original, previous[0])
            self.assertGreater(compared, previous[1])

    def test_newest_frame_of_each_side_is_paired_on_every_tick(self) -> None:
        """한 틱에서 양쪽이 여러 장을 보내도 내보낸 짝은 각 쪽 최신 프레임인지 검증"""
        self._load(None)
        self.engine._playing = True
        clock = _Clock()
        with mock.patch("src.gui.dialogs.frame_compare_player.time.monotonic", side_effect=clock.now):
            for index in range(3):
                self.engine._on_secondary_frame(_counter_frame(index))
            for index in range(3):
                self.engine._on_primary_frame(_counter_frame(index))
                clock.advance(MIN_RENDER_INTERVAL_SEC * 1.5)
        counters = [(_counter(original), _counter(compared)) for original, compared in self.frames]
        self.assertEqual(counters, [(0, 0), (1, 1), (2, 2)])

    def test_pair_takes_the_closest_secondary_frame_in_time(self) -> None:
        """종 프레임이 여러 장 쌓이면 주 프레임과 시작 시각이 가장 가까운 한 장을 고르는지 검증"""
        self._load(None)
        self.engine._playing = True
        self.engine._last_render = 0.0
        for index in (10, 11, 12, 13, 14, 15):
            self.engine._on_secondary_frame(_counter_frame(index))
        self.engine._on_primary_frame(_counter_frame(13))
        self.assertEqual(len(self.frames), 1)
        original, compared = self.frames[0]
        self.assertEqual(_counter(original), 13)
        self.assertEqual(_counter(compared), 13)

    def test_pair_is_skipped_when_no_secondary_frame_matches_in_time(self) -> None:
        """허용 폭을 넘게 어긋난 종 프레임만 있으면 어긋난 짝 대신 직전 화면을 두는지 검증"""
        self._load(None)
        self.engine._playing = True
        self.engine._last_render = 0.0
        self.engine._on_secondary_frame(_counter_frame(0))
        self.engine._on_primary_frame(_counter_frame(30))
        self.assertEqual(self.frames, [])

    def test_render_interval_gate_still_limits_two_file_pairs(self) -> None:
        """두 파일 비교도 같은 간격 안에서는 두 번 변환하지 않는지 검증"""
        self._load(None)
        self.engine._playing = True
        self.engine._last_render = time.monotonic()
        self.engine._on_primary_frame(_counter_frame(0))
        self.engine._on_secondary_frame(_counter_frame(0))
        self.assertEqual(self.frames, [])

    def test_same_pair_is_not_rendered_twice(self) -> None:
        """이미 내보낸 짝을 다시 변환해 그리지 않는지 검증"""
        self._load(None)
        self.engine._playing = True
        clock = _Clock()
        with mock.patch(
            "src.gui.dialogs.frame_compare_player.time.monotonic", side_effect=clock.now
        ):
            self.engine._on_primary_frame(_counter_frame(0))
            self.engine._on_secondary_frame(_counter_frame(0))
            clock.advance(MIN_RENDER_INTERVAL_SEC * 1.5)
            self.engine._on_secondary_frame(_counter_frame(0))
        self.assertEqual(len(self.frames), 1)

    def test_secondary_buffer_keeps_only_recent_frames(self) -> None:
        """종 프레임 보관이 정해진 장수를 넘지 않는지 검증"""
        self._load(None)
        self.engine._playing = True
        for index in range(PAIR_BUFFER_FRAMES + 5):
            self.engine._on_secondary_frame(_counter_frame(index))
        self.assertEqual(len(self.engine._secondary_frames), PAIR_BUFFER_FRAMES)

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
        with mock.patch.object(self.engine._primary, "duration", return_value=8000):
            self.engine._on_primary_status(QMediaPlayer.MediaStatus.EndOfMedia)
            self.engine._on_secondary_status(QMediaPlayer.MediaStatus.EndOfMedia)
        self.engine._on_secondary_status(QMediaPlayer.MediaStatus.LoadedMedia)
        self.assertEqual(ends, [True])

    def test_end_of_media_parks_one_frame_inside_the_end(self) -> None:
        """끝에 정확히 두지 않아 마지막 프레임이 남는지 검증"""
        with (
            mock.patch.object(self.engine._primary, "duration", return_value=8000),
            mock.patch.object(self.engine._primary, "setPosition") as set_position,
        ):
            self.engine._on_primary_status(QMediaPlayer.MediaStatus.EndOfMedia)
        set_position.assert_called_once()
        parked = set_position.call_args.args[0]
        self.assertLess(parked, 8000)
        self.assertGreaterEqual(parked, 8000 - 34)

    def test_seek_clamps_one_frame_before_the_end(self) -> None:
        """끝을 넘는 이동 요청도 마지막 프레임 안쪽으로 제한하는지 검증"""
        with (
            mock.patch.object(self.engine._primary, "duration", return_value=8000),
            mock.patch.object(self.engine._primary, "setPosition") as set_position,
        ):
            self.engine.seek(99_000)
        self.assertEqual(set_position.call_args.args[0], 8000 - 33)

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

    def test_seek_while_loading_replaces_the_pending_position(self) -> None:
        """로딩 중 이동 요청이 대기 시점을 바꾸고 재생기는 건드리지 않는지 검증"""
        with mock.patch.object(self.engine._primary, "setPosition") as primary_seek:
            self.engine.load(
                original_path=self.original,
                compared_path=self.compared,
                tier_key="720p",
                frame_width=1600,
                frame_height=900,
                position_ms=25000,
            )
            self.engine.seek(40000)
            self.assertEqual(self.engine._pending_position_ms, 40000)
            primary_seek.assert_not_called()
            # - 읽기 완료 신호는 BufferedMedia와 LoadedMedia에서 모두 오지만 대기 시점은 한 번만 씀
            self.engine._on_primary_status(QMediaPlayer.MediaStatus.BufferedMedia)
            self.engine._on_primary_status(QMediaPlayer.MediaStatus.LoadedMedia)
        primary_seek.assert_called_once_with(40000)
        self.assertIsNone(self.engine._pending_position_ms)

    def test_roundtrip_uses_the_configured_scale_algorithm(self) -> None:
        """재생 축소 왕복이 지정한 보간 방식을 그대로 넘기는지 검증"""
        self._load()
        self.engine._scale_algorithm = "lanczos"
        self.engine._playing = True
        with mock.patch(
            "src.gui.dialogs.frame_compare_player.roundtrip_compare_frame",
            side_effect=lambda image, *_a, **_k: image,
        ) as roundtrip:
            self.engine._on_primary_frame(QVideoFrame(_image(1600, 900)))
        self.assertEqual(roundtrip.call_args.kwargs["algorithm"], "lanczos")

    def test_load_keeps_the_scale_algorithm_for_playback(self) -> None:
        """세션이 넘긴 보간 방식을 로드가 보관하는지 검증"""
        self.engine.load(
            original_path=self.original,
            compared_path=self.compared,
            tier_key="720p",
            frame_width=1600,
            frame_height=900,
            position_ms=0,
            scale_algorithm="spline",
        )
        self.assertEqual(self.engine._scale_algorithm, "spline")

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
        self.engine._secondary_frames.append(_counter_frame(0))
        with (
            mock.patch.object(self.engine._primary, "position", return_value=5000),
            mock.patch.object(self.engine._secondary, "setPosition") as set_position,
            mock.patch(
                "src.gui.dialogs.frame_compare_player.time.monotonic", side_effect=[100.0, 100.1]
            ),
        ):
            self.engine._sync_secondary()
            self.engine._sync_secondary()
        set_position.assert_called_once_with(5000)

    def test_secondary_sync_ignores_small_frame_time_gaps(self) -> None:
        """원본 두 장 안쪽의 시각 차는 보정하지 않는지 검증"""
        self.engine._dual = True
        self.engine._playing = True
        self.engine._secondary_frames.append(_counter_frame(0))
        with (
            mock.patch.object(self.engine._primary, "position", return_value=50),
            mock.patch.object(self.engine._secondary, "setPosition") as set_position,
        ):
            self.engine._sync_secondary()
        set_position.assert_not_called()

    def test_secondary_sync_seeks_to_the_primary_frame_time_and_drops_stale_frames(self) -> None:
        """보정은 주 프레임 시각으로 이동하고 이전 위치의 종 프레임을 버리는지 검증"""
        self.engine._dual = True
        self.engine._playing = True
        self.engine._primary_frame = _counter_frame(100)
        self.engine._secondary_frames.append(_counter_frame(10))
        with mock.patch.object(self.engine._secondary, "setPosition") as set_position:
            self.engine._sync_secondary()
        set_position.assert_called_once_with(3_333)
        self.assertEqual(len(self.engine._secondary_frames), 0)

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
