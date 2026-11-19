"""작업 폴더의 외부 파일 변경(탐색기 삭제, 이름 변경, 교체)을 백그라운드에서 점검해 표와 캐시를 맞추는 모듈

- 워커 스레드는 폴더 시그니처만 계산하고, 비교와 적용은 GUI 스레드에서 수행(Qt 객체 접근 금지)
- 점검은 한 번에 하나만 실행하며, 실행 중 요청은 끝난 뒤 한 번 더 실행
- 활성화 점검은 최소 간격을 두고, 없는 파일 안내 뒤 요청은 간격 없이 실행
"""

import logging
import threading
import time
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from src.core.analysis.file_manager import compute_dir_signature
from src.gui.constants import call_later
from src.gui.controllers.session_controller import SessionCoordinator
from src.gui.controllers.task_runner import TaskRunner
from src.gui.table.video_table_view import VideoTableView
from src.service.cache import DirSignature
from src.service.classifier_service import DirScanInputs

logger = logging.getLogger(__name__)

ACTION_PRUNE: str = "prune"
ACTION_ANALYZE_FAST: str = "analyze_fast"
ACTION_ANALYZE_FULL: str = "analyze_full"
CHECK_MIN_INTERVAL_SEC: float = 2.0
DRAG_SYNC_DELAY_MS: int = 150
SCAN_THREAD_NAME: str = "external-resync-scan"


def plan_external_resync(
    saved: DirSignature | None,
    current: DirSignature,
    *,
    include_keyframe: bool,
) -> str | None:
    """저장 시그니처와 현재 시그니처를 비교해 재동기화 방식을 반환

    - 저장 시그니처가 없거나 같으면 None
    - 삭제만 있으면 행을 빼는 prune. 행 목록과 캐시 시그니처만 바꾸므로 디스크 분석을 하지 않음
    - 추가나 변경이 있으면 기존 키프레임 포함 여부를 유지한 분석 재실행
    """
    if saved is None or saved == current:
        return None
    if set(current).difference(saved):
        return ACTION_ANALYZE_FULL if include_keyframe else ACTION_ANALYZE_FAST
    return ACTION_PRUNE


def attach_external_resync(
    table_view: VideoTableView,
    session: SessionCoordinator,
    tasks: TaskRunner,
    on_status: Callable[[str], None],
    on_reanalyze: Callable[[bool], None],
    on_reevaluate: Callable[[], None],
) -> "ExternalResyncCoordinator":
    """표의 드래그 완료와 없는 파일 안내 시그널을 점검 코디네이터와 하단 안내에 연결해 코디네이터를 만듦"""
    coordinator = ExternalResyncCoordinator(
        get_scan_inputs=session.external_scan_inputs,
        is_busy=tasks.is_running,
        on_reanalyze=on_reanalyze,
        on_prune=session.prune_external,
        on_reevaluate=on_reevaluate,
    )
    table_view.external_drag_finished.connect(coordinator.on_drag_finished)
    table_view.file_notice.connect(on_status)
    table_view.file_notice.connect(coordinator.on_missing_file)
    return coordinator


class ExternalResyncCoordinator(QObject):
    """외부 파일 변경 점검의 요청 순서, 간격, 취소와 결과 적용을 맡음"""

    _scan_finished = pyqtSignal(object, object)

    def __init__(
        self,
        *,
        get_scan_inputs: Callable[[], DirScanInputs | None],
        is_busy: Callable[[], bool],
        on_reanalyze: Callable[[bool], None],
        on_prune: Callable[[DirSignature], None],
        on_reevaluate: Callable[[], None],
    ) -> None:
        super().__init__()
        self._get_scan_inputs = get_scan_inputs
        self._is_busy = is_busy
        self._on_reanalyze = on_reanalyze
        self._on_prune = on_prune
        self._on_reevaluate = on_reevaluate
        self._running: bool = False
        self._pending: bool = False
        self._pending_immediate: bool = False
        self._last_started: float = float("-inf")
        self._cancel: threading.Event = threading.Event()
        self._scan_finished.connect(self._on_scan_finished)

    def request(self, *, immediate: bool) -> None:
        """점검을 요청하며, 실행 중이면 끝난 뒤 한 번 더 실행하도록 표시

        - immediate가 거짓이면 직전 점검 뒤 최소 간격 안의 요청은 무시(활성화 점검)
        """
        if self._running:
            self._pending = True
            self._pending_immediate = self._pending_immediate or immediate
            return
        now = time.monotonic()
        if not immediate and now - self._last_started < CHECK_MIN_INTERVAL_SEC:
            return
        inputs = self._get_scan_inputs()
        if inputs is None or self._is_busy():
            return
        self._running = True
        self._last_started = now
        threading.Thread(target=self._scan, args=(inputs, self._cancel), name=SCAN_THREAD_NAME, daemon=True).start()

    def reset(self) -> None:
        """작업 폴더 전환이나 목록 초기화 때 진행 중 점검을 취소하고 결과를 버림"""
        self._cancel.set()
        self._cancel = threading.Event()
        self._running = False
        self._pending = False
        self._pending_immediate = False

    def on_drag_finished(self, _paths: list[object]) -> None:
        """외부 드래그 완료 뒤 짧은 지연을 두고 변경 여부를 점검"""
        call_later(self, DRAG_SYNC_DELAY_MS, lambda: self.request(immediate=True))

    def on_missing_file(self, _text: str) -> None:
        """없는 파일 안내 뒤 작업 폴더를 간격 없이 다시 점검"""
        self.request(immediate=True)

    def _scan(self, inputs: DirScanInputs, cancel: threading.Event) -> None:
        """워커 스레드: 폴더 시그니처만 계산해 GUI 스레드로 전달"""
        try:
            current: DirSignature | None = compute_dir_signature(
                inputs.root_dir,
                recursive=inputs.recursive,
                excluded_dirnames=inputs.excluded_dirnames,
            )
        except OSError:
            logger.exception("외부 파일 변경 점검에 실패했습니다")
            current = None
        if not cancel.is_set():
            self._scan_finished.emit(inputs, current)

    def _on_scan_finished(self, inputs: DirScanInputs, current: DirSignature | None) -> None:
        """GUI 스레드: 점검 결과를 현재 작업 폴더와 대조해 재동기화 방식을 적용"""
        self._running = False
        active = self._get_scan_inputs()
        if current is not None and active is not None and active.root_dir == inputs.root_dir and not self._is_busy():
            action = plan_external_resync(
                inputs.saved_signature,
                current,
                include_keyframe=inputs.include_keyframe,
            )
            if action == ACTION_PRUNE:
                self._on_prune(current)
                self._on_reevaluate()
            elif action is not None:
                self._on_reanalyze(action == ACTION_ANALYZE_FULL)
        if self._pending:
            self._pending = False
            immediate, self._pending_immediate = self._pending_immediate, False
            self.request(immediate=immediate)
