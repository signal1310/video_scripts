"""데이터 정리 미리보기 로더 검증: 워커 순수 함수, 세대 번호로 이전 요청 무시, 취소, 스레드 보관

- 화면 없는 환경에서 대화상자가 뜨자마자 저장 데이터 건수를 읽도록 스레드를 분리한 모듈
- 스레드 시작은 대역으로 바꿔 실행 순서를 결정적으로 만들고, 워커 본체는 직접 호출
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.gui.dialogs import data_cleanup_preview_loader as loader_module
from src.gui.dialogs.data_cleanup_preview_loader import (
    PreviewLoader,
    _PreviewThread,
    storage_preview_snapshot,
)
from src.service.data_cleanup_service import (
    STORAGE_CLEANUP_ITEM_KEYS,
    CleanupItemKey,
    CleanupItemPreview,
    CleanupScope,
)

_APP = QApplication.instance() or QApplication([])
COUNT: int = 3
THREAD_WAIT_MS: int = 5_000


class _Recorder:
    """범위별 저장 데이터 미리보기 대역. 요청 범위를 기록하고 실패를 흉내 낼 수 있음"""

    def __init__(self) -> None:
        self.scopes: list[CleanupScope] = []
        self.fail: bool = False

    def preview(self, scope: CleanupScope) -> dict[CleanupItemKey, CleanupItemPreview]:
        """범위별 미리보기. 실패 설정이면 작업 폴더 없음과 같은 오류"""
        self.scopes.append(scope)
        if self.fail:
            raise ValueError("no root")
        return {key: CleanupItemPreview(key, COUNT, 0, True) for key in STORAGE_CLEANUP_ITEM_KEYS}


class StoragePreviewSnapshotTest(unittest.TestCase):
    """워커 스레드가 부르는 순수 함수"""

    def test_returns_previews_of_requested_scope(self) -> None:
        """요청한 범위로 미리보기를 만들고 저장 데이터 항목만 담는지 검증"""
        recorder = _Recorder()
        previews = storage_preview_snapshot(recorder.preview, "all")
        self.assertEqual(recorder.scopes, ["all"])
        assert previews is not None
        self.assertEqual(set(previews), set(STORAGE_CLEANUP_ITEM_KEYS))
        self.assertTrue(all(preview.count == COUNT for preview in previews.values()))

    def test_missing_root_returns_none_and_logs(self) -> None:
        """작업 폴더가 없어 실패하면 예외 대신 None과 기록을 남기는지 검증"""
        recorder = _Recorder()
        recorder.fail = True
        with self.assertLogs(loader_module.logger, level="INFO"):
            self.assertIsNone(storage_preview_snapshot(recorder.preview, "current"))


class PreviewThreadTest(unittest.TestCase):
    """워커 스레드 본체. 시작하지 않고 run을 직접 불러 결과 신호를 확인"""

    def test_run_emits_generation_and_snapshot(self) -> None:
        """run이 세대 번호와 미리보기를 함께 내보내는지 검증"""
        recorder = _Recorder()
        thread = _PreviewThread(7, "current", recorder.preview)
        received: list[tuple[int, object]] = []
        thread.loaded.connect(lambda generation, previews: received.append((generation, previews)))
        thread.run()
        self.assertEqual(received[0][0], 7)
        previews = received[0][1]
        assert isinstance(previews, dict)
        self.assertEqual(set(previews), set(STORAGE_CLEANUP_ITEM_KEYS))

    def test_run_emits_none_when_preview_fails(self) -> None:
        """미리보기가 실패하면 None을 내보내 화면이 기다리지 않게 하는지 검증"""
        recorder = _Recorder()
        recorder.fail = True
        thread = _PreviewThread(1, "all", recorder.preview)
        received: list[tuple[int, object]] = []
        thread.loaded.connect(lambda generation, previews: received.append((generation, previews)))
        with self.assertLogs(loader_module.logger, level="INFO"):
            thread.run()
        self.assertEqual(received, [(1, None)])


class PreviewLoaderTest(unittest.TestCase):
    """세대 번호, 취소, 실패 신호"""

    def setUp(self) -> None:
        self.recorder = _Recorder()
        self.pending: list[_PreviewThread] = []
        self._spawn_patcher = mock.patch.object(
            loader_module, "spawn_preview_thread", side_effect=self.pending.append
        )
        self._spawn_patcher.start()
        self.addCleanup(self._spawn_patcher.stop)
        self.loader = PreviewLoader(self.recorder.preview)

    def test_start_spawns_job_with_increasing_generation(self) -> None:
        """범위를 바꾸면 새 세대로 새 작업을 띄우는지 검증"""
        first = self.loader.start("current")
        second = self.loader.start("all")
        self.assertEqual((first, second), (1, 2))
        self.assertEqual(self.recorder.scopes, [])
        self.assertEqual(len(self.pending), 2)

    def test_ready_reports_only_latest_generation(self) -> None:
        """늦게 끝난 이전 범위 작업을 무시하는지 검증"""
        received: list[object] = []
        self.loader.ready.connect(received.append)
        self.loader.start("current")
        self.loader.start("all")
        self.pending[1].run()
        self.assertEqual(len(received), 1)
        self.pending[0].run()
        self.assertEqual(len(received), 1)
        self.assertEqual(self.recorder.scopes, ["all", "current"])

    def test_cancel_ignores_pending_job(self) -> None:
        """취소 뒤 도착한 결과를 버리는지 검증"""
        received: list[object] = []
        self.loader.ready.connect(received.append)
        self.loader.start("current")
        self.loader.cancel()
        self.pending[0].run()
        self.assertEqual(received, [])

    def test_worker_failure_emits_failed(self) -> None:
        """작업 폴더가 없어 실패하면 실패 신호로 알리는지 검증"""
        failures: list[bool] = []
        self.loader.failed.connect(lambda: failures.append(True))
        self.recorder.fail = True
        self.loader.start("current")
        with self.assertLogs(loader_module.logger, level="INFO"):
            self.pending[0].run()
        self.assertEqual(failures, [True])


class PreviewLoaderDefaultSpawnTest(unittest.TestCase):
    """실제 스레드를 띄우는 기본 경로"""

    def test_default_spawn_runs_thread_and_releases_it(self) -> None:
        """기본 스레드 시작 경로가 결과를 전하고 완료 뒤 보관 목록에서 빠지는지 검증"""
        recorder = _Recorder()
        loader = PreviewLoader(recorder.preview)
        received: list[object] = []
        loader.ready.connect(received.append)
        loader.start("current")
        QApplication.processEvents()
        for thread in list(loader_module._LIVE_THREADS):
            self.assertTrue(thread.wait(THREAD_WAIT_MS))
        QApplication.processEvents()
        self.assertEqual(len(received), 1)
        self.assertEqual(loader_module._LIVE_THREADS, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
