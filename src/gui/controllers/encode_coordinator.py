"""재인코딩 이력 저장소와 실행 컨트롤러를 소유하고, 메뉴의 시작 요청을 확인 창과 배치 시작으로 잇는 모듈

- 생성자는 디스크에 접근하지 않음. 이력 DB 연결은 첫 쓰기 때 열림
- 확인 창은 수락될 때만 배치를 시작하며, 시작 항목 상태는 같은 조건 함수로 다시 확인함
- 실행 중 창 닫기는 확인 창으로 묻고, 종료를 고르면 배치가 끝난 뒤 닫기를 다시 요청함(화면 스레드를 막지 않음)
- 창을 닫을 때 close로 실행 중 배치를 멈추고 이력과 분석 DB 연결을 닫음
"""

import logging
import os
from collections.abc import Callable

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QDialog, QWidget

from src.core.encode.encode_batch_book import EncodeBatchBook, status_label
from src.core.encode.encode_close import CloseAction, CloseChoice, close_action
from src.core.encode.encode_impact import summarize_encode_impact
from src.core.encode.encode_progress_values import ResultRow
from src.core.encode.encode_start_state import StartState, evaluate_start_state
from src.core.encode.encode_state import EncodeState
from src.core.encode.encoder_trial import SHARED_ENCODER_TRIAL_CACHE, check_encoder
from src.core.encode.ffmpeg_capability import FfmpegCapability, probe_ffmpeg_capability
from src.core.encoder_specs import get_encode_codec_family, get_encode_codec_label
from src.core.models import SummaryReport
from src.core.workspace_paths import resolve_source_filepath
from src.gui.controllers.encode_controller import EncodeController
from src.gui.controllers.session_controller import SessionCoordinator
from src.gui.dialogs.encode_confirm_dialogs import ask_close
from src.gui.dialogs.encode_progress_window import BatchInfo, EncodeProgressWindow
from src.gui.dialogs.encode_start_dialog import EncodeStartDialog
from src.gui.menus.menu_callbacks import MenuBarCallbacks
from src.gui.preview.preview_controller import PreviewController
from src.service.analysis_store import AnalysisStore, open_default_store
from src.service.encode.encode_events import EVENT_STATE, EncodeEvent
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_job import EncodeJob, EncodeSettings, job_from_json
from src.service.encode.encode_service import resolve_workers

logger = logging.getLogger(__name__)


