"""재인코딩 진행 창: 영상별 탭 카드, 실시간 비트레이트, 전체 조작, 결과 목록

- 비모달 창이며 닫기는 숨김. 배치는 계속 돌고, 하단 요약 칸으로 다시 연다
- 화면 스레드에서만 쓴다. 이벤트와 일시중지 신호는 컨트롤러가 화면 스레드로 보낸다
- 중지 확인 문구는 계획서 승인 문구를 그대로 쓴다(개별 중지와 전체 중지 두 종류)
"""

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.encode.encode_batch_book import STATUS_BY_STATE, EncodeBatchBook
from src.core.encode.encode_progress_view import (
    clock_text,
    kbps_text,
    overall_remaining_sec,
    progress_fraction,
    remaining_sec,
    speed_text,
)
from src.core.encode.encode_state import EncodeState
from src.gui.dialogs.encode_progress_card import PROGRESS_STEPS, JobCard, tab_title
from src.gui.styles.icons import create_icon_button, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.service.encode.encode_events import EVENT_LONG_PAUSE, EVENT_PROGRESS, EVENT_STATE, EncodeEvent
from src.service.encode.encode_job import EncodeJob

REFRESH_INTERVAL_MS: int = 1000
HEADER_BUTTON_PX: int = 32
WINDOW_MIN_WIDTH_PX: int = 380
WINDOW_MIN_HEIGHT_PX: int = 440
WINDOW_TITLE: str = "재인코딩 진행"

PAUSE_ALL_TIP: str = "진행 중인 모든 영상을 일시중지합니다."
RESUME_ALL_TIP: str = "일시중지한 영상을 다시 재인코딩합니다."
AFTER_CURRENT_TIP: str = "진행 중인 영상을 끝낸 뒤 멈춥니다."
STOP_ALL_TIP: str = "진행 중인 영상을 즉시 멈추고 대기로 되돌립니다. 확인 창을 한 번 거칩니다."
ITEM_STOP_CONFIRM_TEXT: str = "이 영상을 중지할까요? 처음부터 다시 시작해야 하며, 원본 파일은 바뀌지 않습니다."
ALL_STOP_CONFIRM_TEXT: str = "재인코딩을 중지할까요? 진행 중인 영상은 처음부터 다시 시작해야 합니다. 원본 파일은 바뀌지 않습니다."
CANCEL_BUTTON_TEXT: str = "취소"
STOP_BUTTON_TEXT: str = "중지"
FINISHED_HEADER_TEMPLATE: str = "재인코딩을 마쳤습니다. 완료 {done}개"
STOPPED_HEADER_TEMPLATE: str = "재인코딩을 중지했습니다. 완료 {done}개"
PROCESSING_HEADER_TEMPLATE: str = "전체 {processed}/{total} 처리"
RESULT_HEADERS: tuple[str, ...] = ("파일", "상태", "사유")
_UNFINISHED_STATES: frozenset[EncodeState] = frozenset({EncodeState.PENDING, EncodeState.RUNNING})


