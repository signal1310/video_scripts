"""재인코딩 진행 창: 작업별 카드, 실시간 비트레이트, 전체 조작, 결과 목록

- 비모달 창이며 닫기는 숨김. 배치는 계속 돌고, 하단 요약 칸으로 다시 연다
- 화면 스레드에서만 쓴다. 이벤트와 일시 정지 신호는 컨트롤러가 화면 스레드로 보낸다
- 중지 확인 문구는 계획서 승인 문구를 그대로 쓴다(개별 중지와 전체 중지 두 종류)
"""

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.encode.encode_batch_book import STATUS_BY_STATE, EncodeBatchBook
from src.core.encode.encode_progress_view import (
    BitrateSeries,
    clock_text,
    kbps_text,
    overall_remaining_sec,
    progress_fraction,
    remaining_sec,
    speed_text,
)
from src.core.encode.encode_state import EncodeState
from src.gui.styles.icons import create_icon_button, get_icon, set_button_icon
from src.gui.widgets.bitrate_graph import BitrateGraph
from src.service.encode.encode_events import EVENT_LONG_PAUSE, EVENT_PROGRESS, EVENT_STATE, EncodeEvent
from src.service.encode.encode_job import EncodeJob

REFRESH_INTERVAL_MS: int = 1000
PROGRESS_STEPS: int = 1000
ICON_BUTTON_PX: int = 28
HEADER_BUTTON_PX: int = 32
INFO_ICON_PX: int = 14
WINDOW_WIDTH_PX: int = 720
WINDOW_HEIGHT_PX: int = 640
WINDOW_MIN_WIDTH_PX: int = 460
CARD_COLUMNS: int = 2
WINDOW_TITLE: str = "재인코딩 진행"

PAUSE_ALL_TIP: str = "진행 중인 모든 영상을 일시 정지합니다."
RESUME_ALL_TIP: str = "일시 정지한 영상을 다시 재인코딩합니다."
AFTER_CURRENT_TIP: str = "진행 중인 영상을 끝낸 뒤 멈춥니다."
STOP_ALL_TIP: str = "진행 중인 영상을 즉시 멈추고 대기로 되돌립니다. 확인 창을 한 번 거칩니다."
ITEM_PAUSE_TIP: str = "이 영상을 일시 정지합니다. 멈춘 동안 GPU 인코더 세션을 계속 사용합니다."
ITEM_RESUME_TIP: str = "멈춘 영상을 이어서 재인코딩합니다."
ITEM_STOP_TIP: str = "이 영상을 중지합니다. 대기로 돌아가며 확인 창을 한 번 거칩니다."
LEGEND_TIP: str = "실선: 순간 비트레이트\n점선: 최대 비트레이트\n빗금: 일시 정지 구간"
LONG_PAUSE_TEXT: str = (
    "멈춘 동안 GPU 인코더 세션을 계속 사용해 그래픽 드라이버나 다른 작업에 영향을 줄 수 있으며, "
    '오래 멈출 때는 <a href="stop-after">현재 영상 후 중지로 전환</a>을 권장합니다.'
)
ITEM_STOP_CONFIRM_TEXT: str = "이 영상을 중지할까요? 처음부터 다시 시작해야 하며, 원본 파일은 바뀌지 않습니다."
ALL_STOP_CONFIRM_TEXT: str = "재인코딩을 중지할까요? 진행 중인 영상은 처음부터 다시 시작해야 합니다. 원본 파일은 바뀌지 않습니다."
CANCEL_BUTTON_TEXT: str = "취소"
STOP_BUTTON_TEXT: str = "중지"
FINISHED_HEADER_TEMPLATE: str = "재인코딩을 마쳤습니다. 완료 {done}개"
PROCESSING_HEADER_TEMPLATE: str = "전체 {processed}/{total} 처리"
RESULT_HEADERS: tuple[str, ...] = ("파일", "상태", "사유")
CARD_CAPTIONS: tuple[str, ...] = ("진행 위치", "재인코딩 속도", "경과 시간", "남은 시간", "순간 비트레이트")
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


