"""백그라운드 워커 생성, 실행 수명주기 관리 및 작업 완료/오류 결과 반영 모듈"""

import logging
from collections.abc import Callable
from typing import Protocol

from PyQt6.QtWidgets import QMessageBox, QWidget

from src.core.eta import ProgressEstimate, ProgressEstimator, format_rate, format_remaining
from src.core.formatting import format_duration_hms
from src.core.messages import (
    MSG_WORK_CANCELLED,
    format_cancelled_progress,
    format_clear_analysis_cache_confirm,
    format_clear_analysis_cache_done,
    format_task_start,
    format_unclassify_done,
)
from src.core.models import PERCENT_MULTIPLIER, SummaryReport
from src.core.workspace_paths import (
    count_cross_drive_moves,
    primary_root_dir,
    resolve_source_filepath,
)
from src.gui.constants import WORKER_CANCEL_WAIT_MS, WORKER_TERMINATE_WAIT_MS
from src.gui.worker import ClassifierWorker
from src.service.analysis_store import open_default_store
from src.service.analysis_timing import MODE_FAST, MODE_FULL, record_seconds_per_item
from src.service.analyzed_dirs import mark_analyzed_dir
from src.service.cache import DEFAULT_CACHE_REGISTRY
from src.service.session import ClassifierSession

CompletionFormatter = Callable[[SummaryReport | None, int, list[str]], str]

logger = logging.getLogger(__name__)


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
    def set_status_text(self, text: str) -> None:
        """하단 상태 표시줄 텍스트 갱신"""
        ...
    def apply_report(self, report: SummaryReport) -> None:
        """새 요약 리포트를 대시보드, 테이블 UI에 반영"""
        ...


def build_unclassify_confirm_message(
    dirnames: str, restorable: int, conflicts: int
) -> str:
    """작업 폴더로 복원 전 사용자 확인 다이얼로그 본문 문자열을 구성"""
    lines = [f"분류 폴더({dirnames})의 영상 {restorable:,}개를 작업 폴더로 복원하시겠습니까?"]
    if conflicts > 0:
        lines.append(
            f"작업 폴더에 같은 이름의 영상이 있는 {conflicts:,}개는 복원하지 않고 분류 폴더에 그대로 둡니다."
        )
    lines.append("복원 후 비어 있는 분류 폴더는 삭제됩니다.")
    return "\n".join(lines)


def build_classify_confirm_message(report: SummaryReport | None) -> str:
    """실제 폴더 분류 이동 전 사용자 확인 다이얼로그 본문 문자열을 구성"""
    details = ["인코딩 대상 영상들을 하위 폴더로 이동하시겠습니까?"]
    if report is not None:
        has_unmeasured_kf = report.unmeasured_keyframe_count > 0
        details.append("")
        details.append(
            f"• 현재 기준 이동 예정: {report.encoded_count:,}개 / 전체 {report.total_count:,}개"
        )
        details.append(f"• 예상 순 절약 용량: {report.net_saved_mb:,.1f} MB")
        cross_count = count_cross_drive_moves(
            primary_root_dir(report.root_dir),
            [
                resolve_source_filepath(report.root_dir, ev.video.filename)
                for ev in report.evaluations
                if ev.is_encode_target and ev.video.moved_dirname is None
            ],
        )
        if cross_count > 0:
            details.append(
                f"• 안내: 다른 드라이브의 영상 {cross_count:,}개는 주 작업 폴더의 분류 폴더로 이동(복사 후 원본 정리)됩니다."
            )
        if has_unmeasured_kf:
            details.append(
                "• 키프레임 미측정 영상이 포함되어 있어 이동 전 정밀 키프레임 분석이 함께 진행됩니다."
            )
        if report.hdr_preserved_count > 0:
            details.append(
                f"• 경고: HDR 영상 {report.hdr_preserved_count:,}개는 색 정보 보호를 위해 원본을 보존합니다."
            )
    details.append("• 언제든지 [작업 폴더로 복원] 버튼으로 되돌릴 수 있습니다.")
    return "\n".join(details)


def _format_analyze_status(
    report: SummaryReport | None, _moved_count: int, _warnings: list[str]
) -> str:
    return f"분석 완료: 총 {report.total_count if report else 0}개 영상"


def _format_classify_status(
    _report: SummaryReport | None, moved_count: int, _warnings: list[str]
) -> str:
    return f"분류 폴더로 이동 완료: {moved_count}개 파일 이동됨"


def _format_unclassify_status(
    _report: SummaryReport | None, moved_count: int, warnings: list[str]
) -> str:
    return format_unclassify_done(moved_count, len(warnings))


