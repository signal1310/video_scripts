"""화질 분석 실행, 진행 표시, 취소, 표 갱신, 수동 VR 보호와 후보 적용을 연결하는 컨트롤러

- 워커 본문은 `run_frame_analysis_body`로 분리해 QThread 없이 직접 테스트
- 워커는 Qt 객체에 접근하지 않고 시그널로만 화면 스레드에 결과를 넘김
- 수동 VR 보호는 캐시를 지워 다음 분석에서 새 보호 설정으로 다시 재게 함
"""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QWidget

from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.config import ClassifierConfig
from src.core.encode.encode_impact import parse_stream_summary_layout
from src.core.encode.ffmpeg_capability import probe_ffmpeg_capability
from src.core.messages import MSG_WORK_CANCELLED
from src.core.models import SummaryReport, VideoEvaluation
from src.core.workspace_paths import resolve_source_filepath
from src.gui.controllers.manual_target_controller import ManualTargetController
from src.gui.menus.menu_bar import MainMenuController
from src.gui.table.column_history import COLUMN_VISIBILITY_HISTORY_LABEL, ColumnHistoryRecorder
from src.gui.table.frame_analysis_columns import (
    ACTIVE_FRAME_COLUMN,
    DOWNSCALE_CANDIDATE_COLUMN,
)
from src.gui.table.video_table_view import VideoTableView
from src.gui.widgets.footer_bar import FooterBar
from src.service.analysis_store import open_default_store
from src.service.frame_analysis_service import (
    FrameAnalysisRequest,
    FrameAnalysisService,
    FrameAnalysisStore,
)
from src.service.session import ClassifierSession


@dataclass(frozen=True, slots=True)
class FrameAnalysisProgress:
    """화질 분석 진행 한 건의 완료 수, 전체 수, 파일 이름"""

    completed: int
    total: int
    filename: str


@dataclass(frozen=True, slots=True)
class FrameAnalysisFinished:
    """화질 분석 실행 종료: 캐시 결과, 취소 여부, 오류 문구"""

    results: dict[str, FrameAnalysisResult]
    cancelled: bool = False
    error: str = ""


FrameAnalysisEvent = FrameAnalysisProgress | FrameAnalysisFinished
FrameAnalysisSink = Callable[[FrameAnalysisEvent], None]


def run_frame_analysis_body(
    service: FrameAnalysisService,
    requests: Sequence[FrameAnalysisRequest],
    cancel_event: threading.Event,
    sink: FrameAnalysisSink,
) -> None:
    """요청 목록을 분석하고 완료분 캐시를 읽어 종료 이벤트를 보냄

    - 취소는 예외로 올라오므로 삼키고, 이미 저장된 완료분을 캐시에서 다시 읽어 보존
    - 예상하지 못한 오류는 문구를 담아 종료 이벤트로 넘겨 화면이 실행 중 상태에 머물지 않게 함
    """

    def on_progress(completed: int, total: int, filename: str, _result: FrameAnalysisResult) -> None:
        sink(FrameAnalysisProgress(completed, total, filename))

    try:
        service.analyze_batch(
            requests,
            cancel_event=cancel_event,
            on_progress=on_progress,
        )
    except InterruptedError:
        pass
    except Exception as error:
        sink(FrameAnalysisFinished({}, error=str(error)))
        return
    sink(FrameAnalysisFinished(service.load_results(requests), cancelled=cancel_event.is_set()))


