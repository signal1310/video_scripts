"""화질 분석 캐시 적재 함수와 워커 스레드, 늦은 결과 버리기를 검증"""

import os
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from dataclasses import replace
from typing import ClassVar
from unittest import mock

from PyQt6.QtWidgets import QApplication

from src.core.analysis.file_fingerprint import compute_fingerprint
from src.core.config import ClassifierConfig
from src.gui.controllers.frame_cache_loader import (
    FrameCacheLoader,
    FrameCacheLoadWorker,
    FrameCacheSnapshot,
    load_crop_exclusion_names,
    load_manual_frame_names,
    load_manual_vr_names,
    names_for_paths,
    run_frame_cache_load,
    source_paths,
)
from src.service.frame_analysis_record import FrameAnalysisRecord
from src.service.frame_analysis_store import FrameAnalysisStore
from tests._factories import make_eval, make_report, make_video
from tests._frame_analysis_fakes import make_frame_record

_APP = QApplication.instance() or QApplication([])
_WAIT_SEC: float = 5.0


class _Signal:
    """connect한 슬롯을 기록하고 emit으로 직접 부르는 신호 대역"""

    def __init__(self) -> None:
        self.slots: list[Callable[..., None]] = []

    def connect(self, slot: Callable[..., None]) -> None:
        self.slots.append(slot)

    def emit(self, *args: object) -> None:
        for slot in list(self.slots):
            slot(*args)


class _LoaderCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.path = os.path.join(self.root, "a.mp4")
        with open(self.path, "wb") as handle:
            handle.write(b"video")
        self.ev = make_eval(video=make_video(filename="a.mp4"))
        self.report = make_report([self.ev], root_dir=self.root)
        self.store = FrameAnalysisStore(os.path.join(self.root, "video_cache.sqlite3"))
        self.addCleanup(self.store.close)

    def record(self, **overrides: object) -> FrameAnalysisRecord:
        """지금 a.mp4 파일의 식별 값에 맞춘 캐시 행, 지정 필드만 덮어씀"""
        stat = os.stat(self.path)
        record = make_frame_record(
            "a.mp4",
            size=stat.st_size,
            mtime=stat.st_mtime_ns,
            fingerprint=compute_fingerprint(self.path, stat.st_size),
        )
        return record if not overrides else replace(record, **overrides)


class CacheLoadFunctionTests(_LoaderCase):
    """적재 함수가 파일 지문, 캐시 결과, 영상별 지정을 한 번에 읽는지 검증"""

    def test_load_reads_fingerprint_results_and_designations(self) -> None:
        self.store.save(self.record(dark_outliers_ignored=True))
        snapshot = run_frame_cache_load(
            self.store, [self.ev], self.root, ClassifierConfig(), threading.Event()
        )
        assert snapshot is not None
        self.assertEqual(snapshot.root_dir, self.root)
        self.assertEqual([request.filename for request in snapshot.requests], ["a.mp4"])
        # - 지문을 여기서 채워 두어야 실행 시작이 파일을 다시 읽지 않음
        self.assertEqual(
            snapshot.requests[0].fingerprint,
            compute_fingerprint(self.path, os.stat(self.path).st_size),
        )
        self.assertEqual(set(snapshot.results), {"a.mp4"})
        self.assertEqual(snapshot.review_flagged, frozenset({"a.mp4"}))

    def test_load_returns_none_when_cancelled(self) -> None:
        cancel = threading.Event()
        cancel.set()
        self.assertIsNone(
            run_frame_cache_load(self.store, [self.ev], self.root, ClassifierConfig(), cancel)
        )

    def test_load_stops_reading_files_when_cancelled_midway(self) -> None:
        """적재 도중 취소되면 남은 파일을 읽지 않고 결과 없이 끝남"""
        second = make_eval(video=make_video(filename="b.mp4"))
        with open(os.path.join(self.root, "b.mp4"), "wb") as handle:
            handle.write(b"video")
        cancel = threading.Event()

        def stop_after_first(*_args: object) -> str:
            cancel.set()
            return "fingerprint"

        with mock.patch(
            "src.gui.controllers.frame_analysis_targets.compute_fingerprint",
            side_effect=stop_after_first,
        ):
            snapshot = run_frame_cache_load(
                self.store, [self.ev, second], self.root, ClassifierConfig(), cancel
            )
        self.assertIsNone(snapshot)

    def test_load_skips_missing_files(self) -> None:
        missing = make_eval(video=make_video(filename="missing.mp4"))
        snapshot = run_frame_cache_load(
            self.store, [missing], self.root, ClassifierConfig(), threading.Event()
        )
        assert snapshot is not None
        self.assertEqual(snapshot.requests, [])
        self.assertEqual(snapshot.results, {})
        self.assertEqual(snapshot.manual_vr, frozenset())
        self.assertEqual(snapshot.crop_excluded, frozenset())
        self.assertEqual(snapshot.manual_frames, {})
        self.assertEqual(snapshot.review_flagged, frozenset())

    def test_designation_helpers_read_the_store(self) -> None:
        self.store.set_vr_override(source_path=self.path, filename="a.mp4", manual_vr=True)
        self.store.set_crop_exclusion(self.path, True)
        self.assertEqual(source_paths([self.ev], self.root), [self.path])
        self.assertEqual(
            load_manual_vr_names(self.store, [self.ev], self.root), frozenset({"a.mp4"})
        )
        self.assertEqual(
            load_crop_exclusion_names(self.store, [self.ev], self.root), frozenset({"a.mp4"})
        )
        self.assertEqual(load_manual_frame_names(self.store, [self.ev], self.root), {})
        self.assertEqual(
            names_for_paths([self.ev], self.root, frozenset({self.path})), frozenset({"a.mp4"})
        )
        self.assertEqual(names_for_paths([self.ev], self.root, frozenset()), frozenset())


