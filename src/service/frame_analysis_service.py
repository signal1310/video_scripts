"""화질 분석 배치 실행과 결과 캐시(SQLite) 저장소, 서비스 모듈

- 캐시는 분석 캐시와 같은 `video_cache.sqlite3`의 독립 테이블 `frame_analysis_cache`에 두어 분석 캐시 비우기, 스키마 재생성과 무관하게 보존
- 스레드별 연결, 잠금 재시도, `close()` 정리 방식은 `encode_output_history.py`와 같음
- 영상 1개를 마칠 때마다 즉시 저장해 중단되어도 완료분을 잃지 않음
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import TypeVar

from src.core.analysis.frame_analysis import (
    DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    FrameAnalysisThresholds,
    analysis_version_for_mode,
    apply_no_bars_ratio,
    can_reuse_tier_scores,
    candidate_tiers_for,
    is_usable_analysis_version,
    is_vr_protected,
    normalize_manual_active_frame,
    rejudge_candidate_tier,
    resolve_crop_frame,
)
from src.core.analysis.frame_analysis_runner import (
    FrameAnalysisResult,
    ProcessStderrRunner,
    StderrCommandRunner,
    analyze_frames,
)
from src.core.config import DEFAULT_FRAME_ANALYSIS_MODE
from src.core.encode.ffmpeg_command import FFMPEG_EXECUTABLE_DEFAULT
from src.core.models import ActiveFrame
from src.core.quality_probe import METRIC_VMAF, resolve_metric_name
from src.core.resolution_tiers import DEFAULT_SCALE_ALGORITHM, normalize_scale_algorithm
from src.core.workspace_paths import LogicalOwnerResolver, is_within_roots
from src.service.frame_analysis_record import (
    RECORD_COLUMNS,
    FileKey,
    FrameAnalysisRecord,
    FrameAnalysisRequest,
    record_from_result,
    record_to_row,
    result_from_record,
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
ANALYSIS_TIMEOUT_SEC: float = 1800.0
SQLITE_BUSY_TIMEOUT_SEC: float = 5.0
# - 조회 중 곁들이는 행 정리는 화면 스레드에서 돌므로 잠금을 오래 기다리지 않고 포기함
ROW_CLEANUP_BUSY_TIMEOUT_SEC: float = 0.2
WRITE_RETRY_ATTEMPTS: int = 5
WRITE_RETRY_BASE_SEC: float = 0.05
# - 영상 단위와 영상 내부 라운드 병렬 워커 수(이름 있는 상수를 위치 인자로 넘김)
VIDEO_ANALYSIS_WORKERS: int = 4
INNER_ROUND_WORKERS_SINGLE: int = 4
INNER_ROUND_WORKERS_FEW: int = 2
FEW_VIDEO_LIMIT: int = 3
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
    (FRAME_ANALYSIS_MANUAL_FRAME_TABLE, "filesize_bytes", "INTEGER"),
    (FRAME_ANALYSIS_MANUAL_FRAME_TABLE, "mtime_ns", "INTEGER"),
    (FRAME_ANALYSIS_CROP_TABLE, "filesize_bytes", "INTEGER"),
    (FRAME_ANALYSIS_CROP_TABLE, "mtime_ns", "INTEGER"),
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


ProgressCallback = Callable[[int, int, str, FrameAnalysisResult], None]
StageProgressCallback = Callable[[str, str, int, int], None]


def _unique_requests(requests: Sequence[FrameAnalysisRequest]) -> list[FrameAnalysisRequest]:
    """같은 파일 식별 키의 중복 요청을 하나로 줄임"""
    return list({request.key: request for request in requests}.values())


def _expected_vr(request: FrameAnalysisRequest) -> bool:
    """이 요청 영상이 지금 규칙에서 VR 보호 대상인지"""
    return is_vr_protected(
        request.width,
        request.height,
        has_vr_metadata=request.has_vr_metadata,
        manual_vr=request.manual_vr,
    )


def _is_reusable(
    record: FrameAnalysisRecord | None,
    request: FrameAnalysisRequest,
    metric_name: str,
    analysis_version: int,
    *,
    crop_only: bool = False,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
) -> bool:
    """캐시 행이 지금 측정 규칙과 같아 다시 재지 않고 쓸 수 있는지

    - 검은 여백 감지는 전체 화질 분석 행이나 감지 전용 행을 모두 재사용해 기존 점수를 보존
    - 전체 화질 분석은 같은 방식 버전과 같은 지표 행만 재사용하며 감지 전용 행은 다시 잼
    - 기준이 바뀌어도 저장된 점수로 최종 후보가 확정되면 다시 재지 않고 재판정 결과를 씀
    """
    if record is None or record.is_vr_protected != _expected_vr(request):
        return False
    if crop_only:
        return is_usable_analysis_version(record.analysis_version, analysis_version)
    if record.analysis_version != analysis_version or record.metric_used != metric_name:
        return False
    if not record.tier_scores:
        return True
    region = apply_no_bars_ratio(
        record.active_region, request.width, request.height, thresholds=thresholds
    )
    return can_reuse_tier_scores(
        record.tier_scores,
        use_vmaf=metric_name == METRIC_VMAF,
        thresholds=thresholds,
        all_tier_keys=tuple(tier.key for tier in candidate_tiers_for(region.width, region.height)),
    )


def _rejudged_result(
    record: FrameAnalysisRecord,
    request: FrameAnalysisRequest,
    thresholds: FrameAnalysisThresholds,
    *,
    keep_candidate: bool = False,
) -> FrameAnalysisResult:
    """캐시 결과를 현재 축소 후보 기준과 검은 여백 면적 상한으로 다시 판정

    - 저장된 규격 점수는 그대로 두고 후보만 다시 고름
    - 활성 화면 면적이 현재 상한을 넘으면 전체 프레임으로 되돌림
    - keep_candidate면 감지 전용 실행처럼 후보를 그대로 보존
    """
    base = result_from_record(record)
    region = apply_no_bars_ratio(
        base.active_region, request.width, request.height, thresholds=thresholds
    )
    return FrameAnalysisResult(
        active_region=region,
        metric_used=base.metric_used,
        vmaf_model=base.vmaf_model,
        tier_scores=base.tier_scores,
        candidate_tier=(
            base.candidate_tier
            if keep_candidate
            else rejudge_candidate_tier(
                base.tier_scores,
                base.candidate_tier,
                use_vmaf=base.metric_used == METRIC_VMAF,
                thresholds=thresholds,
            )
        ),
        valid_segment_count=base.valid_segment_count,
        total_segment_count=base.total_segment_count,
        unmeasurable=base.unmeasurable,
        is_vr_protected=base.is_vr_protected,
        analysis_version=base.analysis_version,
    )


def _inner_round_workers(video_count: int, *, runner_injected: bool) -> int:
    """영상 수와 실행기 주입 여부로 영상 내부 라운드 병렬 워커 수 결정

    - 주입 실행기는 테스트나 다른 실행 정책을 따르므로 직렬로 둠
    - 영상이 1개면 유휴 워커를 라운드에 쓰고, 소수면 절반만 씀
    """
    if runner_injected:
        return 1
    if video_count <= 1:
        return INNER_ROUND_WORKERS_SINGLE
    if video_count <= FEW_VIDEO_LIMIT:
        return INNER_ROUND_WORKERS_FEW
    return 1


class FrameAnalysisService:
    """화질 분석 배치 실행과 캐시 재사용을 조율하는 서비스

    - 캐시가 현재 측정 규칙 버전과 같으면 다시 재지 않고 재사용
    - 영상 1개를 마칠 때마다 캐시에 저장해 취소되어도 완료분을 보존
    - 실행기 주입으로 FFmpeg 없이 단위 테스트할 수 있음
    """

    def __init__(
        self,
        store: FrameAnalysisStore,
        *,
        work_dir: str,
        ffmpeg_executable: str = FFMPEG_EXECUTABLE_DEFAULT,
        has_vmaf: bool = False,
        runner: StderrCommandRunner | None = None,
        timeout_sec: float = ANALYSIS_TIMEOUT_SEC,
        now: Callable[[], str] = _utc_now_iso,
        mode: str = DEFAULT_FRAME_ANALYSIS_MODE,
        scale_algorithm: str = DEFAULT_SCALE_ALGORITHM,
        video_workers: int = VIDEO_ANALYSIS_WORKERS,
        thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    ) -> None:
        self._store: FrameAnalysisStore = store
        self._work_dir: str = work_dir
        self._ffmpeg_executable: str = ffmpeg_executable
        self._has_vmaf: bool = has_vmaf
        self._runner: StderrCommandRunner | None = runner
        self._timeout_sec: float = timeout_sec
        self._now: Callable[[], str] = now
        self._mode: str = mode
        self._scale_algorithm: str = normalize_scale_algorithm(scale_algorithm)
        self._analysis_version: int = analysis_version_for_mode(mode, self._scale_algorithm)
        self._video_workers: int = max(1, video_workers)
        self._thresholds: FrameAnalysisThresholds = thresholds

    def analyze_batch(
        self,
        requests: Sequence[FrameAnalysisRequest],
        *,
        force: bool = False,
        crop_only: bool = False,
        crop_only_names: frozenset[str] = frozenset(),
        cancel_event: threading.Event | None = None,
        on_progress: ProgressCallback | None = None,
        on_stage: StageProgressCallback | None = None,
    ) -> dict[str, FrameAnalysisResult]:
        """요청 목록을 영상 단위 워커 풀로 분석하고 파일명별 결과를 반환. 취소되면 `InterruptedError`

        - crop_only면 1단계 검은 여백 스캔만 실행하고, 이미 있는 전체 화질 분석 행은 그대로 재사용
        - crop_only_names에 든 영상은 전체 실행에서도 검은 여백만 감지
        """
        unique = _unique_requests(requests)
        total = len(unique)
        cached = {} if force else self._store.load_many([request.key for request in unique])
        results: dict[str, FrameAnalysisResult] = {}
        metric_name = resolve_metric_name(self._has_vmaf)
        os.makedirs(self._work_dir, exist_ok=True)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("화질 분석이 취소되었습니다")
        runner_injected = self._runner is not None
        inner_workers = _inner_round_workers(total, runner_injected=runner_injected)
        exec_runner = self._runner_for(cancel_event)

        def _stage_progress(filename: str, stage: str, done: int, stage_total: int) -> None:
            if on_stage is not None:
                on_stage(filename, stage, done, stage_total)

        def _analyze_one(
            item: tuple[int, FrameAnalysisRequest],
        ) -> tuple[FrameAnalysisRequest, FrameAnalysisResult]:
            index, request = item
            request_crop_only = crop_only or request.filename in crop_only_names
            record = cached.get(request.key)
            if record is not None and _is_reusable(
                record,
                request,
                metric_name,
                self._analysis_version,
                crop_only=request_crop_only,
                thresholds=self._thresholds,
            ):
                return request, _rejudged_result(
                    record, request, self._thresholds, keep_candidate=request_crop_only
                )

            def _stage_callback(stage: str, done: int, stage_total: int) -> None:
                _stage_progress(request.filename, stage, done, stage_total)

            result = analyze_frames(
                source_path=request.path,
                width=request.width,
                height=request.height,
                duration_sec=request.duration_sec,
                work_dir=self._work_dir,
                runner=exec_runner,
                ffmpeg_executable=self._ffmpeg_executable,
                has_vmaf=self._has_vmaf,
                has_vr_metadata=request.has_vr_metadata,
                manual_vr=request.manual_vr,
                mode=self._mode,
                cancel_event=cancel_event,
                inner_workers=inner_workers,
                on_stage=_stage_callback,
                log_prefix=f"{index}_",
                prefer_pyav=not runner_injected,
                crop_only=request_crop_only,
                thresholds=self._thresholds,
                scale_algorithm=self._scale_algorithm,
            )
            self._store.save(record_from_result(request, result, measured_at=self._now()))
            return request, result

        items = list(enumerate(unique, start=1))
        if self._video_workers <= 1 or total <= 1:
            for completed, item in enumerate(items, start=1):
                request, result = _analyze_one(item)
                results[request.filename] = result
                if on_progress is not None:
                    on_progress(completed, total, request.filename, result)
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("화질 분석이 취소되었습니다")
            return results
        with ThreadPoolExecutor(self._video_workers) as pool:
            futures = [pool.submit(_analyze_one, item) for item in items]
            completed = 0
            try:
                for future in as_completed(futures):
                    request, result = future.result()
                    completed += 1
                    results[request.filename] = result
                    if on_progress is not None:
                        on_progress(completed, total, request.filename, result)
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("화질 분석이 취소되었습니다")
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        return results

    def count_cached(
        self,
        requests: Sequence[FrameAnalysisRequest],
        *,
        force: bool = False,
        crop_only: bool = False,
        crop_only_names: frozenset[str] = frozenset(),
    ) -> int:
        """이번 요청 중 지금 캐시로 재사용할 수 있는 건수"""
        unique = _unique_requests(requests)
        if force or not unique:
            return 0
        cached = self._store.load_many([request.key for request in unique])
        metric_name = resolve_metric_name(self._has_vmaf)
        return sum(
            1
            for request in unique
            if _is_reusable(
                cached.get(request.key),
                request,
                metric_name,
                self._analysis_version,
                crop_only=crop_only or request.filename in crop_only_names,
                thresholds=self._thresholds,
            )
        )

    def load_active_frames(
        self, requests: Sequence[FrameAnalysisRequest], *, crop_enabled: bool = True
    ) -> dict[str, ActiveFrame]:
        """잘라내기 확정 영상만 파일명별 활성 화면으로 반환

        - 설정 끔, VR 보호, 검은 여백 없음이면 전체 프레임 유지로 보고 넣지 않음
        - 사용자 지정 활성 화면은 자동 감지와 영상별 제외를 덮어씀
        """
        records = self._store.load_many([request.key for request in requests])
        excluded = self._store.load_crop_exclusions([request.path for request in requests])
        manual = self._store.load_manual_active_frames(
            {request.path: (request.width, request.height) for request in requests}
        )
        frames: dict[str, ActiveFrame] = {}
        for request in requests:
            record = records.get(request.key)
            if record is None or not is_usable_analysis_version(
                record.analysis_version, self._analysis_version
            ):
                continue
            frame = resolve_crop_frame(
                apply_no_bars_ratio(
                    record.active_region,
                    request.width,
                    request.height,
                    thresholds=self._thresholds,
                ),
                crop_enabled=crop_enabled,
                excluded=request.path in excluded,
                is_vr_protected=record.is_vr_protected,
                manual_frame=manual.get(request.path),
            )
            if frame is not None:
                frames[request.filename] = frame
        return frames

    def load_results(self, requests: Sequence[FrameAnalysisRequest]) -> dict[str, FrameAnalysisResult]:
        """현재 작업 폴더의 캐시된 화질 분석 결과를 표시용으로 한 번에 읽음"""
        records = self._store.load_many([request.key for request in requests])
        results: dict[str, FrameAnalysisResult] = {}
        for request in requests:
            record = records.get(request.key)
            if record is None or not is_usable_analysis_version(
                record.analysis_version, self._analysis_version
            ):
                continue
            results[request.filename] = _rejudged_result(record, request, self._thresholds)
        return results

    def close(self) -> None:
        """캐시 저장소 연결을 닫음"""
        self._store.close()

    def _runner_for(self, cancel_event: threading.Event | None) -> StderrCommandRunner:
        if self._runner is not None:
            return self._runner
        return ProcessStderrRunner(cancel_event=cancel_event, timeout_sec=self._timeout_sec)