def ask_stop(parent: QWidget, text: str) -> bool:
    """중지 확인 창. 중지를 누르면 참, 취소나 닫기는 거짓"""
    box = QMessageBox(parent)
    box.setText(text)
    cancel = box.addButton(CANCEL_BUTTON_TEXT, QMessageBox.ButtonRole.RejectRole)
    stop = box.addButton(STOP_BUTTON_TEXT, QMessageBox.ButtonRole.DestructiveRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is stop


class ProgressController(Protocol):
    """진행 창이 쓰는 컨트롤러 신호와 제어 메서드. 구체 컨트롤러 모듈을 가져오지 않아 패키지 순환을 막음"""

    # - 클래스 속성 신호는 정적 타입에서 pyqtSignal로 보이므로 신호 자리는 Any로 둠
    event_received: Any
    batch_finished: Any
    pause_changed: Any
    item_pause_changed: Any

    def pause_all(self) -> None:
        """전체 일시중지"""

    def resume_all(self) -> None:
        """전체 재개"""

    def cancel_all(self) -> None:
        """전체 즉시 중지"""

    def request_stop_after_current(self) -> None:
        """현재 영상 후 중지"""

    def pause_one(self, source: str) -> None:
        """영상 하나 일시중지"""

    def resume_one(self, source: str) -> None:
        """영상 하나 재개"""

    def cancel_one(self, source: str) -> None:
        """영상 하나 즉시 중지"""


@dataclass(frozen=True, slots=True)
class _JobMeta:
    """항목의 원본 길이와 최대 비트레이트(그래프 기준선용)"""

    duration_sec: float
    maxrate_kbps: float | None


class EncodeProgressWindow(QWidget, ThemeAwareMixin):
    """재인코딩 배치의 진행 상황과 조작을 보여 주는 비모달 창"""

    def __init__(
        self,
        controller: ProgressController,
        book: EncodeBatchBook,
        parent: QWidget | None = None,
        confirm: Callable[[QWidget, str], bool] | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(WINDOW_MIN_WIDTH_PX, WINDOW_MIN_HEIGHT_PX)
        self.resize(WINDOW_MIN_WIDTH_PX, WINDOW_MIN_HEIGHT_PX)
        self._controller: ProgressController = controller
        self._book: EncodeBatchBook = book
        self._confirm: Callable[[QWidget, str], bool] = ask_stop if confirm is None else confirm
        self._meta: dict[str, _JobMeta] = {}
        self._cards: dict[str, JobCard] = {}
        self._total: int = 0
        self._order: dict[str, int] = {}
        self._started_at: float = time.monotonic()
        self._batch_paused: bool = False
        self._finished: bool = False
        self._stopped: bool = False
        self._ended_at: float | None = None
        self._build()
        controller.event_received.connect(self._on_event)
        controller.batch_finished.connect(self._on_batch_finished)
        controller.pause_changed.connect(self._on_batch_pause_changed)
        controller.item_pause_changed.connect(self._on_item_pause_changed)
        self._timer: QTimer = QTimer(self)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self._refresh_all)
        self._timer.start()
        self.init_theme_listener()

    def start_batch(self, jobs: Mapping[str, EncodeJob], total: int) -> None:
        """새 배치를 시작할 때 항목별 길이와 최대 비트레이트, 대상 수를 받고 이전 카드와 결과를 지움"""
        self._order = {path: index for index, path in enumerate(jobs, start=1)}
        self._meta = {
            path: _JobMeta(job.source_duration_sec, None if job.maxrate_kbps is None else float(job.maxrate_kbps))
            for path, job in jobs.items()
        }
        self._total = total
        self._started_at = time.monotonic()
        self._batch_paused = False
        self._finished = False
        self._stopped = False
        self._ended_at = None
        self._set_batch_controls_enabled(True)
        for path in list(self._cards):
            self._remove_card(path)
        self._refresh_results()
        self._refresh_all()

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """닫기는 창만 숨김. 배치는 계속 돌아감"""
        if event is not None:
            event.ignore()
        self.hide()

    def _build(self) -> None:
        self._header = QLabel()
        self._elapsed = QLabel()
        self._remaining = QLabel()
        self._overall_bar = QProgressBar()
        self._overall_bar.setRange(0, PROGRESS_STEPS)
        self._overall_bar.setTextVisible(False)
        self._overall_bar.setFixedHeight(8)
        self._pause_all_btn = create_icon_button(
            self, PAUSE_ALL_TIP, self._toggle_batch_pause, accessible_name="전체 일시중지", fixed_size=HEADER_BUTTON_PX
        )
        self._after_btn = create_icon_button(
            self,
            AFTER_CURRENT_TIP,
            self._request_stop_after,
            accessible_name="현재 영상 후 중지",
            fixed_size=HEADER_BUTTON_PX,
        )
        self._stop_all_btn = create_icon_button(
            self, STOP_ALL_TIP, self._stop_all, accessible_name="전체 중지", fixed_size=HEADER_BUTTON_PX
        )
        set_button_icon(self._pause_all_btn, "pause", pad_right=0)
        set_button_icon(self._after_btn, "square", pad_right=0)
        set_button_icon(self._stop_all_btn, "ban", pad_right=0)
        head = QHBoxLayout()
        head.addWidget(self._header, stretch=1)
        head.addWidget(self._pause_all_btn)
        head.addWidget(self._after_btn)
        head.addWidget(self._stop_all_btn)
        timing = QHBoxLayout()
        timing.addWidget(self._elapsed)
        timing.addStretch(1)
        timing.addWidget(self._remaining)
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        tab_bar = self._tabs.tabBar()
        if tab_bar is not None:
            tab_bar.setElideMode(Qt.TextElideMode.ElideMiddle)
        self._tabs.setVisible(False)
        self._results = QTableWidget(0, len(RESULT_HEADERS))
        self._results.setHorizontalHeaderLabels(list(RESULT_HEADERS))
        self._results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        vertical = self._results.verticalHeader()
        horizontal = self._results.horizontalHeader()
        if vertical is not None:
            vertical.setVisible(False)
        if horizontal is not None:
            horizontal.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout = QVBoxLayout(self)
        layout.addLayout(head)
        layout.addLayout(timing)
        layout.addWidget(self._overall_bar)
        results_box = QWidget()
        results_layout = QVBoxLayout(results_box)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.addWidget(QLabel("결과"))
        results_layout.addWidget(self._results, stretch=1)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self._tabs)
        split.addWidget(results_box)
        split.setChildrenCollapsible(False)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, stretch=1)

    def _add_card(self, path: str) -> JobCard:
        card = JobCard(os.path.basename(path))
        card.pause_toggled.connect(lambda: self._toggle_item(path))
        card.stop_requested.connect(lambda: self._stop_item(path))
        card.stop_after_requested.connect(lambda: self._switch_to_stop_after(path))
        card.set_paused(self._batch_paused)
        card.set_pause_enabled(not self._batch_paused)
        self._cards[path] = card
        self._add_tab(card, path)
        self._tabs.setVisible(True)
        return card

    def _add_tab(self, card: JobCard, path: str) -> None:
        """영상 카드를 순서 번호와 줄임 제목의 탭으로 추가하고, 탭 툴팁에 전체 파일 이름을 보여 줌"""
        order = self._order.get(path, 0)
        index = self._tabs.addTab(card, tab_title(order, os.path.basename(path)))
        self._tabs.setTabToolTip(index, f"{order}. {os.path.basename(path)}")

    def apply_theme(self, palette: ThemePalette) -> None:
        """탭을 팔레트 토큰으로 꾸밈: 선택 탭은 카드 바탕과 강조 밑줄, 나머지는 흐린 글자와 호버 바탕"""
        self._tabs.setStyleSheet(
            "QTabWidget::pane { border: none; background: transparent; }"
            "QTabWidget, QTabBar, QTabWidget > QWidget#qt_tabwidget_stackedwidget { background: transparent; }"
            f"QTabBar::tab {{ background: transparent; color: {palette.text_muted};"
            " padding: 6px 12px; margin-right: 4px; border: none;"
            " border-top-left-radius: 6px; border-top-right-radius: 6px; }"
            f"QTabBar::tab:hover {{ background: {palette.bg_hover}; color: {palette.text_primary}; }}"
            f"QTabBar::tab:selected {{ background: {palette.bg_card}; color: {palette.text_primary};"
            f" border-bottom: 2px solid {palette.progress_fill}; }}"
        )

    def _remove_card(self, path: str) -> None:
        card = self._cards.pop(path, None)
        if card is not None:
            index = self._tabs.indexOf(card)
            if index >= 0:
                self._tabs.removeTab(index)
            card.deleteLater()
        self._tabs.setVisible(bool(self._cards))

    def _now(self) -> float:
        return time.monotonic() - self._started_at

    def _batch_elapsed(self) -> float:
        """배치 경과 시간. 배치가 끝나면 끝난 시점에 고정해 더 늘어나지 않게 함"""
        return self._now() if self._ended_at is None else self._ended_at

    def _set_batch_controls_enabled(self, enabled: bool) -> None:
        """전체 조작 버튼을 배치 진행 중에만 누를 수 있게 함"""
        for button in (self._pause_all_btn, self._after_btn, self._stop_all_btn):
            button.setEnabled(enabled)

    def _on_event(self, event: object) -> None:
        if not isinstance(event, EncodeEvent):
            return
        if event.kind == EVENT_PROGRESS and event.sample is not None:
            self._on_progress(event)
        elif event.kind == EVENT_STATE and event.state is not None:
            self._on_state(event.source_path, event.state)
        elif event.kind == EVENT_LONG_PAUSE:
            card = self._cards.get(event.source_path)
            if card is not None:
                card.set_long_pause(True)

    def _on_progress(self, event: EncodeEvent) -> None:
        path = event.source_path
        sample = event.sample
        if sample is None:
            return
        meta = self._meta.get(path, _JobMeta(0.0, None))
        card = self._cards.get(path) or self._add_card(path)
        fraction = progress_fraction(sample.out_time_sec, meta.duration_sec)
        card.set_progress(fraction)
        card.set_values(
            f"{clock_text(sample.out_time_sec)} / {clock_text(meta.duration_sec)}",
            speed_text(sample.speed),
            clock_text(event.elapsed_sec),
            clock_text(remaining_sec(event.elapsed_sec, fraction)),
            kbps_text(event.instant_kbps),
        )
        card.series.add_sample(self._now(), event.instant_kbps)
        card.refresh_graph(self._now(), meta.maxrate_kbps)

    def _on_state(self, path: str, state: EncodeState) -> None:
        if state is EncodeState.RUNNING:
            if path not in self._cards:
                self._add_card(path)
        else:
            self._remove_card(path)
        self._refresh_results()
        self._refresh_all()

    def _on_item_pause_changed(self, path: str, paused: bool) -> None:
        card = self._cards.get(path)
        if card is None:
            return
        if paused and not card.is_paused:
            card.series.add_pause(self._now())
        card.set_paused(paused)

    def _on_batch_pause_changed(self, paused: bool) -> None:
        """전체 일시중지 상태를 카드와 머리글 버튼에 반영. 전체 일시중지 중에는 개별 버튼을 끔"""
        self._batch_paused = paused
        set_button_icon(self._pause_all_btn, "play" if paused else "pause", pad_right=0)
        self._pause_all_btn.setToolTip(RESUME_ALL_TIP if paused else PAUSE_ALL_TIP)
        for card in self._cards.values():
            if paused and not card.is_paused:
                card.series.add_pause(self._now())
            card.set_paused(paused)
            card.set_pause_enabled(not paused)

    def _on_batch_finished(self, _finished: object) -> None:
        self._finished = True
        self._ended_at = self._now()
        self._set_batch_controls_enabled(False)
        self._batch_paused = False
        self._refresh_results()
        self._refresh_all()

    def _toggle_item(self, path: str) -> None:
        card = self._cards.get(path)
        if card is None:
            return
        if card.is_paused:
            self._controller.resume_one(path)
        else:
            self._controller.pause_one(path)

    def _stop_item(self, path: str) -> None:
        if self._confirm(self, ITEM_STOP_CONFIRM_TEXT):
            self._controller.cancel_one(path)

    def _toggle_batch_pause(self) -> None:
        if self._batch_paused:
            self._controller.resume_all()
        else:
            self._controller.pause_all()

    def _stop_all(self) -> None:
        if self._confirm(self, ALL_STOP_CONFIRM_TEXT):
            self._stopped = True
            self._controller.cancel_all()

    def _request_stop_after(self) -> None:
        """현재 영상 후 중지 요청. 배치가 중지로 끝나므로 끝난 뒤 머리글을 중지 문구로 바꿈"""
        self._stopped = True
        self._controller.request_stop_after_current()

    def _switch_to_stop_after(self, path: str) -> None:
        """장시간 안내 링크: 현재 영상 후 중지를 예약하고 멈춘 영상을 다시 실행. 전체 일시중지면 전체 재개, 아니면 이 영상만 재개"""
        self._request_stop_after()
        if self._batch_paused:
            self._controller.resume_all()
        else:
            self._controller.resume_one(path)

    def _refresh_all(self) -> None:
        processed = self._book.processed()
        counts = self._book.counts_by_status()
        if self._finished:
            template = STOPPED_HEADER_TEMPLATE if self._stopped else FINISHED_HEADER_TEMPLATE
            header = template.format(done=counts[STATUS_BY_STATE[EncodeState.DONE]])
        else:
            header = PROCESSING_HEADER_TEMPLATE.format(processed=processed, total=self._total)
        elapsed = self._batch_elapsed()
        self._header.setText(header)
        self._elapsed.setText(f"경과 {clock_text(elapsed)}")
        remaining = "" if self._finished else f"남은 시간 {clock_text(overall_remaining_sec(elapsed, processed, self._total))}"
        self._remaining.setText(remaining)
        overall = 0 if self._total <= 0 else round(processed / self._total * PROGRESS_STEPS)
        self._overall_bar.setValue(overall)
        for path, card in self._cards.items():
            card.refresh_graph(elapsed, self._meta.get(path, _JobMeta(0.0, None)).maxrate_kbps)

    def _refresh_results(self) -> None:
        rows = [row for row in self._book.items() if row[1] not in _UNFINISHED_STATES]
        self._results.setRowCount(len(rows))
        for index, (path, state, reason) in enumerate(rows):
            self._results.setItem(index, 0, QTableWidgetItem(os.path.basename(path)))
            self._results.setItem(index, 1, QTableWidgetItem(STATUS_BY_STATE[state]))
            self._results.setItem(index, 2, QTableWidgetItem(reason))
