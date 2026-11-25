"""백그라운드 워커 생성, 실행 수명주기 관리 및 작업 완료/오류 결과 반영 모듈"""

import logging
from collections.abc import Callable
from typing import Protocol

from PyQt6.QtWidgets import QMessageBox, QWidget

from src.core.analysis.reanalysis_notice import build_reanalysis_notice_plan, count_keyframe_missing
from src.core.eta import ProgressEstimate, ProgressEstimator, format_rate, format_remaining
from src.core.formatting import format_duration_hms
from src.core.messages import (
    MSG_WORK_CANCELLED,
    format_cancelled_progress,
    format_restore_finished,
    format_task_start,
)
from src.core.models import PERCENT_MULTIPLIER, SummaryReport
from src.gui.constants import WORKER_CANCEL_WAIT_MS, WORKER_TERMINATE_WAIT_MS
from src.gui.controllers.frame_analysis_controller import FrameAnalysisController
from src.gui.controllers.worker import ClassifierWorker
from src.service.analysis_probe import StoredRows
from src.service.analysis_timing import (
    MODE_FAST,
    MODE_FULL,
    average_seconds_per_item,
    record_seconds_per_item,
)
from src.service.analyzed_dirs import mark_analyzed_dir
from src.service.session import ClassifierSession

CompletionFormatter = Callable[[SummaryReport | None], str]

logger = logging.getLogger(__name__)


def log_evaluation_lines(report: SummaryReport) -> None:
    """적용한 리포트의 영상별 판정을 작업 기록에 남김

    - 한 줄씩 그대로 넘겨 작업 기록 창이 새 줄만 덧붙이게 하고, 파일에는 순서대로 쌓이게 함
    """
    for ev in report.evaluations:
        logger.info(
            "영상 분석 결과: %s, %s, %s",
            ev.video.filename,
            ev.action_label,
            ev.reason_label,
        )


def log_restore_summary(report: SummaryReport, elapsed_sec: float) -> None:
    """이전 분석 결과 복원 한 번을 요약 한 줄로 남김

    - 복원은 사용자가 시작한 분석이 아니므로 영상별 줄을 남기지 않고 건수와 소요만 알림
    """
    logger.info("%s", format_restore_finished(report.total_count, elapsed_sec, report.encoded_count))


class WorkerHost(Protocol):
    """백그라운드 워커 제어기(TaskRunner)가 메인 윈도우와 통신하기 위한 호스트 인터페이스"""

    @property
    def active_worker(self) -> ClassifierWorker | None:
        """현재 실행 중인 워커 인스턴스 반환"""
        ...

    @active_worker.setter
    def active_worker(self, worker: ClassifierWorker | None) -> None: ...

    @property
    def current_report(self) -> SummaryReport | None:
        """가장 최근 완료된 요약 리포트 반환"""
        ...

    @property
    def frame_analysis(self) -> FrameAnalysisController:
        """화질 분석 실행 컨트롤러. 정밀 분석 완료 뒤 후속 단계 연결에 씀"""
        ...

    def as_widget(self) -> QWidget:
        """메시지 박스 부모로 사용할 호스트 QWidget 반환"""
        ...
    def ensure_classifier(self) -> ClassifierSession | None:
        """현재 경로 기반 분류 세션 준비 및 반환"""
        ...
    def apply_filters_to_classifier(self, clf: ClassifierSession) -> None:
        """UI 필터/임계값 설정을 분류기 인스턴스에 반영"""
        ...
    def set_busy_state(self, busy: bool, status_msg: str = "") -> None:
        """작업 중 컨트롤 잠금 및 하단 상태 문구 전환"""
        ...
    def set_progress(self, value: int, detail: str = "", tooltip: str = "") -> None:
        """진행률 표시줄 값(0~100), 막대 안 문구, 툴팁 설정"""
        ...
    def set_progress_pending(self) -> None:
        """완료 개수를 아직 알 수 없는 구간을 무한 진행으로 표시"""
        ...
    def set_status_text(self, text: str, tooltip: str = "") -> None:
        """하단 상태 표시줄 텍스트와 툴팁 갱신"""
        ...
    def apply_report(self, report: SummaryReport) -> None:
        """새 요약 리포트를 대시보드, 테이블 UI에 반영"""
        ...


def _format_analyze_status(report: SummaryReport | None) -> str:
    return f"분석 완료: 총 {report.total_count if report else 0}개 영상"


