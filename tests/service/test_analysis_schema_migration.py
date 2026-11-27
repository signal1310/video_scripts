"""분석 캐시 스키마 변경 시 기존 행을 보존하는지 검증(열 추가, 내용 행 이전, 실패 시 손상본 보존)"""

import contextlib
import glob
import logging
import os
import sqlite3
import tempfile
import unittest
from collections.abc import Iterable
from unittest.mock import patch

from src.core.analysis.probe_version_rules import KEYFRAME_MEASURE_VERSION
from src.service import analysis_store as store_mod
from src.service import analysis_store_schema as schema_mod
from src.service.analysis_store import (
    ALL_COLUMNS,
    ANALYSIS_TABLE_NAME,
    LEGACY_TABLE_NAME,
    LOCATION_TABLE_NAME,
    SCHEMA_VERSION,
    AnalysisStore,
    expected_table_info,
)


class _NoSpace:
    """디스크 여유가 0인 것처럼 보이게 하는 대역"""

    free: int = 0


def _old_create_sql(drop_columns: Iterable[str] = (), extra_column: str | None = None) -> str:
    """이전 스키마(파일명, 크기, 수정 시각 기본 키)의 CREATE TABLE 문을 만듦

    - 열을 빼거나 임의 열을 더해 더 오래된 구조도 만들 수 있음
    """
    dropped = set(drop_columns)
    columns = [col for col in ALL_COLUMNS if col.name not in dropped]
    lines = [f"  {col.name} {col.sql_type}{' NOT NULL' if col.not_null else ''}" for col in columns]
    if extra_column is not None:
        lines.append(f"  {extra_column} TEXT")
    lines.append("  PRIMARY KEY (filename, filesize_bytes, mtime_ns)")
    return f"CREATE TABLE {ANALYSIS_TABLE_NAME} (\n" + ",\n".join(lines) + "\n) WITHOUT ROWID"


def _old_index_sql() -> str:
    return f"CREATE INDEX idx_video_props_size_fp ON {ANALYSIS_TABLE_NAME} (filesize_bytes, fingerprint)"


def _insert_row(
    conn: sqlite3.Connection,
    columns: list[str],
    filename: str,
    *,
    width: int | None = None,
    fingerprint: str = "fp",
    probe_version: int = 5,
    keyframe_version: int = KEYFRAME_MEASURE_VERSION,
    size: int = 100,
    mtime: int = 200,
) -> None:
    values: dict[str, object] = {}
    for col in ALL_COLUMNS:
        if col.name not in columns:
            continue
        if col.py_type is str:
            values[col.name] = ""
        elif col.py_type is float:
            values[col.name] = 0.0
        else:
            values[col.name] = 0
    values.update(filename=filename, filesize_bytes=size, mtime_ns=mtime, fingerprint=fingerprint)
    if "probe_version" in values:
        values["probe_version"] = probe_version
    if "keyframe_version" in values:
        values["keyframe_version"] = keyframe_version
    if width is not None and "width" in values:
        values["width"] = width
    names = ", ".join(values)
    placeholders = ", ".join("?" * len(values))
    conn.execute(f"INSERT INTO {ANALYSIS_TABLE_NAME} ({names}) VALUES ({placeholders})", list(values.values()))


class SchemaMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "cache.sqlite3")

    def _write_old_table(self, create_sql: str, version: int, width: int | None = None, *, index: bool = False) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(create_sql)
            if index:
                conn.execute(_old_index_sql())
            names = [row[1] for row in conn.execute(f"PRAGMA table_info({ANALYSIS_TABLE_NAME})")]
            _insert_row(conn, names, "kept.mp4", width=width)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()
        finally:
            conn.close()

    def _write_content_table_without(self, drop_column: str) -> None:
        """내용 행 스키마에서 열 하나를 뺀 테이블을 만들고 행 하나를 넣음(열 추가 이전 경로 검증용)"""
        columns = [col for col in ALL_COLUMNS if col.name != drop_column]
        lines = [f"  {col.name} {col.sql_type}{' NOT NULL' if col.not_null else ''}" for col in columns]
        lines.append("  PRIMARY KEY (filesize_bytes, fingerprint)")
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(f"CREATE TABLE {ANALYSIS_TABLE_NAME} (\n" + ",\n".join(lines) + "\n) WITHOUT ROWID")
            names = [row[1] for row in conn.execute(f"PRAGMA table_info({ANALYSIS_TABLE_NAME})")]
            _insert_row(conn, names, "kept.mp4")
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
        finally:
            conn.close()

    def _actual_info(self) -> list[tuple[str, str, bool, int]]:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(f"PRAGMA table_info({ANALYSIS_TABLE_NAME})").fetchall()
        finally:
            conn.close()
        return [(str(r[1]), str(r[2]).upper(), bool(r[3]), int(r[5])) for r in rows]

    def test_filesystem_keyed_rows_move_to_content_rows_and_keep_lookup_keys(self) -> None:
        """옛 파일명 키 스키마를 열면 행이 내용 행으로 옮겨지고, 파일명과 수정 시각은 조회 키로 남는지 검증"""
        self._write_old_table(_old_create_sql(), version=1, width=1920, index=True)
        with self.assertNoLogs(store_mod.logger, level=logging.WARNING):
            store = AnalysisStore(self.db_path)
            self.assertEqual(store.count(), 1)
            store.close()
        self.assertEqual(sorted(self._actual_info()), sorted(expected_table_info()))
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT filename, width, filesize_bytes, mtime_ns FROM video_props").fetchall(),
                [("kept.mp4", 1920, 100, 200)],
            )
            self.assertEqual(
                conn.execute(f"SELECT filename, filesize_bytes, mtime_ns FROM {LEGACY_TABLE_NAME}").fetchall(),
                [("kept.mp4", 100, 200)],
            )
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(video_props_legacy)")}
        self.assertIn(LOCATION_TABLE_NAME, tables)
        self.assertIn(store_mod.LEGACY_INDEX_NAME, indexes)

    def test_opening_twice_changes_nothing(self) -> None:
        """같은 DB를 다시 열면 내용 행, 조회 키, 버전이 그대로인지 검증"""
        self._write_old_table(_old_create_sql(), version=1, width=1920)
        first = AnalysisStore(self.db_path)
        self.assertEqual(first.count_for_roots(), 1)
        first.close()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            before = (
                conn.execute("SELECT * FROM video_props").fetchall(),
                conn.execute(f"SELECT * FROM {LEGACY_TABLE_NAME}").fetchall(),
                conn.execute("PRAGMA user_version").fetchone()[0],
            )
        second = AnalysisStore(self.db_path)
        self.assertEqual(second.count(), 1)
        second.close()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            after = (
                conn.execute("SELECT * FROM video_props").fetchall(),
                conn.execute(f"SELECT * FROM {LEGACY_TABLE_NAME}").fetchall(),
                conn.execute("PRAGMA user_version").fetchone()[0],
            )
        self.assertEqual(after, before)

    def test_rows_of_same_content_collapse_and_keep_the_newest_measurement(self) -> None:
        """같은 내용의 옛 행 둘이 하나의 내용 행으로 합쳐지고 측정 버전이 새 행을 유지하는지 검증"""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(_old_create_sql())
            names = [row[1] for row in conn.execute(f"PRAGMA table_info({ANALYSIS_TABLE_NAME})")]
            _insert_row(conn, names, "sub/a.mp4", fingerprint="same", probe_version=4, keyframe_version=0, width=1280)
            _insert_row(conn, names, "a.mp4", fingerprint="same", probe_version=5, keyframe_version=1, width=1920)
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
        finally:
            conn.close()
        store = AnalysisStore(self.db_path)
        self.assertEqual(store.count(), 2)
        self.assertEqual(store.count_contents(), 1)
        store.close()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT width, probe_version FROM video_props").fetchall(), [(1920, 5)])
            keys = {row[0] for row in conn.execute(f"SELECT filename FROM {LEGACY_TABLE_NAME}")}
        self.assertEqual(keys, {"a.mp4", "sub/a.mp4"})

    def test_legacy_lookup_keys_serve_files_without_a_fingerprint_read(self) -> None:
        """옮긴 뒤에도 파일명으로 조회해 이전 결과를 지문 읽기 없이 찾을 수 있는지 검증"""
        self._write_old_table(_old_create_sql(), version=1, width=640)
        store = AnalysisStore(self.db_path)
        self.addCleanup(store.close)
        hits = store.fetch_by_names(["kept.mp4"])
        self.assertEqual([(hit.filename, hit.mtime_ns, hit.props.width) for hit in hits], [("kept.mp4", 200, 640)])

    def test_missing_video_props_column_is_added_and_row_kept(self) -> None:
        """내용 행 스키마에서 VideoProps 열 하나가 빠진 옛 테이블은 열만 추가되어 행이 보존되는지 검증"""
        self._write_content_table_without("width")
        with self.assertNoLogs(store_mod.logger, level=logging.WARNING):
            store = AnalysisStore(self.db_path)
            self.assertEqual(store.count(), 1)
            store.close()
        self.assertEqual(sorted(self._actual_info()), sorted(expected_table_info()))
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT width, filename FROM video_props").fetchall(), [(0, "kept.mp4")])
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_added_keyframe_version_column_defaults_to_current_measurement(self) -> None:
        """키프레임 측정 번호 열을 덧붙이면 기존 행은 현재 측정 번호로 간주되어 값을 이어 받을 수 있는지 검증"""
        self._write_content_table_without("keyframe_version")
        store = AnalysisStore(self.db_path)
        self.assertEqual(store.count(), 1)
        store.close()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT keyframe_version FROM video_props").fetchall(), [(KEYFRAME_MEASURE_VERSION,)])

    def test_extra_column_is_dropped_and_row_kept(self) -> None:
        """호환되지 않는 옛 열이 있으면 내용 행으로 옮길 때 그 열만 버리고 행을 보존하는지 검증"""
        self._write_old_table(_old_create_sql(extra_column="legacy_note"), version=1, width=1920)
        with self.assertNoLogs(store_mod.logger, level=logging.WARNING):
            store = AnalysisStore(self.db_path)
            self.assertEqual(store.count(), 1)
            store.close()
        self.assertEqual(sorted(self._actual_info()), sorted(expected_table_info()))

    def test_old_table_without_measurement_columns_keeps_its_rows(self) -> None:
        """측정 번호 열이 없던 옛 파일명 키 스키마도 열을 덧붙여 행을 보존하며 캐시가 켜진 채로 남는지 검증"""
        self._write_old_table(
            _old_create_sql(drop_columns=("probe_version", "keyframe_version")), version=1, width=1920
        )
        with self.assertNoLogs(store_mod.logger, level=logging.WARNING):
            store = AnalysisStore(self.db_path)
            self.assertEqual(store.count(), 1)
            self.assertEqual(store.count_contents(), 1)
            hits = store.fetch_by_names(["kept.mp4"])
            self.assertEqual([hit.props.width for hit in hits], [1920])
            store.close()
        self.assertEqual(sorted(self._actual_info()), sorted(expected_table_info()))
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(
                conn.execute("SELECT filename, width, probe_version FROM video_props").fetchall(),
                [("kept.mp4", 1920, 0)],
            )

    def test_old_table_without_measurement_columns_collapses_same_content(self) -> None:
        """측정 번호 열이 없는 옛 테이블에서도 같은 내용의 행을 하나로 합치고 파일명 조회 키를 모두 남기는지 검증"""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(_old_create_sql(drop_columns=("probe_version", "keyframe_version")))
            names = [row[1] for row in conn.execute(f"PRAGMA table_info({ANALYSIS_TABLE_NAME})")]
            _insert_row(conn, names, "a.mp4", fingerprint="same", width=1280)
            _insert_row(conn, names, "sub/a.mp4", fingerprint="same", width=1920)
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
        finally:
            conn.close()
        with self.assertNoLogs(store_mod.logger, level=logging.WARNING):
            store = AnalysisStore(self.db_path)
            self.assertEqual(store.count(), 2)
            self.assertEqual(store.count_contents(), 1)
            store.close()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT width FROM video_props").fetchall(), [(1280,)])

    def test_matching_schema_only_updates_the_version_number(self) -> None:
        """구조가 같고 버전 번호만 다르면 행과 구조를 건드리지 않고 번호만 맞추는지 검증"""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(store_mod.CONTENT_TABLE_SQL)
            conn.execute(store_mod.LEGACY_TABLE_SQL)
            conn.execute(store_mod.LOCATION_TABLE_SQL)
            conn.execute(store_mod.LEGACY_CONTENT_INDEX_SQL)
            names = [row[1] for row in conn.execute(f"PRAGMA table_info({ANALYSIS_TABLE_NAME})")]
            _insert_row(conn, names, "kept.mp4", width=640)
            conn.execute(
                f"INSERT INTO {LEGACY_TABLE_NAME} (filename, filesize_bytes, mtime_ns, fingerprint, root_scope) "
                f"VALUES ('kept.mp4', 100, 200, 'fp', '')"
            )
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
        finally:
            conn.close()
        store = AnalysisStore(self.db_path)
        self.assertEqual(store.count_contents(), 1)
        store.close()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT width FROM video_props").fetchall(), [(640,)])
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_legacy_key_table_without_scope_column_keeps_rows_and_gets_scope(self) -> None:
        """분석 범위 열이 없던 조회 키 표는 범위 열을 기본 키에 넣어 다시 만들고 기존 행을 보존하는지 검증"""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(store_mod.CONTENT_TABLE_SQL)
            conn.execute(store_mod.LOCATION_TABLE_SQL)
            conn.execute(
                f"CREATE TABLE {LEGACY_TABLE_NAME} (filename TEXT NOT NULL, filesize_bytes INTEGER NOT NULL, "
                "mtime_ns INTEGER NOT NULL, fingerprint TEXT NOT NULL, "
                "PRIMARY KEY (filename, filesize_bytes, mtime_ns)) WITHOUT ROWID"
            )
            conn.execute(store_mod.LEGACY_CONTENT_INDEX_SQL)
            names = [row[1] for row in conn.execute(f"PRAGMA table_info({ANALYSIS_TABLE_NAME})")]
            _insert_row(conn, names, "kept.mp4")
            conn.execute(
                f"INSERT INTO {LEGACY_TABLE_NAME} (filename, filesize_bytes, mtime_ns, fingerprint) "
                "VALUES ('kept.mp4', 100, 200, 'fp')"
            )
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
        finally:
            conn.close()
        with self.assertNoLogs(store_mod.logger, level=logging.WARNING):
            store = AnalysisStore(self.db_path)
            self.assertEqual([hit.filename for hit in store.fetch_by_names(["kept.mp4"])], ["kept.mp4"])
            store.close()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(
                conn.execute(f"SELECT filename, root_scope FROM {LEGACY_TABLE_NAME}").fetchall(),
                [("kept.mp4", "")],
            )
            pk = [row[1] for row in conn.execute(f"PRAGMA table_info({LEGACY_TABLE_NAME})") if row[5] > 0]
        self.assertEqual(pk, list(store_mod.LEGACY_KEY_COLUMN_NAMES))

    def test_failed_migration_preserves_the_damaged_file_and_recreates(self) -> None:
        """옮기는 중 DB 오류가 나면 원본 파일을 손상본 이름으로 보존하고 빈 캐시로 다시 만드는지 검증"""
        self._write_old_table(_old_create_sql(extra_column="legacy_note"), version=1)
        with (
            patch.object(schema_mod, "_migrate_content_rows", side_effect=sqlite3.DatabaseError("강제 실패")),
            self.assertLogs(store_mod.logger, level=logging.WARNING),
        ):
            store = AnalysisStore(self.db_path)
            self.assertEqual(store.count(), 0)
            store.close()
        preserved = glob.glob(self.db_path + ".corrupt-*")
        self.assertTrue(preserved, "손상본 보존 파일이 없음")
        with contextlib.closing(sqlite3.connect(preserved[0])) as conn:
            self.assertEqual(conn.execute("SELECT filename FROM video_props").fetchall(), [("kept.mp4",)])

    def test_insufficient_disk_space_leaves_the_original_table_untouched(self) -> None:
        """옮길 디스크 여유가 없으면 원본 캐시를 건드리지 않고 캐시를 끄는지 검증"""
        self._write_old_table(_old_create_sql(extra_column="legacy_note"), version=1, width=720)
        with (
            patch.object(schema_mod.shutil, "disk_usage", return_value=_NoSpace()),
            self.assertLogs(store_mod.logger, level=logging.WARNING),
        ):
            store = AnalysisStore(self.db_path)
            self.assertEqual(store.count(), 0)
            store.close()
        self.assertEqual(glob.glob(self.db_path + ".corrupt-*"), [])
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT width, filename FROM video_props").fetchall(), [(720, "kept.mp4")])


if __name__ == "__main__":
    unittest.main()
