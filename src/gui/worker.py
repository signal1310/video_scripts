"""백그라운드 스캔 및 파일 분류 작업 실행용 QThread 워커 모듈"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QThread, pyqtSignal

from src.core.exceptions import DomainValueError, ErrorCode, format_user_error
from src.core.messages import MSG_ANALYSIS_CANCELLED
from src.core.models import SummaryReport

if TYPE_CHECKING:
    from src.classifier import VideoClassifier


class ClassifierWorker(QThread):
    """VideoClassifier 작업을 백그라운드 스레드에서 비동기로 실행하는 QThread 워커"""

    TASK_SCAN_FAST = "scan_fast"
    TASK_SCAN_FULL = "scan_full"
    TASK_CLASSIFY_REAL = "classify_real"
    TASK_UNCLASSIFY_ALL = "unclassify_all"

    progress_updated = pyqtSignal(int, int, str)
    task_completed = pyqtSignal(str, object, int, list)
    task_cancelled = pyqtSignal(str)
    task_failed = pyqtSignal(str)

    def __init__(
        self,
        classifier: VideoClassifier,
        task_type: str,
        force_rescan: bool = False,
        parent: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self._classifier = classifier
        self._task_type = task_type
        self._force_rescan = force_rescan
        self._cancelled: bool = False

    def cancel(self) -> None:
        """진행 중인 분석·분류 작업에 중단 플래그 설정"""
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
            if self._force_rescan:
                self._classifier.invalidate_cache()

            moved_count: int = 0
            warnings: list[str] = []
            report: SummaryReport | None = None

            if self._task_type == self.TASK_SCAN_FAST:
                self._classifier.include_keyframe_interval(False)
                report = self._classifier.get_summary_report(ensure_keyframe=False)

            elif self._task_type == self.TASK_SCAN_FULL:
                self._classifier.include_keyframe_interval(True)
                report = self._classifier.get_summary_report(ensure_keyframe=True)

            elif self._task_type == self.TASK_CLASSIFY_REAL:
                self._classifier.pseudo_classify_mode(False)
                try:
                    report = self._classifier.classify()
                finally:
                    self._classifier.pseudo_classify_mode(True)

            elif self._task_type == self.TASK_UNCLASSIFY_ALL:
                moved_count, warnings = self._classifier.unclassify_files()
                self._classifier.pseudo_classify_mode(True)
                report = self._classifier.get_summary_report(
                    ensure_keyframe=self._classifier.include_keyframe
                )

            else:
                raise DomainValueError(
                    f"알 수 없는 작업 유형입니다: {self._task_type}",
                    code=ErrorCode.UNKNOWN_TASK_TYPE,
                )

            if self._cancelled:
                self.task_cancelled.emit(MSG_ANALYSIS_CANCELLED)
                return

            self.task_completed.emit(self._task_type, report, moved_count, warnings)

        except InterruptedError as e:
            self.task_cancelled.emit(str(e))
        except Exception as e:
            logging.getLogger(__name__).debug("백그라운드 작업 예외 상세", exc_info=e)
            self.task_failed.emit(format_user_error(e))
        finally:
            self._classifier.set_callbacks(
                on_progress=None, on_is_cancelled=None
            )
