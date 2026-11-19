"""재인코딩 완료 결과 지문과 사용한 태그 이름 이력을 저장, 조회하는 SQLite 저장소 모듈

- 분석 캐시와 같은 SQLite 파일의 별도 테이블을 쓰며, 분석 캐시 비우기와 재생성, 배치 삭제에 영향받지 않음
- 결과 배치가 끝나 done 상태가 될 때만 기록하고, 조회 시 크기가 일치하는 파일만 해시를 계산
"""

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypeVar

from src.core.app_version import APP_VERSION
from src.core.encode_fingerprint import EncodeFingerprint, compute_encode_fingerprint
from src.core.encode_tag import ENCODE_TAG_MODE_VISIBLE, normalize_tag_name

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

ENCODE_OUTPUT_TABLE: str = "encode_output"
ENCODE_OUTPUT_SIZE_INDEX: str = "idx_encode_output_size"
ENCODE_TAG_NAME_TABLE: str = "encode_tag_name_history"
SQLITE_BUSY_TIMEOUT_SEC: float = 5.0
WRITE_RETRY_ATTEMPTS: int = 5
WRITE_RETRY_BASE_SEC: float = 0.05
_LOCKED_MARKERS: tuple[str, ...] = ("locked", "busy")

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    f"CREATE TABLE IF NOT EXISTS {ENCODE_OUTPUT_TABLE} (\n"
    "  output_id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  fingerprint_size INTEGER NOT NULL,\n"
    "  fingerprint_digest TEXT NOT NULL,\n"
    "  output_path TEXT NOT NULL,\n"
    "  encoder TEXT NOT NULL,\n"
    "  quality REAL NOT NULL,\n"
    "  app_version TEXT NOT NULL,\n"
    "  created_at TEXT NOT NULL\n"
    ")",
    f"CREATE INDEX IF NOT EXISTS {ENCODE_OUTPUT_SIZE_INDEX} ON {ENCODE_OUTPUT_TABLE} (fingerprint_size)",
    f"CREATE TABLE IF NOT EXISTS {ENCODE_TAG_NAME_TABLE} (\n"
    "  tag_name TEXT PRIMARY KEY,\n"
    "  first_used_at TEXT NOT NULL\n"
    ")",
)


@dataclass(frozen=True, slots=True)
class EncodeOutputRecord:
    """재인코딩 완료 이력 한 건"""

    output_id: int
    fingerprint_size: int
    fingerprint_digest: str
    output_path: str
    encoder: str
    quality: float
    app_version: str
    created_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_locked_error(error: sqlite3.OperationalError) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _LOCKED_MARKERS)


