"""화질 분석 서비스의 캐시 재사용, 배치 진행과 취소, 부분 보존, 활성 화면 조회 검증"""

import os
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.core import workspace_paths as paths_module
from src.core.analysis.frame_analysis import (
    CROP_SCAN_VERSION,
    DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    FRAME_ANALYSIS_VERSION,
    METRIC_CROP_ONLY,
    FrameAnalysisThresholds,
    TierScore,
    analysis_version_for_mode,
)
from src.core.analysis.frame_analysis_runner import ProcessStderrRunner, StderrCommandRunner
from src.core.models import ActiveFrame
from src.service.analysis_store import ANALYSIS_TABLE_NAME, AnalysisStore
from src.service.frame_analysis_service import (
    INNER_ROUND_WORKERS_FEW,
    INNER_ROUND_WORKERS_SINGLE,
    FrameAnalysisRecord,
    FrameAnalysisRequest,
    FrameAnalysisService,
    FrameAnalysisStore,
    _inner_round_workers,
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
        self,
        runner: StderrCommandRunner | None = None,
        *,
        has_vmaf: bool = True,
        mode: str = "balanced",
        scale_algorithm: str = "bicubic",
        video_workers: int = 1,
        thresholds: FrameAnalysisThresholds | None = None,
    ) -> FrameAnalysisService:
        service = FrameAnalysisService(
            self.store,
            work_dir=self.work_dir,
            has_vmaf=has_vmaf,
            runner=runner,
            mode=mode,
            scale_algorithm=scale_algorithm,
            video_workers=video_workers,
            thresholds=thresholds or DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
        )
        self.addCleanup(service.close)
        return service

    def _relaxable_record(self) -> FrameAnalysisRecord:
        """기본 기준에서는 720p, 완화 기준에서는 540p가 후보인 전 프레임 캐시 행"""
        return replace(
            _record("a.mp4"),
            active_width=1920,
            active_height=1080,
            active_x=0,
            active_y=0,
            has_black_bars=False,
            candidate_tier="720p",
            tier_scores=(
                TierScore("900p", 0.998, 99.0),
                TierScore("720p", 0.997, 98.5),
                TierScore("540p", 0.994, 96.0),
                TierScore("480p", 0.990, 93.0),
            ),
        )


