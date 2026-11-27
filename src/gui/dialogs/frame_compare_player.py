"""화질 비교 뷰의 재생 엔진: 주/종 재생기, 공유 파일 핸들, 프레임 쌍, 축소 왕복 변환

- 축소 비교는 원본 한 파일만 재생하고 받은 프레임을 규격으로 줄였다 되돌려 비교 쌍을 만듦
- 원본 vs 결과 비교는 원본이 주 재생기, 결과가 오디오 없는 종 재생기이며, 원본 프레임을
  결과를 만든 활성 화면으로 잘라 두 파일의 화소를 1:1로 맞춤
- 재생 중에만 프레임을 내보내, 일시중지 뒤에는 화면이 정밀 추출 프레임으로 안착하게 함
- 재생 중에도 다른 프로그램이 파일 이름을 바꾸거나 지울 수 있도록 공유 모드 핸들로 염
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QFile, QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer, QVideoFrame, QVideoSink

from src.core.analysis.frame_compare import clamp_end_position_ms
from src.core.models import ActiveFrame
from src.core.resolution_tiers import DEFAULT_SCALE_ALGORITHM
from src.core.settings.ui_state import PREVIEW_VOLUME_MAX
from src.gui.dialogs.frame_compare_playback_rules import (
    MIN_RENDER_INTERVAL_SEC,
    SYNC_COOLDOWN_SEC,
    restart_from_start,
    roundtrip_compare_frame,
    sync_drift_exceeded,
)
from src.gui.shared_file_handle import open_shared_read_file

SYNC_CHECK_INTERVAL_MS: int = 200


def crop_active_frame(image: QImage, crop: ActiveFrame | None) -> QImage:
    """활성 화면 영역만 남긴 이미지, 범위를 벗어난 크롭이면 원본 이미지

    - 디코딩 프레임 크기가 캐시와 다르면 잘라내지 않아 재생이 깨지지 않게 함
    """
    if crop is None:
        return image
    if (
        crop.x < 0
        or crop.y < 0
        or crop.width <= 0
        or crop.height <= 0
        or crop.x + crop.width > image.width()
        or crop.y + crop.height > image.height()
    ):
        return image
    return image.copy(crop.x, crop.y, crop.width, crop.height)


class ComparePlaybackEngine(QObject):
    """화질 비교 창의 재생기 두 개와 프레임 쌍 전달, 위치 보정

    - 재생 여부는 주 재생기 상태를 따르며, 재생이 멈추면 프레임을 내보내지 않음
    - 파일 핸들은 소스를 비운 뒤에만 닫아 재생기가 놓기 전에 지워지는 일을 막음
    """

    frames_ready = pyqtSignal(object, object)
    position_changed = pyqtSignal(int)
    playing_changed = pyqtSignal(bool)
    end_reached = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tier_key: str | None = None
        self._crop: ActiveFrame | None = None
        self._frame_width: int = 0
        self._frame_height: int = 0
        self._scale_algorithm: str = DEFAULT_SCALE_ALGORITHM
        self._playing: bool = False
        self._rate: float = 1.0
        self._volume_percent: int = PREVIEW_VOLUME_MAX
        self._muted: bool = False
        self._ready: bool = False
        self._dual: bool = False
        self._pending_position_ms: int | None = None
        self._source_files: list[QFile] = []
        self._primary_image: QImage | None = None
        self._secondary_image: QImage | None = None
        self._processing_frame: bool = False
        self._pending_frame: QVideoFrame | None = None
        self._last_render: float = 0.0
        self._sync_cooldown_until: float = 0.0

        self._audio = QAudioOutput(self)
        self._primary = QMediaPlayer(self)
        self._primary_sink = QVideoSink(self)
        self._primary.setAudioOutput(self._audio)
        self._primary.setVideoOutput(self._primary_sink)
        self._secondary = QMediaPlayer(self)
        self._secondary_sink = QVideoSink(self)
        self._secondary.setVideoOutput(self._secondary_sink)
        self._primary_sink.videoFrameChanged.connect(self._on_primary_frame)
        self._secondary_sink.videoFrameChanged.connect(self._on_secondary_frame)
        self._primary.positionChanged.connect(self._on_primary_position)
        self._primary.playbackStateChanged.connect(self._on_playback_state)
        self._primary.mediaStatusChanged.connect(self._on_primary_status)
        self._secondary.mediaStatusChanged.connect(self._on_secondary_status)
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(SYNC_CHECK_INTERVAL_MS)
        self._sync_timer.timeout.connect(self._sync_secondary)

    def load(
        self,
        *,
        original_path: str,
        compared_path: str,
        tier_key: str | None,
        frame_width: int,
        frame_height: int,
        position_ms: int,
        crop: ActiveFrame | None = None,
        scale_algorithm: str = DEFAULT_SCALE_ALGORITHM,
    ) -> None:
        """재생할 파일과 축소 왕복 규격, 잘라낼 활성 화면, 축소 보간 방식을 정하고 지정 위치로 옮김"""
        self.stop()
        self._tier_key = tier_key
        self._crop = crop
        self._frame_width = frame_width
        self._frame_height = frame_height
        self._scale_algorithm = scale_algorithm
        self._dual = tier_key is None
        self._open_source(self._primary, original_path)
        if self._dual:
            self._open_source(self._secondary, compared_path)
        self._primary.setPlaybackRate(self._rate)
        self._secondary.setPlaybackRate(self._rate)
        self._audio.setVolume(self._volume_percent / PREVIEW_VOLUME_MAX)
        self._audio.setMuted(self._muted)
        self._ready = True
        # - 소스를 읽기 전의 위치 지정은 무시되므로 미디어를 읽은 뒤 상태 신호에서 한 번 적용
        self._pending_position_ms = max(0, position_ms)

    def play(self) -> None:
        """끝까지 본 상태면 처음으로 돌린 뒤 두 재생기를 재생"""
        if not self._ready:
            return
        if restart_from_start(
            self._primary.position(), self._primary.duration(), self._frame_rate()
        ):
            self.seek(0)
        self._primary.play()
        if self._dual:
            self._secondary.play()

    def pause(self) -> None:
        """두 재생기를 멈추고 위치 보정 타이머를 멈춤"""
        self._primary.pause()
        if self._dual:
            self._secondary.pause()
        self._sync_timer.stop()

    def toggle(self) -> None:
        """재생과 일시중지를 번갈아 전환"""
        if self._playing:
            self.pause()
        else:
            self.play()

    def set_rate(self, rate: float) -> None:
        """재생 배속을 두 재생기에 적용"""
        self._rate = rate
        self._primary.setPlaybackRate(rate)
        self._secondary.setPlaybackRate(rate)

    def seek(self, position_ms: int) -> None:
        """두 재생기를 같은 위치로 옮기되 끝 여유 안쪽으로 제한

        - 정확히 끝에 두면 디코딩할 프레임이 없어 마지막 화면이 비므로 한 프레임 남김
        - 미디어를 읽는 중이면 재생기에 지정한 위치가 무시되므로 대기 시점만 바꿔 읽은 뒤 한 번 적용
        """
        target = self._clamp_position(position_ms)
        if self._pending_position_ms is not None:
            self._pending_position_ms = target
            return
        self._primary.setPosition(target)
        if self._dual:
            self._secondary.setPosition(target)

    def _clamp_position(self, position_ms: int) -> int:
        """요청 위치를 [0, 길이 - 한 프레임]으로 제한, 길이를 모르면 0 이상으로만 제한"""
        return clamp_end_position_ms(position_ms, self._primary.duration(), self._frame_rate())

    def _frame_rate(self) -> float:
        """주 재생기 메타데이터 FPS, 없으면 30fps"""
        metadata_rate = self._primary.metaData().value(QMediaMetaData.Key.VideoFrameRate)
        return metadata_rate if isinstance(metadata_rate, float) and metadata_rate > 0 else 0.0

    def apply_audio(self, volume_percent: int, muted: bool) -> None:
        """음량과 음소거를 주 재생기 오디오에 적용"""
        self._volume_percent = volume_percent
        self._muted = muted
        self._audio.setVolume(volume_percent / PREVIEW_VOLUME_MAX)
        self._audio.setMuted(muted)

    def stop(self) -> None:
        """재생을 멈추고 소스를 비운 뒤 공유 파일 핸들을 닫음"""
        # - 해제 과정에서 재생기가 위치를 0으로 알려도 창 시점을 덮어쓰지 않게 재생 상태를 먼저 내림
        self._playing = False
        self._sync_timer.stop()
        self._release_player(self._primary)
        self._release_player(self._secondary)
        for source_file in self._source_files:
            source_file.close()
        self._source_files.clear()
        self._ready = False
        self._pending_position_ms = None
        self._crop = None
        self._primary_image = None
        self._secondary_image = None
        self._pending_frame = None
        self._sync_cooldown_until = 0.0

    def is_playing(self) -> bool:
        """재생 중인지"""
        return self._playing

    def position_ms(self) -> int:
        """주 재생기의 현재 위치(밀리초)"""
        return self._primary.position()

    def rate(self) -> float:
        """현재 재생 배속"""
        return self._rate

    def muted(self) -> bool:
        """현재 음소거 여부"""
        return self._muted

    def volume_percent(self) -> int:
        """현재 음량(0~100)"""
        return self._volume_percent

    def _open_source(self, player: QMediaPlayer, path: str) -> None:
        """공유 모드 핸들로 파일을 열어 재생기에 장치로 넘김

        - 공유 핸들을 만들지 못하면 경로 지정 방식으로 대체
        """
        source_file = open_shared_read_file(path)
        if source_file is None:
            player.setSource(QUrl.fromLocalFile(path))
            return
        self._source_files.append(source_file)
        player.setSourceDevice(source_file, QUrl.fromLocalFile(path))

    def _release_player(self, player: QMediaPlayer) -> None:
        """재생기 소스를 비워 파일 핸들을 놓게 함"""
        player.stop()
        player.setSource(QUrl())

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        """주 재생기 상태가 바뀌면 재생 여부를 알리고 위치 보정 타이머를 맞춤"""
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._playing = playing
        if playing:
            self._sync_timer.start()
        else:
            self._sync_timer.stop()
        self.playing_changed.emit(playing)

    def _on_primary_position(self, position_ms: int) -> None:
        """주 재생기 위치를 화면에 알림"""
        if self._ready:
            self.position_changed.emit(position_ms)

    def _on_primary_status(self, status: QMediaPlayer.MediaStatus) -> None:
        """미디어를 읽으면 대기하던 시점으로 옮기고, 끝에 닿으면 일시중지로 마지막 위치를 유지"""
        if (
            status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia)
            and self._pending_position_ms is not None
        ):
            pending = self._pending_position_ms
            self._pending_position_ms = None
            self.seek(pending)
        if status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        self._primary.pause()
        self._primary.setPosition(self._clamp_position(self._primary.duration()))
        self.end_reached.emit()

    def _on_secondary_status(self, status: QMediaPlayer.MediaStatus) -> None:
        """종 재생기가 끝에 닿으면 마지막 프레임을 유지"""
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._secondary.pause()

    def _sync_secondary(self) -> None:
        """재생 중 위치 차가 배속 비례 기준을 넘으면 종 재생기를 맞추고 잠시 재시크를 쉼

        - 보정 시크 직후 쿨다운 동안은 다시 시크하지 않아 고배속에서 보정이 반복되지 않게 함
        """
        if not self._dual or not self._playing:
            return
        now = time.monotonic()
        if now < self._sync_cooldown_until:
            return
        primary_ms = self._primary.position()
        if sync_drift_exceeded(primary_ms, self._secondary.position(), self._rate):
            self._secondary.setPosition(primary_ms)
            self._sync_cooldown_until = now + SYNC_COOLDOWN_SEC

    def _on_primary_frame(self, frame: QVideoFrame) -> None:
        """주 재생기 프레임을 받아 비교 쌍을 만들고, 처리 중이면 최신 프레임만 남김"""
        if not self._playing:
            return
        if self._processing_frame:
            self._pending_frame = frame
            return
        self._processing_frame = True
        try:
            current: QVideoFrame | None = frame
            while current is not None:
                self._consume_primary_frame(current)
                current = self._pending_frame
                self._pending_frame = None
        finally:
            self._processing_frame = False

    def _render_due(self) -> bool:
        """직전 변환에서 30fps 간격이 지났는지, 아니면 이번 프레임을 버림"""
        now = time.monotonic()
        if now - self._last_render < MIN_RENDER_INTERVAL_SEC:
            return False
        self._last_render = now
        return True

    def _consume_primary_frame(self, frame: QVideoFrame) -> None:
        """프레임 한 장을 QImage로 바꿔 축소 쌍이나 두 파일 쌍으로 내보냄"""
        if not self._render_due():
            return
        image = frame.toImage()
        if image.isNull():
            return
        image = crop_active_frame(image, self._crop)
        if not self._dual:
            compared = roundtrip_compare_frame(
                image,
                self._frame_width,
                self._frame_height,
                self._tier_key or "",
                algorithm=self._scale_algorithm,
            )
            self.frames_ready.emit(image, compared)
            return
        self._primary_image = image
        secondary = self._secondary_image
        if secondary is not None:
            self.frames_ready.emit(image, secondary)

    def _on_secondary_frame(self, frame: QVideoFrame) -> None:
        """종 재생기 프레임을 보관하고 주 프레임과 짝지어 내보냄"""
        if not self._playing or not self._dual:
            return
        if not self._render_due():
            return
        image = frame.toImage()
        if image.isNull():
            return
        self._secondary_image = image
        primary = self._primary_image
        if primary is not None:
            self.frames_ready.emit(primary, image)

