"""화질 분석 서비스의 캐시 재사용, 배치 진행과 취소, 부분 보존, 활성 화면 조회 검증"""

import os
import sqlite3
import tempfile
import threading
import unittest

from src.core.analysis.frame_analysis import FRAME_ANALYSIS_VERSION, TierScore
from src.core.analysis.frame_analysis_runner import ProcessStderrRunner, StderrCommandRunner
from src.core.models import ActiveFrame
from src.service.analysis_store import ANALYSIS_TABLE_NAME, AnalysisStore
from src.service.frame_analysis_service import (
    FrameAnalysisRecord,
    FrameAnalysisRequest,
    FrameAnalysisService,
    FrameAnalysisStore,
    _is_locked_error,
)
from tests._frame_analysis_fakes import FakeStderrRunner


def _request(
    filename: str,
    *,
    size: int = 100,
    mtime: int = 1,
    width: int = 1920,
    height: int = 1080,
    has_vr_metadata: bool = False,
) -> FrameAnalysisRequest:
    return FrameAnalysisRequest(
        filename=filename,
        path=filename,
        width=width,
        height=height,
        duration_sec=20.0,
        filesize_bytes=size,
        mtime_ns=mtime,
        has_vr_metadata=has_vr_metadata,
    )


def _record(
    filename: str,
    *,
    size: int = 100,
    mtime: int = 1,
    version: int = FRAME_ANALYSIS_VERSION,
    has_black_bars: bool = True,
) -> FrameAnalysisRecord:
    return FrameAnalysisRecord(
        filename=filename,
        filesize_bytes=size,
        mtime_ns=mtime,
        active_width=720,
        active_height=1080,
        active_x=600,
        active_y=0,
        has_black_bars=has_black_bars,
        metric_used="vmaf",
        vmaf_model="vmaf_v0.6.1",
        tier_scores=(TierScore("720p", 0.997, 98.5),),
        candidate_tier="720p",
        valid_segments=9,
        total_segments=9,
        unmeasurable=False,
        is_vr_protected=False,
        measured_at="2026-01-01T00:00:00Z",
        analysis_version=version,
    )


class _ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_dir = tmp.name
        self.db_path = os.path.join(tmp.name, "video_cache.sqlite3")
        self.work_dir = tmp.name
        self.store = FrameAnalysisStore(self.db_path)
        self.addCleanup(self.store.close)

    def make_service(
        self, runner: StderrCommandRunner | None = None, *, has_vmaf: bool = True
    ) -> FrameAnalysisService:
        service = FrameAnalysisService(
            self.store,
            work_dir=self.work_dir,
            has_vmaf=has_vmaf,
            runner=runner,
        )
        self.addCleanup(service.close)
        return service


class TestFrameAnalysisStore(_ServiceTestCase):
    """캐시 저장소의 저장, 조회, 분석 캐시와의 독립성 검증"""

    def test_save_and_load_roundtrip(self) -> None:
        record = _record("clip.mp4")
        self.store.save(record)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.load_many([record.key]), {record.key: record})
        loaded = next(iter(self.store.load_many([record.key]).values()))
        self.assertEqual(loaded.active_region.to_active_frame(), ActiveFrame(720, 1080))

    def test_load_many_without_keys_or_matches(self) -> None:
        self.assertEqual(self.store.load_many([]), {})
        self.assertEqual(self.store.load_many([("none.mp4", 1, 1)]), {})

    def test_analysis_store_clear_keeps_frame_cache(self) -> None:
        self.store.save(_record("clip.mp4"))
        analysis = AnalysisStore(self.db_path)
        self.addCleanup(analysis.close)
        self.assertEqual(analysis.clear(), 0)
        self.assertEqual(analysis.fetch_by_names(["clip.mp4"]), [])
        self.assertEqual(self.store.count(), 1)

    def test_analysis_schema_recreation_keeps_frame_cache(self) -> None:
        self.store.save(_record("clip.mp4"))
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"DROP TABLE IF EXISTS {ANALYSIS_TABLE_NAME}")
        conn.commit()
        conn.close()
        analysis = AnalysisStore(self.db_path)
        self.addCleanup(analysis.close)
        self.assertEqual(analysis.count(), 0)
        self.assertEqual(self.store.count(), 1)

    def test_close_then_reuse_reconnects(self) -> None:
        self.store.save(_record("clip.mp4"))
        self.store.close()
        self.assertEqual(self.store.count(), 1)

    def test_locked_error_detection(self) -> None:
        self.assertTrue(_is_locked_error(sqlite3.OperationalError("database is locked")))
        self.assertTrue(_is_locked_error(sqlite3.OperationalError("database table is busy")))
        self.assertFalse(_is_locked_error(sqlite3.OperationalError("disk I/O error")))


