"""재인코딩 작업 기록 저장소 모듈: 작업 배치, 항목 테이블과 쓰기 재시도

- 분석 캐시와 같은 SQLite 파일의 별도 테이블을 쓰며, 분석 캐시 스키마 재생성과 캐시 비우기는 이 테이블을 건드리지 않음
- 스레드마다 연결을 따로 열고 WAL 모드로 쓰며, 잠금 오류는 짧은 간격으로 다시 시도
- 저장 값은 상태 코드(EncodeState)와 설정, 계획 입력의 JSON 사본
"""

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TypeVar

from src.core.encode.encode_state import BATCH_STATE_CLOSED, BATCH_STATE_OPEN, EncodeState

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

ENCODE_BATCH_TABLE: str = "encode_batch"
ENCODE_ITEM_TABLE: str = "encode_item"
SQLITE_BUSY_TIMEOUT_SEC: float = 5.0
WRITE_RETRY_ATTEMPTS: int = 5
WRITE_RETRY_BASE_SEC: float = 0.05
_LOCKED_MARKERS: tuple[str, ...] = ("locked", "busy")
ITEM_UPDATABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "state",
        "reason",
        "part_path",
        "output_path",
        "archive_path",
        "started_at",
        "ended_at",
        "output_size",
        "decode_retry",
        "stderr_tail",
        "early_stop_progress",
        "early_stop_estimate_bytes",
    }
)
_ITEM_COLUMNS: tuple[str, ...] = (
    "batch_id",
    "source_path",
    "source_size",
    "source_mtime_ns",
    "plan_json",
    "state",
    "reason",
    "part_path",
    "output_path",
    "archive_path",
    "started_at",
    "ended_at",
    "output_size",
    "decode_retry",
    "stderr_tail",
    "early_stop_progress",
    "early_stop_estimate_bytes",
)
_BATCH_COLUMNS: tuple[str, ...] = ("batch_id", "root_dir", "created_at", "settings_json", "state")
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    f"CREATE TABLE IF NOT EXISTS {ENCODE_BATCH_TABLE} (\n"
    "  batch_id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  root_dir TEXT NOT NULL,\n"
    "  created_at TEXT NOT NULL,\n"
    "  settings_json TEXT NOT NULL,\n"
    "  state TEXT NOT NULL\n"
    ")",
    f"CREATE TABLE IF NOT EXISTS {ENCODE_ITEM_TABLE} (\n"
    "  batch_id INTEGER NOT NULL,\n"
    "  source_path TEXT NOT NULL,\n"
    "  source_size INTEGER NOT NULL,\n"
    "  source_mtime_ns INTEGER NOT NULL,\n"
    "  plan_json TEXT NOT NULL,\n"
    "  state TEXT NOT NULL,\n"
    "  reason TEXT NOT NULL DEFAULT '',\n"
    "  part_path TEXT,\n"
    "  output_path TEXT,\n"
    "  archive_path TEXT,\n"
    "  started_at TEXT,\n"
    "  ended_at TEXT,\n"
    "  output_size INTEGER,\n"
    "  decode_retry INTEGER NOT NULL DEFAULT 0,\n"
    "  stderr_tail TEXT NOT NULL DEFAULT '',\n"
    "  early_stop_progress REAL,\n"
    "  early_stop_estimate_bytes INTEGER,\n"
    "  PRIMARY KEY (batch_id, source_path)\n"
    ")",
)
# - 열 추가 전에 만든 DB는 CREATE TABLE IF NOT EXISTS가 건너뛰므로 빠진 열만 ALTER TABLE로 추가
_ADDED_ITEM_COLUMNS: tuple[tuple[str, str], ...] = (
    ("early_stop_progress", "REAL"),
    ("early_stop_estimate_bytes", "INTEGER"),
)


@dataclass(frozen=True, slots=True)
class NewItem:
    """배치에 새로 넣을 항목의 원본 식별 값과 계획 입력 JSON"""

    source_path: str
    source_size: int
    source_mtime_ns: int
    plan_json: str
    state: EncodeState = EncodeState.PENDING
    reason: str = ""


@dataclass(frozen=True, slots=True)
class BatchRow:
    """작업 배치 한 건"""

    batch_id: int
    root_dir: str
    created_at: str
    settings_json: str
    state: str


@dataclass(frozen=True, slots=True)
class ItemRow:
    """배치 항목 한 건의 저장 값"""

    batch_id: int
    source_path: str
    source_size: int
    source_mtime_ns: int
    plan_json: str
    state: EncodeState
    reason: str
    part_path: str | None
    output_path: str | None
    archive_path: str | None
    started_at: str | None
    ended_at: str | None
    output_size: int | None
    decode_retry: bool
    stderr_tail: str
    early_stop_progress: float | None = None
    early_stop_estimate_bytes: int | None = None


