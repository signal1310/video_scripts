"""화질 비교 창: 비교 대상 드롭다운, 3모드 캔버스, 확대 배율, 타임라인, 재생 통합

- 배치와 문구는 `frame_compare_layout.py`, 그리기와 조작은 `frame_compare_canvas.py`가 맡음
- 정지 화면은 `frame_compare_worker.py` 실행기가 무압축 프레임을 추출하고, 재생은 `frame_compare_player.py`가 맡음
- 재생 중에는 엔진 프레임을, 일시중지와 1프레임 이동 뒤에는 정확한 추출 프레임을 화면에 씀
- Shift+Space는 재생을 멈추고 현재 시점과 창 위치를 그대로 미리 보기로 넘기는 전환 신호를 보냄
"""

from __future__ import annotations

from collections.abc import Callable

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
from src.gui.dialogs.frame_compare_canvas import MODE_SIDE, MODE_SPLIT, MODE_TOGGLE
from src.gui.dialogs.frame_compare_meta import CompareSession
from src.gui.dialogs.frame_compare_playback_rules import PlaybackSettings
from src.gui.dialogs.frame_compare_player import ComparePlaybackControls
from src.gui.dialogs.frame_compare_status import CompareStatusIndicator
from src.gui.dialogs.frame_compare_styles import build_stylesheet, refresh_icons
from src.gui.dialogs.frame_compare_worker import (
    ExtractionRequest,
    ExtractOutcome,
    FrameCompareExtraction,
    FrameExtractor,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.service.frame_compare_decoder import DecodedFrame

IMAGE_DECODE_ERROR: str = "추출한 프레임 이미지를 읽지 못했습니다."
_MS_PER_SECOND: int = 1000


def _frame_image(frame: DecodedFrame | None, fallback: bytes) -> QImage:
    """메모리 디코더의 RGB24 프레임이나 FFmpeg가 만든 BMP 바이트를 QImage로 바꿈"""
    if frame is None:
        return QImage.fromData(fallback, "BMP")
    return QImage(frame.data, frame.width, frame.height, frame.bytes_per_line, QImage.Format.Format_RGB888)


class FrameCompareWindow(QWidget, ThemeAwareMixin):
    """화질 비교 전용 창: 비교 대상 전환, 3모드 캔버스, 확대 배율, 재생과 타임라인"""

    preview_requested = pyqtSignal(str, float)

    def __init__(
        self,
        extractor: FrameExtractor,
        get_seek_seconds: Callable[[], int],
        playback: PlaybackSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(layout.WINDOW_TITLE)
        self.setMinimumSize(layout.WINDOW_MIN_WIDTH_PX, layout.WINDOW_MIN_HEIGHT_PX)
        self.resize(layout.WINDOW_WIDTH_PX, layout.WINDOW_HEIGHT_PX)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._get_seek_seconds = get_seek_seconds
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
        self._player = ComparePlaybackControls(self._timeline, playback, self)
        self._player.frames_ready.connect(self._on_playback_frames)
        self._player.position_changed.connect(self._on_playback_position)
        self._player.playing_changed.connect(self._on_playing_changed)
        self._player.end_reached.connect(self._on_playback_end)
        self.set_mode(MODE_SPLIT)
        self.set_zoom_preset(ZOOM_FIT)
        self.init_theme_listener()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._toolbar = layout.build_toolbar(self, self.set_mode, self.set_zoom_preset)
        outer.addWidget(self._toolbar.filename.parentWidget())
        self._score_label = compare_meta.build_score_label(self)
        outer.addWidget(self._score_label)
        self._meta = compare_meta.build_meta_row(self)
        outer.addWidget(self._meta.original.parentWidget())
        self._stage = layout.build_stage(
            self, lambda _scale: layout.sync_zoom_input(self._canvas, self._toolbar), lambda _show: None
        )
        self._canvas = self._stage.canvas
        self._canvas.zoomed_manually.connect(
            lambda: layout.clear_button_checks(self._toolbar.zoom_buttons)
        )
        outer.addWidget(self._canvas.parentWidget(), stretch=1)
        self._toolbar.zoom_input.editingFinished.connect(self._apply_zoom_input)
        self._timeline = layout.build_timeline(
            self,
            self.step_frame,
            lambda percent: self.set_time(self._duration * percent / 100.0, immediate=True),
            lambda value: self.set_time(value / _MS_PER_SECOND),
            self.toggle_playback,
            lambda rate: self._player.select_rate(rate),
            lambda: self._player.toggle_mute(),
            lambda value: self._player.set_volume(value),
        )
        outer.addWidget(self._timeline.slider.parentWidget())

    def apply_theme(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 창 스타일과 캔버스, 아이콘 색을 맞춤"""
        self.setStyleSheet(build_stylesheet(palette))
        self._canvas.apply_palette(palette)
        refresh_icons(self._toolbar, self._timeline, palette)
        self._player.sync_controls()

    def load_session(self, session: CompareSession) -> None:
        """새 비교 대상을 창에 올리고 재생기와 프레임을 그 대상으로 다시 엶"""
        self._stop_playback()
        self._hide_overlay()
        self._player.reload_settings()
        self._session = session
        self._target_key = session.plan.default_key
        self._position_sec = clamp_time_sec(session.position_sec, session.duration_sec)
        self._reset_view_pending = True
        self._toolbar.filename.setText(session.filename)
        self._toolbar.filename.setToolTip(session.filename)
        self.setWindowTitle(f"{layout.WINDOW_TITLE} - {session.filename}")
        self._rebuild_target_menu(session.plan)
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

    def _rebuild_target_menu(self, plan: ComparePlan) -> None:
        layout.fill_target_menu(self._toolbar.target_menu, plan, self.set_target)
        self._toolbar.target_button.setEnabled(bool(plan.targets))

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
        for action in self._toolbar.target_menu.actions():
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
        for key, button in self._toolbar.mode_buttons.items():
            button.setChecked(key == mode)

    def set_zoom_preset(self, preset: str) -> None:
        """확대 배율 버튼을 적용하고 버튼 체크와 배율 입력 칸을 맞춤"""
        self._canvas.apply_zoom_preset(preset)
        for key, button in self._toolbar.zoom_buttons.items():
            button.setChecked(key == preset)
        layout.sync_zoom_input(self._canvas, self._toolbar)

    def set_time(self, seconds: float, *, immediate: bool = False) -> None:
        """시점을 옮기고, 재생 중이면 이어서 재생하고 아니면 그 시점 프레임을 다시 불러옴

        - 즉시 요청은 대표 시점, 프레임 이동, 구간 이동처럼 한 번의 정확한 이동에 씀
        """
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
        """재생과 일시중지를 전환. 멈출 때는 현재 시점의 정확한 프레임으로 안착"""
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
        original = _frame_image(outcome.frames.original_frame, outcome.frames.original_bytes)
        compared = _frame_image(outcome.frames.compared_frame, outcome.frames.compared_bytes)
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

    def _reveal_loading(self) -> None:
        """기준 시간을 넘긴 추출에 알약 배지나 빈 화면 안내를 표시"""
        self._status.reveal()

    def _hide_overlay(self) -> None:
        """로딩 알약과 오버레이, 지연 타이머를 숨김"""
        self._status.hide()

    def _show_error(self, body: str) -> None:
        """오류 오버레이를 표시"""
        self._status.show_error(body)

    def _sync_position_widgets(self) -> None:
        """현재 시점을 막대, 시각, 메타 줄, 대표 시점 칩에 반영"""
        compare_meta.sync_position(self._timeline, self._meta, self._position_sec, self._duration)

    def _sync_target_static(self) -> None:
        """비교 대상 라벨, 경로 툴팁, 캔버스 라벨, 점수 줄을 맞춤"""
        plan = self._plan()
        target = self._target()
        if plan is None or target is None:
            return
        compare_meta.sync_target_labels(
            self._meta, self._toolbar.target_button, self._canvas, plan, target
        )
        scores = () if self._session is None else self._session.scores
        compare_meta.show_score(self._score_label, target, scores)

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

    def _apply_zoom_input(self) -> None:
        """입력한 배율을 적용하고, 포커스를 창으로 돌려 단축키 조작이 바로 이어지게 함"""
        if not layout.apply_zoom_input(self._canvas, self._toolbar):
            return
        if self._toolbar.zoom_input.hasFocus():
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _request_preview(self) -> None:
        """재생을 멈추고 현재 시점 그대로 미리 보기로 전환해 달라고 요청"""
        self._stop_playback()
        if self._session is not None and self._session.source_path:
            self.preview_requested.emit(self._session.source_path, self._position_sec)

    def _handle_playback_key(self, key: int) -> bool:
        """재생, 배속, 음소거 단축키를 처리했는지"""
        if key == Qt.Key.Key_K:
            self.toggle_playback()
        elif key == Qt.Key.Key_BracketLeft:
            self._player.step_rate(-1)
        elif key == Qt.Key.Key_BracketRight:
            self._player.step_rate(1)
        elif key == Qt.Key.Key_Backslash:
            self._player.reset_rate()
        elif key == Qt.Key.Key_M:
            self._player.toggle_mute()
        else:
            return False
        return True

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """미리 보기와 같은 키 배치로 재생, 시점, 모드, 배율을 조작하고 Space, Esc로 닫음

        - 배율 입력 칸이 키를 받는 동안에는 창 단축키가 숫자와 기호 입력을 가로채지 않음
        """
        if event is None:
            return
        if self._toolbar.zoom_input.hasFocus():
            super().keyPressEvent(event)
            return
        key = event.key()
        modifiers = event.modifiers()
        if key == Qt.Key.Key_Space and modifiers & Qt.KeyboardModifier.ShiftModifier:
            self._request_preview()
            event.accept()
            return
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
            if self._toolbar.target_menu.isVisible():
                self._toolbar.target_menu.close()
            else:
                self._stop_playback()
                self.close()
            event.accept()
            return
        if self._handle_playback_key(key):
            event.accept()
            return
        mode_keys: dict[int, str] = {
            Qt.Key.Key_1: MODE_SPLIT,
            Qt.Key.Key_2: MODE_TOGGLE,
            Qt.Key.Key_3: MODE_SIDE,
        }
        if key in mode_keys:
            self.set_mode(mode_keys[key])
        elif key == Qt.Key.Key_C and self._canvas.mode() == MODE_TOGGLE:
            if not event.isAutoRepeat():
                self._canvas.set_original_pinned(not self._canvas.original_pinned())
        elif key == Qt.Key.Key_0:
            self.set_zoom_preset(ZOOM_FIT)
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            layout.manual_zoom(self._canvas, self._toolbar, 1)
        elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            layout.manual_zoom(self._canvas, self._toolbar, -1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            direction = -1 if key == Qt.Key.Key_Left else 1
            factor = (
                PREVIEW_LARGE_SEEK_MULTIPLIER
                if modifiers & Qt.KeyboardModifier.ShiftModifier
                else 1
            )
            self.set_time(
                shift_time_sec(
                    self._position_sec, direction * self._get_seek_seconds() * factor, self._duration
                ),
                immediate=True,
            )
        elif key == Qt.Key.Key_Comma:
            self.step_frame(-1)
        elif key == Qt.Key.Key_Period:
            self.step_frame(1)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

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


