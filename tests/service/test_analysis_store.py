import dataclasses
import logging
import os
import sqlite3
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

from src.core.models import VideoProps
from src.service import analysis_store as store_mod
from src.service.analysis_store import (
    ALL_COLUMN_NAMES,
    ANALYSIS_INDEX_NAME,
    ANALYSIS_TABLE_NAME,
    SCHEMA_VERSION,
    STORED_PROPS_FIELDS,
    AnalysisStore,
    StoredEntry,
    compute_fingerprint,
    expected_table_info,
    lookup_by_names_sql,
    lookup_by_sizes_sql,
)
from src.service.session import normalize_root_key

_BIG_MTIME_NS: int = 2**62 + 12345
_FULL_FILENAME: str = "한글 🍓 video ㅋㅋ.mp4"


def _full_props(filename: str = _FULL_FILENAME, *, keyframe: float | None = 0.4166666666666667) -> VideoProps:
    """저장 대상 필드를 모두 기본값이 아닌 값으로 채운 VideoProps"""
    return VideoProps(
        filename=filename,
        width=3840,
        height=2160,
        rotate_type=270,
        fps=59.94005994005994,
        vid_kbps=45000,
        aud_kbps=320,
        vid_size_MB=1234.5678,
        duration=7922.125,
        codec="av1",
        keyframe_interval=keyframe,
        rotate_source="Display Matrix 90°",
        is_hdr=True,
        is_10bit=True,
        has_encode_tag=True,
    )


def _entry(filename: str, *, size: int = 100, mtime: int = _BIG_MTIME_NS, fp: str = "f" * 32, keyframe: float | None = 2.0) -> StoredEntry:
    return StoredEntry(filename, size, mtime, fp, _full_props(filename, keyframe=keyframe))


class _StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = os.path.join(tmp.name, "video_cache.sqlite3")
        self.store = AnalysisStore(self.db_path)
        self.addCleanup(self.store.close)


class TestSchemaDerivation(unittest.TestCase):
    """스키마가 VideoProps 필드에서 빠짐없이, 중복 없이 생성되는지 검증"""

    def test_stored_fields_cover_video_props(self) -> None:
        all_fields = {f.name for f in dataclasses.fields(VideoProps)}
        self.assertEqual(set(STORED_PROPS_FIELDS), all_fields)
        self.assertEqual(len(STORED_PROPS_FIELDS), len(set(STORED_PROPS_FIELDS)))

    def test_schema_version_is_nonzero_and_fits_user_version(self) -> None:
        self.assertGreater(SCHEMA_VERSION, 0)
        self.assertLessEqual(SCHEMA_VERSION, 0x7FFFFFFF)

    def test_expected_table_info_lists_keys_first(self) -> None:
        info = expected_table_info()
        self.assertEqual([row[0] for row in info[:3]], ["filename", "filesize_bytes", "mtime_ns"])
        self.assertEqual([row[3] for row in info[:3]], [1, 2, 3])
        self.assertEqual(
            [row[0] for row in info],
            list(ALL_COLUMN_NAMES),
        )