class EncodeCoordinator:
    """재인코딩 이력 저장소, 실행 컨트롤러, 시작 확인 흐름의 소유자(메인 창 밖)

    - 설정, 판정 결과, 분석 여부는 호출 시점의 값을 읽음. 판정 결과 함수는 메인 창 연결(attach_menu) 때 받음
    """

    def __init__(
        self,
        parent: QWidget,
        session: SessionCoordinator,
        is_analysis_busy: Callable[[], bool],
        preview: PreviewController,
        on_ready_to_close: Callable[[], object],
    ) -> None:
        self._parent: QWidget = parent
        self._session: SessionCoordinator = session
        self._get_report: Callable[[], SummaryReport | None] | None = None
        self._is_analysis_busy: Callable[[], bool] = is_analysis_busy
        self._preview: PreviewController = preview
        self._on_ready_to_close: Callable[[], object] = on_ready_to_close
        self._close_pending: bool = False
        self._store: AnalysisStore = open_default_store()
        self._history: EncodeHistory = EncodeHistory(self._store.db_path)
        self._controller: EncodeController = EncodeController(self._history, store=self._store, parent=parent)
        self._controller.batch_finished.connect(self._on_batch_finished)
        self._controller.event_received.connect(self._on_event)
        self._controller.pause_changed.connect(self._on_pause_changed)
        self._controller.item_pause_changed.connect(self._on_item_pause_changed)
        self._book: EncodeBatchBook = EncodeBatchBook()
        self._capability_cache: tuple[str, FfmpegCapability] | None = None
        self._progress_window: EncodeProgressWindow | None = None
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
    ) -> None:
        """재인코딩 메뉴 콜백을 연결하고, 판정 결과 조회와 시작 항목 상태가 바뀔 때 부를 함수를 등록"""
        self._get_report = get_report
        callbacks.on_start_encode = self.start_from_menu
        callbacks.on_open_encode_settings = open_settings
        callbacks.get_encode_state = self.start_state
        callbacks.on_open_encode_progress = self.open_progress_window
        callbacks.has_encode_batch = self.has_encode_batch
        self._on_changed = on_changed

    def start_state(self) -> StartState:
        """현재 조건으로 시작 항목 상태를 계산. FFmpeg 확인은 경로별로 한 번만 하고, 인코더는 시험 캐시를 씀"""
        config = self._session.state.to_classifier_config()
        capability = self._capability(config.execution.ffmpeg_path)
        report = self._current_report()
        has_targets = report is not None and any(ev.is_encode_target for ev in report.evaluations)
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
        """재인코딩 시작 항목 동작: 활성일 때만 확인 창을 열고, 수락되면 판정 결과로 배치를 시작"""
        state = self.start_state()
        report = self._current_report()
        if not state.enabled or report is None:
            return
        config = self._session.state.to_classifier_config()
        settings = EncodeSettings.from_config(config)
        videos = [ev.video for ev in report.evaluations if ev.is_encode_target]
        summary = summarize_encode_impact(videos, settings.container_policy, get_encode_codec_family(settings.encoder))
        dialog = EncodeStartDialog(
            summary,
            settings,
            report.evaluations,
            report.root_dir,
            get_encode_codec_label(settings.encoder),
            lambda ev, path: self._preview.preview_for_dialog(ev, path, dialog),
            self._parent,
        )
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        self._preview.release_dialog_owner()
        dialog.deleteLater()
        if accepted:
            self._book.start(
                resolve_source_filepath(report.root_dir, ev.video.filename)
                for ev in report.evaluations
                if ev.is_encode_target
            )
            batch_id = self._controller.start(config, report.evaluations, report.root_dir, report.root_dir)
            logger.info("재인코딩 배치 시작: 대상 %d개", self._book.total())
            if batch_id is not None:
                workers = resolve_workers(settings, os.cpu_count() or 1)
                info = BatchInfo(
                    total=self._book.total(),
                    single_panel=min(workers, self._book.total()) <= 1,
                    encoder_label=get_encode_codec_label(settings.encoder),
                )
                self._ensure_progress_window().start_batch(self._jobs_of(batch_id), info)
                self.open_progress_window()
            self._notify_changed()
            self._notify_book_changed()

    def has_encode_batch(self) -> bool:
        """배치를 한 번이라도 시작했으면 참. 진행 창 메뉴 항목과 하단 요약 칸이 같은 기준을 씀"""
        return self._book.has_batch

    def open_progress_window(self) -> None:
        """재인코딩 진행 창을 보이게 함. 배치가 없으면 빈 상태로 열림"""
        window = self._ensure_progress_window()
        window.show()
        window.raise_()
        window.activateWindow()

    def _ensure_progress_window(self) -> EncodeProgressWindow:
        """진행 창을 한 번만 만들고 재사용. 배치 시작 때 미리 만들어 첫 이벤트를 놓치지 않게 함"""
        if self._progress_window is None:
            self._progress_window = EncodeProgressWindow(self._controller, self._book, self._parent)
            self._progress_window.close_cancel_requested.connect(self._cancel_close)
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

    def _on_item_pause_changed(self, path: str, paused: bool) -> None:
        self._book.set_item_paused(path, paused)
        self._notify_book_changed()

    def close(self) -> None:
        """창 종료 때 실행 중 배치를 멈추고 이력과 분석 DB 연결을 닫음"""
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

    def _on_batch_finished(self, _finished: object) -> None:
        self._book.set_paused(False)
        logger.info("재인코딩 배치 종료: %d/%d개 처리", self._book.processed(), self._book.total())
        self._notify_changed()
        self._notify_book_changed()
        if self._close_pending:
            self._close_pending = False
            if self._progress_window is not None:
                self._progress_window.set_close_pending(False)
            self._on_ready_to_close()

    def _on_event(self, event: object) -> None:
        if isinstance(event, EncodeEvent) and event.kind == EVENT_STATE and event.state is not None:
            self._book.apply(event.source_path, event.state, event.reason)
            reason_part = f", 사유: {event.reason}" if event.reason else ""
            logger.info(
                "재인코딩 상태: %s, %s%s",
                os.path.basename(event.source_path),
                status_label(event.state, self._book.paused),
                reason_part,
            )
            if event.state is EncodeState.DONE:
                self._record_result(event.batch_id, event.source_path)
            self._notify_book_changed()

    def _record_result(self, batch_id: int, source_path: str) -> None:
        """완료 항목의 이력 행을 한 번 읽어 결과 경로와 크기를 진행 창에 넘김"""
        row = self._history.get_item(batch_id, source_path)
        if row is None or row.output_path is None or row.output_size is None:
            return
        job = job_from_json(row.plan_json)
        self._ensure_progress_window().set_result(
            ResultRow(
                source_path=source_path,
                output_path=row.output_path,
                source_bytes=row.source_size,
                output_bytes=row.output_size,
                expected_delta_mb=job.expected_delta_mb,
            )
        )

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