_STATUS_FORMATTERS: dict[str, CompletionFormatter] = {
    ClassifierWorker.TASK_ANALYZE_FAST: _format_analyze_status,
    ClassifierWorker.TASK_ANALYZE_FULL: _format_analyze_status,
}


_ANALYZE_MODE_BY_TASK: dict[str, str] = {
    ClassifierWorker.TASK_ANALYZE_FAST: MODE_FAST,
    ClassifierWorker.TASK_ANALYZE_FULL: MODE_FULL,
}


def _build_progress_tooltip(estimate: ProgressEstimate) -> str:
    """진행 막대 툴팁: 경과 시간과 분석 속도(속도가 계산된 뒤에만 표시)"""
    text = f"경과 {format_duration_hms(estimate.elapsed_sec)}"
    if estimate.rate_per_sec is not None:
        text += f", 분석 속도 {format_rate(estimate.rate_per_sec)}"
    return text


def format_completion_status(task_type: str, report: SummaryReport | None) -> str:
    """완료된 워커 작업 유형에 대응하는 하단 상태 표시 문자열을 생성"""
    formatter = _STATUS_FORMATTERS.get(task_type, _format_analyze_status)
    return formatter(report)


class TaskRunner:
    """ClassifierWorker 백그라운드 작업 실행, 취소, 종료 대기 및 완료/실패 시그널 처리를 전담"""

    def __init__(self, host: WorkerHost) -> None:
        self._host = host
        self._estimator: ProgressEstimator = ProgressEstimator()
        self._last_current: int = 0
        self._last_total: int = 0

    def is_running(self) -> bool:
        """현재 백그라운드 워커가 실행 중인지 반환"""
        worker = self._host.active_worker
        return worker is not None and worker.isRunning()

    def start_analyze(
        self, include_keyframe: bool = True, force_reanalyze: bool = False
    ) -> None:
        """빠른 분석 또는 정밀 분석 워커를 실행"""
        clf = self._host.ensure_classifier()
        if clf is None:
            return
        self._host.apply_filters_to_classifier(clf)
        task = (
            ClassifierWorker.TASK_ANALYZE_FULL
            if include_keyframe
            else ClassifierWorker.TASK_ANALYZE_FAST
        )
        label = "정밀 분석" if include_keyframe else "빠른 분석"
        self.launch(clf, task, force_reanalyze=force_reanalyze, label=label)

    def launch(
        self,
        clf: ClassifierSession,
        task_type: str,
        *,
        force_reanalyze: bool = False,
        label: str = "작업",
        prescan: StoredRows | None = None,
        restore: bool = False,
    ) -> None:
        """ClassifierWorker 인스턴스를 생성하고 시그널을 바인딩하여 비동기 시작

        - restore가 참이면 시작 시 이전 결과 복원 작업. 완료 로그를 영상별 대신 요약 한 줄로 남김
        - prescan을 넘기면 복원 판정이 이미 읽은 파일 목록과 저장 행을 워커가 다시 읽지 않음
        """
        if self.is_running():
            return
        self._estimator.start()
        self._last_current = 0
        self._last_total = 0
        options = [
            text
            for text, enabled in (("하위 폴더 포함", clf.recursive), ("강제 재분석", force_reanalyze))
            if enabled
        ]
        logger.info("%s", format_task_start(label, options))
        self._host.set_progress_pending()
        self._host.set_busy_state(True, f"{label} 진행 중...")
        worker = ClassifierWorker(
            classifier=clf,
            task_type=task_type,
            force_reanalyze=force_reanalyze,
            parent=self._host.as_widget(),
            prescan=prescan,
        )
        worker.progress_updated.connect(self.handle_progress)
        worker.task_completed.connect(
            lambda task_type, report: self.handle_completed(
                task_type, report, restore=restore
            )
        )
        analyzed_root = clf.root_dir
        analyzed_recursive = clf.recursive
        worker.task_completed.connect(
            lambda task_type, report: self._record_analyzed(
                task_type, report, analyzed_root, analyzed_recursive
            )
        )
        worker.task_cancelled.connect(self.handle_cancelled)
        worker.task_failed.connect(self.handle_failed)
        self._host.active_worker = worker
        worker.start()

    @staticmethod
    def _record_analyzed(
        task_type: str,
        report: SummaryReport | None,
        root_dir: str,
        recursive: bool,
    ) -> None:
        """결과가 나온 분석 작업의 폴더를 기록해 이후 시작과 폴더 전환 때 자동 복원 대상으로 삼음"""
        if report is None:
            return
        if task_type in (ClassifierWorker.TASK_ANALYZE_FAST, ClassifierWorker.TASK_ANALYZE_FULL):
            mark_analyzed_dir(root_dir, recursive=recursive)

    def cancel(self) -> None:
        """실행 중인 워커에 중단 플래그를 전달"""
        if self.is_running():
            worker = self._host.active_worker
            if worker is not None:
                worker.cancel()
            self._host.set_status_text("작업 중단 요청 중...")

    def shutdown(self) -> None:
        """윈도우 종료 시 실행 중인 워커를 안전하게 중단 대기 및 정리"""
        worker = self._host.active_worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            if not worker.wait(WORKER_CANCEL_WAIT_MS):
                worker.terminate()
                worker.wait(WORKER_TERMINATE_WAIT_MS)

    def handle_progress(self, current: int, total: int, filename: str) -> None:
        """진행률 표시줄 및 하단 상태 문구를 현재 파일 기준으로 갱신하고 남은 시간을 함께 표시"""
        self._last_current = current
        self._last_total = total
        pct = round((current / max(total, 1)) * PERCENT_MULTIPLIER)
        estimate = self._estimator.estimate(current, total)
        self._host.set_progress(
            pct,
            f"{pct}%, {format_remaining(estimate.remaining_sec)}",
            _build_progress_tooltip(estimate),
        )
        self._host.set_status_text(f"[{current}/{total}] {filename}")

    def handle_completed(
        self, task_type: str, report: SummaryReport | None, *, restore: bool = False
    ) -> None:
        """워커 정상 완료 후 리포트, 충돌 안내, 완료 로그 UI 반영

        restore면 영상별 결과 줄 대신 복원 요약 한 줄만 남김
        """
        analyze_mode = _ANALYZE_MODE_BY_TASK.get(task_type)
        if analyze_mode is not None:
            record_seconds_per_item(
                analyze_mode, self._estimator.seconds_per_item(self._last_total)
            )
        self._host.set_progress(100)
        self._host.set_busy_state(False)
        if report is not None:
            self._host.apply_report(report)

        status_text = format_completion_status(task_type, report)
        if analyze_mode is not None:
            elapsed = self._estimator.elapsed_sec()
            if restore and report is not None:
                log_restore_summary(report, elapsed)
            else:
                logger.info("%s (소요 %s)", status_text, format_duration_hms(elapsed))
                if report is not None:
                    log_evaluation_lines(report)
        else:
            logger.info("%s", status_text)

        if analyze_mode == MODE_FAST and report is not None:
            keyframe_missing = count_keyframe_missing(report.evaluations)
            if keyframe_missing > 0:
                plan = build_reanalysis_notice_plan(
                    0,
                    keyframe_missing,
                    include_keyframe=False,
                    fast_sec_per_item=average_seconds_per_item(MODE_FAST),
                    full_sec_per_item=average_seconds_per_item(MODE_FULL),
                )
                self._host.set_status_text(plan.text, plan.tooltip)
                return
        # 분석 완료 개수는 푸터 요약과 핵심 지표 카드가 이미 표시하므로 중복 문구 없이 상태 줄을 비움
        self._host.set_status_text("" if analyze_mode is not None else status_text)
        if task_type == ClassifierWorker.TASK_ANALYZE_FULL and report is not None:
            self._host.frame_analysis.on_analysis_finished()

    def handle_cancelled(self, reason: str) -> None:
        """워커 중단 완료 시 진행률 초기화 및 취소 상태, 로그 반영"""
        self._host.frame_analysis.clear_pending_followup()
        self._host.set_progress(0)
        self._host.set_busy_state(False, MSG_WORK_CANCELLED)
        if self._last_total > 0:
            logger.info("%s", format_cancelled_progress(reason, self._last_current, self._last_total))
        else:
            logger.info("%s", reason)

    def handle_failed(self, error_msg: str) -> None:
        """워커 실패 시 오류 로그 기록 및 사용자 안내 대화상자 표시"""
        self._host.frame_analysis.clear_pending_followup()
        self._host.set_progress(0)
        summary = next((line.strip() for line in error_msg.splitlines() if line.strip()), error_msg)
        self._host.set_busy_state(False, f"오류 발생: {summary}")
        logger.error("%s", error_msg)
        QMessageBox.critical(
            self._host.as_widget(),
            "작업 오류",
            f"처리 중 오류가 발생했습니다.\n\n{error_msg}",
        )
