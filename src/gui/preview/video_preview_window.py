"""선택 영상을 앱 안에서 재생하고 같은 창에서 화질 비교 뷰로 전환하는 미리 보기 창 모듈

- 일반 모드는 `QVideoWidget` 하드웨어 가속, 비교 모드는 `FrameCompareView` + PyAV 정밀 추출을 씀
- OS 최상위 창은 하나뿐이고, 하단 재생 바와 단축키 알림 토스트도 하나만 둠
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from typing import Protocol

from PyQt6 import sip
from PyQt6.QtCore import QByteArray, QEvent, QObject, QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import (
    QHideEvent,
    QKeyEvent,
    QKeySequence,
    QMoveEvent,
    QResizeEvent,
    QShowEvent,
)
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

from src.core.messages import format_preview_start
from src.core.models import ActiveFrame, SummaryReport, VideoEvaluation
from src.core.settings.ui_state import DEFAULT_PREVIEW_BACKGROUND, UiPreviewInfoState
from src.gui.dialogs.frame_compare_meta import CompareSession
from src.gui.dialogs.frame_compare_styles import build_compare_qss
from src.gui.dialogs.frame_compare_worker import FrameExtractor
from src.gui.media_stage import stage_background_notice, stage_color
from src.gui.preview import crop_resize_guard, preview_modes
from src.gui.preview import preview_keys as preview
from src.gui.preview.crop_edit import (
    CROP_EDIT_MARGIN_PX,
    HANDLE_EDGE_PX,
    full_active_frame,
    kept_label,
)
from src.gui.preview.crop_edit_session import (
    CROP_FULL_TIP,
    CROP_RESET_TIP,
    UNDO_DONE_NOTICE,
    CropEditSession,
)
from src.gui.preview.crop_hud import CropEditHud
from src.gui.preview.crop_overlay import CropOverlay
from src.gui.preview.preview_info_items import PreviewVideoInfo
from src.gui.preview.preview_info_presenter import PreviewInfoPresenter
from src.gui.preview.preview_playback import PreviewPlayback
from src.gui.preview.preview_window_owner import PreviewOwnerTracker
from src.gui.preview.stage import PreviewStage
from src.gui.preview.video_preview_controls import PlaybackControls
from src.gui.preview.video_preview_overlay import EDGE_MARGIN_PX, TOAST_DURATION_MS
from src.gui.styles.palettes import DARK_PALETTE, ThemePalette
from src.gui.theme import ThemeAwareMixin, build_stylesheet

logger = logging.getLogger(__name__)

PREVIEW_WINDOW_WIDTH: int = 960
PREVIEW_WINDOW_HEIGHT: int = 540
_MS_PER_SECOND: int = 1000
# - 아주 처음에서 닫은 영상은 다음에 열 때도 처음부터 보여 주고, 끝 부근은 본 것으로 보고 지움
_POSITION_MIN_SEC: float = 0.5
_POSITION_END_GAP_SEC: float = 1.0
# - `WM_NCHITTEST` 응답으로 돌려줄 클라이언트 영역 코드. 네이티브 창 없이도 같은 값을 씀
_HTCLIENT: int = crop_resize_guard.HTCLIENT
_PLAYBACK_ERROR_TEXT: str = "이 영상은 재생할 수 없습니다. 영상 코덱이 설치되어 있는지 확인해 주세요."
SAVED_MANUAL_FRAME_TEXT: str = "사용자 지정 활성 화면을 저장했습니다. 인코딩할 때 지정한 영역을 잘라냅니다."


class PositionMemory(Protocol):
    """앱 실행 중에만 영상별 재생 위치를 기억하는 저장소 인터페이스"""

    def recall(self, path: str) -> float | None:
        """기억한 재생 시점(초), 없으면 None"""
        ...

    def remember(self, path: str, position_sec: float) -> None:
        """재생 시점(초)을 저장"""
        ...

    def forget(self, path: str) -> None:
        """저장한 재생 시점을 지움"""
        ...


class VideoPreviewWindow(QWidget, ThemeAwareMixin):
    """일반 미리 보기와 화질 비교 뷰를 한 창에서 전환하는 미리 보기 창

    좌우 방향키는 구간 이동, 위아래 방향키는 목록의 이전, 다음 영상 요청(step_requested)으로 넘김.
    영상 목록은 창이 아니라 호출자(표)가 관리
    """

    step_requested = pyqtSignal(int)
    settings_requested = pyqtSignal()
    compare_requested = pyqtSignal(str, float)

    def __init__(
        self,
        get_seek_seconds: Callable[[], int],
        get_audio: Callable[[], tuple[int, bool]],
        set_audio: Callable[[int, bool], None],
        get_shortcuts: Callable[[], Mapping[str, str]],
        forward_key: Callable[[str], bool],
        get_info_state: Callable[[], UiPreviewInfoState],
        on_info_changed: Callable[[], None],
        get_playback_rate: Callable[[], float],
        set_playback_rate: Callable[[float], None],
        get_fullscreen: Callable[[], bool],
        on_fullscreen_changed: Callable[[bool], None],
        get_always_on_top: Callable[[], bool],
        on_always_on_top_changed: Callable[[bool], None],
        positions: PositionMemory,
        *,
        get_stage_background: Callable[[], str] | None = None,
        get_frame_outline: Callable[[], bool] | None = None,
        cycle_background: Callable[[], str] | None = None,
        get_compare_top_bar_collapsed: Callable[[], bool] | None = None,
        on_compare_top_bar_collapsed_changed: Callable[[bool], None] | None = None,
        apply_compare_manual_target: (
            Callable[[VideoEvaluation, str | None], bool] | None
        ) = None,
        apply_manual_active_frame: (
            Callable[[VideoEvaluation, ActiveFrame | None], None] | None
        ) = None,
        apply_crop_exclusion: Callable[[VideoEvaluation, bool], None] | None = None,
    ) -> None:
        # - 소유자 창은 컨트롤러가 정하므로 창 자체는 독립 최상위 창으로 만듦
        super().__init__(None, Qt.WindowType.Window)
        self._always_on_top = get_always_on_top()
        self._forward_key = forward_key
        self._path: str = ""
        self._positions = positions
        # - 읽기 전에 지정한 시점이 어느 영상의 것인지 구분해, 영상을 바꿀 때 이전 영상의 대기 시점을 버림
        self._pending_for: str | None = None
        self._get_seek_seconds = get_seek_seconds
        self._get_shortcuts = get_shortcuts
        self._get_audio = get_audio
        self._get_playback_rate = get_playback_rate
        self._get_fullscreen = get_fullscreen
        self._on_fullscreen_changed = on_fullscreen_changed
        self._on_always_on_top_changed = on_always_on_top_changed
        self._key_filter_installed: bool = False
        # - 창 플래그를 바꾸는 동안 발생하는 숨김 사건에서 재생과 파일 핸들을 건드리지 않게 하는 표시
        self._toggling_window_flag: bool = False
        # - 소유 창이 함께 닫히는 동안에는 소유 창을 앞으로 올리지 않아, 사라지는 창이 남지 않게 함
        self._owner = PreviewOwnerTracker(self, self._set_toggling_window_flag)
        self._video_info: PreviewVideoInfo | None = None
        self._compare_extractor: FrameExtractor | None = None
        # - 여백 미리 보기는 창을 열 때마다 꺼진 상태로 시작
        self._crop_enabled: bool = False
        # - 배경 색과 영상 테두리 접근자를 창이 기억하고 적용 때 읽음
        self._get_stage_background: Callable[[], str] = (
            get_stage_background
            if get_stage_background is not None
            else (lambda: DEFAULT_PREVIEW_BACKGROUND)
        )
        self._get_frame_outline: Callable[[], bool] = (
            get_frame_outline if get_frame_outline is not None else (lambda: False)
        )
        # - 배경 색 전환 단축키는 저장까지 맡은 호출자가 새 색을 돌려주고, 창은 표시만 갱신
        self._cycle_background: Callable[[], str] = (
            cycle_background
            if cycle_background is not None
            else (lambda: self._get_stage_background())
        )
        self._theme_palette: ThemePalette = DARK_PALETTE
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(PREVIEW_WINDOW_WIDTH, PREVIEW_WINDOW_HEIGHT)

        self._video = QVideoWidget()
        self._error_label = QLabel(_PLAYBACK_ERROR_TEXT)
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        self._stage = PreviewStage(self._video, self._video_frame_size)
        self._stage.video_moved.connect(self._on_video_geometry_changed)
        self._normal_page = QWidget(self)
        self._normal_layout = QVBoxLayout(self._normal_page)
        self._normal_layout.setContentsMargins(0, 0, 0, 0)
        self._normal_layout.setSpacing(0)
        self._normal_layout.addWidget(self._stage, 1)
        self._normal_layout.addWidget(self._error_label)
        self._compare_page = QWidget(self)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._normal_page)
        self._stack.addWidget(self._compare_page)
        self._controls = PlaybackControls(self, get_shortcuts)
        self._controls.set_shortcut_tooltips(self._get_shortcuts())
        self._controls.set_always_on_top(self._always_on_top)
        self._crop_overlay = CropOverlay(self, self._video_view_rect)
        self._crop_hud = CropEditHud(self, self._video_view_rect)
        # - 겹침 갱신이 편집 여백을 바꿔 영상 위젯 크기를 다시 바꾸므로, 그 안쪽 사건은 다시 처리하지 않음
        self._syncing_crop_geometry: bool = False
        self._apply_manual_active_frame = apply_manual_active_frame
        self._apply_crop_exclusion = apply_crop_exclusion
        self._crop_edit = CropEditSession(self._video, self)
        self._crop_edit.preview_changed.connect(self._update_crop_overlay)
        self._crop_edit.commit_requested.connect(self._on_crop_commit)
        self._crop_edit.undo_requested.connect(self._on_crop_undo)
        self._crop_edit.reset_auto_requested.connect(self._on_crop_reset_auto)
        self._crop_edit.keep_full_requested.connect(self._on_crop_keep_full)

        self._playback = PreviewPlayback(self._video, self, frame_rate=self._frame_rate)
        self._playback.position_changed.connect(self._controls.set_position)
        self._playback.duration_changed.connect(self._controls.set_duration)
        self._playback.playing_changed.connect(self._controls.set_playing)
        self._playback.error_changed.connect(lambda failed: self._error_label.setVisible(failed))
        self._playback.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._info = PreviewInfoPresenter(
            self, self._video, self._player, get_info_state, on_info_changed, self._toast_area
        )
        self._keys = preview.PreviewKeyRouter(
            get_shortcuts=get_shortcuts,
            forward_key=forward_key,
            get_actions=self._key_actions,
            is_visible=lambda: self.isVisible(),
            is_active=lambda: self.isActiveWindow(),
            seek_seconds=get_seek_seconds,
        )
        self._modes = preview_modes.PreviewModeSwitcher(
            self._stack,
            self._normal_page,
            self._compare_page,
            self._controls,
            self._info,
            get_seek_seconds=get_seek_seconds,
            get_shortcuts=get_shortcuts,
            get_extractor=lambda: self._compare_extractor,
            seek_normal=self._playback.set_position,
            step_normal=lambda direction: self._actions.step_frame(direction),
            toggle_normal=lambda: self._actions.toggle_playback(),
            get_compare_top_bar_collapsed=get_compare_top_bar_collapsed,
            on_compare_top_bar_collapsed_changed=on_compare_top_bar_collapsed_changed,
            apply_manual_target=apply_compare_manual_target,
            cycle_background=self._cycle_stage_background,
        )
        self._audio_sync = preview_modes.PreviewAudioSync(
            self._playback,
            self._modes,
            self._controls,
            set_rate=set_playback_rate,
            set_audio=set_audio,
            notify=lambda text: self.show_notice(text, duration_ms=preview.FEEDBACK_MS),
        )
        self._actions = preview.PreviewPlaybackActions(
            preview_modes.build_playback_ports(
                self._playback,
                frame_rate=lambda: self._frame_rate(),
                set_rate=self._audio_sync.on_rate_changed,
                notify=lambda text: self.show_notice(text, duration_ms=preview.FEEDBACK_MS),
            )
        )
        self._connect_compare()
        self._connect_controls()
        volume_percent, muted = get_audio()
        self._controls.set_audio_state(volume_percent, muted)
        self._audio_sync.apply_audio(volume_percent, muted)
        playback_rate = get_playback_rate()
        self._controls.set_playback_rate(playback_rate)
        self._player.setPlaybackRate(playback_rate)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._stack, 1)
        outer.addWidget(self._controls)
        self.init_theme_listener()

    def _connect_controls(self) -> None:
        """공용 하단 재생 바의 요청을 일반 재생기나 비교 뷰로 나눠 넘김"""
        self._controls.settings_requested.connect(self.settings_requested)
        self._controls.compare_requested.connect(self._on_compare_requested)
        self._controls.toggle_requested.connect(self._modes.toggle)
        self._controls.seek_requested.connect(self._modes.seek)
        self._controls.frame_step_requested.connect(self._modes.step_frame)
        self._controls.volume_changed.connect(lambda _value: self._audio_sync.save_audio())
        self._controls.rate_changed.connect(self._audio_sync.on_rate_changed)
        self._controls.mute_toggled.connect(self._audio_sync.on_mute_toggled)
        self._controls.fullscreen_requested.connect(self._toggle_fullscreen)
        self._controls.always_on_top_toggled.connect(self._apply_always_on_top)
        self._controls.crop_preview_toggled.connect(self._on_crop_toggled)
        self._crop_hud.action_buttons.reset_requested.connect(
            self._crop_edit.request_reset_auto
        )
        self._crop_hud.action_buttons.keep_full_requested.connect(
            self._crop_edit.request_keep_full
        )

    @property
    def _player(self) -> QMediaPlayer:
        """일반 모드 재생기 본체, 테스트와 메타데이터 조회가 같은 인스턴스를 봄"""
        return self._playback.player

    def apply_theme(self, palette: ThemePalette) -> None:
        """독립 창에서도 공용 QSS와 비교 전용 QSS, 배경 규칙을 함께 적용"""
        self._theme_palette = palette
        self._apply_stage_style()
        self._modes.apply_theme(palette)
        self._crop_hud.refresh_icons()

    @staticmethod
    def _window_stylesheet(palette: ThemePalette, background: str) -> str:
        """공용 QSS, 비교 전용 QSS, 고른 배경 색의 창 배경 규칙을 합침

        - 창 테두리 띠와 조작 바 바깥 여백은 영상 뒤 배경 색과 같게 칠함
        """
        stage = stage_color(palette, background)
        return (
            f"{build_stylesheet(palette)}\n{build_compare_qss(palette)}\n"
            f"VideoPreviewWindow {{ background-color: {stage}; }}"
        )

    @property
    def current_path(self) -> str:
        """지금 로드된 영상 경로, 아직 없으면 빈 문자열"""
        return self._path

    @property
    def compare_mode(self) -> bool:
        """지금 화질 비교 뷰를 보고 있는지"""
        return self._modes.compare_mode

    @property
    def position_sec(self) -> float:
        """지금 재생 위치(초). 비교 모드로 전환할 때 넘김"""
        if self._modes.compare_mode:
            return self._modes.position_sec
        return self._player.position() / _MS_PER_SECOND

    @property
    def is_playing(self) -> bool:
        """지금 재생 중인지. 비교 모드로 전환할 때 넘김"""
        if self._modes.compare_mode:
            return self._modes.is_playing
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def set_compare_extractor(self, extractor: FrameExtractor) -> None:
        """비교 뷰가 프레임을 추출할 실행기를 연결, 창을 만들기 전에도 호출 가능"""
        self._compare_extractor = extractor

    def show_compare_session(self, session: CompareSession, *, auto_play: bool = False) -> None:
        """같은 창 안에서 비교 뷰로 바꿔 세션을 올리고 창을 앞으로 표시

        - 일반 재생기는 멈추고 공유 파일 핸들을 놓아 두 디코더가 같은 파일을 잡지 않게 함
        - 숨겨져 있던 창이면 저장된 전체화면 상태로 열고, 하단 바와 키 필터도 함께 맞춤
        """
        if self._compare_extractor is None:
            return
        self._save_watched_position()
        self._playback.stop()
        self._playback.clear_pending_position()
        self._pending_for = None
        self._path = session.source_path
        self.setWindowTitle(f"화질 비교 - {session.filename}")
        volume_percent, muted = self._get_audio()
        self._controls.set_audio_state(volume_percent, muted)
        self._audio_sync.apply_audio(volume_percent, muted)
        playback_rate = self._get_playback_rate()
        self._controls.set_playback_rate(playback_rate)
        self._modes.show_session(
            session,
            auto_play=auto_play,
            rate=playback_rate,
            volume=volume_percent,
            muted=muted,
        )
        self._sync_crop_controls()
        if not self.isVisible() or self.isMinimized():
            if self._get_fullscreen():
                self.showFullScreen()
            elif self.isMinimized():
                self.showNormal()
            else:
                self.show()
            self._controls.set_fullscreen(self.isFullScreen())
        if not self._key_filter_installed:
            self._key_filter_installed = preview.install_key_filter(self)
        self.raise_()
        self.activateWindow()

    def leave_compare(self) -> None:
        """비교 뷰를 멈추고 같은 시점과 재생 상태로 일반 미리 보기를 다시 엶"""
        if not self._modes.compare_mode:
            return
        self._save_watched_position()
        path = self._modes.source_path or self._path
        position_sec = self._modes.position_sec
        was_playing = self._modes.is_playing
        self._modes.reset()
        if path:
            self.show_path_at(path, position_sec, auto_play=was_playing)
            return
        self._info.refresh()

    def _connect_compare(self) -> None:
        """비교 모드 전환 담당의 신호를 창의 재생기, 저장값, 알림에 연결"""
        modes = self._modes
        modes.step_requested.connect(self.step_requested)
        modes.position_changed.connect(
            lambda sec: self._controls.set_position(round(sec * _MS_PER_SECOND))
        )
        modes.duration_changed.connect(
            lambda sec: self._controls.set_duration(round(sec * _MS_PER_SECOND))
        )
        modes.playing_changed.connect(self._controls.set_playing)
        modes.rate_changed.connect(self._on_compare_rate_changed)
        modes.mute_toggled.connect(self._audio_sync.on_mute_toggled)
        modes.notice_requested.connect(self.show_notice)
        modes.close_requested.connect(self.hide)
        modes.preview_requested.connect(self.leave_compare)
        modes.fullscreen_requested.connect(self._toggle_fullscreen)

    def _on_compare_rate_changed(self, rate: float) -> None:
        """비교 뷰 키보드로 바뀐 배속을 하단 바와 저장값에 반영"""
        self._controls.set_playback_rate(rate)
        self._audio_sync.on_rate_changed(rate)

    def show_path(self, path: str, *, auto_play: bool = True) -> None:
        """영상을 로드해 재생하고 창을 앞으로 표시

        - auto_play가 거짓이면 첫 프레임만 보여 주고 일시중지 상태로 둠
        - 비교 모드였으면 비교 뷰를 멈추고 일반 미리 보기 페이지로 되돌림
        """
        if not path:
            return
        # - 비교 모드를 먼저 끄면 비교 뷰 시점을 잃으므로, 지금 모드의 위치를 먼저 남김
        self._save_watched_position()
        self._modes.reset()
        volume_percent, muted = self._get_audio()
        self._controls.set_audio_state(volume_percent, muted)
        self._audio_sync.apply_audio(volume_percent, muted)
        playback_rate = self._get_playback_rate()
        self._controls.set_playback_rate(playback_rate)
        self._playback.set_rate(playback_rate)
        self._modes.set_rate(playback_rate)
        self.load_path(path, auto_play=auto_play)
        # - 창을 열 때마다 여백 미리 보기는 꺼진 상태로 시작
        self._reset_crop_preview()
        # - 전체 화면 상태는 숨길 때 저장되므로, 저장된 상태가 전체 화면이면 같은 상태로 다시 표시
        if self._get_fullscreen():
            self.showFullScreen()
        elif self.isMinimized():
            # - 최소화한 채로 숨겼다가 다시 열면 최소화 상태가 남아 있어 보통 창으로 되돌림
            self.showNormal()
        else:
            self.show()
        # - 플래그 전환이나 복원으로 상태가 달라질 수 있어 표시 직후 전체화면 아이콘을 맞춤
        self._controls.set_fullscreen(self.isFullScreen())
        if not self._key_filter_installed:
            self._key_filter_installed = preview.install_key_filter(self)
        self.raise_()
        self.activateWindow()

    def set_crop_edit_targets(
        self,
        apply_manual_active_frame: (
            Callable[[VideoEvaluation, ActiveFrame | None], None] | None
        ),
        apply_crop_exclusion: Callable[[VideoEvaluation, bool], None] | None,
    ) -> None:
        """사용자 지정 활성 화면 저장과 잘라내기 제외 전환 대상만 바꿈(창을 만들기 전에는 호출하지 않음)"""
        self._apply_manual_active_frame = apply_manual_active_frame
        self._apply_crop_exclusion = apply_crop_exclusion

    def set_video_info(self, info: PreviewVideoInfo | None) -> None:
        """정보 표시에 쓸 지금 영상의 표 값을 전달하고, 프레임 이동에 쓸 FPS를 기억"""
        self._video_info = info
        self._info.set_video_info(info)
        self._stage.update_geometry()
        self._sync_crop_controls()

    def _video_view_rect(self) -> QRect:
        """영상 위젯의 지금 화면 전역 영역, 영상 내용은 이 안에서 영상비로 가운데 배치됨"""
        return QRect(self._video.mapToGlobal(QPoint(0, 0)), self._video.size())

    def _video_frame_size(self) -> tuple[int, int] | None:
        """영상 위젯이 영상비로 맞춰질 프레임 크기, 표 값이 없으면 None(무대 전체를 씀)"""
        info = self._video_info
        if info is None:
            return None
        try:
            width = int(info.ev.video.width)
            height = int(info.ev.video.height)
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return (width, height)

    def refresh_stage_style(self) -> None:
        """배경 색과 영상 테두리 설정을 두 모드에 바로 반영"""
        self._apply_stage_style()

    def _apply_stage_style(self) -> None:
        """저장된 배경 색과 영상 테두리를 무대, 창 배경, 비교 모드에 함께 반영

        - 일반 모드는 무대 위젯이 영상 바깥을 칠하고, 비교 모드는 캔버스가 같은 값을 씀
        - 비교 뷰는 만들기 전이면 값을 기억하므로 테마 적용 때도 항상 넘겨야
          첫 비교 화면이 저장된 배경과 테두리를 지킴
        """
        background = self._get_stage_background()
        outline = self._get_frame_outline()
        self._stage.apply_stage_style(self._theme_palette, background, outline)
        self._modes.set_stage_style(background, outline)
        self.setStyleSheet(self._window_stylesheet(self._theme_palette, background))

    def _crop_active_frame(self) -> ActiveFrame | None:
        """지금 영상의 잘라내기 기준 영역, 편집 대상이 아니거나 비교 모드면 None

        - 화질 분석 결과가 없어도 영상 크기를 알면 전체 프레임을 편집 영역으로 씀
        """
        info = self._video_info
        frame_size = self._video_frame_size()
        if info is None or frame_size is None or self.compare_mode:
            return None
        return info.ev.active_frame or full_active_frame(frame_size)

    def _sync_crop_controls(self) -> None:
        """여백 미리 보기 버튼 표시와 겹침 상태를 지금 영상과 모드에 맞춤

        - 영상 크기를 모르는 동안에는 버튼을 꺼 둔 채 켤 수 없는 이유를 툴팁으로 알림
        """
        active = self._crop_active_frame()
        unknown_size = self._video_info is not None and not self.compare_mode and active is None
        self._controls.set_crop_available(active is not None, unknown_size=unknown_size)
        # - 대상이 아닌 영상으로 바뀌면 켜 둔 표시도 함께 끔
        self._crop_enabled = active is not None and self._controls.crop_button.isChecked()
        self._update_crop_overlay()

    def _update_crop_overlay(self) -> None:
        """겹침에 그릴 배치를 지금 영상과 창 크기로 계산해 넘기고, 그릴 것이 없으면 숨김"""
        active = self._crop_active_frame()
        video = None if self._video_info is None else self._video_info.ev.video
        if not self._crop_enabled or active is None or not self.isVisible() or video is None:
            self._set_crop_editing(False)
            return
        self._set_crop_editing(True)
        self._crop_edit.set_video(self._path, (video.width, video.height), active)
        self._crop_edit.set_enabled(True)
        self._crop_overlay.set_size_label(kept_label(self._crop_edit.frame))
        self._crop_hud.set_kept(self._crop_edit.frame)
        if self._crop_hud.isVisible():
            self._crop_hud.reposition()
        else:
            self._crop_hud.show_hud()
        hud_rect = QRect(self._crop_hud.mapToGlobal(QPoint(0, 0)), self._crop_hud.size())
        self._crop_overlay.set_crop(self._crop_edit.rects(), hud_rect=hud_rect)
        self._crop_hud.raise_()

    def _set_crop_editing(self, editing: bool) -> None:
        """편집 중 여부에 맞춰 영상 둘레 여백과 겹침, 편집 도구 모음 표시를 함께 맞춤

        - 여백은 유지 영역 손잡이가 창 테두리 크기 조절 영역에 닿지 않게 영상을 안쪽으로 들임
        - 여백이 이미 같으면 레이아웃을 다시 풀지 않아 끌기 중 매번 재배치하지 않음
        """
        margin = CROP_EDIT_MARGIN_PX if editing else 0
        if self._normal_layout.contentsMargins().left() != margin:
            self._normal_layout.setContentsMargins(margin, margin, margin, margin)
            self._normal_layout.activate()
        if editing:
            return
        self._crop_edit.set_enabled(False)
        self._crop_overlay.set_crop(None)
        self._crop_hud.hide()

    def _on_crop_commit(self, frame: ActiveFrame) -> None:
        """끌기를 마친 유지 영역을 영상별 직접 지정으로 저장하고 알림"""
        ev = self._video_info.ev if self._video_info is not None else None
        if ev is None or self._apply_manual_active_frame is None:
            return
        self._apply_manual_active_frame(ev, frame)
        self.show_notice(SAVED_MANUAL_FRAME_TEXT, duration_ms=preview.FEEDBACK_MS)

    def _on_crop_undo(self, frame: ActiveFrame | None) -> None:
        """창 세션에서 마지막으로 바꾼 값을 다시 저장 요청"""
        ev = self._video_info.ev if self._video_info is not None else None
        if ev is None or self._apply_manual_active_frame is None:
            return
        self._apply_manual_active_frame(ev, frame)
        self.show_notice(UNDO_DONE_NOTICE, duration_ms=preview.FEEDBACK_MS)

    def _on_crop_reset_auto(self) -> None:
        """자동 감지 결과로 되돌리고 잘라내기 제외도 함께 해제"""
        ev = self._video_info.ev if self._video_info is not None else None
        if ev is None or self._apply_manual_active_frame is None:
            return
        if self._apply_crop_exclusion is not None:
            self._apply_crop_exclusion(ev, False)
        self._apply_manual_active_frame(ev, None)
        self.show_notice(CROP_RESET_TIP, duration_ms=preview.FEEDBACK_MS)

    def _on_crop_keep_full(self) -> None:
        """이 영상을 자르지 않도록 지정을 지우고 잘라내기 제외를 켬"""
        ev = self._video_info.ev if self._video_info is not None else None
        if ev is None or self._apply_crop_exclusion is None:
            return
        if self._apply_manual_active_frame is not None:
            self._apply_manual_active_frame(ev, None)
        self._apply_crop_exclusion(ev, True)
        self.show_notice(CROP_FULL_TIP, duration_ms=preview.FEEDBACK_MS)

    def _on_crop_toggled(self, enabled: bool) -> None:
        """여백 미리 보기 버튼 상태를 따라 겹침을 켜고 끔"""
        self._crop_enabled = enabled
        self._update_crop_overlay()

    def _toggle_crop_preview(self) -> None:
        """여백 미리 보기 단축키: 편집 대상이 아니거나 비교 모드면 아무것도 하지 않음

        - 버튼 토글 한 경로만 써서 아이콘, 겹침, 상태가 함께 바뀌게 함
        - 알림은 키로 바꿨을 때만 띄워, 영상 전환이나 창 닫기로 꺼질 때 토스트가 남지 않게 함
        - 영상 크기를 몰라 켤 수 없는 이유는 버튼 툴팁이 알림
        """
        if self._crop_active_frame() is None:
            return
        button = self._controls.crop_button
        button.toggle()
        self.show_notice(preview.crop_notice(button.isChecked()), duration_ms=preview.FEEDBACK_MS)

    def _cycle_stage_background(self) -> str:
        """배경 색 전환 단축키: 호출자가 계산해 저장한 새 색을 두 모드에 바로 적용하고 알림

        - 비교 모드에서도 같은 명령을 쓰므로 두 모드를 함께 갱신
        - 부모 창과 비교 뷰가 같은 경로를 쓰도록 바뀐 색을 돌려줌
        """
        background = self._cycle_background()
        self.refresh_stage_style()
        self.show_notice(stage_background_notice(background), duration_ms=preview.FEEDBACK_MS)
        return background

    def _reset_crop_preview(self) -> None:
        """여백 미리 보기를 끄고 버튼 체크도 풀어 새로 연 창이 꺼진 상태에서 시작하게 함"""
        self._crop_enabled = False
        self._controls.crop_button.setChecked(False)
        self._hide_crop_views()

    def _hide_crop_views(self) -> None:
        """영상 위에 띄운 겹침과 편집 도구 모음을 내림(편집 상태와 버튼 체크는 그대로)"""
        self._crop_hud.hide()
        self._crop_overlay.set_crop(None)

    def show_path_at(
        self,
        path: str,
        position_sec: float,
        geometry: QRect | None = None,
        *,
        auto_play: bool = True,
    ) -> None:
        """지정 시점과 창 위치로 영상을 열어 표시, 읽기 전 위치 지정은 읽은 뒤에 적용하고 auto_play가 거짓이면 멈춘 채 이어감

        - 새 영상의 대기 시점을 걸면 `load_path`의 저장이 건너뛰므로, 지금 영상의 위치를 먼저 남김
        """
        self._save_watched_position()
        self._playback.queue_position(round(position_sec * _MS_PER_SECOND))
        self._pending_for = path
        self.show_path(path, auto_play=auto_play)
        if geometry is not None and not self.isFullScreen():
            self.setGeometry(geometry)
        if not auto_play:
            self._player.pause()

    def show_notice(self, text: str, *, duration_ms: int = TOAST_DURATION_MS) -> None:
        """영상 위에 실행 결과 문구를 잠깐 표시, 비교 모드에서도 같은 토스트를 씀"""
        self._info.show_notice(text, duration_ms=duration_ms)

    def _toast_area(self) -> QRect:
        """토스트를 놓을 전역 영역, 비교 모드면 비교 캔버스의 상단 오버레이와 알약을 비켜 놓음"""
        compare_area = self._modes.notice_area_global()
        if compare_area is not None:
            return compare_area
        top_left = self._video.mapToGlobal(QPoint(0, 0))
        return QRect(top_left, self._video.size()).adjusted(
            EDGE_MARGIN_PX, EDGE_MARGIN_PX, -EDGE_MARGIN_PX, -EDGE_MARGIN_PX
        )

    def refresh_info(self) -> None:
        """정보 표시 설정이 바뀐 뒤 표시를 다시 맞춤"""
        self._info.refresh()

    def settings_button_rect(self) -> QRect:
        """미리 보기 설정 버튼의 전역 화면 영역, 팝업을 버튼 옆에 맞추는 기준"""
        button = self._controls.settings_button
        return QRect(button.mapToGlobal(QPoint(0, 0)), button.size())

    def load_path(self, path: str, *, auto_play: bool = True) -> None:
        """창 표시 상태는 바꾸지 않고 영상만 교체, 표 선택 이동에 따라 호출

        - auto_play가 거짓이면 첫 프레임만 보여 주고 일시중지 상태로 둠
        - 이전 영상의 위치는 기억하고, 새 영상은 기억한 시점이 있으면 그 시점부터 엶
        """
        if not path:
            return
        self._save_watched_position()
        self._path = path
        self._error_label.hide()
        # - 재생기 원시 출력(FFmpeg)은 시각 없이 출력되므로 소스 교체 직전에 시각 줄을 남겨 묶음의 시작을 표시
        logger.info("%s", format_preview_start(os.path.basename(path)))
        if self._playback.pending_position_ms is not None and self._pending_for != path:
            self._playback.clear_pending_position()
            self._pending_for = None
        if self._playback.pending_position_ms is None:
            remembered = self._positions.recall(path)
            if remembered is not None:
                self._playback.queue_position(round(remembered * _MS_PER_SECOND))
                self._pending_for = path
        self._playback.load(path, auto_play=auto_play)
        self.setWindowTitle(os.path.basename(path))
        # - 새 영상의 정보가 오기 전까지 이전 영상 기준 겹침과 편집 도구 모음을 지움
        self._hide_crop_views()

    def showEvent(self, event: QShowEvent | None) -> None:
        """표시될 때 정보 표시 갱신을 시작하고 켜 둔 여백 겹침을 다시 올림"""
        super().showEvent(event)
        self._info.refresh()
        self._update_crop_overlay()

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        """창 크기가 바뀌면 정보 표시와 안내를 다시 놓음

        - 이 시점에는 레이아웃이 영상 위젯을 새 크기로 아직 확정하지 않아 겹침을 옮겨도 한 박자 늦으므로,
          겹침과 편집 도구 모음은 영상 위젯의 크기, 위치 변경 사건에서 바로 맞춤
        """
        super().resizeEvent(event)
        self._info.relayout()

    def _on_video_geometry_changed(self) -> None:
        """영상 위젯이 실제로 새 크기로 놓이면 여백 겹침과 편집 도구 모음을 그 자리로 즉시 맞춤

        - 창 크기 변경과 전체 화면 전환은 레이아웃 확정이 늦어 창 사건 시점의 영상 영역이 낡으므로,
          무대가 영상 위젯을 새 영역에 놓는 순간 이 경로로 따라오게 함
        - 갱신 중 편집 여백 변경으로 다시 들어온 사건은 건너뛰어 무한 반복을 막음
        """
        if self._syncing_crop_geometry or not self._crop_overlay.isVisible():
            return
        self._syncing_crop_geometry = True
        try:
            self._update_crop_overlay()
        finally:
            self._syncing_crop_geometry = False

    def nativeEvent(
        self, event_type: QByteArray | bytes | bytearray | memoryview, message: sip.voidptr | None
    ) -> tuple[bool, sip.voidptr]:
        """편집 중 영상 안의 창 테두리 적중 판정을 클라이언트로 되돌림

        - Windows는 변두리 누름을 `WM_NCHITTEST` 응답으로 창 크기 조절에 넘기므로, 손잡이를
          잡으려는 누름이 창 크기 변경으로 새지 않게 클라이언트 영역으로 답함
        - 편집 중이 아니면 기본 처리를 그대로 두어 평소 창 크기 조절을 유지
        """
        if self._crop_enabled and crop_resize_guard.should_keep_client_hit_test(
            event_type,
            message,
            self._video_view_rect(),
            edited_kept=self._crop_edit.kept_rect_global(),
            tolerance=HANDLE_EDGE_PX,
        ):
            return (True, sip.voidptr(_HTCLIENT))
        # - 기본 구현을 부르면 Windows에서 창을 표시할 때 접근 위반이 나므로 처리하지 않았다고만 답함
        return (False, sip.voidptr(0))

    def moveEvent(self, event: QMoveEvent | None) -> None:
        """창이 움직이면 화면 전역 좌표로 놓인 정보 표시와 안내, 겹침, 도구 모음이 따라가게 함"""
        super().moveEvent(event)
        self._info.relayout()
        if self._crop_overlay.isVisible():
            self._crop_overlay.reposition()
        self._crop_hud.reposition()

    def sync_shortcuts(self, shortcuts: Mapping[str, str]) -> None:
        """단축키 설정 변경을 조작 바 툴팁과 비교 뷰 상단 바에 반영"""
        self._controls.set_shortcut_tooltips(shortcuts)
        self._modes.sync_shortcuts(shortcuts)

    def hideEvent(self, event: QHideEvent | None) -> None:
        """창을 숨길 때 정리. 최소화나 소유자 최소화로 인한 숨김이면 재생만 멈추고 보던 세션은 남김

        - 최소화 상태에서 명시적으로 숨기면 isMinimized는 남고 isVisible만 꺼지므로 두 경우를 표시 여부로 구분함
        """
        if self._toggling_window_flag:
            # - 플래그 전환으로 숨을 때도 겹침은 내려 두고, 다시 표시될 때 위치를 맞춤
            self._hide_crop_views()
            super().hideEvent(event)
            return
        if (self.isMinimized() and self.isVisible()) or (
            event is not None and event.spontaneous()
        ):
            # - 겹침과 편집 도구 모음은 별도 최상위 창이라 창이 가려질 때 함께 숨겨야 남지 않음
            self._hide_crop_views()
            self._playback.pause()
            self._modes.pause()
            super().hideEvent(event)
            return
        self._hide_crop_views()
        self._controls.guide_button.close_guide()
        if self._key_filter_installed:
            preview.remove_key_filter(self)
            self._key_filter_installed = False
        self._save_watched_position()
        self._playback.stop()
        self._modes.stop()
        self._path = ""
        self._playback.clear_pending_position()
        self._pending_for = None
        # - 숨긴 창을 다시 열면 여백 미리 보기는 꺼진 상태로 시작
        self._reset_crop_preview()
        if self.isFullScreen() != self._get_fullscreen():
            self._on_fullscreen_changed(self.isFullScreen())
        super().hideEvent(event)
        self._info.refresh()
        self._raise_owner()

    def hide_without_owner_raise(self) -> None:
        """숨기되 소유 창을 앞으로 올리지 않음. 메인 창이 활성일 때 진행 창이 그 위로 떠오르지 않게 함"""
        self.set_owner_raise_suppressed(True)
        try:
            self.hide()
        finally:
            self.set_owner_raise_suppressed(False)

    def set_owner_raise_suppressed(self, suppressed: bool) -> None:
        """숨김이 소유 창을 앞으로 올릴지 정함. 창이 함께 닫히는 동안 올리지 않게 함"""
        self._owner.set_raise_suppressed(suppressed)

    def _set_toggling_window_flag(self, toggling: bool) -> None:
        """창 플래그 전환 중 표시를 세우고 풀어 숨김 사건 처리 방식을 정함"""
        self._toggling_window_flag = toggling

    def _raise_owner(self) -> None:
        """미리 보기를 숨길 때 소유 창을 다시 앞으로 올림"""
        self._owner.raise_owner()

    def _save_watched_position(self) -> None:
        """지금 영상의 재생 위치를 이번 실행 기억에 남기고, 처음이나 끝 부근이면 지움

        - 일반 모드에서 미디어를 아직 다 읽지 않았거나 읽은 뒤 시점을 적용하기 전이면 건드리지 않음
        """
        path = self._path
        if not path:
            return
        if self._modes.compare_mode:
            position_sec = self._modes.position_sec
            duration_sec = self._modes.duration_sec
        else:
            duration_ms = self._player.duration()
            if duration_ms <= 0 or self._playback.pending_position_ms is not None:
                return
            position_sec = self._player.position() / _MS_PER_SECOND
            duration_sec = duration_ms / _MS_PER_SECOND
        if duration_sec > 0 and duration_sec - position_sec <= _POSITION_END_GAP_SEC:
            self._positions.forget(path)
            return
        if position_sec < _POSITION_MIN_SEC:
            self._positions.forget(path)
            return
        self._positions.remember(path, position_sec)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        """일반 모드에서 끝까지 재생되면 기억한 위치를 지워 다음에 처음부터 엶"""
        if (
            status == QMediaPlayer.MediaStatus.EndOfMedia
            and not self._modes.compare_mode
            and self._path
        ):
            self._positions.forget(self._path)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """비교 모드면 비교 뷰 단축키로, 일반 모드면 미리 보기 명령으로 키를 처리"""
        key_event = (
            a1 is not None
            and a1.type() == QEvent.Type.KeyPress
            and isinstance(a1, QKeyEvent)
            and self.isVisible()
            and self.isActiveWindow()
            and not preview.blocks_preview_keys()
        )
        if (
            key_event
            and isinstance(a1, QKeyEvent)
            # - 여백 편집 중 Esc, Ctrl+Z는 창 닫기나 앱 되돌리기보다 먼저 이 창에서 처리
            and self._crop_key(a1)
        ):
            return True
        if key_event and isinstance(a1, QKeyEvent) and self._modes.compare_mode:
            if self._modes.handle_key_event(a1):
                return True
            # - 비교 뷰가 쓰지 않는 전역 명령(되돌리기, 출력 해상도 지정 등)은 메인 창이 실행
            return self._forward_key(QKeySequence(a1.keyCombination()).toString())
        if not self._modes.compare_mode and self._keys.filter_event(a1):
            return True
        return super().eventFilter(a0, a1)

    def sync_compare_report(self, report: SummaryReport) -> None:
        """비교 모드면 현재 영상의 판정 결과를 다시 찾아 2줄 바 출력 상태를 맞춤"""
        if self._modes.compare_mode:
            self._modes.sync_compare_report(report)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """Space, Esc는 고정으로 닫고, 나머지 키는 지금 모드의 단축키 명령으로 처리"""
        if event is None:
            return
        if self._crop_key(event):
            event.accept()
            return
        if self._modes.compare_mode:
            if self._modes.handle_key_event(event) or self._forward_key(
                QKeySequence(event.keyCombination()).toString()
            ):
                event.accept()
                return
        elif self._keys.handle_key(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def _crop_key(self, event: QKeyEvent) -> bool:
        """여백 편집 중 Esc는 끌기 취소, Ctrl+Z는 이 창의 마지막 변경 되돌리기로 처리

        - 끌기 중이 아닌 Esc와 다른 키는 기존 닫기/단축키 동작으로 넘김
        - Esc는 앱 이벤트 필터에서도 먼저 받아 창 닫기로 새지 않게 함
        """
        if not self._crop_enabled:
            return False
        if event.key() == Qt.Key.Key_Escape and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            return self._crop_edit.cancel_drag()
        if (
            event.key() == Qt.Key.Key_Z
            and event.modifiers() == Qt.KeyboardModifier.ControlModifier
        ):
            return self._crop_edit.undo()
        return False

    def _key_actions(self) -> preview.PreviewActionHandlers:
        """미리 보기 명령이 부를 재생기와 창 동작 묶음"""
        return preview.PreviewActionHandlers(
            seek_by=self._actions.seek_by,
            step_frame=self._actions.step_frame,
            toggle_playback=self._modes.toggle,
            toggle_mute=self._controls.toggle_mute,
            set_rate=self._actions.set_rate,
            toggle_fullscreen=self._toggle_fullscreen,
            cycle_info=self._info.cycle_mode,
            request_step=self.step_requested.emit,
            rate=lambda: self._player.playbackRate(),
            hide=self.hide,
            toggle_compare=self._on_compare_requested,
            toggle_crop=self._toggle_crop_preview,
            cycle_background=self._cycle_stage_background,
        )

    def _on_compare_requested(self) -> None:
        """화질 비교 버튼과 전환 단축키를 비교 모드 진입이나 해제로 나눔"""
        if self._modes.compare_mode:
            self.leave_compare()
        elif self._path:
            self.compare_requested.emit(self._path, self.position_sec)

    def _apply_always_on_top(self, pinned: bool) -> None:
        """핀 상태를 조작 바에 반영하고 저장을 요청한 뒤 알림을 표시

        - 창 소유자 변경과 앱 내부 Z 순서는 컨트롤러가 저장 콜백에서 처리
        """
        self._always_on_top = pinned
        preview_modes.apply_stays_on_top(
            pinned,
            controls=self._controls,
            on_changed=self._on_always_on_top_changed,
        )
        self.show_notice(preview.pin_notice(pinned), duration_ms=preview.FEEDBACK_MS)

    def set_owner(self, owner: QWidget | None) -> None:
        """창 소유자를 바꿔 앱 범위 고정을 적용하고 위치와 전체화면, 재생 세션을 유지"""
        self._owner.set_owner(owner)

    def _toggle_fullscreen(self) -> None:
        """전체화면이면 이전 창 크기로 되돌리고, 아니면 전체화면으로 전환한 뒤 상태를 저장"""
        preview_modes.toggle_fullscreen(
            self,
            controls=self._controls,
            stored=self._get_fullscreen(),
            on_changed=self._on_fullscreen_changed,
        )
        self.show_notice(
            preview.fullscreen_notice(self.isFullScreen()), duration_ms=preview.FEEDBACK_MS
        )

    def _frame_rate(self) -> float:
        """표에서 읽은 FPS, 없으면 재생기 메타데이터, 그것도 없으면 30fps"""
        info = self._video_info
        info_fps = 0.0 if info is None else float(info.ev.video.fps)
        return preview_modes.resolve_preview_frame_rate(self._player, info_fps)

