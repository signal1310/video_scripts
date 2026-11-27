"""화질 분석 캐시 표 스키마와 옛 파일명 키 행 승격 검증"""

import os
import sqlite3
import tempfile
import unittest

from src.core.analysis.file_fingerprint import compute_fingerprint
from src.service.frame_analysis_schema import (
    CONTENT_KEY_COLUMN_NAMES,
    FRAME_ANALYSIS_LEGACY_TABLE,
    FRAME_ANALYSIS_TABLE,
    _content_table_is_current,
    computed_fingerprint,
    migrate_cache_table,
    prepare_cache_tables,
    promote_legacy_rows,
    record_legacy_fingerprints,
    stored_fingerprint,
    table_columns,
    table_exists,
)
from src.service.frame_analysis_store import FrameAnalysisStore
from tests._frame_analysis_fakes import make_frame_record


class _SchemaTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_dir = tmp.name
        self.db_path = os.path.join(tmp.name, "video_cache.sqlite3")
        self.conn = sqlite3.connect(self.db_path, isolation_level=None)
        self.addCleanup(self.conn.close)

    def _write_legacy_row(self, *, filename: str, size: int, mtime: int) -> None:
        """옛 파일명 키 표를 만들고 행 하나를 넣음"""
        migrate_cache_table(self.conn)
        self.conn.execute(
            f"INSERT INTO {FRAME_ANALYSIS_LEGACY_TABLE} "
            "(filename, filesize_bytes, mtime_ns, active_width, active_height, active_x, active_y,"
            " has_black_bars, metric_used, vmaf_model, tier_scores_json, candidate_tier,"
            " valid_segments, total_segments, unmeasurable, is_vr_protected, measured_at,"
            " analysis_version, margins_vary, dark_outliers_ignored, crop_scan_failed)"
            " VALUES (?, ?, ?, 1920, 1080, 0, 0, 0, 'vmaf', 'vmaf_v0.6.1', '[]', NULL,"
            " 9, 9, 0, 0, '2026-01-01T00:00:00Z', 202, 0, 0, 0)",
            (filename, size, mtime),
        )


class TestFrameAnalysisSchema(_SchemaTestCase):
    """표 생성, 열 확인, 옛 행 승격을 직접 호출로 검증"""

    def test_prepare_cache_tables_creates_content_key_table(self) -> None:
        prepare_cache_tables(self.conn)
        self.assertTrue(table_exists(self.conn, FRAME_ANALYSIS_TABLE))
        self.assertTrue(table_exists(self.conn, FRAME_ANALYSIS_LEGACY_TABLE))
        columns = table_columns(self.conn, FRAME_ANALYSIS_TABLE)
        key_columns = tuple(name for name, (_type, _not_null, position) in columns.items() if position > 0)
        self.assertEqual(key_columns, CONTENT_KEY_COLUMN_NAMES)
        # - 지문은 내용 키의 한 축이라 WITHOUT ROWID 표에서 NOT NULL로 보고됨
        for name in CONTENT_KEY_COLUMN_NAMES:
            self.assertTrue(columns[name][1], f"{name} must be NOT NULL")
        self.assertFalse(columns["candidate_tier"][1], "candidate_tier stays nullable")
        # - 표가 이미 현재 구성이므로 두 번째 호출은 표를 다시 만들지 않고 그대로 둠
        self.assertTrue(_content_table_is_current(self.conn))
        prepare_cache_tables(self.conn)
        self.assertEqual(table_columns(self.conn, FRAME_ANALYSIS_TABLE), columns)
        self.assertTrue(_content_table_is_current(self.conn))

    def test_legacy_row_is_promoted_once_its_fingerprint_is_known(self) -> None:
        self._write_legacy_row(filename="clip.mp4", size=100, mtime=1)
        record = make_frame_record("clip.mp4", fingerprint="a" * 32)
        self.assertEqual(promote_legacy_rows(self.conn), 0, "지문을 모르는 옛 행은 그대로 둠")
        record_legacy_fingerprints(
            self.conn, {(record.filename, record.filesize_bytes, record.mtime_ns): record.fingerprint}
        )
        self.assertEqual(promote_legacy_rows(self.conn), 1)
        row = self.conn.execute(
            f"SELECT filename FROM {FRAME_ANALYSIS_TABLE} WHERE filesize_bytes = ? AND fingerprint = ?",
            (100, "a" * 32),
        ).fetchone()
        self.assertEqual(row[0], "clip.mp4")
        self.assertEqual(
            self.conn.execute(f"SELECT COUNT(*) FROM {FRAME_ANALYSIS_LEGACY_TABLE}").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(f"SELECT COUNT(*) FROM {FRAME_ANALYSIS_TABLE}").fetchone()[0], 1
        )
        self.assertTrue(_content_table_is_current(self.conn))

    def test_stored_and_computed_fingerprints(self) -> None:
        self._write_legacy_row(filename="clip.mp4", size=100, mtime=1)
        key = ("clip.mp4", 100, 1)
        self.assertIsNone(stored_fingerprint(self.conn, None, key))
        self.assertIsNone(computed_fingerprint(None, 100))
        self.assertIsNone(computed_fingerprint(os.path.join(self.tmp_dir, "missing.mp4"), 100))
        clip = os.path.join(self.tmp_dir, "clip.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"z" * 100)
        self.assertEqual(computed_fingerprint(clip, 100), compute_fingerprint(clip, 100))
        self.conn.execute(f"UPDATE {FRAME_ANALYSIS_LEGACY_TABLE} SET fingerprint = 'b'")
        self.assertEqual(stored_fingerprint(self.conn, None, key), "b")

    def test_prepare_promotes_rows_written_after_an_earlier_connection(self) -> None:
        self._write_legacy_row(filename="clip.mp4", size=100, mtime=1)
        fingerprint = "c" * 32
        self.conn.execute(f"UPDATE {FRAME_ANALYSIS_LEGACY_TABLE} SET fingerprint = ?", (fingerprint,))
        store = FrameAnalysisStore(self.db_path)
        self.addCleanup(store.close)
        self.assertEqual(store.count(), 1)
        loaded = store.load_many([(100, fingerprint)])
        self.assertEqual(loaded[(100, fingerprint)].filename, "clip.mp4")


if __name__ == "__main__":
    unittest.main()
