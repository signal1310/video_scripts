"""정밀 결정 결과 캐시의 표 구성, 키, 행 변환, 지문 확인 검증"""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.core.evaluation.sample_tuner import TuneResult
from src.service import sample_tuner_cache as tuner_cache_module
from src.service.sample_tuner_cache import (
    CACHE_COLUMNS,
    KEY_COLUMNS,
    SAMPLE_TUNER_TABLE_NAME,
    SampleTunerCache,
    SampleTuneRecord,
    TuneCacheKey,
    TuneJob,
    record_from_result,
    record_to_row,
    row_to_record,
    source_fingerprint,
    tune_cache_key,
    utc_now_iso,
)


def _job(**overrides: object) -> TuneJob:
    values: dict[str, object] = {
        "source_path": "in.mp4",
        "source_size": 1000,
        "source_mtime_ns": 42,
        "source_duration_sec": 600.0,
        "encoder": "hevc_nvenc",
        "baseline_quality": 22.0,
        "width": 3840,
        "height": 2160,
    }
    values.update(overrides)
    return TuneJob(**values)  # type: ignore[arg-type]


def _key(**overrides: object) -> TuneCacheKey:
    values: dict[str, object] = {
        "filesize_bytes": 1000,
        "fingerprint": "c" * 32,
        "encoder": "hevc_nvenc",
        "baseline_quality": 22.0,
        "plan_hash": "abc123",
        "target_key": "vmaf:95.0:ssim:0.99",
    }
    values.update(overrides)
    return TuneCacheKey(**values)  # type: ignore[arg-type]


class TestSampleTunerCacheLayer(unittest.TestCase):
    """내용 키 캐시의 생성, 왕복, 옛 표 교체를 검증"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_dir = tmp.name
        self.db_path = os.path.join(tmp.name, "video_cache.sqlite3")

    def test_roundtrip_through_sqlite(self) -> None:
        cache = SampleTunerCache(self.db_path)
        result = TuneResult(
            best_quality=23.5,
            probes=(),
            converged=True,
            reason="수렴",
        )
        record = record_from_result(_key(), result, measured_at="2026-01-01T00:00:00Z")
        cache.save(record)
        self.assertEqual(cache.count(), 1)
        loaded = cache.load(_key())
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.best_quality, 23.5)  # type: ignore[union-attr]
        self.assertEqual(cache.clear(), 1)
        self.assertEqual(cache.count(), 0)

    def test_previous_path_key_table_is_rebuilt(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            f"CREATE TABLE {SAMPLE_TUNER_TABLE_NAME} (source_path TEXT NOT NULL,"
            " filesize_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL)"
        )
        conn.commit()
        conn.close()
        cache = SampleTunerCache(self.db_path)
        self.assertEqual(cache.count(), 0)
        conn = sqlite3.connect(self.db_path)
        columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({SAMPLE_TUNER_TABLE_NAME})")]
        conn.close()
        self.assertEqual(columns, list(CACHE_COLUMNS))

    def test_row_conversion_uses_column_order(self) -> None:
        record = record_from_result(_key(), TuneResult(22.0, (), False, "미수렴"))
        row = record_to_row(record)
        self.assertEqual(len(row), len(CACHE_COLUMNS))
        self.assertEqual(row_to_record(row), record)

    def test_missing_file_skips_the_cache_key(self) -> None:
        job = _job(source_path=os.path.join(self.tmp_dir, "missing.mp4"))
        self.assertIsNone(source_fingerprint(job))
        self.assertIsNone(tune_cache_key(job, baseline_quality=22.0, plan_hash="h", target_key="t"))

    def test_given_fingerprint_avoids_reading_the_file(self) -> None:
        job = _job(source_path=os.path.join(self.tmp_dir, "missing.mp4"), source_fingerprint="d" * 32)
        self.assertEqual(source_fingerprint(job), "d" * 32)
        key = tune_cache_key(job, baseline_quality=22.0, plan_hash="h", target_key="t")
        self.assertIsNotNone(key)
        self.assertEqual(key.fingerprint, "d" * 32)  # type: ignore[union-attr]

    def test_utc_timestamp_format(self) -> None:
        text = utc_now_iso()
        self.assertRegex(text, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_unreadable_database_disables_cache(self) -> None:
        cache = SampleTunerCache(self.tmp_dir)
        self.assertIsNone(cache.load(_key()))
        self.assertEqual(cache.count(), 0)
        self.assertIsInstance(record_from_result(_key(), TuneResult(22.0, (), True, "r")), SampleTuneRecord)

    def test_key_columns_match_the_primary_key_of_the_table(self) -> None:
        """키 열 목록이 표의 기본 키 구성과 정확히 같은지 검증(열 순서 변경을 잡음)"""
        cache = SampleTunerCache(self.db_path)
        self.assertEqual(cache.count(), 0)
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(f"PRAGMA table_info({SAMPLE_TUNER_TABLE_NAME})").fetchall()
        conn.close()
        primary_key = tuple(str(row[1]) for row in sorted(rows, key=lambda row: int(row[5])) if int(row[5]) > 0)
        self.assertEqual(primary_key, KEY_COLUMNS)
        self.assertEqual(KEY_COLUMNS, tuple(CACHE_COLUMNS[: len(KEY_COLUMNS)]))

    def test_concurrent_ddl_error_is_retried_instead_of_disabling(self) -> None:
        """표를 만들다 동시 DDL 충돌이 나면 한 번 더 시도해 캐시를 끄지 않는지 검증"""
        cache = SampleTunerCache(self.db_path)
        real_ensure = tuner_cache_module._ensure_cache_table
        calls: list[int] = []

        def flaky(conn: sqlite3.Connection) -> None:
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("table sample_tuner_cache already exists")
            real_ensure(conn)

        with patch.object(tuner_cache_module, "_ensure_cache_table", flaky):
            self.assertEqual(cache.count(), 0)
        self.assertEqual(len(calls), 2)
        self.assertFalse(cache._disabled)
        # 이후 조회는 표가 이미 있으므로 재시도 없이 이어짐
        self.assertEqual(cache.count(), 0)

    def test_table_preparation_failure_closes_the_connection(self) -> None:
        """표 준비가 끝내 실패하면 연결을 닫고 이번만 캐시를 건너뛰는지 검증"""
        cache = SampleTunerCache(self.db_path)
        opened: list[sqlite3.Connection] = []
        real_connect = sqlite3.connect

        def tracking(*args: object, **kwargs: object) -> sqlite3.Connection:
            conn = real_connect(*args, **kwargs)  # type: ignore[arg-type]
            opened.append(conn)
            return conn

        with (
            patch.object(tuner_cache_module.sqlite3, "connect", tracking),
            patch.object(
                tuner_cache_module,
                "_ensure_cache_table",
                side_effect=sqlite3.OperationalError("table sample_tuner_cache already exists"),
            ),
            self.assertLogs("src.service.sample_tuner_cache", level="WARNING"),
        ):
            self.assertEqual(cache.count(), 0)
        self.assertEqual(len(opened), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")
        self.assertFalse(cache._disabled)


if __name__ == "__main__":
    unittest.main()
