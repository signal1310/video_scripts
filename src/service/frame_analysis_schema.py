"""화질 분석 캐시 표의 스키마, 내용 키 구성, 옛 파일명 키 행 승격

- 표 이름과 열 정의, DDL 생성, 옛 표에서 내용 키 표로 옮기는 마이그레이션을 한 곳에 모음
- 저장소(`frame_analysis_store.py`)는 이 모듈이 만든 표 위에서 행 입출력만 다룸
- 캐시 행은 내용 키(파일 크기, 내용 지문)로 식별하고, 옛 파일명 키 행은 지문을 알 때까지 따로 보존
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping, Sequence

from src.core.analysis.file_fingerprint import compute_fingerprint
from src.core.analysis.volume_identity import volume_location
from src.service.analysis_store import FileKey
from src.service.analysis_store_schema import (
    ANALYSIS_TABLE_NAME,
    CONTENT_KEY_COLUMN_NAMES,
    LEGACY_TABLE_NAME,
    LOCATION_TABLE_NAME,
)
from src.service.frame_analysis_record import (
    RECORD_COLUMN_TYPES,
    RECORD_COLUMNS,
    record_to_row,
    row_to_record,
)
from src.service.frame_override_rows import add_missing_columns

logger = logging.getLogger(__name__)

FRAME_ANALYSIS_TABLE: str = "frame_analysis_cache"
# - 옛 파일명 키 행을 지문을 알 때까지 보관하는 표
FRAME_ANALYSIS_LEGACY_TABLE: str = "frame_analysis_cache_legacy"
FRAME_ANALYSIS_VR_TABLE: str = "frame_analysis_vr_override"
FRAME_ANALYSIS_CROP_TABLE: str = "frame_analysis_crop_exclude"
FRAME_ANALYSIS_MANUAL_FRAME_TABLE: str = "frame_analysis_manual_active_frame"
FRAME_ANALYSIS_REVIEW_ACK_TABLE: str = "frame_analysis_crop_review_ack"
# - 1단계 분석 캐시의 위치 표와 파일명 조회 키 표. 같은 파일의 지문을 파일을 읽지 않고 얻는 조회 대상
ANALYSIS_LOCATION_TABLE: str = LOCATION_TABLE_NAME
ANALYSIS_LEGACY_TABLE: str = LEGACY_TABLE_NAME
# - 옛 파일명 키 표의 기본 키. 1단계 파일명 조회 키와 같은 구성
LEGACY_KEY_COLUMN_NAMES: tuple[str, ...] = ("filename", "filesize_bytes", "mtime_ns")
# - 옛 행에 없을 수 있어 NULL을 허용하는 열. 나머지 열은 키이거나 측정 결과라 NOT NULL
_NULLABLE_COLUMNS: frozenset[str] = frozenset(
    {"candidate_tier", "fingerprint", "unmeasurable_reason"}
)


def _allows_null(name: str, primary_key: Sequence[str]) -> bool:
    """그 열이 NULL을 허용하는지. 기본 키 열은 WITHOUT ROWID 표에서 SQLite가 NOT NULL로 보고함"""
    return name in _NULLABLE_COLUMNS and name not in primary_key


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """표가 있는지 확인"""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def table_columns(conn: sqlite3.Connection, table: str) -> dict[str, tuple[str, bool, int]]:
    """표의 열 이름별 (SQLite 타입, NOT NULL, 기본 키 순번)"""
    return {
        str(row[1]): (str(row[2]).upper(), bool(row[3]), int(row[5]))
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _expected_cache_columns() -> dict[str, tuple[str, bool, int]]:
    """현재 내용 행 표의 열 이름별 (SQLite 타입, NOT NULL, 기본 키 순번)"""
    key_positions = {name: index + 1 for index, name in enumerate(CONTENT_KEY_COLUMN_NAMES)}
    return {
        name: (sql_type, not _allows_null(name, CONTENT_KEY_COLUMN_NAMES), key_positions.get(name, 0))
        for name, sql_type in RECORD_COLUMN_TYPES
    }


def _content_table_is_current(conn: sqlite3.Connection) -> bool:
    """화질 분석 표가 내용 키 구성인지 확인. 옛 파일명 키 표는 기본 키 구성이 달라 거짓

    - 열을 덧붙인 표는 새 열이 끝에 붙어 순서가 다르므로 이름별로 비교
    """
    if not table_exists(conn, FRAME_ANALYSIS_TABLE):
        return False
    return table_columns(conn, FRAME_ANALYSIS_TABLE) == _expected_cache_columns()


def _table_sql(
    table_name: str,
    *,
    primary_key: Sequence[str],
    nullable: frozenset[str],
    if_not_exists: bool,
) -> str:
    """레코드 열 정의로 화질 분석 표 DDL을 만듬

    - 옛 행에 없을 수 있는 열만 NULL을 허용하고 나머지는 NOT NULL로 둠
    - 기본 키 열은 값이 반드시 있어야 하므로 NULL 허용 목록에 있어도 NOT NULL로 씀
    """
    lines: list[str] = []
    for name, sql_type in RECORD_COLUMN_TYPES:
        not_null = "" if name in nullable and name not in primary_key else " NOT NULL"
        lines.append(f"  {name} {sql_type}{not_null}")
    lines.append(f"  PRIMARY KEY ({', '.join(primary_key)})")
    guard = "IF NOT EXISTS " if if_not_exists else ""
    return f"CREATE TABLE {guard}{table_name} (\n" + ",\n".join(lines) + "\n) WITHOUT ROWID"


_CACHE_TABLE_SQL: str = _table_sql(
    FRAME_ANALYSIS_TABLE,
    primary_key=CONTENT_KEY_COLUMN_NAMES,
    nullable=_NULLABLE_COLUMNS,
    if_not_exists=True,
)
# - 옛 파일명 키 행은 지문을 알 때까지 이 표에 그대로 두고, 옮긴 행은 지움
# - 옛 표마다 빠진 열이 다르므로 키가 아닌 열은 모두 NULL을 허용해 옮길 수 있는 값만 넣음
_LEGACY_TABLE_SQL: str = _table_sql(
    FRAME_ANALYSIS_LEGACY_TABLE,
    primary_key=LEGACY_KEY_COLUMN_NAMES,
    nullable=frozenset(set(RECORD_COLUMNS) - set(LEGACY_KEY_COLUMN_NAMES)),
    if_not_exists=True,
)
# - 경로 키 지정 표들. 경로와 함께 내용 키(크기, 내용 지문)를 남겨 파일을 옮기거나 복사해도 지정을 찾음
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    _CACHE_TABLE_SQL,
    _LEGACY_TABLE_SQL,
    f"CREATE TABLE IF NOT EXISTS {FRAME_ANALYSIS_VR_TABLE} (\n"
    "  source_path TEXT PRIMARY KEY,\n"
    "  manual_vr INTEGER NOT NULL,\n"
    "  filesize_bytes INTEGER,\n"
    "  mtime_ns INTEGER,\n"
    "  fingerprint TEXT\n"
    ") WITHOUT ROWID",
    f"CREATE TABLE IF NOT EXISTS {FRAME_ANALYSIS_CROP_TABLE} (\n"
    "  source_path TEXT PRIMARY KEY,\n"
    "  exclude_crop INTEGER NOT NULL,\n"
    "  filesize_bytes INTEGER,\n"
    "  mtime_ns INTEGER,\n"
    "  fingerprint TEXT\n"
    ") WITHOUT ROWID",
    f"CREATE TABLE IF NOT EXISTS {FRAME_ANALYSIS_MANUAL_FRAME_TABLE} (\n"
    "  source_path TEXT PRIMARY KEY,\n"
    "  manual_x INTEGER NOT NULL,\n"
    "  manual_y INTEGER NOT NULL,\n"
    "  manual_width INTEGER NOT NULL,\n"
    "  manual_height INTEGER NOT NULL,\n"
    "  filesize_bytes INTEGER,\n"
    "  mtime_ns INTEGER,\n"
    "  fingerprint TEXT\n"
    ") WITHOUT ROWID",
    f"CREATE TABLE IF NOT EXISTS {FRAME_ANALYSIS_REVIEW_ACK_TABLE} (\n"
    "  source_path TEXT PRIMARY KEY,\n"
    "  region_width INTEGER NOT NULL,\n"
    "  region_height INTEGER NOT NULL,\n"
    "  region_x INTEGER NOT NULL,\n"
    "  region_y INTEGER NOT NULL,\n"
    "  filesize_bytes INTEGER,\n"
    "  mtime_ns INTEGER\n"
    ") WITHOUT ROWID",
)
SELECT_COLUMNS: str = ", ".join(RECORD_COLUMNS)
PLACEHOLDERS: str = ", ".join("?" * len(RECORD_COLUMNS))
# - CREATE TABLE IF NOT EXISTS는 기존 테이블에 열을 더하지 않으므로, 이전 DB에는 이 열을 따로 덧붙임
# - 사용자 지정 활성 화면의 파일 식별 값은 예전 행을 그대로 읽을 수 있도록 NULL을 허용
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    (FRAME_ANALYSIS_TABLE, "margins_vary", "INTEGER NOT NULL DEFAULT 0"),
    (FRAME_ANALYSIS_TABLE, "dark_outliers_ignored", "INTEGER NOT NULL DEFAULT 0"),
    (FRAME_ANALYSIS_TABLE, "crop_scan_failed", "INTEGER NOT NULL DEFAULT 0"),
    # - 옛 HDR 결과는 PQ 값을 그대로 잰 값이라 쓰지 않도록 0으로 두고 다시 잼
    (FRAME_ANALYSIS_TABLE, "hdr_tonemapped", "INTEGER NOT NULL DEFAULT 0"),
    (FRAME_ANALYSIS_TABLE, "unmeasurable_reason", "TEXT"),
    (FRAME_ANALYSIS_MANUAL_FRAME_TABLE, "filesize_bytes", "INTEGER"),
    (FRAME_ANALYSIS_MANUAL_FRAME_TABLE, "mtime_ns", "INTEGER"),
    (FRAME_ANALYSIS_MANUAL_FRAME_TABLE, "fingerprint", "TEXT"),
    (FRAME_ANALYSIS_CROP_TABLE, "filesize_bytes", "INTEGER"),
    (FRAME_ANALYSIS_CROP_TABLE, "mtime_ns", "INTEGER"),
    (FRAME_ANALYSIS_CROP_TABLE, "fingerprint", "TEXT"),
    (FRAME_ANALYSIS_VR_TABLE, "filesize_bytes", "INTEGER"),
    (FRAME_ANALYSIS_VR_TABLE, "mtime_ns", "INTEGER"),
    (FRAME_ANALYSIS_VR_TABLE, "fingerprint", "TEXT"),
)


def add_cache_missing_columns(conn: sqlite3.Connection) -> None:
    """이전 DB의 지정 테이블에 없는 열을 덧붙임"""
    add_missing_columns(conn, _ADDED_COLUMNS)


def _legacy_fingerprint_sql(conn: sqlite3.Connection, table: str, column: str) -> int:
    """다른 표에서 같은 파일명, 크기, 수정 시각의 지문을 옛 행에 채움. 채운 행 수 반환

    - 분석 캐시의 파일명 조회 키 표(`video_props_legacy`)와 내용 행이 같은 값을 남기므로 재사용
    - 표가 없거나 그 DB가 아니면 채우지 않음(화질 분석 캐시만 있는 DB에서도 동작)
    """
    if not table_exists(conn, table):
        return 0
    columns = table_columns(conn, table)
    if not {"filename", "filesize_bytes", "mtime_ns", column} <= set(columns):
        return 0
    cursor = conn.execute(
        f"UPDATE {FRAME_ANALYSIS_LEGACY_TABLE} SET fingerprint = ("
        f"SELECT {column} FROM {table} AS s "
        f"WHERE s.filename = {FRAME_ANALYSIS_LEGACY_TABLE}.filename "
        f"AND s.filesize_bytes = {FRAME_ANALYSIS_LEGACY_TABLE}.filesize_bytes "
        f"AND s.mtime_ns = {FRAME_ANALYSIS_LEGACY_TABLE}.mtime_ns LIMIT 1) "
        f"WHERE fingerprint IS NULL AND EXISTS ("
        f"SELECT 1 FROM {table} AS s "
        f"WHERE s.filename = {FRAME_ANALYSIS_LEGACY_TABLE}.filename "
        f"AND s.filesize_bytes = {FRAME_ANALYSIS_LEGACY_TABLE}.filesize_bytes "
        f"AND s.mtime_ns = {FRAME_ANALYSIS_LEGACY_TABLE}.mtime_ns)"
    )
    return cursor.rowcount


_TEXT_COLUMNS: frozenset[str] = frozenset(name for name, sql_type in RECORD_COLUMN_TYPES if sql_type == "TEXT")


def _legacy_select_columns() -> str:
    """옛 행을 내용 행으로 읽을 때 빠진 열을 기본값으로 채우는 SELECT 목록

    - 옛 표마다 없던 열이 다르므로 정수는 0, 글은 빈 문자열, 목록 JSON은 빈 배열로 채움
    - 후보 등급과 지문은 NULL을 그대로 두고 지문이 있는 행만 옮김
    """
    parts: list[str] = []
    for name, _sql_type in RECORD_COLUMN_TYPES:
        if name == "tier_scores_json":
            parts.append("COALESCE(tier_scores_json, '[]') AS tier_scores_json")
        elif name in _NULLABLE_COLUMNS:
            parts.append(name)
        elif name in _TEXT_COLUMNS:
            parts.append(f"COALESCE({name}, '') AS {name}")
        else:
            parts.append(f"COALESCE({name}, 0) AS {name}")
    return ", ".join(parts)


def promote_legacy_rows(conn: sqlite3.Connection) -> int:
    """지문을 아는 옛 행을 내용 행으로 옮기고 옛 행을 지움. 옮긴 행 수 반환

    - 같은 내용의 행이 여럿이면 측정 시각이 늦은 행의 값을 남김
    - 지문을 모르는 행은 그대로 두어 다음 조회에서 경로로 채움
    """
    rows = conn.execute(
        f"SELECT {_legacy_select_columns()} FROM {FRAME_ANALYSIS_LEGACY_TABLE} "
        "WHERE fingerprint IS NOT NULL ORDER BY measured_at, filename"
    ).fetchall()
    for row in rows:
        record = row_to_record(row)
        conn.execute(
            f"INSERT INTO {FRAME_ANALYSIS_TABLE} ({SELECT_COLUMNS}) VALUES ({PLACEHOLDERS}) "
            f"ON CONFLICT ({', '.join(CONTENT_KEY_COLUMN_NAMES)}) DO UPDATE SET "
            + ", ".join(f"{name} = excluded.{name}" for name in RECORD_COLUMNS if name not in CONTENT_KEY_COLUMN_NAMES)
            + f" WHERE excluded.measured_at >= {FRAME_ANALYSIS_TABLE}.measured_at",
            record_to_row(record),
        )
    if rows:
        conn.executemany(
            f"DELETE FROM {FRAME_ANALYSIS_LEGACY_TABLE} WHERE filename = ? AND filesize_bytes = ? AND mtime_ns = ?",
            [(str(row[0]), int(row[1]), int(row[2])) for row in rows],
        )
    return len(rows)


def _legacy_shape_ok(conn: sqlite3.Connection) -> bool:
    """옛 행 표가 현재 열 구성과 파일명 키 기본 키를 갖췄는지"""
    if not table_exists(conn, FRAME_ANALYSIS_LEGACY_TABLE):
        return False
    columns = table_columns(conn, FRAME_ANALYSIS_LEGACY_TABLE)
    if set(columns) != set(RECORD_COLUMNS):
        return False
    return all(columns.get(name, ("", False, 0))[2] == index for index, name in enumerate(LEGACY_KEY_COLUMN_NAMES, 1))


def _ensure_legacy_table(conn: sqlite3.Connection) -> None:
    """옛 행 표를 현재 열 구성으로 맞춤. 열이 모자라면 다시 만들어 옮길 수 있는 값만 옮김"""
    if not table_exists(conn, FRAME_ANALYSIS_LEGACY_TABLE):
        conn.execute(_LEGACY_TABLE_SQL)
        return
    if _legacy_shape_ok(conn):
        return
    present = set(table_columns(conn, FRAME_ANALYSIS_LEGACY_TABLE))
    tmp = f"{FRAME_ANALYSIS_LEGACY_TABLE}_rebuilt"
    conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    conn.execute(
        _table_sql(
            tmp,
            primary_key=LEGACY_KEY_COLUMN_NAMES,
            nullable=frozenset(set(RECORD_COLUMNS) - set(LEGACY_KEY_COLUMN_NAMES)),
            if_not_exists=False,
        )
    )
    if set(LEGACY_KEY_COLUMN_NAMES) <= present:
        names = ", ".join(name for name in RECORD_COLUMNS if name in present)
        conn.execute(f"INSERT OR IGNORE INTO {tmp} ({names}) SELECT {names} FROM {FRAME_ANALYSIS_LEGACY_TABLE}")
    conn.execute(f"DROP TABLE {FRAME_ANALYSIS_LEGACY_TABLE}")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO {FRAME_ANALYSIS_LEGACY_TABLE}")


_ADDITIVE_COLUMN_NAMES: frozenset[str] = frozenset(
    {name for table, name, _definition in _ADDED_COLUMNS if table == FRAME_ANALYSIS_TABLE}
)


def _content_table_needs_added_columns(conn: sqlite3.Connection) -> bool:
    """내용 키 기본 키는 갖췄고 일부 열만 빠진 표인지(행을 옮기지 않고 열만 덧붙일 수 있음)"""
    if not table_exists(conn, FRAME_ANALYSIS_TABLE):
        return False
    columns = table_columns(conn, FRAME_ANALYSIS_TABLE)
    if not all(columns.get(name, (None, False, 0))[2] > 0 for name in CONTENT_KEY_COLUMN_NAMES):
        return False
    missing = {name for name, _sql_type in RECORD_COLUMN_TYPES if name not in columns}
    return missing <= _ADDITIVE_COLUMN_NAMES


def migrate_cache_table(conn: sqlite3.Connection) -> None:
    """옛 파일명 키 화질 분석 표를 내용 키 표로 옮김

    - 옛 표의 행은 지문을 알 때까지 옛 키 표(`frame_analysis_cache_legacy`)에 보존
    - 옛 표에 없던 열은 NULL로 두고, 옮길 때 기본값으로 채움
    - 내용 키 표에 열만 모자란 경우는 `add_cache_missing_columns`가 처리하도록 그대로 둠
    """
    _ensure_legacy_table(conn)
    if _content_table_is_current(conn) or _content_table_needs_added_columns(conn):
        return
    if not table_exists(conn, FRAME_ANALYSIS_TABLE):
        conn.execute(_CACHE_TABLE_SQL)
        return
    old_columns = set(table_columns(conn, FRAME_ANALYSIS_TABLE))
    if {"filename", "filesize_bytes", "mtime_ns"} <= old_columns:
        names = ", ".join(name for name in RECORD_COLUMNS if name in old_columns)
        conn.execute(
            f"INSERT OR REPLACE INTO {FRAME_ANALYSIS_LEGACY_TABLE} ({names}) SELECT {names} FROM {FRAME_ANALYSIS_TABLE}"
        )
    else:
        logger.warning("화질 분석 캐시 스키마를 알 수 없어 캐시를 다시 만듭니다.")
    conn.execute(f"DROP TABLE {FRAME_ANALYSIS_TABLE}")
    conn.execute(_CACHE_TABLE_SQL)


def adopt_known_fingerprints(conn: sqlite3.Connection) -> None:
    """이미 저장된 지문으로 옛 파일명 키 화질 분석 행을 내용 행으로 옮김

    - 1단계 분석 캐시의 파일명 키 표와 내용 행이 같은 파일의 지문을 남기므로 파일을 읽지 않고 씀
    - 화질 분석을 쓰는 앞선 실행이 남긴 옛 행도 다음 연결에서 같은 방식으로 처리
    """
    _legacy_fingerprint_sql(conn, ANALYSIS_LEGACY_TABLE, "fingerprint")
    _legacy_fingerprint_sql(conn, ANALYSIS_TABLE_NAME, "fingerprint")
    promote_legacy_rows(conn)


def legacy_fingerprint_targets(conn: sqlite3.Connection, found: Mapping[FileKey, str]) -> dict[FileKey, str]:
    """찾은 지문 중 실제로 옛 행을 채우게 될 항목만 고름

    - 조회 경로는 채울 행이 있을 때만 쓰기를 시도해, 평소 새로고침은 순수 SELECT로 끝나게 함
    - 채울 행이 있는지만 먼저 확인해, 옛 행이 없을 때 표 전체 내용을 읽지 않음
    """
    if not found or not table_exists(conn, FRAME_ANALYSIS_LEGACY_TABLE):
        return {}
    if not legacy_rows_fillable(conn):
        return {}
    rows = conn.execute(
        f"SELECT filename, filesize_bytes, mtime_ns FROM {FRAME_ANALYSIS_LEGACY_TABLE} WHERE fingerprint IS NULL"
    ).fetchall()
    pending = {(str(row[0]), int(row[1]), int(row[2])) for row in rows}
    return {key: fingerprint for key, fingerprint in found.items() if key in pending}


def legacy_rows_fillable(conn: sqlite3.Connection) -> bool:
    """옛 행 표에 지문을 채울 행(지문이 빈 행)이 있는지"""
    if not table_exists(conn, FRAME_ANALYSIS_LEGACY_TABLE):
        return False
    row = conn.execute(
        f"SELECT 1 FROM {FRAME_ANALYSIS_LEGACY_TABLE} WHERE fingerprint IS NULL LIMIT 1"
    ).fetchone()
    return row is not None


def legacy_rows_promotable(conn: sqlite3.Connection) -> bool:
    """옛 행 표에 지문을 알아 내용 행으로 옮길 행이 있는지"""
    if not table_exists(conn, FRAME_ANALYSIS_LEGACY_TABLE):
        return False
    row = conn.execute(
        f"SELECT 1 FROM {FRAME_ANALYSIS_LEGACY_TABLE} WHERE fingerprint IS NOT NULL LIMIT 1"
    ).fetchone()
    return row is not None


def record_legacy_fingerprints(conn: sqlite3.Connection, found: Mapping[FileKey, str]) -> None:
    """찾은 지문을 옛 파일명 키 화질 분석 행에 채워 다음 연결에서 옮길 수 있게 함"""
    rows = [(fingerprint, name, size, mtime) for (name, size, mtime), fingerprint in found.items()]
    conn.executemany(
        f"UPDATE {FRAME_ANALYSIS_LEGACY_TABLE} SET fingerprint = ? "
        "WHERE filename = ? AND filesize_bytes = ? AND mtime_ns = ? AND fingerprint IS NULL",
        rows,
    )


def _fingerprint_from_table(conn: sqlite3.Connection, table: str, key: FileKey) -> str | None:
    """파일명, 크기, 수정 시각이 같은 행의 지문. 표나 열이 없거나 행이 없으면 None"""
    if not table_exists(conn, table):
        return None
    if not {"filename", "filesize_bytes", "mtime_ns", "fingerprint"} <= set(table_columns(conn, table)):
        return None
    row = conn.execute(
        f"SELECT fingerprint FROM {table} WHERE filename = ? AND filesize_bytes = ? AND mtime_ns = ? LIMIT 1",
        key,
    ).fetchone()
    return None if row is None or row[0] is None else str(row[0])


def stored_fingerprint(conn: sqlite3.Connection, path: str | None, key: FileKey) -> str | None:
    """저장된 값에서 파일의 내용 지문을 찾음. 1단계 분석 캐시 표를 먼저 씀

    - 위치 표는 경로가 같고 파일이 바뀌지 않은 행만 지문으로 인정
    - 파일명 키 표와 화질 분석 옛 행은 파일명, 크기, 수정 시각이 모두 같을 때만 씀
    """
    if path is not None and table_exists(conn, ANALYSIS_LOCATION_TABLE):
        columns = table_columns(conn, ANALYSIS_LOCATION_TABLE)
        if {"volume_id", "volume_path", "filesize_bytes", "mtime_ns", "fingerprint"} <= set(columns):
            location = volume_location(path)
            row = conn.execute(
                f"SELECT filesize_bytes, mtime_ns, fingerprint FROM {ANALYSIS_LOCATION_TABLE} "
                "WHERE volume_id = ? AND volume_path = ?",
                (location.volume_id, location.volume_path),
            ).fetchone()
            if row is not None and (int(row[0]), int(row[1])) == (key[1], key[2]):
                return str(row[2])
    for table in (ANALYSIS_LEGACY_TABLE, ANALYSIS_TABLE_NAME, FRAME_ANALYSIS_LEGACY_TABLE):
        fingerprint = _fingerprint_from_table(conn, table, key)
        if fingerprint is not None:
            return fingerprint
    return None


def computed_fingerprint(path: str | None, filesize_bytes: int) -> str | None:
    """파일을 읽어 내용 지문을 계산. 경로가 없거나 읽지 못하면 None"""
    if not path:
        return None
    try:
        return compute_fingerprint(path, filesize_bytes)
    except OSError:
        logger.warning("화질 분석 캐시 지문을 계산하지 못했습니다: %s", path)
        return None


def prepare_cache_tables(conn: sqlite3.Connection) -> None:
    """표 구성과 옛 행 승격을 적용해 이 연결에서 내용 키 조회가 가능하게 함

    - 연결을 열 때 한 번 불려 저장소는 행 입출력에만 집중
    """
    migrate_cache_table(conn)
    for statement in _SCHEMA_STATEMENTS:
        conn.execute(statement)
    add_cache_missing_columns(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        adopt_known_fingerprints(conn)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
