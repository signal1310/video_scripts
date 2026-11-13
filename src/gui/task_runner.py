"""백그라운드 워커 생성, 실행 수명주기 관리 및 작업 완료/오류 결과 반영 모듈"""

from typing import Protocol

from PyQt6.QtWidgets import QMessageBox, QWidget

from src.classifier import VideoClassifier
from src.core.messages import (
    LOG_TAG_CANCEL,
    LOG_TAG_DONE,
    LOG_TAG_ERROR,
    MSG_WORK_CANCELLED,
    format_log,
    format_unclassify_done,
)
from src.core.models import PERCENT_MULTIPLIER, SummaryReport
from src.gui.constants import WORKER_CANCEL_WAIT_MS, WORKER_TERMINATE_WAIT_MS
from src.gui.worker import ClassifierWorker


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
    def ensure_classifier(self) -> VideoClassifier | None:
        """현재 경로 기반 VideoClassifier 준비 및 반환"""
        ...
    def apply_filters_to_classifier(self, clf: VideoClassifier) -> None:
        """UI 필터/임계값 설정을 분류기 인스턴스에 반영"""
        ...
    def set_busy_state(self, busy: bool, status_msg: str = "") -> None:
        """작업 중 컨트롤 잠금 및 하단 상태 문구 전환"""
        ...
    def set_progress(self, value: int) -> None:
        """진행률 표시줄 값(0~100) 설정"""
        ...
    def set_status_text(self, text: str) -> None:
        """하단 상태 표시줄 텍스트 갱신"""
        ...
    def append_log(self, line: str) -> None:
        """로그 대화상자 이력에 한 줄 추가"""
        ...
    def apply_report(self, report: SummaryReport) -> None:
        """새 요약 리포트를 대시보드·테이블 UI에 반영"""
        ...


def build_classify_confirm_message(report: SummaryReport | None) -> str:
    """실제 폴더 분류 이동 전 사용자 확인 다이얼로그 본문 문자열을 구성"""
    details = ["인코딩 대상 영상들을 하위 폴더로 이동하시겠습니까?"]
    if report is not None:
        has_unmeasured_kf = report.unmeasured_keyframe_count > 0
        details.append("")
        details.append(
            f"• 현재 기준 이동 예정: {report.encoded_count:,}개 / 전체 {report.total_count:,}개"
        )
        details.append(f"• 예상 순수 절감 용량: {report.net_saved_mb:,.1f} MB")
        if has_unmeasured_kf:
            details.append(
                "• 키프레임 미측정 영상이 포함되어 있어 이동 전 정밀 키프레임 분석이 함께 진행됩니다."
            )
    details.append("• 언제든지 [분류 복원] 버튼으로 되돌릴 수 있습니다.")
    return "\n".join(details)


def format_completion_status(
    task_type: str,
    report: SummaryReport | None,
    moved_count: int,
    warnings: list[str],
) -> str:
    """완료된 워커 작업 유형에 대응하는 하단 상태 표시 문자열을 생성"""
    if task_type == ClassifierWorker.TASK_CLASSIFY_REAL:
        return f"폴더 분류 완료: {report.encoded_count if report else 0}개 파일 이동됨"
    if task_type == ClassifierWorker.TASK_UNCLASSIFY_ALL:
        return format_unclassify_done(moved_count, len(warnings))
    return f"분석 완료: 총 {report.total_count if report else 0}개 영상"


