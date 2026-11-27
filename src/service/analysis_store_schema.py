"""분석 캐시 SQLite 저장소의 스키마 정의, DDL 생성, 구조 검증 및 마이그레이션 모듈

- VideoProps 필드에서 내용 행(`video_props`) 열 정의와 DDL 해시(`SCHEMA_VERSION`)를 자동 생성
- DB를 열 때 `user_version`과 `PRAGMA table_info`를 대조해 열 추가, 옛 파일명 키 이전, 반쯤 변환된 보조 표 재구성을 처리
"""

import dataclasses
import hashlib
import logging
import os
import shutil
import sqlite3
import types
import typing
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Union

from src.core.analysis.probe_version_rules import KEYFRAME_MEASURE_VERSION
from src.core.models import VideoProps
from src.service.sql_transaction import transaction

logger = logging.getLogger(__name__)

ANALYSIS_TABLE_NAME: str = "video_props"
LEGACY_TABLE_NAME: str = "video_props_legacy"
LOCATION_TABLE_NAME: str = "video_location"
LEGACY_INDEX_NAME: str = "idx_video_props_legacy_content"
LOCATION_INDEX_NAME: str = "idx_video_location_content"
# - 내용 행의 기본 키. 크기와 내용 지문이 같으면 이름이나 경로가 달라도 한 행
CONTENT_KEY_COLUMN_NAMES: tuple[str, ...] = ("filesize_bytes", "fingerprint")
# - 파일명 조회 키(파일명, 크기, 수정 시각)와 내용 행에 대표 값으로 두는 VideoProps 필드
FILE_KEY_COLUMN_NAMES: tuple[str, ...] = ("filename", "filesize_bytes", "mtime_ns")
SQL_TYPE_BY_PY_TYPE: dict[type, str] = {int: "INTEGER", float: "REAL", str: "TEXT", bool: "INTEGER", bytes: "BLOB"}
SCHEMA_HASH_HEX_DIGITS: int = 8
USER_VERSION_MASK: int = 0x7FFFFFFF
REBUILD_DISK_MARGIN: float = 2.0

ColumnInfo = tuple[str, str, bool, int]
_MIGRATED_TABLE_NAME: str = f"{ANALYSIS_TABLE_NAME}_migrated"
_NUMERIC_DEFAULT_SQL: str = "0"
_REAL_DEFAULT_SQL: str = "0.0"
_TEXT_DEFAULT_SQL: str = "''"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """테이블 열 한 개의 이름, SQLite 타입, NULL 허용 여부와 파이썬 원형 타입"""

    name: str
    sql_type: str
    not_null: bool
    py_type: type


def _unwrap_optional(hint: Any) -> tuple[type, bool]:
    """`T | None` 형태의 주석에서 원형 타입과 None 허용 여부를 분리"""
    origin = typing.get_origin(hint)
    if origin is Union or origin is types.UnionType:
        inner = [arg for arg in typing.get_args(hint) if arg is not type(None)]
        return inner[0], True
    return hint, False


def _sql_type_of(py_type: type) -> str:
    sql_type = SQL_TYPE_BY_PY_TYPE.get(py_type)
    if sql_type is None:
        raise TypeError(f"분석 캐시에 저장할 수 없는 타입입니다: {py_type}")
    return sql_type


def _build_props_columns() -> tuple[ColumnSpec, ...]:
    """VideoProps 필드 중 파일 식별 열(이름, 크기, 수정 시각)을 뺀 나머지를 저장 열로 생성"""
    hints = typing.get_type_hints(VideoProps)
    columns: list[ColumnSpec] = []
    for field in dataclasses.fields(VideoProps):
        if field.name in FILE_KEY_COLUMN_NAMES:
            continue
        py_type, nullable = _unwrap_optional(hints[field.name])
        columns.append(ColumnSpec(field.name, _sql_type_of(py_type), not nullable, py_type))
    return tuple(columns)


