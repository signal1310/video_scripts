"""재인코딩 이력 저장소와 실행 컨트롤러를 소유하고, 메뉴의 시작 요청과 이어서 재인코딩, 완료 후 동작, 진행 창의 조작을 잇는 모듈

- 생성자는 디스크에 접근하지 않음. 이력 DB 연결은 첫 쓰기 때 열림
- 확인 창은 수락될 때만 배치를 시작하며, 시작 항목 상태는 같은 조건 함수로 다시 확인함
- 실행 중 창 닫기는 확인 창으로 묻고, 종료를 고르면 배치가 끝난 뒤 닫기를 다시 요청함(화면 스레드를 막지 않음)
- 창을 닫을 때 close로 실행 중 배치를 멈추고 이력과 분석 DB 연결을 닫음
- 완료 후 동작은 정상 종료한 배치에만 카운트다운 뒤 실행함(즉시 중지, 현재 영상 후 중지, 실행 오류 제외)
"""

import logging
import os
from collections.abc import Callable, Sequence

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QMessageBox, QWidget

from src.core.config import RESULT_POLICY_REPLACE
from src.core.encode.encode_batch_book import EncodeBatchBook, status_label
from src.core.encode.encode_close import CloseAction, CloseChoice, close_action
from src.core.encode.encode_impact import parse_stream_summary_layout, summarize_encode_impact
from src.core.encode.encode_progress import LONG_RUN_NOTICE_SEC
from src.core.encode.encode_start_state import StartState, evaluate_start_state
from src.core.encode.encoder_trial import SHARED_ENCODER_TRIAL_CACHE, check_encoder
from src.core.encode.ffmpeg_capability import FfmpegCapability, probe_ffmpeg_capability
from src.core.encoder_specs import get_encode_codec_family, get_encode_codec_label
from src.core.models import SummaryReport, VideoEvaluation
from src.core.workspace_paths import parse_root_dirs, resolve_source_filepath
from src.gui.constants import call_later
from src.gui.controllers.encode_cleanup import LeftoverCleanup
from src.gui.controllers.encode_completion import CompletionFlow
from src.gui.controllers.encode_controller import EncodeController
from src.gui.controllers.encode_item_actions import EncodeItemActions, notify_text
from src.gui.controllers.encode_result_feed import SHOWN_STATES, ResultFeed
from src.gui.controllers.encode_worker import BatchFinished
from src.gui.controllers.session_controller import SessionCoordinator
from src.gui.dialogs.encode_confirm_dialogs import ask_close
from src.gui.dialogs.encode_progress_card import LONG_PAUSE_CAUSE
from src.gui.dialogs.encode_progress_window import BatchInfo, EncodeProgressWindow
from src.gui.dialogs.encode_resume_dialogs import ResumeChoice, ask_resume
from src.gui.dialogs.encode_summary_view import EncodeStartPlan
from src.gui.menus.menu_callbacks import MenuBarCallbacks
from src.gui.preview.preview_controller import PreviewController
from src.gui.widgets.header_bar import HeaderBar
from src.service.analysis_store import AnalysisStore, open_default_store
from src.service.encode.encode_events import EVENT_LONG_PAUSE, EVENT_STATE, EncodeEvent
from src.service.encode.encode_history import BatchRow, EncodeHistory
from src.service.encode.encode_job import EncodeJob, EncodeSettings, job_from_json, resolve_workers
from src.service.encode.encode_previous_discard import find_previous_discards
from src.service.encode.encode_resume import (
    count_resume,
    find_unrecorded_parts,
    prepare_resume,
)
from src.service.frame_analysis_service import FrameAnalysisStore

logger = logging.getLogger(__name__)

