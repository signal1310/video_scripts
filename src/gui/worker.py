"""백그라운드 스캔 및 파일 분류 작업 실행용 QThread 워커 모듈"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from PyQt6.QtCore import QThread, pyqtSignal

from src.core.exceptions import DomainValueError, ErrorCode, format_user_error
from src.core.messages import MSG_ANALYSIS_CANCELLED
from src.core.models import SummaryReport

if TYPE_CHECKING:
    from src.service.session import ClassifierSession


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """백그라운드 워커 작업 1회의 실행 결과 페이로드"""

    report: SummaryReport | None = None
    moved_count: int = 0
    warnings: tuple[str, ...] = ()


TaskExecutor = Callable[["ClassifierSession"], TaskOutcome]


def _execute_analyze_fast(clf: ClassifierSession) -> TaskOutcome:
    clf.include_keyframe_interval(False)
    return TaskOutcome(report=clf.get_summary_report(ensure_keyframe=False))


def _execute_analyze_full(clf: ClassifierSession) -> TaskOutcome:
    clf.include_keyframe_interval(True, eager_prepare=False)
    return TaskOutcome(report=clf.get_summary_report(ensure_keyframe=True))


def _execute_classify_real(clf: ClassifierSession) -> TaskOutcome:
    report, moved_count, warnings = clf.classify_with_result(dry_run=False)
    return TaskOutcome(
        report=report, moved_count=moved_count, warnings=tuple(warnings)
    )


def _execute_unclassify_all(clf: ClassifierSession) -> TaskOutcome:
    moved_count, warnings = clf.unclassify_files()
    # - 이동 완료 후 재평가 중 취소가 끼어들어 결과 반영이 누락되지 않도록 콜백 해제
    clf.set_callbacks()
    report = clf.get_summary_report(ensure_keyframe=clf.include_keyframe)
    return TaskOutcome(
        report=report, moved_count=moved_count, warnings=tuple(warnings)
    )


class ClassifierWorker(QThread):
    """분류 세션 작업을 백그라운드 스레드에서 비동기로 실행하는 QThread 워커"""

    TASK_ANALYZE_FAST = "analyze_fast"
    TASK_ANALYZE_FULL = "analyze_full"
    TASK_CLASSIFY_REAL = "classify_real"
    TASK_UNCLASSIFY_ALL = "unclassify_all"

    _TASK_EXECUTORS: ClassVar[dict[str, TaskExecutor]] = {
        TASK_ANALYZE_FAST: _execute_analyze_fast,
        TASK_ANALYZE_FULL: _execute_analyze_full,
        TASK_CLASSIFY_REAL: _execute_classify_real,
        TASK_UNCLASSIFY_ALL: _execute_unclassify_all,
    }
    # - 디스크 이동이 끝난 작업은 완료 후 취소 요청이 와도 결과를 버리지 않고 반영
    _MOVE_TASKS: ClassVar[frozenset[str]] = frozenset(
        {TASK_CLASSIFY_REAL, TASK_UNCLASSIFY_ALL}
    )

    progress_updated = pyqtSignal(int, int, str)
    task_completed = pyqtSignal(str, object, int, list)
    task_cancelled = pyqtSignal(str)
    task_failed = pyqtSignal(str)

    def __init__(
        self,
        classifier: ClassifierSession,
        task_type: str,
        force_reanalyze: bool = False,
        parent: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self._classifier = classifier
        self._task_type = task_type
        self._force_reanalyze = force_reanalyze
        self._cancelled: bool = False

    def cancel(self) -> None:
        """진행 중인 분석, 분류 작업에 중단 플래그 설정"""
        self._cancelled = True

    def is_cancelled(self) -> bool:
        """현재 작업의 중단 요청 설정 여부 반환"""
        return self._cancelled

    def _handle_progress(self, current: int, total: int, filename: str) -> None:
        if self._cancelled:
            raise InterruptedError(MSG_ANALYSIS_CANCELLED)
        self.progress_updated.emit(current, total, filename)

    def run(self) -> None:
        """선택된 작업 유형에 따라 백그라운드 스캔 또는 파일 분류 실행"""
        self._classifier.set_callbacks(
            on_progress=self._handle_progress,
            on_is_cancelled=self.is_cancelled,
        )
        try:
            if self._force_reanalyze:
                self._classifier.invalidate_cache()

            executor = self._TASK_EXECUTORS.get(self._task_type)
            if executor is None:
                raise DomainValueError(
                    f"알 수 없는 작업 유형입니다: {self._task_type}",
                    code=ErrorCode.UNKNOWN_TASK_TYPE,
                )
            outcome = executor(self._classifier)

            if self._cancelled and self._task_type not in self._MOVE_TASKS:
                self.task_cancelled.emit(MSG_ANALYSIS_CANCELLED)
                return

            self.task_completed.emit(
                self._task_type,
                outcome.report,
                outcome.moved_count,
                list(outcome.warnings),
            )

        except InterruptedError as e:
            self.task_cancelled.emit(str(e))
        except Exception as e:
            logging.getLogger(__name__).debug("백그라운드 작업 예외 상세", exc_info=e)
            self.task_failed.emit(format_user_error(e))
        finally:
            self._classifier.set_callbacks(
                on_progress=None, on_is_cancelled=None
            )