def utc_now_iso() -> str:
    """UTC 기준 ISO 8601 시각 문자열(초 단위)"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_locked_error(error: sqlite3.OperationalError) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in _LOCKED_MARKERS)


def _to_column_value(value: object) -> object:
    if isinstance(value, EncodeState):
        return value.value
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return int(value)
    return value


class EncodeHistory:
    """재인코딩 작업 배치와 항목을 SQLite 테이블에 기록하고 읽는 저장소

    - 생성자는 디스크에 접근하지 않으며, 스레드가 처음 쓸 때 해당 스레드의 연결을 연다
    - 잠금 오류는 WRITE_RETRY_ATTEMPTS 번까지 지수 간격으로 재시도하고, 그래도 실패하면 예외를 올림
    """

    def __init__(self, db_path: str, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._db_path: str = db_path
        self._sleep: Callable[[float], None] = sleep
        self._connections: dict[int, sqlite3.Connection] = {}
        self._lock: threading.Lock = threading.Lock()

    @property
    def db_path(self) -> str:
        """저장소 DB 파일 경로 반환"""
        return self._db_path

    def create_batch(self, root_dir: str, settings_json: str, items: Sequence[NewItem]) -> int:
        """배치와 항목을 한 트랜잭션으로 넣고 새 배치 번호를 반환"""

        def _insert(conn: sqlite3.Connection) -> int:
            with _Transaction(conn):
                cursor = conn.execute(
                    f"INSERT INTO {ENCODE_BATCH_TABLE} (root_dir, created_at, settings_json, state) VALUES (?, ?, ?, ?)",
                    (root_dir, utc_now_iso(), settings_json, BATCH_STATE_OPEN),
                )
                batch_id = int(cursor.lastrowid or 0)
                conn.executemany(
                    f"INSERT INTO {ENCODE_ITEM_TABLE} "
                    "(batch_id, source_path, source_size, source_mtime_ns, plan_json, state, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            batch_id,
                            item.source_path,
                            item.source_size,
                            item.source_mtime_ns,
                            item.plan_json,
                            item.state.value,
                            item.reason,
                        )
                        for item in items
                    ],
                )
            return batch_id

        return self._run(_insert)

    def list_open_batches(self) -> list[BatchRow]:
        """아직 닫히지 않은 배치를 번호 순으로 반환(이어서 재인코딩 대상)"""

        def _query(conn: sqlite3.Connection) -> list[BatchRow]:
            rows = conn.execute(
                f"SELECT {', '.join(_BATCH_COLUMNS)} FROM {ENCODE_BATCH_TABLE} WHERE state = ? ORDER BY batch_id",
                (BATCH_STATE_OPEN,),
            ).fetchall()
            return [
                BatchRow(int(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4])) for r in rows
            ]

        return self._run(_query)

    def list_items(self, batch_id: int) -> list[ItemRow]:
        """배치의 항목을 원본 경로 순으로 반환"""

        def _query(conn: sqlite3.Connection) -> list[ItemRow]:
            rows = conn.execute(
                f"SELECT {', '.join(_ITEM_COLUMNS)} FROM {ENCODE_ITEM_TABLE} WHERE batch_id = ? ORDER BY source_path",
                (batch_id,),
            ).fetchall()
            return [_row_to_item(row) for row in rows]

        return self._run(_query)

    def update_item(self, batch_id: int, source_path: str, fields: Mapping[str, object]) -> None:
        """항목의 지정 열만 갱신. 허용되지 않은 열 이름은 ValueError"""
        unknown = set(fields) - ITEM_UPDATABLE_COLUMNS
        if unknown:
            raise ValueError(f"갱신할 수 없는 항목 열입니다: {sorted(unknown)}")
        if not fields:
            return
        names = list(fields)
        assignments = ", ".join(f"{name} = ?" for name in names)
        values = [_to_column_value(fields[name]) for name in names]

        def _update(conn: sqlite3.Connection) -> None:
            conn.execute(
                f"UPDATE {ENCODE_ITEM_TABLE} SET {assignments} WHERE batch_id = ? AND source_path = ?",
                [*values, batch_id, source_path],
            )

        self._run(_update)

    def reset_running(self, batch_id: int) -> int:
        """진행 중으로 남은 항목을 대기로 되돌리고 되돌린 개수를 반환(이어서 재인코딩 시작 시)"""

        def _reset(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(
                f"UPDATE {ENCODE_ITEM_TABLE} SET state = ?, started_at = NULL WHERE batch_id = ? AND state = ?",
                (EncodeState.PENDING.value, batch_id, EncodeState.RUNNING.value),
            )
            return int(cursor.rowcount)

        return self._run(_reset)

    def close_batch(self, batch_id: int) -> None:
        """배치를 닫아 이어서 재인코딩 대상에서 빼냄"""
        self._run(
            lambda conn: conn.execute(
                f"UPDATE {ENCODE_BATCH_TABLE} SET state = ? WHERE batch_id = ?",
                (BATCH_STATE_CLOSED, batch_id),
            )
        )

    def delete_batch(self, batch_id: int) -> None:
        """배치와 그 항목을 지움(사용자가 기록을 버릴 때만 호출)"""

        def _delete(conn: sqlite3.Connection) -> None:
            with _Transaction(conn):
                conn.execute(f"DELETE FROM {ENCODE_ITEM_TABLE} WHERE batch_id = ?", (batch_id,))
                conn.execute(f"DELETE FROM {ENCODE_BATCH_TABLE} WHERE batch_id = ?", (batch_id,))

        self._run(_delete)

    def close(self) -> None:
        """열려 있는 모든 스레드의 연결을 닫음"""
        with self._lock:
            for conn in self._connections.values():
                conn.close()
            self._connections.clear()

    def _run(self, action: Callable[[sqlite3.Connection], _T]) -> _T:
        attempt = 0
        while True:
            try:
                return action(self._connection())
            except sqlite3.OperationalError as error:
                if not _is_locked_error(error) or attempt + 1 >= WRITE_RETRY_ATTEMPTS:
                    raise
                delay = WRITE_RETRY_BASE_SEC * (2**attempt)
                logger.debug("작업 기록 DB 잠금으로 %.2f초 뒤 재시도합니다", delay)
                self._sleep(delay)
                attempt += 1

    def _connection(self) -> sqlite3.Connection:
        ident = threading.get_ident()
        with self._lock:
            existing = self._connections.get(ident)
        if existing is not None:
            return existing
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(
            self._db_path,
            timeout=SQLITE_BUSY_TIMEOUT_SEC,
            check_same_thread=False,
            isolation_level=None,
        )
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
            _add_missing_item_columns(conn)
        except BaseException:
            conn.close()
            raise
        with self._lock:
            self._connections[ident] = conn
        return conn


class _Transaction:
    """BEGIN IMMEDIATE로 시작해 정상 종료 시 COMMIT, 예외 시 ROLLBACK"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn: sqlite3.Connection = conn

    def __enter__(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self._conn.execute("COMMIT")
        else:
            self._conn.execute("ROLLBACK")


def _add_missing_item_columns(conn: sqlite3.Connection) -> None:
    """항목 테이블에 없는 열만 추가. 기존 행의 값은 NULL로 남고 다른 테이블은 건드리지 않음"""
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({ENCODE_ITEM_TABLE})").fetchall()}
    for name, sql_type in _ADDED_ITEM_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE {ENCODE_ITEM_TABLE} ADD COLUMN {name} {sql_type}")


def _row_to_item(row: tuple[object, ...]) -> ItemRow:
    values = dict(zip(_ITEM_COLUMNS, row, strict=True))
    return ItemRow(
        batch_id=int(_as_int(values["batch_id"])),
        source_path=str(values["source_path"]),
        source_size=_as_int(values["source_size"]),
        source_mtime_ns=_as_int(values["source_mtime_ns"]),
        plan_json=str(values["plan_json"]),
        state=EncodeState(str(values["state"])),
        reason=str(values["reason"]),
        part_path=_as_optional_text(values["part_path"]),
        output_path=_as_optional_text(values["output_path"]),
        archive_path=_as_optional_text(values["archive_path"]),
        started_at=_as_optional_text(values["started_at"]),
        ended_at=_as_optional_text(values["ended_at"]),
        output_size=None if values["output_size"] is None else _as_int(values["output_size"]),
        decode_retry=bool(_as_int(values["decode_retry"])),
        stderr_tail=str(values["stderr_tail"]),
        early_stop_progress=_as_optional_float(values["early_stop_progress"]),
        early_stop_estimate_bytes=(
            None if values["early_stop_estimate_bytes"] is None else _as_int(values["early_stop_estimate_bytes"])
        ),
    )


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"정수 열 값이 아닙니다: {value!r}")
    return value


def _as_optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"실수 열 값이 아닙니다: {value!r}")
    return float(value)


def _as_optional_text(value: object) -> str | None:
    return None if value is None else str(value)