PROPS_COLUMNS: tuple[ColumnSpec, ...] = _build_props_columns()
META_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("fingerprint", "TEXT", True, str),
    ColumnSpec("probe_version", "INTEGER", True, int),
    ColumnSpec("keyframe_version", "INTEGER", True, int),
    ColumnSpec("probe_raw", "BLOB", False, bytes),
)
# - 열을 덧붙일 때 기존 행에 넣는 값. 키프레임 측정 방법이 바뀌지 않았으므로 기존 키프레임 값은 현재 번호로 간주
ADDED_COLUMN_DEFAULTS: dict[str, str] = {
    "keyframe_version": str(KEYFRAME_MEASURE_VERSION),
    "encode_keywords": "''",
    "fingerprint_lookup_size": "0",
    "stream_summary": "''",
}
KEY_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("filename", "TEXT", True, str),
    ColumnSpec("filesize_bytes", "INTEGER", True, int),
    ColumnSpec("mtime_ns", "INTEGER", True, int),
)
ALL_COLUMNS: tuple[ColumnSpec, ...] = (*KEY_COLUMNS, *META_COLUMNS, *PROPS_COLUMNS)
ALL_COLUMN_NAMES: tuple[str, ...] = tuple(col.name for col in ALL_COLUMNS)
STORED_PROPS_FIELDS: tuple[str, ...] = ("filename", *(col.name for col in PROPS_COLUMNS))

# - 키를 저장한 분석 범위(정규화한 작업 폴더 목록). 같은 이름의 옛 키를 지울 때 이 범위 안으로 한정
LEGACY_SCOPE_COLUMN: ColumnSpec = ColumnSpec("root_scope", "TEXT", True, str)
# - 파일명 조회 키 표. 이름과 수정 시각이 같은 파일이 가리키는 내용 지문을 남겨, 폴더를 옮겨도 지문 읽기 없이 찾게 함
LEGACY_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("filename", "TEXT", True, str),
    ColumnSpec("filesize_bytes", "INTEGER", True, int),
    ColumnSpec("mtime_ns", "INTEGER", True, int),
    ColumnSpec("fingerprint", "TEXT", True, str),
    LEGACY_SCOPE_COLUMN,
)
LEGACY_KEY_COLUMN_NAMES: tuple[str, ...] = ("filename", "filesize_bytes", "mtime_ns", "root_scope")
# - 볼륨 위치 표. 드라이브 문자나 상위 폴더가 바뀌어도 같은 값이 나오는 볼륨 식별자와 그 볼륨 기준 상대 경로로 키를 만듦
LOCATION_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("volume_id", "TEXT", True, str),
    ColumnSpec("volume_path", "TEXT", True, str),
    ColumnSpec("filesize_bytes", "INTEGER", True, int),
    ColumnSpec("mtime_ns", "INTEGER", True, int),
    ColumnSpec("fingerprint", "TEXT", True, str),
)
LOCATION_KEY_COLUMN_NAMES: tuple[str, ...] = ("volume_id", "volume_path")


def _create_table_sql(
    table_name: str, columns: Sequence[ColumnSpec], key_names: Sequence[str], *, if_not_exists: bool = False
) -> str:
    """열 정의와 기본 키로 WITHOUT ROWID 테이블 생성 SQL을 만듦"""
    column_lines = [
        f"  {col.name} {col.sql_type}{' NOT NULL' if col.not_null else ''}" for col in columns
    ]
    column_lines.append(f"  PRIMARY KEY ({', '.join(key_names)})")
    guard = "IF NOT EXISTS " if if_not_exists else ""
    return f"CREATE TABLE {guard}{table_name} (\n" + ",\n".join(column_lines) + "\n) WITHOUT ROWID"


