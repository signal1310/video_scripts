"""백그라운드 스캔 및 평가 작업 실행용 QThread 워커 모듈"""

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
    from src.service.analysis_probe import StoredRows
    from src.service.session import ClassifierSession


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """백그라운드 워커 작업 1회의 실행 결과 페이로드"""

    report: SummaryReport | None = None


TaskExecutor = Callable[["ClassifierSession", "StoredRows | None"], TaskOutcome]


def _execute_analyze_fast(
    clf: ClassifierSession, prescan: StoredRows | None
) -> TaskOutcome:
    clf.include_keyframe_interval(False)
    return TaskOutcome(report=clf.get_summary_report(ensure_keyframe=False, prescan=prescan))


def _execute_analyze_full(
    clf: ClassifierSession, prescan: StoredRows | None
) -> TaskOutcome:
    clf.include_keyframe_interval(True, eager_prepare=False)
    return TaskOutcome(report=clf.get_summary_report(ensure_keyframe=True, prescan=prescan))


class ClassifierWorker(QThread):
    """분석 세션 작업을 백그라운드 스레드에서 비동기로 실행하는 QThread 워커"""

    TASK_ANALYZE_FAST = "analyze_fast"
    TASK_ANALYZE_FULL = "analyze_full"

    _TASK_EXECUTORS: ClassVar[dict[str, TaskExecutor]] = {
        TASK_ANALYZE_FAST: _execute_analyze_fast,
        TASK_ANALYZE_FULL: _execute_analyze_full,
    }

    progress_updated = pyqtSignal(int, int, str)
    task_completed = pyqtSignal(str, object)
    task_cancelled = pyqtSignal(str)
    task_failed = pyqtSignal(str)

    def __init__(
        self,
        classifier: ClassifierSession,
        task_type: str,
        force_reanalyze: bool = False,
        parent: Any | None = None,
        prescan: StoredRows | None = None,
    ) -> None:
        super().__init__(parent)
        self._classifier = classifier
        self._task_type = task_type
        self._force_reanalyze = force_reanalyze
        self._prescan = prescan
        self._cancelled: bool = False

    def cancel(self) -> None:
        """진행 중인 분석 작업에 중단 플래그 설정"""
        self._cancelled = True

    def is_cancelled(self) -> bool:
        """현재 작업의 중단 요청 설정 여부 반환"""
        return self._cancelled

    def _handle_progress(self, current: int, total: int, filename: str) -> None:
        if self._cancelled:
            raise InterruptedError(MSG_ANALYSIS_CANCELLED)
        self.progress_updated.emit(current, total, filename)

    def run(self) -> None:
        """선택된 작업 유형에 따라 백그라운드 스캔과 평가 실행"""
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
            outcome = executor(self._classifier, self._prescan)

            if self._cancelled:
                self.task_cancelled.emit(MSG_ANALYSIS_CANCELLED)
                return

            self.task_completed.emit(self._task_type, outcome.report)

        except InterruptedError as e:
            self.task_cancelled.emit(str(e))
        except Exception as e:
            logging.getLogger(__name__).debug("백그라운드 작업 예외 상세", exc_info=e)
            self.task_failed.emit(format_user_error(e))
        finally:
            self._classifier.set_callbacks(
                on_progress=None, on_is_cancelled=None
            )
