"""비디오 메타데이터의 영구 분석 캐시(SQLite) 저장소 모듈

- 스키마는 VideoProps 필드에서 자동 생성하며 DDL 해시를 PRAGMA user_version에 기록
- 열 때 user_version과 table_info를 대조해 불일치하면 마이그레이션 없이 테이블을 다시 만듦
- DB 오류는 경고 로그 후 캐시 없이 계속하도록 이 모듈 안에서 흡수
"""

import dataclasses
import hashlib
import logging
import os
import sqlite3
import threading
import time
import types
import typing
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar, Union

from src.core.file_fingerprint import (
    FINGERPRINT_CHUNK_BYTES as FINGERPRINT_CHUNK_BYTES,
)
from src.core.file_fingerprint import (
    FINGERPRINT_DIGEST_BYTES as FINGERPRINT_DIGEST_BYTES,
)
from src.core.file_fingerprint import (
    compute_fingerprint as compute_fingerprint,
)
from src.core.models import VideoProps
from src.core.paths import get_data_dir
from src.core.probe_parser import PROBE_VERSION
from src.core.resolution_tiers import copy_manual_targets

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

ANALYSIS_DB_FILENAME: str = "video_cache.sqlite3"
ANALYSIS_TABLE_NAME: str = "video_props"
ANALYSIS_INDEX_NAME: str = "idx_video_props_size_fp"
KEY_COLUMN_NAMES: tuple[str, ...] = ("filename", "filesize_bytes", "mtime_ns")
SQL_TYPE_BY_PY_TYPE: dict[type, str] = {int: "INTEGER", float: "REAL", str: "TEXT", bool: "INTEGER"}
SQLITE_BUSY_TIMEOUT_SEC: float = 5.0
LOOKUP_CHUNK_SIZE: int = 500
SCHEMA_HASH_HEX_DIGITS: int = 8
USER_VERSION_MASK: int = 0x7FFFFFFF
SQLITE_SIDE_FILE_SUFFIXES: tuple[str, ...] = ("", "-wal", "-shm")

FileKey = tuple[str, int, int]


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """테이블 열 한 개의 이름, SQLite 타입, NULL 허용 여부와 파이썬 원형 타입"""

    name: str
    sql_type: str
    not_null: bool
    py_type: type


@dataclass(slots=True)
class StoredEntry:
    """분석 캐시 한 행: 파일 식별 키(이름, 크기, 수정 시각), 내용 지문, 메타데이터"""

    filename: str
    filesize_bytes: int
    mtime_ns: int
    fingerprint: str
    props: VideoProps

    @property
    def key(self) -> FileKey:
        """파일 식별 키(이름, 크기, 수정 시각) 반환"""
        return (self.filename, self.filesize_bytes, self.mtime_ns)


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
    """VideoProps 필드 중 키 열을 뺀 나머지를 저장 열로 생성"""
    hints = typing.get_type_hints(VideoProps)
    columns: list[ColumnSpec] = []
    for field in dataclasses.fields(VideoProps):
        if field.name in KEY_COLUMN_NAMES:
            continue
        py_type, nullable = _unwrap_optional(hints[field.name])
        columns.append(ColumnSpec(field.name, _sql_type_of(py_type), not nullable, py_type))
    return tuple(columns)


PROPS_COLUMNS: tuple[ColumnSpec, ...] = _build_props_columns()
META_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("fingerprint", "TEXT", True, str),
    ColumnSpec("probe_version", "INTEGER", True, int),
)
KEY_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("filename", "TEXT", True, str),
    ColumnSpec("filesize_bytes", "INTEGER", True, int),
    ColumnSpec("mtime_ns", "INTEGER", True, int),
)
ALL_COLUMNS: tuple[ColumnSpec, ...] = (*KEY_COLUMNS, *META_COLUMNS, *PROPS_COLUMNS)
ALL_COLUMN_NAMES: tuple[str, ...] = tuple(col.name for col in ALL_COLUMNS)
STORED_PROPS_FIELDS: tuple[str, ...] = ("filename", *(col.name for col in PROPS_COLUMNS))


def _build_schema_statements() -> tuple[str, ...]:
    column_lines = [
        f"  {col.name} {col.sql_type}{' NOT NULL' if col.not_null else ''}" for col in ALL_COLUMNS
    ]
    column_lines.append(f"  PRIMARY KEY ({', '.join(KEY_COLUMN_NAMES)})")
    table_sql = f"CREATE TABLE {ANALYSIS_TABLE_NAME} (\n" + ",\n".join(column_lines) + "\n) WITHOUT ROWID"
    index_sql = f"CREATE INDEX {ANALYSIS_INDEX_NAME} ON {ANALYSIS_TABLE_NAME} (filesize_bytes, fingerprint)"
    return (table_sql, index_sql)


