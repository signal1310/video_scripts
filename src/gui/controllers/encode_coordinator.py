"""재인코딩 이력 저장소와 실행 컨트롤러를 소유하고, 메뉴의 시작 요청을 확인 창과 배치 시작으로 잇는 모듈

- 생성자는 디스크에 접근하지 않음. 이력 DB 연결은 첫 쓰기 때 열림
- 확인 창은 수락될 때만 배치를 시작하며, 시작 항목 상태는 같은 조건 함수로 다시 확인함
- 창을 닫을 때 close로 실행 중 배치를 멈추고 이력과 분석 DB 연결을 닫음
"""

import logging
import os
from collections.abc import Callable

from PyQt6.QtWidgets import QDialog, QWidget

from src.core.encode.encode_batch_book import EncodeBatchBook, status_label
from src.core.encode.encode_impact import summarize_encode_impact
from src.core.encode.encode_start_state import StartState, evaluate_start_state
from src.core.encode.encoder_trial import SHARED_ENCODER_TRIAL_CACHE, check_encoder
from src.core.encode.ffmpeg_capability import FfmpegCapability, probe_ffmpeg_capability
from src.core.encoder_specs import get_encode_codec_family, get_encode_codec_label
from src.core.models import SummaryReport
from src.core.workspace_paths import resolve_source_filepath
from src.gui.controllers.encode_controller import EncodeController
from src.gui.controllers.session_controller import SessionCoordinator
from src.gui.dialogs.encode_start_dialog import EncodeStartDialog
from src.gui.menus.menu_callbacks import MenuBarCallbacks
from src.gui.preview.preview_controller import PreviewController
from src.service.analysis_store import AnalysisStore, open_default_store
from src.service.encode.encode_events import EVENT_STATE, EncodeEvent
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_job import EncodeSettings

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
    ) -> None:
        self._parent: QWidget = parent
        self._session: SessionCoordinator = session
        self._get_report: Callable[[], SummaryReport | None] | None = None
        self._is_analysis_busy: Callable[[], bool] = is_analysis_busy
        self._preview: PreviewController = preview
        self._store: AnalysisStore = open_default_store()
        self._history: EncodeHistory = EncodeHistory(self._store.db_path)
        self._controller: EncodeController = EncodeController(self._history, store=self._store, parent=parent)
        self._controller.batch_finished.connect(self._on_batch_finished)
        self._controller.event_received.connect(self._on_event)
        self._controller.pause_changed.connect(self._on_pause_changed)
        self._book: EncodeBatchBook = EncodeBatchBook()
        self._capability_cache: tuple[str, FfmpegCapability] | None = None
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
            self._controller.start(config, report.evaluations, report.root_dir, report.root_dir)
            logger.info("재인코딩 배치 시작: 대상 %d개", self._book.total())
            self._notify_changed()
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
            self._notify_book_changed()

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