class FrameAnalysisWorker(QThread):
    """화질 분석 배치를 실행하는 전용 스레드"""

    progress = pyqtSignal(int, int, str)
    completed = pyqtSignal(object)

    def __init__(
        self,
        service: FrameAnalysisService,
        requests: Sequence[FrameAnalysisRequest],
        cancel_event: threading.Event,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._requests = list(requests)
        self._cancel_event = cancel_event

    def cancel(self) -> None:
        """진행 중인 화질 분석 취소 요청"""
        self._cancel_event.set()

    def run(self) -> None:
        """워커 스레드 본문"""
        run_frame_analysis_body(self._service, self._requests, self._cancel_event, self._emit)

    def _emit(self, event: FrameAnalysisEvent) -> None:
        """워커 스레드에서 진행과 종료를 시그널로 넘김"""
        if isinstance(event, FrameAnalysisProgress):
            self.progress.emit(event.completed, event.total, event.filename)
        else:
            self.completed.emit(event)


def build_requests(
    evaluations: Sequence[VideoEvaluation],
    root_dir: str,
    overrides: frozenset[str],
) -> list[FrameAnalysisRequest]:
    """평가 결과와 원본 파일 정보로 화질 분석 요청 목록 생성, 없는 파일은 건너뜀"""
    requests: list[FrameAnalysisRequest] = []
    for ev in evaluations:
        path = resolve_source_filepath(root_dir, ev.video.filename)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        layout = parse_stream_summary_layout(ev.video.stream_summary)
        requests.append(
            FrameAnalysisRequest(
                filename=ev.video.filename,
                path=path,
                width=ev.video.width,
                height=ev.video.height,
                duration_sec=ev.video.duration,
                filesize_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                has_vr_metadata=layout is not None and layout.is_vr,
                manual_vr=path in overrides,
            )
        )
    return requests

@dataclass(frozen=True)
class FrameAnalysisHost:
    """컨트롤러가 메인 창에서 받는 의존성 묶음"""

    parent: QWidget
    table_view: VideoTableView
    footer: FooterBar
    menu: MainMenuController
    report_provider: Callable[[], SummaryReport | None]
    config_provider: Callable[[], ClassifierConfig]
    is_busy: Callable[[], bool]
    re_evaluate: Callable[[Callable[[], None]], None]
    apply_candidates: Callable[[list[VideoEvaluation]], None]
    show_columns: Callable[[], None]
    notify: Callable[[str], None]


class FrameAnalysisController:
    """화질 분석 실행, 표 갱신, 진행 표시, 수동 VR 보호를 조율하는 컨트롤러"""

    def __init__(self, host: FrameAnalysisHost, store: FrameAnalysisStore) -> None:
        self._host = host
        self._store = store
        self._worker: FrameAnalysisWorker | None = None
        self._requests: list[FrameAnalysisRequest] = []
        self._results: dict[str, FrameAnalysisResult] = {}
        self._shown_roots: set[str] = set()
        host.table_view.selection_changed.connect(lambda _count: self.refresh_actions())
        host.table_view.table_model.modelReset.connect(self.reload_cached)
        self.refresh_actions()

    @property
    def is_running(self) -> bool:
        """화질 분석이 실행 중인지 여부"""
        return self._worker is not None

    def reload_cached(self) -> None:
        """현재 작업 폴더의 캐시 결과와 수동 VR 보호를 표에 다시 반영"""
        report = self._host.report_provider()
        if report is None:
            self._requests = []
            self._results = {}
            self._host.table_view.table_model.set_manual_vr_overrides(())
        else:
            overrides = self._load_overrides(report.evaluations, report.root_dir)
            self._requests = build_requests(report.evaluations, report.root_dir, overrides)
            service = self._load_service()
            self._results = service.load_results(self._requests)
            self._apply_manual_vr(overrides)
        self._apply_results(show_columns=False)
        self.refresh_actions()

    def analyze_selected(self) -> None:
        """선택한 영상의 화질 분석 시작"""
        self._start(self._host.table_view.get_selected_evaluations())

    def analyze_all(self) -> None:
        """현재 작업 폴더의 모든 영상 화질 분석 시작"""
        report = self._host.report_provider()
        self._start(list(report.evaluations) if report is not None else [])

    def refresh_actions(self) -> None:
        """메뉴 항목의 대상, 선택, 실행 상태를 갱신"""
        report = self._host.report_provider()
        self._host.menu.sync_frame_analysis_state(
            has_list=report is not None and bool(report.evaluations),
            has_selection=bool(self._host.table_view.get_selected_evaluations()),
            running=self.is_running,
        )

    def apply_candidates(self, evals: list[VideoEvaluation]) -> None:
        """선택 영상의 화질 분석 축소 후보 적용 요청을 직접 지정 컨트롤러에 넘김"""
        self._host.apply_candidates(evals)

    def toggle_manual_vr(self, evals: list[VideoEvaluation], protected: bool) -> None:
        """선택 영상의 수동 VR 보호를 저장하고 캐시 결과를 지워 다시 분석하게 함"""
        report = self._host.report_provider()
        if report is None or not evals:
            return
        for ev in evals:
            path = resolve_source_filepath(report.root_dir, ev.video.filename)
            self._store.set_vr_override(
                source_path=path,
                filename=ev.video.filename,
                manual_vr=protected,
            )
            self._results.pop(ev.video.filename, None)
        overrides = self._load_overrides(report.evaluations, report.root_dir)
        self._apply_manual_vr(overrides)
        self._apply_results(show_columns=False)
        self._host.re_evaluate(lambda: None)
        self._host.notify(
            "VR 영상으로 보호를 켰습니다. 다음 화질 분석부터 검은 띠 감지와 잘라내기에서 보호합니다."
            if protected
            else "VR 영상으로 보호를 껐습니다. 다음 화질 분석부터 검은 띠를 다시 감지합니다."
        )

    def cancel(self) -> None:
        """진행 중인 화질 분석 취소 요청"""
        if self._worker is not None:
            self._worker.cancel()

    def shutdown(self) -> None:
        """창을 닫을 때 실행 중인 분석을 취소하고 스레드 종료를 기다린 뒤 캐시 연결을 닫음"""
        worker = self._worker
        if worker is not None:
            worker.cancel()
            worker.wait()
        self._store.close()

    def _start(self, evals: Sequence[VideoEvaluation]) -> None:
        if self.is_running or self._host.is_busy() or not evals:
            return
        report = self._host.report_provider()
        if report is None:
            return
        overrides = self._load_overrides(evals, report.root_dir)
        requests = build_requests(evals, report.root_dir, overrides)
        if not requests:
            self._host.notify("화질 분석할 영상 파일을 찾지 못했습니다.")
            return
        config = self._host.config_provider()
        capability = probe_ffmpeg_capability(config.execution.ffmpeg_path)
        if not capability.usable:
            self._host.notify(f"FFmpeg를 쓸 수 없어 화질 분석을 시작할 수 없습니다: {capability.reason}")
            return
        self._requests = requests
        cancel_event = threading.Event()
        service = FrameAnalysisService(
            self._store,
            work_dir=os.path.join(tempfile.gettempdir(), "video_scripts_frame_analysis"),
            ffmpeg_executable=capability.executable,
            has_vmaf=capability.has_vmaf,
        )
        worker = FrameAnalysisWorker(service, requests, cancel_event, self._host.parent)
        worker.progress.connect(self._on_progress)
        worker.completed.connect(self._on_completed)
        self._worker = worker
        self._host.footer.set_frame_analysis_progress(0, len(requests), requests[0].filename)
        self.refresh_actions()
        worker.start()

    def _on_progress(self, completed: int, total: int, filename: str) -> None:
        self._host.footer.set_frame_analysis_progress(completed, total, filename)

    def _on_completed(self, event: object) -> None:
        worker = self._worker
        if worker is not None:
            worker.wait()
            self._worker = None
        finished = event if isinstance(event, FrameAnalysisFinished) else FrameAnalysisFinished({})
        self._host.footer.clear_frame_analysis_progress()
        if finished.error:
            self._host.notify(f"화질 분석 중 오류가 발생했습니다: {finished.error}")
        else:
            self._results = dict(finished.results)
            self._apply_results()
            if finished.cancelled:
                self._host.notify(MSG_WORK_CANCELLED)
            else:
                self._host.re_evaluate(lambda: None)
        self.refresh_actions()

    def _load_service(self) -> FrameAnalysisService:
        return FrameAnalysisService(
            self._store,
            work_dir=os.path.join(tempfile.gettempdir(), "video_scripts_frame_analysis"),
            has_vmaf=True,
        )

    def _load_overrides(
        self, evals: Sequence[VideoEvaluation], root_dir: str
    ) -> frozenset[str]:
        paths = [resolve_source_filepath(root_dir, ev.video.filename) for ev in evals]
        return self._store.load_vr_overrides(paths)

    def _apply_manual_vr(self, overrides: frozenset[str]) -> None:
        names = frozenset(request.filename for request in self._requests if request.path in overrides)
        self._host.table_view.table_model.set_manual_vr_overrides(names)

    def _apply_results(self, *, show_columns: bool = True) -> None:
        self._host.table_view.table_model.set_frame_analysis_results(self._results)
        if show_columns:
            self._maybe_show_columns()

    def _maybe_show_columns(self) -> None:
        report = self._host.report_provider()
        if not self._results or report is None or report.root_dir in self._shown_roots:
            return
        self._shown_roots.add(report.root_dir)
        self._host.show_columns()



class FrameAnalysisWindow(Protocol):
    """화질 분석 컨트롤러를 붙이는 메인 창의 공개 인터페이스"""

    table_view: VideoTableView
    footer_bar: FooterBar
    menu: MainMenuController
    manual_targets: ManualTargetController
    column_history: ColumnHistoryRecorder

    @property
    def current_report(self) -> SummaryReport | None:
        """가장 최근에 적용한 요약 리포트"""
        ...

    @property
    def classifier(self) -> ClassifierSession | None:
        """현재 분류 세션"""
        ...

    def set_status_text(self, text: str, tooltip: str = "") -> None:
        """하단 상태 줄 문구 설정"""
        ...


def attach_frame_analysis(
    window: FrameAnalysisWindow,
    re_evaluate: Callable[[Callable[[], None]], None],
    is_busy: Callable[[], bool],
) -> FrameAnalysisController:
    """메인 창에 화질 분석 컨트롤러를 만들어 메뉴, 하단 취소, 우클릭 요청을 연결"""
    default_store = open_default_store()
    try:
        store = FrameAnalysisStore(default_store.db_path)
    finally:
        default_store.close()

    def show_columns() -> None:
        for col in (ACTIVE_FRAME_COLUMN, DOWNSCALE_CANDIDATE_COLUMN):
            if window.table_view.isColumnHidden(col):
                window.column_history.run_command(
                    COLUMN_VISIBILITY_HISTORY_LABEL,
                    lambda c=col: window.table_view.setColumnHidden(c, False),
                )

    def config_provider() -> ClassifierConfig:
        classifier = window.classifier
        return classifier.config if classifier is not None else ClassifierConfig()

    host = FrameAnalysisHost(
        parent=window.table_view,
        table_view=window.table_view,
        footer=window.footer_bar,
        menu=window.menu,
        report_provider=lambda: window.current_report,
        config_provider=config_provider,
        is_busy=is_busy,
        re_evaluate=re_evaluate,
        apply_candidates=window.manual_targets.apply_candidates,
        show_columns=show_columns,
        notify=window.set_status_text,
    )
    controller = FrameAnalysisController(host, store)
    host.table_view.table_model.set_frame_action_handlers(
        apply_candidates=controller.apply_candidates,
        toggle_vr=controller.toggle_manual_vr,
    )
    window.menu.act_frame_selected.triggered.connect(controller.analyze_selected)
    window.menu.act_frame_all.triggered.connect(controller.analyze_all)
    window.footer_bar.frame_analysis_cancel_requested.connect(controller.cancel)
    window.footer_bar.destroyed.connect(controller.shutdown)
    return controller

