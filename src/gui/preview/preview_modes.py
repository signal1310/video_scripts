"""미리 보기 창의 일반 모드와 화질 비교 모드 전환, 공용 조작 바와 창 상태 동기화

- 창은 두 렌더러와 스택을 소유하고, 이 모듈이 페이지, 하단 바, 정보 표시, 단축키 알림 자리를 바꿈
- 비교 뷰는 처음 비교 모드로 들어갈 때 만들어 재사용하고, 모드를 나갈 때 재생과 디코더를 정리
- 핀과 전체화면은 윈도우 플래그와 저장값을 함께 바꾸므로 창 상태 전환으로 이 모듈이 맡음
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt6.QtCore import QObject, QRect, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtMultimedia import QMediaMetaData, QMediaPlayer
from PyQt6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from src.core.models import SummaryReport, VideoEvaluation
from src.core.settings.ui_state import DEFAULT_PREVIEW_BACKGROUND
from src.gui.dialogs.frame_compare_layout import (
    COMPARE_GUIDE_ACTIONS,
)
from src.gui.dialogs.frame_compare_layout import (
    GUIDE_TITLE as COMPARE_GUIDE_TITLE,
)
from src.gui.dialogs.frame_compare_meta import CompareSession
from src.gui.dialogs.frame_compare_view import FrameCompareView
from src.gui.dialogs.frame_compare_worker import FrameExtractor
from src.gui.preview.preview_info_presenter import PreviewInfoPresenter
from src.gui.preview.preview_keys import (
    PreviewPlaybackPorts,
    frame_notice,
    mute_notice,
    playback_notice,
    resolve_frame_rate,
)
from src.gui.preview.preview_playback import PreviewPlayback
from src.gui.preview.video_preview_controls import (
    GUIDE_TITLE as PREVIEW_GUIDE_TITLE,
)
from src.gui.preview.video_preview_controls import (
    PREVIEW_GUIDE_ACTIONS,
    PlaybackControls,
)
from src.gui.styles.icons import get_active_palette
from src.gui.styles.palettes import ThemePalette

_MS_PER_SECOND: int = 1000


def build_playback_ports(
    playback: PreviewPlayback,
    *,
    frame_rate: Callable[[], float],
    set_rate: Callable[[float], None],
    notify: Callable[[str], None],
) -> PreviewPlaybackPorts:
    """재생 세션에서 미리 보기 재생 명령이 쓰는 접근자를 묶음

    - 이동은 세션이 끝 여유를 적용해 제한하므로 단축키와 조작 바가 같은 규칙을 씀
    """
    player = playback.player
    return PreviewPlaybackPorts(
        position_ms=lambda: player.position(),
        duration_ms=lambda: player.duration(),
        is_playing=lambda: player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
        frame_rate=frame_rate,
        seek=playback.set_position,
        pause=lambda: player.pause(),
        play=lambda: player.play(),
        set_rate=set_rate,
        notify=notify,
    )


def resolve_preview_frame_rate(player: QMediaPlayer, info_fps: float) -> float:
    """표 FPS가 양수면 그 값, 아니면 재생기 메타데이터, 그것도 없으면 30fps"""
    return resolve_frame_rate(
        info_fps, player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
    )


class PreviewAudioSync:
    """일반 재생기, 비교 뷰, 저장값에 음량과 음소거, 배속을 함께 반영

    - 조작 바, 단축키, 비교 뷰 어느 쪽에서 바뀌어도 같은 저장 경로를 씀
    - 음소거는 바뀐 상태를 알림 문구로 알림
    """

    def __init__(
        self,
        playback: PreviewPlayback,
        modes: PreviewModeSwitcher,
        controls: PlaybackControls,
        *,
        set_rate: Callable[[float], None],
        set_audio: Callable[[int, bool], None],
        notify: Callable[[str], None],
    ) -> None:
        self._playback = playback
        self._modes = modes
        self._controls = controls
        self._set_rate = set_rate
        self._set_audio = set_audio
        self._notify = notify

    def apply_audio(self, volume_percent: int, muted: bool) -> None:
        """음량과 음소거를 일반 재생기와 비교 뷰 오디오에 함께 적용"""
        self._playback.set_audio(volume_percent, muted)
        self._modes.set_audio(volume_percent, muted)

    def save_audio(self) -> None:
        """조작 바의 음량과 음소거를 두 모드에 적용하고 호출자에게 저장을 요청"""
        volume_percent, muted = self._controls.audio_state()
        self.apply_audio(volume_percent, muted)
        self._set_audio(volume_percent, muted)

    def on_rate_changed(self, rate: float) -> None:
        """바뀐 배속을 두 모드 재생기에 적용하고 저장"""
        self._playback.set_rate(rate)
        self._set_rate(rate)
        self._modes.set_rate(rate)

    def on_mute_toggled(self, muted: bool) -> None:
        """음소거 상태를 저장하고 두 모드에 적용한 뒤 알림"""
        self.save_audio()
        self._notify(mute_notice(muted))



class PreviewModeSwitcher(QObject):
    """일반 미리 보기와 화질 비교 뷰의 페이지, 공용 조작 바, 안내 자리를 바꾸는 전환 담당

    - 비교 뷰 신호를 그대로 다시 내보내 창이 재생 바와 저장값을 한 곳에서 맞추게 함
    - 비교 모드를 나갈 때 재생과 추출을 멈춰 파일 핸들을 남기지 않음
    """

    step_requested = pyqtSignal(int)
    position_changed = pyqtSignal(float)
    duration_changed = pyqtSignal(float)
    playing_changed = pyqtSignal(bool)
    rate_changed = pyqtSignal(float)
    mute_toggled = pyqtSignal(bool)
    notice_requested = pyqtSignal(str)
    close_requested = pyqtSignal()
    preview_requested = pyqtSignal()
    fullscreen_requested = pyqtSignal()

    def __init__(
        self,
        stack: QStackedWidget,
        normal_page: QWidget,
        compare_page: QWidget,
        controls: PlaybackControls,
        info: PreviewInfoPresenter,
        *,
        get_seek_seconds: Callable[[], int],
        get_shortcuts: Callable[[], Mapping[str, str]],
        get_extractor: Callable[[], FrameExtractor | None],
        seek_normal: Callable[[int], None],
        step_normal: Callable[[int], None],
        toggle_normal: Callable[[], None],
        get_compare_top_bar_collapsed: Callable[[], bool] | None = None,
        on_compare_top_bar_collapsed_changed: Callable[[bool], None] | None = None,
        apply_manual_target: Callable[[VideoEvaluation, str | None], bool] | None = None,
        cycle_background: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(compare_page)
        self._stack = stack
        self._normal_page = normal_page
        self._compare_page = compare_page
        self._controls = controls
        self._info = info
        self._get_seek_seconds = get_seek_seconds
        self._get_shortcuts = get_shortcuts
        self._get_extractor = get_extractor
        self._seek_normal = seek_normal
        self._step_normal = step_normal
        self._toggle_normal = toggle_normal
        self._get_compare_top_bar_collapsed = get_compare_top_bar_collapsed
        self._on_compare_top_bar_collapsed_changed = on_compare_top_bar_collapsed_changed
        self._apply_manual_target = apply_manual_target
        self._cycle_background = cycle_background
        self._compare_mode: bool = False
        self._view: FrameCompareView | None = None
        # - 비교 뷰를 처음 만들 때도 지금 설정을 적용하도록 마지막 값을 기억
        self._stage_background: str = DEFAULT_PREVIEW_BACKGROUND
        self._stage_outline: bool = False

    @property
    def compare_mode(self) -> bool:
        """지금 화질 비교 페이지를 보고 있는지"""
        return self._compare_mode

    @property
    def view(self) -> FrameCompareView | None:
        """만들어진 비교 뷰, 아직 비교 모드로 들어가지 않았으면 None"""
        return self._view

    def show_session(
        self, session: CompareSession, *, auto_play: bool, rate: float, volume: int, muted: bool
    ) -> None:
        """비교 페이지로 바꾸고 세션을 올리며 저장된 재생 옵션을 적용"""
        view = self._ensure_view()
        if view is None:
            return
        self._compare_mode = True
        self._stack.setCurrentWidget(self._compare_page)
        self._controls.set_compare_mode(
            True, guide_title=COMPARE_GUIDE_TITLE, guide_actions=COMPARE_GUIDE_ACTIONS
        )
        self._info.set_compare_mode(True)
        view.set_playback_rate(rate)
        view.set_audio(volume, muted)
        view.load_session(session, auto_play=auto_play)
        self._info.relayout()

    def reset(self) -> None:
        """비교 뷰를 멈추고 일반 미리 보기 페이지와 하단 바로 되돌림"""
        self.stop()
        if not self._compare_mode:
            return
        self._compare_mode = False
        self._stack.setCurrentWidget(self._normal_page)
        self._controls.set_compare_mode(
            False, guide_title=PREVIEW_GUIDE_TITLE, guide_actions=PREVIEW_GUIDE_ACTIONS
        )
        self._info.set_compare_mode(False)

    def sync_compare_report(self, report: SummaryReport) -> None:
        """비교 뷰가 현재 영상의 판정 결과를 다시 찾아 출력 상태를 맞추게 함"""
        if self._view is not None:
            self._view.sync_report(report)

    def stop(self) -> None:
        """비교 모드 재생과 추출을 멈춤"""
        if self._view is not None:
            self._view.stop_playback()

    def pause(self) -> None:
        """비교 모드 재생만 일시중지, 창 최소화처럼 화면을 잠시 가릴 때 씀"""
        if self._compare_mode and self._view is not None:
            self._view.pause_playback()

    def seek(self, position_ms: int) -> None:
        """위치 이동을 비교 뷰에 넘기고, 일반 모드면 일반 재생기로 넘김"""
        if self._compare_mode and self._view is not None:
            self._view.set_time(position_ms / _MS_PER_SECOND, immediate=True)
            return
        self._seek_normal(position_ms)

    def step_frame(self, direction: int) -> None:
        """한 프레임 이동을 모드에 맞는 재생기로 넘기고 비교 모드면 결과를 알림"""
        if self._compare_mode and self._view is not None:
            self._view.step_frame(direction)
            self.notice_requested.emit(frame_notice(direction))
            return
        self._step_normal(direction)

    def toggle(self) -> None:
        """재생과 일시중지를 모드에 맞는 재생기로 넘기고 비교 모드면 결과를 알림"""
        if self._compare_mode and self._view is not None:
            self._view.toggle_playback()
            self.notice_requested.emit(playback_notice(self._view.is_playing))
            return
        self._toggle_normal()

    def set_rate(self, rate: float) -> None:
        """비교 뷰 재생 엔진에 배속을 적용"""
        if self._view is not None:
            self._view.set_playback_rate(rate)

    def set_audio(self, volume_percent: int, muted: bool) -> None:
        """비교 뷰 재생 엔진에 음량과 음소거를 적용"""
        if self._view is not None:
            self._view.set_audio(volume_percent, muted)

    def sync_shortcuts(self, shortcuts: Mapping[str, str]) -> None:
        """비교 뷰 상단 바 툴팁을 최신 단축키로 맞춤"""
        if self._view is not None:
            self._view.sync_shortcuts(shortcuts)

    def apply_theme(self, palette: ThemePalette) -> None:
        """비교 뷰 캔버스와 상단 바 색, 아이콘을 팔레트에 맞춤"""
        if self._view is not None:
            self._view.apply_theme(palette)

    def set_stage_style(self, background: str, outline: bool) -> None:
        """비교 뷰 캔버스의 배경 색과 영상 테두리를 맞춤, 아직 만들기 전이면 기억해 두었다가 만든 뒤 적용"""
        self._stage_background = background
        self._stage_outline = outline
        if self._view is not None:
            self._view.set_stage_style(background, outline)

    def notice_area_global(self) -> QRect | None:
        """공용 토스트를 놓을 비교 캔버스 영역, 비교 모드가 아니면 None"""
        if not self._compare_mode or self._view is None:
            return None
        return self._view.notice_area_global()

    def handle_key_event(self, event: QKeyEvent) -> bool:
        """비교 뷰 단축키로 키를 처리했으면 참"""
        if self._view is None:
            return False
        return self._view.handle_key_event(event)

    @property
    def position_sec(self) -> float:
        """비교 뷰 시점(초), 아직 없으면 0"""
        return 0.0 if self._view is None else self._view.position_sec

    @property
    def duration_sec(self) -> float:
        """비교 뷰 대상 길이(초), 아직 없으면 0"""
        return 0.0 if self._view is None else self._view.duration_sec

    @property
    def is_playing(self) -> bool:
        """비교 뷰 재생 여부"""
        return False if self._view is None else self._view.is_playing

    @property
    def source_path(self) -> str:
        """비교 중인 영상 경로, 없으면 빈 문자열"""
        return "" if self._view is None else self._view.source_path

    def _ensure_view(self) -> FrameCompareView | None:
        """처음 비교 모드로 들어갈 때만 뷰를 만들어 비교 페이지에 채움

        - 뷰는 만들어진 뒤에야 테마 적용 신호를 받지 못하므로 생성 직후 현재 팔레트를 직접 적용함
        """
        if self._view is not None:
            return self._view
        extractor = self._get_extractor()
        if extractor is None:
            return None
        view = FrameCompareView(
            extractor,
            self._get_seek_seconds,
            self._get_shortcuts,
            self._controls.guide_button.close_guide_if_open,
            self._controls.guide_button.is_guide_open,
            self._compare_page,
            collapsed=(
                False
                if self._get_compare_top_bar_collapsed is None
                else self._get_compare_top_bar_collapsed()
            ),
            on_collapsed_changed=self._on_compare_top_bar_collapsed_changed,
            apply_manual_target=self._apply_manual_target,
            cycle_background=self._cycle_background,
        )
        view.apply_theme(get_active_palette())
        view.set_stage_style(self._stage_background, self._stage_outline)
        self._page_layout().addWidget(view)
        self._connect_view(view)
        self._view = view
        return view

    def _page_layout(self) -> QVBoxLayout:
        """비교 페이지의 빈 레이아웃을 한 번만 만듦"""
        existing = self._compare_page.layout()
        if isinstance(existing, QVBoxLayout):
            return existing
        created = QVBoxLayout(self._compare_page)
        created.setContentsMargins(0, 0, 0, 0)
        return created

    def _connect_view(self, view: FrameCompareView) -> None:
        """비교 뷰 신호를 창이 쓸 수 있도록 다시 내보냄"""
        view.step_requested.connect(self.step_requested)
        view.position_changed.connect(self.position_changed)
        view.duration_changed.connect(self.duration_changed)
        view.playing_changed.connect(self.playing_changed)
        view.rate_changed.connect(self.rate_changed)
        view.mute_toggled.connect(self.mute_toggled)
        view.notice_requested.connect(self.notice_requested)
        view.close_requested.connect(self.close_requested)
        view.preview_requested.connect(self.preview_requested)
        view.fullscreen_requested.connect(self.fullscreen_requested)



def apply_stays_on_top(
    pinned: bool,
    *,
    controls: PlaybackControls,
    on_changed: Callable[[bool], None],
) -> None:
    """핀 상태를 조작 바에 반영하고 저장을 요청

    - 창 소유자와 실제 Z 순서는 컨트롤러가 정하고, 이 함수는 표시만 맞춤
    """
    controls.set_always_on_top(pinned)
    on_changed(pinned)


def toggle_fullscreen(
    window: QWidget,
    *,
    controls: PlaybackControls,
    stored: bool,
    on_changed: Callable[[bool], None],
) -> None:
    """전체화면과 창 모드를 전환하고 하단 바 아이콘과 저장값을 맞춤"""
    if window.isFullScreen():
        window.showNormal()
    else:
        window.showFullScreen()
    fullscreen = window.isFullScreen()
    controls.set_fullscreen(fullscreen)
    if fullscreen != stored:
        on_changed(fullscreen)

