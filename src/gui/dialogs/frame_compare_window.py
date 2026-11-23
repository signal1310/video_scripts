"""화질 비교 창: 다크 시네마 캔버스, 2줄 상단 바, 하단 재생 바, 사용자 지정 단축키

- 배치는 `frame_compare_layout.py`, 상단 접기와 오버레이는 `frame_compare_top_bar.py`가 맡음
- 시점 프레임은 `frame_compare_worker.py` 실행기가 추출하고 재생은 `frame_compare_player.py`가 맡음
- 키 입력은 `frame_compare_shortcuts.py`가 단축키 설정과 대조해 동작으로 바꿈
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QHideEvent, QImage, QKeyEvent
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from src.core.analysis.frame_compare import (
    ZOOM_FIT,
    ComparePlan,
    FrameSource,
    clamp_time_sec,
    frame_step_seconds,
    shift_time_sec,
)
from src.core.settings.ui_state import PREVIEW_LARGE_SEEK_MULTIPLIER
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs import frame_compare_meta as compare_meta
from src.gui.dialogs.frame_compare_canvas import MODE_TOGGLE
from src.gui.dialogs.frame_compare_meta import CompareSession
from src.gui.dialogs.frame_compare_playback_rules import PlaybackSettings
from src.gui.dialogs.frame_compare_player import ComparePlaybackControls
from src.gui.dialogs.frame_compare_shortcuts import (
    CompareKeyController,
    CompareShortcutHandlers,
    apply_timeline_tooltips,
)
from src.gui.dialogs.frame_compare_status import CompareStatusIndicator
from src.gui.dialogs.frame_compare_styles import (
    build_stylesheet,
    refresh_timeline_icons,
    update_fullscreen_button,
)
from src.gui.dialogs.frame_compare_top_bar import CompareTopBar
from src.gui.dialogs.frame_compare_worker import (
    ExtractionRequest,
    ExtractOutcome,
    FrameCompareExtraction,
    FrameExtractor,
)
from src.gui.shortcut_events import shortcut_events
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin

IMAGE_DECODE_ERROR: str = "추출한 프레임 이미지를 읽지 못했습니다."
_MS_PER_SECOND: int = 1000


class FrameCompareWindow(QWidget, ThemeAwareMixin):
    """화질 비교 전용 창: 비교 대상 전환, 3모드 캔버스, 상단 접기, 재생과 단축키"""

    preview_requested = pyqtSignal(str, float)

    def __init__(
        self,
        extractor: FrameExtractor,
        get_seek_seconds: Callable[[], int],
        get_shortcuts: Callable[[], Mapping[str, str]],
        playback: PlaybackSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(layout.WINDOW_TITLE)
        self.setMinimumSize(layout.WINDOW_MIN_WIDTH_PX, layout.WINDOW_MIN_HEIGHT_PX)
        self.resize(layout.WINDOW_WIDTH_PX, layout.WINDOW_HEIGHT_PX)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._get_seek_seconds = get_seek_seconds
        self._get_shortcuts = get_shortcuts
        self._session: CompareSession | None = None
        self._target_key: str | None = None
        self._position_sec: float = 0.0
        self._reset_view_pending: bool = True
        self._build()
        self._status = CompareStatusIndicator(self._stage, self)
        self._extraction = FrameCompareExtraction(extractor, self)
        self._extraction.started.connect(self._show_loading)
        self._extraction.loaded.connect(self._on_frames_extracted)
        self._extraction.failed.connect(self._on_extraction_failed)
        self._player = ComparePlaybackControls(
            self._timeline, playback, self, shortcuts=get_shortcuts
        )
        self._player.frames_ready.connect(self._on_playback_frames)
        self._player.position_changed.connect(self._on_playback_position)
        self._player.playing_changed.connect(self._on_playing_changed)
        self._player.end_reached.connect(self._on_playback_end)
        self._top.set_safety_check(self._can_auto_hide)
        self.set_mode(layout.MODE_LABELS[0][0])
        self.set_zoom_preset(ZOOM_FIT)
        shortcut_events().changed.connect(self._on_shortcuts_changed)
        self._on_shortcuts_changed(dict(get_shortcuts()))
        self._keys = CompareKeyController(
            get_shortcuts=get_shortcuts,
            get_handlers=self._handlers,
            focus_in_editor=lambda: self._top.zoom_input.hasFocus(),
            close_menu=self._top.close_open_menu,
            close_guide=self._timeline.guide_button.close_guide_if_open,
            is_fullscreen=self.isFullScreen,
            toggle_fullscreen=self._toggle_fullscreen,
            close_window=self.close,
        )
        self.init_theme_listener()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._stage = layout.build_stage(
            self, lambda _scale: self._top.sync_zoom_input(), lambda _show: None
        )
        self._canvas = self._stage.canvas
        self._canvas.zoomed_manually.connect(self._on_manual_zoom)
        outer.addWidget(self._canvas.parentWidget(), stretch=1)
        self._top = CompareTopBar(
            self,
            self._canvas,
            on_mode=self.set_mode,
            on_zoom=self.set_zoom_preset,
            on_target=self.set_target,
        )
        self._top.zoom_input.editingFinished.connect(self._apply_zoom_input)
        self._timeline = layout.build_timeline(
            self,
            self.step_frame,
            lambda value: self.set_time(value / _MS_PER_SECOND),
            self.toggle_playback,
            lambda rate: self._player.select_rate(rate),
            lambda: self._player.toggle_mute(),
            lambda value: self._player.set_volume(value),
            self._toggle_fullscreen,
            self._get_shortcuts,
        )
        outer.addWidget(self._timeline.slider.parentWidget())

    def apply_theme(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 다크 시네마 스타일과 아이콘 색을 맞춤"""
        self.setStyleSheet(build_stylesheet(palette))
        self._canvas.apply_palette(palette)
        self._top.apply_theme(palette)
        refresh_timeline_icons(self._timeline, palette)
        self._player.sync_controls()
        update_fullscreen_button(self._timeline, self.isFullScreen())

    def load_session(self, session: CompareSession) -> None:
        """새 비교 대상을 창에 올리고 재생기와 프레임을 그 대상으로 다시 엶"""
        self._stop_playback()
        self._hide_overlay()
        self._player.reload_settings()
        self._session = session
        self._target_key = session.plan.default_key
        self._position_sec = clamp_time_sec(session.position_sec, session.duration_sec)
        self._reset_view_pending = True
        self._top.filename.setText(session.filename)
        self._top.filename.setToolTip(session.filename)
        self.setWindowTitle(f"{layout.WINDOW_TITLE} - {session.filename}")
        self._top.rebuild_target_menu(session.plan)
        self._canvas.fit()
        self.set_zoom_preset(ZOOM_FIT)
        self._timeline.slider.blockSignals(True)
        self._timeline.slider.setRange(0, round(session.duration_sec * _MS_PER_SECOND))
        self._timeline.slider.blockSignals(False)
        self._sync_position_widgets()
        self._sync_target_static()
        if not session.plan.can_compare:
            self._show_error(session.plan.reason)
            return
        self._reload_playback()
        self._request_precise_frame()

    def set_target(self, key: str) -> None:
        """비교 대상을 바꾸고 재생기를 새 대상으로 다시 엶"""
        plan = self._plan()
        if plan is None:
            return
        target = plan.target(key)
        if target is None:
            return
        was_playing = self._player.is_playing()
        self._target_key = key
        for action in self._top.target_menu.actions():
            action.setChecked(action.text() == target.label)
        self._sync_target_static()
        self._reload_playback()
        if was_playing:
            self._player.start()
            return
        self._request_precise_frame()

    def set_mode(self, mode: str) -> None:
        """비교 모드를 바꾸고 버튼 체크를 맞춤"""
        self._canvas.set_mode(mode)
        self._top.set_mode_checks(mode)

    def set_zoom_preset(self, preset: str) -> None:
        """확대 배율 버튼을 적용하고 버튼 체크와 배율 입력 칸을 맞춤"""
        self._canvas.apply_zoom_preset(preset)
        self._top.set_zoom_checks(preset)
        self._top.sync_zoom_input()

    def set_time(self, seconds: float, *, immediate: bool = False) -> None:
        """시점을 옮기고, 재생 중이면 이어서 재생하고 아니면 그 시점 프레임을 다시 불러옴"""
        self._position_sec = clamp_time_sec(seconds, self._duration)
        self._sync_position_widgets()
        if self._player.is_playing():
            self._player.seek(round(self._position_sec * _MS_PER_SECOND))
            return
        self._request_precise_frame(immediate=immediate)

    def step_frame(self, delta: int) -> None:
        """한 프레임 앞이나 뒤로 이동, 재생 중이면 먼저 멈춤"""
        if self._player.is_playing():
            self._player.pause()
        step = frame_step_seconds(0.0 if self._session is None else self._session.fps)
        self.set_time(self._position_sec + step * delta, immediate=True)

    def toggle_playback(self) -> None:
        """재생과 일시중지를 전환, 멈출 때는 현재 시점의 정확한 프레임으로 안착"""
        if self._player.is_playing():
            self.pause_playback()
        else:
            self._start_playback()

    def pause_playback(self) -> None:
        """재생을 멈추고 현재 시점의 정확한 프레임을 추출해 표시"""
        position_ms = self._player.position_ms()
        self._player.pause()
        self._position_sec = clamp_time_sec(position_ms / _MS_PER_SECOND, self._duration)
        self._sync_position_widgets()
        self._request_precise_frame()

    def start_playback(self) -> None:
        """현재 시점에서 재생을 시작, 이미 재생 중이면 아무것도 하지 않음"""
        if not self._player.is_playing():
            self._start_playback()

    @property
    def is_playing(self) -> bool:
        """지금 재생 중인지, 미리 보기로 전환할 때 넘김"""
        return self._player.is_playing()

    def _start_playback(self) -> None:
        """정지 화면의 시점에서 재생기를 옮겨 재생"""
        self._extraction.cancel()
        self._hide_overlay()
        self._player.seek(round(self._position_sec * _MS_PER_SECOND))
        self._player.start()

    def _on_playback_frames(self, original: object, compared: object) -> None:
        """재생 중 프레임 쌍을 캔버스에 그림"""
        if not isinstance(original, QImage) or not isinstance(compared, QImage):
            return
        self._apply_frames(original, compared)

    def _on_playback_position(self, position_ms: int) -> None:
        """재생 중 위치를 막대와 시각에 반영"""
        if not self._player.is_playing():
            return
        self._position_sec = clamp_time_sec(position_ms / _MS_PER_SECOND, self._duration)
        self._sync_position_widgets()

    def _on_playing_changed(self, playing: bool) -> None:
        """재생이 시작되면 정지 화면용 로딩 오버레이를 숨김"""
        if playing:
            self._hide_overlay()

    def _on_playback_end(self) -> None:
        """끝까지 재생하면 일시중지 상태로 마지막 위치에 머묾"""
        self._position_sec = self._duration
        self._sync_position_widgets()

    def _on_frames_extracted(self, outcome: object) -> None:
        """정밀 추출 프레임을 캔버스에 반영, 읽지 못한 이미지는 오류로 표시"""
        if not isinstance(outcome, ExtractOutcome) or outcome.frames is None:
            return
        original = compare_meta.frame_image(
            outcome.frames.original_frame, outcome.frames.original_bytes
        )
        compared = compare_meta.frame_image(
            outcome.frames.compared_frame, outcome.frames.compared_bytes
        )
        if original.isNull() or compared.isNull():
            self._show_error(IMAGE_DECODE_ERROR)
            return
        self._apply_frames(original, compared)

    def _on_extraction_failed(self, outcome: object) -> None:
        """추출 실패 사유를 오버레이에 표시"""
        if isinstance(outcome, ExtractOutcome):
            self._show_error(outcome.error)

    def _apply_frames(self, original: QImage, compared: QImage) -> None:
        """프레임 쌍을 캔버스에 넘기고, 새 세션의 첫 프레임만 맞춤으로 다시 맞춤"""
        reset_view = self._reset_view_pending
        self._reset_view_pending = False
        self._canvas.set_images(original, compared, reset_view=reset_view)
        self._hide_overlay()

    def _request_precise_frame(self, *, immediate: bool = True) -> None:
        """현재 시점과 대상의 정확한 프레임을 추출에 요청"""
        plan = self._plan()
        target = self._target()
        session = self._session
        if plan is None or target is None or session is None:
            return
        self._extraction.request(
            ExtractionRequest(
                original=plan.original,
                compared=target,
                frame_width=session.width,
                frame_height=session.height,
                time_sec=round(self._position_sec, 3),
                target_key=target.key,
                immediate=immediate,
            )
        )

    def _reload_playback(self) -> None:
        """현재 대상의 원본과 비교 파일로 재생기를 채움"""
        plan = self._plan()
        target = self._target()
        session = self._session
        if plan is None or target is None or session is None:
            return
        self._player.load(
            original_path=plan.original.path,
            compared_path=target.path,
            tier_key=target.tier_key,
            frame_width=session.width,
            frame_height=session.height,
            position_ms=round(self._position_sec * _MS_PER_SECOND),
        )

    def _stop_playback(self) -> None:
        """재생과 진행 중인 추출을 멈추고 열린 디코더와 공유 파일 핸들을 놓음"""
        self._player.stop()
        self._extraction.release()

    def _show_loading(self) -> None:
        """추출이 늦어질 때만 로딩을 표시, 프레임이 있으면 화면을 가리지 않음"""
        self._status.show_loading()

    def _hide_overlay(self) -> None:
        """로딩 알약과 오버레이, 지연 타이머를 숨김"""
        self._status.hide()

    def _show_error(self, body: str) -> None:
        """오류 오버레이를 표시"""
        self._status.show_error(body)

    def _sync_position_widgets(self) -> None:
        """현재 시점을 막대, 시각, 메타 줄에 반영"""
        compare_meta.sync_position(self._timeline, self._top.meta, self._position_sec, self._duration)

    def _sync_target_static(self) -> None:
        """비교 대상 라벨, 경로 툴팁, 캔버스 라벨, 점수 칩을 맞춤"""
        plan = self._plan()
        target = self._target()
        if plan is None or target is None:
            return
        compare_meta.sync_target_labels(
            self._top.meta, self._top.target_button, self._canvas, plan, target
        )
        scores = () if self._session is None else self._session.scores
        compare_meta.show_score(self._top.score, target, scores)

    def _plan(self) -> ComparePlan | None:
        return None if self._session is None else self._session.plan

    def _target(self) -> FrameSource | None:
        plan = self._plan()
        return None if plan is None else plan.target(self._target_key)

    @property
    def _duration(self) -> float:
        return 0.0 if self._session is None else self._session.duration_sec

    @property
    def position_sec(self) -> float:
        """지금 보고 있는 시점(초). 미리 보기로 전환할 때 넘김"""
        return self._position_sec

    @property
    def source_path(self) -> str:
        """비교 중인 표 영상의 경로. 미리 보기로 전환할 때 넘김"""
        return "" if self._session is None else self._session.source_path

    def _on_manual_zoom(self) -> None:
        """휠이나 드래그로 배율이 바뀌면 프리셋 체크를 풀고 입력 칸을 맞춤"""
        self._top.clear_zoom_checks()
        self._top.sync_zoom_input()

    def _apply_zoom_input(self) -> None:
        """입력한 배율을 적용하고, 포커스를 창으로 돌려 단축키 조작이 바로 이어지게 함"""
        if not self._top.apply_zoom_input():
            return
        if self._top.zoom_input.hasFocus():
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _toggle_fullscreen(self) -> None:
        """전체화면과 이전 창 크기를 전환하고 버튼 아이콘과 접근성 이름을 맞춤"""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        update_fullscreen_button(self._timeline, self.isFullScreen())

    def _request_preview(self) -> None:
        """재생을 멈추고 현재 시점 그대로 미리 보기로 전환해 달라고 요청"""
        self._stop_playback()
        if self._session is not None and self._session.source_path:
            self.preview_requested.emit(self._session.source_path, self._position_sec)

    def _seek_direction(self, direction: int, large: bool) -> None:
        """구간 이동 간격만큼 앞뒤로 옮기고, 크게 이동은 여러 배 간격을 씀"""
        factor = PREVIEW_LARGE_SEEK_MULTIPLIER if large else 1
        self.set_time(
            shift_time_sec(
                self._position_sec, direction * self._get_seek_seconds() * factor, self._duration
            ),
            immediate=True,
        )

    def _toggle_original(self) -> None:
        """즉시 전환 모드에서만 원본 고정을 뒤집음"""
        if self._canvas.mode() == MODE_TOGGLE:
            self._canvas.set_original_pinned(not self._canvas.original_pinned())

    def _handlers(self) -> CompareShortcutHandlers:
        """단축키 명령이 부를 창 동작 묶음"""
        return CompareShortcutHandlers(
            set_mode=self.set_mode,
            toggle_original=self._toggle_original,
            zoom_fit=lambda: self.set_zoom_preset(ZOOM_FIT),
            zoom_in=lambda: self._top.manual_zoom(1),
            zoom_out=lambda: self._top.manual_zoom(-1),
            toggle_controls=self._top.toggle_collapsed,
            seek=self._seek_direction,
            step_frame=self.step_frame,
            toggle_playback=self.toggle_playback,
            rate_step=self._player.step_rate,
            rate_reset=self._player.reset_rate,
            toggle_mute=self._player.toggle_mute,
            toggle_fullscreen=self._toggle_fullscreen,
            request_preview=self._request_preview,
        )

    def _can_auto_hide(self) -> bool:
        """상단 오버레이 자동 숨김을 막아야 하는 조작이 열려 있는지"""
        return not (
            self._top.target_menu.isVisible()
            or self._top.zoom_input.hasFocus()
            or self._timeline.guide_button.is_guide_open()
        )

    def _on_shortcuts_changed(self, shortcuts: object) -> None:
        """열린 창의 버튼 툴팁과 단축키 안내를 최신 설정으로 맞춤"""
        if not isinstance(shortcuts, Mapping):
            shortcuts = self._get_shortcuts()
        effective = dict(shortcuts)
        self._top.sync_shortcut_tooltips(effective)
        apply_timeline_tooltips(
            self._timeline,
            effective,
            muted=self._player.muted(),
            rate=self._player.rate(),
        )
        self._timeline.guide_button.refresh_entries()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """키 입력을 고정 닫기와 사용자 지정 단축키 처리기에 넘김"""
        if event is None:
            return
        if self._keys.handle(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event: QHideEvent | None) -> None:
        """창을 숨기면 재생과 추출을 멈춰 파일 잠금을 남기지 않음"""
        self._stop_playback()
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """창을 닫을 때 재생과 추출을 멈추고 스레드 종료를 기다림"""
        self._stop_playback()
        if event is not None:
            event.accept()

    def shutdown(self) -> None:
        """창 소유자가 끝날 때 재생과 추출 정리"""
        self._stop_playback()
