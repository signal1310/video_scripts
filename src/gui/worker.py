from typing import Any, List, Optional
from PyQt6.QtCore import QThread, pyqtSignal

from src.core.models import SummaryReport
from src.classifier import VideoClassifier


class ClassifierWorker(QThread):
    """
    - VideoClassifier 작업을 백그라운드 스레드에서 비동기로 실행하는 QThread 워커.
    """

    TASK_SCAN_FAST = "scan_fast"
    TASK_SCAN_FULL = "scan_full"
    TASK_CLASSIFY_PSEUDO = "classify_pseudo"
    TASK_CLASSIFY_REAL = "classify_real"
    TASK_UNCLASSIFY_PSEUDO = "unclassify_pseudo"
    TASK_UNCLASSIFY_ALL = "unclassify_all"

    progress_updated = pyqtSignal(int, int, str)
    log_emitted = pyqtSignal(str)
    task_completed = pyqtSignal(str, object, int, list)
    task_cancelled = pyqtSignal(str)
    task_failed = pyqtSignal(str)

    def __init__(
        self,
        classifier: VideoClassifier,
        task_type: str,
        force_rescan: bool = False,
        parent: Optional[Any] = None,
    ) -> None:
        super().__init__(parent)
        self._classifier = classifier
        self._task_type = task_type
        self._force_rescan = force_rescan
        self._cancelled: bool = False

    def cancel(self) -> None:
        """
        - 진행 중인 분석/분류 작업에 중단 플래그를 설정.
        """
        self._cancelled = True

    def _handle_progress(self, current: int, total: int, filename: str) -> None:
        if self._cancelled:
            raise InterruptedError("사용자에 의해 분석 작업이 중단됨.")
        self.progress_updated.emit(current, total, filename)

    def _handle_log(self, message: str) -> None:
        if self._cancelled:
            raise InterruptedError("사용자에 의해 분석 작업이 중단됨.")
        self.log_emitted.emit(message)

    def run(self) -> None:
        self._classifier.set_callbacks(
            on_progress=self._handle_progress,
            on_log=self._handle_log,
        )
        try:
            if self._force_rescan:
                self._classifier.invalidate_cache()

            moved_count: int = 0
            warnings: List[str] = []
            report: Optional[SummaryReport] = None

            if self._task_type == self.TASK_SCAN_FAST:
                self._classifier.include_keyframe_interval(False)
                report = self._classifier.get_summary_report(ensure_keyframe=False)

            elif self._task_type == self.TASK_SCAN_FULL:
                self._classifier.include_keyframe_interval(True)
                report = self._classifier.get_summary_report(ensure_keyframe=True)

            elif self._task_type == self.TASK_CLASSIFY_PSEUDO:
                self._classifier.pseudo_classify_mode(True)
                report = self._classifier.classify()

            elif self._task_type == self.TASK_CLASSIFY_REAL:
                self._classifier.pseudo_classify_mode(False)
                report = self._classifier.classify()

            elif self._task_type == self.TASK_UNCLASSIFY_PSEUDO:
                moved_count, warnings = self._classifier.unclassify_files(
                    unmark_pseudo_classified_only=True
                )
                self._classifier.pseudo_classify_mode(True)
                report = self._classifier.get_summary_report(
                    ensure_keyframe=self._classifier.service.include_keyframe
                )

            elif self._task_type == self.TASK_UNCLASSIFY_ALL:
                moved_count, warnings = self._classifier.unclassify_files(
                    unmark_pseudo_classified_only=False
                )
                self._classifier.pseudo_classify_mode(True)
                report = self._classifier.get_summary_report(
                    ensure_keyframe=self._classifier.service.include_keyframe
                )

            else:
                raise ValueError(f"알 수 없는 작업 유형: {self._task_type}")

            if self._cancelled:
                self.task_cancelled.emit("작업이 중단됨.")
                return

            self.task_completed.emit(self._task_type, report, moved_count, warnings)

        except InterruptedError as e:
            self.task_cancelled.emit(str(e))
        except Exception as e:
            self.task_failed.emit(str(e))
        finally:
            self._classifier.set_callbacks(on_progress=None, on_log=None)