RESUME_RETRY_MS: int = 1000
LONG_PAUSE_MINUTES: int = int(LONG_RUN_NOTICE_SEC // 60)
LONG_PAUSE_LOG_TEMPLATE: str = (
    "재인코딩 장시간 일시중지: {name}, {minutes}분을 넘게 멈춰 있습니다. " + LONG_PAUSE_CAUSE + " 오래 멈출 때는 현재 영상 후 중지를 권장합니다."
)
CLEANUP_BUSY_TEXT: str = "재인코딩이 끝난 뒤 정리할 수 있습니다."
CLEAR_DISCARDS_BUSY_TEXT: str = "재인코딩이 끝난 뒤 비울 수 있습니다."
CLEAR_DISCARDS_TITLE: str = "이전 폐기 기록 비우기"
CLEAR_DISCARDS_EMPTY_TEXT: str = "비울 이전 폐기 기록이 없습니다."
CLEAR_DISCARDS_CONFIRM_TEXT: str = (
    "이전 폐기 기록 {count}건을 비울까요?\n비우면 이전에 폐기된 영상도 다음 재인코딩 대상에 다시 포함됩니다."
)
CLEAR_DISCARDS_DONE_TEXT: str = "이전 폐기 기록 {count}건을 비웠습니다."
RETRY_ERROR_TEXT: str = "교체 대기를 다시 시도하는 중 오류가 나 중단했습니다. 결과 목록에서 상태를 확인해 주세요. 오류: {error}"


def _is_within(path: str, root: str) -> bool:
    """경로가 작업 폴더와 같거나 그 아래인지. 구분자와 대소문자 차이는 무시하고, 이름만 같은 접두 폴더는 안이 아님"""
    child = os.path.normcase(os.path.normpath(path))
    base = os.path.normcase(os.path.normpath(root))
    return child == base or child.startswith(base.rstrip(os.sep) + os.sep)


def batch_in_roots(batch_spec: str, root_spec: str) -> bool:
    """배치의 작업 폴더가 모두 현재 작업 폴더 안에 있는지. 여러 폴더는 세미콜론으로 구분된 문자열"""
    current = parse_root_dirs(root_spec)
    return all(any(_is_within(batch_root, root) for root in current) for batch_root in parse_root_dirs(batch_spec))


class EncodeCoordinator:
    """재인코딩 이력 저장소, 실행 컨트롤러, 시작과 이어서 재인코딩 흐름, 완료 후 동작의 소유자(메인 창 밖)

    - 설정, 판정 결과, 분석 여부는 호출 시점의 값을 읽음. 판정 결과 함수는 메인 창 연결(attach_menu) 때 받음
    - 진행 창의 영상 열기, 미리 보기, 교체 대기 조작을 맡음(ProgressMedia 인터페이스)
    """

    def __init__(
        self,
        parent: QWidget,
        session: SessionCoordinator,
        is_analysis_busy: Callable[[], bool],
        preview: PreviewController,
        on_ready_to_close: Callable[[], object],
        completion: CompletionFlow | None = None,
        compare_files: Callable[[str], None] | None = None,
    ) -> None:
        self._parent: QWidget = parent
        self._session: SessionCoordinator = session
        self._get_report: Callable[[], SummaryReport | None] | None = None
        self._is_analysis_busy: Callable[[], bool] = is_analysis_busy
        self._preview: PreviewController = preview
        self._on_ready_to_close: Callable[[], object] = on_ready_to_close
        self._completion: CompletionFlow = (
            completion if completion is not None else CompletionFlow(parent, exit_app=on_ready_to_close)
        )
        self._close_pending: bool = False
        self._store: AnalysisStore = open_default_store()
        self._history: EncodeHistory = EncodeHistory(self._store.db_path)
        self._controller: EncodeController = EncodeController(self._history, store=self._store, parent=parent)
        self._controller.batch_finished.connect(self._on_batch_finished)
        self._controller.event_received.connect(self._on_event)
        self._controller.pause_changed.connect(self._on_pause_changed)
        self._controller.item_pause_changed.connect(self._on_item_pause_changed)
        self._controller.replace_retry_finished.connect(self._on_replace_retry_finished)
        self._cleanup: LeftoverCleanup = LeftoverCleanup(parent, self._history)
        self._get_root: Callable[[], str] = lambda: ""
        self._book: EncodeBatchBook = EncodeBatchBook()
        self._capability_cache: tuple[str, FfmpegCapability] | None = None
        self._progress_window: EncodeProgressWindow | None = None
        self._results: ResultFeed = ResultFeed(self._history, self._ensure_progress_window)
        self._items: EncodeItemActions = EncodeItemActions(
            self._controller,
            self._history,
            self._cleanup,
            preview,
            self._ensure_progress_window,
            self._active_batch,
            self._refresh_batch_rows,
            compare_files,
        )
        self._batch_id: int | None = None
        self._batch_settings: EncodeSettings | None = None
        self._open_settings: Callable[[], None] = lambda: None
        self._asked_roots: set[str] = set()
        self._on_changed: Callable[[], None] | None = None
        self._on_book_changed: Callable[[EncodeBatchBook], None] | None = None
        self._on_summary_changed: Callable[[str, str], None] | None = None

    @property
    def is_running(self) -> bool:
        """재인코딩 배치가 실행 중이면 참"""
        return self._controller.is_running

    @property
    def book(self) -> EncodeBatchBook:
        """현재 배치의 항목 상태 저장소"""
        return self._book

    def attach_view(
        self,
        on_book_changed: Callable[[EncodeBatchBook], None],
        on_summary_changed: Callable[[str, str], None],
    ) -> None:
        """항목 상태나 일시중지가 바뀔 때 표에는 상태 저장소를, 하단 요약에는 문구와 툴팁을 넘길 함수를 등록"""
        self._on_book_changed = on_book_changed
        self._on_summary_changed = on_summary_changed

    def attach_menu(
        self,
        callbacks: MenuBarCallbacks,
        *,
        get_report: Callable[[], SummaryReport | None],
        open_settings: Callable[[], None],
        on_changed: Callable[[], None],
        get_root: Callable[[], str],
        header_bar: HeaderBar | None = None,
    ) -> None:
        """재인코딩 메뉴 콜백을 연결하고, 판정 결과 조회와 시작 항목 상태가 바뀔 때 부를 함수를 등록"""
        self._get_report = get_report
        self._get_root = get_root
        self._open_settings = open_settings
        callbacks.on_start_encode = self.start_from_menu
        callbacks.on_open_encode_settings = open_settings
        callbacks.get_encode_state = self.start_state
        callbacks.on_open_encode_progress = self.open_progress_window
        callbacks.has_encode_batch = self.has_encode_batch
        callbacks.on_open_encode_cleanup = self.open_cleanup
        callbacks.get_encode_cleanup_block = self.cleanup_block_reason
        callbacks.on_clear_encode_discards = self.clear_discard_history
        callbacks.get_encode_clear_discards_block = self.clear_discard_block_reason
        self._on_changed = on_changed
        if header_bar is not None:
            header_bar.encode_start_clicked.connect(self.start_from_menu)
            header_bar.set_encode_start_state(self.start_state())

    def open_cleanup(self) -> None:
        """재인코딩 메뉴의 임시 파일 정리: 재인코딩 중이 아니면 작업 폴더 전체를 검사해 정리 창을 연다"""
        if not self.cleanup_block_reason():
            self._cleanup.open(self._get_root(), self._output_dirname())

    def cleanup_block_reason(self) -> str:
        """임시 파일 정리를 쓸 수 없는 사유. 재인코딩 중이면 사유를 돌려주고, 쓸 수 있으면 빈 문자열"""
        return CLEANUP_BUSY_TEXT if self.is_running else ""

    def clear_discard_block_reason(self) -> str:
        """이전 폐기 기록 비우기를 쓸 수 없는 사유. 재인코딩 중이면 사유를 돌려주고, 쓸 수 있으면 빈 문자열"""
        return CLEAR_DISCARDS_BUSY_TEXT if self.is_running else ""

    def clear_discard_history(self) -> None:
        """이전 결과 폐기 기록만 삭제하고 메인 표와 메뉴 상태를 갱신"""
        if self.is_running:
            return
        count = self._history.count_discarded() if os.path.exists(self._store.db_path) else 0
        if count <= 0:
            QMessageBox.information(self._parent, CLEAR_DISCARDS_TITLE, CLEAR_DISCARDS_EMPTY_TEXT)
            return
        reply = QMessageBox.question(
            self._parent,
            CLEAR_DISCARDS_TITLE,
            CLEAR_DISCARDS_CONFIRM_TEXT.format(count=count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        deleted = self._history.clear_discarded()
        self._book.clear_discarded()
        self._notify_book_changed()
        self._notify_changed()
        QMessageBox.information(self._parent, CLEAR_DISCARDS_TITLE, CLEAR_DISCARDS_DONE_TEXT.format(count=deleted))

    def previous_discard_paths(self, evaluations: Sequence[VideoEvaluation], root_dir: str) -> frozenset[str]:
        """이전 결과 폐기 기록과 원본이 같아 재인코딩 대상에서 뺄 경로. 기록 DB 파일이 없으면 기록이 없는 것으로 봄"""
        if not os.path.exists(self._store.db_path):
            return frozenset()
        encoder = EncodeSettings.from_config(self._session.state.to_classifier_config()).encoder
        return find_previous_discards(evaluations, root_dir, self._history.discarded_facts(), encoder)

    def _targets(self, report: SummaryReport) -> list[VideoEvaluation]:
        """재인코딩 대상 판정. 이전 결과 폐기 기록과 원본이 같은 영상은 뺌"""
        candidates = [ev for ev in report.evaluations if ev.is_encode_target]
        excluded = self.previous_discard_paths(candidates, report.root_dir)
        return [ev for ev in candidates if resolve_source_filepath(report.root_dir, ev.video.filename) not in excluded]

    def _output_dirname(self) -> str:
        return EncodeSettings.from_config(self._session.state.to_classifier_config()).output_dirname

    def start_state(self) -> StartState:
        """현재 조건으로 시작 항목 상태를 계산. FFmpeg 확인은 경로별로 한 번만 하고, 인코더는 시험 캐시를 씀"""
        config = self._session.state.to_classifier_config()
        capability = self._capability(config.execution.ffmpeg_path)
        report = self._current_report()
        has_targets = report is not None and bool(self._targets(report))
        encoder = (
            check_encoder(capability, config.encode.encode_codec, SHARED_ENCODER_TRIAL_CACHE)
            if capability.usable
            else None
        )
        return evaluate_start_state(
            analysis_busy=self._is_analysis_busy(),
            encode_running=self.is_running,
            has_targets=has_targets,
            capability=capability,
            encoder=encoder,
        )

    def start_from_menu(self) -> None:
        """재인코딩 시작 항목 동작: 재인코딩 창을 열고, 시작 전이면 요약 탭 브리핑을 채움

        - 실행 중이면 기존 창을 그대로 전면에 보여 줌
        - 완료된 이전 배치가 있어도 새 대상이 있으면 새 브리핑으로 바꿔 요약 탭을 엶
        """
        if self.is_running:
            self.open_progress_window()
            return
        report = self._current_report()
        plan = self._build_plan(report) if self.start_state().enabled and report is not None else None
        if plan is not None:
            window = self._ensure_progress_window()
            window.set_plan(plan)
            self.open_progress_window()
            self._notify_changed()
            return
        if self._book.has_batch:
            window = self._ensure_progress_window()
            window.set_start_available(self._has_new_targets())
            self.open_progress_window()

    def _build_plan(
        self, report: SummaryReport, targets: Sequence[VideoEvaluation] | None = None
    ) -> EncodeStartPlan | None:
        """현재 판정 결과와 설정으로 시작 전 브리핑 값을 만듦. 대상이 없으면 None

        - targets를 주면 그 목록만 브리핑에 넣음(창에서 제외한 영상을 뺀 목록)
        """
        selected = list(self._targets(report)) if targets is None else list(targets)
        if not selected:
            return None
        config = self._session.state.to_classifier_config()
        settings = EncodeSettings.from_config(config)
        vr_filenames = self._vr_filenames(selected, report.root_dir)
        summary = summarize_encode_impact(
            selected,
            settings.container_policy,
            get_encode_codec_family(settings.encoder),
            vr_filenames,
        )
        return EncodeStartPlan(
            evaluations=tuple(selected),
            paths=tuple(resolve_source_filepath(report.root_dir, ev.video.filename) for ev in selected),
            summary=summary,
            settings=settings,
            root_dir=report.root_dir,
            target_codec_label=get_encode_codec_label(settings.encoder),
            baseline_quality=config.encode.baseline_cq,
        )

    def _start_from_window(self, excluded: frozenset[str]) -> None:
        """창의 시작 버튼에서 배치를 시작. 시작 시점의 설정과 포함 대상으로 원본 교체를 다시 확인"""
        if self.is_running:
            return
        state = self.start_state()
        report = self._current_report()
        if not state.enabled or report is None:
            return
        config = self._session.state.to_classifier_config()
        settings = EncodeSettings.from_config(config)
        targets = [
            ev
            for ev in self._targets(report)
            if resolve_source_filepath(report.root_dir, ev.video.filename) not in excluded
        ]
        window = self._ensure_progress_window()
        if not targets:
            window.set_start_available(False)
            return
        if settings.result_policy == RESULT_POLICY_REPLACE and not window.confirm_replace(len(targets)):
            return
        plan = self._build_plan(report, targets=targets)
        if plan is not None:
            window.set_plan(plan)
        self._book.start(resolve_source_filepath(report.root_dir, ev.video.filename) for ev in targets)
        batch_id = self._controller.start(config, targets, report.root_dir, report.root_dir)
        logger.info("재인코딩 배치 시작: 대상 %d개", self._book.total())
        if batch_id is not None:
            self._batch_id = batch_id
            self._batch_settings = settings
            workers = resolve_workers(settings, os.cpu_count() or 1)
            info = BatchInfo(
                total=self._book.total(),
                single_panel=min(workers, self._book.total()) <= 1,
                encoder_label=get_encode_codec_label(settings.encoder),
            )
            window.start_batch(self._jobs_of(batch_id), info)
            self.open_progress_window()
        self._notify_changed()
        self._notify_book_changed()

    def _vr_filenames(self, targets: Sequence[VideoEvaluation], root_dir: str) -> frozenset[str]:
        """VR 부가 정보가 있거나 수동 보호한 영상 파일명 집합(MP4 출력 시 입체 정보 소실 안내용)"""
        store = FrameAnalysisStore(self._store.db_path)
        try:
            paths = [resolve_source_filepath(root_dir, ev.video.filename) for ev in targets]
            overrides = store.load_vr_overrides(paths)
        finally:
            store.close()
        names: set[str] = set()
        for ev, path in zip(targets, paths, strict=True):
            layout = parse_stream_summary_layout(ev.video.stream_summary)
            if (layout is not None and layout.is_vr) or path in overrides:
                names.add(ev.video.filename)
        return frozenset(names)

    def check_resume(self, root_dir: str) -> None:
        """앱 시작과 작업 폴더 변경 때 같은 폴더의 미완료 배치 중 가장 최근 것을 찾아 이어서 재인코딩을 묻는다

        - 작업 폴더마다 실행 중 한 번만 묻고, 어떤 선택을 해도 같은 폴더의 다음 배치는 같은 실행에서 묻지 않음
        - 분석이 도는 중이면 끝날 때까지 기다렸다가 다시 확인
        """
        if self.is_running or root_dir in self._asked_roots:
            return
        if self._is_analysis_busy():
            call_later(self._parent, RESUME_RETRY_MS, lambda: self.check_resume(root_dir))
            return
        candidates = [b for b in self._history.list_open_batches() if batch_in_roots(b.root_dir, root_dir)]
        batch = candidates[-1] if candidates else None
        if batch is None:
            return
        self._asked_roots.add(root_dir)
        choice = self._ask_batch(batch, root_dir)
        if choice is ResumeChoice.RESUME:
            self._resume_batch(batch)
        elif choice is ResumeChoice.DISCARD:
            self._cleanup.discard_batch(batch.batch_id)

    def _ask_batch(self, batch: BatchRow, root_dir: str) -> ResumeChoice:
        """이어서 재인코딩 확인. 정리를 고르면 정리 창을 연 뒤 기록 밖 목록을 새로 읽어 다시 묻는다"""
        while True:
            unrecorded = find_unrecorded_parts(parse_root_dirs(batch.root_dir), self._history.recorded_part_paths())
            choice = ask_resume(self._parent, count_resume(self._history, batch.batch_id), unrecorded)
            if choice is not ResumeChoice.CLEANUP:
                return choice
            self._cleanup.open(root_dir, self._output_dirname())

    def has_encode_batch(self) -> bool:
        """배치를 한 번이라도 시작했으면 참. 진행 창 메뉴 항목과 하단 요약 칸이 같은 기준을 씀"""
        return self._book.has_batch

    def open_progress_window(self) -> None:
        """재인코딩 진행 창을 보이게 함. 배치가 없으면 빈 상태로 열림"""
        window = self._ensure_progress_window()
        window.show()
        window.raise_()
        window.activateWindow()

    def _active_batch(self) -> tuple[int, EncodeSettings] | None:
        """현재 배치의 번호와 설정. 배치를 시작하지 않았으면 없음"""
        if self._batch_id is None or self._batch_settings is None:
            return None
        return self._batch_id, self._batch_settings

    def _ensure_progress_window(self) -> EncodeProgressWindow:
        """진행 창을 한 번만 만들고 재사용. 배치 시작 때 미리 만들어 첫 이벤트를 놓치지 않게 함

        - 메인 창의 자식이 아닌 독립 창(설정 창과 같은 방식). 처음 만들 때만 메인 창 가운데에 둠
        """
        if self._progress_window is None:
            window = EncodeProgressWindow(
                self._controller,
                self._book,
                self._items,
                None,
                start_requested=self._start_from_window,
                open_settings=self._open_settings,
            )
            window.close_cancel_requested.connect(self._cancel_close)
            if self._parent.isVisible():
                center = self._parent.frameGeometry().center()
                window.move(center.x() - window.width() // 2, center.y() - window.height() // 2)
            self._progress_window = window
        return self._progress_window

    def can_close(self, event: QCloseEvent | None) -> bool:
        """메인 창 닫기 요청을 처리. 바로 닫아도 되면 참, 아니면 이벤트를 무시하고 거짓

        - 실행 중이 아니면 바로 닫음. 실행 중이면 확인 창을 열고, 종료를 예약했으면 배치가 끝날 때까지 기다림
        """
        action = close_action(self.is_running, self._close_pending)
        if action is CloseAction.CLOSE_NOW:
            return True
        if event is not None:
            event.ignore()
        if action is CloseAction.ASK:
            self._apply_close_choice(ask_close(self._parent, self._book.any_paused()))
        return False

    def _apply_close_choice(self, choice: CloseChoice) -> None:
        if choice is CloseChoice.KEEP:
            return
        self._close_pending = True
        if choice is CloseChoice.IMMEDIATE:
            self._controller.cancel_all()
            return
        self._controller.request_stop_after_current()
        if self._book.any_paused():
            self._controller.resume_all()
        self._ensure_progress_window().set_close_pending(True)
        self.open_progress_window()

    def _cancel_close(self) -> None:
        """종료 예약 취소: 현재 영상 후 중지를 풀고 배치는 계속함"""
        if not self._close_pending:
            return
        self._close_pending = False
        self._controller.cancel_stop_after_current()
        window = self._ensure_progress_window()
        window.set_close_pending(False)
        window.clear_stop_after_request()

    def _jobs_of(self, batch_id: int) -> dict[str, EncodeJob]:
        """배치 행의 계획 입력을 경로별로 읽음(원본 길이와 최대 비트레이트는 진행 창이 씀)"""
        return {row.source_path: job_from_json(row.plan_json) for row in self._history.list_items(batch_id)}

    def _resume_batch(self, batch: BatchRow) -> None:
        """기록된 배치를 준비하고 이어서 실행. 진행 창에는 기록된 상태와 결과를 그대로 보여 줌"""
        settings = EncodeSettings.from_json(batch.settings_json)
        prepare_resume(self._history, batch.batch_id)
        rows = self._history.list_items(batch.batch_id)
        self._batch_id = batch.batch_id
        self._batch_settings = settings
        self._book.start(row.source_path for row in rows)
        for row in rows:
            self._book.apply(row.source_path, row.state, row.reason)
        self._controller.resume(batch.batch_id, settings)
        jobs = self._jobs_of(batch.batch_id)
        workers = resolve_workers(settings, os.cpu_count() or 1)
        info = BatchInfo(
            total=self._book.total(),
            single_panel=min(workers, self._book.total()) <= 1,
            encoder_label=get_encode_codec_label(settings.encoder),
        )
        window = self._ensure_progress_window()
        window.set_resumed()
        window.start_batch(jobs, info)
        for row in rows:
            if row.state in SHOWN_STATES:
                self._results.record(batch.batch_id, row.source_path)
        self.open_progress_window()
        self._notify_changed()
        self._notify_book_changed()

    def _on_item_pause_changed(self, path: str, paused: bool) -> None:
        self._book.set_item_paused(path, paused)
        self._notify_book_changed()

    def close(self) -> None:
        """창 종료 때 실행 중 배치를 멈추고 이력과 분석 DB 연결을 닫음. 독립 창인 진행 창도 함께 숨김"""
        if self._progress_window is not None:
            self._progress_window.hide()
        self._controller.shutdown()
        self._history.close()
        self._store.close()

    def _capability(self, path: str) -> FfmpegCapability:
        """FFmpeg 능력은 실행 파일 경로별로 한 번만 확인하고 같은 경로면 다시 실행하지 않음"""
        cached = self._capability_cache
        if cached is not None and cached[0] == path:
            return cached[1]
        capability = probe_ffmpeg_capability(path)
        self._capability_cache = (path, capability)
        return capability

    def _current_report(self) -> SummaryReport | None:
        """메뉴 연결 전에는 판정 결과가 없는 것으로 봄"""
        return self._get_report() if self._get_report is not None else None

    def _has_new_targets(self) -> bool:
        """지금 판정 결과에 새 재인코딩 대상이 있는지"""
        report = self._current_report()
        return report is not None and bool(self._targets(report))

    def _on_batch_finished(self, finished: object) -> None:
        self._book.set_paused(False)
        logger.info("재인코딩 배치 종료: %d/%d개 처리", self._book.processed(), self._book.total())
        self._notify_changed()
        self._notify_book_changed()
        if self._progress_window is not None:
            self._progress_window.set_start_available(self._has_new_targets())
        if self._close_pending:
            self._close_pending = False
            if self._progress_window is not None:
                self._progress_window.set_close_pending(False)
            self._on_ready_to_close()
            return
        if (
            isinstance(finished, BatchFinished)
            and not finished.error
            and not self._controller.interrupted
            and self._batch_settings is not None
        ):
            self._completion.start(self._batch_settings.after_complete)

    def _on_event(self, event: object) -> None:
        if not isinstance(event, EncodeEvent):
            return
        if event.kind == EVENT_STATE and event.state is not None:
            self._book.apply(event.source_path, event.state, event.reason)
            reason_part = f", 사유: {event.reason}" if event.reason else ""
            logger.info(
                "재인코딩 상태: %s, %s%s",
                os.path.basename(event.source_path),
                status_label(event.state, self._book.paused),
                reason_part,
            )
            if event.state in SHOWN_STATES:
                self._results.record(event.batch_id, event.source_path)
            self._notify_book_changed()
        elif event.kind == EVENT_LONG_PAUSE:
            logger.info(
                LONG_PAUSE_LOG_TEMPLATE.format(name=os.path.basename(event.source_path), minutes=LONG_PAUSE_MINUTES)
            )

    def _refresh_batch_rows(self, batch_id: int) -> None:
        """기록 DB를 다시 읽어 상태 저장소, 결과 목록, 요약을 맞춤. 이벤트 없이 바뀐 교체 대기 결과에 씀"""
        for row in self._history.list_items(batch_id):
            self._book.apply(row.source_path, row.state, row.reason)
            if row.state in SHOWN_STATES:
                self._results.record(batch_id, row.source_path)
        self._ensure_progress_window().refresh_rows()
        self._notify_book_changed()

    def _on_replace_retry_finished(self, error: str) -> None:
        if self._batch_id is not None:
            self._refresh_batch_rows(self._batch_id)
        if error:
            notify_text(self._ensure_progress_window(), RETRY_ERROR_TEXT.format(error=error))

    def _on_pause_changed(self, paused: bool) -> None:
        self._book.set_paused(paused)
        self._notify_book_changed()

    def _notify_changed(self) -> None:
        if self._on_changed is not None:
            self._on_changed()

    def _notify_book_changed(self) -> None:
        if self._on_book_changed is not None:
            self._on_book_changed(self._book)
        if self._on_summary_changed is not None:
            text = self._book.summary_text() if self._book.has_batch else ""
            self._on_summary_changed(text, self._book.summary_tooltip())
