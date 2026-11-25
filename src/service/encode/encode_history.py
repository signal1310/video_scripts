"""인코딩 작업 기록 저장소 모듈: 작업 배치, 항목 테이블과 쓰기 재시도

- 분석 캐시와 같은 SQLite 파일의 별도 테이블을 쓰며, 분석 캐시 스키마 재생성과 캐시 비우기는 이 테이블을 건드리지 않음
- 스레드마다 연결을 따로 열고 WAL 모드로 쓰며, 잠금 오류는 짧은 간격으로 다시 시도
- 저장 값은 상태 코드(EncodeState)와 설정, 계획 입력의 JSON 사본
"""

import json
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

from src.core.encode.encode_previous_discard import DiscardFact
from src.core.encode.encode_state import BATCH_STATE_CLOSED, BATCH_STATE_OPEN, EncodeState
from src.core.workspace_paths import is_within_roots, parse_root_dirs

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
        "tuned_quality",
        "sample_predicted_bytes",
        "tune_retry_count",
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
    "tuned_quality",
    "sample_predicted_bytes",
    "tune_retry_count",
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
    "  tuned_quality REAL,\n"
    "  sample_predicted_bytes INTEGER,\n"
    "  tune_retry_count INTEGER NOT NULL DEFAULT 0,\n"
    "  PRIMARY KEY (batch_id, source_path)\n"
    ")",
)
# - 열 추가 전에 만든 DB는 CREATE TABLE IF NOT EXISTS가 건너뛰므로 빠진 열만 ALTER TABLE로 추가
_ADDED_ITEM_COLUMNS: tuple[tuple[str, str], ...] = (
    ("early_stop_progress", "REAL"),
    ("early_stop_estimate_bytes", "INTEGER"),
    ("tuned_quality", "REAL"),
    ("sample_predicted_bytes", "INTEGER"),
    ("tune_retry_count", "INTEGER NOT NULL DEFAULT 0"),
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
    tuned_quality: float | None = None
    sample_predicted_bytes: int | None = None
    tune_retry_count: int = 0


@dataclass(frozen=True, slots=True)
class PartRecord:
    """기록된 임시 파일 한 개의 항목 상태와 사유, 그리고 소속 배치가 아직 열려 있는지"""

    state: EncodeState
    reason: str
    batch_open: bool


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


def _discard_fact(row: Sequence[object]) -> DiscardFact:
    """폐기 항목 행을 폐기 사실로 바꿈. 인코더는 배치 설정에서, 화질 기준값은 계획 입력에서 읽음"""
    plan: dict[str, object] = json.loads(str(row[3]))
    settings: dict[str, object] = json.loads(str(row[4]))
    return DiscardFact(int(str(row[1])), int(str(row[2])), str(settings["encoder"]), float(str(plan["quality"])))


class EncodeHistory:
    """인코딩 작업 배치와 항목을 SQLite 테이블에 기록하고 읽는 저장소

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
        """아직 닫히지 않은 배치를 번호 순으로 반환(이어서 인코딩 대상)"""

        def _query(conn: sqlite3.Connection) -> list[BatchRow]:
            rows = conn.execute(
                f"SELECT {', '.join(_BATCH_COLUMNS)} FROM {ENCODE_BATCH_TABLE} WHERE state = ? ORDER BY batch_id",
                (BATCH_STATE_OPEN,),
            ).fetchall()
            return [
                BatchRow(int(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4])) for r in rows
            ]

        return self._run(_query)

    def recorded_part_paths(self) -> set[str]:
        """모든 배치 항목이 기록한 임시 파일 경로. 닫힌 배치의 파일도 포함해 기록 밖 목록에서 제외함"""

        def _query(conn: sqlite3.Connection) -> set[str]:
            rows = conn.execute(
                f"SELECT part_path FROM {ENCODE_ITEM_TABLE} WHERE part_path IS NOT NULL",
            ).fetchall()
            return {str(r[0]) for r in rows}

        return self._run(_query)

    def part_records(self) -> dict[str, PartRecord]:
        """기록된 임시 파일 경로별 항목 정보. 같은 경로가 여러 행이면 나중 배치의 행을 씀"""

        def _query(conn: sqlite3.Connection) -> dict[str, PartRecord]:
            rows = conn.execute(
                f"SELECT i.part_path, i.state, i.reason, b.state FROM {ENCODE_ITEM_TABLE} i "
                f"JOIN {ENCODE_BATCH_TABLE} b ON b.batch_id = i.batch_id "
                "WHERE i.part_path IS NOT NULL ORDER BY i.batch_id, i.source_path",
            ).fetchall()
            return {
                str(r[0]): PartRecord(EncodeState(str(r[1])), str(r[2]), str(r[3]) == BATCH_STATE_OPEN)
                for r in rows
            }

        return self._run(_query)

    def discarded_facts(self) -> dict[str, DiscardFact]:
        """결과 폐기로 끝난 원본 경로별 폐기 사실. 같은 경로가 여러 번이면 나중 배치의 값을 씀"""

        def _query(conn: sqlite3.Connection) -> dict[str, DiscardFact]:
            rows = conn.execute(
                "SELECT i.source_path, i.source_size, i.source_mtime_ns, i.plan_json, b.settings_json "
                f"FROM {ENCODE_ITEM_TABLE} AS i JOIN {ENCODE_BATCH_TABLE} AS b ON b.batch_id = i.batch_id "
                "WHERE i.state = ? ORDER BY i.batch_id, i.source_path",
                (EncodeState.DISCARDED.value,),
            ).fetchall()
            return {str(r[0]): _discard_fact(r) for r in rows}

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

    def get_item(self, batch_id: int, source_path: str) -> ItemRow | None:
        """배치 항목 한 건을 원본 경로로 읽음. 없으면 None"""

        def _query(conn: sqlite3.Connection) -> ItemRow | None:
            row = conn.execute(
                f"SELECT {', '.join(_ITEM_COLUMNS)} FROM {ENCODE_ITEM_TABLE} WHERE batch_id = ? AND source_path = ?",
                (batch_id, source_path),
            ).fetchone()
            return None if row is None else _row_to_item(row)

        return self._run(_query)

    def latest_item_for_source(self, source_path: str) -> ItemRow | None:
        """원본 경로의 가장 최근 항목 한 건. 나중 배치를 우선하고, 없으면 None"""

        def _query(conn: sqlite3.Connection) -> ItemRow | None:
            row = conn.execute(
                f"SELECT {', '.join(_ITEM_COLUMNS)} FROM {ENCODE_ITEM_TABLE} "
                "WHERE source_path = ? ORDER BY batch_id DESC LIMIT 1",
                (source_path,),
            ).fetchone()
            return None if row is None else _row_to_item(row)

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
        """진행 중으로 남은 항목을 대기로 되돌리고 되돌린 개수를 반환(이어서 인코딩 시작 시)"""

        def _reset(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(
                f"UPDATE {ENCODE_ITEM_TABLE} SET state = ?, started_at = NULL WHERE batch_id = ? AND state = ?",
                (EncodeState.PENDING.value, batch_id, EncodeState.RUNNING.value),
            )
            return int(cursor.rowcount)

        return self._run(_reset)

    def close_batch(self, batch_id: int) -> None:
        """배치를 닫아 이어서 인코딩 대상에서 빼냄"""
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

    def count_discarded(self, root_dirs: Sequence[str] | None = None) -> int:
        """이전 결과 폐기(discarded) 항목 건수를 반환

        - root_dirs를 주면 그 작업 폴더 범위에 속한 배치의 항목만 셈(대소문자, 구분자 무시)
        """

        def _count(conn: sqlite3.Connection) -> int:
            if root_dirs is None:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {ENCODE_ITEM_TABLE} WHERE state = ?",
                    (EncodeState.DISCARDED.value,),
                ).fetchone()
                return 0 if row is None else int(row[0])
            return len(_discarded_keys(conn, root_dirs))

        return self._run(_count)

    def clear_discarded(self, root_dirs: Sequence[str] | None = None) -> int:
        """결과 폐기(discarded) 항목만 삭제하고 삭제 건수를 반환

        - root_dirs를 주면 그 작업 폴더 범위의 폐기 항목만 지우고 다른 폴더 기록은 그대로 둠
        """

        def _clear(conn: sqlite3.Connection) -> int:
            if root_dirs is None:
                cursor = conn.execute(
                    f"DELETE FROM {ENCODE_ITEM_TABLE} WHERE state = ?",
                    (EncodeState.DISCARDED.value,),
                )
                return int(cursor.rowcount)
            keys = _discarded_keys(conn, root_dirs)
            if keys:
                with _Transaction(conn):
                    conn.executemany(
                        f"DELETE FROM {ENCODE_ITEM_TABLE} WHERE batch_id = ? AND source_path = ?",
                        keys,
                    )
            return len(keys)

        return self._run(_clear)

    def count_completed_history(self, root_dirs: Sequence[str] | None = None) -> int:
        """완료(done) 상태로 끝난 작업 항목 기록 건수를 반환

        - root_dirs를 주면 그 작업 폴더 범위에 속한 배치의 항목만 셈
        """

        def _count(conn: sqlite3.Connection) -> int:
            if root_dirs is None:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {ENCODE_ITEM_TABLE} WHERE state = ?",
                    (EncodeState.DONE.value,),
                ).fetchone()
                return 0 if row is None else int(row[0])
            return len(_completed_keys(conn, root_dirs))

        return self._run(_count)

    def clear_completed_history(self, root_dirs: Sequence[str] | None = None) -> int:
        """완료(done) 상태 항목 기록을 삭제하고 삭제 건수를 반환

        - 이번에 지운 항목이 속한 배치에 항목이 남지 않았을 때만 그 배치를 함께 지움
          (정리 범위 밖의 빈 배치는 이번 정리와 무관하므로 건드리지 않음)
        - 진행 중, 대기, 교체 대기 항목은 이어서 인코딩에 필요하므로 남김
        """

        def _clear(conn: sqlite3.Connection) -> int:
            keys = _completed_keys(conn, root_dirs)
            if not keys:
                return 0
            batch_ids = sorted({batch_id for batch_id, _path in keys})
            with _Transaction(conn):
                conn.executemany(
                    f"DELETE FROM {ENCODE_ITEM_TABLE} WHERE batch_id = ? AND source_path = ?",
                    keys,
                )
                conn.executemany(
                    f"DELETE FROM {ENCODE_BATCH_TABLE} WHERE batch_id = ? AND NOT EXISTS ("
                    f"SELECT 1 FROM {ENCODE_ITEM_TABLE} i WHERE i.batch_id = {ENCODE_BATCH_TABLE}.batch_id)",
                    [(batch_id,) for batch_id in batch_ids],
                )
            return len(keys)

        return self._run(_clear)

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


def _filter_keys(conn: sqlite3.Connection, state: EncodeState, root_dirs: Sequence[str] | None) -> list[tuple[int, str]]:
    """지정 상태 항목의 (배치 번호, 원본 경로) 목록. root_dirs를 주면 그 범위의 항목만 고름

    - 원본 경로가 절대 경로면 그 경로 자체로 범위를 판정해 여러 작업 폴더 배치에서도 항목별로 맞춤
    - 상대 경로만 배치 폴더로 판정(항목 경로에는 작업 폴더 정보가 없음)
    """
    rows = conn.execute(
        f"SELECT i.batch_id, i.source_path, b.root_dir FROM {ENCODE_ITEM_TABLE} i "
        f"JOIN {ENCODE_BATCH_TABLE} b ON b.batch_id = i.batch_id WHERE i.state = ?",
        (state.value,),
    ).fetchall()
    keys: list[tuple[int, str]] = []
    for row in rows:
        source_path = str(row[1])
        if root_dirs is not None:
            inside = is_within_roots(source_path, root_dirs) if os.path.isabs(source_path) else _batch_in_roots(
                str(row[2]), root_dirs
            )
            if not inside:
                continue
        keys.append((int(row[0]), source_path))
    return keys


def _batch_in_roots(batch_root_spec: str, root_dirs: Sequence[str]) -> bool:
    """배치의 작업 폴더가 정리 범위 안에 있는지. 배치 폴더가 여러 개면 모두 안에 있어야 함"""
    batch_roots = parse_root_dirs(batch_root_spec)
    return bool(batch_roots) and all(is_within_roots(root, root_dirs) for root in batch_roots)


def _discarded_keys(conn: sqlite3.Connection, root_dirs: Sequence[str] | None) -> list[tuple[int, str]]:
    """폐기 상태 항목의 (배치 번호, 원본 경로) 목록"""
    return _filter_keys(conn, EncodeState.DISCARDED, root_dirs)


def _completed_keys(conn: sqlite3.Connection, root_dirs: Sequence[str] | None) -> list[tuple[int, str]]:
    """완료 상태 항목의 (배치 번호, 원본 경로) 목록"""
    return _filter_keys(conn, EncodeState.DONE, root_dirs)


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
        tuned_quality=_as_optional_float(values["tuned_quality"]),
        sample_predicted_bytes=(
            None if values["sample_predicted_bytes"] is None else _as_int(values["sample_predicted_bytes"])
        ),
        tune_retry_count=0 if values["tune_retry_count"] is None else _as_int(values["tune_retry_count"]),
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
