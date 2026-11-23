"""내장 미리 보기 창의 정보 표시를 재생기 상태와 표 값으로 채워 영상 위에 그리는 표시 담당 모듈"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from PyQt6.QtCore import QObject, QPoint, QRect, Qt, QTimer
from PyQt6.QtMultimedia import QMediaPlayer, QVideoFrame
from PyQt6.QtWidgets import QWidget

from src.core.config import ClassifierConfig
from src.core.settings.preview_info import (
    INFO_LAYOUT_SINGLE_LINE,
    INFO_MODE_DETAIL,
    INFO_MODE_OFF,
    INFO_MODE_SUMMARY,
    VALID_INFO_MODES,
)
from src.core.settings.ui_state import UiPreviewInfoState
from src.gui.preview.preview_info_items import (
    INFO_MODE_LABELS,
    PlayerSnapshot,
    PreviewVideoInfo,
    info_rows,
    info_value_colors,
)
from src.gui.preview.video_preview_overlay import (
    EDGE_MARGIN_PX,
    TOAST_DURATION_MS,
    PreviewInfoOverlay,
    PreviewToast,
)

_REFRESH_INTERVAL_MS: int = 250
_FPS_WINDOW_SECONDS: float = 1.0
_MULTI_SELECTION_NOTE: str = "선택 {count}개 중 재생 중"
_MODE_NOTICE: str = "정보 표시 {label}"


class PreviewInfoPresenter(QObject):
    """정보 표시 단계에 따라 오버레이를 갱신하고 위치 이동과 단계 전환, 실행 결과 안내를 처리

    - 단계가 꺼져 있거나 창이 숨겨져 있으면 갱신 타이머와 프레임 기록을 멈춰 재생에 부담을 주지 않음
    - 설정 값은 호출자가 넘긴 상태 객체에 바로 쓰고 on_changed로 저장을 요청
    """

    def __init__(
        self,
        window: QWidget,
        video: QWidget,
        player: QMediaPlayer,
        get_state: Callable[[], UiPreviewInfoState],
        on_changed: Callable[[], None],
        toast_area: Callable[[], QRect] | None = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._video = video
        self._player = player
        self._get_state = get_state
        self._on_changed = on_changed
        self._compare_mode: bool = False
        self._video_info: PreviewVideoInfo | None = None
        self._fallback_config = ClassifierConfig()
        self._frame_times: deque[float] = deque()
        self._overlay = PreviewInfoOverlay(window, self._area)
        self._overlay.anchor_changed.connect(self._on_anchor_changed)
        self._overlay.scale_changed.connect(self._on_scale_changed)
        self._overlay.box_changed.connect(self._on_box_changed)
        self._toast = PreviewToast(window, self._area if toast_area is None else toast_area)
        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self._render)
        sink = player.videoSink()
        if sink is not None:
            sink.videoFrameChanged.connect(self._on_video_frame)

    def set_compare_mode(self, enabled: bool) -> None:
        """비교 모드에서는 정보 표시 오버레이를 숨기고, 일반 모드로 돌아오면 다시 그림"""
        self._compare_mode = enabled
        if enabled:
            self._overlay.hide()
            return
        self._render()

    def set_video_info(self, info: PreviewVideoInfo | None) -> None:
        """표에서 가져온 지금 영상의 평가 결과를 반영해 다시 그림"""
        self._video_info = info
        self._render()

    def refresh(self) -> None:
        """설정과 창 표시 상태를 다시 읽어 타이머와 표시를 맞춤"""
        active = self._get_state().mode != INFO_MODE_OFF and self._window.isVisible()
        if active and not self._timer.isActive():
            self._timer.start()
        elif not active:
            self._timer.stop()
            self._frame_times.clear()
        # - 소유 창이 숨겨져도 도구 창은 플랫폼에 따라 남을 수 있어 직접 닫음
        if not self._window.isVisible():
            self._toast.dismiss()
        self._render()

    def relayout(self) -> None:
        """창 크기가 바뀌면 저장된 비율 위치로 표시와 안내를 다시 놓음"""
        if self._overlay.isVisible():
            self._overlay.reposition()
        if self._toast.isVisible():
            self._toast.reposition()

    def sync_stays_on_top(self, pinned: bool) -> None:
        """정보 표시와 토스트도 창의 핀 상태에 맞춰 항상 위에 둘지 함께 맞춤"""
        for widget in (self._overlay, self._toast):
            # - 플래그를 바꾸면 도구 창이 숨겨지므로, 바꾸기 전 표시 여부를 먼저 기억해 다시 표시
            was_visible = widget.isVisible()
            widget.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, pinned)
            if was_visible:
                widget.show()

    def cycle_mode(self) -> None:
        """끔, 요약, 상세 순서로 단계를 바꾸고 바뀐 단계를 안내"""
        state = self._get_state()
        modes = VALID_INFO_MODES
        state.mode = modes[(modes.index(state.mode) + 1) % len(modes)]
        self._on_changed()
        self.refresh()
        self.show_notice(_MODE_NOTICE.format(label=INFO_MODE_LABELS[state.mode]))

    def show_notice(self, text: str, *, duration_ms: int = TOAST_DURATION_MS) -> None:
        """영상 위에 실행 결과 문구를 잠깐 표시"""
        if text:
            self._toast.show_message(text, duration_ms=duration_ms)

    def _area(self) -> QRect:
        """오버레이가 놓일 수 있는 영상 영역, 가장자리 여백을 뺀 화면 전역 좌표"""
        top_left = self._video.mapToGlobal(QPoint(0, 0))
        return QRect(top_left, self._video.size()).adjusted(
            EDGE_MARGIN_PX, EDGE_MARGIN_PX, -EDGE_MARGIN_PX, -EDGE_MARGIN_PX
        )

    def _on_anchor_changed(self, anchor_x: float, anchor_y: float) -> None:
        state = self._get_state()
        state.anchor_x = anchor_x
        state.anchor_y = anchor_y
        self._on_changed()

    def _on_scale_changed(self, scale: int) -> None:
        self._get_state().scale = scale
        self._on_changed()
        self._render()

    def _on_box_changed(self, width: float, height: float) -> None:
        state = self._get_state()
        state.box_width = width
        state.box_height = height
        self._on_changed()

    def _on_video_frame(self, _frame: QVideoFrame) -> None:
        if self._timer.isActive():
            self._frame_times.append(time.monotonic())

    def _render(self) -> None:
        state = self._get_state()
        keys = {INFO_MODE_SUMMARY: state.summary_items, INFO_MODE_DETAIL: state.detail_items}.get(
            state.mode, []
        )
        if not keys or not self._window.isVisible() or self._compare_mode:
            self._overlay.hide()
            return
        video = self._video_info
        config = video.config if video is not None else self._fallback_config
        note = (
            _MULTI_SELECTION_NOTE.format(count=video.selected_count)
            if video is not None and video.selected_count > 1
            else ""
        )
        self._overlay.set_content(
            info_rows(keys, video, self._snapshot(), config, short=True),
            value_colors=info_value_colors(keys, video),
            single_line=state.layout == INFO_LAYOUT_SINGLE_LINE,
            note=note,
            anchor=(state.anchor_x, state.anchor_y),
            scale=state.scale,
            box=(state.box_width, state.box_height),
        )
        if not self._overlay.isVisible():
            self._overlay.show()
            self._overlay.raise_()

    def _snapshot(self) -> PlayerSnapshot:
        sink = self._player.videoSink()
        frame = sink.videoFrame() if sink is not None else QVideoFrame()
        valid = frame.isValid()
        start_us = frame.startTime() if valid else -1
        return PlayerSnapshot(
            decoded_size=(frame.width(), frame.height()) if valid else None,
            view_size=(self._video.width(), self._video.height()),
            pixel_format=frame.pixelFormat().name if valid else "",
            play_fps=self._measured_fps(),
            position_ms=self._player.position(),
            duration_ms=self._player.duration(),
            frame_time_us=start_us if start_us >= 0 else None,
        )

    def _measured_fps(self) -> float | None:
        """최근 1초 동안 그려진 프레임 수로 계산한 초당 프레임, 기록이 부족하면 None"""
        now = time.monotonic()
        while self._frame_times and now - self._frame_times[0] > _FPS_WINDOW_SECONDS:
            self._frame_times.popleft()
        if len(self._frame_times) < 2:
            return None
        elapsed = self._frame_times[-1] - self._frame_times[0]
        return (len(self._frame_times) - 1) / elapsed if elapsed > 0 else None