class TestFrameAnalysisStore(_ServiceTestCase):
    """캐시 저장소의 저장, 조회, 분석 캐시와의 독립성 검증"""

    def test_save_and_load_roundtrip(self) -> None:
        record = _record("clip.mp4")
        self.store.save(record)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.load_many([record.key]), {record.key: record})
        loaded = next(iter(self.store.load_many([record.key]).values()))
        self.assertEqual(loaded.active_region.to_active_frame(), ActiveFrame(720, 1080, 600, 0))

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

    def test_vr_override_roundtrip_and_cache_invalidation(self) -> None:
        """수동 VR 보호 저장, 조회, 해제와 캐시 삭제를 검증"""
        self.store.save(_record("clip.mp4"))
        self.store.set_vr_override(source_path="C:/work/clip.mp4", filename="clip.mp4", manual_vr=True)
        self.assertEqual(self.store.load_vr_overrides(["C:/work/clip.mp4"]), frozenset({"C:/work/clip.mp4"}))
        self.assertEqual(self.store.count(), 0)
        self.store.set_vr_override(source_path="C:/work/clip.mp4", filename="clip.mp4", manual_vr=False)
        self.assertEqual(self.store.load_vr_overrides(["C:/work/clip.mp4"]), frozenset())
        self.assertEqual(self.store.load_vr_overrides([]), frozenset())


    def test_manual_active_frame_table_is_added_to_an_old_database(self) -> None:
        """직접 지정 테이블이 없는 이전 DB를 열어도 만들고 기존 행을 그대로 읽는지 검증"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE frame_analysis_cache ("
            "filename TEXT NOT NULL, filesize_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, "
            "active_width INTEGER NOT NULL, active_height INTEGER NOT NULL, active_x INTEGER NOT NULL, "
            "active_y INTEGER NOT NULL, has_black_bars INTEGER NOT NULL, metric_used TEXT NOT NULL, "
            "vmaf_model TEXT NOT NULL, tier_scores_json TEXT NOT NULL, candidate_tier TEXT, "
            "valid_segments INTEGER NOT NULL, total_segments INTEGER NOT NULL, "
            "unmeasurable INTEGER NOT NULL, is_vr_protected INTEGER NOT NULL, measured_at TEXT NOT NULL, "
            "analysis_version INTEGER NOT NULL, PRIMARY KEY (filename, filesize_bytes, mtime_ns)"
            ") WITHOUT ROWID"
        )
        conn.execute(
            "INSERT INTO frame_analysis_cache VALUES "
            "('clip.mp4', 100, 1, 720, 1080, 600, 0, 1, 'vmaf', 'vmaf_v0.6.1', '[]', NULL, "
            "9, 9, 0, 0, '2026-01-01T00:00:00Z', 202)"
        )
        conn.commit()
        conn.close()
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.load_manual_active_frames(["C:/work/clip.mp4"]), {})
        self.store.set_manual_active_frame(
            "C:/work/clip.mp4",
            ActiveFrame(1600, 1000, 260, 40),
            frame_width=1920,
            frame_height=1080,
        )
        self.assertEqual(
            self.store.load_manual_active_frames(["C:/work/clip.mp4"]),
            {"C:/work/clip.mp4": ActiveFrame(1600, 1000, 260, 40)},
        )

    def test_margins_vary_column_is_added_to_an_old_database(self) -> None:
        """구간별 여백 열이 없는 이전 DB에서 열을 덧붙이고 기존 행은 기본값 거짓으로 읽음"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE frame_analysis_cache ("
            "filename TEXT NOT NULL, filesize_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, "
            "active_width INTEGER NOT NULL, active_height INTEGER NOT NULL, active_x INTEGER NOT NULL, "
            "active_y INTEGER NOT NULL, has_black_bars INTEGER NOT NULL, metric_used TEXT NOT NULL, "
            "vmaf_model TEXT NOT NULL, tier_scores_json TEXT NOT NULL, candidate_tier TEXT, "
            "valid_segments INTEGER NOT NULL, total_segments INTEGER NOT NULL, "
            "unmeasurable INTEGER NOT NULL, is_vr_protected INTEGER NOT NULL, measured_at TEXT NOT NULL, "
            "analysis_version INTEGER NOT NULL, PRIMARY KEY (filename, filesize_bytes, mtime_ns)"
            ") WITHOUT ROWID"
        )
        conn.execute(
            "INSERT INTO frame_analysis_cache VALUES "
            "('clip.mp4', 100, 1, 720, 1080, 600, 0, 1, 'vmaf', 'vmaf_v0.6.1', '[]', NULL, "
            "9, 9, 0, 0, '2026-01-01T00:00:00Z', 202)"
        )
        conn.commit()
        conn.close()
        loaded = self.store.load_many([("clip.mp4", 100, 1)])
        self.assertFalse(loaded[("clip.mp4", 100, 1)].margins_vary)
        self.assertEqual(self.store.count(), 1)

    def test_margins_vary_flag_roundtrips_through_sqlite(self) -> None:
        record = replace(_record("clip.mp4"), margins_vary=True)
        self.store.save(record)
        self.assertTrue(self.store.load_many([record.key])[record.key].margins_vary)