class TestStoreSchemaOnDisk(_StoreTestCase):
    """실제 DB 파일의 테이블, 인덱스, user_version이 생성 규칙과 일치하는지 검증"""

    def test_table_matches_expected_definition(self) -> None:
        self.store.count()
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(f"PRAGMA table_info({ANALYSIS_TABLE_NAME})").fetchall()
            actual = [(r[1], str(r[2]).upper(), bool(r[3]), r[5]) for r in rows]
            self.assertEqual(actual, [(n, t, nn, pk) for n, t, nn, pk in expected_table_info()])
            indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertIn(ANALYSIS_INDEX_NAME, indexes)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        finally:
            conn.close()

    def test_mismatched_table_is_recreated_without_migration(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"CREATE TABLE {ANALYSIS_TABLE_NAME} (filename TEXT PRIMARY KEY, legacy INTEGER)")
        conn.execute(f"INSERT INTO {ANALYSIS_TABLE_NAME} VALUES ('old.mp4', 1)")
        conn.commit()
        conn.close()

        with self.assertLogs(store_mod.logger, level=logging.WARNING):
            self.assertEqual(self.store.count(), 0)
        self.store.apply((), [_entry("new.mp4")])
        self.assertEqual(self.store.count(), 1)

    def test_version_match_with_tampered_table_is_recreated(self) -> None:
        self.store.count()
        self.store.close()
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"DROP TABLE {ANALYSIS_TABLE_NAME}")
        conn.execute(f"CREATE TABLE {ANALYSIS_TABLE_NAME} (filename TEXT NOT NULL, PRIMARY KEY (filename)) WITHOUT ROWID")
        conn.commit()
        conn.close()
        with self.assertLogs(store_mod.logger, level=logging.WARNING):
            self.assertEqual(self.store.count(), 0)
        self.assertEqual(sqlite3.connect(self.db_path).execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_corrupt_file_is_recreated(self) -> None:
        with open(self.db_path, "wb") as f:
            f.write(b"this is not a sqlite database" * 200)
        with self.assertLogs(store_mod.logger, level=logging.WARNING):
            self.store.apply((), [_entry("a.mp4")])
        self.assertEqual(self.store.count(), 1)

    def test_corrupt_file_is_preserved_under_suffixed_name_not_deleted(self) -> None:
        """손상된 DB는 지우지 않고 .corrupt-<시각> 이름으로 남김(작업 기록 보존)"""
        garbage = b"this is not a sqlite database" * 200
        with open(self.db_path, "wb") as f:
            f.write(garbage)
        with self.assertLogs(store_mod.logger, level=logging.WARNING):
            self.store.apply((), [_entry("a.mp4")])
        folder = os.path.dirname(self.db_path)
        base = os.path.basename(self.db_path)
        preserved = [name for name in os.listdir(folder) if name.startswith(base + ".corrupt-")]
        self.assertEqual(len(preserved), 1)
        with open(os.path.join(folder, preserved[0]), "rb") as f:
            self.assertEqual(f.read(), garbage)


class TestStoreRoundTrip(_StoreTestCase):
    """모든 저장 필드가 쓰기와 읽기를 거쳐 값 그대로 보존되는지 검증"""

    def test_all_fields_survive_round_trip(self) -> None:
        entry = StoredEntry(_FULL_FILENAME, 2**40 + 7, _BIG_MTIME_NS, "ab" * 16, _full_props())
        self.store.apply((), [entry])
        fetched = self.store.fetch_by_names([_FULL_FILENAME])
        self.assertEqual(len(fetched), 1)
        self.assertEqual(fetched[0].props, _full_props())
        self.assertEqual((fetched[0].filesize_bytes, fetched[0].mtime_ns, fetched[0].fingerprint), (2**40 + 7, _BIG_MTIME_NS, "ab" * 16))
        self.assertIs(fetched[0].props.is_hdr, True)
        self.assertIs(fetched[0].props.is_10bit, True)
        self.assertIs(fetched[0].props.has_encode_tag, True)

    def test_none_keyframe_and_false_flags_round_trip(self) -> None:
        props = dataclasses.replace(
            _full_props("plain.mp4", keyframe=None),
            is_hdr=False,
            is_10bit=False,
            rotate_source=None,
        )
        self.store.apply((), [StoredEntry("plain.mp4", 1, 1, "00" * 16, props)])
        fetched = self.store.fetch_by_names(["plain.mp4"])[0].props
        self.assertIsNone(fetched.keyframe_interval)
        self.assertIs(fetched.is_hdr, False)
        self.assertIs(fetched.is_10bit, False)
        self.assertIsNone(fetched.rotate_source)
        self.assertEqual(fetched, props)

    def test_apply_replaces_same_key_and_deletes_given_keys(self) -> None:
        self.store.apply((), [_entry("a.mp4", keyframe=None)])
        self.store.apply((), [_entry("a.mp4", keyframe=3.0)])
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.fetch_by_names(["a.mp4"])[0].props.keyframe_interval, 3.0)
        self.store.apply([("a.mp4", 100, _BIG_MTIME_NS)], [_entry("b.mp4")])
        self.assertEqual([e.filename for e in self.store.fetch_by_names(["a.mp4", "b.mp4"])], ["b.mp4"])

    def test_lookup_chunks_large_name_lists(self) -> None:
        names = [f"clip_{i:04d}.mp4" for i in range(1234)]
        self.store.apply((), [_entry(n) for n in names])
        found = self.store.fetch_by_names(names)
        self.assertEqual(len(found), 1234)
        self.assertEqual(len(self.store.fetch_by_sizes([100, 100, 999])), 1234)

    def test_lookup_returns_rows_of_every_probe_version_with_their_number(self) -> None:
        """조회는 측정 버전을 거르지 않고 행의 번호와 함께 돌려주며, 재사용 판정은 호출 측 규칙이 맡는지 검증"""
        self.store.apply((), [_entry("v.mp4")])
        with patch.object(store_mod, "PROBE_VERSION", 3):
            self.store.apply((), [_entry("old.mp4")])
        by_name = self.store.fetch_by_names(["v.mp4", "old.mp4"])
        self.assertEqual({e.filename: e.probe_version for e in by_name}, {"v.mp4": store_mod.PROBE_VERSION, "old.mp4": 3})
        self.assertEqual({e.probe_version for e in self.store.fetch_by_sizes([100])}, {store_mod.PROBE_VERSION, 3})

    def test_clear_returns_deleted_count(self) -> None:
        self.store.apply((), [_entry("a.mp4"), _entry("b.mp4")])
        self.assertEqual(self.store.clear(), 2)
        self.assertEqual(self.store.count(), 0)


