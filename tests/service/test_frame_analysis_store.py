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
from src.core.analysis.file_fingerprint import compute_fingerprint
from src.core.analysis.frame_analysis import ActiveRegion
from src.core.models import ActiveFrame, VideoProps
from src.service.analysis_store import ANALYSIS_TABLE_NAME, AnalysisStore, StoredEntry
from src.service.frame_analysis_store import (
    FRAME_ANALYSIS_LEGACY_TABLE,
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
        self.assertEqual(
            self.store.load_many([record.content_key]), {record.content_key: record}
        )
        loaded = next(iter(self.store.load_many([record.content_key]).values()))
        self.assertEqual(loaded.active_region.to_active_frame(), ActiveFrame(720, 1080, 600, 0))
        self.assertEqual(self.store.db_path, self.db_path)

    def test_load_many_without_keys_or_matches(self) -> None:
        self.assertEqual(self.store.load_many([]), {})
        self.assertEqual(self.store.load_many([(1, "missing")]), {})

    def test_same_content_under_two_names_shares_one_row(self) -> None:
        """이름이 달라도 내용 키가 같으면 한 행을 쓰고, 나중에 저장한 대표 파일명을 남김"""
        first = make_frame_record("a.mp4", fingerprint="a" * 32)
        self.store.save(first)
        self.store.save(replace(make_frame_record("b.mp4", fingerprint="a" * 32)))
        self.assertEqual(self.store.count(), 1)
        key = first.content_key
        self.assertEqual(self.store.load_many([key])[key].filename, "b.mp4")

    def test_same_name_size_and_mtime_with_different_content_stays_separate(self) -> None:
        """이름, 크기, 수정 시각이 같아도 내용이 다르면 다른 행으로 분리해 각자의 활성 화면을 남김

        - 같은 폴더가 아닌 두 영상이 한 행을 공유하면 두 번째 영상이 첫 번째의 활성 화면으로 잘림
        """
        first = make_frame_record("a.mp4", fingerprint="a" * 32)
        second = replace(
            make_frame_record("b.mp4", fingerprint="b" * 32),
            active_width=1920,
            active_height=1080,
            active_x=0,
            active_y=140,
        )
        self.store.save(first)
        self.store.save(second)
        self.assertEqual(self.store.count(), 2)
        loaded = self.store.load_many([first.content_key, second.content_key])
        self.assertEqual(loaded[first.content_key].active_region.width, 720)
        self.assertEqual(loaded[second.content_key].active_region.y, 140)

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


    def _write_legacy_table(self, *, with_margins: bool) -> None:
        """옛 파일명 키 화질 분석 표 한 행을 만듬"""
        extra = ", margins_vary INTEGER NOT NULL DEFAULT 0" if with_margins else ""
        values = (
            "('clip.mp4', 100, 1, 720, 1080, 600, 0, 1, 'vmaf', 'vmaf_v0.6.1', '[]', NULL, "
            "9, 9, 0, 0, '2026-01-01T00:00:00Z', 202" + (", 1)" if with_margins else ")")
        )
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE frame_analysis_cache ("
            "filename TEXT NOT NULL, filesize_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, "
            "active_width INTEGER NOT NULL, active_height INTEGER NOT NULL, active_x INTEGER NOT NULL, "
            "active_y INTEGER NOT NULL, has_black_bars INTEGER NOT NULL, metric_used TEXT NOT NULL, "
            "vmaf_model TEXT NOT NULL, tier_scores_json TEXT NOT NULL, candidate_tier TEXT, "
            "valid_segments INTEGER NOT NULL, total_segments INTEGER NOT NULL, "
            "unmeasurable INTEGER NOT NULL, is_vr_protected INTEGER NOT NULL, measured_at TEXT NOT NULL, "
            "analysis_version INTEGER NOT NULL" + extra + ", "
            "PRIMARY KEY (filename, filesize_bytes, mtime_ns)"
            ") WITHOUT ROWID"
        )
        conn.execute(f"INSERT INTO frame_analysis_cache VALUES {values}")
        conn.commit()
        conn.close()
        # - 지금 연결이 이전에 표를 만들지 않았을 수 있으므로 옛 표를 쓴 뒤 열어 마이그레이션을 태움
        self.store.count()

    def test_old_filename_key_table_is_kept_until_its_file_is_known(self) -> None:
        """옛 파일명 키 행은 지문을 알기 전까지 버리지 않고 옛 키 표에 남김"""
        self._write_legacy_table(with_margins=False)
        self.assertEqual(self.store.count(), 1)
        conn = sqlite3.connect(self.db_path)
        stored = conn.execute(
            f"SELECT filename, filesize_bytes, mtime_ns, fingerprint FROM {FRAME_ANALYSIS_LEGACY_TABLE}"
        ).fetchall()
        conn.close()
        self.assertEqual(stored, [("clip.mp4", 100, 1, None)])
        self.assertEqual(self.store.load_many([(100, make_frame_record("clip.mp4").fingerprint)]), {})

    def test_old_filename_key_row_moves_when_the_file_is_found(self) -> None:
        """같은 폴더의 파일을 찾으면 지문을 채워 내용 행으로 옮기고 옛 행을 지움"""
        clip = os.path.join(self.tmp_dir, "clip.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"c" * 100)
        stat = os.stat(clip)
        self._write_legacy_table(with_margins=False)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            f"UPDATE {FRAME_ANALYSIS_LEGACY_TABLE} SET filesize_bytes = ?, mtime_ns = ?",
            (stat.st_size, stat.st_mtime_ns),
        )
        conn.commit()
        conn.close()
        key = ("clip.mp4", stat.st_size, stat.st_mtime_ns)
        fingerprint = compute_fingerprint(clip, stat.st_size)
        self.assertEqual(self.store.resolve_content_keys([(key, clip)]), {key: fingerprint})
        self.assertEqual(self.store.count(), 1)
        loaded = self.store.load_many([(stat.st_size, fingerprint)])
        self.assertEqual(loaded[(stat.st_size, fingerprint)].filename, "clip.mp4")
        conn = sqlite3.connect(self.db_path)
        remaining = conn.execute(f"SELECT COUNT(*) FROM {FRAME_ANALYSIS_LEGACY_TABLE}").fetchone()[0]
        conn.close()
        self.assertEqual(remaining, 0)

    def test_old_filename_key_row_moves_with_a_stored_fingerprint(self) -> None:
        """분석 캐시의 파일명 키 행에 같은 파일의 지문이 있으면 파일을 읽지 않고 옮김"""
        analysis = AnalysisStore(self.db_path)
        self.addCleanup(analysis.close)
        props = VideoProps(
            filename="clip.mp4",
            width=1920,
            height=1080,
            rotate_type=0,
            fps=30.0,
            vid_kbps=4000,
            aud_kbps=128,
            vid_size_MB=300.0,
            duration=600.0,
            codec="hevc",
        )
        analysis.apply(
            [],
            [StoredEntry("clip.mp4", 100, 1, "d" * 32, props)],
        )
        self._write_legacy_table(with_margins=False)
        restart = FrameAnalysisStore(self.db_path)
        self.addCleanup(restart.close)
        self.assertEqual(restart.count(), 1)
        self.assertEqual(
            restart.load_many([(100, "d" * 32)])[(100, "d" * 32)].filename, "clip.mp4"
        )

    def test_old_table_without_margins_column_keeps_rows(self) -> None:
        """구간별 여백 열이 없던 옛 표에서도 행을 잃지 않고 기본값 거짓으로 옮김"""
        self._write_legacy_table(with_margins=False)
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"UPDATE {FRAME_ANALYSIS_LEGACY_TABLE} SET fingerprint = 'e'")
        conn.commit()
        conn.close()
        restart = FrameAnalysisStore(self.db_path)
        self.addCleanup(restart.close)
        loaded = restart.load_many([(100, "e")])[(100, "e")]
        self.assertFalse(loaded.margins_vary)
        self.assertFalse(loaded.crop_scan_failed)
        self.assertEqual(restart.count(), 1)

    def test_margins_vary_flag_roundtrips_through_sqlite(self) -> None:
        record = replace(make_frame_record("clip.mp4"), margins_vary=True)
        self.store.save(record)
        self.assertTrue(self.store.load_many([record.content_key])[record.content_key].margins_vary)

    def test_crop_scan_failed_flag_roundtrips_through_sqlite(self) -> None:
        """감지 실패 표시가 캐시에 저장되고 그대로 복원되는지 검증"""
        record = replace(make_frame_record("clip.mp4"), unmeasurable=True, crop_scan_failed=True)
        self.store.save(record)
        self.assertTrue(
            self.store.load_many([record.content_key])[record.content_key].crop_scan_failed
        )

    def test_dark_outliers_ignored_flag_roundtrips_through_sqlite(self) -> None:
        record = replace(make_frame_record("clip.mp4"), dark_outliers_ignored=True)
        self.store.save(record)
        self.assertTrue(
            self.store.load_many([record.content_key])[record.content_key].dark_outliers_ignored
        )


    def test_vr_override_invalidates_the_content_row_of_a_renamed_file(self) -> None:
        """이름이 바뀐 영상도 그 경로의 내용 행을 지워 다시 분석하게 하는지 검증

        - 캐시 행은 내용 키로 식별하므로 대표 파일명만 보고 지우면 이름이 바뀐 영상의 측정이 남음
        """
        clip = os.path.join(self.tmp_dir, "clip.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"r" * 64)
        stat = os.stat(clip)
        fingerprint = compute_fingerprint(clip, stat.st_size)
        self.store.save(
            make_frame_record(
                "renamed.mp4", size=stat.st_size, mtime=stat.st_mtime_ns, fingerprint=fingerprint
            )
        )
        self.assertEqual(self.store.count(), 1)
        self.store.set_vr_override(source_path=clip, filename="clip.mp4", manual_vr=True)
        self.assertEqual(self.store.count(), 0)

    def test_vr_override_keeps_the_measurement_of_a_same_name_different_file(self) -> None:
        """같은 이름의 다른 내용 행은 지우지 않는지 검증(내용 키로만 지움)"""
        self.store.save(make_frame_record("clip.mp4", fingerprint="a" * 32))
        self.store.save(make_frame_record("other.mp4", fingerprint="b" * 32))
        missing = os.path.join(self.tmp_dir, "missing.mp4")
        self.store.set_vr_override(source_path=missing, filename="clip.mp4", manual_vr=True)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(
            set(self.store.load_many([(100, "a" * 32), (100, "b" * 32)])),
            {(100, "b" * 32)},
        )


class TestFrameStorePromotionWrites(_StoreTestCase):
    """조회 경로의 승격 쓰기가 실패해도 지문을 돌려주고, 쓸 일이 없으면 쓰지 않는지"""

    def _write_legacy_row(self, *, filename: str, size: int, mtime: int) -> None:
        """옛 파일명 키 표에 지문이 빈 행 하나를 넣음"""
        self.store.count()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            f"INSERT INTO {FRAME_ANALYSIS_LEGACY_TABLE} "
            "(filename, filesize_bytes, mtime_ns, active_width, active_height, active_x, active_y,"
            " has_black_bars, metric_used, vmaf_model, tier_scores_json, candidate_tier,"
            " valid_segments, total_segments, unmeasurable, is_vr_protected, measured_at,"
            " analysis_version, margins_vary, dark_outliers_ignored, crop_scan_failed)"
            " VALUES (?, ?, ?, 720, 1080, 600, 0, 1, 'vmaf', 'vmaf_v0.6.1', '[]', NULL,"
            " 9, 9, 0, 0, '2026-01-01T00:00:00Z', 202, 0, 0, 0)",
            (filename, size, mtime),
        )
        conn.commit()
        conn.close()

    def test_fingerprints_are_returned_when_the_promotion_write_fails(self) -> None:
        """읽기 전용 DB처럼 승격 쓰기가 실패해도 찾은 지문은 그대로 돌려줌"""
        clip = os.path.join(self.tmp_dir, "clip.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"p" * 64)
        stat = os.stat(clip)
        self._write_legacy_row(filename="clip.mp4", size=stat.st_size, mtime=stat.st_mtime_ns)
        key = ("clip.mp4", stat.st_size, stat.st_mtime_ns)
        expected = compute_fingerprint(clip, stat.st_size)
        with (
            patch(
                "src.service.frame_analysis_store.record_legacy_fingerprints",
                side_effect=sqlite3.OperationalError("attempt to write a readonly database"),
            ),
            self.assertLogs("src.service.frame_analysis_store", level="WARNING"),
        ):
            self.assertEqual(self.store.resolve_content_keys([(key, clip)]), {key: expected})
        # 승격되지 않은 옛 행은 남겨 다음 조회에서 다시 시도
        conn = sqlite3.connect(self.db_path)
        remaining = conn.execute(f"SELECT COUNT(*) FROM {FRAME_ANALYSIS_LEGACY_TABLE}").fetchone()[0]
        conn.close()
        self.assertEqual(remaining, 1)

    def test_plain_reload_writes_nothing_when_nothing_is_pending(self) -> None:
        """옛 행이나 채울 지문이 없으면 순수 SELECT만 해 저장된 지문을 돌려줌"""
        clip = os.path.join(self.tmp_dir, "clip.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"q" * 64)
        stat = os.stat(clip)
        key = ("clip.mp4", stat.st_size, stat.st_mtime_ns)
        expected = compute_fingerprint(clip, stat.st_size)
        self.assertEqual(self.store.resolve_content_keys([(key, clip)]), {key: expected})
        statements: list[str] = []
        self.store._connection().set_trace_callback(statements.append)
        with (
            patch(
                "src.service.frame_analysis_store.record_legacy_fingerprints",
                side_effect=AssertionError("record_legacy_fingerprints must not run"),
            ) as record,
            patch(
                "src.service.frame_analysis_store.promote_legacy_rows",
                side_effect=AssertionError("promote_legacy_rows must not run"),
            ) as promote,
        ):
            self.assertEqual(self.store.resolve_content_keys([(key, clip)]), {key: expected})
        self.assertEqual(record.call_count, 0)
        self.assertEqual(promote.call_count, 0)
        self.assertFalse([text for text in statements if text.startswith(("BEGIN", "UPDATE", "INSERT", "DELETE"))])


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
        self.store.save(make_frame_record("a.mp4", fingerprint="a" * 32))
        self.store.save(make_frame_record("root_dir_b/b.mp4", fingerprint="b" * 32))
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
        self.store.save(make_frame_record("only_a.mp4", fingerprint="a" * 32))
        self.store.save(make_frame_record("only_b.mp4", fingerprint="b" * 32))
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

    def test_clearing_more_rows_than_one_lookup_chunk(self) -> None:
        """조회 묶음보다 많은 캐시 행을 한 SQL에 몰아 넣지 않고 묶음마다 지우는지 검증

        - SQL 변수 한도가 낮은 SQLite 빌드에서는 한 문장으로 지우면 `too many SQL variables`로 실패함
        """
        count = 5
        for index in range(count):
            self.store.save(make_frame_record(f"clip_{index}.mp4", fingerprint=f"{index:032x}"))
        self.assertEqual(self.store.count(), count)
        statements: list[str] = []
        self.store._connection().set_trace_callback(statements.append)
        with patch("src.service.frame_analysis_store._LOOKUP_CHUNK_SIZE", 2):
            self.assertEqual(self.store.clear_for_roots(), count)
        deletes = [text for text in statements if text.startswith("DELETE FROM frame_analysis_cache ")]
        self.assertEqual(len(deletes), 3)
        self.assertEqual(self.store.count(), 0)


class TestFrameStoreDesignationFollowsContent(_StoreTestCase):
    """영상별 지정이 이름 변경, 폴더 이동, 복사, 다른 드라이브로 옮겨도 내용을 따라가는지"""

    def setUp(self) -> None:
        super().setUp()
        roots = tempfile.TemporaryDirectory()
        self.addCleanup(roots.cleanup)
        self.root = os.path.join(roots.name, "work")
        os.makedirs(self.root)
        self.clip = os.path.join(self.root, "Solo cam - video 2.mp4")
        with open(self.clip, "wb") as handle:
            handle.write(b"v" * 4096)

    def _identity(self, path: str) -> tuple[int, str]:
        stat = os.stat(path)
        return (stat.st_size, compute_fingerprint(path, stat.st_size))

    def _copy(self, source: str, target: str) -> None:
        with open(source, "rb") as src, open(target, "wb") as dst:
            dst.write(src.read())

    def _crop_paths(self) -> set[str]:
        conn = sqlite3.connect(self.db_path)
        paths = {str(row[0]) for row in conn.execute("SELECT source_path FROM frame_analysis_crop_exclude")}
        conn.close()
        return paths

    def test_rename_moves_the_crop_exclusion_row(self) -> None:
        self.store.set_crop_exclusion(self.clip, True)
        renamed = os.path.join(self.root, "renamed.mp4")
        os.rename(self.clip, renamed)
        self.store.sync_designations({renamed: self._identity(renamed)})
        self.assertEqual(self._crop_paths(), {renamed})
        self.assertEqual(self.store.load_crop_exclusions([renamed]), frozenset({renamed}))

    def test_move_to_another_folder_and_drive_letter_keeps_the_row(self) -> None:
        self.store.set_crop_exclusion(self.clip, True)
        other = os.path.join(self.tmp_dir, "other", "nested")
        os.makedirs(other)
        moved = os.path.join(other, "moved.mp4")
        os.rename(self.clip, moved)
        self.store.sync_designations({moved: self._identity(moved)})
        self.assertEqual(self._crop_paths(), {moved})
        self.assertEqual(self.store.load_crop_exclusions([moved]), frozenset({moved}))

    def test_copy_gives_the_copy_an_independent_crop_exclusion(self) -> None:
        self.store.set_crop_exclusion(self.clip, True)
        copy_path = os.path.join(self.root, "copy.mp4")
        self._copy(self.clip, copy_path)
        self.store.sync_designations({copy_path: self._identity(copy_path)})
        self.assertEqual(self._crop_paths(), {self.clip, copy_path})
        self.assertEqual(self.store.load_crop_exclusions([self.clip, copy_path]), frozenset({self.clip, copy_path}))
        self.store.set_crop_exclusion(self.clip, False)
        self.assertEqual(self.store.load_crop_exclusions([copy_path]), frozenset({copy_path}))

    def test_designation_is_not_applied_when_a_same_size_file_differs(self) -> None:
        self.store.set_crop_exclusion(self.clip, True)
        other = os.path.join(self.root_other(), "same_size.mp4")
        os.makedirs(os.path.dirname(other), exist_ok=True)
        with open(other, "wb") as handle:
            handle.write(b"w" * 4096)
        self.store.sync_designations({other: self._identity(other)})
        self.assertEqual(self._crop_paths(), {self.clip})

    def test_manual_frame_and_vr_override_follow_a_rename(self) -> None:
        manual = ActiveFrame(1600, 1000, 260, 40)
        self.store.set_manual_active_frame(self.clip, manual, frame_width=1920, frame_height=1080)
        self.store.set_vr_override(source_path=self.clip, filename="Solo cam - video 2.mp4", manual_vr=True)
        renamed = os.path.join(self.root, "renamed.mp4")
        os.rename(self.clip, renamed)
        self.store.sync_designations({renamed: self._identity(renamed)})
        self.assertEqual(self.store.load_manual_active_frames({renamed: (1920, 1080)}), {renamed: manual})
        self.assertEqual(self.store.load_vr_overrides([renamed]), frozenset({renamed}))
        self.assertEqual(self.store.load_vr_overrides([self.clip]), frozenset())

    def test_same_name_size_and_mtime_with_other_content_is_not_applied(self) -> None:
        self.store.set_crop_exclusion(self.clip, True)
        stat = os.stat(self.clip)
        with open(self.clip, "wb") as handle:
            handle.write(b"w" * stat.st_size)
        os.utime(self.clip, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(os.stat(self.clip).st_mtime_ns, stat.st_mtime_ns)
        self.assertEqual(self.store.load_crop_exclusions([self.clip]), frozenset())
        self.assertEqual(self._crop_paths(), {self.clip})

    def test_same_content_copy_moves_after_the_source_is_gone(self) -> None:
        self.store.set_crop_exclusion(self.clip, True)
        elsewhere = os.path.join(self.root_other(), "copy.mp4")
        os.makedirs(os.path.dirname(elsewhere), exist_ok=True)
        self._copy(self.clip, elsewhere)
        identity = self._identity(elsewhere)
        os.remove(self.clip)
        self.store.sync_designations({elsewhere: identity})
        self.assertEqual(self._crop_paths(), {elsewhere})
        self.assertEqual(self.store.load_crop_exclusions([elsewhere]), frozenset({elsewhere}))

    def test_manual_frame_of_a_copy_elsewhere_is_applied(self) -> None:
        """보고 사례: 수동 활성 화면이 있는 영상의 같은 내용 복사본도 그 화면으로 판정됨"""
        manual = ActiveFrame(1600, 1000, 260, 40)
        self.store.set_manual_active_frame(self.clip, manual, frame_width=1920, frame_height=1080)
        other_root = self.root_other()
        os.makedirs(other_root, exist_ok=True)
        copy_path = os.path.join(other_root, "Solo cam - video 2.mp4")
        self._copy(self.clip, copy_path)
        self.store.sync_designations({copy_path: self._identity(copy_path)})
        self.assertEqual(
            self.store.load_manual_active_frames({copy_path: (1920, 1080)}),
            {copy_path: manual},
        )
        self.assertEqual(
            self.store.load_manual_active_frames({self.clip: (1920, 1080)}),
            {self.clip: manual},
        )

    def test_layout_backfills_the_content_key_of_an_old_row(self) -> None:
        """내용 키가 비어 있던 지정 행도 지문을 한 번 채워 새 경로를 따라갈 준비가 됨"""
        self.store.count()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO frame_analysis_crop_exclude (source_path, exclude_crop) VALUES (?, 1)",
            (self.clip,),
        )
        conn.commit()
        conn.close()
        stat = os.stat(self.clip)
        self.assertEqual(self.store.designation_sizes(), {stat.st_size})
        conn = sqlite3.connect(self.db_path)
        stored = conn.execute(
            "SELECT filesize_bytes, fingerprint FROM frame_analysis_crop_exclude WHERE source_path = ?",
            (self.clip,),
        ).fetchone()
        conn.close()
        self.assertEqual(stored[0], stat.st_size)
        self.assertIsNotNone(stored[1])

    def test_designation_sizes_lists_saved_sizes_only(self) -> None:
        self.assertEqual(self.store.designation_sizes(), set())
        self.store.set_crop_exclusion(self.clip, True)
        stat = os.stat(self.clip)
        self.assertEqual(self.store.designation_sizes(), {stat.st_size})

    def root_other(self) -> str:
        return os.path.join(self.tmp_dir, "another_root")

    def test_empty_scan_changes_nothing(self) -> None:
        self.store.set_crop_exclusion(self.clip, True)
        self.assertEqual(self.store.sync_designations({}), [])
        self.assertEqual(self._crop_paths(), {self.clip})

    def test_data_cleanup_counts_and_clears_rows_created_by_a_copy(self) -> None:
        self.store.set_crop_exclusion(self.clip, True)
        copy_path = os.path.join(self.root, "copy.mp4")
        with open(self.clip, "rb") as src, open(copy_path, "wb") as dst:
            dst.write(src.read())
        self.store.sync_designations({copy_path: self._identity(copy_path)})
        self.assertEqual(self.store.count_crop_exclusions([self.root]), 2)
        self.assertEqual(self.store.clear_crop_exclusions([self.root]), 2)
        self.assertEqual(self._crop_paths(), set())


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
            self.store.save(
                make_frame_record(f"clip_{index}.mp4", fingerprint=f"{index:032x}")
            )
        with (
            patch.object(paths_module, "parse_root_dirs", wraps=paths_module.parse_root_dirs) as parse,
            patch.object(paths_module.os.path, "isfile", wraps=os.path.isfile) as isfile,
            patch.object(paths_module.os, "scandir", wraps=os.scandir) as scandir,
        ):
            self.assertEqual(self.store.count_for_roots([self.root_a]), 0)
        self.assertLessEqual(parse.call_count, 2)
        self.assertEqual(isfile.call_count, 0)
        self.assertEqual(scandir.call_count, 1)