SCHEMA_STATEMENTS: tuple[str, ...] = _build_schema_statements()
SCHEMA_VERSION: int = (
    int(hashlib.sha256("\n".join(SCHEMA_STATEMENTS).encode("utf-8")).hexdigest()[:SCHEMA_HASH_HEX_DIGITS], 16)
    & USER_VERSION_MASK
)
_SELECT_COLUMNS: str = ", ".join(ALL_COLUMN_NAMES)
_PLACEHOLDERS: str = ", ".join("?" * len(ALL_COLUMNS))


def lookup_by_names_sql(placeholder_count: int) -> str:
    """파일명 IN 조회 SQL, 기본 키 접두사로 검색되어야 함"""
    placeholders = ", ".join("?" * placeholder_count)
    return (
        f"SELECT {_SELECT_COLUMNS} FROM {ANALYSIS_TABLE_NAME} "
        f"WHERE filename IN ({placeholders}) AND probe_version = ?"
    )


def lookup_by_sizes_sql(placeholder_count: int) -> str:
    """파일 크기 IN 조회 SQL, 크기와 지문 보조 인덱스로 검색되어야 함"""
    placeholders = ", ".join("?" * placeholder_count)
    return (
        f"SELECT {_SELECT_COLUMNS} FROM {ANALYSIS_TABLE_NAME} "
        f"WHERE filesize_bytes IN ({placeholders}) AND probe_version = ?"
    )


def expected_table_info() -> list[tuple[str, str, bool, int]]:
    """PRAGMA table_info 결과와 비교할 (이름, 타입, NOT NULL, PK 순번) 목록"""
    rows: list[tuple[str, str, bool, int]] = []
    for col in ALL_COLUMNS:
        pk_pos = KEY_COLUMN_NAMES.index(col.name) + 1 if col.name in KEY_COLUMN_NAMES else 0
        rows.append((col.name, col.sql_type, col.not_null or pk_pos > 0, pk_pos))
    return rows


def _row_to_entry(row: sqlite3.Row | tuple[Any, ...], columns: Sequence[str]) -> StoredEntry:
    values = dict(zip(columns, row, strict=True))
    prop_kwargs: dict[str, Any] = {}
    for col in PROPS_COLUMNS:
        value = values[col.name]
        prop_kwargs[col.name] = bool(value) if col.py_type is bool and value is not None else value
    props = VideoProps(filename=str(values["filename"]), **prop_kwargs)
    return StoredEntry(
        filename=str(values["filename"]),
        filesize_bytes=int(values["filesize_bytes"]),
        mtime_ns=int(values["mtime_ns"]),
        fingerprint=str(values["fingerprint"]),
        props=props,
    )


def _entry_to_row(entry: StoredEntry) -> tuple[Any, ...]:
    values: dict[str, Any] = {
        "filename": entry.filename,
        "filesize_bytes": entry.filesize_bytes,
        "mtime_ns": entry.mtime_ns,
        "fingerprint": entry.fingerprint,
        "probe_version": PROBE_VERSION,
    }
    for col in PROPS_COLUMNS:
        values[col.name] = getattr(entry.props, col.name)
    return tuple(values[name] for name in ALL_COLUMN_NAMES)