class _JobCard(QFrame):
    """작업 하나의 이름, 일시 정지와 중지 버튼, 진행 막대, 수치, 비트레이트 그래프"""

    pause_toggled = pyqtSignal()
    stop_requested = pyqtSignal()
    stop_after_requested = pyqtSignal()

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EncodeJobCard")
        self._paused: bool = False
        self.series: BitrateSeries = BitrateSeries()
        self._pause_btn = create_icon_button(
            self,
            ITEM_PAUSE_TIP,
            lambda: self.pause_toggled.emit(),
            accessible_name="영상 일시 정지",
            fixed_size=ICON_BUTTON_PX,
        )
        self._stop_btn = create_icon_button(
            self,
            ITEM_STOP_TIP,
            lambda: self.stop_requested.emit(),
            accessible_name="영상 중지",
            fixed_size=ICON_BUTTON_PX,
        )
        set_button_icon(self._pause_btn, "pause")
        set_button_icon(self._stop_btn, "x")
        self._bar = QProgressBar()
        self._bar.setRange(0, PROGRESS_STEPS)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._values: tuple[QLabel, ...] = tuple(QLabel() for _ in CARD_CAPTIONS)
        grid = QGridLayout()
        for index, caption in enumerate(CARD_CAPTIONS):
            row, column = divmod(index, CARD_COLUMNS)
            self._values[index].setWordWrap(True)
            grid.addWidget(QLabel(caption), row, column * 2)
            grid.addWidget(self._values[index], row, column * 2 + 1)
        self._graph = BitrateGraph(self)
        info = QLabel()
        info.setPixmap(get_icon("info", size=INFO_ICON_PX).pixmap(INFO_ICON_PX, INFO_ICON_PX))
        info.setToolTip(LEGEND_TIP)
        info.setAccessibleName("그래프 범례")
        graph_head = QHBoxLayout()
        graph_head.addWidget(QLabel("순간 비트레이트"))
        graph_head.addStretch(1)
        graph_head.addWidget(info)
        self._notice = QLabel(LONG_PAUSE_TEXT)
        self._notice.setTextFormat(Qt.TextFormat.RichText)
        self._notice.setWordWrap(True)
        self._notice.setVisible(False)
        self._notice.linkActivated.connect(lambda _href: self.stop_after_requested.emit())
        head = QHBoxLayout()
        title = QLabel(name)
        title.setMinimumWidth(0)
        head.addWidget(title, stretch=1)
        head.addWidget(self._pause_btn)
        head.addWidget(self._stop_btn)
        layout = QVBoxLayout(self)
        layout.addLayout(head)
        layout.addWidget(self._bar)
        layout.addLayout(grid)
        layout.addLayout(graph_head)
        layout.addWidget(self._graph)
        layout.addWidget(self._notice)

    @property
    def is_paused(self) -> bool:
        """이 항목이 일시 정지 상태이면 참"""
        return self._paused

    def set_paused(self, paused: bool) -> None:
        """일시 정지 여부에 맞춰 버튼 아이콘과 툴팁을 바꾸고, 재개하면 장시간 안내를 숨김"""
        self._paused = paused
        set_button_icon(self._pause_btn, "play" if paused else "pause")
        self._pause_btn.setToolTip(ITEM_RESUME_TIP if paused else ITEM_PAUSE_TIP)
        if not paused:
            self._notice.setVisible(False)

    def set_long_pause(self, visible: bool) -> None:
        """멈춘 시간이 기준을 넘었을 때 안내를 보이거나 숨김"""
        self._notice.setVisible(visible)

    def set_progress(self, fraction: float | None) -> None:
        """진행 막대 값. 측정 값이 없으면 0"""
        self._bar.setValue(0 if fraction is None else round(fraction * PROGRESS_STEPS))

    def set_values(self, position: str, speed: str, elapsed: str, remaining: str, kbps: str) -> None:
        """카드의 수치 문자열을 캡션 순서대로 설정"""
        for label, text in zip(self._values, (position, speed, elapsed, remaining, kbps), strict=True):
            label.setText(text)

    def refresh_graph(self, now_sec: float, max_kbps: float | None) -> None:
        """기록한 점과 현재 시각으로 그래프를 다시 그림"""
        self._graph.set_series(self.series.points(), max_kbps, now_sec)


