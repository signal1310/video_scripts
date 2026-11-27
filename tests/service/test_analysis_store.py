import dataclasses
import hashlib
import logging
import os
import sqlite3
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

from src.core.analysis.volume_identity import volume_location
from src.core.models import VideoProps
from src.service import analysis_store as store_mod
from src.service.analysis_store import (
    ALL_COLUMN_NAMES,
    ANALYSIS_TABLE_NAME,
    CONTENT_KEY_COLUMN_NAMES,
    LEGACY_INDEX_NAME,
    LEGACY_TABLE_NAME,
    LOCATION_INDEX_NAME,
    LOCATION_TABLE_NAME,
    SCHEMA_VERSION,
    STORED_PROPS_FIELDS,
    AnalysisStore,
    LocationRecord,
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


def _entry(
    filename: str,
    *,
    size: int = 100,
    mtime: int = _BIG_MTIME_NS,
    fp: str | None = None,
    keyframe: float | None = 2.0,
) -> StoredEntry:
    """파일 하나를 나타내는 저장 항목. 지문을 주지 않으면 파일명에서 내용 지문을 만듦"""
    fingerprint = fp if fp is not None else hashlib.sha256(filename.encode("utf-8")).hexdigest()[:32]
    return StoredEntry(filename, size, mtime, fingerprint, _full_props(filename, keyframe=keyframe))


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

    def test_expected_table_info_lists_content_key(self) -> None:
        info = expected_table_info()
        self.assertEqual([row[0] for row in info], list(ALL_COLUMN_NAMES))
        pk_names = [row[0] for row in info if row[3] > 0]
        self.assertEqual(pk_names, list(CONTENT_KEY_COLUMN_NAMES))
        self.assertEqual([row[3] for row in info if row[3] > 0], [1, 2])


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
            self.assertIn(LEGACY_INDEX_NAME, indexes)
            self.assertIn(LOCATION_INDEX_NAME, indexes)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertLessEqual({ANALYSIS_TABLE_NAME, LEGACY_TABLE_NAME, LOCATION_TABLE_NAME}, tables)
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

    def test_old_database_without_location_index_gets_it_on_open(self) -> None:
        """위치 표 내용 인덱스가 없던 DB도 열 때 인덱스를 만들고 user_version을 맞추는지 검증"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(store_mod.CONTENT_TABLE_SQL)
        conn.execute(store_mod.LEGACY_TABLE_SQL)
        conn.execute(store_mod.LEGACY_CONTENT_INDEX_SQL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        conn.close()

        self.store.count()
        conn = sqlite3.connect(self.db_path)
        try:
            indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        finally:
            conn.close()
        self.assertIn(LOCATION_INDEX_NAME, indexes)

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
            self.store.apply((), [_entry("old.mp4", fp="e" * 32)])
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
        self.assertIn(LEGACY_INDEX_NAME, plan)
        self.assertNotIn("SCAN", plan)

    def test_orphan_delete_uses_both_content_indexes(self) -> None:
        """내용 행 삭제가 위치 표와 파일명 키 표를 내용 키 인덱스로 확인하는지 검증"""
        plan = self._plan(store_mod.ORPHAN_CONTENT_ROWS_SQL, [])
        self.assertIn(LEGACY_INDEX_NAME, plan)
        self.assertIn(LOCATION_INDEX_NAME, plan)
        self.assertNotIn(f"SCAN {LOCATION_TABLE_NAME}", plan)


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

    def test_location_rows_are_counted_and_cleared_by_root_prefix(self) -> None:
        """파일명 키가 없는 위치 행도 작업 폴더 접두사로 세고 지우는지 검증"""
        record_a = self._location(self.root_a, "a.mp4")
        record_b = self._location(self.root_b, "b.mp4")
        self.store.record_locations([record_a, record_b])
        self.assertEqual(self.store.count_for_roots([self.root_a]), 1)
        self.assertEqual(self.store.clear_for_roots([self.root_a]), 1)
        self.assertEqual(
            self.store.fetch_locations([(record_a.volume_id, record_a.volume_path)]), []
        )
        self.assertEqual(
            len(self.store.fetch_locations([(record_b.volume_id, record_b.volume_path)])), 1
        )

    def _location(self, root: str, name: str) -> LocationRecord:
        """작업 폴더의 파일 하나에 대응하는 위치 행"""
        location = volume_location(os.path.join(root, name))
        return LocationRecord(location.volume_id, location.volume_path, 100, _BIG_MTIME_NS, "f" * 32)

    def test_manual_targets_are_counted_and_cleared_by_root_key(self) -> None:
        key_a = normalize_root_key(self.root_a)
        key_b = normalize_root_key(self.root_b)
        self.store.write_manual_targets({key_a: {"a.mp4": "720p"}, key_b: {"b.mp4": "1080p"}})
        self.assertEqual(self.store.count_manual_targets([self.root_a]), 1)
        self.assertEqual(self.store.clear_manual_targets([self.root_a]), 1)
        self.assertEqual(self.store.read_manual_targets({}), {key_b: {"b.mp4": "1080p"}})
        self.assertEqual(self.store.count_manual_targets(), 1)
        self.assertEqual(self.store.clear_manual_targets(), 1)

    def test_same_basename_saved_in_two_roots_keeps_both_keys(self) -> None:
        """서로 다른 작업 폴더에서 같은 이름의 파일을 저장해도 서로의 조회 키를 지우지 않는지 검증

        - 단일 작업 폴더는 접두사 없는 상대 이름을 쓰므로 다른 폴더의 같은 이름과 겹칠 수 있음
        """
        self.store.apply((), [_entry("clip.mp4", size=100, mtime=1, fp="a" * 32)], self.root_a)
        self.store.apply((), [_entry("clip.mp4", size=200, mtime=2, fp="b" * 32)], self.root_b)
        hits = self.store.fetch_by_sizes([100, 200])
        self.assertEqual(sorted((hit.filesize_bytes, hit.fingerprint) for hit in hits), [(100, "a" * 32), (200, "b" * 32)])
        self.assertEqual(self.store.count(), 2)
        self.assertEqual(self.store.count_contents(), 2)

    def test_same_root_replacement_drops_the_stale_key(self) -> None:
        """같은 작업 폴더에서 같은 경로의 파일이 바뀌면 옛 조회 키만 지우고 다른 폴더의 키는 남기는지 검증"""
        self.store.apply((), [_entry("clip.mp4", size=100, mtime=1, fp="a" * 32)], self.root_a)
        self.store.apply((), [_entry("clip.mp4", size=200, mtime=2, fp="b" * 32)], self.root_b)
        self.store.apply((), [_entry("clip.mp4", size=300, mtime=3, fp="c" * 32)], self.root_a)
        hits = self.store.fetch_by_names(["clip.mp4"])
        self.assertEqual(sorted((hit.filesize_bytes, hit.fingerprint) for hit in hits), [(200, "b" * 32), (300, "c" * 32)])
        self.assertEqual(self.store.count(), 2)

    def test_scope_is_normalized_so_the_same_root_spelled_differently_still_prunes(self) -> None:
        """같은 작업 폴더를 다른 표기(끝 구분자)로 넘겨도 같은 범위로 보아 옛 키를 지우는지 검증"""
        self.store.apply((), [_entry("clip.mp4", size=100, mtime=1, fp="a" * 32)], self.root_a)
        self.store.apply((), [_entry("clip.mp4", size=200, mtime=2, fp="b" * 32)], self.root_a + os.sep)
        hits = self.store.fetch_by_names(["clip.mp4"])
        self.assertEqual([(hit.filesize_bytes, hit.fingerprint) for hit in hits], [(200, "b" * 32)])

    def test_apply_without_root_keeps_keys_of_every_scope(self) -> None:
        """분석 범위를 모르는 저장(인코딩 후 행 삭제 등)은 범위가 빈 키로 남아 다른 범위의 키를 지우지 않는지 검증"""
        self.store.apply((), [_entry("clip.mp4", size=100, mtime=1, fp="a" * 32)], self.root_a)
        self.store.apply([], [_entry("clip.mp4", size=200, mtime=2, fp="b" * 32)])
        hits = self.store.fetch_by_names(["clip.mp4"])
        self.assertEqual(sorted(hit.filesize_bytes for hit in hits), [100, 200])
        self.assertEqual(self.store.count(), 2)
        self.assertEqual(self.store.count_contents(), 2)

    def test_resaving_a_migrated_row_leaves_one_key(self) -> None:
        """범위를 모르는 옛 키가 남은 파일을 작업 폴더 범위로 다시 저장하면 키가 하나만 남는지 검증

        - 옛 키는 같은 파일명, 크기, 수정 시각이므로 같은 파일의 키이고, 정리 대화상자가 파일을 두 번 세면 안 됨
        """
        with open(os.path.join(self.root_a, "legacy.mp4"), "wb") as handle:
            handle.write(b"x")
        self.store.apply((), [_entry("legacy.mp4", size=100, mtime=1, fp="a" * 32)])
        self.assertEqual(self.store.count(), 1)

        self.store.apply((), [_entry("legacy.mp4", size=100, mtime=1, fp="a" * 32)], self.root_a)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.count_for_roots([self.root_a]), 1)
        self.assertEqual(self.store.count_contents(), 1)

    def test_scoped_save_keeps_an_empty_scope_key_of_another_file(self) -> None:
        """파일명만 같고 크기가 다른 범위 없는 키는 다른 파일이므로 지우지 않는지 검증"""
        self.store.apply((), [_entry("clip.mp4", size=100, mtime=1, fp="a" * 32)])
        self.store.apply((), [_entry("clip.mp4", size=200, mtime=2, fp="b" * 32)], self.root_a)
        hits = self.store.fetch_by_names(["clip.mp4"])
        self.assertEqual(sorted(hit.filesize_bytes for hit in hits), [100, 200])
        self.assertEqual(self.store.count(), 2)

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


class TestContentAndLocationRows(_StoreTestCase):
    """내용 행과 위치 행: 같은 내용이 두 폴더에 있으면 내용 행 하나, 위치 행 둘, 같은 경로 교체는 위치 행 갱신"""

    def test_same_content_in_two_folders_shares_one_content_row(self) -> None:
        same = hashlib.sha256(b"same-content").hexdigest()[:32]
        self.store.apply((), [_entry("sub/a.mp4", fp=same), _entry("a.mp4", fp=same)])
        self.assertEqual(self.store.count(), 2)
        self.assertEqual(self.store.count_contents(), 1)
        self.store.record_locations(
            [
                LocationRecord("vol:0011", "videos/sub/a.mp4", 100, _BIG_MTIME_NS, same),
                LocationRecord("vol:0011", "videos/a.mp4", 100, _BIG_MTIME_NS, same),
            ]
        )
        stored = self.store.fetch_locations([("vol:0011", "videos/a.mp4"), ("vol:0011", "videos/sub/a.mp4")])
        self.assertEqual(sorted(record.volume_path for record in stored), ["videos/a.mp4", "videos/sub/a.mp4"])
        self.assertEqual([entry.filename for entry in self.store.fetch_by_names(["a.mp4"])], ["a.mp4"])

    def test_location_row_is_replaced_when_file_at_path_changes(self) -> None:
        self.store.record_locations([LocationRecord("vol:0011", "videos/a.mp4", 100, 1, "old")])
        self.store.record_locations([LocationRecord("vol:0011", "videos/a.mp4", 200, 2, "new")])
        stored = self.store.fetch_locations([("vol:0011", "videos/a.mp4")])
        self.assertEqual(stored, [LocationRecord("vol:0011", "videos/a.mp4", 200, 2, "new")])

    def test_clearing_analysis_rows_also_clears_location_rows(self) -> None:
        """분석 캐시를 비우면 내용 행 조회에 쓰이지 않는 위치 행도 함께 지우는지 검증"""
        entry = _entry("a.mp4")
        self.store.apply((), [entry])
        self.store.record_locations(
            [LocationRecord("vol:0011", "videos/a.mp4", 100, _BIG_MTIME_NS, entry.fingerprint)]
        )
        self.assertEqual(self.store.clear(), 1)
        self.assertEqual(self.store.count(), 0)
        self.assertEqual(self.store.fetch_locations([("vol:0011", "videos/a.mp4")]), [])

    def test_clear_counts_a_location_row_left_without_keys(self) -> None:
        """파일명 키 없이 남은 위치 행도 clear가 지우고 지운 수에 포함하는지 검증"""
        self.store.record_locations([LocationRecord("vol:0011", "videos/a.mp4", 100, _BIG_MTIME_NS, "f" * 32)])
        self.assertEqual(self.store.clear(), 1)
        self.assertEqual(self.store.fetch_locations([("vol:0011", "videos/a.mp4")]), [])


    def test_content_row_reachable_only_by_location_survives_a_key_prune(self) -> None:
        """같은 경로의 옛 이름 키가 지워져도 위치 행이 가리키는 내용 행은 남는지 검증"""
        old = hashlib.sha256(b"kept-by-location").hexdigest()[:32]
        new = hashlib.sha256(b"new-at-same-name").hexdigest()[:32]
        self.store.apply((), [_entry("a.mp4", fp=old)])
        self.store.record_locations([LocationRecord("vol:0011", "videos/a.mp4", 100, _BIG_MTIME_NS, old)])
        self.store.apply((), [_entry("a.mp4", mtime=500, fp=new)])
        self.assertEqual(self.store.count_contents(), 2)
        self.assertEqual([hit.fingerprint for hit in self.store.fetch_by_contents([(100, old)])], [old])

    def test_empty_lookups_do_not_query_the_database(self) -> None:
        """빈 목록 조회는 DB를 열지 않고 빈 결과를 돌려주는지 검증"""
        self.assertEqual(self.store.fetch_by_names([]), [])
        self.assertEqual(self.store.fetch_by_sizes([]), [])
        self.assertEqual(self.store.fetch_locations([]), [])
        self.store.record_locations([])

    def test_fetch_by_contents_reads_content_rows_by_size_and_fingerprint(self) -> None:
        """위치 행이 가리키는 내용을 크기와 지문으로 찾는지 검증"""
        same = hashlib.sha256(b"content-row").hexdigest()[:32]
        self.store.apply((), [_entry("a.mp4", fp=same), _entry("b.mp4", fp="f" * 32, size=200)])
        found = self.store.fetch_by_contents([(100, same), (200, "missing")])
        self.assertEqual([(hit.filesize_bytes, hit.fingerprint) for hit in found], [(100, same)])
        self.assertEqual(self.store.fetch_by_contents([]), [])

    def test_remove_locations_deletes_only_the_named_keys(self) -> None:
        """지정한 위치 키만 지우고 다른 위치 행은 남기는지 검증"""
        self.store.record_locations(
            [
                LocationRecord("vol:0011", "videos/a.mp4", 100, _BIG_MTIME_NS, "a" * 32),
                LocationRecord("vol:0011", "videos/b.mp4", 200, _BIG_MTIME_NS, "b" * 32),
            ]
        )
        self.store.remove_locations([("vol:0011", "videos/a.mp4")])
        self.assertEqual(self.store.fetch_locations([("vol:0011", "videos/a.mp4")]), [])
        self.assertEqual(len(self.store.fetch_locations([("vol:0011", "videos/b.mp4")])), 1)
        self.store.remove_locations([])

    def test_renaming_a_file_keeps_the_content_row(self) -> None:
        """이름을 바꾼 파일은 조회 키만 옮겨지고 내용 행은 지워지지 않는지 검증"""
        same = hashlib.sha256(b"rename-content").hexdigest()[:32]
        self.store.apply((), [_entry("old.mp4", fp=same)])
        self.assertEqual(self.store.count_contents(), 1)
        self.store.apply([("old.mp4", 100, _BIG_MTIME_NS)], [_entry("new.mp4", mtime=999, fp=same)])
        self.assertEqual(self.store.count_contents(), 1)
        self.assertEqual([entry.filename for entry in self.store.fetch_by_names(["old.mp4", "new.mp4"])], ["new.mp4"])
        self.assertEqual(self.store.fetch_by_names(["new.mp4"])[0].mtime_ns, 999)

    def test_replacing_a_file_at_the_same_path_leaves_only_the_new_key(self) -> None:
        """같은 경로의 파일을 교체하면 새 조회 키만 남고 옛 키는 지워지는지 검증"""
        self.store.apply((), [_entry("a.mp4", fp="old" * 8)])
        self.store.apply((), [_entry("a.mp4", mtime=500, fp="new" * 8)])
        hits = self.store.fetch_by_names(["a.mp4"])
        self.assertEqual([(hit.fingerprint, hit.mtime_ns) for hit in hits], [("new" * 8, 500)])
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.count_contents(), 1)

    def test_older_measurement_does_not_overwrite_a_newer_content_row(self) -> None:
        """같은 내용의 행에 옛 측정 결과를 저장해도 새 측정 행을 되돌리지 않는지 검증"""
        same = hashlib.sha256(b"measured-content").hexdigest()[:32]
        self.store.apply((), [_entry("a.mp4", fp=same)])
        with patch.object(store_mod, "PROBE_VERSION", 3):
            older = _entry("a.mp4", fp=same)
            older.props = dataclasses.replace(older.props, width=640)
            self.store.apply((), [older])
        hit = self.store.fetch_by_names(["a.mp4"])[0]
        self.assertEqual((hit.props.width, hit.probe_version), (3840, store_mod.PROBE_VERSION))


if __name__ == "__main__":
    unittest.main()