CONTENT_TABLE_SQL: str = _create_table_sql(ANALYSIS_TABLE_NAME, ALL_COLUMNS, CONTENT_KEY_COLUMN_NAMES)
LEGACY_TABLE_SQL: str = _create_table_sql(
    LEGACY_TABLE_NAME, LEGACY_COLUMNS, LEGACY_KEY_COLUMN_NAMES, if_not_exists=True
)
LOCATION_TABLE_SQL: str = _create_table_sql(
    LOCATION_TABLE_NAME, LOCATION_COLUMNS, LOCATION_KEY_COLUMN_NAMES, if_not_exists=True
)
LEGACY_CONTENT_INDEX_SQL: str = (
    f"CREATE INDEX IF NOT EXISTS {LEGACY_INDEX_NAME} ON {LEGACY_TABLE_NAME} (filesize_bytes, fingerprint)"
)
# - 내용 행을 지울 때 위치 행이 같은 내용을 가리키는지 확인하므로 위치 표도 내용 키로 찾을 수 있어야 함
LOCATION_CONTENT_INDEX_SQL: str = (
    f"CREATE INDEX IF NOT EXISTS {LOCATION_INDEX_NAME} ON {LOCATION_TABLE_NAME} (filesize_bytes, fingerprint)"
)
# - 파일명 조회 키가 하나도 없는 내용 행(옛 버전에서 열만 추가한 DB)의 대표 파일명으로 키를 채움
# - 옛 키만 있던 DB의 행은 분석 범위를 알 수 없으므로 빈 범위로 두어 어느 작업 폴더의 키도 지우지 않게 함
LEGACY_BACKFILL_SQL: str = (
    f"INSERT OR IGNORE INTO {LEGACY_TABLE_NAME} (filename, filesize_bytes, mtime_ns, fingerprint, root_scope) "
    f"SELECT filename, filesize_bytes, mtime_ns, fingerprint, '' FROM {ANALYSIS_TABLE_NAME} "
    f"WHERE NOT EXISTS (SELECT 1 FROM {LEGACY_TABLE_NAME} AS k "
    f"WHERE k.filesize_bytes = {ANALYSIS_TABLE_NAME}.filesize_bytes "
    f"AND k.fingerprint = {ANALYSIS_TABLE_NAME}.fingerprint)"
)
SCHEMA_STATEMENTS: tuple[str, ...] = (
    CONTENT_TABLE_SQL,
    LEGACY_TABLE_SQL,
    LOCATION_TABLE_SQL,
    LEGACY_CONTENT_INDEX_SQL,
    LOCATION_CONTENT_INDEX_SQL,
)
SCHEMA_VERSION: int = (
    int(hashlib.sha256("\n".join(SCHEMA_STATEMENTS).encode("utf-8")).hexdigest()[:SCHEMA_HASH_HEX_DIGITS], 16)
    & USER_VERSION_MASK
)


def expected_table_info() -> list[ColumnInfo]:
    """PRAGMA table_info 결과와 비교할 (이름, 타입, NOT NULL, PK 순번) 목록"""
    return _expected_info(ALL_COLUMNS, CONTENT_KEY_COLUMN_NAMES)


def _actual_columns(conn: sqlite3.Connection) -> list[ColumnInfo]:
    rows = conn.execute(f"PRAGMA table_info({ANALYSIS_TABLE_NAME})").fetchall()
    return [(str(r[1]), str(r[2]).upper(), bool(r[3]), int(r[5])) for r in rows]


def _default_sql(col: ColumnSpec) -> str:
    """NOT NULL 열을 기존 행에 덧붙일 때 쓰는 기본값 SQL 리터럴"""
    if col.py_type is str:
        return _TEXT_DEFAULT_SQL
    if col.py_type is float:
        return _REAL_DEFAULT_SQL
    return _NUMERIC_DEFAULT_SQL


def _is_additive_change(actual: Sequence[ColumnInfo]) -> bool:
    """실제 열이 기대 열의 부분 집합이고 공통 열의 형식, NOT NULL, 키 구성이 같으면 참(열 추가만으로 맞출 수 있음)"""
    expected = {name: (sql_type, not_null, pk) for name, sql_type, not_null, pk in expected_table_info()}
    return all(expected.get(name) == (sql_type, not_null, pk) for name, sql_type, not_null, pk in actual)


def _add_missing_columns(conn: sqlite3.Connection, actual: Sequence[ColumnInfo]) -> None:
    present = {name for name, *_rest in actual}
    for col in ALL_COLUMNS:
        if col.name in present:
            continue
        not_null = f" NOT NULL DEFAULT {ADDED_COLUMN_DEFAULTS.get(col.name, _default_sql(col))}" if col.not_null else ""
        conn.execute(f"ALTER TABLE {ANALYSIS_TABLE_NAME} ADD COLUMN {col.name} {col.sql_type}{not_null}")