class TestFrameStoreScopedCleanup(_ServiceTestCase):
    """화질 분석 캐시와 영상별 지정의 정리 범위별 건수와 비우기"""

    def setUp(self) -> None:
        super().setUp()
        roots = tempfile.TemporaryDirectory()
        self.addCleanup(roots.cleanup)
        self.root_a = os.path.join(roots.name, "root_dir_a")
        self.root_b = os.path.join(roots.name, "root_dir_b")
        for path in (self.root_a, self.root_b):
            os.makedirs(path, exist_ok=True)
        self.crop_a = os.path.join(self.root_a, "a.mp4")
        self.crop_b = os.path.join(self.root_b, "b.mp4")
        self.vr_a = os.path.join(self.root_a, "vr.mp4")
        self.vr_b = os.path.join(self.root_b, "vr_b.mp4")

    def test_frame_cache_counts_and_clears_only_given_roots(self) -> None:
        with open(os.path.join(self.root_a, "a.mp4"), "wb") as handle:
            handle.write(b"x")
        self.store.save(_record("a.mp4"))
        self.store.save(_record("root_dir_b/b.mp4"))
        self.assertEqual(self.store.count_for_roots([self.root_a]), 1)
        self.assertEqual(self.store.clear_for_roots([self.root_a]), 1)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.count_for_roots(), 1)
        self.assertEqual(self.store.clear_for_roots(), 1)
        self.assertEqual(self.store.count(), 0)

    def test_frame_cache_keeps_bare_filename_of_other_root(self) -> None:
        """경로 없는 파일명은 범위 폴더에 실제 파일이 있을 때만 그 폴더 소속으로 봄"""
        with open(os.path.join(self.root_a, "only_a.mp4"), "wb") as handle:
            handle.write(b"x")
        self.store.save(_record("only_a.mp4"))
        self.store.save(_record("only_b.mp4"))
        self.assertEqual(self.store.count_for_roots([self.root_a]), 1)
        self.assertEqual(self.store.clear_for_roots([self.root_a]), 1)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.clear_for_roots(), 1)
        self.assertEqual(self.store.count(), 0)

    def test_crop_exclusions_and_vr_overrides_use_path_scope(self) -> None:
        self.store.set_crop_exclusion(self.crop_a, True)
        self.store.set_crop_exclusion(self.crop_b, True)
        self.store.set_vr_override(source_path=self.vr_a, filename="vr.mp4", manual_vr=True)
        self.store.set_vr_override(source_path=self.vr_b, filename="root_dir_b/vr_b.mp4", manual_vr=True)

        self.assertEqual(self.store.count_crop_exclusions([self.root_a]), 1)
        self.assertEqual(self.store.count_vr_overrides([self.root_a]), 1)
        self.assertEqual(self.store.clear_crop_exclusions([self.root_a]), 1)
        self.assertEqual(self.store.clear_vr_overrides([self.root_a]), 1)
        self.assertEqual(self.store.load_crop_exclusions([self.crop_b]), frozenset({self.crop_b}))
        self.assertEqual(self.store.load_vr_overrides([self.vr_b]), frozenset({self.vr_b}))

        self.assertEqual(self.store.count_crop_exclusions(), 1)
        self.assertEqual(self.store.count_vr_overrides(), 1)
        self.assertEqual(self.store.clear_crop_exclusions(), 1)
        self.assertEqual(self.store.clear_vr_overrides(), 1)
        self.assertEqual(self.store.count_crop_exclusions(), 0)
        self.assertEqual(self.store.count_vr_overrides(), 0)

    def test_manual_active_frames_use_path_scope(self) -> None:
        """사용자 지정 활성 화면도 경로 범위로 세고 지우는지 검증"""
        manual = ActiveFrame(1600, 1000, 260, 40)
        self.store.set_manual_active_frame(
            self.crop_a, manual, frame_width=1920, frame_height=1080
        )
        self.store.set_manual_active_frame(
            self.crop_b, manual, frame_width=1920, frame_height=1080
        )
        self.assertEqual(self.store.count_manual_active_frames([self.root_a]), 1)
        self.assertEqual(self.store.clear_manual_active_frames([self.root_a]), 1)
        self.assertEqual(
            self.store.load_manual_active_frames([self.crop_b]), {self.crop_b: manual}
        )
        self.assertEqual(self.store.count_manual_active_frames(), 1)
        self.assertEqual(self.store.clear_manual_active_frames(), 1)
        self.assertEqual(self.store.count_manual_active_frames(), 0)

    def test_clearing_frame_cache_keeps_per_video_overrides(self) -> None:
        self.store.save(_record("a.mp4"))
        self.store.set_crop_exclusion(self.crop_a, True)
        self.store.set_vr_override(source_path=self.vr_a, filename="vr.mp4", manual_vr=True)
        self.assertEqual(self.store.clear_for_roots(), 1)
        self.assertEqual(self.store.load_crop_exclusions([self.crop_a]), frozenset({self.crop_a}))
        self.assertEqual(self.store.load_vr_overrides([self.vr_a]), frozenset({self.vr_a}))


