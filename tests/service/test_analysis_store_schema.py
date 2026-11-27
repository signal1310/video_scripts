"""분석 캐시 스키마 정의 및 마이그레이션 단위 테스트"""

import sqlite3
import unittest

from src.service.analysis_store_schema import (
    ANALYSIS_TABLE_NAME,
    LEGACY_KEY_COLUMN_NAMES,
    LEGACY_TABLE_NAME,
    LOCATION_KEY_COLUMN_NAMES,
    LOCATION_TABLE_NAME,
    SCHEMA_VERSION,
    ensure_schema,
    expected_table_info,
)


class TestAnalysisStoreSchema(unittest.TestCase):
    """스키마 생성과 버전 기록이 기대 구조와 일치하는지 검증"""

    def test_ensure_schema_creates_all_tables_and_user_version(self) -> None:
        conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(conn.close)
        ensure_schema(conn)

        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(version, SCHEMA_VERSION)

        actual = [
            (str(r[1]), str(r[2]).upper(), bool(r[3]), int(r[5]))
            for r in conn.execute(f"PRAGMA table_info({ANALYSIS_TABLE_NAME})").fetchall()
        ]
        self.assertEqual(sorted(actual), sorted(expected_table_info()))

        legacy_pk = [
            str(r[1])
            for r in sorted(
                (r for r in conn.execute(f"PRAGMA table_info({LEGACY_TABLE_NAME})").fetchall() if r[5] > 0),
                key=lambda r: int(r[5]),
            )
        ]
        self.assertEqual(legacy_pk, list(LEGACY_KEY_COLUMN_NAMES))

        location_pk = [
            str(r[1])
            for r in sorted(
                (r for r in conn.execute(f"PRAGMA table_info({LOCATION_TABLE_NAME})").fetchall() if r[5] > 0),
                key=lambda r: int(r[5]),
            )
        ]
        self.assertEqual(location_pk, list(LOCATION_KEY_COLUMN_NAMES))


if __name__ == "__main__":
    unittest.main()
