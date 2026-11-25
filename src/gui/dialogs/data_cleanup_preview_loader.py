"""데이터 정리 창의 저장 데이터 미리보기를 화면 스레드 밖에서 읽는 모듈

- 임시 파일 탐색과 SQLite 건수 조회는 오래 걸리므로 창을 먼저 띄우고 나중에 채움
- 워커는 Qt 위젯을 만지지 않고 주입한 미리보기 함수만 부름(화면과 설정 항목 검사는 화면 스레드 담당)
- 범위를 바꾸면 세대 번호가 달라져 늦게 도착한 이전 결과를 버림. 창을 닫으면 기다리지 않고 결과만 버림
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from typing import cast

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from src.service.data_cleanup_service import (
    CleanupItemKey,
    CleanupItemPreview,
    CleanupScope,
    StoragePreviewFn,
)

logger = logging.getLogger(__name__)

# - 창이 먼저 닫혀도 실행 중 스레드가 수거되지 않도록 모듈에서 참조를 붙들고, 끝나면 놓음
_LIVE_THREADS: set[_PreviewThread] = set()


def storage_preview_snapshot(
    preview_fn: StoragePreviewFn, scope: CleanupScope
) -> dict[CleanupItemKey, CleanupItemPreview] | None:
    """범위의 저장 데이터 미리보기. 작업 폴더가 없거나 저장소를 읽지 못하면 None

    - 워커 스레드에서만 부르므로 Qt 위젯을 만지지 않음
    """
    try:
        return dict(preview_fn(scope))
    except (ValueError, OSError, sqlite3.Error) as error:
        logger.info("저장 데이터 미리보기를 읽지 못했습니다: %s (%s)", scope, error)
        return None


class _PreviewThread(QThread):
    """저장 데이터 미리보기를 한 번 읽어 세대 번호와 함께 내보내는 스레드"""

    loaded = pyqtSignal(int, object)

    def __init__(self, generation: int, scope: CleanupScope, preview_fn: StoragePreviewFn) -> None:
        super().__init__()
        self._generation: int = generation
        self._scope: CleanupScope = scope
        self._preview_fn: StoragePreviewFn = preview_fn

    def run(self) -> None:
        """미리보기를 읽어 결과를 내보냄. 실패는 None으로 알림"""
        self.loaded.emit(self._generation, storage_preview_snapshot(self._preview_fn, self._scope))


def spawn_preview_thread(thread: _PreviewThread) -> None:
    """스레드를 시작하고 끝날 때까지 모듈에서 참조를 유지"""
    _LIVE_THREADS.add(thread)
    thread.finished.connect(lambda: _LIVE_THREADS.discard(thread))
    thread.start()


class PreviewLoader(QObject):
    """범위별 저장 데이터 미리보기를 백그라운드에서 읽어 최신 결과만 전달"""

    ready = pyqtSignal(object)
    failed = pyqtSignal()

    def __init__(
        self,
        preview_fn: StoragePreviewFn,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._preview_fn: StoragePreviewFn = preview_fn
        self._generation: int = 0

    def start(self, scope: CleanupScope) -> int:
        """범위의 미리보기를 새 세대로 읽기 시작하고 세대 번호를 반환"""
        self._generation += 1
        generation = self._generation
        thread = _PreviewThread(generation, scope, self._preview_fn)
        thread.loaded.connect(self._on_loaded)
        spawn_preview_thread(thread)
        return generation

    def cancel(self) -> None:
        """진행 중 작업의 결과를 버림. 스레드를 기다리지 않으므로 창 닫기가 늦어지지 않음"""
        self._generation += 1

    def _on_loaded(self, generation: int, previews: object) -> None:
        """최신 세대의 결과만 전달하고, 읽지 못했으면 실패로 알림"""
        if generation != self._generation:
            return
        if previews is None:
            self.failed.emit()
            return
        self.ready.emit(cast("Mapping[CleanupItemKey, CleanupItemPreview]", previews))
