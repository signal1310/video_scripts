"""화질 분석 결과 캐시 저장소의 저장, 조회, 영상별 지정과 정리 범위 검증"""

import os
import sqlite3
import tempfile
import time
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import replace
from unittest.mock import patch

from src.core import workspace_paths as paths_module
from src.core.analysis.frame_analysis import ActiveRegion
from src.core.models import ActiveFrame
from src.service.analysis_store import ANALYSIS_TABLE_NAME, AnalysisStore
from src.service.frame_analysis_store import (
    SQLITE_BUSY_TIMEOUT_SEC,
    FrameAnalysisStore,
    _is_locked_error,
)
from src.service.frame_override_rows import (
    review_region_box,
    validated_crop_exclusion_rows,
    validated_manual_rows,
)
from tests._frame_analysis_fakes import make_frame_record


class _StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_dir = tmp.name
        self.db_path = os.path.join(tmp.name, "video_cache.sqlite3")
        self.store = FrameAnalysisStore(self.db_path)
        self.addCleanup(self.store.close)


class TestFrameAnalysisStore(_StoreTestCase):
    """캐시 저장소의 저장, 조회, 분석 캐시와의 독립성 검증"""

    def test_save_and_load_roundtrip(self) -> None:
        record = make_frame_record("clip.mp4")
        self.store.save(record)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.load_many([record.key]), {record.key: record})
        loaded = next(iter(self.store.load_many([record.key]).values()))
        self.assertEqual(loaded.active_region.to_active_frame(), ActiveFrame(720, 1080, 600, 0))
        self.assertEqual(self.store.db_path, self.db_path)

    def test_load_many_without_keys_or_matches(self) -> None:
        self.assertEqual(self.store.load_many([]), {})
        self.assertEqual(self.store.load_many([("none.mp4", 1, 1)]), {})

    def test_analysis_store_clear_keeps_frame_cache(self) -> None:
        self.store.save(make_frame_record("clip.mp4"))
        analysis = AnalysisStore(self.db_path)
        self.addCleanup(analysis.close)
        self.assertEqual(analysis.clear(), 0)
        self.assertEqual(analysis.fetch_by_names(["clip.mp4"]), [])
        self.assertEqual(self.store.count(), 1)

    def test_analysis_schema_recreation_keeps_frame_cache(self) -> None:
        self.store.save(make_frame_record("clip.mp4"))
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"DROP TABLE IF EXISTS {ANALYSIS_TABLE_NAME}")
        conn.commit()
        conn.close()
        analysis = AnalysisStore(self.db_path)
        self.addCleanup(analysis.close)
        self.assertEqual(analysis.count(), 0)
        self.assertEqual(self.store.count(), 1)

    def test_close_then_reuse_reconnects(self) -> None:
        self.store.save(make_frame_record("clip.mp4"))
        self.store.close()
        self.assertEqual(self.store.count(), 1)

    def test_locked_error_detection(self) -> None:
        self.assertTrue(_is_locked_error(sqlite3.OperationalError("database is locked")))
        self.assertTrue(_is_locked_error(sqlite3.OperationalError("database table is busy")))
        self.assertFalse(_is_locked_error(sqlite3.OperationalError("disk I/O error")))

    def test_vr_override_roundtrip_and_cache_invalidation(self) -> None:
        """수동 VR 보호 저장, 조회, 해제와 캐시 삭제를 검증"""
        self.store.save(make_frame_record("clip.mp4"))
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
        self.assertEqual(self.store.load_manual_active_frames({"C:/work/clip.mp4": (1920, 1080)}), {})
        self.store.set_manual_active_frame(
            "C:/work/clip.mp4",
            ActiveFrame(1600, 1000, 260, 40),
            frame_width=1920,
            frame_height=1080,
        )
        self.assertEqual(
            self.store.load_manual_active_frames({"C:/work/clip.mp4": (1920, 1080)}),
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
        self.assertFalse(loaded[("clip.mp4", 100, 1)].crop_scan_failed)
        self.assertEqual(self.store.count(), 1)

    def test_margins_vary_flag_roundtrips_through_sqlite(self) -> None:
        record = replace(make_frame_record("clip.mp4"), margins_vary=True)
        self.store.save(record)
        self.assertTrue(self.store.load_many([record.key])[record.key].margins_vary)

    def test_crop_scan_failed_flag_roundtrips_through_sqlite(self) -> None:
        """감지 실패 표시가 캐시에 저장되고 그대로 복원되는지 검증"""
        record = replace(make_frame_record("clip.mp4"), unmeasurable=True, crop_scan_failed=True)
        self.store.save(record)
        self.assertTrue(self.store.load_many([record.key])[record.key].crop_scan_failed)

    def test_dark_outliers_ignored_column_is_added_to_an_old_database(self) -> None:
        """어두운 근거 무시 열이 없는 이전 DB에서 열을 덧붙이고 기존 행은 기본값 거짓으로 읽음"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE frame_analysis_cache ("
            "filename TEXT NOT NULL, filesize_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, "
            "active_width INTEGER NOT NULL, active_height INTEGER NOT NULL, active_x INTEGER NOT NULL, "
            "active_y INTEGER NOT NULL, has_black_bars INTEGER NOT NULL, metric_used TEXT NOT NULL, "
            "vmaf_model TEXT NOT NULL, tier_scores_json TEXT NOT NULL, candidate_tier TEXT, "
            "valid_segments INTEGER NOT NULL, total_segments INTEGER NOT NULL, "
            "unmeasurable INTEGER NOT NULL, is_vr_protected INTEGER NOT NULL, measured_at TEXT NOT NULL, "
            "analysis_version INTEGER NOT NULL, margins_vary INTEGER NOT NULL DEFAULT 0, "
            "PRIMARY KEY (filename, filesize_bytes, mtime_ns)"
            ") WITHOUT ROWID"
        )
        conn.execute(
            "INSERT INTO frame_analysis_cache VALUES "
            "('clip.mp4', 100, 1, 720, 1080, 600, 0, 1, 'vmaf', 'vmaf_v0.6.1', '[]', NULL, "
            "9, 9, 0, 0, '2026-01-01T00:00:00Z', 233, 1)"
        )
        conn.commit()
        conn.close()
        loaded = self.store.load_many([("clip.mp4", 100, 1)])[("clip.mp4", 100, 1)]
        self.assertFalse(loaded.dark_outliers_ignored)
        self.assertTrue(loaded.margins_vary)
        self.assertEqual(self.store.count(), 1)

    def test_dark_outliers_ignored_flag_roundtrips_through_sqlite(self) -> None:
        record = replace(make_frame_record("clip.mp4"), dark_outliers_ignored=True)
        self.store.save(record)
        self.assertTrue(self.store.load_many([record.key])[record.key].dark_outliers_ignored)


class TestFrameStoreChangedFileIdentity(_StoreTestCase):
    """원본 파일이 바뀐 뒤 남은 사용자 지정 활성 화면과 잘라내기 제외를 버리는지"""

    def setUp(self) -> None:
        super().setUp()
        self.clip = os.path.join(self.tmp_dir, "clip.mp4")
        with open(self.clip, "wb") as handle:
            handle.write(b"v" * 4096)

    def _replace_clip(self) -> None:
        """같은 경로의 영상을 다른 내용으로 바꿔 크기와 수정 시각을 모두 바꿈"""
        with open(self.clip, "wb") as handle:
            handle.write(b"w" * 1024)

    def test_manual_frame_survives_unchanged_file(self) -> None:
        """저장 뒤 파일이 그대로면 같은 활성 화면을 다시 읽음"""
        manual = ActiveFrame(1600, 1000, 260, 40)
        self.store.set_manual_active_frame(
            self.clip, manual, frame_width=1920, frame_height=1080
        )
        self.assertEqual(
            self.store.load_manual_active_frames({self.clip: (1920, 1080)}),
            {self.clip: manual},
        )

    def test_manual_frame_is_ignored_when_the_file_changes(self) -> None:
        """같은 경로의 영상이 바뀌면 저장된 활성 화면을 쓰지 않되 행은 지우지 않음

        - 원본 교체 인코딩 뒤에는 경로의 파일이 결과이고 보관 원본을 띄우는 비교가 이 행을 씀
        - 남은 행은 적용되지 않다가 그 영상에 다시 지정하면 덮임
        """
        self.store.set_manual_active_frame(
            self.clip, ActiveFrame(1600, 1000, 260, 40), frame_width=1920, frame_height=1080
        )
        self._replace_clip()
        self.assertEqual(self.store.load_manual_active_frames({self.clip: (1920, 1080)}), {})
        self.assertEqual(self.store.count_manual_active_frames(), 1)

    def test_manual_frame_out_of_bounds_for_current_frame_is_dropped(self) -> None:
        """지금 프레임 크기를 벗어난 사각형은 읽을 때 버리고 행도 지움"""
        self.store.set_manual_active_frame(
            self.clip, ActiveFrame(1600, 1000, 260, 40), frame_width=1920, frame_height=1080
        )
        self.assertEqual(self.store.load_manual_active_frames({self.clip: (1280, 720)}), {})
        self.assertEqual(self.store.count_manual_active_frames(), 0)

    def test_manual_frame_odd_rectangle_is_normalized_on_read(self) -> None:
        """읽을 때도 같은 정규화를 거쳐 짝수 좌표와 크기로 맞춤"""
        self.assertEqual(self.store.count_manual_active_frames(), 0)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO frame_analysis_manual_active_frame"
            " (source_path, manual_x, manual_y, manual_width, manual_height)"
            " VALUES (?, 261, 41, 1601, 999)",
            (self.clip,),
        )
        conn.commit()
        conn.close()
        self.assertEqual(
            self.store.load_manual_active_frames({self.clip: (1920, 1080)}),
            {self.clip: ActiveFrame(1600, 998, 260, 40)},
        )

    def test_manual_frame_from_an_old_schema_is_kept_and_stamped(self) -> None:
        """식별 값 열이 없던 DB의 행은 한 번 유효하게 읽고 지금 파일 값으로 채움"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE frame_analysis_manual_active_frame ("
            "source_path TEXT PRIMARY KEY, manual_x INTEGER NOT NULL, "
            "manual_y INTEGER NOT NULL, manual_width INTEGER NOT NULL, "
            "manual_height INTEGER NOT NULL) WITHOUT ROWID"
        )
        conn.execute(
            "INSERT INTO frame_analysis_manual_active_frame VALUES (?, 260, 40, 1600, 1000)",
            (self.clip,),
        )
        conn.commit()
        conn.close()
        manual = ActiveFrame(1600, 1000, 260, 40)
        self.assertEqual(
            self.store.load_manual_active_frames({self.clip: (1920, 1080)}),
            {self.clip: manual},
        )
        stat = os.stat(self.clip)
        conn = sqlite3.connect(self.db_path)
        stored = conn.execute(
            "SELECT filesize_bytes, mtime_ns FROM frame_analysis_manual_active_frame"
        ).fetchone()
        conn.close()
        self.assertEqual(stored, (stat.st_size, stat.st_mtime_ns))
        self._replace_clip()
        self.assertEqual(self.store.load_manual_active_frames({self.clip: (1920, 1080)}), {})

    def test_manual_frame_with_unknown_frame_size_is_kept(self) -> None:
        """프레임 크기를 모르는 채로 조회해도 사용자 지정 활성 화면을 지우지 않음"""
        manual = ActiveFrame(1600, 1000, 260, 40)
        self.store.set_manual_active_frame(
            self.clip, manual, frame_width=1920, frame_height=1080
        )
        self.assertEqual(
            self.store.load_manual_active_frames({self.clip: (0, 0)}), {self.clip: manual}
        )
        self.assertEqual(self.store.count_manual_active_frames(), 1)

    def test_manual_frame_of_a_deleted_file_survives_the_lookup(self) -> None:
        """파일을 지운 뒤 조회해도 사용자 지정 활성 화면 행을 지우지 않음"""
        manual = ActiveFrame(1600, 1000, 260, 40)
        self.store.set_manual_active_frame(
            self.clip, manual, frame_width=1920, frame_height=1080
        )
        os.remove(self.clip)
        self.assertEqual(
            self.store.load_manual_active_frames({self.clip: (1280, 720)}), {self.clip: manual}
        )
        self.assertEqual(self.store.count_manual_active_frames(), 1)

    def test_manual_frame_read_survives_a_failing_cleanup_write(self) -> None:
        """정리 쓰기가 실패해도 조회는 걸러 낸 결과를 돌려주고 한 줄 경고만 남김"""
        self.store.set_manual_active_frame(
            self.clip, ActiveFrame(1600, 1000, 260, 40), frame_width=1920, frame_height=1080
        )
        with (
            patch(
                "src.service.frame_analysis_store.write_row_changes",
                side_effect=sqlite3.OperationalError("attempt to write a readonly database"),
            ),
            self.assertLogs("src.service.frame_analysis_store", level="WARNING") as logs,
        ):
            self.assertEqual(self.store.load_manual_active_frames({self.clip: (1280, 720)}), {})
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(self.store.count_manual_active_frames(), 1)

    def test_crop_exclusion_read_survives_a_failing_cleanup_write(self) -> None:
        """잘라내기 제외 조회도 정리 쓰기 실패와 무관하게 결과를 돌려줌"""
        self.store.set_crop_exclusion(self.clip, True)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE frame_analysis_crop_exclude SET filesize_bytes = NULL, mtime_ns = NULL"
        )
        conn.commit()
        conn.close()
        with (
            patch(
                "src.service.frame_analysis_store.write_row_changes",
                side_effect=sqlite3.OperationalError("attempt to write a readonly database"),
            ),
            self.assertLogs("src.service.frame_analysis_store", level="WARNING"),
        ):
            self.assertEqual(
                self.store.load_crop_exclusions([self.clip]), frozenset({self.clip})
            )
        self.assertEqual(self.store.count_crop_exclusions(), 1)

    def test_manual_frame_read_returns_when_a_writer_holds_the_lock(self) -> None:
        """다른 연결이 쓰기 잠금을 잡고 있어 정리가 막혀도 짧게 포기하고 걸러 낸 결과를 돌려줌

        - 조회는 화면 스레드에서 돌므로 분석 워커의 쓰기를 오래 기다리지 않아야 함
        - 정리하지 못한 행은 지우지 않고 남겨 다음 조회에서 다시 시도
        """
        self.store.set_manual_active_frame(
            self.clip, ActiveFrame(1600, 1000, 260, 40), frame_width=1920, frame_height=1080
        )
        blocker = sqlite3.connect(self.db_path)
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN IMMEDIATE")
        self.addCleanup(
            lambda: blocker.execute("ROLLBACK") if blocker.in_transaction else None
        )
        started = time.monotonic()
        with self.assertLogs("src.service.frame_analysis_store", level="WARNING") as logs:
            result = self.store.load_manual_active_frames({self.clip: (1280, 720)})
        elapsed = time.monotonic() - started
        self.assertEqual(result, {})
        self.assertEqual(len(logs.records), 1)
        self.assertLess(elapsed, SQLITE_BUSY_TIMEOUT_SEC)
        blocker.execute("ROLLBACK")
        self.assertEqual(self.store.count_manual_active_frames(), 1)

    def test_crop_exclusion_is_ignored_when_the_file_changes(self) -> None:
        """잘라내기 제외도 같은 경로의 영상이 바뀌면 쓰지 않되 행은 지우지 않음"""
        self.store.set_crop_exclusion(self.clip, True)
        self.assertEqual(self.store.load_crop_exclusions([self.clip]), frozenset({self.clip}))
        self._replace_clip()
        self.assertEqual(self.store.load_crop_exclusions([self.clip]), frozenset())
        self.assertEqual(self.store.count_crop_exclusions(), 1)

    def test_manual_frame_drawn_before_a_replace_encode_applies_to_the_archived_original(
        self,
    ) -> None:
        """원본 교체 인코딩 뒤 정상 조회는 행을 건너뛰고, 보관 원본 식별 값으로 조회하면 적용됨

        - 정상 조회가 행을 지우면 보관 원본을 띄우는 비교가 그 지정을 잃음
        """
        manual = ActiveFrame(1600, 1000, 260, 40)
        self.store.set_manual_active_frame(self.clip, manual, frame_width=1920, frame_height=1080)
        stored_size, stored_mtime = os.stat(self.clip).st_size, os.stat(self.clip).st_mtime_ns
        self._replace_clip()
        self.assertEqual(self.store.load_manual_active_frames({self.clip: (1920, 1080)}), {})
        self.assertEqual(self.store.count_manual_active_frames(), 1)
        self.assertEqual(
            self.store.load_manual_active_frames(
                {self.clip: (1920, 1080)},
                identity_overrides={self.clip: (stored_size, stored_mtime)},
            ),
            {self.clip: manual},
        )
        self.assertEqual(self.store.count_manual_active_frames(), 1)

    def test_crop_exclusion_uses_the_override_identity_of_the_rendered_file(self) -> None:
        """다른 파일을 띄우는 조회는 그 파일의 식별 값과 맞는 행을 쓰고, 맞지 않는 행은 지우지 않음"""
        self.store.set_crop_exclusion(self.clip, True)
        stat = os.stat(self.clip)
        original = (stat.st_size, stat.st_mtime_ns)
        self.assertEqual(
            self.store.load_crop_exclusions([self.clip], identity_overrides={self.clip: original}),
            frozenset({self.clip}),
        )
        mismatched = (stat.st_size + 1, stat.st_mtime_ns)
        self.assertEqual(
            self.store.load_crop_exclusions(
                [self.clip], identity_overrides={self.clip: mismatched}
            ),
            frozenset(),
        )
        self.assertEqual(self.store.count_crop_exclusions(), 1)
        self.assertEqual(self.store.load_crop_exclusions([self.clip]), frozenset({self.clip}))

    def test_manual_frame_uses_the_override_identity_of_the_rendered_file(self) -> None:
        """사용자 지정 활성 화면도 띄우는 파일의 식별 값과 맞으면 쓰고, 맞지 않으면 지우지 않고 건너뜀"""
        manual = ActiveFrame(1600, 1000, 260, 40)
        self.store.set_manual_active_frame(
            self.clip, manual, frame_width=1920, frame_height=1080
        )
        stat = os.stat(self.clip)
        original = (stat.st_size, stat.st_mtime_ns)
        self.assertEqual(
            self.store.load_manual_active_frames(
                {self.clip: (1920, 1080)}, identity_overrides={self.clip: original}
            ),
            {self.clip: manual},
        )
        mismatched = (stat.st_size + 1, stat.st_mtime_ns)
        self.assertEqual(
            self.store.load_manual_active_frames(
                {self.clip: (1920, 1080)}, identity_overrides={self.clip: mismatched}
            ),
            {},
        )
        self.assertEqual(self.store.count_manual_active_frames(), 1)
        self.assertEqual(
            self.store.load_manual_active_frames({self.clip: (1920, 1080)}),
            {self.clip: manual},
        )

    def test_crop_review_ack_of_a_result_file_survives_an_archived_original_lookup(self) -> None:
        """원본 보관/교체 모드의 보관 원본 식별 값으로 조회해도 결과 파일의 확인 표시는 남음

        - 화질 비교 창을 열기만 해도 확인 표시가 지워지면 여백 확인 필요 경고가 표에 다시 나타남
        """
        region = ActiveRegion(1600, 1000, 260, 40, has_black_bars=True, dark_outliers_ignored=True)
        box = review_region_box(region)
        self.store.set_crop_review_acknowledged(self.clip, region)
        stat = os.stat(self.clip)
        archive_identity = (stat.st_size + 1, stat.st_mtime_ns)
        self.assertEqual(
            self.store.load_crop_review_acknowledged(
                {self.clip: box}, identity_overrides={self.clip: archive_identity}
            ),
            frozenset(),
        )
        self.assertEqual(
            self.store.load_crop_review_acknowledged({self.clip: box}), frozenset({self.clip})
        )

    def test_crop_review_ack_is_dropped_for_a_changed_file_or_a_changed_region(self) -> None:
        """같은 경로의 영상이 바뀌거나 새 분석이 다른 영역을 내면 확인 표시를 버림"""
        region = ActiveRegion(1600, 1000, 260, 40, has_black_bars=True, dark_outliers_ignored=True)
        box = review_region_box(region)
        self.store.set_crop_review_acknowledged(self.clip, region)
        self._replace_clip()
        self.assertEqual(self.store.load_crop_review_acknowledged({self.clip: box}), frozenset())

        self.store.set_crop_review_acknowledged(self.clip, region)
        moved = ActiveRegion(1600, 1000, 300, 40, has_black_bars=True, dark_outliers_ignored=True)
        self.assertEqual(
            self.store.load_crop_review_acknowledged({self.clip: review_region_box(moved)}),
            frozenset(),
        )
        # 지운 행은 다시 저장하지 않는 한 같은 영역으로 조회해도 살아나지 않음
        self.assertEqual(self.store.load_crop_review_acknowledged({self.clip: box}), frozenset())

    def test_crop_exclusion_from_an_old_schema_is_kept_and_stamped(self) -> None:
        """식별 값 열이 없던 잘라내기 제외 행도 한 번 유효하게 읽고 지금 파일 값으로 채움"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE frame_analysis_crop_exclude ("
            "source_path TEXT PRIMARY KEY, exclude_crop INTEGER NOT NULL) WITHOUT ROWID"
        )
        conn.execute(
            "INSERT INTO frame_analysis_crop_exclude VALUES (?, 1)",
            (self.clip,),
        )
        conn.commit()
        conn.close()
        self.assertEqual(self.store.load_crop_exclusions([self.clip]), frozenset({self.clip}))
        self._replace_clip()
        self.assertEqual(self.store.load_crop_exclusions([self.clip]), frozenset())
        self.assertEqual(self.store.count_crop_exclusions(), 1)

    def test_crop_exclusion_from_an_old_schema_stays_null_when_read_with_an_override(self) -> None:
        """예전 스키마 잘라내기 제외 행은 다른 파일을 띄우는 조회에서도 식별 값이 NULL로 남음

        - 보관 원본의 식별 값으로 채우면 다음 정상 조회가 결과 파일과 비교해 이 행을 무시함
        """
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE frame_analysis_crop_exclude ("
            "source_path TEXT PRIMARY KEY, exclude_crop INTEGER NOT NULL,"
            " filesize_bytes INTEGER, mtime_ns INTEGER) WITHOUT ROWID"
        )
        conn.execute(
            "INSERT INTO frame_analysis_crop_exclude VALUES (?, 1, NULL, NULL)",
            (self.clip,),
        )
        conn.commit()
        conn.close()
        stat = os.stat(self.clip)
        archive_identity = (stat.st_size + 1, stat.st_mtime_ns)
        self.assertEqual(
            self.store.load_crop_exclusions(
                [self.clip], identity_overrides={self.clip: archive_identity}
            ),
            frozenset({self.clip}),
        )
        conn = sqlite3.connect(self.db_path)
        stored = conn.execute(
            "SELECT filesize_bytes, mtime_ns FROM frame_analysis_crop_exclude"
        ).fetchone()
        conn.close()
        self.assertEqual(stored, (None, None))
        self.assertEqual(self.store.load_crop_exclusions([self.clip]), frozenset({self.clip}))

    def test_manual_frame_with_one_identity_column_is_ignored_and_kept(self) -> None:
        """식별 값이 한 열만 있는 행은 정상 조회에서 바뀐 행처럼 건너뛰되 지우지 않음(채우지 않음)"""
        self.store.set_manual_active_frame(
            self.clip, ActiveFrame(1600, 1000, 260, 40), frame_width=1920, frame_height=1080
        )
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE frame_analysis_manual_active_frame SET mtime_ns = NULL")
        conn.commit()
        conn.close()
        self.assertEqual(self.store.load_manual_active_frames({self.clip: (1920, 1080)}), {})
        self.assertEqual(self.store.count_manual_active_frames(), 1)

    def test_manual_frame_saved_after_the_select_survives_cleanup(self) -> None:
        """조회 뒤 같은 경로에 새로 저장한 활성 화면을 정리 삭제가 지우지 않음"""
        self.store.set_manual_active_frame(
            self.clip, ActiveFrame(1600, 1000, 260, 40), frame_width=1920, frame_height=1080
        )
        fresh = ActiveFrame(1280, 720, 0, 180)

        def _validate_then_save(
            rows: Sequence[Sequence[object]],
            frame_sizes: Mapping[str, tuple[int, int]],
            *,
            identity_overrides: Mapping[str, tuple[int, int]] | None = None,
        ) -> tuple[dict[str, ActiveFrame], list[Sequence[object]], list[tuple[int, int, str]]]:
            result = validated_manual_rows(
                rows, frame_sizes, identity_overrides=identity_overrides
            )
            self.store.set_manual_active_frame(
                self.clip, fresh, frame_width=1920, frame_height=1080
            )
            return result

        with patch(
            "src.service.frame_analysis_store.validated_manual_rows",
            side_effect=_validate_then_save,
        ):
            self.assertEqual(self.store.load_manual_active_frames({self.clip: (1280, 720)}), {})
        self.assertEqual(self.store.count_manual_active_frames(), 1)
        self.assertEqual(
            self.store.load_manual_active_frames({self.clip: (1920, 1080)}), {self.clip: fresh}
        )

    def test_crop_exclusion_saved_after_the_select_survives_cleanup(self) -> None:
        """조회 뒤 같은 경로에 새로 저장한 잘라내기 제외를 정리 삭제가 지우지 않음"""
        self.store.set_crop_exclusion(self.clip, True)
        self._replace_clip()
        original = validated_crop_exclusion_rows

        def _validate_then_save(
            rows: Sequence[Sequence[object]],
            *,
            identity_overrides: Mapping[str, tuple[int, int]] | None = None,
        ) -> tuple[list[str], list[tuple[int, int, str]]]:
            result = original(rows, identity_overrides=identity_overrides)
            self.store.set_crop_exclusion(self.clip, True)
            return result

        with patch(
            "src.service.frame_analysis_store.validated_crop_exclusion_rows",
            side_effect=_validate_then_save,
        ):
            self.assertEqual(self.store.load_crop_exclusions([self.clip]), frozenset())
        self.assertEqual(self.store.count_crop_exclusions(), 1)
        self.assertEqual(self.store.load_crop_exclusions([self.clip]), frozenset({self.clip}))


class TestFrameStoreScopedCleanup(_StoreTestCase):
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
        self.store.save(make_frame_record("a.mp4"))
        self.store.save(make_frame_record("root_dir_b/b.mp4"))
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
        self.store.save(make_frame_record("only_a.mp4"))
        self.store.save(make_frame_record("only_b.mp4"))
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
            self.store.load_manual_active_frames({self.crop_b: (1920, 1080)}), {self.crop_b: manual}
        )
        self.assertEqual(self.store.count_manual_active_frames(), 1)
        self.assertEqual(self.store.clear_manual_active_frames(), 1)
        self.assertEqual(self.store.count_manual_active_frames(), 0)

    def test_clearing_frame_cache_keeps_per_video_overrides(self) -> None:
        self.store.save(make_frame_record("a.mp4"))
        self.store.set_crop_exclusion(self.crop_a, True)
        self.store.set_vr_override(source_path=self.vr_a, filename="vr.mp4", manual_vr=True)
        self.assertEqual(self.store.clear_for_roots(), 1)
        self.assertEqual(self.store.load_crop_exclusions([self.crop_a]), frozenset({self.crop_a}))
        self.assertEqual(self.store.load_vr_overrides([self.vr_a]), frozenset({self.vr_a}))


class TestFrameStoreScopedCleanupCost(_StoreTestCase):
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
            self.store.save(make_frame_record(f"clip_{index}.mp4"))
        with (
            patch.object(paths_module, "parse_root_dirs", wraps=paths_module.parse_root_dirs) as parse,
            patch.object(paths_module.os.path, "isfile", wraps=os.path.isfile) as isfile,
            patch.object(paths_module.os, "scandir", wraps=os.scandir) as scandir,
        ):
            self.assertEqual(self.store.count_for_roots([self.root_a]), 0)
        self.assertLessEqual(parse.call_count, 2)
        self.assertEqual(isfile.call_count, 0)
        self.assertEqual(scandir.call_count, 1)
