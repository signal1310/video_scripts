"""내장 미리 보기의 일반 모드 재생 세션: 재생기, 오디오, 공유 파일 핸들, 읽은 뒤 시점 적용

- 하드웨어 가속 `QVideoWidget`으로 재생하고, 비교 모드로 바꿀 때 소스와 파일 핸들을 놓음
- 재생 중에도 다른 프로그램이 파일 이름을 바꾸거나 지울 수 있도록 공유 모드 핸들로 염
- 미디어를 읽기 전에 지정한 시점은 읽은 뒤 상태 신호에서 한 번만 적용
"""

from __future__ import annotations

from PyQt6.QtCore import QFile, QObject, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget

from src.core.settings.ui_state import PREVIEW_VOLUME_MAX
from src.gui.shared_file_handle import open_shared_read_file


class PreviewPlayback(QObject):
    """일반 미리 보기 재생기와 오디오 출력, 공유 파일 핸들을 관리하는 세션

    - 재생기 상태를 신호로 내보내 창의 공용 하단 바가 표시만 맞추게 함
    - 파일 핸들은 소스를 비운 뒤에만 닫아 재생기가 놓기 전에 지워지는 일을 막음
    """

    position_changed = pyqtSignal(int)
    duration_changed = pyqtSignal(int)
    playing_changed = pyqtSignal(bool)
    error_changed = pyqtSignal(bool)

    def __init__(self, video: QVideoWidget, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending_position_ms: int | None = None
        self._source_file: QFile | None = None
        self._audio = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(video)
        self._player.positionChanged.connect(lambda value: self.position_changed.emit(value))
        self._player.durationChanged.connect(lambda value: self.duration_changed.emit(value))
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.errorOccurred.connect(self._on_error)

    @property
    def player(self) -> QMediaPlayer:
        """재생기 본체, 메타데이터와 상태 조회에 씀"""
        return self._player

    @property
    def source_file(self) -> QFile | None:
        """지금 열어 둔 공유 모드 파일 핸들, 없으면 None"""
        return self._source_file

    @property
    def pending_position_ms(self) -> int | None:
        """미디어를 읽은 뒤 적용할 시점"""
        return self._pending_position_ms

    def queue_position(self, position_ms: int) -> None:
        """미디어를 읽은 뒤 적용할 시점을 보관"""
        self._pending_position_ms = max(0, position_ms)

    def clear_pending_position(self) -> None:
        """읽기 전에 지정했던 시점을 버림"""
        self._pending_position_ms = None

    def load(self, path: str, *, auto_play: bool) -> None:
        """영상 소스를 공유 모드 핸들로 열어 재생기에 넘김, auto_play가 거짓이면 일시중지로 둠

        - 공유 핸들을 만들지 못하면 경로 지정 방식으로 대체
        """
        self.release_source()
        source_file = open_shared_read_file(path)
        if source_file is None:
            self._player.setSource(QUrl.fromLocalFile(path))
        else:
            self._source_file = source_file
            self._player.setSourceDevice(source_file, QUrl.fromLocalFile(path))
        if auto_play:
            self._player.play()
        else:
            self._player.pause()

    def release_source(self) -> None:
        """재생기 소스를 비우고 열어 둔 파일 핸들을 닫음"""
        self._player.setSource(QUrl())
        if self._source_file is not None:
            self._source_file.close()
            self._source_file = None

    def stop(self) -> None:
        """재생을 멈추고 소스와 파일 핸들을 놓음"""
        self._player.stop()
        self.release_source()

    def pause(self) -> None:
        """재생을 일시중지"""
        self._player.pause()

    def play(self) -> None:
        """재생을 시작"""
        self._player.play()

    def position_ms(self) -> int:
        """현재 재생 위치(밀리초)"""
        return self._player.position()

    def duration_ms(self) -> int:
        """영상 길이(밀리초)"""
        return self._player.duration()

    def set_position(self, position_ms: int) -> None:
        """재생 위치를 옮김"""
        self._player.setPosition(position_ms)

    def set_rate(self, rate: float) -> None:
        """재생 배속을 적용"""
        self._player.setPlaybackRate(rate)

    def set_audio(self, volume_percent: int, muted: bool) -> None:
        """음량과 음소거를 오디오 출력에 적용"""
        self._audio.setVolume(volume_percent / PREVIEW_VOLUME_MAX)
        self._audio.setMuted(muted)

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        """재생 여부를 신호로 알림"""
        self.playing_changed.emit(state == QMediaPlayer.PlaybackState.PlayingState)

    def _on_error(self, error: QMediaPlayer.Error, _text: str) -> None:
        """오류 여부를 신호로 알림"""
        self.error_changed.emit(error != QMediaPlayer.Error.NoError)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        """미디어를 다 읽으면 대기하던 시점으로 옮기고, 끝에 닿으면 마지막 위치를 유지"""
        if status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ) and self._pending_position_ms is not None:
            pending = self._pending_position_ms
            self._pending_position_ms = None
            self._player.setPosition(pending)
        # - 종료 상태에서는 탐색해도 프레임이 다시 디코딩되지 않아 검은 화면이 남음, 일시중지 상태에서는 탐색 시 프레임이 렌더링됨
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._player.pause()
            self._player.setPosition(self._player.duration())
