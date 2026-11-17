"""시작 시 이전 분석 결과 복원 모드를 백그라운드 스레드에서 판정해 첫 화면 표시를 막지 않는 모듈"""

import logging
import threading
from collections.abc import Collection

from PyQt6.QtCore import QObject, pyqtSignal

from src.service.analysis_probe import resolve_restore_keyframe_mode
from src.service.analysis_store import open_default_store

_logger = logging.getLogger(__name__)


class RestoreModeResolver(QObject):
    """작업 폴더 디스크 스캔과 분석 DB 조회를 워커 스레드에서 수행하고 판정 결과를 GUI 스레드로 전달

    - 워커 스레드는 Qt 객체에 접근하지 않고 결과를 시그널로만 넘김
    - 판정 불가 또는 실패는 None으로 전달해 복원을 생략
    """

    resolved = pyqtSignal(object)

    def start(
        self,
        root_dir: str,
        *,
        recursive: bool,
        excluded_dirnames: Collection[str],
        downscale_prefix: str,
    ) -> None:
        """호출 시점의 설정 값으로 판정을 백그라운드에서 시작"""
        threading.Thread(
            target=self._run,
            args=(root_dir, recursive, tuple(excluded_dirnames), downscale_prefix),
            daemon=True,
        ).start()

    def _run(
        self,
        root_dir: str,
        recursive: bool,
        excluded_dirnames: tuple[str, ...],
        downscale_prefix: str,
    ) -> None:
        mode: bool | None
        try:
            store = open_default_store()
            try:
                mode = resolve_restore_keyframe_mode(
                    store,
                    root_dir,
                    recursive=recursive,
                    excluded_dirnames=excluded_dirnames,
                    downscale_prefix=downscale_prefix,
                )
            finally:
                store.close()
        except Exception:
            _logger.exception("이전 분석 결과 복원 판정에 실패했습니다")
            mode = None
        self.resolved.emit(mode)
