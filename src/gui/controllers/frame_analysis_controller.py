"""화질 분석 실행, 진행 표시, 취소, 표 갱신, 수동 VR 보호와 후보 적용을 연결하는 컨트롤러

- 워커 본문은 `run_frame_analysis_body`로 분리해 QThread 없이 직접 테스트
- 워커는 Qt 객체에 접근하지 않고 시그널로만 화면 스레드에 결과를 넘김
- 수동 VR 보호는 캐시를 지워 다음 분석에서 새 보호 설정으로 다시 재게 함
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QWidget

from src.core.analysis.frame_analysis import frame_thresholds_from_config
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.analysis.reanalysis_notice import count_keyframe_missing
from src.core.config import ClassifierConfig
from src.core.encode.ffmpeg_capability import probe_ffmpeg_capability
from src.core.eta import ProgressEstimator, format_estimate_label, format_remaining
from src.core.messages import MSG_WORK_CANCELLED
from src.core.models import ActiveFrame, SummaryReport, VideoEvaluation
from src.core.workspace_paths import resolve_source_filepath
from src.gui.controllers.frame_analysis_events import (
    FRAME_ANALYSIS_LABEL,
    FrameAnalysisEvent,
    FrameAnalysisFinished,
    FrameAnalysisProgress,
    FrameAnalysisSink,
    FrameAnalysisStageProgress,
    completion_status_text,
)
from src.gui.controllers.frame_analysis_targets import (
    NO_CROP_TARGETS_TEXT,
    NO_FRAME_TARGETS_TEXT,
    build_requests,
    is_below_frame_min_tier,
)
from src.gui.controllers.frame_analysis_targets import frame_estimate_text as targets_frame_estimate_text
from src.gui.controllers.frame_cache_loader import (
    FRAME_ANALYSIS_WORK_DIR,
    FrameCacheLoader,
    FrameCacheSnapshot,
    frame_cache_service,
    load_crop_exclusion_names,
    load_manual_frame_names,
    load_manual_vr_names,
    source_paths,
)
from src.gui.controllers.frame_progress_buffer import (
    FrameProgressBuffer,
    StageProgress,
    frame_analysis_stage_text,
)
from src.gui.controllers.manual_target_controller import ManualTargetController
from src.gui.frame_analysis_skip import (
    MANUAL_HDR_SKIP_NOTICE,
    SkipKind,
    select_run_targets,
    skip_reason_for_crop_scan,
    skip_reason_for_frame_analysis,
)
from src.gui.menus.menu_bar import MainMenuController
from src.gui.table.column_history import ColumnHistoryRecorder
from src.gui.table.frame_analysis_columns import (
    ACTIVE_FRAME_COLUMN,
    DOWNSCALE_CANDIDATE_COLUMN,
    result_summary_text,
)
from src.gui.table.table_context_menu import set_row_frame_analysis_opener
from src.gui.table.table_empty_overlay import TableEmptyOverlay
from src.gui.table.video_table_view import VideoTableView
from src.gui.widgets.filter_bar import FilterTuningBar
from src.gui.widgets.footer_bar import FooterBar
from src.gui.widgets.header_bar import HeaderBar, SplitAnalyzeButton
from src.service.analysis_store import open_default_store
from src.service.frame_analysis_service import (
    FrameAnalysisRequest,
    FrameAnalysisService,
    crop_review_status,
)
from src.service.frame_analysis_store import FrameAnalysisStore
from src.service.session import ClassifierSession

logger = logging.getLogger(__name__)

CROP_SCAN_LABEL: str = "여백 감지"
CROP_SCAN_NO_FILES_TEXT: str = "여백 감지할 영상 파일을 찾지 못했습니다."
CROP_SCAN_FFMPEG_BLOCK_TEMPLATE: str = "FFmpeg를 쓸 수 없어 여백 감지를 시작할 수 없습니다: {reason}"
CROP_SCAN_ERROR_TEMPLATE: str = "여백 감지 중 오류가 발생했습니다: {error}"
# - 감지 전용 실행은 영상이 3개 이상일 때만 남은 시간을 표시(짧은 실행에서 오차가 큼)
CROP_SCAN_REMAINING_MIN_TOTAL: int = 3
# - 영상별 결과 줄을 표에 반영하는 간격. 도착하는 대로 한 줄씩 그리지 않도록 묶어서 처리
FRAME_ANALYSIS_FLUSH_MS: int = 300
# - 정밀 분석 완료 뒤 이어서 실행할 후속 단계 이름
FOLLOWUP_CROP: str = "crop"
FOLLOWUP_FRAME: str = "frame"


def run_frame_analysis_body(
    service: FrameAnalysisService,
    requests: Sequence[FrameAnalysisRequest],
    cancel_event: threading.Event,
    sink: FrameAnalysisSink,
    *,
    crop_only: bool = False,
    crop_only_names: frozenset[str] = frozenset(),
) -> None:
    """요청 목록을 분석하고 완료분 캐시를 읽어 종료 이벤트를 보냄

    - crop_only면 검은 여백 스캔만 실행하고 캐시 재사용 수도 같은 기준으로 셈
    - crop_only_names에 든 영상은 전체 실행에서도 검은 여백만 감지
    - 취소는 예외로 올라오므로 삼키고, 이미 저장된 완료분을 캐시에서 다시 읽어 보존
    - 캐시 조회와 결과 읽기도 DB 오류가 날 수 있어 같은 오류 경계 안에서 실행하고, 실패는 문구를 담아 종료 이벤트로 넘김
    - 캐시 재사용 수는 실행 전에 재어 두어야 결과를 저장한 뒤와 구분됨
    """

    def on_progress(
        completed: int, total: int, filename: str, result: FrameAnalysisResult
    ) -> None:
        sink(FrameAnalysisProgress(completed, total, filename, result))

    def on_stage(filename: str, stage: str, done: int, total_stage: int) -> None:
        sink(FrameAnalysisStageProgress(0, 0, filename, stage, done, total_stage))

    total = len({request.key for request in requests})
    # - 캐시 조회와 결과 읽기도 DB 오류가 날 수 있으므로 함께 경계 안에 둠
    try:
        cached_count = service.count_cached(requests, crop_only=crop_only, crop_only_names=crop_only_names)
        with contextlib.suppress(InterruptedError):
            service.analyze_batch(
                requests,
                crop_only=crop_only,
                cancel_event=cancel_event,
                on_progress=on_progress,
                on_stage=on_stage,
                crop_only_names=crop_only_names,
            )
        results = service.load_results(requests)
    except Exception as error:
        sink(FrameAnalysisFinished({}, error=str(error) or type(error).__name__, crop_only=crop_only))
        return
    sink(
        FrameAnalysisFinished(
            results,
            cancelled=cancel_event.is_set(),
            total=total,
            cached_count=cached_count,
            crop_only=crop_only,
        )
    )


class FrameAnalysisWorker(QThread):
    """화질 분석 배치를 실행하는 전용 스레드"""

    progress = pyqtSignal(int, int, str, object)
    stage_progress = pyqtSignal(int, int, str, str, int, int)
    completed = pyqtSignal(object)

    def __init__(
        self,
        service: FrameAnalysisService,
        requests: Sequence[FrameAnalysisRequest],
        cancel_event: threading.Event,
        parent: QWidget | None = None,
        *,
        crop_only: bool = False,
        crop_only_names: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._requests = list(requests)
        self._cancel_event = cancel_event
        self._crop_only = crop_only
        self._crop_only_names = crop_only_names

    def cancel(self) -> None:
        """진행 중인 화질 분석 취소 요청"""
        self._cancel_event.set()

    def run(self) -> None:
        """워커 스레드 본문"""
        run_frame_analysis_body(
            self._service,
            self._requests,
            self._cancel_event,
            self._emit,
            crop_only=self._crop_only,
            crop_only_names=self._crop_only_names,
        )

    def _emit(self, event: FrameAnalysisEvent) -> None:
        """워커 스레드에서 진행과 종료를 시그널로 넘김"""
        if isinstance(event, FrameAnalysisProgress):
            self.progress.emit(event.completed, event.total, event.filename, event.result)
        elif isinstance(event, FrameAnalysisStageProgress):
            self.stage_progress.emit(
                event.completed,
                event.total,
                event.filename,
                event.stage,
                event.stage_done,
                event.stage_total,
            )
        else:
            self.completed.emit(event)


@dataclass(frozen=True)
class FrameAnalysisHost:
    """컨트롤러가 메인 창에서 받는 의존성 묶음"""

    parent: QWidget
    table_view: VideoTableView
    footer: FooterBar
    menu: MainMenuController
    filter_bar: FilterTuningBar
    report_provider: Callable[[], SummaryReport | None]
    config_provider: Callable[[], ClassifierConfig]
    classifier_provider: Callable[[], ClassifierSession | None]
    start_analysis: Callable[[bool, bool], object]
    is_busy: Callable[[], bool]
    re_evaluate: Callable[[Callable[[], None]], None]
    apply_candidates: Callable[[list[VideoEvaluation]], None]
    show_columns: Callable[[bool], None]
    notify: Callable[[str], None]


class FrameAnalysisController:
    """화질 분석 실행, 표 갱신, 진행 표시, 수동 VR 보호를 조율하는 컨트롤러"""

    def __init__(self, host: FrameAnalysisHost, store: FrameAnalysisStore) -> None:
        self._host = host
        self._store = store
        self._worker: FrameAnalysisWorker | None = None
        self._requests: list[FrameAnalysisRequest] = []
        self._results: dict[str, FrameAnalysisResult] = {}
        # - 여백 확인 표시를 셀 때 쓰는 리포트 전체 요청. 파일 식별 값 조회(os.stat)를 묶음 주기마다 되풀이하지 않도록 보관
        self._report_requests: list[FrameAnalysisRequest] = []
        self._shown_roots: set[tuple[str, bool]] = set()
        self._start_root: str = ""
        self._crop_scan: bool = False
        # - 방금 시작한 실행이 건너뛴 영상의 사유별 개수. 완료 상태 줄에 함께 알리는 데 씀
        self._skipped_counts: dict[SkipKind, int] = {}
        self._pending_followup: str = ""
        self._estimator: ProgressEstimator = ProgressEstimator()
        self._current_completed: int = 0
        self._current_total: int = 0
        self._current_filename: str = ""
        # - 실시간 표 갱신용 대기 결과와 짧은 묶음 타이머. 타이머가 살아 있으면 다시 걸지 않아 주기를 지킴
        self._pending_results: dict[str, FrameAnalysisResult] = {}
        self._flush_timer = QTimer()
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(FRAME_ANALYSIS_FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush_frame_results)
        # - 단계 이벤트는 영상 하나에 여러 번 오므로 마지막 값만 묶어 두었다가 같은 주기로 반영
        self._stage_buffer = FrameProgressBuffer(self._apply_stage_progress, FRAME_ANALYSIS_FLUSH_MS)
        # - 캐시 적재는 워커 스레드가 맡고, 적재를 기다리던 분석 시작은 (대상, 감지 전용, 강제)로 보관
        self._cache = FrameCacheLoader(store)
        self._pending_start: tuple[list[VideoEvaluation], bool, bool] | None = None
        # - 적재한 리포트 전체 요청의 작업 폴더. 다른 폴더의 요청을 실행 시작에 재사용하지 않으려고 기억
        self._report_root: str = ""
        # - 마지막으로 계산한 여백 확인 필요 표시. 결과를 적용할 때마다 다시 조회하지 않도록 보관
        self._review_flagged: frozenset[str] = frozenset()
        host.table_view.selection_changed.connect(lambda _count: self.refresh_actions())
        host.table_view.table_model.modelReset.connect(self.reload_cached)
        self.refresh_actions()

    @property
    def is_running(self) -> bool:
        """화질 분석이 실행 중인지 여부"""
        return self._worker is not None

    def reload_cached(self) -> None:
        """현재 작업 폴더의 캐시 결과와 수동 VR 보호를 표에 다시 반영

        - 파일 지문과 SQLite 조회는 워커 스레드가 맡고 화면 스레드는 결과 적용만 함
        - 폴더가 바뀌거나 표가 비워지면 이전 폴더의 진행 중 분석을 취소해 결과가 새 표에 섞이지 않게 함
        - 적재 중에 시작하려던 분석은 적재가 끝난 뒤 같은 요청으로 이어서 시작
        """
        report = self._host.report_provider()
        if self.is_running and (report is None or report.root_dir != self._start_root):
            self.cancel()
        if report is None:
            self._cache.cancel()
            self._pending_start = None
            self._clear_cached_state()
        else:
            # - 진행 중인 적재가 있으면 그 폴더, 없으면 마지막으로 적용한 폴더를 기준으로 봄
            active_root = (
                self._cache.root_dir if self._cache.pending else self._report_root
            )
            if active_root and active_root != report.root_dir:
                # - 폴더가 바뀌면 이전 폴더의 캐시 결과와 지정을 먼저 지워 새 행에 섞이지 않게 함
                self._cache.cancel()
                self._pending_start = None
                self._clear_cached_state()
            self._cache.start(
                report.evaluations,
                report.root_dir,
                self._host.config_provider(),
                self._apply_snapshot,
            )
        self._apply_results(show_columns=False)
        self.refresh_actions()

    def _clear_cached_state(self) -> None:
        """표에 반영했던 캐시 결과와 영상별 지정을 지움"""
        self._requests = []
        self._report_requests = []
        self._report_root = ""
        self._results = {}
        self._review_flagged = frozenset()
        self._host.table_view.table_model.set_manual_vr_overrides(())
        self._host.table_view.table_model.set_crop_exclusions(())
        self._host.table_view.table_model.set_manual_active_frames({})
        self._host.table_view.table_model.set_review_flagged(())
        self._host.filter_bar.set_review_flagged_count(0)

    def _apply_snapshot(self, snapshot: FrameCacheSnapshot | None) -> None:
        """워커 스레드가 읽은 캐시 결과와 영상별 지정을 표에 반영. 적재 실패면 기다리던 시작만 잇게 함"""
        report = self._host.report_provider()
        if snapshot is None:
            self._resume_pending_start()
            return
        if report is None or report.root_dir != snapshot.root_dir:
            return
        self._report_requests = snapshot.requests
        self._report_root = snapshot.root_dir
        self._requests = list(snapshot.requests)
        self._results = snapshot.results
        self._review_flagged = snapshot.review_flagged
        self._host.table_view.table_model.set_manual_vr_overrides(snapshot.manual_vr)
        self._host.table_view.table_model.set_crop_exclusions(snapshot.crop_excluded)
        self._host.table_view.table_model.set_manual_active_frames(snapshot.manual_frames)
        self._apply_results(show_columns=False)
        self.refresh_actions()
        self._resume_pending_start()

    def _resume_pending_start(self) -> None:
        """적재를 기다리던 분석을 이미 읽은 요청으로 이어서 시작"""
        pending = self._pending_start
        self._pending_start = None
        if pending is None or self.is_running or self._host.is_busy():
            return
        if self._host.report_provider() is None:
            return
        evals, crop_only, force = pending
        self._begin(evals, crop_only=crop_only, force=force, requests=list(self._requests))

    def analyze_selected(self) -> None:
        """선택한 영상의 화질 분석 시작

        - 수동 지정은 대상 해상도와 직접 지정 여부와 무관하게 강제로 측정
        """
        self._start(self._host.table_view.get_selected_evaluations(), crop_only=False, force=True)

    def analyze_all(self) -> None:
        """현재 작업 폴더의 모든 영상 화질 분석 시작"""
        report = self._host.report_provider()
        self._start(list(report.evaluations) if report is not None else [], crop_only=False)

    def analyze_evaluations(self, evals: Sequence[VideoEvaluation], crop_only: bool = False) -> None:
        """표 우클릭에서 지정한 영상만 화질 분석하거나 검은 여백을 감지. 수동 지정이므로 강제 측정"""
        self._start(evals, crop_only=crop_only, force=True)

    def detect_crop_selected(self) -> None:
        """선택한 영상의 검은 여백만 빠르게 감지"""
        self._start(self._host.table_view.get_selected_evaluations(), crop_only=True, force=True)

    def detect_crop_all(self) -> None:
        """현재 작업 폴더의 모든 영상 검은 여백 감지"""
        report = self._host.report_provider()
        self._start(list(report.evaluations) if report is not None else [], crop_only=True)

    def run_mode(self, mode: str, *, force_reanalyze: bool = False) -> None:
        """상단 분할 버튼 모드에 맞는 분석을 시작하고, 정밀, 화질 분석은 필요한 다음 단계를 잇게 함"""
        if mode == SplitAnalyzeButton.MODE_FRAME:
            self.run_frame_mode(force_reanalyze=force_reanalyze)
        elif mode == SplitAnalyzeButton.MODE_FULL:
            self.run_full_analysis(force_reanalyze=force_reanalyze)
        else:
            self._host.start_analysis(False, force_reanalyze)

    def _start_chained_analysis(self, followup: str, *, force_reanalyze: bool) -> None:
        """정밀 분석을 시작하고, 실제로 시작된 경우에만 완료 뒤 후속 단계를 예약"""
        if self._host.is_busy() or self.is_running:
            return
        self._pending_followup = followup
        started = self._host.start_analysis(True, force_reanalyze)
        if started is False:
            self._pending_followup = ""

    def run_full_analysis(self, *, force_reanalyze: bool = False) -> None:
        """정밀 분석을 시작하고, 검은 여백 자동 자르기가 켜져 있으면 완료 뒤 감지를 이어서 실행"""
        followup = FOLLOWUP_CROP if self._host.config_provider().encode.crop_black_bars else ""
        self._start_chained_analysis(followup, force_reanalyze=force_reanalyze)

    def run_frame_mode(self, *, force_reanalyze: bool = False) -> None:
        """화질 분석을 시작하고, 키프레임이 없으면 정밀 분석을 먼저 돌린 뒤 이어서 실행"""
        report = self._host.report_provider()
        if report is None or count_keyframe_missing(report.evaluations) > 0:
            self._start_chained_analysis(FOLLOWUP_FRAME, force_reanalyze=force_reanalyze)
            return
        self.analyze_all()

    def on_analysis_finished(self) -> None:
        """정밀 분석이 끝나면 예약된 후속 단계(전체 검은 여백 감지, 전체 화질 분석)를 실행"""
        pending = self._pending_followup
        self._pending_followup = ""
        if pending == FOLLOWUP_CROP:
            self.detect_crop_all()
        elif pending == FOLLOWUP_FRAME:
            self.analyze_all()

    def clear_pending_followup(self) -> None:
        """정밀 분석이 중단되면 예약된 후속 단계를 지워 다음 분석에 섞이지 않게 함"""
        self._pending_followup = ""

    def analysis_estimate_text(self, mode: str) -> str:
        """분석 모드 팝업의 예상 문구. 화질 분석은 현재 기준 대상 수, 나머지는 분류 세션 예상값"""
        if mode == SplitAnalyzeButton.MODE_FRAME:
            return self.frame_estimate_text()
        classifier = self._host.classifier_provider()
        if classifier is None:
            return ""
        estimate = classifier.estimate_analysis(mode == SplitAnalyzeButton.MODE_FULL)
        return format_estimate_label(estimate.pending_count, estimate.expected_sec)

    def frame_estimate_text(self) -> str:
        """현재 리포트와 화질 분석 대상 해상도 기준으로 측정할 영상 수 문구"""
        report = self._host.report_provider()
        if report is None:
            return ""
        min_tier = self._host.config_provider().encode.frame_analysis_min_tier
        return targets_frame_estimate_text(report.evaluations, min_tier)

    def refresh_actions(self) -> None:
        """메뉴 항목의 대상, 선택, 실행 상태를 갱신"""
        report = self._host.report_provider()
        self._host.menu.sync_frame_analysis_state(
            has_list=report is not None and bool(report.evaluations),
            has_selection=bool(self._host.table_view.get_selected_evaluations()),
            running=self.is_running,
            crop_running=self.is_running and self._crop_scan,
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
        self._host.table_view.table_model.set_manual_vr_overrides(
            load_manual_vr_names(self._store, report.evaluations, report.root_dir)
        )
        self._refresh_review_flags()
        self._apply_results(show_columns=False)
        self._host.re_evaluate(lambda: None)
        self._host.notify(
            "VR 영상으로 보호를 켰습니다. 다음 화질 분석부터 여백 감지와 잘라내기에서 보호합니다."
            if protected
            else "VR 영상으로 보호를 껐습니다. 다음 화질 분석부터 여백을 다시 감지합니다."
        )

    def toggle_crop_exclusion(self, evals: list[VideoEvaluation], excluded: bool) -> None:
        """선택 영상의 검은 여백 잘라내기 제외를 저장하고 즉시 재평가만 요청

        - 화질 분석 캐시는 지우지 않아 측정 결과를 그대로 두고 판정만 다시 함
        """
        report = self._host.report_provider()
        if report is None or not evals:
            return
        for ev in evals:
            path = resolve_source_filepath(report.root_dir, ev.video.filename)
            self._store.set_crop_exclusion(path, excluded)
        self._host.table_view.table_model.set_crop_exclusions(
            load_crop_exclusion_names(self._store, report.evaluations, report.root_dir)
        )
        self._refresh_review_flags()
        self._apply_results(show_columns=False)
        self._host.re_evaluate(lambda: None)
        self._host.notify(
            "여백 잘라내기 제외를 켰습니다. 이 영상은 인코딩할 때 화면 여백을 잘라내지 않습니다."
            if excluded
            else "여백 잘라내기 제외를 껐습니다. 이 영상은 화면 여백이 감지되면 자동으로 잘라냅니다."
        )

    def set_manual_active_frame(
        self, evals: list[VideoEvaluation], frame: ActiveFrame | None
    ) -> None:
        """선택 영상의 사용자 지정 활성 화면을 저장하고 즉시 재평가만 요청

        - frame이 None이면 지정을 지워 자동 감지 결과로 되돌림
        - 화질 분석 캐시는 지우지 않아 측정 결과를 그대로 두고 판정과 인코딩 입력만 바꿈
        - 잘라내기 제외 전환과 같이 실행 취소 기록은 남기지 않음
        - 사용자가 고른 활성 화면은 자동 감지 영역을 확인한 것으로 남겨 같은 영역의 경고가 다시 뜨지 않게 함
        """
        report = self._host.report_provider()
        if report is None or not evals:
            return
        for ev in evals:
            path = resolve_source_filepath(report.root_dir, ev.video.filename)
            self._acknowledge_review(path, ev.video.filename)
            self._store.set_manual_active_frame(
                path,
                frame,
                frame_width=ev.video.width,
                frame_height=ev.video.height,
            )
        self._host.table_view.table_model.set_manual_active_frames(
            load_manual_frame_names(self._store, report.evaluations, report.root_dir)
        )
        self._refresh_review_flags()
        self._apply_results(show_columns=False)
        self._host.re_evaluate(lambda: None)
        self._host.notify(
            "사용자 지정 활성 화면을 저장했습니다. 이 영상은 인코딩할 때 지정한 영역을 잘라냅니다."
            if frame is not None
            else "사용자 지정 활성 화면을 지웠습니다. 이 영상은 자동으로 감지한 활성 화면을 씁니다."
        )

    def cancel(self) -> None:
        """진행 중인 화질 분석 취소 요청"""
        if self._worker is not None:
            self._worker.cancel()

    def shutdown(self) -> None:
        """창을 닫을 때 실행 중인 분석을 취소하고 스레드 종료를 기다린 뒤 캐시 연결을 닫음

        - 닫힌 창의 표로 결과를 밀어 넣지 않도록 묶음 타이머와 대기 결과를 함께 비움
        """
        worker = self._worker
        if worker is not None:
            worker.cancel()
            worker.wait()
        # - 캐시 적재 스레드도 저장소를 쓰므로 연결을 닫기 전에 끝까지 기다림
        self._cache.shutdown()
        self._flush_timer.stop()
        self._stage_buffer.drop()
        self._pending_results = {}
        self._store.close()

    def _start(
        self, evals: Sequence[VideoEvaluation], *, crop_only: bool, force: bool = False
    ) -> None:
        if self.is_running or self._host.is_busy() or not evals:
            return
        report = self._host.report_provider()
        if report is None:
            return
        if self._cache.pending and self._cache.root_dir == report.root_dir:
            # - 캐시 적재가 끝나면 그 요청을 재사용해 시작하므로 파일 지문을 두 번 읽지 않음
            self._pending_start = (list(evals), crop_only, force)
            return
        self._begin(evals, crop_only=crop_only, force=force)

    def _begin(
        self,
        evals: Sequence[VideoEvaluation],
        *,
        crop_only: bool,
        force: bool = False,
        requests: Sequence[FrameAnalysisRequest] | None = None,
    ) -> None:
        """대상 판정을 마치고 워커를 시작. requests를 주면 그 목록으로 대상을 좁힘"""
        report = self._host.report_provider()
        if report is None:
            return
        config = self._host.config_provider()
        reason_of = skip_reason_for_crop_scan if crop_only else skip_reason_for_frame_analysis
        # - 영상마다 스트림 요약을 다시 읽지 않도록 사유를 한 번만 계산해 대상과 집계에 함께 씀
        reasons = [reason_of(ev) for ev in evals]
        selection = select_run_targets(evals, reasons, crop_only=crop_only, force=force)
        targets = list(selection.targets)
        if selection.forced_hdr_count > 0:
            self._host.notify(MANUAL_HDR_SKIP_NOTICE.format(count=selection.forced_hdr_count))
        if not targets:
            if not force:
                self._host.notify(NO_CROP_TARGETS_TEXT if crop_only else NO_FRAME_TARGETS_TEXT)
            return
        self._skipped_counts = selection.skipped_counts
        crop_only_names = frozenset(
            ev.video.filename
            for ev in targets
            if not crop_only
            and is_below_frame_min_tier(
                ev.video.width, ev.video.height, config.encode.frame_analysis_min_tier
            )
        )
        self._start_root = report.root_dir
        self._requests = self._target_requests(targets, report.root_dir, requests)
        if not self._requests:
            self._host.notify(
                CROP_SCAN_NO_FILES_TEXT
                if crop_only
                else "화질 분석할 영상 파일을 찾지 못했습니다."
            )
            return
        capability = probe_ffmpeg_capability(config.execution.ffmpeg_path)
        if not capability.usable:
            self._host.notify(
                CROP_SCAN_FFMPEG_BLOCK_TEMPLATE.format(reason=capability.reason)
                if crop_only
                else f"FFmpeg를 쓸 수 없어 화질 분석을 시작할 수 없습니다: {capability.reason}"
            )
            return
        requests = self._requests
        self._crop_scan = crop_only
        cancel_event = threading.Event()
        service = FrameAnalysisService(
            self._store,
            work_dir=FRAME_ANALYSIS_WORK_DIR,
            ffmpeg_executable=capability.executable,
            has_vmaf=capability.has_vmaf,
            mode=config.encode.frame_analysis_mode,
            scale_algorithm=config.encode.scale_algorithm,
            thresholds=frame_thresholds_from_config(config),
        )
        worker = FrameAnalysisWorker(
            service,
            requests,
            cancel_event,
            self._host.parent,
            crop_only=crop_only,
            crop_only_names=crop_only_names,
        )
        worker.progress.connect(self._on_progress)
        worker.stage_progress.connect(self._on_stage)
        worker.completed.connect(self._on_completed)
        self._worker = worker
        self._estimator = ProgressEstimator()
        self._current_completed = 0
        self._current_total = len(requests)
        self._current_filename = requests[0].filename
        self._pending_results = {}
        self._host.footer.set_frame_analysis_progress(
            0,
            len(requests),
            requests[0].filename,
            work_dir=FRAME_ANALYSIS_WORK_DIR,
            crop_only=crop_only,
        )
        self.refresh_actions()
        worker.start()

    def _target_requests(
        self,
        targets: Sequence[VideoEvaluation],
        root_dir: str,
        loaded: Sequence[FrameAnalysisRequest] | None,
    ) -> list[FrameAnalysisRequest]:
        """실행 대상의 요청 목록. 같은 폴더를 미리 적재했으면 그 요청의 지문을 재사용"""
        overrides = self._store.load_vr_overrides(source_paths(targets, root_dir))
        cached = list(loaded) if loaded is not None and root_dir == self._report_root else []
        return build_requests(targets, root_dir, overrides, cached=cached)

    def _on_progress(
        self, completed: int, total: int, filename: str, result: FrameAnalysisResult | None = None
    ) -> None:
        self._current_completed = completed
        self._current_total = total
        self._current_filename = filename
        # - 묶여 있던 단계 표시는 지금 값보다 오래되었으므로 버려 새 진행 값을 덮지 않게 함
        self._stage_buffer.drop()
        if result is not None:
            self._pending_results[filename] = result
            # - 묶음 타이머가 도는 중이면 다시 걸지 않아 갱신 주기가 늘어나지 않게 함
            if not self._flush_timer.isActive():
                self._flush_timer.start()
        self._show_progress(completed, total, filename)

    def _show_progress(self, completed: int, total: int, filename: str, detail: str = "") -> None:
        """하단 진행 표시를 갱신, 감지 전용 실행은 세부 단계 문구를 붙이지 않음"""
        self._host.footer.set_frame_analysis_progress(
            completed,
            total,
            filename,
            detail="" if self._crop_scan else detail,
            remaining=self._remaining_text(completed, total),
            work_dir=FRAME_ANALYSIS_WORK_DIR,
            crop_only=self._crop_scan,
        )

    def _flush_frame_results(self, *, log: bool = True) -> None:
        """대기 중인 화질 분석 결과를 표에 반영하고, 요청 시 도착분의 영상별 결과 줄을 기록"""
        pending = self._pending_results
        if not pending:
            return
        self._flush_timer.stop()
        self._pending_results = {}
        self._host.table_view.table_model.update_frame_analysis_results(pending)
        if log:
            self._log_result_lines(pending)

    def _log_result_lines(self, results: Mapping[str, FrameAnalysisResult]) -> None:
        """도착한 영상별 결과를 실행 종류(여백 감지/화질 분석)별 이름으로 작업 기록에 남김"""
        label = CROP_SCAN_LABEL if self._crop_scan else FRAME_ANALYSIS_LABEL
        for filename, result in results.items():
            logger.info("%s 결과: %s, %s", label, filename, result_summary_text(result))

    def _flush_on_stop(self, *, log: bool = True) -> None:
        """실행 종료 직전에 묶음 타이머를 멈추고 남은 결과를 모두 반영

        - 오류로 끝난 실행은 표에만 반영하고 영상별 결과 줄은 남기지 않음
        """
        self._flush_timer.stop()
        self._flush_frame_results(log=log)

    def _on_stage(
        self,
        completed: int,
        total: int,
        filename: str,
        stage: str,
        stage_done: int,
        stage_total: int,
    ) -> None:
        # - 세부 단계 이벤트는 영상 완료 수를 담지 않으므로 마지막 진행 값을 유지
        if total <= 0:
            completed = self._current_completed
            total = self._current_total
            filename = filename or self._current_filename
        self._stage_buffer.submit(StageProgress(completed, total, filename or "", stage, stage_done, stage_total))

    def _apply_stage_progress(self, progress: StageProgress) -> None:
        """묶여 있던 단계 진행 값을 하단 표시에 반영"""
        text = frame_analysis_stage_text(progress.stage, progress.stage_done, progress.stage_total)
        self._show_progress(progress.completed, progress.total, progress.filename, text)

    def _remaining_text(self, completed: int, total: int) -> str:
        """완료 수 기준 예상 남은 시간 문구. 추정 전이거나 감지 전용 소수 실행이면 빈 문자열"""
        if self._crop_scan and total < CROP_SCAN_REMAINING_MIN_TOTAL:
            return ""
        remaining = self._estimator.estimate(completed, total).remaining_sec
        return format_remaining(remaining) if remaining is not None else ""

    def _on_completed(self, event: object) -> None:
        worker = self._worker
        if worker is not None:
            worker.wait()
            self._worker = None
        finished = event if isinstance(event, FrameAnalysisFinished) else FrameAnalysisFinished({})
        crop_only = finished.crop_only
        # - 끝난 실행의 건너뜀 수는 완료 상태 줄에만 쓰고 곧바로 비워, 취소, 오류, 폴더 전환 뒤 다음 배치로 새지 않게 함
        skip_counts = self._skipped_counts
        self._skipped_counts = {}
        self._stage_buffer.drop()
        self._host.footer.clear_frame_analysis_progress()
        report = self._host.report_provider()
        if report is None or report.root_dir != self._start_root:
            self._crop_scan = False
            self._pending_results = {}
            self._flush_timer.stop()
            self.refresh_actions()
            return
        # - 마지막 결과 줄이 실행 종류(여백 감지/화질 분석)를 따르도록 flush 뒤에 해제
        self._flush_on_stop(log=finished.error == "")
        self._crop_scan = False
        if finished.error:
            self._host.notify(
                CROP_SCAN_ERROR_TEMPLATE.format(error=finished.error)
                if crop_only
                else f"화질 분석 중 오류가 발생했습니다: {finished.error}"
            )
        else:
            merged = dict(self._results)
            merged.update(finished.results)
            self._results = merged
            # - 새로 측정한 영상이 여백 확인 대상일 수 있으므로 표시를 다시 계산한 뒤 반영
            self._refresh_review_flags()
            self._apply_results(crop_only=crop_only)
            if finished.cancelled:
                self._host.notify(MSG_WORK_CANCELLED)
                if finished.results:
                    self._host.re_evaluate(lambda: None)
            else:
                self._host.re_evaluate(lambda: None)
                status = completion_status_text(
                    finished,
                    label=CROP_SCAN_LABEL if crop_only else FRAME_ANALYSIS_LABEL,
                    skip_counts=skip_counts,
                )
                if status:
                    self._host.notify(status)
        self.refresh_actions()

    def _acknowledge_review(self, path: str, filename: str) -> None:
        """사용자가 고른 활성 화면을 확인 표시로 저장

        - 자동 감지 영역이 여백 확인 대상일 때만 남겨, 다시 분석해 같은 영역이 나오면 경고가 뜨지 않게 함
        - 자동 감지로 되돌리기와 전체 화면 유지도 같은 호출로 확인 처리됨
        """
        result = self._results.get(filename)
        if result is None:
            return
        status = crop_review_status(
            result.active_region,
            crop_enabled=self._host.config_provider().encode.crop_black_bars,
            is_vr_protected=result.is_vr_protected,
        )
        if status.flagged:
            self._store.set_crop_review_acknowledged(path, result.active_region)

    def _apply_review_flags(self) -> None:
        """여백 확인 필요 표시 집합과 필터 칩 개수를 마지막으로 계산한 값으로 반영"""
        self._host.table_view.table_model.set_review_flagged(self._review_flagged)
        self._host.filter_bar.set_review_flagged_count(len(self._review_flagged))

    def _refresh_review_flags(self) -> None:
        """여백 확인 필요 표시를 지금 캐시와 설정으로 다시 계산

        - 적재한 요청의 내용 지문을 그대로 써 캐시 조회만 하고 파일을 다시 읽지 않음
        - 실행 대상이 일부만 분석돼도 표의 나머지 영상 표시가 사라지지 않도록 적재한 리포트 전체를 기준으로 셈
        """
        report = self._host.report_provider()
        config = self._host.config_provider()
        if report is None or not self._report_requests or report.root_dir != self._report_root:
            self._review_flagged = frozenset()
        else:
            statuses = frame_cache_service(self._store, config).crop_review_statuses(
                self._report_requests,
                crop_enabled=config.encode.crop_black_bars,
            )
            self._review_flagged = frozenset(statuses)
        self._apply_review_flags()

    def _apply_results(self, *, show_columns: bool = True, crop_only: bool = False) -> None:
        self._host.table_view.table_model.set_frame_analysis_results(self._results)
        self._apply_review_flags()
        if show_columns:
            self._maybe_show_columns(crop_only)

    def _maybe_show_columns(self, crop_only: bool) -> None:
        report = self._host.report_provider()
        key = ("" if report is None else report.root_dir, crop_only)
        if not self._results or report is None or key in self._shown_roots:
            return
        self._shown_roots.add(key)
        self._host.show_columns(crop_only)


class FrameAnalysisWindow(Protocol):
    """화질 분석 컨트롤러를 붙이는 메인 창의 공개 인터페이스"""

    table_view: VideoTableView
    footer_bar: FooterBar
    menu: MainMenuController
    manual_targets: ManualTargetController
    column_history: ColumnHistoryRecorder
    header_bar: HeaderBar
    filter_bar: FilterTuningBar

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

    def start_analyze(self, include_keyframe: bool = True, force_reanalyze: bool = False) -> None:
        """빠른 분석 또는 정밀 분석 백그라운드 작업 시작"""
        ...

    def sync_preset(self) -> None:
        """표의 숨김 열이 바뀐 뒤 필터 바 라벨과 보기 메뉴 열 프리셋 체크를 다시 맞춤"""
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

    def show_columns(crop_only: bool) -> None:
        # - 분석 결과에 따른 자동 표시는 사용자 열 표시 변경이 아니므로 실행 취소 기록을 남기지 않음
        columns = (ACTIVE_FRAME_COLUMN,) if crop_only else (ACTIVE_FRAME_COLUMN, DOWNSCALE_CANDIDATE_COLUMN)
        for col in columns:
            if window.table_view.isColumnHidden(col):
                window.table_view.setColumnHidden(col, False)
        # - 자동으로 연 열 때문에 프리셋 라벨과 보기 메뉴 체크가 어긋나지 않도록 다시 맞춤
        window.sync_preset()

    def config_provider() -> ClassifierConfig:
        classifier = window.classifier
        return classifier.config if classifier is not None else ClassifierConfig()

    def _start_meta_analysis(include_keyframe: bool, force_reanalyze: bool) -> bool:
        window.start_analyze(include_keyframe=include_keyframe, force_reanalyze=force_reanalyze)
        return is_busy()

    host = FrameAnalysisHost(
        parent=window.table_view,
        table_view=window.table_view,
        footer=window.footer_bar,
        menu=window.menu,
        filter_bar=window.filter_bar,
        report_provider=lambda: window.current_report,
        config_provider=config_provider,
        classifier_provider=lambda: window.classifier,
        start_analysis=_start_meta_analysis,
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
        toggle_crop_exclusion=controller.toggle_crop_exclusion,
        set_manual_active_frame=controller.set_manual_active_frame,
    )
    window.menu.act_frame_selected.triggered.connect(controller.analyze_selected)
    window.menu.act_frame_all.triggered.connect(controller.run_frame_mode)
    window.menu.act_crop_selected.triggered.connect(controller.detect_crop_selected)
    window.menu.act_crop_all.triggered.connect(controller.detect_crop_all)
    window.menu.act_cancel.triggered.connect(controller.cancel)
    header = window.header_bar
    empty_overlay = getattr(window.table_view, "empty_overlay", None)
    min_tier = config_provider().encode.frame_analysis_min_tier

    def _min_tier_provider() -> str:
        return config_provider().encode.frame_analysis_min_tier

    def _on_min_tier_changed(tier_key: str) -> None:
        window.manual_targets.set_frame_analysis_min_tier(tier_key)
        header.set_frame_min_tier(tier_key)
        if isinstance(empty_overlay, TableEmptyOverlay):
            empty_overlay.set_frame_min_tier(tier_key)

    header.split_analyze.set_estimate_provider(controller.analysis_estimate_text)
    header.set_min_tier_provider(_min_tier_provider)
    header.set_frame_min_tier(min_tier)
    header.frame_min_tier_changed.connect(_on_min_tier_changed)
    if isinstance(empty_overlay, TableEmptyOverlay):
        empty_overlay.set_estimate_provider(controller.analysis_estimate_text)
        empty_overlay.set_min_tier_provider(_min_tier_provider)
        empty_overlay.set_frame_min_tier(min_tier)
        empty_overlay.frame_min_tier_changed.connect(_on_min_tier_changed)
    set_row_frame_analysis_opener(controller.analyze_evaluations)
    window.footer_bar.frame_analysis_cancel_requested.connect(controller.cancel)
    window.footer_bar.destroyed.connect(controller.shutdown)
    window.footer_bar.destroyed.connect(lambda: set_row_frame_analysis_opener(None))
    return controller