class EncodeProgressWindow(QWidget):
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
        self.resize(WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX)
        self.setMinimumWidth(WINDOW_MIN_WIDTH_PX)
        self._controller: ProgressController = controller
        self._book: EncodeBatchBook = book
        self._confirm: Callable[[QWidget, str], bool] = ask_stop if confirm is None else confirm
        self._meta: dict[str, _JobMeta] = {}
        self._cards: dict[str, _JobCard] = {}
        self._total: int = 0
        self._started_at: float = time.monotonic()
        self._batch_paused: bool = False
        self._finished: bool = False
        self._build()
        controller.event_received.connect(self._on_event)
        controller.batch_finished.connect(self._on_batch_finished)
        controller.pause_changed.connect(self._on_batch_pause_changed)
        controller.item_pause_changed.connect(self._on_item_pause_changed)
        self._timer: QTimer = QTimer(self)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self._refresh_all)
        self._timer.start()

    def start_batch(self, jobs: Mapping[str, EncodeJob], total: int) -> None:
        """새 배치를 시작할 때 항목별 길이와 최대 비트레이트, 대상 수를 받고 이전 카드와 결과를 지움"""
        self._meta = {
            path: _JobMeta(job.source_duration_sec, None if job.maxrate_kbps is None else float(job.maxrate_kbps))
            for path, job in jobs.items()
        }
        self._total = total
        self._started_at = time.monotonic()
        self._batch_paused = False
        self._finished = False
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
            self, PAUSE_ALL_TIP, self._toggle_batch_pause, accessible_name="전체 일시 정지", fixed_size=HEADER_BUTTON_PX
        )
        self._after_btn = create_icon_button(
            self,
            AFTER_CURRENT_TIP,
            self._controller.request_stop_after_current,
            accessible_name="현재 영상 후 중지",
            fixed_size=HEADER_BUTTON_PX,
        )
        self._stop_all_btn = create_icon_button(
            self, STOP_ALL_TIP, self._stop_all, accessible_name="전체 중지", fixed_size=HEADER_BUTTON_PX
        )
        set_button_icon(self._pause_all_btn, "pause")
        set_button_icon(self._after_btn, "square")
        set_button_icon(self._stop_all_btn, "ban")
        head = QHBoxLayout()
        head.addWidget(self._header, stretch=1)
        head.addWidget(self._pause_all_btn)
        head.addWidget(self._after_btn)
        head.addWidget(self._stop_all_btn)
        timing = QHBoxLayout()
        timing.addWidget(self._elapsed)
        timing.addStretch(1)
        timing.addWidget(self._remaining)
        self._jobs_container = QWidget()
        self._jobs_layout = QVBoxLayout(self._jobs_container)
        self._jobs_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self._jobs_container)
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
        layout.addWidget(scroll, stretch=1)
        layout.addWidget(QLabel("결과"))
        layout.addWidget(self._results, stretch=1)

    def _add_card(self, path: str) -> _JobCard:
        card = _JobCard(os.path.basename(path))
        card.pause_toggled.connect(lambda: self._toggle_item(path))
        card.stop_requested.connect(lambda: self._stop_item(path))
        card.stop_after_requested.connect(self._controller.request_stop_after_current)
        self._cards[path] = card
        self._jobs_layout.insertWidget(self._jobs_layout.count() - 1, card)
        return card

    def _remove_card(self, path: str) -> None:
        card = self._cards.pop(path, None)
        if card is not None:
            self._jobs_layout.removeWidget(card)
            card.deleteLater()

    def _now(self) -> float:
        return time.monotonic() - self._started_at

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
        self._batch_paused = paused
        set_button_icon(self._pause_all_btn, "play" if paused else "pause")
        self._pause_all_btn.setToolTip(RESUME_ALL_TIP if paused else PAUSE_ALL_TIP)
        for card in self._cards.values():
            if paused and not card.is_paused:
                card.series.add_pause(self._now())
            card.set_paused(paused)

    def _on_batch_finished(self, _finished: object) -> None:
        self._finished = True
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
            self._controller.cancel_all()

    def _refresh_all(self) -> None:
        processed = self._book.processed()
        counts = self._book.counts_by_status()
        if self._finished:
            header = FINISHED_HEADER_TEMPLATE.format(done=counts[STATUS_BY_STATE[EncodeState.DONE]])
        else:
            header = PROCESSING_HEADER_TEMPLATE.format(processed=processed, total=self._total)
        elapsed = self._now()
        self._header.setText(header)
        self._elapsed.setText(f"경과 {clock_text(elapsed)}")
        self._remaining.setText(f"남은 시간 {clock_text(overall_remaining_sec(elapsed, processed, self._total))}")
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