class TestFrameAnalysisService(_ServiceTestCase):
    """배치 실행, 캐시 재사용, 진행과 취소, 활성 화면 조회 검증"""

    def test_batch_measures_and_caches_each_item(self) -> None:
        runner = FakeStderrRunner()
        service = self.make_service(runner)
        progress: list[tuple[int, int, str]] = []
        results = service.analyze_batch(
            [_request("a.mp4"), _request("b.mp4")],
            on_progress=lambda completed, total, name, _result: progress.append((completed, total, name)),
        )
        self.assertEqual(set(results), {"a.mp4", "b.mp4"})
        self.assertEqual(progress, [(1, 2, "a.mp4"), (2, 2, "b.mp4")])
        self.assertEqual(self.store.count(), 2)
        self.assertFalse(results["a.mp4"].unmeasurable)

    def test_cache_hit_skips_runner(self) -> None:
        runner = FakeStderrRunner()
        service = self.make_service(runner)
        first = service.analyze_batch([_request("a.mp4")])
        runner.commands.clear()
        second = service.analyze_batch([_request("a.mp4")])
        self.assertEqual(runner.commands, [])
        self.assertEqual(second["a.mp4"], first["a.mp4"])

    def test_version_mismatch_triggers_reanalysis(self) -> None:
        self.store.save(_record("a.mp4", version=0))
        runner = FakeStderrRunner()
        service = self.make_service(runner)
        service.analyze_batch([_request("a.mp4")])
        self.assertGreater(len(runner.commands), 0)
        record = self.store.load_many([("a.mp4", 100, 1)])[("a.mp4", 100, 1)]
        self.assertEqual(record.analysis_version, FRAME_ANALYSIS_VERSION)

    def test_force_reanalyzes_cached_item(self) -> None:
        runner = FakeStderrRunner()
        service = self.make_service(runner)
        service.analyze_batch([_request("a.mp4")])
        runner.commands.clear()
        service.analyze_batch([_request("a.mp4")], force=True)
        self.assertGreater(len(runner.commands), 0)
        self.assertEqual(self.store.count(), 1)

    def test_cancel_after_first_item_keeps_completed_cache(self) -> None:
        cancel = threading.Event()
        service = self.make_service(FakeStderrRunner())

        def _cancel_after_first(completed: int, _total: int, _name: str, _result: object) -> None:
            if completed == 1:
                cancel.set()

        with self.assertRaises(InterruptedError):
            service.analyze_batch(
                [_request("a.mp4"), _request("b.mp4"), _request("c.mp4")],
                cancel_event=cancel,
                on_progress=_cancel_after_first,
            )
        self.assertEqual(self.store.count(), 1)
        self.assertIn(("a.mp4", 100, 1), self.store.load_many([("a.mp4", 100, 1)]))

    def test_cancel_before_start_saves_nothing(self) -> None:
        cancel = threading.Event()
        cancel.set()
        service = self.make_service(FakeStderrRunner())
        with self.assertRaises(InterruptedError):
            service.analyze_batch([_request("a.mp4")], cancel_event=cancel)
        self.assertEqual(self.store.count(), 0)

    def test_duplicate_requests_are_measured_once(self) -> None:
        runner = FakeStderrRunner()
        service = self.make_service(runner)
        calls: list[tuple[int, int]] = []
        results = service.analyze_batch(
            [_request("a.mp4"), _request("a.mp4")],
            on_progress=lambda completed, total, _name, _result: calls.append((completed, total)),
        )
        self.assertEqual(calls, [(1, 1)])
        self.assertEqual(set(results), {"a.mp4"})

    def test_load_active_frames_returns_cached_only(self) -> None:
        service = self.make_service(FakeStderrRunner())
        requests = [_request("a.mp4"), _request("b.mp4")]
        service.analyze_batch(requests)
        self.store.save(_record("old.mp4", version=0))
        frames = service.load_active_frames([*requests, _request("missing.mp4"), _request("old.mp4")])
        self.assertEqual(set(frames), {"a.mp4", "b.mp4"})
        self.assertEqual(frames["a.mp4"], ActiveFrame(1920, 1080))

    def test_runner_for_returns_injected_or_default(self) -> None:
        injected = FakeStderrRunner()
        service = self.make_service(injected)
        self.assertIs(service._runner_for(None), injected)
        default_service = self.make_service()
        self.assertIsInstance(default_service._runner_for(None), ProcessStderrRunner)

    def test_ssim_only_service_records_metric_name(self) -> None:
        service = self.make_service(FakeStderrRunner(), has_vmaf=False)
        result = service.analyze_batch([_request("a.mp4")])["a.mp4"]
        self.assertEqual(result.metric_used, "ssim")
        record = self.store.load_many([("a.mp4", 100, 1)])[("a.mp4", 100, 1)]
        self.assertEqual(record.metric_used, "ssim")
        self.assertEqual(record.tier_scores, result.tier_scores)


if __name__ == "__main__":
    unittest.main()