def _chunks(items: Sequence[_T], size: int) -> Iterable[Sequence[_T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _table_matches(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(f"PRAGMA table_info({ANALYSIS_TABLE_NAME})").fetchall()
    actual = [(str(r[1]), str(r[2]).upper(), bool(r[3]), int(r[5])) for r in rows]
    return actual == expected_table_info()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version == SCHEMA_VERSION and _table_matches(conn):
        return
    if version != 0 or conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (ANALYSIS_TABLE_NAME,)
    ).fetchone():
        logger.warning("분석 캐시 스키마가 현재 버전과 달라 캐시를 다시 만듭니다.")
    conn.execute(f"DROP TABLE IF EXISTS {ANALYSIS_TABLE_NAME}")
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


MANUAL_TARGET_TABLE_NAME: str = "manual_target"
_MANUAL_TARGET_TABLE_SQL: str = (
    f"CREATE TABLE {MANUAL_TARGET_TABLE_NAME} (\n"
    "  root_key TEXT NOT NULL,\n"
    "  filename TEXT NOT NULL,\n"
    "  tier TEXT NOT NULL,\n"
    "  PRIMARY KEY (root_key, filename)\n"
    ") WITHOUT ROWID"
)
_MANUAL_TARGET_INSERT_SQL: str = (
    f"INSERT INTO {MANUAL_TARGET_TABLE_NAME} (root_key, filename, tier) VALUES (?, ?, ?)"
)


def _manual_target_rows(store: Mapping[str, Mapping[str, str]]) -> list[tuple[str, str, str]]:
    return [(root, name, tier) for root, entries in store.items() for name, tier in entries.items()]


def _ensure_manual_target_table(conn: sqlite3.Connection) -> bool:
    """직접 지정 테이블이 없으면 만들고, 이번 호출에서 새로 만들었는지 반환

    - 분석 캐시 스키마 검사와 무관하게 유지되어 VideoProps 변경으로 사라지지 않음
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (MANUAL_TARGET_TABLE_NAME,)
    ).fetchone()
    if exists is None:
        conn.execute(_MANUAL_TARGET_TABLE_SQL)
        return True
    return False


def _read_manual_targets(
    conn: sqlite3.Connection, legacy: dict[str, dict[str, str]]
) -> dict[str, dict[str, str]]:
    # - 테이블 생성과 옛 맵 이전을 한 트랜잭션으로 묶어 중간 실패 시 이전이 반복되지 않게 함
    with _transaction(conn):
        if _ensure_manual_target_table(conn):
            conn.executemany(_MANUAL_TARGET_INSERT_SQL, _manual_target_rows(legacy))
        rows = conn.execute(
            f"SELECT root_key, filename, tier FROM {MANUAL_TARGET_TABLE_NAME}"
        ).fetchall()
    result: dict[str, dict[str, str]] = {}
    for root, name, tier in rows:
        result.setdefault(str(root), {})[str(name)] = str(tier)
    return result


def _write_manual_targets(conn: sqlite3.Connection, store: Mapping[str, Mapping[str, str]]) -> None:
    with _transaction(conn):
        _ensure_manual_target_table(conn)
        conn.execute(f"DELETE FROM {MANUAL_TARGET_TABLE_NAME}")
        conn.executemany(_MANUAL_TARGET_INSERT_SQL, _manual_target_rows(store))


TABLE_SELECTION_TABLE_NAME: str = "table_selection"
_TABLE_SELECTION_TABLE_SQL: str = (
    f"CREATE TABLE IF NOT EXISTS {TABLE_SELECTION_TABLE_NAME} (\n"
    "  root_key TEXT NOT NULL,\n"
    "  filename TEXT NOT NULL,\n"
    "  PRIMARY KEY (root_key, filename)\n"
    ") WITHOUT ROWID"
)
_TABLE_SELECTION_INSERT_SQL: str = (
    f"INSERT OR IGNORE INTO {TABLE_SELECTION_TABLE_NAME} (root_key, filename) VALUES (?, ?)"
)


def _read_table_selection(conn: sqlite3.Connection, root_key: str) -> set[str]:
    with _transaction(conn):
        conn.execute(_TABLE_SELECTION_TABLE_SQL)
        rows = conn.execute(
            f"SELECT filename FROM {TABLE_SELECTION_TABLE_NAME} WHERE root_key = ?",
            (root_key,),
        ).fetchall()
    return {str(row[0]) for row in rows}


def _write_table_selection(
    conn: sqlite3.Connection, root_key: str, filenames: Iterable[str]
) -> None:
    entries = [(root_key, name) for name in filenames if name]
    with _transaction(conn):
        conn.execute(_TABLE_SELECTION_TABLE_SQL)
        conn.execute(
            f"DELETE FROM {TABLE_SELECTION_TABLE_NAME} WHERE root_key = ?",
            (root_key,),
        )
        if entries:
            conn.executemany(_TABLE_SELECTION_INSERT_SQL, entries)


def _preserve_corrupt_db_files(db_path: str) -> str:
    """손상된 DB와 보조 파일을 삭제하지 않고 .corrupt-<나노초> 이름으로 옮겨 보존하고, 붙인 접미사를 반환

    - 작업 기록 테이블이 같은 파일에 있으므로 손상 복구에서도 파일을 지우지 않음
    """
    stamp = f".corrupt-{time.time_ns()}"
    for suffix in SQLITE_SIDE_FILE_SUFFIXES:
        path = db_path + suffix
        if os.path.exists(path):
            os.replace(path, path + stamp)
    return stamp


class AnalysisStore:
    """비디오 메타데이터 분석 결과를 SQLite 파일에 보관하는 저장소

    생성자는 디스크에 접근하지 않으며 첫 작업 시 연결과 스키마 검증을 수행
    연결 실패 또는 DB 오류 시 경고 로그를 남기고 빈 결과를 반환해 호출자가 인메모리로 계속하게 함
    """

    def __init__(self, db_path: str) -> None:
        self._db_path: str = db_path
        self._conn: sqlite3.Connection | None = None
        self._disabled: bool = False
        self._lock: threading.Lock = threading.Lock()

    @property
    def db_path(self) -> str:
        """저장소 DB 파일 경로 반환"""
        return self._db_path

    def fetch_by_names(self, filenames: Sequence[str]) -> list[StoredEntry]:
        """파일명 기준 일치 행을 조회하며 현재 PROBE_VERSION 행만 반환"""
        names = list(dict.fromkeys(filenames))

        def _query(conn: sqlite3.Connection) -> list[StoredEntry]:
            found: list[StoredEntry] = []
            for chunk in _chunks(names, LOOKUP_CHUNK_SIZE):
                cursor = conn.execute(lookup_by_names_sql(len(chunk)), [*chunk, PROBE_VERSION])
                found.extend(_row_to_entry(row, ALL_COLUMN_NAMES) for row in cursor.fetchall())
            return found

        if not names:
            return []
        return self._run(_query, [])

    def fetch_by_sizes(self, sizes: Sequence[int]) -> list[StoredEntry]:
        """파일 크기 기준 일치 행을 조회하며 현재 PROBE_VERSION 행만 반환"""
        unique_sizes = list(dict.fromkeys(sizes))

        def _query(conn: sqlite3.Connection) -> list[StoredEntry]:
            found: list[StoredEntry] = []
            for chunk in _chunks(unique_sizes, LOOKUP_CHUNK_SIZE):
                cursor = conn.execute(lookup_by_sizes_sql(len(chunk)), [*chunk, PROBE_VERSION])
                found.extend(_row_to_entry(row, ALL_COLUMN_NAMES) for row in cursor.fetchall())
            return found

        if not unique_sizes:
            return []
        return self._run(_query, [])

    def apply(self, delete_keys: Sequence[FileKey], entries: Sequence[StoredEntry]) -> None:
        """지정 키 행을 삭제하고 항목을 저장(동일 키는 덮어쓰기)을 한 트랜잭션으로 수행"""
        if not delete_keys and not entries:
            return
        rows = [_entry_to_row(entry) for entry in entries]

        def _write(conn: sqlite3.Connection) -> None:
            with _transaction(conn):
                conn.executemany(
                    f"DELETE FROM {ANALYSIS_TABLE_NAME} WHERE filename = ? AND filesize_bytes = ? AND mtime_ns = ?",
                    delete_keys,
                )
                conn.executemany(
                    f"INSERT OR REPLACE INTO {ANALYSIS_TABLE_NAME} ({_SELECT_COLUMNS}) VALUES ({_PLACEHOLDERS})",
                    rows,
                )

        self._run(_write, None)

    def count(self) -> int:
        """저장된 전체 행 수 반환"""
        return self._run(
            lambda conn: int(conn.execute(f"SELECT COUNT(*) FROM {ANALYSIS_TABLE_NAME}").fetchone()[0]),
            0,
        )

    def clear(self) -> int:
        """모든 행을 삭제하고 삭제한 행 수 반환"""

        def _delete(conn: sqlite3.Connection) -> int:
            with _transaction(conn):
                total = int(conn.execute(f"SELECT COUNT(*) FROM {ANALYSIS_TABLE_NAME}").fetchone()[0])
                conn.execute(f"DELETE FROM {ANALYSIS_TABLE_NAME}")
            return total

        return self._run(_delete, 0)

    def read_manual_targets(
        self, legacy: Mapping[str, Mapping[str, str]]
    ) -> dict[str, dict[str, str]]:
        """작업 폴더별 출력 해상도 직접 지정 맵을 전용 테이블에서 읽음

        - 테이블을 처음 만드는 때만 설정 파일에 남은 옛 맵(legacy)을 옮김
        - DB를 쓸 수 없으면 legacy 사본을 반환해 현재 세션의 지정이 사라지지 않게 함
        """
        fallback = copy_manual_targets(legacy)
        return self._run(lambda conn: _read_manual_targets(conn, fallback), fallback)

    def write_manual_targets(self, store: Mapping[str, Mapping[str, str]]) -> None:
        """작업 폴더별 직접 지정 맵 전체를 전용 테이블에 한 트랜잭션으로 교체 저장"""
        self._run(lambda conn: _write_manual_targets(conn, store), None)

    def read_table_selection(self, root_key: str) -> set[str]:
        """작업 폴더에 마지막으로 선택되어 있던 영상 파일명 집합을 전용 테이블에서 읽음"""
        if not root_key:
            return set()
        return self._run(lambda conn: _read_table_selection(conn, root_key), set())

    def write_table_selection(self, root_key: str, filenames: Iterable[str]) -> None:
        """작업 폴더에 선택된 영상 파일명 집합을 전용 테이블에 한 트랜잭션으로 교체 저장"""
        if not root_key:
            return
        names = list(dict.fromkeys(filenames))
        self._run(lambda conn: _write_table_selection(conn, root_key, names), None)

    def close(self) -> None:
        """열려 있는 DB 연결을 닫으며 이후 작업 시 다시 연결"""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _run(self, action: Callable[[sqlite3.Connection], _T], default: _T) -> _T:
        with self._lock:
            if self._disabled:
                return default
            try:
                if self._conn is None:
                    self._conn = self._open_with_recovery()
                return action(self._conn)
            except (sqlite3.Error, OSError) as e:
                logger.warning("분석 캐시 DB 작업에 실패해 캐시 없이 계속합니다: %s", e)
                if self._conn is None:
                    self._disabled = True
                return default

    def _open_with_recovery(self) -> sqlite3.Connection:
        try:
            return self._open()
        except sqlite3.OperationalError:
            # - 잠금, 경로 오류는 파일 손상이 아니므로 삭제하지 않고 상위에서 비활성화
            raise
        except sqlite3.DatabaseError as e:
            stamp = _preserve_corrupt_db_files(self._db_path)
            logger.warning("분석 캐시 DB가 손상되어 손상본을 %s 이름으로 보존하고 다시 만듭니다: %s", stamp, e)
            return self._open()

    def _open(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        conn = sqlite3.connect(
            self._db_path,
            timeout=SQLITE_BUSY_TIMEOUT_SEC,
            check_same_thread=False,
            isolation_level=None,
        )
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            _ensure_schema(conn)
        except BaseException:
            conn.close()
            raise
        return conn


class _transaction:
    """BEGIN IMMEDIATE로 시작해 정상 종료 시 COMMIT, 예외 시 ROLLBACK 하는 컨텍스트"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn: sqlite3.Connection = conn

    def __enter__(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self._conn.execute("COMMIT")
        else:
            self._conn.execute("ROLLBACK")


def open_default_store() -> AnalysisStore:
    """data 폴더의 분석 캐시 DB를 가리키는 저장소 생성, 실제 연결은 첫 작업 시 수행"""
    return AnalysisStore(os.path.join(get_data_dir(), ANALYSIS_DB_FILENAME))


def load_manual_target_store(
    legacy: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    """기본 DB에서 출력 해상도 직접 지정 맵을 읽는 편의 함수, 옛 맵은 테이블 최초 생성 때만 사용"""
    store = open_default_store()
    try:
        return store.read_manual_targets(legacy)
    finally:
        store.close()


def save_manual_target_store(store: Mapping[str, Mapping[str, str]]) -> None:
    """기본 DB에 출력 해상도 직접 지정 맵 전체를 저장하는 편의 함수"""
    db = open_default_store()
    try:
        db.write_manual_targets(store)
    finally:
        db.close()


def load_table_selection(root_key: str) -> set[str]:
    """기본 DB에서 작업 폴더의 저장된 테이블 선택 파일명 집합을 읽는 편의 함수"""
    if not root_key:
        return set()
    store = open_default_store()
    try:
        return store.read_table_selection(root_key)
    finally:
        store.close()


def save_table_selection(root_key: str, filenames: Iterable[str]) -> None:
    """기본 DB에 작업 폴더의 테이블 선택 파일명 집합을 저장하는 편의 함수"""
    if not root_key:
        return
    store = open_default_store()
    try:
        store.write_table_selection(root_key, filenames)
    finally:
        store.close()