def _ensure_rebuild_space(conn: sqlite3.Connection) -> None:
    """복사본을 만들 디스크 여유가 부족하면 원본을 건드리지 않고 연결 오류로 중단(손상본 보존 경로로 보내지 않음)"""
    db_file = str(conn.execute("PRAGMA database_list").fetchone()[2])
    if not db_file:
        return
    required = os.path.getsize(db_file) * REBUILD_DISK_MARGIN
    if shutil.disk_usage(os.path.dirname(db_file)).free < required:
        raise sqlite3.OperationalError("분석 캐시 스키마 변환에 필요한 디스크 여유 공간이 부족합니다")


def _expected_info(columns: Sequence[ColumnSpec], key_names: Sequence[str]) -> list[ColumnInfo]:
    """열 정의와 기본 키 구성으로 비교할 (이름, 타입, NOT NULL, PK 순번) 목록"""
    rows: list[ColumnInfo] = []
    for col in columns:
        pk_pos = key_names.index(col.name) + 1 if col.name in key_names else 0
        rows.append((col.name, col.sql_type, col.not_null or pk_pos > 0, pk_pos))
    return rows


def _table_shape_ok(
    conn: sqlite3.Connection, table_name: str, columns: Sequence[ColumnSpec], key_names: Sequence[str]
) -> bool:
    """표의 열과 기본 키 구성이 기대와 같은지(열 이름만 맞고 키가 다른 반쯤 변환된 표를 걸러 냄)"""
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not rows:
        return False
    actual = [(str(r[1]), str(r[2]).upper(), bool(r[3]), int(r[5])) for r in rows]
    return sorted(actual) == sorted(_expected_info(columns, key_names))


def _rebuild_table(
    conn: sqlite3.Connection,
    table_name: str,
    columns: Sequence[ColumnSpec],
    key_names: Sequence[str],
    source_exprs: Sequence[str] | None,
) -> None:
    """표를 현재 구성으로 다시 만들고, 옮길 수 있는 행은 옮김(옮길 수 없으면 빈 표로 둠)"""
    tmp = f"{table_name}_rebuilt"
    conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    conn.execute(_create_table_sql(tmp, columns, key_names))
    if source_exprs is not None:
        names = ", ".join(col.name for col in columns)
        conn.execute(f"INSERT OR IGNORE INTO {tmp} ({names}) SELECT {', '.join(source_exprs)} FROM {table_name}")
    conn.execute(f"DROP TABLE {table_name}")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO {table_name}")


def _ensure_legacy_scope_column(conn: sqlite3.Connection) -> None:
    """조회 키 표의 열이나 기본 키가 현재 구성과 다르면 현재 구성으로 다시 만들어 행을 옮김

    - 같은 이름의 키가 작업 폴더 범위마다 따로 남아야 하므로 범위 열은 기본 키에 들어감(열만 더할 수 없음)
    - 옛 행의 범위는 알 수 없으므로 빈 문자열. 범위가 빈 키는 어느 작업 폴더의 프루닝에도 걸리지 않음
    - 범위 열은 있는데 기본 키에 빠진 반쯤 변환된 표도 이 검사로 다시 변환
    """
    if _table_shape_ok(conn, LEGACY_TABLE_NAME, LEGACY_COLUMNS, LEGACY_KEY_COLUMN_NAMES):
        return
    present = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({LEGACY_TABLE_NAME})").fetchall()}
    if not present:
        conn.execute(LEGACY_TABLE_SQL)
        return
    names = [col.name for col in LEGACY_COLUMNS]
    # - 범위 열만 없던 옛 표는 빈 범위로 옮기고, 그 밖의 열이 빠진 표는 행을 쓸 수 없어 비움
    if set(names) - {LEGACY_SCOPE_COLUMN.name} <= present:
        exprs: list[str] | None = [name if name in present else "''" for name in names]
    else:
        exprs = None
    _rebuild_table(conn, LEGACY_TABLE_NAME, LEGACY_COLUMNS, LEGACY_KEY_COLUMN_NAMES, exprs)