class TestFrameStoreScopedCleanupCost(_ServiceTestCase):
    """정리 범위 판정이 행 수에 비례해 폴더를 훑지 않는지"""

    ROW_COUNT: int = 2_000

    def setUp(self) -> None:
        super().setUp()
        roots = tempfile.TemporaryDirectory()
        self.addCleanup(roots.cleanup)
        self.root_a = os.path.join(roots.name, "root_dir_a")
        os.makedirs(self.root_a, exist_ok=True)

    def test_thousands_of_rows_scan_folder_once(self) -> None:
        """수천 행 화질 분석 캐시를 현재 폴더 범위로 셀 때 폴더 파싱과 scandir이 행 수를 따르지 않는지 검증"""
        for index in range(self.ROW_COUNT):
            self.store.save(_record(f"clip_{index}.mp4"))
        with (
            patch.object(paths_module, "parse_root_dirs", wraps=paths_module.parse_root_dirs) as parse,
            patch.object(paths_module.os.path, "isfile", wraps=os.path.isfile) as isfile,
            patch.object(paths_module.os, "scandir", wraps=os.scandir) as scandir,
        ):
            self.assertEqual(self.store.count_for_roots([self.root_a]), 0)
        self.assertLessEqual(parse.call_count, 2)
        self.assertEqual(isfile.call_count, 0)
        self.assertEqual(scandir.call_count, 1)


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

    def test_relaxed_thresholds_reuse_scores_and_rejudge_candidate(self) -> None:
        """기준 완화 뒤 저장 점수로 후보가 확정되면 다시 재지 않고 새 후보를 쓰는지 검증"""
        self.store.save(self._relaxable_record())
        runner = FakeStderrRunner()
        service = self.make_service(
            runner, thresholds=FrameAnalysisThresholds(vmaf=95.0, ssim=0.993)
        )
        results = service.analyze_batch([_request("a.mp4")])
        self.assertEqual(runner.commands, [])
        self.assertEqual(results["a.mp4"].candidate_tier, "540p")
        self.assertEqual(service.count_cached([_request("a.mp4")]), 1)

    def test_stricter_thresholds_remeasure_when_the_next_vmaf_is_missing(self) -> None:
        """기준 강화로 위 규격의 VMAF가 필요해지면 캐시를 쓰지 않고 다시 재는지 검증"""
        record = replace(
            _record("a.mp4"),
            active_width=3840,
            active_height=2160,
            active_x=0,
            active_y=0,
            has_black_bars=False,
            candidate_tier="1080p",
            tier_scores=(TierScore("1440p", 0.999, None), TierScore("1080p", 0.998, 98.6)),
        )
        self.store.save(record)
        runner = FakeStderrRunner()
        service = self.make_service(runner, thresholds=FrameAnalysisThresholds(vmaf=99.0))
        service.analyze_batch([_request("a.mp4", width=3840, height=2160)])
        self.assertGreater(len(runner.commands), 0)

    def test_load_results_uses_current_thresholds(self) -> None:
        """표 다시 읽기가 현재 기준으로 축소 후보를 다시 판정하는지 검증"""
        self.store.save(self._relaxable_record())
        relaxed = self.make_service(
            thresholds=FrameAnalysisThresholds(vmaf=95.0, ssim=0.993)
        )
        self.assertEqual(relaxed.load_results([_request("a.mp4")])["a.mp4"].candidate_tier, "540p")
        default = self.make_service()
        self.assertEqual(default.load_results([_request("a.mp4")])["a.mp4"].candidate_tier, "720p")

    def test_load_results_reverts_the_active_frame_above_the_area_limit(self) -> None:
        """면적 상한을 낮춘 뒤 표 다시 읽기가 활성 화면을 전체 프레임으로 되돌리는지 검증"""
        self.store.save(
            replace(
                _record("a.mp4"),
                active_width=1800,
                active_height=1000,
                active_x=60,
                active_y=40,
                has_black_bars=True,
            )
        )
        service = self.make_service(
            thresholds=FrameAnalysisThresholds(active_area_no_bars_ratio=0.5)
        )
        result = service.load_results([_request("a.mp4")])["a.mp4"]
        self.assertFalse(result.active_region.has_black_bars)

    def test_count_cached_reports_only_reusable_rows(self) -> None:
        """실행 전 캐시 재사용 가능 건수를 세고 force면 0으로 보는지 검증"""
        service = self.make_service(FakeStderrRunner())
        self.assertEqual(service.count_cached([]), 0)
        self.assertEqual(service.count_cached([_request("a.mp4")]), 0)
        service.analyze_batch([_request("a.mp4")])
        self.assertEqual(service.count_cached([_request("a.mp4")]), 1)
        self.assertEqual(service.count_cached([_request("a.mp4")], force=True), 0)

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

    def test_load_active_frames_returns_crop_confirmed_only(self) -> None:
        """잘라내기 확정 영상만 활성 화면으로 돌려주고 설정 끔, 제외, VR 보호는 빼는지 검증"""
        runner = FakeStderrRunner(boxes=[(1600, 1000, 260, 40)])
        service = self.make_service(runner)
        requests = [_request("a.mp4"), _request("b.mp4")]
        service.analyze_batch(requests)
        self.store.save(_record("old.mp4", version=0))
        frames = service.load_active_frames(
            [*requests, _request("missing.mp4"), _request("old.mp4")]
        )
        self.assertEqual(set(frames), {"a.mp4", "b.mp4"})
        self.assertEqual(frames["a.mp4"], ActiveFrame(1600, 1000, 260, 40))
        self.assertEqual(service.load_active_frames(requests, crop_enabled=False), {})
        self.store.set_crop_exclusion("a.mp4", True)
        self.assertEqual(set(service.load_active_frames(requests)), {"b.mp4"})

    def test_load_active_frames_skips_vr_protected_and_full_frame_rows(self) -> None:
        """VR 보호 행과 검은 여백이 없는 행은 활성 화면으로 넘기지 않는지 검증"""
        service = self.make_service(FakeStderrRunner())
        service.analyze_batch([_request("full.mp4")])
        self.assertEqual(service.load_active_frames([_request("full.mp4")]), {})
        vr_service = self.make_service(FakeStderrRunner(boxes=[(1600, 1000, 260, 40)]))
        vr_service.analyze_batch([_request("vr.mp4", has_vr_metadata=True)])
        self.assertEqual(vr_service.load_active_frames([_request("vr.mp4", has_vr_metadata=True)]), {})

    def test_crop_exclusion_roundtrip_keeps_frame_cache(self) -> None:
        """잘라내기 제외 저장, 해제가 분석 캐시를 지우지 않고 경로별로 동작하는지 검증"""
        self.store.save(_record("clip.mp4"))
        self.store.set_crop_exclusion("C:/work/clip.mp4", True)
        self.assertEqual(self.store.load_crop_exclusions(["C:/work/clip.mp4"]), frozenset({"C:/work/clip.mp4"}))
        self.assertEqual(self.store.load_crop_exclusions(["C:/work/other.mp4"]), frozenset())
        self.assertEqual(self.store.count(), 1)
        self.store.set_crop_exclusion("C:/work/clip.mp4", False)
        self.assertEqual(self.store.load_crop_exclusions(["C:/work/clip.mp4"]), frozenset())
        self.assertEqual(self.store.load_crop_exclusions([]), frozenset())
        self.store.set_crop_exclusion("", True)
        self.assertEqual(self.store.count(), 1)

    def test_manual_active_frame_roundtrip_keeps_frame_cache(self) -> None:
        """사용자 지정 활성 화면 저장, 해제가 분석 캐시를 지우지 않고 경로별로 동작하는지 검증"""
        self.store.save(_record("clip.mp4"))
        manual = ActiveFrame(1600, 1000, 260, 40)
        self.store.set_manual_active_frame(
            "C:/work/clip.mp4", manual, frame_width=1920, frame_height=1080
        )
        self.assertEqual(
            self.store.load_manual_active_frames(["C:/work/clip.mp4"]),
            {"C:/work/clip.mp4": manual},
        )
        self.assertEqual(self.store.load_manual_active_frames(["C:/work/other.mp4"]), {})
        self.assertEqual(self.store.load_manual_active_frames([]), {})
        self.assertEqual(self.store.count(), 1)
        self.store.set_manual_active_frame(
            "C:/work/clip.mp4", None, frame_width=1920, frame_height=1080
        )
        self.assertEqual(self.store.load_manual_active_frames(["C:/work/clip.mp4"]), {})
        self.store.set_manual_active_frame(
            "", manual, frame_width=1920, frame_height=1080
        )
        self.assertEqual(self.store.count_manual_active_frames(), 0)

    def test_manual_active_frame_value_is_normalized_before_saving(self) -> None:
        """저장 값이 짝수 정렬되고 전체 프레임과 같은 지정은 지움으로 처리되는지 검증"""
        self.store.set_manual_active_frame(
            "C:/work/clip.mp4",
            ActiveFrame(1601, 999, 261, 41),
            frame_width=1920,
            frame_height=1080,
        )
        self.assertEqual(
            self.store.load_manual_active_frames(["C:/work/clip.mp4"])["C:/work/clip.mp4"],
            ActiveFrame(1600, 998, 260, 40),
        )
        self.store.set_manual_active_frame(
            "C:/work/clip.mp4",
            ActiveFrame(1920, 1080),
            frame_width=1920,
            frame_height=1080,
        )
        self.assertEqual(self.store.load_manual_active_frames(["C:/work/clip.mp4"]), {})

    def test_manual_active_frame_rejects_tiny_and_out_of_bounds_values(self) -> None:
        """최소 크기 미만, 영상 밖 지정은 저장하지 않고 `ValueError`"""
        with self.assertRaises(ValueError):
            self.store.set_manual_active_frame(
                "C:/work/clip.mp4",
                ActiveFrame(31, 1000),
                frame_width=1920,
                frame_height=1080,
            )
        with self.assertRaises(ValueError):
            self.store.set_manual_active_frame(
                "C:/work/clip.mp4",
                ActiveFrame(1600, 1000, 400, 40),
                frame_width=1920,
                frame_height=1080,
            )
        self.assertEqual(self.store.count_manual_active_frames(), 0)

    def test_manual_active_frame_overrides_automatic_and_exclusion(self) -> None:
        """사용자 지정 활성 화면이 자동 감지 결과와 제외를 덮어쓰고 설정 끔, VR 보호는 못 넘는지 검증"""
        service = self.make_service(FakeStderrRunner(boxes=[(1600, 1000, 260, 40)]))
        requests = [_request("a.mp4")]
        service.analyze_batch(requests)
        manual = ActiveFrame(1280, 720, 0, 180)
        self.store.set_manual_active_frame("a.mp4", manual, frame_width=1920, frame_height=1080)
        self.assertEqual(service.load_active_frames(requests), {"a.mp4": manual})
        self.store.set_crop_exclusion("a.mp4", True)
        self.assertEqual(service.load_active_frames(requests), {"a.mp4": manual})
        self.assertEqual(service.load_active_frames(requests, crop_enabled=False), {})

    def test_manual_active_frame_is_ignored_for_vr_protected_video(self) -> None:
        """VR 보호 영상은 직접 지정이 있어도 활성 화면을 넘기지 않는지 검증"""
        requests = [_request("vr.mp4", has_vr_metadata=True)]
        service = self.make_service(FakeStderrRunner(boxes=[(1600, 1000, 260, 40)]))
        service.analyze_batch(requests)
        self.store.set_manual_active_frame(
            "vr.mp4", ActiveFrame(1280, 720, 0, 180), frame_width=1920, frame_height=1080
        )
        self.assertEqual(service.load_active_frames(requests), {})

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

    def test_crop_only_scan_saves_a_detection_row_without_metrics(self) -> None:
        """감지 전용 실행이 SSIM/VMAF 명령 없이 활성 화면만 저장하는지 검증"""
        runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        service = self.make_service(runner)
        request = _request("a.mp4")
        result = service.analyze_batch([request], crop_only=True)["a.mp4"]
        self.assertEqual(runner.count_commands("-filter_complex"), 0)
        record = self.store.load_many([request.key])[request.key]
        self.assertEqual(record.analysis_version, CROP_SCAN_VERSION)
        self.assertEqual(record.metric_used, METRIC_CROP_ONLY)
        self.assertEqual(record.tier_scores, ())
        self.assertIsNone(record.candidate_tier)
        self.assertEqual(record.active_region.to_active_frame(), ActiveFrame(720, 1080, 600, 0))
        self.assertEqual(result.active_region.to_active_frame(), ActiveFrame(720, 1080, 600, 0))
        self.assertEqual(service.load_active_frames([request]), {"a.mp4": ActiveFrame(720, 1080, 600, 0)})

    def test_crop_only_reuses_a_full_analysis_row_and_keeps_its_scores(self) -> None:
        """전체 화질 분석 행 위의 감지 실행이 캐시를 재사용해 후보와 점수를 보존하는지 검증"""
        runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        service = self.make_service(runner)
        request = _request("a.mp4")
        service.analyze_batch([request])
        runner.commands.clear()
        reused = service.analyze_batch([request], crop_only=True)["a.mp4"]
        self.assertEqual(runner.commands, [])
        self.assertEqual(service.count_cached([request], crop_only=True), 1)
        record = self.store.load_many([request.key])[request.key]
        self.assertEqual(record.analysis_version, FRAME_ANALYSIS_VERSION)
        self.assertEqual(record.candidate_tier, "360p")
        self.assertEqual(reused.candidate_tier, "360p")
        self.assertTrue(reused.tier_scores)

    def test_full_analysis_promotes_a_crop_only_row(self) -> None:
        """감지 전용 행 위의 전체 화질 분석이 캐시 미스로 측정해 전체 결과로 승격하는지 검증"""
        crop_service = self.make_service(FakeStderrRunner(boxes=[(720, 1080, 600, 0)]))
        request = _request("a.mp4")
        crop_service.analyze_batch([request], crop_only=True)
        self.assertEqual(
            self.store.load_many([request.key])[request.key].analysis_version, CROP_SCAN_VERSION
        )
        full_runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        full_service = self.make_service(full_runner)
        self.assertEqual(full_service.count_cached([request]), 0)
        result = full_service.analyze_batch([request])["a.mp4"]
        self.assertGreater(full_runner.count_commands("-filter_complex"), 0)
        record = self.store.load_many([request.key])[request.key]
        self.assertEqual(record.analysis_version, FRAME_ANALYSIS_VERSION)
        self.assertIsNotNone(record.candidate_tier)
        self.assertEqual(result.analysis_version, FRAME_ANALYSIS_VERSION)
        self.assertEqual(full_service.count_cached([request]), 1)

    def test_crop_only_row_stays_valid_after_a_quality_mode_change(self) -> None:
        """화질 분석 방식을 바꿔도 감지 전용 행의 활성 화면이 판정과 표에 남는지 검증"""
        request = _request("a.mp4")
        self.make_service(
            FakeStderrRunner(boxes=[(720, 1080, 600, 0)]), mode="balanced"
        ).analyze_batch([request], crop_only=True)
        precise = self.make_service(mode="precise")
        self.assertEqual(precise.count_cached([request], crop_only=True), 1)
        self.assertEqual(precise.load_active_frames([request]), {"a.mp4": ActiveFrame(720, 1080, 600, 0)})
        self.assertEqual(set(precise.load_results([request])), {"a.mp4"})
        again = precise.analyze_batch([request], crop_only=True)["a.mp4"]
        self.assertEqual(again.analysis_version, CROP_SCAN_VERSION)
        self.assertEqual(self.store.count(), 1)

    def test_manual_vr_or_metric_change_invalidates_cached_row(self) -> None:
        """VR 보호 설정이나 측정 지표가 캐시와 다르면 다시 분석함을 검증"""
        runner = FakeStderrRunner()
        service = self.make_service(runner)
        service.analyze_batch([_request("a.mp4")])
        runner.commands.clear()
        service.analyze_batch([_request("a.mp4", has_vr_metadata=True)])
        self.assertGreater(len(runner.commands), 0)
        runner.commands.clear()
        ssim_runner = FakeStderrRunner()
        ssim_service = self.make_service(ssim_runner, has_vmaf=False)
        ssim_service.analyze_batch([_request("a.mp4", has_vr_metadata=True)])
        self.assertGreater(len(ssim_runner.commands), 0)

    def test_analyze_creates_missing_work_dir_and_lists_results(self) -> None:
        """작업 폴더가 없으면 만들고, load_results가 캐시 결과만 돌려줌을 검증"""
        work_dir = os.path.join(self.tmp_dir, "nested", "work")
        service = FrameAnalysisService(
            self.store,
            work_dir=work_dir,
            has_vmaf=True,
            runner=FakeStderrRunner(),
            video_workers=1,
        )
        self.addCleanup(service.close)
        requests = [_request("a.mp4"), _request("missing.mp4", size=200, mtime=2)]
        service.analyze_batch([_request("a.mp4")])
        self.assertTrue(os.path.isdir(work_dir))
        self.assertEqual(set(service.load_results(requests)), {"a.mp4"})
        self.store.save(_record("old.mp4", version=0))
        self.assertEqual(service.load_results([_request("old.mp4")]), {})


    def test_mode_change_invalidates_cache_and_reanalyzes(self) -> None:
        """방식이 바뀌면 캐시 버전이 달라져 다시 재고 새 버전으로 저장하는지 검증"""
        service = self.make_service(FakeStderrRunner(), mode="balanced")
        service.analyze_batch([_request("a.mp4")])
        self.assertEqual(self.store.count(), 1)
        fast_runner = FakeStderrRunner()
        fast_service = self.make_service(fast_runner, mode="fast")
        self.assertEqual(fast_service.count_cached([_request("a.mp4")]), 0)
        fast_service.analyze_batch([_request("a.mp4")])
        self.assertGreater(len(fast_runner.commands), 0)
        record = self.store.load_many([("a.mp4", 100, 1)])[("a.mp4", 100, 1)]
        self.assertEqual(record.analysis_version, analysis_version_for_mode("fast"))

    def test_scale_algorithm_change_invalidates_cache_and_changes_filters(self) -> None:
        """보간 방식을 바꾸면 캐시를 다시 재고 필터에도 그 방식이 들어가는지 검증"""
        service = self.make_service(FakeStderrRunner(), scale_algorithm="bicubic")
        service.analyze_batch([_request("a.mp4")])
        self.assertEqual(service.count_cached([_request("a.mp4")]), 1)
        lanczos_runner = FakeStderrRunner()
        lanczos_service = self.make_service(lanczos_runner, scale_algorithm="lanczos")
        self.assertEqual(lanczos_service.count_cached([_request("a.mp4")]), 0)
        lanczos_service.analyze_batch([_request("a.mp4")])
        self.assertGreater(len(lanczos_runner.commands), 0)
        chains = [
            args[args.index("-filter_complex") + 1]
            for args in lanczos_runner.commands
            if "-filter_complex" in args
        ]
        self.assertTrue(chains)
        for chain in chains:
            with self.subTest(chain=chain):
                self.assertIn("flags=lanczos", chain)
                self.assertNotIn("flags=bicubic", chain)
        record = self.store.load_many([("a.mp4", 100, 1)])[("a.mp4", 100, 1)]
        self.assertEqual(
            record.analysis_version, analysis_version_for_mode("balanced", "lanczos")
        )

    def test_default_algorithm_keeps_the_existing_analysis_version(self) -> None:
        """기본 보간 방식은 기존 화질 분석 방식의 캐시 버전을 그대로 쓰는지 검증"""
        self.assertEqual(analysis_version_for_mode("balanced"), FRAME_ANALYSIS_VERSION)
        self.assertEqual(
            analysis_version_for_mode("balanced", "bicubic"), FRAME_ANALYSIS_VERSION
        )
        self.assertNotEqual(
            analysis_version_for_mode("balanced", "lanczos"),
            analysis_version_for_mode("balanced", "spline"),
        )

    def test_stage_callback_reports_current_video_and_stage(self) -> None:
        service = self.make_service(FakeStderrRunner())
        stages: list[tuple[str, str, int, int]] = []
        service.analyze_batch(
            [_request("a.mp4")],
            on_stage=lambda name, stage, done, total: stages.append((name, stage, done, total)),
        )
        self.assertTrue(stages)
        self.assertTrue(all(name == "a.mp4" for name, _stage, _done, _total in stages))
        self.assertEqual(stages[0][1], "scan")

    def test_parallel_video_workers_analyze_all_requests(self) -> None:
        runner = FakeStderrRunner(vmaf_by_tier={"360p": 99.6})
        service = self.make_service(runner, video_workers=2)
        results = service.analyze_batch([_request("a.mp4"), _request("b.mp4")])
        self.assertEqual(set(results), {"a.mp4", "b.mp4"})
        self.assertEqual(self.store.count(), 2)


class TestInnerRoundWorkers(unittest.TestCase):
    """영상 수와 실행기 주입 여부에 따른 내부 라운드 워커 수 검증"""

    def test_worker_count_by_video_count(self) -> None:
        self.assertEqual(_inner_round_workers(1, runner_injected=False), INNER_ROUND_WORKERS_SINGLE)
        self.assertEqual(_inner_round_workers(3, runner_injected=False), INNER_ROUND_WORKERS_FEW)
        self.assertEqual(_inner_round_workers(4, runner_injected=False), 1)
        self.assertEqual(_inner_round_workers(1, runner_injected=True), 1)


if __name__ == "__main__":
    unittest.main()
