"""시작 시 이전 분석 결과 복원의 판정 시작, 진행 안내, 결과 적용을 MainWindow에서 분리한 모듈

- 이전 결과가 없는 폴더는 아무 작업도 하지 않음. 분석을 처음부터 몰래 돌리지 않기 위함
- 이전 형식 행이 있으면 저장 결과만 표시하고, 다시 분석은 사용자가 시작함
"""

from collections.abc import Callable

from PyQt6.QtCore import QObject

from src.core.messages import format_restore_progress
from src.gui.restore_resolver import RestoreModeResolver
from src.gui.session_controller import SessionCoordinator
from src.gui.task_runner import TaskRunner
from src.gui.worker import ClassifierWorker
from src.service.analysis_probe import StoredRows
from src.service.analyzed_dirs import is_analyzed_dir
from src.service.session import ClassifierSession

RESTORE_PENDING_STATUS: str = "이전 분석 결과 복원 진행 중..."
RESTORE_LABEL: str = "이전 분석 결과 복원"


class RestoreFlow:
    """판정 워커를 소유하고, 결과에 따라 표시용 결과 적용 또는 복원 분석 실행을 결정"""

    def __init__(
        self,
        session: SessionCoordinator,
        tasks: TaskRunner,
        parent: QObject,
        set_busy: Callable[[bool, str], None],
        set_status: Callable[[str], None],
        set_pending: Callable[[], None],
        apply_filters: Callable[[ClassifierSession], None],
        re_evaluate: Callable[[], None],
    ) -> None:
        self._session = session
        self._tasks = tasks
        self._set_busy = set_busy
        self._set_status = set_status
        self._set_pending = set_pending
        self._apply_filters = apply_filters
        self._re_evaluate = re_evaluate
        self._resolver = RestoreModeResolver(parent)
        self._resolver.resolved.connect(self.apply_resolved)
        self._resolver.progress.connect(lambda count: self._set_status(format_restore_progress(count)))

    def start(self) -> None:
        """작업 폴더에 이전 분석 결과가 남아 있으면 판정을 백그라운드에서 시작"""
        clf = self._session.classifier
        # - 한 번도 분석되지 않은 폴더는 같은 이름, 크기, 수정 시각의 파일이 다른 폴더 기록과 겹쳐도 자동 분석하지 않음
        if clf is None or self._tasks.is_running() or not is_analyzed_dir(clf.root_dir, recursive=clf.recursive):
            return
        # - 판정 대기 동안에도 진행 표시가 보이도록 먼저 바쁨 상태로 전환, 판정 결과가 복원 없음이면 해제
        self._set_busy(True, RESTORE_PENDING_STATUS)
        self._set_pending()
        self._resolver.start(clf.root_dir, recursive=clf.recursive)

    def cancel(self) -> None:
        """진행 중인 판정 취소"""
        self._resolver.cancel()

    def apply_resolved(self, stored: object) -> None:
        """판정 결과를 적용: 이전 형식이 있으면 저장 결과 표시, 없으면 기존 방식으로 복원 분석"""
        # - 판정 대기 중 사용자가 시작한 작업이 있으면 그 작업이 바쁨 상태를 관리하므로 건드리지 않음
        if self._tasks.is_running():
            return
        clf = self._session.classifier
        if not isinstance(stored, StoredRows) or clf is None:
            self._set_status("")
            self._set_busy(False, "")
            return
        if stored.legacy_names:
            # - 저장 결과만 보여 주고 다시 분석은 사용자가 시작(몰래 대량 재분석하지 않음)
            self._set_busy(False, self._session.install_stored_rows(stored))
            self._re_evaluate()
            return
        self._apply_filters(clf)
        task = ClassifierWorker.TASK_ANALYZE_FULL if stored.include_keyframe else ClassifierWorker.TASK_ANALYZE_FAST
        self._tasks.launch(clf, task, label=RESTORE_LABEL)
