"""화질 분석 결과 캐시(SQLite) 저장소 모듈

- 캐시는 분석 캐시와 같은 `video_cache.sqlite3`의 독립 테이블 `frame_analysis_cache`에 두어 분석 캐시 비우기, 스키마 재생성과 무관하게 보존
- 스레드별 연결, 잠금 재시도, `close()` 정리 방식은 `encode_output_history.py`와 같음
- 영상 한 개를 마칠 때마다 `save()`가 불려 중단되어도 완료분이 남고, 다른 테이블을 만들거나 지우지 않음
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import TypeVar

from src.core.analysis.frame_analysis import normalize_manual_active_frame
from src.core.models import ActiveFrame
from src.core.workspace_paths import LogicalOwnerResolver, is_within_roots
from src.service.frame_analysis_record import (
    RECORD_COLUMNS,
    FileKey,
    FrameAnalysisRecord,
    record_to_row,
    row_to_record,
)
from src.service.frame_override_rows import (
    MANUAL_FRAME_MATCH_COLUMNS,
    add_missing_columns,
    file_identity,
    validated_crop_exclusion_rows,
    validated_manual_rows,
    write_row_changes,
)

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

FRAME_ANALYSIS_TABLE: str = "frame_analysis_cache"
FRAME_ANALYSIS_VR_TABLE: str = "frame_analysis_vr_override"
FRAME_ANALYSIS_CROP_TABLE: str = "frame_analysis_crop_exclude"
FRAME_ANALYSIS_MANUAL_FRAME_TABLE: str = "frame_analysis_manual_active_frame"
SQLITE_BUSY_TIMEOUT_SEC: float = 5.0
# - 조회 중 곁들이는 행 정리는 화면 스레드에서 돌므로 잠금을 오래 기다리지 않고 포기함
ROW_CLEANUP_BUSY_TIMEOUT_SEC: float = 0.2
WRITE_RETRY_ATTEMPTS: int = 5
WRITE_RETRY_BASE_SEC: float = 0.05
_LOOKUP_CHUNK_SIZE: int = 500
_LOCKED_MARKERS: tuple[str, ...] = ("locked", "busy")


_SCHEMA_STATEMENTS: tuple[str, ...] = (
    f"CREATE TABLE IF NOT EXISTS {FRAME_ANALYSIS_TABLE} (\n"
    "  filename TEXT NOT NULL,\n"
    "  filesize_bytes INTEGER NOT NULL,\n"
    "  mtime_ns INTEGER NOT NULL,\n"
    "  active_width INTEGER NOT NULL,\n"
    "  active_height INTEGER NOT NULL,\n"
    "  active_x INTEGER NOT NULL,\n"
    "  active_y INTEGER NOT NULL,\n"
    "  has_black_bars INTEGER NOT NULL,\n"
    "  metric_used TEXT NOT NULL,\n"
    "  vmaf_model TEXT NOT NULL,\n"
    "  tier_scores_json TEXT NOT NULL,\n"
    "  candidate_tier TEXT,\n"
    "  valid_segments INTEGER NOT NULL,\n"
    "  total_segments INTEGER NOT NULL,\n"
    "  unmeasurable INTEGER NOT NULL,\n"
    "  is_vr_protected INTEGER NOT NULL,\n"
    "  measured_at TEXT NOT NULL,\n"
    "  analysis_version INTEGER NOT NULL,\n"
    "  margins_vary INTEGER NOT NULL DEFAULT 0,\n"
    "  dark_outliers_ignored INTEGER NOT NULL DEFAULT 0,\n"
    "  crop_scan_failed INTEGER NOT NULL DEFAULT 0,\n"
    "  PRIMARY KEY (filename, filesize_bytes, mtime_ns)\n"
    ") WITHOUT ROWID",
    f"CREATE TABLE IF NOT EXISTS {FRAME_ANALYSIS_VR_TABLE} (\n"
    "  source_path TEXT PRIMARY KEY,\n"
    "  manual_vr INTEGER NOT NULL\n"
    ") WITHOUT ROWID",
    f"CREATE TABLE IF NOT EXISTS {FRAME_ANALYSIS_CROP_TABLE} (\n"
    "  source_path TEXT PRIMARY KEY,\n"
    "  exclude_crop INTEGER NOT NULL,\n"
    "  filesize_bytes INTEGER,\n"
    "  mtime_ns INTEGER\n"
    ") WITHOUT ROWID",
    f"CREATE TABLE IF NOT EXISTS {FRAME_ANALYSIS_MANUAL_FRAME_TABLE} (\n"
    "  source_path TEXT PRIMARY KEY,\n"
    "  manual_x INTEGER NOT NULL,\n"
    "  manual_y INTEGER NOT NULL,\n"
    "  manual_width INTEGER NOT NULL,\n"
    "  manual_height INTEGER NOT NULL,\n"
    "  filesize_bytes INTEGER,\n"
    "  mtime_ns INTEGER\n"
    ") WITHOUT ROWID",
)
_SELECT_COLUMNS: str = ", ".join(RECORD_COLUMNS)
_PLACEHOLDERS: str = ", ".join("?" * len(RECORD_COLUMNS))
# - CREATE TABLE IF NOT EXISTS는 기존 테이블에 열을 더하지 않으므로, 이전 DB에는 이 열을 따로 덧붙임
# - 사용자 지정 활성 화면의 파일 식별 값은 예전 행을 그대로 읽을 수 있도록 NULL을 허용
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    (FRAME_ANALYSIS_TABLE, "margins_vary", "INTEGER NOT NULL DEFAULT 0"),
    (FRAME_ANALYSIS_TABLE, "dark_outliers_ignored", "INTEGER NOT NULL DEFAULT 0"),
    (FRAME_ANALYSIS_TABLE, "crop_scan_failed", "INTEGER NOT NULL DEFAULT 0"),
    (FRAME_ANALYSIS_MANUAL_FRAME_TABLE, "filesize_bytes", "INTEGER"),
    (FRAME_ANALYSIS_MANUAL_FRAME_TABLE, "mtime_ns", "INTEGER"),
    (FRAME_ANALYSIS_CROP_TABLE, "filesize_bytes", "INTEGER"),
    (FRAME_ANALYSIS_CROP_TABLE, "mtime_ns", "INTEGER"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """이전 DB의 지정 테이블에 없는 열을 덧붙임"""
    add_missing_columns(conn, _ADDED_COLUMNS)


def _is_locked_error(error: sqlite3.OperationalError) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _LOCKED_MARKERS)


def _write_row_changes_best_effort(
    db_path: str,
    *,
    table: str,
    stamped: Sequence[tuple[int, int, str]],
    drop_columns: Sequence[str] = (),
    dropped: Sequence[Sequence[object]] = (),
) -> None:
    """조회 중 걸러 낸 행 정리를 짧은 잠금 대기로 시도하되 실패는 경고만 남김

    - 조회는 화면 스레드에서 돌아 분석 워커가 쓰는 동안 잠금을 오래 기다릴 수 없으므로, 정리 전용
      연결을 짧은 대기 시간으로 새로 열고 기다리는 대신 포기함(공유 연결의 대기 시간은 건드리지 않음)
    - 잠금이나 읽기 전용 DB 때문에 정리가 실패해도 조회 결과는 그대로 돌려줌
    - 정리되지 않은 행은 다음 조회에서 다시 걸러 내거나 채움
    """
    if not dropped and not stamped:
        return
    try:
        conn = sqlite3.connect(
            db_path,
            timeout=ROW_CLEANUP_BUSY_TIMEOUT_SEC,
            isolation_level=None,
        )
    except sqlite3.Error as error:
        logger.warning(
            "걸러 낸 지정 행을 정리하지 못했습니다. 다음 조회에서 다시 시도합니다: %s", error
        )
        return
    try:
        write_row_changes(
            conn, table=table, drop_columns=drop_columns, dropped=dropped, stamped=stamped
        )
    except sqlite3.Error as error:
        logger.warning(
            "걸러 낸 지정 행을 정리하지 못했습니다. 다음 조회에서 다시 시도합니다: %s", error
        )
    finally:
        conn.close()


def _frame_cache_keys(conn: sqlite3.Connection, root_dirs: Sequence[str] | None) -> list[FileKey]:
    """정리 범위에 드는 화질 분석 캐시 행의 파일 식별 키 목록

    - 범위 판정기를 한 번만 만들어 행마다 폴더 파싱과 파일 존재 조회를 반복하지 않음
    """
    rows = conn.execute(f"SELECT filename, filesize_bytes, mtime_ns FROM {FRAME_ANALYSIS_TABLE}").fetchall()
    if root_dirs is None:
        return [(str(row[0]), int(row[1]), int(row[2])) for row in rows]
    resolver = LogicalOwnerResolver(root_dirs)
    return [
        (str(row[0]), int(row[1]), int(row[2]))
        for row in rows
        if resolver.owner(str(row[0])) is not None
    ]


def _path_rows(conn: sqlite3.Connection, table: str, root_dirs: Sequence[str] | None) -> list[str]:
    """경로 열 하나만 가진 지정 테이블에서 정리 범위에 드는 경로 목록"""
    rows = conn.execute(f"SELECT source_path FROM {table}").fetchall()
    return [
        str(row[0])
        for row in rows
        if root_dirs is None or is_within_roots(str(row[0]), root_dirs)
    ]


def _delete_path_rows(conn: sqlite3.Connection, table: str, root_dirs: Sequence[str] | None) -> int:
    """경로 열 하나만 가진 지정 테이블의 범위 행을 지우고 지운 건수 반환"""
    paths = _path_rows(conn, table, root_dirs)
    if not paths:
        return 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany(f"DELETE FROM {table} WHERE source_path = ?", [(path,) for path in paths])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return len(paths)


class FrameAnalysisStore:
    """화질 분석 결과 캐시의 스레드 안전 SQLite 저장소

    - 스레드마다 연결을 하나씩 두고 잠금 오류는 지수 간격으로 재시도
    - 다른 테이블을 만들거나 지우지 않아 분석 캐시 비우기, 재생성과 독립적
    """

    def __init__(self, db_path: str, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._db_path: str = db_path
        self._sleep: Callable[[float], None] = sleep
        self._lock: threading.Lock = threading.Lock()
        self._connections: dict[int, sqlite3.Connection] = {}

    @property
    def db_path(self) -> str:
        """저장소 DB 파일 경로 반환"""
        return self._db_path

    def load_many(self, keys: Sequence[FileKey]) -> dict[FileKey, FrameAnalysisRecord]:
        """파일 식별 키 목록에 해당하는 캐시 행을 조회"""
        wanted = set(keys)
        if not wanted:
            return {}
        names = list(dict.fromkeys(name for name, _size, _mtime in keys))

        def _select(conn: sqlite3.Connection) -> dict[FileKey, FrameAnalysisRecord]:
            found: dict[FileKey, FrameAnalysisRecord] = {}
            for start in range(0, len(names), _LOOKUP_CHUNK_SIZE):
                chunk = names[start : start + _LOOKUP_CHUNK_SIZE]
                placeholders = ", ".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM {FRAME_ANALYSIS_TABLE} WHERE filename IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    record = row_to_record(row)
                    if record.key in wanted:
                        found[record.key] = record
            return found

        return self._run(_select)

    def save(self, record: FrameAnalysisRecord) -> None:
        """캐시 행을 저장하며 같은 키가 있으면 덮어씀"""

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO {FRAME_ANALYSIS_TABLE} ({_SELECT_COLUMNS}) VALUES ({_PLACEHOLDERS})",
                    record_to_row(record),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        self._run(_write)

    def count(self) -> int:
        """저장된 행 수 반환"""

        def _count(conn: sqlite3.Connection) -> int:
            return int(conn.execute(f"SELECT COUNT(*) FROM {FRAME_ANALYSIS_TABLE}").fetchone()[0])

        return self._run(_count)

    def count_for_roots(self, root_dirs: Sequence[str] | None = None) -> int:
        """화질 분석 캐시 행 수. root_dirs를 주면 그 작업 폴더 소속 논리 파일명만 셈"""
        return self._run(lambda conn: len(_frame_cache_keys(conn, root_dirs)))

    def clear_for_roots(self, root_dirs: Sequence[str] | None = None) -> int:
        """화질 분석 캐시 행을 지우고 지운 행 수 반환

        - 수동 VR 보호와 잘라내기 제외 지정은 별도 테이블이라 함께 지우지 않음
        """
        keys = self._run(lambda conn: _frame_cache_keys(conn, root_dirs))
        if not keys:
            return 0

        def _delete(conn: sqlite3.Connection) -> int:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.executemany(
                    f"DELETE FROM {FRAME_ANALYSIS_TABLE} "
                    "WHERE filename = ? AND filesize_bytes = ? AND mtime_ns = ?",
                    keys,
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            return len(keys)

        return self._run(_delete)

    def count_crop_exclusions(self, root_dirs: Sequence[str] | None = None) -> int:
        """검은 여백 잘라내기 제외 지정 건수. root_dirs를 주면 그 범위 경로만 셈"""
        return self._run(lambda conn: len(_path_rows(conn, FRAME_ANALYSIS_CROP_TABLE, root_dirs)))

    def clear_crop_exclusions(self, root_dirs: Sequence[str] | None = None) -> int:
        """검은 여백 잘라내기 제외 지정을 지우고 지운 건수 반환"""
        return self._run(lambda conn: _delete_path_rows(conn, FRAME_ANALYSIS_CROP_TABLE, root_dirs))

    def count_manual_active_frames(self, root_dirs: Sequence[str] | None = None) -> int:
        """사용자 지정 활성 화면 건수. root_dirs를 주면 그 범위 경로만 셈"""
        return self._run(
            lambda conn: len(_path_rows(conn, FRAME_ANALYSIS_MANUAL_FRAME_TABLE, root_dirs))
        )

    def clear_manual_active_frames(self, root_dirs: Sequence[str] | None = None) -> int:
        """사용자 지정 활성 화면을 지우고 지운 건수 반환"""
        return self._run(
            lambda conn: _delete_path_rows(conn, FRAME_ANALYSIS_MANUAL_FRAME_TABLE, root_dirs)
        )

    def count_vr_overrides(self, root_dirs: Sequence[str] | None = None) -> int:
        """수동 VR 보호 지정 건수. root_dirs를 주면 그 범위 경로만 셈"""
        return self._run(lambda conn: len(_path_rows(conn, FRAME_ANALYSIS_VR_TABLE, root_dirs)))

    def clear_vr_overrides(self, root_dirs: Sequence[str] | None = None) -> int:
        """수동 VR 보호 지정을 지우고 지운 건수 반환"""
        return self._run(lambda conn: _delete_path_rows(conn, FRAME_ANALYSIS_VR_TABLE, root_dirs))

    def load_vr_overrides(self, paths: Sequence[str]) -> frozenset[str]:
        """수동 VR 보호로 저장된 전체 경로 집합 조회"""

        def _select(conn: sqlite3.Connection) -> frozenset[str]:
            keys = list(dict.fromkeys(path for path in paths if path))
            if not keys:
                return frozenset()
            placeholders = ", ".join("?" * len(keys))
            rows = conn.execute(
                f"SELECT source_path FROM {FRAME_ANALYSIS_VR_TABLE} "
                f"WHERE manual_vr = 1 AND source_path IN ({placeholders})",
                keys,
            ).fetchall()
            return frozenset(str(row[0]) for row in rows)

        return self._run(_select)

    def set_vr_override(self, *, source_path: str, filename: str, manual_vr: bool) -> None:
        """수동 VR 보호를 저장하고 그 영상의 캐시 결과를 지워 다시 분석하게 함

        - 보호 여부에 따라 활성 화면과 축소 후보가 달라지므로 낡은 측정 결과를 남기지 않음
        """

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if manual_vr:
                    conn.execute(
                        f"INSERT OR REPLACE INTO {FRAME_ANALYSIS_VR_TABLE} (source_path, manual_vr) VALUES (?, 1)",
                        (source_path,),
                    )
                else:
                    conn.execute(
                        f"DELETE FROM {FRAME_ANALYSIS_VR_TABLE} WHERE source_path = ?",
                        (source_path,),
                    )
                conn.execute(f"DELETE FROM {FRAME_ANALYSIS_TABLE} WHERE filename = ?", (filename,))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        self._run(_write)

    def load_crop_exclusions(
        self,
        paths: Sequence[str],
        *,
        identity_overrides: Mapping[str, tuple[int, int]] | None = None,
    ) -> frozenset[str]:
        """영상별 검은 여백 잘라내기 제외로 저장된 전체 경로 집합 조회

        - 저장 시점의 파일 크기와 수정 시각이 지금 파일과 다르면 원본이 바뀐 행이라 그 경로를 빼고,
          행은 지우지 않음(원본 교체 인코딩 뒤 보관 원본 비교가 이 행을 씀)
        - `identity_overrides`에 있는 경로는 그 파일 대신 지정한 (크기, 수정 시각)과 맞춰 보고,
          맞지 않는 행은 지우지 않고 이번 조회에서만 건너뜀(결과 파일 경로의 지정일 수 있음)
        - 식별 값이 없던 행은 이 기회에 지금 값으로 채움
        """

        def _select(conn: sqlite3.Connection) -> frozenset[str]:
            keys = list(dict.fromkeys(path for path in paths if path))
            if not keys:
                return frozenset()
            placeholders = ", ".join("?" * len(keys))
            rows = conn.execute(
                f"SELECT source_path, exclude_crop, filesize_bytes, mtime_ns"
                f" FROM {FRAME_ANALYSIS_CROP_TABLE}"
                f" WHERE exclude_crop = 1 AND source_path IN ({placeholders})",
                keys,
            ).fetchall()
            kept, stamped = validated_crop_exclusion_rows(
                rows, identity_overrides=identity_overrides
            )
            _write_row_changes_best_effort(
                self._db_path,
                table=FRAME_ANALYSIS_CROP_TABLE,
                stamped=stamped,
            )
            return frozenset(kept)

        return self._run(_select)

    def set_crop_exclusion(self, source_path: str, excluded: bool) -> None:
        """영상별 검은 여백 잘라내기 제외를 저장. 분석 캐시는 지우지 않음

        - 활성 화면과 축소 후보는 그대로 유효하며 판정 입력에서만 잘라내기를 건너뜀
        - 저장 시점 파일 크기와 수정 시각을 함께 남겨, 같은 경로의 파일이 바뀌면 읽을 때 버림
        """
        if not source_path:
            return
        identity = file_identity(source_path) if excluded else None

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if excluded:
                    conn.execute(
                        f"INSERT OR REPLACE INTO {FRAME_ANALYSIS_CROP_TABLE}"
                        " (source_path, exclude_crop, filesize_bytes, mtime_ns)"
                        " VALUES (?, 1, ?, ?)",
                        (
                            source_path,
                            None if identity is None else identity[0],
                            None if identity is None else identity[1],
                        ),
                    )
                else:
                    conn.execute(
                        f"DELETE FROM {FRAME_ANALYSIS_CROP_TABLE} WHERE source_path = ?",
                        (source_path,),
                    )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        self._run(_write)

    def load_manual_active_frames(
        self,
        frame_sizes: Mapping[str, tuple[int, int]],
        *,
        identity_overrides: Mapping[str, tuple[int, int]] | None = None,
    ) -> dict[str, ActiveFrame]:
        """영상별 사용자 지정 활성 화면을 경로별로 조회

        - `frame_sizes`는 경로별 지금 프레임 (가로, 세로) 크기이며, 저장 값이 그 프레임을 벗어나면 버림
        - 저장 시점의 파일 크기와 수정 시각이 지금 파일과 다르면 원본이 바뀐 행이라 그 경로를 빼고,
          행은 지우지 않음(원본 교체 인코딩 뒤 보관 원본 비교가 이 행을 씀)
        - `identity_overrides`의 처리는 `load_crop_exclusions`와 같고, 지금 프레임을 벗어난 행은 지움
        - 식별 값이 없던 행은 이 기회에 지금 값으로 채움
        """

        def _select(conn: sqlite3.Connection) -> dict[str, ActiveFrame]:
            keys = list(dict.fromkeys(path for path in frame_sizes if path))
            if not keys:
                return {}
            placeholders = ", ".join("?" * len(keys))
            rows = conn.execute(
                f"SELECT source_path, manual_width, manual_height, manual_x, manual_y,"
                f" filesize_bytes, mtime_ns FROM {FRAME_ANALYSIS_MANUAL_FRAME_TABLE}"
                f" WHERE source_path IN ({placeholders})",
                keys,
            ).fetchall()
            frames, dropped, stamped = validated_manual_rows(
                rows, frame_sizes, identity_overrides=identity_overrides
            )
            _write_row_changes_best_effort(
                self._db_path,
                table=FRAME_ANALYSIS_MANUAL_FRAME_TABLE,
                drop_columns=MANUAL_FRAME_MATCH_COLUMNS,
                dropped=dropped,
                stamped=stamped,
            )
            return frames

        return self._run(_select)

    def set_manual_active_frame(
        self,
        source_path: str,
        frame: ActiveFrame | None,
        *,
        frame_width: int,
        frame_height: int,
    ) -> None:
        """영상별 사용자 지정 활성 화면을 저장. None이면 지워 자동 감지 결과로 되돌림

        - 저장 전 `normalize_manual_active_frame`으로 프레임 안의 짝수 크기와 좌표로 맞추고,
          전체 프레임과 같아지면 지움으로 처리
        - 저장 시점 파일 크기와 수정 시각을 함께 남겨, 같은 경로의 파일이 바뀌면 읽을 때 버림
        - 분석 캐시는 지우지 않음. 활성 화면은 판정 입력에서만 바뀌고 측정 결과는 그대로 씀
        """
        if not source_path:
            return
        normalized = (
            None
            if frame is None
            else normalize_manual_active_frame(frame, frame_width, frame_height)
        )
        identity = None if normalized is None else file_identity(source_path)

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    f"DELETE FROM {FRAME_ANALYSIS_MANUAL_FRAME_TABLE} WHERE source_path = ?",
                    (source_path,),
                )
                if normalized is not None:
                    conn.execute(
                        f"INSERT OR REPLACE INTO {FRAME_ANALYSIS_MANUAL_FRAME_TABLE}"
                        " (source_path, manual_x, manual_y, manual_width, manual_height,"
                        " filesize_bytes, mtime_ns) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            source_path,
                            normalized.x,
                            normalized.y,
                            normalized.width,
                            normalized.height,
                            None if identity is None else identity[0],
                            None if identity is None else identity[1],
                        ),
                    )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        self._run(_write)

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
                self._sleep(WRITE_RETRY_BASE_SEC * (2**attempt))
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
            _add_missing_columns(conn)
        except BaseException:
            conn.close()
            raise
        with self._lock:
            self._connections[ident] = conn
        return conn