class TestStoreQueryPlan(_StoreTestCase):
    """조회가 기본 키와 보조 인덱스를 사용하는지 실행 계획으로 검증"""

    def _plan(self, sql: str, params: list[Any]) -> str:
        self.store.count()
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
            return " | ".join(str(r[-1]) for r in rows)
        finally:
            conn.close()

    def test_name_lookup_uses_primary_key_prefix(self) -> None:
        plan = self._plan(lookup_by_names_sql(2), ["a.mp4", "b.mp4"])
        self.assertIn("PRIMARY KEY", plan)
        self.assertNotIn("SCAN", plan)

    def test_size_lookup_uses_size_fingerprint_index(self) -> None:
        plan = self._plan(lookup_by_sizes_sql(2), [1, 2])
        self.assertIn(ANALYSIS_INDEX_NAME, plan)
        self.assertNotIn("SCAN", plan)


class TestStoreFailureHandling(_StoreTestCase):
    """DB 잠금이나 경로 오류 시 예외 없이 빈 결과로 물러나고 파일을 지우지 않는지 검증"""

    def test_locked_database_skips_writes_without_deleting_file(self) -> None:
        self.store.count()
        blocker = sqlite3.connect(self.db_path, isolation_level=None)
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN EXCLUSIVE")
        with patch.object(store_mod, "SQLITE_BUSY_TIMEOUT_SEC", 0.01):
            fresh = AnalysisStore(self.db_path)
            self.addCleanup(fresh.close)
            with self.assertLogs(store_mod.logger, level=logging.WARNING):
                fresh.apply((), [_entry("locked.mp4")])
        blocker.execute("ROLLBACK")
        self.assertTrue(os.path.exists(self.db_path))

    def test_unusable_directory_disables_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocker_file = os.path.join(tmp, "not_a_dir")
            with open(blocker_file, "wb") as f:
                f.write(b"x")
            store = AnalysisStore(os.path.join(blocker_file, "video_cache.sqlite3"))
            self.addCleanup(store.close)
            with self.assertLogs(store_mod.logger, level=logging.WARNING):
                self.assertEqual(store.count(), 0)
            self.assertEqual(store.fetch_by_names(["a.mp4"]), [])
            store.apply((), [_entry("a.mp4")])


class TestProbeScopeCost(_StoreTestCase):
    """분석 캐시 정리 범위 판정이 행 수에 비례해 폴더를 훑지 않는지"""

    ROW_COUNT: int = 2_000

    def setUp(self) -> None:
        super().setUp()
        roots = tempfile.TemporaryDirectory()
        self.addCleanup(roots.cleanup)
        self.root_a = os.path.join(roots.name, "root_dir_a")
        os.makedirs(self.root_a, exist_ok=True)

    def test_thousands_of_rows_scan_folder_once(self) -> None:
        """수천 행 분석 캐시를 현재 폴더 범위로 셀 때 폴더 파싱, scandir, isfile이 행 수를 따르지 않는지 검증"""
        self.store.apply((), [_entry(f"clip_{index}.mp4") for index in range(self.ROW_COUNT)])
        with (
            patch.object(store_mod, "parse_root_dirs", wraps=store_mod.parse_root_dirs) as parse,
            patch.object(store_mod.os.path, "isfile", wraps=os.path.isfile) as isfile,
            patch.object(store_mod.os.path, "isdir", wraps=os.path.isdir) as isdir,
            patch.object(store_mod.os, "scandir", wraps=os.scandir) as scandir,
        ):
            self.assertEqual(self.store.count_for_roots([self.root_a]), 0)
        self.assertLessEqual(parse.call_count, 2)
        self.assertEqual(isfile.call_count, 0)
        self.assertEqual(scandir.call_count, 1)
        # - 작업 폴더 해석에서 폴더 확인이 몇 번 있으므로 행 수(2,000)에 비례하지 않는지만 확인
        self.assertLessEqual(isdir.call_count, 4)