class _ManualWorker:
    """시작만 기록하고 결과는 테스트가 직접 보내는 워커 대역"""

    instances: ClassVar[list["_ManualWorker"]] = []

    def __init__(
        self,
        store: FrameAnalysisStore,
        evaluations: object,
        root_dir: str,
        config: ClassifierConfig,
        token: int,
    ) -> None:
        self.completed = _Signal()
        self.root_dir = root_dir
        self.token = token
        self.cancelled = 0
        self.waited = 0
        self.finished = False
        _ManualWorker.instances.append(self)

    def start(self) -> None:
        return

    def isFinished(self) -> bool:
        return self.finished

    def wait(self) -> None:
        self.waited += 1
        self.finished = True

    def cancel(self) -> None:
        self.cancelled += 1
        self.finished = True


class FrameCacheLoaderTests(_LoaderCase):
    """적재 결과 적용, 늦은 결과 버리기, 종료 대기를 검증"""

    def setUp(self) -> None:
        super().setUp()
        _ManualWorker.instances = []

    def _snapshot(self, root_dir: str) -> FrameCacheSnapshot:
        return FrameCacheSnapshot(
            root_dir=root_dir,
            requests=[],
            results={},
            manual_vr=frozenset(),
            crop_excluded=frozenset(),
            manual_frames={},
            review_flagged=frozenset(),
            vary_flagged=frozenset(),
        )

    def test_only_the_latest_load_is_delivered(self) -> None:
        """다시 적재를 시작하면 앞선 워커는 취소되고 그 결과는 표에 적용되지 않음"""
        loader = FrameCacheLoader(self.store)
        self.addCleanup(loader.shutdown)
        applied: list[FrameCacheSnapshot | None] = []
        with mock.patch(
            "src.gui.controllers.frame_cache_loader.FrameCacheLoadWorker", _ManualWorker
        ):
            loader.start([self.ev], "first", ClassifierConfig(), applied.append)
            first = _ManualWorker.instances[0]
            loader.start([self.ev], "second", ClassifierConfig(), applied.append)
            second = _ManualWorker.instances[1]
        self.assertEqual(first.cancelled, 1)
        self.assertTrue(loader.pending)
        self.assertEqual(loader.root_dir, "second")
        first.completed.emit(first.token, self._snapshot("first"))
        self.assertEqual(applied, [])
        self.assertTrue(loader.pending)
        second.completed.emit(second.token, self._snapshot("second"))
        self.assertEqual([item.root_dir for item in applied if item], ["second"])
        self.assertFalse(loader.pending)

    def test_failed_load_is_reported_and_clears_the_waiting_state(self) -> None:
        loader = FrameCacheLoader(self.store)
        self.addCleanup(loader.shutdown)
        applied: list[FrameCacheSnapshot | None] = []
        with mock.patch(
            "src.gui.controllers.frame_cache_loader.FrameCacheLoadWorker", _ManualWorker
        ):
            loader.start([self.ev], self.root, ClassifierConfig(), applied.append)
        worker = _ManualWorker.instances[0]
        worker.completed.emit(worker.token, None)
        self.assertEqual(applied, [None])
        self.assertFalse(loader.pending)

    def test_cancel_and_shutdown_stop_the_running_loads(self) -> None:
        loader = FrameCacheLoader(self.store)
        applied: list[FrameCacheSnapshot | None] = []
        with mock.patch(
            "src.gui.controllers.frame_cache_loader.FrameCacheLoadWorker", _ManualWorker
        ):
            loader.start([self.ev], self.root, ClassifierConfig(), applied.append)
            worker = _ManualWorker.instances[0]
            loader.cancel()
            self.assertFalse(loader.pending)
            worker.completed.emit(worker.token, self._snapshot(self.root))
            self.assertEqual(applied, [])
        loader.shutdown()
        self.assertEqual(worker.waited, 1)
        # - 종료 뒤에는 새 적재를 시작하지 않음
        loader.start([self.ev], self.root, ClassifierConfig(), applied.append)
        self.assertEqual(len(_ManualWorker.instances), 1)

    def test_results_apply_on_finish_through_the_real_worker(self) -> None:
        """실제 워커 스레드가 끝나면 화면 스레드에서 결과가 적용되는지 검증"""
        self.store.save(self.record(dark_outliers_ignored=True))
        loader = FrameCacheLoader(self.store)
        self.addCleanup(loader.shutdown)
        applied: list[FrameCacheSnapshot | None] = []
        loader.start([self.ev], self.root, ClassifierConfig(), applied.append)
        self.assertTrue(self._wait_until(lambda: bool(applied)))
        snapshot = applied[0]
        assert snapshot is not None
        self.assertEqual(set(snapshot.results), {"a.mp4"})
        self.assertEqual(snapshot.review_flagged, frozenset({"a.mp4"}))
        self.assertFalse(loader.pending)

    def test_fingerprints_are_computed_off_the_main_thread(self) -> None:
        """표를 다시 채울 때 파일 지문 계산이 화면 스레드에서 실행되지 않는지 검증"""
        threads: list[threading.Thread] = []
        original = compute_fingerprint

        def record_fingerprint(filepath: str, filesize_bytes: int) -> str:
            threads.append(threading.current_thread())
            return original(filepath, filesize_bytes)

        loader = FrameCacheLoader(self.store)
        self.addCleanup(loader.shutdown)
        applied: list[FrameCacheSnapshot | None] = []
        with mock.patch(
            "src.gui.controllers.frame_analysis_targets.compute_fingerprint",
            side_effect=record_fingerprint,
        ):
            loader.start([self.ev], self.root, ClassifierConfig(), applied.append)
            self.assertTrue(self._wait_until(lambda: bool(applied)))
        self.assertTrue(threads)
        self.assertEqual([thread for thread in threads if thread is threading.main_thread()], [])

    def test_worker_run_emits_snapshot_with_token(self) -> None:
        """워커 본문이 적재 결과를 요청 번호와 함께 내보내는지 검증(커버리지는 스레드 밖 직접 호출로 잡음)"""
        self.store.save(self.record())
        worker = FrameCacheLoadWorker(self.store, [self.ev], self.root, ClassifierConfig(), 7)
        received: list[tuple[int, object]] = []
        worker.completed.connect(lambda token, snapshot: received.append((token, snapshot)))
        worker.run()
        self.assertEqual(len(received), 1)
        token, snapshot = received[0]
        self.assertEqual(token, 7)
        assert isinstance(snapshot, FrameCacheSnapshot)
        self.assertEqual(set(snapshot.results), {"a.mp4"})

    def test_worker_run_reports_none_when_load_fails(self) -> None:
        """적재 중 예외가 나면 결과 없이 끝을 알리는지 검증"""
        worker = FrameCacheLoadWorker(self.store, [self.ev], self.root, ClassifierConfig(), 3)
        received: list[tuple[int, object]] = []
        worker.completed.connect(lambda token, snapshot: received.append((token, snapshot)))
        with (
            mock.patch(
                "src.gui.controllers.frame_cache_loader.run_frame_cache_load",
                side_effect=OSError("disk"),
            ),
            self.assertLogs("src.gui.controllers.frame_cache_loader", level="ERROR"),
        ):
            worker.run()
        self.assertEqual(received, [(3, None)])

    def _wait_until(self, predicate: Callable[[], bool]) -> bool:
        """이벤트를 처리하며 조건이 참이 될 때까지 기다림"""
        end = time.monotonic() + _WAIT_SEC
        while not predicate():
            if time.monotonic() > end:
                return False
            _APP.processEvents()
            time.sleep(0.01)
        return True


if __name__ == "__main__":
    unittest.main(verbosity=2)



