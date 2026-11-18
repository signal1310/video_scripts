"""분석 캐시 스키마 변경 시 기존 행을 보존하는지 검증(열 추가, 공통 열 복사, 실패 시 손상본 보존)"""

import contextlib
import glob
import logging
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.core.probe_version_rules import KEYFRAME_MEASURE_VERSION
from src.service import analysis_store as store_mod
from src.service.analysis_store import (
    ALL_COLUMNS,
    ANALYSIS_TABLE_NAME,
    SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
    AnalysisStore,
    expected_table_info,
)


class _NoSpace:
    """디스크 여유가 0인 것처럼 보이게 하는 대역"""

    free: int = 0


def _old_create_sql(drop_column: str | None = None, extra_column: str | None = None) -> str:
    """현재 DDL에서 열 하나를 빼거나 임의 열을 더해 옛 구조의 CREATE TABLE 문을 만듦"""
    sql = SCHEMA_STATEMENTS[0]
    if drop_column is not None:
        col = next(c for c in ALL_COLUMNS if c.name == drop_column)
        line = f"  {col.name} {col.sql_type}{' NOT NULL' if col.not_null else ''},\n"
        assert sql.count(line) == 1, line
        sql = sql.replace(line, "")
    if extra_column is not None:
        sql = sql.replace("  PRIMARY KEY", f"  {extra_column} TEXT,\n  PRIMARY KEY", 1)
    return sql


def _insert_row(conn: sqlite3.Connection, names: list[str], filename: str, width: int | None = None) -> None:
    values: dict[str, object] = {}
    for col in ALL_COLUMNS:
        if col.name not in names:
            continue
        if col.py_type is str:
            values[col.name] = ""
        elif col.py_type is float:
            values[col.name] = 0.0
        else:
            values[col.name] = 0
    values.update(filename=filename, filesize_bytes=100, mtime_ns=200, fingerprint="fp")
    if width is not None and "width" in values:
        values["width"] = width
    columns = ", ".join(values)
    placeholders = ", ".join("?" * len(values))
    conn.execute(f"INSERT INTO {ANALYSIS_TABLE_NAME} ({columns}) VALUES ({placeholders})", list(values.values()))


class SchemaMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "cache.sqlite3")

    def _write_old_table(self, create_sql: str, version: int, width: int | None = None) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(create_sql)
            names = [row[1] for row in conn.execute(f"PRAGMA table_info({ANALYSIS_TABLE_NAME})")]
            _insert_row(conn, names, "kept.mp4", width=width)
            conn.execute(f"PRAGMA user_version = {version}")
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

    def test_missing_nullable_or_not_null_column_is_added_and_row_kept(self) -> None:
        """열 하나가 빠진 옛 테이블은 열만 추가되어 기존 행이 보존되고, NOT NULL 열은 기본값을 가지는지 검증"""
        self._write_old_table(_old_create_sql(drop_column="width"), version=1, width=None)
        with self.assertNoLogs(store_mod.logger, level=logging.WARNING):
            store = AnalysisStore(self.db_path)
            self.assertEqual(store.count(), 1)
            store.close()
        self.assertEqual(sorted(self._actual_info()), sorted(expected_table_info()))
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT width, filename FROM video_props").fetchall(), [(0, "kept.mp4")])
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_added_keyframe_version_column_defaults_to_current_measurement(self) -> None:
        """키프레임 측정 번호 열을 덧붙이면 기존 행은 현재 측정 번호로 간주되어 키프레임 값을 이어 받을 수 있는지 검증"""
        self._write_old_table(_old_create_sql(drop_column="keyframe_version"), version=1)
        store = AnalysisStore(self.db_path)
        self.assertEqual(store.count(), 1)
        store.close()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT keyframe_version FROM video_props").fetchall(), [(KEYFRAME_MEASURE_VERSION,)])

    def test_extra_column_triggers_copy_of_common_columns_only(self) -> None:
        """호환되지 않는 옛 열이 있으면 공통 열만 새 테이블로 복사하고 나머지 열은 버리며 행을 보존하는지 검증"""
        self._write_old_table(_old_create_sql(extra_column="legacy_note"), version=1, width=1920)
        with self.assertNoLogs(store_mod.logger, level=logging.WARNING):
            store = AnalysisStore(self.db_path)
            self.assertEqual(store.count(), 1)
            store.close()
        self.assertEqual(sorted(self._actual_info()), sorted(expected_table_info()))
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT width, filename, mtime_ns FROM video_props").fetchall(), [(1920, "kept.mp4", 200)])
            index_names = {row[1] for row in conn.execute("PRAGMA index_list(video_props)")}
        self.assertIn(store_mod.ANALYSIS_INDEX_NAME, index_names)

    def test_matching_schema_keeps_rows_without_touching_structure(self) -> None:
        """구조가 같고 버전 번호만 다르면 행과 구조를 건드리지 않고 번호만 맞추는지 검증"""
        self._write_old_table(SCHEMA_STATEMENTS[0], version=1, width=640)
        store = AnalysisStore(self.db_path)
        self.assertEqual(store.count(), 1)
        store.close()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT width FROM video_props").fetchall(), [(640,)])
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_failed_copy_preserves_the_damaged_file_and_recreates(self) -> None:
        """복사 중 DB 오류가 나면 원본 파일을 손상본 이름으로 보존하고 빈 캐시로 다시 만드는지 검증"""
        self._write_old_table(_old_create_sql(extra_column="legacy_note"), version=1)
        with (
            patch.object(store_mod, "_rebuild_table", side_effect=sqlite3.DatabaseError("강제 실패")),
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
        """복사본을 만들 디스크 여유가 없으면 원본 캐시를 건드리지 않고 캐시를 끄는지 검증"""
        self._write_old_table(_old_create_sql(extra_column="legacy_note"), version=1, width=720)
        with (
            patch.object(store_mod.shutil, "disk_usage", return_value=_NoSpace()),
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