def _ensure_location_shape(conn: sqlite3.Connection) -> None:
    """위치 표의 열이나 기본 키가 현재 구성과 다르면 현재 구성으로 다시 만들어 행을 옮김"""
    if _table_shape_ok(conn, LOCATION_TABLE_NAME, LOCATION_COLUMNS, LOCATION_KEY_COLUMN_NAMES):
        return
    present = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({LOCATION_TABLE_NAME})").fetchall()}
    if not present:
        conn.execute(LOCATION_TABLE_SQL)
        return
    names = [col.name for col in LOCATION_COLUMNS]
    _rebuild_table(
        conn, LOCATION_TABLE_NAME, LOCATION_COLUMNS, LOCATION_KEY_COLUMN_NAMES, names if set(names) <= present else None
    )


def _ensure_aux_tables(conn: sqlite3.Connection) -> None:
    """파일명 조회 키 표와 위치 표를 만들고, 열, 기본 키, 인덱스가 현재 구성과 맞는지 확인

    - 조회 키 표를 이번에 새로 만들었다면(옛 내용 행만 있던 DB) 내용 행의 대표 파일명으로 키를 채움
    - 열만 맞고 키나 인덱스가 빠진 반쯤 변환된 표도 다시 만들어, 조회가 의존하는 구조를 갖추게 함
    """
    legacy_existed = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? AND sql LIKE '%root_scope%'",
            (LEGACY_TABLE_NAME,),
        ).fetchone()
        is not None
    )
    conn.execute(LEGACY_TABLE_SQL)
    _ensure_legacy_scope_column(conn)
    conn.execute(LOCATION_TABLE_SQL)
    _ensure_location_shape(conn)
    conn.execute(LEGACY_CONTENT_INDEX_SQL)
    conn.execute(LOCATION_CONTENT_INDEX_SQL)
    if not legacy_existed:
        conn.execute(LEGACY_BACKFILL_SQL)


def _create_content_table(conn: sqlite3.Connection, table_name: str) -> None:
    """현재 내용 행 스키마로 테이블을 만듬(마이그레이션 임시 이름 지원)"""
    sql = CONTENT_TABLE_SQL
    if table_name != ANALYSIS_TABLE_NAME:
        sql = sql.replace(f"CREATE TABLE {ANALYSIS_TABLE_NAME} (", f"CREATE TABLE {table_name} (", 1)
    conn.execute(sql)


def _source_exprs(present: Collection[str]) -> str:
    """옛 테이블에서 현재 열을 채우는 식. 없는 열은 덧붙일 때와 같은 기본값을 씀"""
    return ", ".join(
        col.name
        if col.name in present
        else (ADDED_COLUMN_DEFAULTS.get(col.name, _default_sql(col)) if col.not_null else "NULL")
        for col in ALL_COLUMNS
    )


def _newest_content_row_filter() -> str:
    """같은 내용(크기, 지문)의 옛 행 중 측정 버전이 가장 새 행만 고르는 조건

    - 같은 내용이 두 폴더에 있으면 옛 스키마에서는 파일명이 달라 두 행이므로, 옮길 때 한 행으로 합침
    - 측정 번호까지 같으면 파일명 순으로 하나만 골라 실행마다 결과가 달라지지 않게 함
    """
    table = ANALYSIS_TABLE_NAME
    return (
        f"NOT EXISTS (SELECT 1 FROM {table} AS newer "
        f"WHERE newer.filesize_bytes = {table}.filesize_bytes "
        f"AND newer.fingerprint = {table}.fingerprint "
        f"AND (newer.probe_version > {table}.probe_version "
        f"OR (newer.probe_version = {table}.probe_version "
        f"AND (newer.keyframe_version > {table}.keyframe_version "
        f"OR (newer.keyframe_version = {table}.keyframe_version "
        f"AND newer.filename < {table}.filename)))))"
    )


