"""화질 비교 창: 비교 대상 드롭다운, 3모드 캔버스, 확대 배율, 타임라인과 대표 시점, 비동기 프레임 추출

- 배치와 문구는 `frame_compare_layout.py`, 그리기와 조작은 `frame_compare_canvas.py`가 맡음
- 추출은 전용 QThread에서 실행하고, 시점이나 대상이 바뀌면 이전 요청을 취소함
- 워커 본문은 스레드 밖 함수(`run_extract_body`)로 분리해 직접 테스트
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QImage, QKeyEvent
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from src.core.analysis.frame_analysis import TierScore
from src.core.analysis.frame_compare import (
    ZOOM_FIT,
    ComparePlan,
    FrameSource,
    analysis_score_text,
    clamp_time_sec,
    format_percent,
    format_timecode,
    frame_step_seconds,
    shift_time_sec,
)
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs.frame_compare_canvas import MODE_SIDE, MODE_SPLIT, MODE_TOGGLE
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.service.frame_compare_service import ExtractedFrames, FrameCompareFileMissing

EXTRACT_DEBOUNCE_MS: int = 120
LARGE_SEEK_MULTIPLIER: int = 6
_MS_PER_SECOND: int = 1000
_SCORE_TIP_TEMPLATE: str = (
    "화질 분석에서 잰 {tier} 축소 후보의 점수입니다. "
    "VMAF는 100에 가까울수록, SSIM은 1에 가까울수록 원본과 비슷합니다."
)
_MARK_RATIO_TOLERANCE: float = 0.008


@dataclass(frozen=True, slots=True)
class CompareSession:
    """화질 비교 창이 여는 영상 한 건의 비교 계획, 재생 정보와 캐시된 화질 분석 점수"""

    plan: ComparePlan
    duration_sec: float
    fps: float
    filename: str
    position_sec: float = 0.0
    scores: tuple[TierScore, ...] = ()
    width: int = 0
    height: int = 0


class FrameExtractor(Protocol):
    """화질 비교 창이 쓰는 프레임 추출기"""

    def extract_pair(
        self,
        original: FrameSource,
        compared: FrameSource,
        *,
        time_sec: float,
        frame_width: int,
        frame_height: int,
        cancel_event: threading.Event,
    ) -> ExtractedFrames:
        """같은 시점의 두 프레임 PNG 바이트를 반환"""
        ...


@dataclass(frozen=True, slots=True)
class ExtractOutcome:
    """추출 요청 한 건의 결과: 요청 순번, 프레임, 오류 문구, 파일 부재 여부, 시점"""

    request_id: int
    frames: ExtractedFrames | None
    error: str = ""
    file_missing: bool = False
    time_sec: float = 0.0


def run_extract_body(
    extractor: FrameExtractor,
    request: ExtractOutcome,
    original: FrameSource,
    compared: FrameSource,
    frame_width: int,
    frame_height: int,
    cancel_event: threading.Event,
    sink: Callable[[ExtractOutcome], None],
) -> None:
    """프레임 쌍 추출을 실행해 결과나 원인별 오류를 넘김

    - 취소는 결과를 보내지 않고, 파일 부재와 실행 오류는 구분해 넘김
    """
    try:
        frames = extractor.extract_pair(
            original,
            compared,
            time_sec=request.time_sec,
            frame_width=frame_width,
            frame_height=frame_height,
            cancel_event=cancel_event,
        )
    except InterruptedError:
        return
    except FrameCompareFileMissing as error:
        sink(ExtractOutcome(request.request_id, None, str(error), True, request.time_sec))
        return
    except Exception as error:
        sink(ExtractOutcome(request.request_id, None, str(error), False, request.time_sec))
        return
    sink(ExtractOutcome(request.request_id, frames, "", False, request.time_sec))


class FrameCompareWorker(QThread):
    """프레임 쌍 추출을 실행하는 전용 스레드"""

    completed = pyqtSignal(object)

    def __init__(
        self,
        extractor: FrameExtractor,
        request: ExtractOutcome,
        original: FrameSource,
        compared: FrameSource,
        frame_width: int,
        frame_height: int,
        cancel_event: threading.Event,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._extractor: FrameExtractor = extractor
        self._request: ExtractOutcome = request
        self._original: FrameSource = original
        self._compared: FrameSource = compared
        self._frame_width: int = frame_width
        self._frame_height: int = frame_height
        self._cancel_event: threading.Event = cancel_event

    def cancel(self) -> None:
        """진행 중인 추출 취소 요청"""
        self._cancel_event.set()

    def run(self) -> None:
        """워커 스레드 본문"""
        run_extract_body(
            self._extractor,
            self._request,
            self._original,
            self._compared,
            self._frame_width,
            self._frame_height,
            self._cancel_event,
            self.completed.emit,
        )


class FrameCompareWindow(QWidget, ThemeAwareMixin):
    """화질 비교 전용 창: 비교 대상 전환, 3모드 캔버스, 확대 배율, 타임라인과 대표 시점"""

    def __init__(
        self,
        extractor: FrameExtractor,
        get_seek_seconds: Callable[[], int],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(layout.WINDOW_TITLE)
        self.setMinimumSize(layout.WINDOW_MIN_WIDTH_PX, layout.WINDOW_MIN_HEIGHT_PX)
        self.resize(layout.WINDOW_WIDTH_PX, layout.WINDOW_HEIGHT_PX)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._extractor: FrameExtractor = extractor
        self._get_seek_seconds = get_seek_seconds
        self._session: CompareSession | None = None
        self._target_key: str | None = None
        self._position_sec: float = 0.0
        self._loaded_time: float = -1.0
        self._loaded_key: str | None = None
        self._request_id: int = 0
        self._worker: FrameCompareWorker | None = None
        self._build()
        self._debounce: QTimer = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(EXTRACT_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._start_pending_extract)
        self.set_mode(MODE_SPLIT)
        self.set_zoom_preset(ZOOM_FIT)
        self.init_theme_listener()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._toolbar = layout.build_toolbar(self, self.set_mode, self.set_zoom_preset)
        outer.addWidget(self._toolbar.filename.parentWidget())
        self._score_label = layout.build_score_label(self)
        outer.addWidget(self._score_label)
        self._meta = layout.build_meta_row(self)
        outer.addWidget(self._meta.original.parentWidget())
        self._stage = layout.build_stage(self, self._on_scale_changed, lambda _show: None)
        self._canvas = self._stage.canvas
        outer.addWidget(self._canvas.parentWidget(), stretch=1)
        self._timeline = layout.build_timeline(
            self,
            self.step_frame,
            self._on_mark_clicked,
            lambda value: self.set_time(value / _MS_PER_SECOND),
        )
        outer.addWidget(self._timeline.slider.parentWidget())

    def apply_theme(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 창 스타일과 캔버스, 아이콘 색을 맞춤"""
        self.setStyleSheet(layout.build_stylesheet(palette))
        self._canvas.apply_palette(palette)
        layout.refresh_icons(self._toolbar, self._timeline, palette)

    def load_session(self, session: CompareSession) -> None:
        """새 비교 대상을 창에 올리고 기본 대상과 시점 프레임을 불러옴"""
        self._cancel_worker()
        self._session = session
        self._target_key = session.plan.default_key
        self._position_sec = clamp_time_sec(session.position_sec, session.duration_sec)
        self._loaded_time = -1.0
        self._loaded_key = None
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
        self._schedule_extract(immediate=True)

    def _rebuild_target_menu(self, plan: ComparePlan) -> None:
        layout.fill_target_menu(self._toolbar.target_menu, plan, self.set_target)
        self._toolbar.target_button.setEnabled(bool(plan.targets))

    def set_target(self, key: str) -> None:
        """비교 대상을 바꾸고 같은 시점의 프레임을 다시 불러옴"""
        plan = self._plan()
        if plan is None:
            return
        target = plan.target(key)
        if target is None:
            return
        self._target_key = key
        for action in self._toolbar.target_menu.actions():
            action.setChecked(action.text() == target.label)
        self._sync_target_static()
        self._schedule_extract(immediate=True)

    def set_mode(self, mode: str) -> None:
        """비교 모드를 바꾸고 버튼 체크를 맞춤"""
        self._canvas.set_mode(mode)
        for key, button in self._toolbar.mode_buttons.items():
            button.setChecked(key == mode)

    def set_zoom_preset(self, preset: str) -> None:
        """확대 배율 버튼을 적용하고 버튼 체크를 맞춤"""
        self._canvas.apply_zoom_preset(preset)
        for key, button in self._toolbar.zoom_buttons.items():
            button.setChecked(key == preset)
        self._toolbar.zoom_badge.setText(self._canvas.percent_text())

    def set_time(self, seconds: float) -> None:
        """시점을 옮기고 같은 시점이 아니면 프레임을 다시 불러옴"""
        self._position_sec = clamp_time_sec(seconds, self._duration)
        self._sync_position_widgets()
        self._schedule_extract(immediate=False)

    def step_frame(self, delta: int) -> None:
        """한 프레임 앞이나 뒤로 이동"""
        step = frame_step_seconds(0.0 if self._session is None else self._session.fps)
        self.set_time(self._position_sec + step * delta)

    def _on_mark_clicked(self, percent: int) -> None:
        self.set_time(self._duration * percent / 100.0)

    def _on_scale_changed(self, scale: float) -> None:
        self._toolbar.zoom_badge.setText(format_percent(scale))

    def _sync_position_widgets(self) -> None:
        total = self._duration
        self._timeline.slider.blockSignals(True)
        self._timeline.slider.setValue(round(self._position_sec * _MS_PER_SECOND))
        self._timeline.slider.blockSignals(False)
        self._timeline.time_label.setText(
            f"{format_timecode(self._position_sec)} / {format_timecode(total)}"
        )
        self._meta.time.setText(f"시점 {format_timecode(self._position_sec)}")
        ratio = self._position_sec / total if total > 0 else 0.0
        for index, chip in enumerate(self._timeline.mark_buttons, start=1):
            chip.setChecked(abs(ratio - index / 10.0) < _MARK_RATIO_TOLERANCE)

    def _sync_target_static(self) -> None:
        plan = self._plan()
        target = self._target()
        if plan is None or target is None:
            return
        self._meta.original.setText(f"원본 {plan.original.detail}")
        self._meta.target.setText(f"{target.label} {target.detail}")
        self._meta.target.setToolTip(target.tooltip)
        self._toolbar.target_button.setText(target.label)
        self._canvas.set_labels(plan.original.label, target.label, target.tooltip)
        self._update_score(target)

    def _update_score(self, target: FrameSource) -> None:
        session = self._session
        if session is None or target.tier_key is None:
            self._score_label.hide()
            return
        score = next((item for item in session.scores if item.tier == target.tier_key), None)
        if score is None:
            self._score_label.hide()
            return
        tier = RESOLUTION_TIER_BY_KEY.get(target.tier_key)
        display = target.tier_key if tier is None else tier.display
        text = analysis_score_text(display, score.vmaf, score.ssim)
        if not text:
            self._score_label.hide()
            return
        self._score_label.setText(text)
        self._score_label.setToolTip(_SCORE_TIP_TEMPLATE.format(tier=display))
        self._score_label.show()

    def _plan(self) -> ComparePlan | None:
        return None if self._session is None else self._session.plan

    def _target(self) -> FrameSource | None:
        plan = self._plan()
        return None if plan is None else plan.target(self._target_key)

    @property
    def _duration(self) -> float:
        return 0.0 if self._session is None else self._session.duration_sec

    def _schedule_extract(self, *, immediate: bool) -> None:
        """시점이나 대상이 바뀌면 이전 추출을 취소하고 새 추출을 예약"""
        plan = self._plan()
        if plan is None or not plan.can_compare:
            return
        if immediate:
            self._start_pending_extract()
        else:
            self._debounce.start()

    def _start_pending_extract(self) -> None:
        """현재 시점과 대상의 프레임을 비동기로 추출"""
        plan = self._plan()
        target = self._target()
        session = self._session
        if plan is None or target is None or session is None:
            return
        loaded_key = self._loaded_key
        if loaded_key == target.key and abs(self._position_sec - self._loaded_time) < 0.001:
            return
        self._cancel_worker()
        self._request_id += 1
        cancel_event = threading.Event()
        request = ExtractOutcome(self._request_id, None, time_sec=round(self._position_sec, 3))
        self._show_loading()
        worker = FrameCompareWorker(
            self._extractor,
            request,
            plan.original,
            target,
            session.width,
            session.height,
            cancel_event,
            self,
        )
        worker.completed.connect(self._on_worker_completed)
        self._worker = worker
        worker.start()

    def _cancel_worker(self) -> None:
        """진행 중인 추출을 취소하고 스레드 종료를 기다림"""
        self._debounce.stop()
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            worker.wait()

    def _show_loading(self) -> None:
        self._show_overlay(layout.LOADING_TEXT)

    def _show_error(self, body: str) -> None:
        self._show_overlay(layout.ERROR_TITLE, body)

    def _show_overlay(self, title: str, body: str = "") -> None:
        self._stage.title.setText(title)
        self._stage.body.setText(body)
        self._stage.overlay.show()
        self._stage.overlay.raise_()

    def _on_worker_completed(self, outcome: object) -> None:
        """최신 요청의 결과만 반영하고 지연 도착한 이전 결과는 버림"""
        if not isinstance(outcome, ExtractOutcome) or outcome.request_id != self._request_id:
            return
        self._worker = None
        if outcome.error or outcome.frames is None:
            self._show_error(outcome.error)
            return
        original = QImage.fromData(outcome.frames.original_png, "PNG")
        compared = QImage.fromData(outcome.frames.compared_png, "PNG")
        if original.isNull() or compared.isNull():
            self._show_error(outcome.error)
            return
        self._canvas.set_images(original, compared)
        self._loaded_time = outcome.time_sec
        target = self._target()
        self._loaded_key = None if target is None else target.key
        self._stage.overlay.hide()

    def _manual_zoom(self, direction: int) -> None:
        """키보드 단계 확대, 축소. 배율 버튼 체크는 풀어 현재 배율만 배지에 남김"""
        self._canvas.zoom_step(direction)
        layout.clear_button_checks(self._toolbar.zoom_buttons)
        self._toolbar.zoom_badge.setText(self._canvas.percent_text())

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """미리 보기와 같은 키 배치로 시점, 모드, 배율을 조작하고 Space, Esc로 닫음"""
        if event is None:
            return
        key = event.key()
        modifiers = event.modifiers()
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
            if self._toolbar.target_menu.isVisible():
                self._toolbar.target_menu.close()
            else:
                self.close()
            event.accept()
            return
        mode_keys: dict[int, str] = {Qt.Key.Key_1: MODE_SPLIT, Qt.Key.Key_2: MODE_TOGGLE, Qt.Key.Key_3: MODE_SIDE}
        if key in mode_keys:
            self.set_mode(mode_keys[key])
        elif key == Qt.Key.Key_C and self._canvas.mode() == MODE_TOGGLE:
            self._canvas.set_original_pinned(not self._canvas.original_pinned())
        elif key == Qt.Key.Key_0:
            self.set_zoom_preset(ZOOM_FIT)
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._manual_zoom(1)
        elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self._manual_zoom(-1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            direction = -1 if key == Qt.Key.Key_Left else 1
            factor = LARGE_SEEK_MULTIPLIER if modifiers & Qt.KeyboardModifier.ShiftModifier else 1
            self.set_time(
                shift_time_sec(
                    self._position_sec, direction * self._get_seek_seconds() * factor, self._duration
                )
            )
        elif key == Qt.Key.Key_Comma:
            self.step_frame(-1)
        elif key == Qt.Key.Key_Period:
            self.step_frame(1)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """창을 닫을 때 실행 중인 추출을 취소하고 스레드 종료를 기다림"""
        self._cancel_worker()
        if event is not None:
            event.accept()

    def shutdown(self) -> None:
        """창 소유자가 끝날 때 실행 중인 추출 정리"""
        self._cancel_worker()
