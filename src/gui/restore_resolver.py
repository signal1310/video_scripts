"""시작 시 이전 분석 결과를 백그라운드 스레드에서 읽어 첫 화면 표시를 막지 않는 모듈

- 워커 스레드는 Qt 객체에 접근하지 않고, 진행 수와 결과를 시그널로만 넘김
- 취소 요청은 이벤트로 받아 묶음 경계에서 멈추고 결과 없음으로 전달
"""

import logging
import threading
import time
from collections.abc import Collection

from PyQt6.QtCore import QObject, pyqtSignal

from src.service.analysis_probe import StoredRows, load_stored_rows
from src.service.analysis_store import open_default_store

_logger = logging.getLogger(__name__)


class RestoreModeResolver(QObject):
    """작업 폴더 파일 목록과 분석 DB를 워커 스레드에서 읽어 표시용 결과를 GUI 스레드로 전달

    - resolved는 복원할 행이 없거나 취소되었으면 None
    - progress는 확인한 파일 수
    """

    resolved = pyqtSignal(object)
    progress = pyqtSignal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cancel: threading.Event = threading.Event()

    def start(
        self,
        root_dir: str,
        *,
        recursive: bool,
        excluded_dirnames: Collection[str] = (),
    ) -> None:
        """호출 시점의 설정 값으로 판정을 백그라운드에서 시작"""
        self._cancel = threading.Event()
        threading.Thread(
            target=self._run,
            args=(root_dir, recursive, tuple(excluded_dirnames), self._cancel),
            name="restore-resolver",
            daemon=True,
        ).start()

    def cancel(self) -> None:
        """진행 중인 판정을 취소(다음 묶음 경계에서 멈춤)"""
        self._cancel.set()

    def _run(
        self,
        root_dir: str,
        recursive: bool,
        excluded_dirnames: tuple[str, ...],
        cancel: threading.Event,
    ) -> None:
        started = time.monotonic()
        _logger.info("이전 분석 결과 판정 시작")
        result: StoredRows | None
        try:
            store = open_default_store()
            try:
                result = load_stored_rows(
                    store,
                    root_dir,
                    recursive=recursive,
                    excluded_dirnames=excluded_dirnames,
                    on_progress=self.progress.emit,
                    is_cancelled=cancel.is_set,
                )
            finally:
                store.close()
        except Exception:
            _logger.exception("이전 분석 결과 복원 판정에 실패했습니다")
            result = None
        if cancel.is_set():
            _logger.info("이전 분석 결과 복원 판정을 취소했습니다")
            result = None
        else:
            _logger.info("이전 분석 결과 복원 판정 완료(소요 %.1f초)", time.monotonic() - started)
        self.resolved.emit(result if result is not None and result.rows else None)