def _migrate_content_rows(conn: sqlite3.Connection, actual: Sequence[ColumnInfo], log: logging.Logger) -> None:
    """옛 파일 키 스키마의 행을 내용 행과 파일명 조회 키 행으로 옮김

    - 각 옛 행의 파일명과 수정 시각을 조회 키로 남겨 위치 행이 생기기 전까지 지문 읽기 없이 찾게 함
    - 같은 내용의 옛 행이 여럿이면 측정 버전이 가장 새 행만 내용 행으로 남김
    - 측정 번호 열이 없던 옛 테이블은 열을 먼저 덧붙여 최신 행 판정이 참조할 수 있게 함
    - 옛 행에 내용 식별 열(크기, 지문)이 없으면 옮길 수 없어 빈 캐시로 다시 만듦
    """
    present = {name for name, *_rest in actual}
    if not {"filename", "filesize_bytes", "mtime_ns", "fingerprint"} <= present:
        log.warning("분석 캐시 스키마가 현재 버전과 달라 캐시를 다시 만듭니다.")
        _recreate_table(conn)
        return
    _ensure_rebuild_space(conn)
    _ensure_aux_tables(conn)
    # - 측정 번호 열이 없던 옛 테이블도 최신 행 판정이 그 열을 참조할 수 있도록 열 추가 경로와 같은 기본값으로 먼저 덧붙임
    _add_missing_columns(conn, actual)
    _create_content_table(conn, _MIGRATED_TABLE_NAME)
    target_columns = ", ".join(ALL_COLUMN_NAMES)
    conn.execute(
        f"INSERT OR IGNORE INTO {_MIGRATED_TABLE_NAME} ({target_columns}) "
        f"SELECT {_source_exprs(present)} FROM {ANALYSIS_TABLE_NAME} WHERE {_newest_content_row_filter()}"
    )
    conn.execute(
        f"INSERT OR REPLACE INTO {LEGACY_TABLE_NAME} (filename, filesize_bytes, mtime_ns, fingerprint, root_scope) "
        f"SELECT filename, filesize_bytes, mtime_ns, fingerprint, '' FROM {ANALYSIS_TABLE_NAME}"
    )
    conn.execute(f"DROP TABLE {ANALYSIS_TABLE_NAME}")
    conn.execute(f"ALTER TABLE {_MIGRATED_TABLE_NAME} RENAME TO {ANALYSIS_TABLE_NAME}")


def _recreate_table(conn: sqlite3.Connection) -> None:
    """분석 캐시 테이블을 버리고 현재 스키마로 빈 테이블을 만듦(행을 옮길 수 없을 때만 사용)"""
    conn.execute(f"DROP TABLE IF EXISTS {ANALYSIS_TABLE_NAME}")
    conn.execute(f"DROP TABLE IF EXISTS {_MIGRATED_TABLE_NAME}")
    _create_content_table(conn, ANALYSIS_TABLE_NAME)
    _ensure_aux_tables(conn)


def ensure_schema(conn: sqlite3.Connection, *, log: logging.Logger = logger) -> None:
    """분석 캐시 테이블을 현재 스키마로 맞추며, 기존 행은 열 추가나 내용 행 이전으로 보존

    - 테이블이 없으면 새로 만들고, 구조가 같으면 버전 번호만 맞춤
    - 열 추가만 필요하면 ALTER TABLE로 덧붙이고, 그 밖의 변경은 옛 파일명 키를 조회 키로 옮긴 뒤 내용 행으로 합침
    - 옮기기나 교체가 실패하면 DatabaseError로 올라가 손상본 보존 경로(_open_with_recovery)가 처리
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (ANALYSIS_TABLE_NAME,)
    ).fetchone()
    with transaction(conn):
        if exists is None:
            _recreate_table(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return
        actual = _actual_columns(conn)
        if sorted(actual) == sorted(expected_table_info()):
            _ensure_aux_tables(conn)
        elif _is_additive_change(actual):
            _add_missing_columns(conn, actual)
            _ensure_aux_tables(conn)
        else:
            _migrate_content_rows(conn, actual, log)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
