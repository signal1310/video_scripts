"""재인코딩 진행 창: 영상별 카드(또는 단일 패널), 실시간 비트레이트, 전체 조작, 전체 속도와 결과 목록

- 비모달 창이며 닫기는 숨김. 배치는 계속 돌고, 하단 요약 칸으로 다시 연다
- 화면 스레드에서만 쓴다. 이벤트와 일시중지 신호는 컨트롤러가 화면 스레드로 보낸다
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
    QWidget,
)

from src.core.encode.encode_batch_book import ENCODED_STATES, STATUS_BY_STATE, EncodeBatchBook
from src.core.encode.encode_progress import ProgressSample
from src.core.encode.encode_progress_values import (
    DiscardRow,
    ResultRow,
    batch_speed_text,
    display_fraction,
    original_kbps,
    result_lines,
    saved_summary_text,
    saved_totals,
)
from src.core.encode.encode_progress_view import (
    clock_text,
    overall_remaining_sec,
    run_counts,
)
from src.core.encode.encode_state import EncodeState
from src.gui.dialogs.encode_confirm_dialogs import ask_stop
from src.gui.dialogs.encode_progress_card import (
    NO_POSITION_TIP,
    PROGRESS_STEPS,
    JobCard,
    card_fields,
)
from src.gui.dialogs.encode_progress_layout import (
    PAUSE_ALL_TIP,
    RESULT_TAB_TEMPLATE,
    ProgressActions,
    build_progress_widgets,
)
from src.gui.styles.icons import set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.service.encode.encode_events import EVENT_LONG_PAUSE, EVENT_PROGRESS, EVENT_STATE, EncodeEvent
from src.service.encode.encode_job import EncodeJob
from src.service.encode.encode_reasons import LARGER_THAN_SOURCE, PART_LEFT

REFRESH_INTERVAL_MS: int = 1000
RESULT_TAB_INDEX: int = 1
WINDOW_MIN_WIDTH_PX: int = 460
WINDOW_MIN_HEIGHT_PX: int = 560
WINDOW_TITLE: str = "재인코딩 진행"
# - 시작 시점에 이 상태인 항목만 이번 실행의 인코딩 대상으로 셈
RUN_START_STATES: frozenset[EncodeState] = frozenset({EncodeState.PENDING, EncodeState.RUNNING})

RESUME_ALL_TIP: str = "일시중지한 영상을 다시 재인코딩합니다."
ITEM_STOP_CONFIRM_TEXT: str = "이 영상을 중지할까요? 처음부터 다시 시작해야 하며, 원본 파일은 바뀌지 않습니다."
ALL_STOP_CONFIRM_TEXT: str = "재인코딩을 중지할까요? 진행 중인 영상은 처음부터 다시 시작해야 합니다. 원본 파일은 바뀌지 않습니다."
FINISHED_HEADER_TEMPLATE: str = "재인코딩을 마쳤습니다. 완료 {done}개"
STOPPED_HEADER_TEMPLATE: str = "재인코딩을 중지했습니다. 완료 {done}개"
PROCESSING_HEADER_TEMPLATE: str = "전체 {processed}/{total} 처리"


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


class ProgressMedia(Protocol):
    """영상 열기, 미리 보기, 교체 대기 조작을 조정자가 맡는 인터페이스. 파일 확인과 확인 창도 조정자가 처리"""

    def open_file(self, path: str) -> None:
        """영상 파일을 기본 플레이어로 연다"""

    def preview_file(self, path: str) -> None:
        """영상 파일을 설정된 미리 보기 방식으로 연다"""

    def reveal_leftover(self, source: str) -> bool:
        """임시 파일이 남은 행이면 그 파일을 탐색기에서 선택하고 참을 돌려줌. 그 밖의 행은 거짓"""
        ...

    def retry_replace(self, source: str) -> None:
        """교체 대기 항목 하나를 다시 배치"""

    def forfeit_replace(self, source: str) -> None:
        """교체 대기 항목 하나를 포기해 실패로 바꿈"""

    def trash_leftover(self, source: str) -> None:
        """같은 자리 임시 파일 때문에 실패한 항목이 쓰려던 임시 파일을 확인 뒤 휴지통으로 옮김"""


@dataclass(frozen=True, slots=True)
class BatchInfo:
    """배치를 시작할 때 창에 알리는 값: 대상 수, 단일 패널 여부(동시 실행 1개), 인코더 표기"""

    total: int
    single_panel: bool
    encoder_label: str


class EncodeProgressWindow(QWidget, ThemeAwareMixin):
    """재인코딩 배치의 진행 상황과 조작을 보여 주는 비모달 창"""

    # - 종료 예약 배너의 "종료 예약 취소"를 누르면 보냄. 실제 취소는 조정자가 맡음
    close_cancel_requested = pyqtSignal()

    def __init__(
        self,
        controller: ProgressController,
        book: EncodeBatchBook,
        media: ProgressMedia,
        parent: QWidget | None = None,
        confirm: Callable[[QWidget, str], bool] | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(WINDOW_MIN_WIDTH_PX, WINDOW_MIN_HEIGHT_PX)
        self.resize(WINDOW_MIN_WIDTH_PX, WINDOW_MIN_HEIGHT_PX)
        self._controller: ProgressController = controller
        self._book: EncodeBatchBook = book
        self._media: ProgressMedia = media
        self._confirm: Callable[[QWidget, str], bool] = ask_stop if confirm is None else confirm
        self._jobs: dict[str, EncodeJob] = {}
        self._cards: dict[str, JobCard] = {}
        self._speeds: dict[str, float | None] = {}
        self._positions: dict[str, float] = {}
        self._fractions: dict[str, float] = {}
        self._result_rows: dict[str, ResultRow] = {}
        self._discard_rows: dict[str, DiscardRow] = {}
        # - 이번 실행에서 인코딩할 대상 경로: 시작 때 대기 또는 진행 중인 항목. 이전 실행에서 끝난 항목은 제외
        self._run_paths: frozenset[str] = frozenset()
        self._total: int = 0
        self._encoder_label: str = ""
        self._order: dict[str, int] = {}
        self._started_at: float = time.monotonic()
        self._batch_paused: bool = False
        self._finished: bool = False
        self._stopped: bool = False
        self._ended_at: float | None = None
        self._stop_all_requested: bool = False
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

    def start_batch(self, jobs: Mapping[str, EncodeJob], info: BatchInfo) -> None:
        """새 배치를 시작할 때 항목별 계획 입력과 배치 값을 받고 이전 카드와 결과를 지움"""
        self._order = {path: index for index, path in enumerate(jobs, start=1)}
        self._jobs = dict(jobs)
        self._run_paths = frozenset(path for path, state, _reason in self._book.items() if state in RUN_START_STATES)
        self._total = info.total
        self._encoder_label = info.encoder_label
        self._result_rows = {}
        self._discard_rows = {}
        self._started_at = time.monotonic()
        self._batch_paused = False
        self._apply_batch_pause_look(False)
        self._finished = False
        self._stopped = False
        self._ended_at = None
        self._stop_all_requested = False
        self._set_batch_controls_enabled(True)
        self._tabs.set_tab_bar_visible(not info.single_panel)
        for path in list(self._cards):
            self._remove_card(path)
        self._refresh_results()
        self._refresh_all()

    def set_result(self, row: ResultRow) -> None:
        """완료 항목의 결과 경로와 크기를 받아 결과 목록과 완료분 절약을 갱신"""
        self._result_rows[row.source_path] = row
        self._refresh_results()
        self._refresh_all()

    def set_discard(self, row: DiscardRow) -> None:
        """폐기 항목의 예상, 결과 크기를 받아 결과 목록의 크기 변화 칸을 갱신"""
        self._discard_rows[row.source_path] = row
        self._refresh_results()
        self._refresh_all()

    def refresh_rows(self) -> None:
        """조정자가 기록을 다시 읽어 상태 저장소를 맞춘 뒤 결과 목록과 요약을 갱신"""
        self._refresh_results()
        self._refresh_all()

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """닫기는 창만 숨김. 배치는 계속 돌아감"""
        if event is not None:
            event.ignore()
        self.hide()

    def set_close_pending(self, pending: bool) -> None:
        """종료 예약 배너를 보이거나 숨김"""
        self._banner.setVisible(pending)

    def clear_stop_after_request(self) -> None:
        """현재 영상 후 중지 예약을 취소한 뒤 머리글이 중지 문구를 쓰지 않도록, 전체 중지 여부만 남김"""
        self._stopped = self._stop_all_requested

    def _build(self) -> None:
        widgets = build_progress_widgets(
            self,
            ProgressActions(
                toggle_batch_pause=self._toggle_batch_pause,
                request_stop_after=self._request_stop_after,
                stop_all=self._stop_all,
                cancel_close=lambda: self.close_cancel_requested.emit(),
            ),
        )
        self._banner = widgets.banner
        self._header = widgets.header
        self._elapsed = widgets.elapsed
        self._remaining = widgets.remaining
        self._overall_bar = widgets.overall_bar
        self._pause_all_btn = widgets.pause_all_btn
        self._after_btn = widgets.after_btn
        self._stop_all_btn = widgets.stop_all_btn
        self._totals = widgets.totals
        self._tabs = widgets.tabs
        self._results = widgets.results
        self._idle = widgets.idle
        self._view_switch = widgets.view_switch
        self._pages = widgets.pages
        self._results.activated.connect(self._open_source)
        self._results.preview_requested.connect(self._preview_source)
        self._results.retry_requested.connect(self._media.retry_replace)
        self._results.forfeit_requested.connect(self._media.forfeit_replace)
        self._results.reveal_requested.connect(self._media.reveal_leftover)
        self._results.trash_requested.connect(self._media.trash_leftover)

    def _add_card(self, path: str) -> JobCard:
        card = JobCard(os.path.basename(path))
        card.pause_toggled.connect(lambda: self._toggle_item(path))
        card.stop_requested.connect(lambda: self._stop_item(path))
        card.stop_after_requested.connect(self._request_stop_after)
        card.activated.connect(lambda: self._open_source(path))
        card.preview_requested.connect(lambda: self._preview_source(path))
        card.set_paused(self._batch_paused)
        card.set_pause_enabled(not self._batch_paused)
        self._cards[path] = card
        self._idle.setVisible(False)
        self._tabs.add_card(card, self._order.get(path, 0), os.path.basename(path))
        return card

    def apply_theme(self, palette: ThemePalette) -> None:
        """창 바탕은 기본 바탕 색으로 두고, 하위 위젯의 꾸밈은 각 위젯이 맡음"""
        self.setStyleSheet(f"EncodeProgressWindow {{ background: {palette.bg_base}; }}")
        self._tabs.apply_palette(palette)
        self._totals.apply_palette(palette)
        self._results.apply_palette(palette)
        self._view_switch.apply_palette(palette)

    def _remove_card(self, path: str) -> None:
        self._speeds.pop(path, None)
        self._positions.pop(path, None)
        self._fractions.pop(path, None)
        card = self._cards.pop(path, None)
        self._idle.setVisible(not self._cards)
        if card is not None:
            self._tabs.remove_card(card)

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
            self._on_progress(event, event.sample)
        elif event.kind == EVENT_STATE and event.state is not None:
            self._on_state(event.source_path, event.state)
        elif event.kind == EVENT_LONG_PAUSE:
            card = self._cards.get(event.source_path)
            if card is not None:
                card.set_long_pause(True)

    def _on_progress(self, event: EncodeEvent, sample: ProgressSample) -> None:
        path = event.source_path
        job = self._jobs.get(path)
        if job is None:
            return
        card = self._cards.get(path) or self._add_card(path)
        fraction = display_fraction(sample.out_time_sec, sample.frame, job.source_duration_sec, job.fps)
        self._speeds[path] = sample.speed
        if fraction is not None:
            self._fractions[path] = fraction
        if sample.out_time_sec is not None:
            self._positions[path] = sample.out_time_sec
        card.set_progress(fraction, "" if sample.out_time_sec is not None else NO_POSITION_TIP)
        self._tabs.set_progress(card, fraction)
        card.set_fields(
            card_fields(
                sample,
                job,
                event.elapsed_sec,
                fraction,
                event.instant_kbps,
                event.projected_bytes,
                self._encoder_label,
            )
        )
        card.series.add_sample(self._now(), event.instant_kbps)
        self._refresh_card_graph(path, card, self._now())

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

    def _apply_batch_pause_look(self, paused: bool) -> None:
        """전체 일시중지 버튼의 아이콘과 툴팁을 상태에 맞춤. 배치 시작, 종료, 전환이 같은 도우미를 씀"""
        set_button_icon(self._pause_all_btn, "play" if paused else "pause", pad_right=0)
        self._pause_all_btn.setToolTip(RESUME_ALL_TIP if paused else PAUSE_ALL_TIP)

    def _on_batch_pause_changed(self, paused: bool) -> None:
        """전체 일시중지 상태를 카드와 머리글 버튼에 반영. 전체 일시중지 중에는 개별 버튼을 끔"""
        self._batch_paused = paused
        self._apply_batch_pause_look(paused)
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
        self._apply_batch_pause_look(False)
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
            self._stop_all_requested = True
            self._controller.cancel_all()

    def _request_stop_after(self) -> None:
        """현재 영상 후 중지를 예약하고, 멈춘 영상이 있으면 전체 재개. 머리글 버튼과 장시간 안내 링크가 함께 씀

        - 배치가 중지로 끝나므로 끝난 뒤 머리글을 중지 문구로 바꿈
        """
        self._stopped = True
        self._controller.request_stop_after_current()
        if self._batch_paused or any(card.is_paused for card in self._cards.values()):
            self._controller.resume_all()

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
        run_total, run_done = run_counts(state for path, state, _reason in self._book.items() if path in self._run_paths)
        done_units = run_done + sum(self._fractions.values())
        remaining = (
            "" if self._finished else f"남은 시간 {clock_text(overall_remaining_sec(elapsed, done_units, run_total))}"
        )
        self._remaining.setText(remaining)
        overall = 0 if self._total <= 0 else round(processed / self._total * PROGRESS_STEPS)
        self._overall_bar.setValue(overall)
        self._totals.set_values(self._speed_text(elapsed), saved_summary_text(*saved_totals(self._result_rows.values())))
        for path, card in self._cards.items():
            self._refresh_card_graph(path, card, elapsed)

    def _speed_text(self, elapsed: float) -> str:
        """진행 중 영상의 배속과 위치, 처리한 영상의 길이로 전체 속도 문구를 만듦"""
        processed = [
            self._jobs[path].source_duration_sec
            for path, state, _reason in self._book.items()
            if state in ENCODED_STATES and path in self._run_paths and path in self._jobs
        ]
        running = list(self._cards)
        return batch_speed_text(
            [self._speeds.get(path) for path in running],
            [self._positions.get(path, 0.0) for path in running],
            processed,
            elapsed,
        )

    def _refresh_card_graph(self, path: str, card: JobCard, now_sec: float) -> None:
        job = self._jobs.get(path)
        if job is None:
            return
        max_kbps = None if job.maxrate_kbps is None else float(job.maxrate_kbps)
        card.refresh_graph(now_sec, max_kbps, original_kbps(job.source_size, job.source_duration_sec))

    def _refresh_results(self) -> None:
        roots = {path: job.root_dir for path, job in self._jobs.items()}
        self._results.set_lines(
            result_lines(
                self._book.items(),
                self._result_rows,
                roots,
                PART_LEFT,
                discards=self._discard_rows,
                larger_reason=LARGER_THAN_SOURCE,
            )
        )
        self._view_switch.setTabText(RESULT_TAB_INDEX, RESULT_TAB_TEMPLATE.format(count=self._results.row_count()))

    def _open_source(self, source: str) -> None:
        """더블클릭: 임시 파일이 남은 행은 그 위치를 선택하고, 그 밖은 완료 영상의 결과 파일이나 원본을 연다"""
        if not self._media.reveal_leftover(source):
            self._media.open_file(self._target_of(source))

    def _preview_source(self, source: str) -> None:
        """스페이스바: 임시 파일이 남은 행은 열기와 같은 위치를 보여 주고, 그 밖은 열기와 같은 대상을 미리 본다"""
        if not self._media.reveal_leftover(source):
            self._media.preview_file(self._target_of(source))

    def _target_of(self, source: str) -> str:
        """영상 행이 가리키는 파일. 완료된 항목은 결과 파일이 있으면 결과, 아니면 원본"""
        row = self._result_rows.get(source)
        if self._book.state_of(source) is EncodeState.DONE and row is not None:
            return row.output_path
        return source