class TestFingerprint(unittest.TestCase):
    """내용 지문이 이름과 수정 시각에 무관하고 앞, 뒤 청크 변화를 구분하는지 검증"""

    def test_same_content_gives_same_fingerprint_regardless_of_name_and_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.mp4")
            b = os.path.join(tmp, "renamed.mp4")
            payload = b"0123456789" * 1000
            with open(a, "wb") as f:
                f.write(payload)
            with open(b, "wb") as f:
                f.write(payload)
            os.utime(b, ns=(1, 1))
            self.assertEqual(compute_fingerprint(a, len(payload)), compute_fingerprint(b, len(payload)))

    def test_head_or_tail_change_is_detected_for_large_files(self) -> None:
        size = 4 * store_mod.FINGERPRINT_CHUNK_BYTES
        base = bytearray(b"A" * size)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "big.mp4")
            with open(path, "wb") as f:
                f.write(base)
            original = compute_fingerprint(path, size)
            base[10] = ord("B")
            base[-10] = ord("C")
            with open(path, "wb") as f:
                f.write(base)
            self.assertNotEqual(original, compute_fingerprint(path, size))

    def test_middle_change_is_not_detected_by_design(self) -> None:
        """앞, 뒤 청크만 읽는 설계상 가운데만 바뀐 파일은 같은 지문이 나오는 한계를 문서화"""
        size = 4 * store_mod.FINGERPRINT_CHUNK_BYTES
        base = bytearray(b"A" * size)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mid.mp4")
            with open(path, "wb") as f:
                f.write(base)
            original = compute_fingerprint(path, size)
            base[size // 2] = ord("B")
            with open(path, "wb") as f:
                f.write(base)
            self.assertEqual(original, compute_fingerprint(path, size))


class TestScopedClear(_StoreTestCase):
    """정리 범위(root_dirs)별 분석 캐시, 직접 지정, 표 선택 건수와 비우기"""

    def setUp(self) -> None:
        super().setUp()
        roots = tempfile.TemporaryDirectory()
        self.addCleanup(roots.cleanup)
        self.root_a = os.path.join(roots.name, "root_dir_a")
        self.root_b = os.path.join(roots.name, "root_dir_b")
        for path in (self.root_a, self.root_b, os.path.join(self.root_a, "sub")):
            os.makedirs(path, exist_ok=True)

    def test_probe_cache_counts_and_clears_only_given_roots(self) -> None:
        with open(os.path.join(self.root_a, "a.mp4"), "wb") as handle:
            handle.write(b"x")
        self.store.apply((), [_entry("a.mp4"), _entry("sub/a2.mp4"), _entry("root_dir_b/b.mp4")])
        self.assertEqual(self.store.count_for_roots([self.root_a]), 2)
        self.assertEqual(self.store.clear_for_roots([self.root_a]), 2)
        self.assertEqual(
            [entry.filename for entry in self.store.fetch_by_sizes([100])], ["root_dir_b/b.mp4"]
        )
        self.assertEqual(self.store.count_for_roots(), 1)
        self.assertEqual(self.store.clear_for_roots(), 1)
        self.assertEqual(self.store.count(), 0)

    def test_bare_filename_of_other_root_is_kept(self) -> None:
        """경로 없는 파일명은 범위 폴더에 실제 파일이 있을 때만 그 폴더 소속으로 봄"""
        with open(os.path.join(self.root_a, "only_a.mp4"), "wb") as handle:
            handle.write(b"x")
        self.store.apply((), [_entry("only_a.mp4"), _entry("only_b.mp4")])
        self.assertEqual(self.store.count_for_roots([self.root_a]), 1)
        self.assertEqual(self.store.clear_for_roots([self.root_a]), 1)
        self.assertEqual([entry.filename for entry in self.store.fetch_by_sizes([100])], ["only_b.mp4"])

    def test_manual_targets_are_counted_and_cleared_by_root_key(self) -> None:
        key_a = normalize_root_key(self.root_a)
        key_b = normalize_root_key(self.root_b)
        self.store.write_manual_targets({key_a: {"a.mp4": "720p"}, key_b: {"b.mp4": "1080p"}})
        self.assertEqual(self.store.count_manual_targets([self.root_a]), 1)
        self.assertEqual(self.store.clear_manual_targets([self.root_a]), 1)
        self.assertEqual(self.store.read_manual_targets({}), {key_b: {"b.mp4": "1080p"}})
        self.assertEqual(self.store.count_manual_targets(), 1)
        self.assertEqual(self.store.clear_manual_targets(), 1)

    def test_table_selection_is_counted_and_cleared_by_root_key(self) -> None:
        key_a = normalize_root_key(self.root_a)
        key_b = normalize_root_key(self.root_b)
        self.store.write_table_selection(key_a, ["a.mp4"])
        self.store.write_table_selection(key_b, ["b.mp4"])
        self.assertEqual(self.store.count_table_selections([self.root_a]), 1)
        self.assertEqual(self.store.clear_table_selections([self.root_a]), 1)
        self.assertEqual(self.store.read_table_selection(key_b), {"b.mp4"})
        self.assertEqual(self.store.count_table_selections(), 1)
        self.assertEqual(self.store.count_table_selections([self.root_b]), 1)


if __name__ == "__main__":
    unittest.main()
