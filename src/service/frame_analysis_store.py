"""화질 분석 결과 캐시(SQLite) 저장소 모듈

- 캐시는 분석 캐시와 같은 `video_cache.sqlite3`의 독립 테이블 `frame_analysis_cache`에 두어 분석 캐시 비우기, 스키마 재생성과 무관하게 보존
- 캐시 행은 내용 키(파일 크기, 내용 지문)로 식별해 폴더를 옮기거나 이름을 바꿔도 같은 측정을 재사용
- 옛 파일명 키 행은 `frame_analysis_cache_legacy`에 남겨 두고, 지문을 알게 되는 조회에서 내용 행으로 옮김(표 구성과 승격은 `frame_analysis_schema.py`)
- 스레드별 연결, 잠금 재시도, `close()` 정리 방식은 `encode_output_history.py`와 같음
- 영상 한 개를 마칠 때마다 `save()`가 불려 중단되어도 완료분이 남고, 다른 테이블을 만들거나 지우지 않음
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import TypeVar

from src.core.analysis.frame_analysis import ActiveRegion, normalize_manual_active_frame
from src.core.models import ActiveFrame
from src.core.workspace_paths import LogicalOwnerResolver, is_within_roots
from src.service.analysis_store import ContentKey, FileKey
from src.service.designation_content import (
    ContentIdentity,
    ContentMove,
    fill_missing_fingerprints,
    mismatched_paths,
    optional_text,
    sync_path_designations,
)
from src.service.frame_analysis_record import FrameAnalysisRecord, record_to_row, row_to_record
from src.service.frame_analysis_schema import (
    CONTENT_KEY_COLUMN_NAMES,
    FRAME_ANALYSIS_CROP_TABLE,
    FRAME_ANALYSIS_LEGACY_TABLE,
    FRAME_ANALYSIS_MANUAL_FRAME_TABLE,
    FRAME_ANALYSIS_REVIEW_ACK_TABLE,
    FRAME_ANALYSIS_TABLE,
    FRAME_ANALYSIS_VR_TABLE,
    PLACEHOLDERS,
    SELECT_COLUMNS,
    computed_fingerprint,
    legacy_fingerprint_targets,
    legacy_rows_promotable,
    prepare_cache_tables,
    promote_legacy_rows,
    record_legacy_fingerprints,
    stored_fingerprint,
)
from src.service.frame_override_rows import (
    MANUAL_FRAME_MATCH_COLUMNS,
    REVIEW_ACK_MATCH_COLUMNS,
    file_identity,
    review_region_box,
    validated_crop_exclusion_rows,
    validated_manual_rows,
    validated_review_ack_rows,
    write_row_changes,
)

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

SQLITE_BUSY_TIMEOUT_SEC: float = 5.0
# - 조회 중 곁들이는 행 정리는 화면 스레드에서 돌므로 잠금을 오래 기다리지 않고 포기함
ROW_CLEANUP_BUSY_TIMEOUT_SEC: float = 0.2
WRITE_RETRY_ATTEMPTS: int = 5
WRITE_RETRY_BASE_SEC: float = 0.05
_LOOKUP_CHUNK_SIZE: int = 500
_LOCKED_MARKERS: tuple[str, ...] = ("locked", "busy")


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
        logger.warning("걸러 낸 지정 행을 정리하지 못했습니다. 다음 조회에서 다시 시도합니다: %s", error)
        return
    try:
        write_row_changes(conn, table=table, drop_columns=drop_columns, dropped=dropped, stamped=stamped)
    except sqlite3.Error as error:
        logger.warning("걸러 낸 지정 행을 정리하지 못했습니다. 다음 조회에서 다시 시도합니다: %s", error)
    finally:
        conn.close()


def _record_and_promote_best_effort(conn: sqlite3.Connection, found: Mapping[FileKey, str]) -> None:
    """조회 중 알게 된 지문을 옛 행에 채우고 내용 행으로 옮기는 쓰기를 시도하되 실패는 경고만 남김

    - 이 승격은 조회 결과를 좇아 하는 부수 작업이라, 읽기 전용 DB나 잠금 때문에 실패해도 지문은 그대로 돌려줌
    - 채울 행이나 옮길 행이 없으면 아무 쓰기도 하지 않아 평소 새로고침은 순수 SELECT로 끝남
    """
    targets = legacy_fingerprint_targets(conn, found)
    if not targets and not legacy_rows_promotable(conn):
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as error:
        logger.warning("화질 분석 옛 행 승격을 시작하지 못했습니다. 다음 조회에서 다시 시도합니다: %s", error)
        return
    try:
        if targets:
            record_legacy_fingerprints(conn, targets)
        if legacy_rows_promotable(conn):
            promote_legacy_rows(conn)
        conn.execute("COMMIT")
    except sqlite3.Error as error:
        logger.warning("화질 분석 옛 행 승격을 마치지 못했습니다. 다음 조회에서 다시 시도합니다: %s", error)
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")


def _frame_cache_keys(
    conn: sqlite3.Connection, root_dirs: Sequence[str] | None
) -> tuple[list[ContentKey], list[FileKey]]:
    """정리 범위에 드는 내용 행 키와 아직 옮기지 않은 옛 행 키 목록

    - 범위 판정기는 한 번만 만들어 행마다 폴더 파싱과 파일 존재 조회를 반복하지 않음
    - 내용 행의 파일명은 대표 값이므로 그 값으로 소속을 판정(옮기기 전과 같은 기준)
    """
    content_rows = conn.execute(
        f"SELECT {', '.join(CONTENT_KEY_COLUMN_NAMES)}, filename FROM {FRAME_ANALYSIS_TABLE}"
    ).fetchall()
    legacy_rows = conn.execute(
        f"SELECT filename, filesize_bytes, mtime_ns FROM {FRAME_ANALYSIS_LEGACY_TABLE}"
    ).fetchall()
    if root_dirs is None:
        return (
            [(int(row[0]), str(row[1])) for row in content_rows],
            [(str(row[0]), int(row[1]), int(row[2])) for row in legacy_rows],
        )
    resolver = LogicalOwnerResolver(root_dirs)
    return (
        [(int(row[0]), str(row[1])) for row in content_rows if resolver.owner(str(row[2])) is not None],
        [(str(row[0]), int(row[1]), int(row[2])) for row in legacy_rows if resolver.owner(str(row[0])) is not None],
    )


def _path_rows(conn: sqlite3.Connection, table: str, root_dirs: Sequence[str] | None) -> list[str]:
    """경로 열 하나만 가진 지정 테이블에서 정리 범위에 드는 경로 목록"""
    rows = conn.execute(f"SELECT source_path FROM {table}").fetchall()
    return [str(row[0]) for row in rows if root_dirs is None or is_within_roots(str(row[0]), root_dirs)]


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


def _kept_with_matching_content(
    rows: Sequence[Sequence[object]],
    kept: Sequence[str],
    *,
    fingerprint_index: int,
    identity_overrides: Mapping[str, tuple[int, int]] | None,
) -> list[str]:
    """유지 목록에서 지금 파일 내용이 저장 지문과 다른 경로를 뺀 목록

    - 같은 이름, 크기, 수정 시각이어도 내용이 다르면 지정을 붙이지 않음
    - 다른 파일을 띄우는 조회(`identity_overrides`)의 경로는 그 파일의 지정일 수 있어 그대로 둠
    """
    overridden = set() if identity_overrides is None else set(identity_overrides)
    stored = {str(row[0]): optional_text(row[fingerprint_index]) for row in rows}
    bad = mismatched_paths([(path, stored.get(path)) for path in kept if path not in overridden])
    return [path for path in kept if path not in bad]


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

    def load_many(self, keys: Sequence[ContentKey]) -> dict[ContentKey, FrameAnalysisRecord]:
        """내용 키(크기, 내용 지문) 목록에 해당하는 캐시 행을 조회"""
        wanted = set(keys)
        if not wanted:
            return {}
        unique = list(dict.fromkeys(keys))

        def _select(conn: sqlite3.Connection) -> dict[ContentKey, FrameAnalysisRecord]:
            found: dict[ContentKey, FrameAnalysisRecord] = {}
            for start in range(0, len(unique), _LOOKUP_CHUNK_SIZE):
                chunk = unique[start : start + _LOOKUP_CHUNK_SIZE]
                pairs = ", ".join("(?, ?)" for _ in chunk)
                params = [value for key in chunk for value in key]
                rows = conn.execute(
                    f"SELECT {SELECT_COLUMNS} FROM {FRAME_ANALYSIS_TABLE} "
                    f"WHERE ({', '.join(CONTENT_KEY_COLUMN_NAMES)}) IN ({pairs})",
                    params,
                ).fetchall()
                for row in rows:
                    record = row_to_record(row)
                    if record.content_key in wanted:
                        found[record.content_key] = record
            return found

        return self._run(_select)

    def resolve_content_keys(self, entries: Sequence[tuple[FileKey, str]]) -> dict[FileKey, str]:
        """파일별 내용 지문을 파일을 읽지 않고 확인할 수 있는 데까지 찾음

        - 조회 순서는 1단계 분석 캐시와 같음: 볼륨 위치 표 -> 파일명 키 표 -> 화질 분석 옛 행 -> 직접 계산
        - entries는 (파일명, 크기, 수정 시각)과 그 파일의 절대 경로. 경로가 없으면(NULL) 직접 계산을 건너뜀
        - 찾은 지문으로 옛 파일명 키 화질 분석 행을 내용 행으로 옮겨 다음 조회부터 이름과 무관하게 찾게 함
        - 승격 쓰기는 조회를 좇는 부수 작업이라 실패해도 지문은 그대로 돌려줌(읽기 전용 DB, 잠금)
        """
        wanted = dict(entries)
        if not wanted:
            return {}

        def _select(conn: sqlite3.Connection) -> dict[FileKey, str]:
            found: dict[FileKey, str] = {}
            for key, path in wanted.items():
                fingerprint = stored_fingerprint(conn, path, key)
                if fingerprint is not None:
                    found[key] = fingerprint
            for key, path in wanted.items():
                if key in found:
                    continue
                fingerprint = computed_fingerprint(path, key[1])
                if fingerprint is not None:
                    found[key] = fingerprint
            if found:
                _record_and_promote_best_effort(conn, found)
            return found

        return self._run(_select)

    def save(self, record: FrameAnalysisRecord) -> None:
        """캐시 행을 저장하며 같은 내용 키가 있으면 덮어씀

        - 같은 파일의 옛 파일명 키 행은 내용 행으로 옮기므로 함께 지움
        """

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO {FRAME_ANALYSIS_TABLE} ({SELECT_COLUMNS}) VALUES ({PLACEHOLDERS})",
                    record_to_row(record),
                )
                conn.execute(
                    f"DELETE FROM {FRAME_ANALYSIS_LEGACY_TABLE} "
                    "WHERE filename = ? AND filesize_bytes = ? AND mtime_ns = ?",
                    (record.filename, record.filesize_bytes, record.mtime_ns),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        self._run(_write)

    def count(self) -> int:
        """저장된 행 수 반환(옮기지 않은 옛 행 포함)"""

        def _count(conn: sqlite3.Connection) -> int:
            content = int(conn.execute(f"SELECT COUNT(*) FROM {FRAME_ANALYSIS_TABLE}").fetchone()[0])
            legacy = int(conn.execute(f"SELECT COUNT(*) FROM {FRAME_ANALYSIS_LEGACY_TABLE}").fetchone()[0])
            return content + legacy

        return self._run(_count)

    def count_for_roots(self, root_dirs: Sequence[str] | None = None) -> int:
        """화질 분석 캐시 행 수. root_dirs를 주면 그 작업 폴더 소속 논리 파일명만 셈"""

        def _count(conn: sqlite3.Connection) -> int:
            content, legacy = _frame_cache_keys(conn, root_dirs)
            return len(content) + len(legacy)

        return self._run(_count)

    def clear_for_roots(self, root_dirs: Sequence[str] | None = None) -> int:
        """화질 분석 캐시 행을 지우고 지운 행 수 반환

        - 수동 VR 보호와 잘라내기 제외 지정은 별도 테이블이라 함께 지우지 않음
        """
        content, legacy = self._run(lambda conn: _frame_cache_keys(conn, root_dirs))
        if not content and not legacy:
            return 0

        def _delete(conn: sqlite3.Connection) -> int:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for start in range(0, len(content), _LOOKUP_CHUNK_SIZE):
                    chunk = content[start : start + _LOOKUP_CHUNK_SIZE]
                    pairs = ", ".join("(?, ?)" for _ in chunk)
                    conn.execute(
                        f"DELETE FROM {FRAME_ANALYSIS_TABLE} "
                        f"WHERE ({', '.join(CONTENT_KEY_COLUMN_NAMES)}) IN ({pairs})",
                        [value for key in chunk for value in key],
                    )
                if legacy:
                    conn.executemany(
                        f"DELETE FROM {FRAME_ANALYSIS_LEGACY_TABLE} "
                        "WHERE filename = ? AND filesize_bytes = ? AND mtime_ns = ?",
                        legacy,
                    )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            return len(content) + len(legacy)

        return self._run(_delete)

    def count_crop_exclusions(self, root_dirs: Sequence[str] | None = None) -> int:
        """검은 여백 잘라내기 제외 지정 건수. root_dirs를 주면 그 범위 경로만 셈"""
        return self._run(lambda conn: len(_path_rows(conn, FRAME_ANALYSIS_CROP_TABLE, root_dirs)))

    def clear_crop_exclusions(self, root_dirs: Sequence[str] | None = None) -> int:
        """검은 여백 잘라내기 제외 지정을 지우고 지운 건수 반환"""
        return self._run(lambda conn: _delete_path_rows(conn, FRAME_ANALYSIS_CROP_TABLE, root_dirs))

    def count_manual_active_frames(self, root_dirs: Sequence[str] | None = None) -> int:
        """사용자 지정 활성 화면 건수. root_dirs를 주면 그 범위 경로만 셈"""
        return self._run(lambda conn: len(_path_rows(conn, FRAME_ANALYSIS_MANUAL_FRAME_TABLE, root_dirs)))

    def clear_manual_active_frames(self, root_dirs: Sequence[str] | None = None) -> int:
        """사용자 지정 활성 화면을 지우고 지운 건수 반환"""
        return self._run(lambda conn: _delete_path_rows(conn, FRAME_ANALYSIS_MANUAL_FRAME_TABLE, root_dirs))

    def count_vr_overrides(self, root_dirs: Sequence[str] | None = None) -> int:
        """수동 VR 보호 지정 건수. root_dirs를 주면 그 범위 경로만 셈"""
        return self._run(lambda conn: len(_path_rows(conn, FRAME_ANALYSIS_VR_TABLE, root_dirs)))

    def count_crop_review_acks(self, root_dirs: Sequence[str] | None = None) -> int:
        """여백 확인 필요 표시를 확인한 기록 건수. root_dirs를 주면 그 범위 경로만 셈"""
        return self._run(lambda conn: len(_path_rows(conn, FRAME_ANALYSIS_REVIEW_ACK_TABLE, root_dirs)))

    def clear_crop_review_acks(self, root_dirs: Sequence[str] | None = None) -> int:
        """여백 확인 표시 기록을 지워 분석 캐시를 지운 뒤에도 경고가 다시 뜨게 함"""
        return self._run(lambda conn: _delete_path_rows(conn, FRAME_ANALYSIS_REVIEW_ACK_TABLE, root_dirs))

    def clear_vr_overrides(self, root_dirs: Sequence[str] | None = None) -> int:
        """수동 VR 보호 지정을 지우고 지운 건수 반환"""
        return self._run(lambda conn: _delete_path_rows(conn, FRAME_ANALYSIS_VR_TABLE, root_dirs))

    def load_vr_overrides(self, paths: Sequence[str]) -> frozenset[str]:
        """수동 VR 보호로 저장된 전체 경로 집합 조회

        - 저장 시점 내용 지문이 지금 파일과 다르면(같은 이름, 크기, 수정 시각이어도) 그 경로를 빼고 행은 남김
        - 지문을 아직 모르는 행과 파일을 읽지 못한 행은 종전처럼 경로로만 판정
        """

        def _select(conn: sqlite3.Connection) -> frozenset[str]:
            keys = list(dict.fromkeys(path for path in paths if path))
            if not keys:
                return frozenset()
            placeholders = ", ".join("?" * len(keys))
            rows = conn.execute(
                f"SELECT source_path, filesize_bytes, mtime_ns, fingerprint"
                f" FROM {FRAME_ANALYSIS_VR_TABLE}"
                f" WHERE manual_vr = 1 AND source_path IN ({placeholders})",
                keys,
            ).fetchall()
            bad = mismatched_paths([(str(row[0]), optional_text(row[3])) for row in rows])
            return frozenset(str(row[0]) for row in rows if str(row[0]) not in bad)

        return self._run(_select)

    def set_vr_override(self, *, source_path: str, filename: str, manual_vr: bool) -> None:
        """수동 VR 보호를 저장하고 그 영상의 캐시 결과를 지워 다시 분석하게 함

        - 보호 여부에 따라 활성 화면과 축소 후보가 달라지므로 낡은 측정 결과를 남기지 않음
        - 측정 행은 내용 키로 식별하므로 이름이 바뀐 영상도 지우려면 그 경로의 내용 키를 풀어 씀.
          지문을 확인하지 못하면 옛 파일명 키 행만 지움
        - 저장하는 지정 행에도 내용 키를 함께 남겨 파일을 옮기거나 복사해도 지정을 찾음
        """
        identity = file_identity(source_path)
        fingerprint: str | None = None
        if identity is not None:
            key: FileKey = (filename, identity[0], identity[1])
            fingerprint = self._run(lambda conn: stored_fingerprint(conn, source_path, key))
            if fingerprint is None:
                fingerprint = computed_fingerprint(source_path, identity[0])

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if manual_vr:
                    conn.execute(
                        f"INSERT OR REPLACE INTO {FRAME_ANALYSIS_VR_TABLE}"
                        " (source_path, manual_vr, filesize_bytes, mtime_ns, fingerprint)"
                        " VALUES (?, 1, ?, ?, ?)",
                        (
                            source_path,
                            None if identity is None else identity[0],
                            None if identity is None else identity[1],
                            fingerprint,
                        ),
                    )
                else:
                    conn.execute(
                        f"DELETE FROM {FRAME_ANALYSIS_VR_TABLE} WHERE source_path = ?",
                        (source_path,),
                    )
                if identity is not None and fingerprint is not None:
                    conn.execute(
                        f"DELETE FROM {FRAME_ANALYSIS_TABLE} "
                        f"WHERE {CONTENT_KEY_COLUMN_NAMES[0]} = ? AND {CONTENT_KEY_COLUMN_NAMES[1]} = ?",
                        (identity[0], fingerprint),
                    )
                conn.execute(f"DELETE FROM {FRAME_ANALYSIS_TABLE} WHERE filename = ?", (filename,))
                conn.execute(f"DELETE FROM {FRAME_ANALYSIS_LEGACY_TABLE} WHERE filename = ?", (filename,))
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
                f"SELECT source_path, exclude_crop, filesize_bytes, mtime_ns, fingerprint"
                f" FROM {FRAME_ANALYSIS_CROP_TABLE}"
                f" WHERE exclude_crop = 1 AND source_path IN ({placeholders})",
                keys,
            ).fetchall()
            kept, stamped = validated_crop_exclusion_rows(
                [row[:4] for row in rows], identity_overrides=identity_overrides
            )
            _write_row_changes_best_effort(
                self._db_path,
                table=FRAME_ANALYSIS_CROP_TABLE,
                stamped=stamped,
            )
            return frozenset(
                _kept_with_matching_content(
                    rows, kept, fingerprint_index=4, identity_overrides=identity_overrides
                )
            )

        return self._run(_select)

    def set_crop_exclusion(self, source_path: str, excluded: bool) -> None:
        """영상별 검은 여백 잘라내기 제외를 저장. 분석 캐시는 지우지 않음

        - 활성 화면과 축소 후보는 그대로 유효하며 판정 입력에서만 잘라내기를 건너뜀
        - 저장 시점 파일 크기, 수정 시각, 내용 지문을 함께 남겨, 같은 경로의 파일이 바뀌면 읽을 때 버림
        """
        if not source_path:
            return
        identity = file_identity(source_path) if excluded else None
        fingerprint = None if identity is None else computed_fingerprint(source_path, identity[0])

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if excluded:
                    conn.execute(
                        f"INSERT OR REPLACE INTO {FRAME_ANALYSIS_CROP_TABLE}"
                        " (source_path, exclude_crop, filesize_bytes, mtime_ns, fingerprint)"
                        " VALUES (?, 1, ?, ?, ?)",
                        (
                            source_path,
                            None if identity is None else identity[0],
                            None if identity is None else identity[1],
                            fingerprint,
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
                f" filesize_bytes, mtime_ns, fingerprint FROM {FRAME_ANALYSIS_MANUAL_FRAME_TABLE}"
                f" WHERE source_path IN ({placeholders})",
                keys,
            ).fetchall()
            frames, dropped, stamped = validated_manual_rows(
                [row[:7] for row in rows], frame_sizes, identity_overrides=identity_overrides
            )
            _write_row_changes_best_effort(
                self._db_path,
                table=FRAME_ANALYSIS_MANUAL_FRAME_TABLE,
                drop_columns=MANUAL_FRAME_MATCH_COLUMNS,
                dropped=dropped,
                stamped=stamped,
            )
            kept = _kept_with_matching_content(
                rows, list(frames), fingerprint_index=7, identity_overrides=identity_overrides
            )
            return {path: frames[path] for path in kept}

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
        - 저장 시점 파일 크기, 수정 시각, 내용 지문을 함께 남겨, 같은 경로의 파일이 바뀌면 읽을 때 버림
        - 분석 캐시는 지우지 않음. 활성 화면은 판정 입력에서만 바뀌고 측정 결과는 그대로 씀
        """
        if not source_path:
            return
        normalized = None if frame is None else normalize_manual_active_frame(frame, frame_width, frame_height)
        identity = None if normalized is None else file_identity(source_path)
        fingerprint = (
            None
            if identity is None
            else computed_fingerprint(source_path, identity[0])
        )

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
                        " filesize_bytes, mtime_ns, fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            source_path,
                            normalized.x,
                            normalized.y,
                            normalized.width,
                            normalized.height,
                            None if identity is None else identity[0],
                            None if identity is None else identity[1],
                            fingerprint,
                        ),
                    )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        self._run(_write)

    def load_crop_review_acknowledged(
        self,
        regions: Mapping[str, tuple[int, int, int, int]],
        *,
        identity_overrides: Mapping[str, tuple[int, int]] | None = None,
    ) -> frozenset[str]:
        """어두운 근거 무시 자동 크롭을 사용자가 확인한 경로 집합 조회

        - `regions`는 경로별 자동 감지 활성 화면 (너비, 높이, x, y)이며, 저장 값과 다르면 새 분석이 다른
          영역으로 같은 경고를 낸 것이므로 그 경로를 빼고 행을 지움
        - 저장 시점의 파일 크기와 수정 시각이 지금 파일과 다르면 원본이 바뀐 행이라 그 경로를 빼고 행을 지움
        - 영역을 모르는 경로는 비교할 수 없으므로 빼고 지움
        - `identity_overrides`의 처리는 `load_crop_exclusions`와 같음. 다른 파일을 띄우는 조회에서 식별 값이
          맞지 않는 행은 결과 파일용 확인 표시일 수 있어 지우지 않고 그 조회에서만 건너뜀
        """

        def _select(conn: sqlite3.Connection) -> frozenset[str]:
            keys = list(dict.fromkeys(path for path in regions if path))
            if not keys:
                return frozenset()
            placeholders = ", ".join("?" * len(keys))
            rows = conn.execute(
                f"SELECT source_path, region_width, region_height, region_x, region_y,"
                f" filesize_bytes, mtime_ns FROM {FRAME_ANALYSIS_REVIEW_ACK_TABLE}"
                f" WHERE source_path IN ({placeholders})",
                keys,
            ).fetchall()
            kept, dropped, stamped = validated_review_ack_rows(rows, regions, identity_overrides=identity_overrides)
            _write_row_changes_best_effort(
                self._db_path,
                table=FRAME_ANALYSIS_REVIEW_ACK_TABLE,
                drop_columns=REVIEW_ACK_MATCH_COLUMNS,
                dropped=dropped,
                stamped=stamped,
            )
            return frozenset(kept)

        return self._run(_select)

    def set_crop_review_acknowledged(self, source_path: str, region: ActiveRegion | None) -> None:
        """영상의 여백 확인 필요 표시를 확인한 것으로 저장. None이면 지워 표시를 되살림

        - 자동 감지 영역을 함께 남겨, 다시 분석해 다른 영역이 나오면 그 표시를 지우고 경고를 되살림
        - 저장 시점 파일 크기와 수정 시각을 함께 남겨, 같은 경로의 파일이 바뀌면 읽을 때 버림
        - 분석 캐시는 지우지 않음. 확인 여부는 보류 판정 입력에서만 씀
        """
        if not source_path:
            return

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    f"DELETE FROM {FRAME_ANALYSIS_REVIEW_ACK_TABLE} WHERE source_path = ?",
                    (source_path,),
                )
                if region is not None:
                    identity = file_identity(source_path)
                    box = review_region_box(region)
                    conn.execute(
                        f"INSERT OR REPLACE INTO {FRAME_ANALYSIS_REVIEW_ACK_TABLE}"
                        " (source_path, region_width, region_height, region_x, region_y,"
                        " filesize_bytes, mtime_ns) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            source_path,
                            box[0],
                            box[1],
                            box[2],
                            box[3],
                            None if identity is None else identity[0],
                            None if identity is None else identity[1],
                        ),
                    )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        self._run(_write)

    def designation_sizes(self) -> set[int]:
        """경로 지정 행(수동 VR 보호, 잘라내기 제외, 사용자 지정 활성 화면)이 남긴 파일 크기 집합

        - 내용이 같은 파일은 크기도 같으므로, 스캔이 이 크기의 파일만 지문을 확인하게 좁힘
        - 내용 키가 없던 예전 행은 그 경로의 파일을 읽어 한 번 채워, 옛 지정도 새 경로를 따라가게 함
          (파일이 없거나 읽지 못한 행은 NULL로 남아 종전처럼 경로로만 적용)
        """
        tables = (FRAME_ANALYSIS_VR_TABLE, FRAME_ANALYSIS_CROP_TABLE, FRAME_ANALYSIS_MANUAL_FRAME_TABLE)

        def _select(conn: sqlite3.Connection) -> set[int]:
            sizes: set[int] = set()
            for table in tables:
                fill_missing_fingerprints(conn, table=table)
                rows = conn.execute(
                    f"SELECT DISTINCT filesize_bytes FROM {table} WHERE filesize_bytes IS NOT NULL"
                ).fetchall()
                sizes.update(int(str(row[0])) for row in rows)
            return sizes

        return self._run(_select)

    def sync_designations(self, found: Mapping[str, ContentIdentity]) -> list[ContentMove[str]]:
        """스캔에서 찾은 내용 키로 경로 지정 행(수동 VR 보호, 잘라내기 제외, 사용자 지정 활성 화면)을 새 경로에 붙임

        - 옛 경로의 파일이 사라졌으면 그 행을 새 경로로 옮기고, 남아 있으면 복사(독립된 행)
        - 이미 지정이 있는 경로는 건드리지 않아 사용자가 새로 고른 값을 덮지 않음
        - 내용이 다른 파일에는 붙이지 않음(파일 크기와 지문이 모두 같은 행만 짝지음)
        """
        if not found:
            return []

        def _write(conn: sqlite3.Connection) -> list[ContentMove[str]]:
            conn.execute("BEGIN IMMEDIATE")
            try:
                moves: list[ContentMove[str]] = []
                for table in (FRAME_ANALYSIS_VR_TABLE, FRAME_ANALYSIS_CROP_TABLE, FRAME_ANALYSIS_MANUAL_FRAME_TABLE):
                    moves.extend(sync_path_designations(conn, table=table, found=found))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            return moves

        return self._run(_write)

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
            prepare_cache_tables(conn)
        except BaseException:
            conn.close()
            raise
        with self._lock:
            self._connections[ident] = conn
        return conn
