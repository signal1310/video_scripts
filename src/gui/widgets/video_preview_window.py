"""선택 영상을 앱 안에서 재생하는 내장 미리 보기 창 모듈"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping

from PyQt6.QtCore import QEvent, QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QHideEvent, QKeyEvent, QKeySequence
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.messages import format_preview_start
from src.core.shortcuts import normalize_shortcut
from src.core.ui_state import PREVIEW_VOLUME_MAX
from src.gui.widgets.video_preview_controls import PlaybackControls

logger = logging.getLogger(__name__)

PREVIEW_WINDOW_WIDTH: int = 960
PREVIEW_WINDOW_HEIGHT: int = 540
STEP_PREVIOUS: int = -1
STEP_NEXT: int = 1
_MS_PER_SECOND: int = 1000
_ACTION_SEEK_BACKWARD: str = "preview.seek_backward"
_ACTION_SEEK_FORWARD: str = "preview.seek_forward"
_ACTION_PREVIOUS: str = "preview.previous"
_ACTION_NEXT: str = "preview.next"
_ACTION_TOGGLE_PLAYBACK: str = "preview.toggle_playback"
_ACTION_TOGGLE_MUTE: str = "preview.toggle_mute"
_ACTION_TOGGLE_FULLSCREEN: str = "preview.toggle_fullscreen"
_TEXT_INPUT_TYPES: tuple[type[QWidget], ...] = (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)
_PLAYBACK_ERROR_TEXT: str = "이 영상은 재생할 수 없습니다. 영상 코덱이 설치되어 있는지 확인해 주세요."


def _blocks_preview_keys() -> bool:
    """텍스트 입력, 열린 팝업, 모달 대화상자가 키를 직접 받아야 하면 True"""
    if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
        return True
    return isinstance(QApplication.focusWidget(), _TEXT_INPUT_TYPES)


def clamp_seek_position(current_ms: int, delta_ms: int, duration_ms: int) -> int:
    """현재 위치에서 delta_ms만큼 옮긴 위치를 영상 범위 안으로 제한, 길이를 모르면 현재 위치 유지"""
    if duration_ms <= 0:
        return current_ms
    return min(max(current_ms + delta_ms, 0), duration_ms)


class VideoPreviewWindow(QWidget):
    """재생 조작 바를 갖추고 Space, Esc로 닫는 미리 보기 창

    좌우 방향키는 구간 이동, 위아래 방향키는 목록의 이전, 다음 영상 요청(step_requested)으로 넘김.
    영상 목록은 창이 아니라 호출자(표)가 관리
    """

    step_requested = pyqtSignal(int)

    def __init__(
        self,
        parent: QWidget | None,
        get_seek_seconds: Callable[[], int],
        get_audio: Callable[[], tuple[int, bool]],
        set_audio: Callable[[int, bool], None],
        get_shortcuts: Callable[[], Mapping[str, str]],
        forward_key: Callable[[str], bool],
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self._forward_key = forward_key
        self._path: str = ""
        self._get_seek_seconds = get_seek_seconds
        self._get_shortcuts = get_shortcuts
        self._set_audio = set_audio
        self._key_filter_installed: bool = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(PREVIEW_WINDOW_WIDTH, PREVIEW_WINDOW_HEIGHT)

        self._video = QVideoWidget(self)
        self._error_label = QLabel(_PLAYBACK_ERROR_TEXT, self)
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        self._controls = PlaybackControls(self)

        self._audio = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video)
        self._player.errorOccurred.connect(self._on_playback_error)
        self._player.positionChanged.connect(self._controls.set_position)
        self._player.durationChanged.connect(self._controls.set_duration)
        self._player.playbackStateChanged.connect(
            lambda state: self._controls.set_playing(
                state == QMediaPlayer.PlaybackState.PlayingState
            )
        )
        self._controls.toggle_requested.connect(self._toggle_playback)
        self._controls.seek_requested.connect(self._player.setPosition)
        volume_percent, muted = get_audio()
        self._controls.set_audio_state(volume_percent, muted)
        self._apply_audio(volume_percent, muted)
        self._controls.volume_changed.connect(self._on_volume_changed)
        self._controls.mute_toggled.connect(self._on_mute_toggled)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._video, 1)
        layout.addWidget(self._error_label)
        layout.addWidget(self._controls)

    @property
    def current_path(self) -> str:
        """지금 로드된 영상 경로, 아직 없으면 빈 문자열"""
        return self._path

    def show_path(self, path: str) -> None:
        """영상을 로드해 재생하고 창을 앞으로 표시"""
        if not path:
            return
        self.load_path(path)
        self.show()
        self._install_key_filter()
        self.raise_()
        self.activateWindow()

    def load_path(self, path: str) -> None:
        """창 표시 상태는 바꾸지 않고 영상만 교체해 재생, 표 선택 이동에 따라 호출"""
        if not path:
            return
        self._path = path
        self._error_label.hide()
        # - 재생기 원시 출력(FFmpeg)은 시각 없이 출력되므로 소스 교체 직전에 시각 줄을 남겨 묶음의 시작을 표시
        logger.info("%s", format_preview_start(os.path.basename(path)))
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()
        self.setWindowTitle(os.path.basename(path))

    def hideEvent(self, event: QHideEvent | None) -> None:
        """창을 숨길 때 키 가로채기를 해제하고 재생을 멈춘 뒤 영상 파일을 놓음"""
        self._remove_key_filter()
        self._player.stop()
        # - 소스를 비워야 재생기가 파일 핸들을 놓아 외부에서 삭제, 이동이 가능함
        self._player.setSource(QUrl())
        self._path = ""
        super().hideEvent(event)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """창이 비활성이어도 열려 있는 동안 미리 보기 명령 키를 처리, 텍스트 입력과 팝업, 모달 입력은 가로채지 않음"""
        if a1 is None or a1.type() != QEvent.Type.KeyPress or not isinstance(a1, QKeyEvent):
            return super().eventFilter(a0, a1)
        if not self.isVisible() or _blocks_preview_keys():
            return super().eventFilter(a0, a1)
        key = a1.key()
        if self.isActiveWindow() and key in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
            if key == Qt.Key.Key_Escape and self.isFullScreen():
                self.showNormal()
            else:
                self.hide()
            return True
        action_id = self._action_for_key(a1)
        if action_id is not None:
            self._run_action(action_id)
            return True
        # - 활성 창일 때만 앱 명령으로 넘김, 비활성이면 활성 창이 이미 그 단축키를 처리함
        if self.isActiveWindow() and self._forward_key(QKeySequence(a1.keyCombination()).toString()):
            return True
        return super().eventFilter(a0, a1)

    def _install_key_filter(self) -> None:
        if self._key_filter_installed:
            return
        app = QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._key_filter_installed = True

    def _remove_key_filter(self) -> None:
        if not self._key_filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._key_filter_installed = False

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """Space, Esc는 고정으로 닫고, 나머지 키는 단축키 설정의 미리 보기 명령으로 처리"""
        if event is None:
            return
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
            self.hide()
            event.accept()
            return
        action_id = self._action_for_key(event)
        if action_id is None:
            super().keyPressEvent(event)
            return
        self._run_action(action_id)
        event.accept()

    def _action_for_key(self, event: QKeyEvent) -> str | None:
        """눌린 키와 같은 단축키를 가진 미리 보기 명령 식별자, 없으면 None"""
        pressed = normalize_shortcut(QKeySequence(event.keyCombination()).toString())
        if not pressed:
            return None
        for action_id, key in self._get_shortcuts().items():
            if key and normalize_shortcut(key) == pressed and action_id.startswith("preview."):
                return action_id
        return None

    def _run_action(self, action_id: str) -> None:
        if action_id == _ACTION_SEEK_BACKWARD:
            self._seek_by(-self._seek_ms())
        elif action_id == _ACTION_SEEK_FORWARD:
            self._seek_by(self._seek_ms())
        elif action_id == _ACTION_PREVIOUS:
            self.step_requested.emit(STEP_PREVIOUS)
        elif action_id == _ACTION_NEXT:
            self.step_requested.emit(STEP_NEXT)
        elif action_id == _ACTION_TOGGLE_PLAYBACK:
            self._toggle_playback()
        elif action_id == _ACTION_TOGGLE_MUTE:
            self._controls.toggle_mute()
        elif action_id == _ACTION_TOGGLE_FULLSCREEN:
            self._toggle_fullscreen()

    def _toggle_fullscreen(self) -> None:
        """전체화면이면 이전 창 크기로 되돌리고, 아니면 전체화면으로 전환"""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _seek_ms(self) -> int:
        return self._get_seek_seconds() * _MS_PER_SECOND

    def _seek_by(self, delta_ms: int) -> None:
        """현재 재생 위치에서 delta_ms만큼 이동, 영상 길이를 모르면 무시"""
        target = clamp_seek_position(self._player.position(), delta_ms, self._player.duration())
        self._player.setPosition(target)

    def _apply_audio(self, volume_percent: int, muted: bool) -> None:
        self._audio.setVolume(volume_percent / PREVIEW_VOLUME_MAX)
        self._audio.setMuted(muted)

    def _on_volume_changed(self, _volume_percent: int) -> None:
        self._save_audio()

    def _on_mute_toggled(self, _muted: bool) -> None:
        self._save_audio()

    def _save_audio(self) -> None:
        """바뀐 음량과 음소거 상태를 오디오 출력에 적용하고 호출자에게 저장을 요청"""
        volume_percent, muted = self._controls.audio_state()
        self._apply_audio(volume_percent, muted)
        self._set_audio(volume_percent, muted)

    def _toggle_playback(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_playback_error(self, error: QMediaPlayer.Error, _error_string: str) -> None:
        """재생 오류 발생 시 안내 문구를 영상 영역 아래에 표시"""
        if error != QMediaPlayer.Error.NoError:
            self._error_label.show()