_STATUS_FORMATTERS: dict[str, CompletionFormatter] = {
    ClassifierWorker.TASK_ANALYZE_FAST: _format_analyze_status,
    ClassifierWorker.TASK_ANALYZE_FULL: _format_analyze_status,
    ClassifierWorker.TASK_CLASSIFY_REAL: _format_classify_status,
    ClassifierWorker.TASK_UNCLASSIFY_ALL: _format_unclassify_status,
}
_WARNING_TITLES: dict[str, str] = {
    ClassifierWorker.TASK_CLASSIFY_REAL: "분류 이동 충돌 안내",
    ClassifierWorker.TASK_UNCLASSIFY_ALL: "작업 폴더로 복원 충돌 안내",
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


def format_completion_status(
    task_type: str,
    report: SummaryReport | None,
    moved_count: int,
    warnings: list[str],
) -> str:
    """완료된 워커 작업 유형에 대응하는 하단 상태 표시 문자열을 생성"""
    formatter = _STATUS_FORMATTERS.get(task_type, _format_analyze_status)
    return formatter(report, moved_count, warnings)


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

    def clear_analysis_cache(self) -> None:
        """분석 캐시 DB 행을 확인 후 모두 삭제하고 인메모리 캐시도 초기화"""
        parent = self._host.as_widget()
        store = open_default_store()
        try:
            entry_count = store.count()
            if entry_count == 0:
                QMessageBox.information(parent, "분석 캐시 비우기", "비울 분석 캐시가 없습니다.")
                return
            answer = QMessageBox.question(
                parent,
                "분석 캐시 비우기 확인",
                format_clear_analysis_cache_confirm(entry_count),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            deleted = store.clear()
        finally:
            store.close()
        DEFAULT_CACHE_REGISTRY.invalidate()
        done_msg = format_clear_analysis_cache_done(deleted)
        logger.info("%s", done_msg)
        QMessageBox.information(parent, "분석 캐시 비우기", done_msg)

    def run_real_classify(self, confirm: bool = True) -> None:
        """실제 하위 폴더 파일 이동 분류 작업을 실행"""
        clf = self._host.ensure_classifier()
        if clf is None:
            return
        self._host.apply_filters_to_classifier(clf)
        if confirm:
            ans = QMessageBox.question(
                self._host.as_widget(),
                "분류 폴더로 이동 확인",
                build_classify_confirm_message(self._host.current_report),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        self.launch(clf, ClassifierWorker.TASK_CLASSIFY_REAL, label="분류 폴더로 이동")

    def run_unclassify(self, confirm: bool = True) -> None:
        """분류 폴더로 이동된 파일의 작업 폴더 복원 작업을 실행"""
        clf = self._host.ensure_classifier()
        if clf is None:
            return
        if confirm:
            restorable, conflicts = clf.preview_unclassify()
            if restorable == 0 and conflicts == 0:
                QMessageBox.information(
                    self._host.as_widget(),
                    "작업 폴더로 복원",
                    "분류 폴더에 복원할 영상이 없습니다.",
                )
                return
            # - 복원할 영상이 없으면 이동이 일어나지 않으므로 충돌 안내만 받도록 확인 생략
            if restorable > 0:
                dirnames = ", ".join(clf.service.class_dirnames())
                ans = QMessageBox.question(
                    self._host.as_widget(),
                    "작업 폴더로 복원 확인",
                    build_unclassify_confirm_message(dirnames, restorable, conflicts),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if ans != QMessageBox.StandardButton.Yes:
                    return
        self.launch(clf, ClassifierWorker.TASK_UNCLASSIFY_ALL, label="작업 폴더로 복원")

    def launch(
        self,
        clf: ClassifierSession,
        task_type: str,
        *,
        force_reanalyze: bool = False,
        label: str = "작업",
    ) -> None:
        """ClassifierWorker 인스턴스를 생성하고 시그널을 바인딩하여 비동기 시작"""
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
        )
        worker.progress_updated.connect(self.handle_progress)
        worker.task_completed.connect(self.handle_completed)
        analyzed_root = clf.root_dir
        analyzed_recursive = clf.recursive
        worker.task_completed.connect(
            lambda task_type, report, _moved, _warnings: self._record_analyzed(
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
        self,
        task_type: str,
        report: SummaryReport | None,
        moved_count: int,
        warnings: list[str],
    ) -> None:
        """워커 정상 완료 후 리포트, 충돌 안내, 완료 로그 UI 반영"""
        analyze_mode = _ANALYZE_MODE_BY_TASK.get(task_type)
        if analyze_mode is not None:
            record_seconds_per_item(
                analyze_mode, self._estimator.seconds_per_item(self._last_total)
            )
        self._host.set_progress(100)
        self._host.set_busy_state(False)
        if report is not None:
            self._host.apply_report(report)

        if warnings:
            QMessageBox.warning(
                self._host.as_widget(),
                _WARNING_TITLES.get(task_type, "작업 경고 안내"),
                "\n".join(warnings),
            )

        status_text = format_completion_status(task_type, report, moved_count, warnings)
        if analyze_mode is not None:
            elapsed = self._estimator.elapsed_sec()
            logger.info("%s (소요 %s)", status_text, format_duration_hms(elapsed))
        else:
            logger.info("%s", status_text)
        # 분석 완료 개수는 푸터 요약과 핵심 지표 카드가 이미 표시하므로 중복 문구 없이 상태 줄을 비움
        is_analyze_task = task_type in (
            ClassifierWorker.TASK_ANALYZE_FAST,
            ClassifierWorker.TASK_ANALYZE_FULL,
        )
        self._host.set_status_text("" if (is_analyze_task and not warnings) else status_text)

    def handle_cancelled(self, reason: str) -> None:
        """워커 중단 완료 시 진행률 초기화 및 취소 상태, 로그 반영"""
        self._host.set_progress(0)
        self._host.set_busy_state(False, MSG_WORK_CANCELLED)
        if self._last_total > 0:
            logger.info("%s", format_cancelled_progress(reason, self._last_current, self._last_total))
        else:
            logger.info("%s", reason)

    def handle_failed(self, error_msg: str) -> None:
        """워커 실패 시 오류 로그 기록 및 사용자 안내 대화상자 표시"""
        self._host.set_progress(0)
        summary = next((line.strip() for line in error_msg.splitlines() if line.strip()), error_msg)
        self._host.set_busy_state(False, f"오류 발생: {summary}")
        logger.error("%s", error_msg)
        QMessageBox.critical(
            self._host.as_widget(),
            "작업 오류",
            f"처리 중 오류가 발생했습니다.\n\n{error_msg}",
        )