class EncodeOutputHistory:
    """재인코딩 결과 지문과 태그 이름 이력의 스레드 안전 SQLite 저장소"""

    def __init__(self, db_path: str, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._db_path: str = db_path
        self._sleep: Callable[[float], None] = sleep
        self._lock: threading.Lock = threading.Lock()
        self._connections: dict[int, sqlite3.Connection] = {}

    def record_output(
        self,
        *,
        fingerprint: EncodeFingerprint,
        output_path: str,
        encoder: str,
        quality: float,
        app_version: str = APP_VERSION,
        created_at: str | None = None,
    ) -> None:
        """완료된 재인코딩 결과의 지문과 메타정보를 기록"""
        stamp = created_at if created_at is not None else _utc_now_iso()

        def _insert(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    f"INSERT INTO {ENCODE_OUTPUT_TABLE} "
                    "(fingerprint_size, fingerprint_digest, output_path, encoder, quality, app_version, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (fingerprint.size, fingerprint.digest, output_path, encoder, quality, app_version, stamp),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        self._run(_insert)

    def record_tag_name(self, tag_name: str, *, used_at: str | None = None) -> None:
        """visible 방식으로 기록한 태그 이름을 이력에 남김(중복 무시)"""
        clean = normalize_tag_name(tag_name)
        stamp = used_at if used_at is not None else _utc_now_iso()

        def _insert(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    f"INSERT OR IGNORE INTO {ENCODE_TAG_NAME_TABLE} (tag_name, first_used_at) VALUES (?, ?)",
                    (clean, stamp),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        self._run(_insert)

    def list_tag_names(self) -> tuple[str, ...]:
        """지금까지 사용한 모든 재인코딩 태그 이름 목록을 반환"""

        def _select(conn: sqlite3.Connection) -> tuple[str, ...]:
            rows = conn.execute(
                f"SELECT tag_name FROM {ENCODE_TAG_NAME_TABLE} ORDER BY first_used_at, tag_name"
            ).fetchall()
            return tuple(str(row[0]) for row in rows)

        return self._run(_select)

    def find_by_size(self, size: int) -> list[EncodeOutputRecord]:
        """조회 키 크기와 일치하는 결과 이력 목록을 반환"""

        def _select(conn: sqlite3.Connection) -> list[EncodeOutputRecord]:
            rows = conn.execute(
                f"SELECT output_id, fingerprint_size, fingerprint_digest, output_path, encoder, "
                f"quality, app_version, created_at FROM {ENCODE_OUTPUT_TABLE} WHERE fingerprint_size = ?",
                (size,),
            ).fetchall()
            return [
                EncodeOutputRecord(
                    output_id=int(row[0]),
                    fingerprint_size=int(row[1]),
                    fingerprint_digest=str(row[2]),
                    output_path=str(row[3]),
                    encoder=str(row[4]),
                    quality=float(row[5]),
                    app_version=str(row[6]),
                    created_at=str(row[7]),
                )
                for row in rows
            ]

        return self._run(_select)

    def match_fingerprints(self, candidates: Mapping[str, int]) -> set[str]:
        """크기 후보와 일치하는 파일만 해시를 계산해 이력 일치 파일 경로 집합을 반환"""
        if not candidates:
            return set()
        unique_sizes = sorted(set(candidates.values()))
        placeholders = ",".join("?" for _ in unique_sizes)

        def _select_pairs(conn: sqlite3.Connection) -> set[tuple[int, str]]:
            rows = conn.execute(
                f"SELECT fingerprint_size, fingerprint_digest FROM {ENCODE_OUTPUT_TABLE} "
                f"WHERE fingerprint_size IN ({placeholders})",
                tuple(unique_sizes),
            ).fetchall()
            return {(int(row[0]), str(row[1])) for row in rows}

        known_pairs = self._run(_select_pairs)
        if not known_pairs:
            return set()
        known_sizes = {size for size, _digest in known_pairs}
        matched: set[str] = set()
        for filepath, size in candidates.items():
            if size not in known_sizes:
                continue
            try:
                fp = compute_encode_fingerprint(filepath)
            except (OSError, ValueError) as error:
                logger.warning("재인코딩 이력 대조용 지문 계산 실패: %s (%s)", filepath, error)
                continue
            if (fp.size, fp.digest) in known_pairs:
                matched.add(filepath)
        return matched

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
        except BaseException:
            conn.close()
            raise
        with self._lock:
            self._connections[ident] = conn
        return conn


def record_done_output(
    output_history: EncodeOutputHistory,
    *,
    output_path: str,
    encoder: str,
    quality: float,
    tag_mode: str,
    tag_name: str,
) -> None:
    """done 상태가 된 결과의 지문과 visible 태그 이름을 기록(지문 실패는 경고 후 성공 유지)"""
    try:
        fp = compute_encode_fingerprint(output_path)
    except (OSError, ValueError) as error:
        logger.warning("재인코딩 이력 지문 계산 실패(배치는 성공 유지): %s (%s)", output_path, error)
    else:
        output_history.record_output(
            fingerprint=fp,
            output_path=output_path,
            encoder=encoder,
            quality=quality,
        )
    if tag_mode == ENCODE_TAG_MODE_VISIBLE:
        output_history.record_tag_name(tag_name)


def query_known_tag_names(output_history: EncodeOutputHistory) -> Sequence[str]:
    """태그 계획 계산에 넘길 이전 태그 이름 목록을 조회"""
    return output_history.list_tag_names()