class TaskRunner:
    """ClassifierWorker 백그라운드 작업 실행, 취소, 종료 대기 및 완료/실패 시그널 처리를 전담"""

    def __init__(self, host: WorkerHost) -> None:
        self._host = host

    def is_running(self) -> bool:
        """현재 백그라운드 워커가 실행 중인지 반환"""
        worker = self._host.active_worker
        return worker is not None and worker.isRunning()

    def start_scan(
        self, include_keyframe: bool = True, force_rescan: bool = False
    ) -> None:
        """빠른 스캔 또는 키프레임 정밀 분석 워커를 실행"""
        clf = self._host.ensure_classifier()
        if clf is None:
            return
        self._host.apply_filters_to_classifier(clf)
        task = (
            ClassifierWorker.TASK_SCAN_FULL
            if include_keyframe
            else ClassifierWorker.TASK_SCAN_FAST
        )
        label = "정밀 분석" if include_keyframe else "빠른 스캔"
        self.launch(clf, task, force_rescan=force_rescan, label=label)

    def run_real_classify(self, confirm: bool = True) -> None:
        """실제 하위 폴더 파일 이동 분류 작업을 실행"""
        clf = self._host.ensure_classifier()
        if clf is None:
            return
        self._host.apply_filters_to_classifier(clf)
        if confirm:
            ans = QMessageBox.question(
                self._host.as_widget(),
                "폴더 분류 이동 확인",
                build_classify_confirm_message(self._host.current_report),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        self.launch(clf, ClassifierWorker.TASK_CLASSIFY_REAL, label="폴더 분류 이동")

    def run_unclassify(self) -> None:
        """하위 폴더로 분류된 파일의 작업 폴더 복원 작업을 실행"""
        clf = self._host.ensure_classifier()
        if clf is None:
            return
        self.launch(clf, ClassifierWorker.TASK_UNCLASSIFY_ALL, label="분류 복원")

    def launch(
        self,
        clf: VideoClassifier,
        task_type: str,
        *,
        force_rescan: bool = False,
        label: str = "작업",
    ) -> None:
        """ClassifierWorker 인스턴스를 생성하고 시그널을 바인딩하여 비동기 시작"""
        if self.is_running():
            return
        self._host.set_progress(0)
        self._host.set_busy_state(True, f"{label} 진행 중...")
        worker = ClassifierWorker(
            classifier=clf,
            task_type=task_type,
            force_rescan=force_rescan,
            parent=self._host.as_widget(),
        )
        worker.progress_updated.connect(self.handle_progress)
        worker.task_completed.connect(self.handle_completed)
        worker.task_cancelled.connect(self.handle_cancelled)
        worker.task_failed.connect(self.handle_failed)
        self._host.active_worker = worker
        worker.start()

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
            if (
                hasattr(worker, "wait")
                and not worker.wait(WORKER_CANCEL_WAIT_MS)
            ):
                worker.terminate()
                worker.wait(WORKER_TERMINATE_WAIT_MS)

    def handle_progress(self, current: int, total: int, filename: str) -> None:
        """진행률 표시줄 및 하단 상태 문구를 현재 파일 기준으로 갱신"""
        pct = round((current / max(total, 1)) * PERCENT_MULTIPLIER)
        self._host.set_progress(pct)
        self._host.set_status_text(f"[{current}/{total}] {filename}")

    def handle_completed(
        self,
        task_type: str,
        report: SummaryReport | None,
        moved_count: int,
        warnings: list[str],
    ) -> None:
        """워커 정상 완료 후 리포트·충돌 안내·완료 로그 UI 반영"""
        self._host.set_progress(100)
        self._host.set_busy_state(False)
        if report is not None:
            self._host.apply_report(report)

        if task_type == ClassifierWorker.TASK_UNCLASSIFY_ALL and warnings:
            QMessageBox.warning(
                self._host.as_widget(), "분류 복원 충돌 안내", "\n".join(warnings)
            )

        status_text = format_completion_status(
            task_type, report, moved_count, warnings
        )
        self._host.set_status_text(status_text)
        self._host.append_log(format_log(LOG_TAG_DONE, status_text))

    def handle_cancelled(self, reason: str) -> None:
        """워커 중단 완료 시 진행률 초기화 및 취소 상태·로그 반영"""
        self._host.set_progress(0)
        self._host.set_busy_state(False, MSG_WORK_CANCELLED)
        self._host.append_log(format_log(LOG_TAG_CANCEL, reason))

    def handle_failed(self, error_msg: str) -> None:
        """워커 실패 시 오류 로그 기록 및 사용자 안내 대화상자 표시"""
        self._host.set_progress(0)
        summary = next((line.strip() for line in error_msg.splitlines() if line.strip()), error_msg)
        self._host.set_busy_state(False, f"오류 발생: {summary}")
        self._host.append_log(format_log(LOG_TAG_ERROR, error_msg))
        QMessageBox.critical(
            self._host.as_widget(),
            "작업 오류",
            f"처리 중 오류가 발생했습니다.\n\n{error_msg}",
        )
