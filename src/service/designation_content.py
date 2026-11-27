"""영상별 직접 지정 행의 내용 키(크기, 내용 지문) 기록과 새 경로 이관 모듈

- 지정은 경로로 묶여 있어 이름이나 폴더가 바뀌면 붙지 않으므로, 각 행에 내용 키를 함께 남겨
  스캔에서 같은 내용의 새 경로를 찾아 옮기거나 복사함
- 옛 경로에 파일이 아직 있으면 복사로 보고, 사라졌으면 옮기기로 봄(복사본도 같은 지정을 받음)
- 내용이 다른 파일에는 지정을 붙이지 않음(이름, 크기, 수정 시각이 같아도 지문이 다르면 다른 내용)
- 지문을 모르는 행(파일을 읽지 못했거나 아직 채우지 못한 행)은 종전처럼 경로로만 적용
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from src.core.analysis.file_fingerprint import compute_fingerprint

logger = logging.getLogger(__name__)

# - 내용 키(파일 크기, 내용 지문). 분석 캐시의 내용 키와 같은 구성이라 저장 값에서 지문을 그대로 씀
ContentIdentity = tuple[int, str]
# - 지정 행과 새 경로의 식별자. 경로 지정 표는 경로 문자열, 출력 해상도 지정은 (작업 폴더 키, 파일명)
RowId = TypeVar("RowId")

# - 지정 표 이름의 정본은 `frame_analysis_schema.py`이나, 그 모듈이 분석 캐시 저장소를 가져오므로
#   이 모듈이 다시 가져오면 순환이 생김. 같은 이름을 두고 테스트에서 두 값이 같은지 확인
VR_TABLE: str = "frame_analysis_vr_override"
CROP_TABLE: str = "frame_analysis_crop_exclude"
MANUAL_FRAME_TABLE: str = "frame_analysis_manual_active_frame"

# - 경로 지정 표가 경로 밖에 지닌 값(옮기기와 복사가 그대로 넘김). 순서가 조회 열 순서와 같음
DESIGNATION_VALUE_COLUMNS: dict[str, tuple[str, ...]] = {
    VR_TABLE: ("manual_vr", "filesize_bytes", "mtime_ns", "fingerprint"),
    CROP_TABLE: ("exclude_crop", "filesize_bytes", "mtime_ns", "fingerprint"),
    MANUAL_FRAME_TABLE: (
        "manual_x",
        "manual_y",
        "manual_width",
        "manual_height",
        "filesize_bytes",
        "mtime_ns",
        "fingerprint",
    ),
}


def file_content_identity(path: str) -> ContentIdentity | None:
    """파일을 읽어 내용 키(크기, 내용 지문)를 구함. 읽지 못하면 None"""
    try:
        stat = os.stat(path)
        return (stat.st_size, compute_fingerprint(path, stat.st_size))
    except OSError:
        logger.warning("영상별 지정의 내용 지문을 확인하지 못했습니다: %s", path)
        return None


def designation_rows(conn: sqlite3.Connection, table: str) -> list[tuple[str, tuple[object, ...]]]:
    """표의 (경로, 함께 옮길 값들) 목록"""
    columns = DESIGNATION_VALUE_COLUMNS[table]
    selected = ", ".join(("source_path", *columns))
    return [
        (str(row[0]), tuple(row[1:]))
        for row in conn.execute(f"SELECT {selected} FROM {table}").fetchall()
    ]


def stored_identity(values: Sequence[object], *, table: str) -> ContentIdentity | None:
    """행의 값들에서 저장된 내용 키(크기, 지문). 둘 중 하나라도 없으면 None"""
    size_index, _mtime_index, fingerprint_index = value_indexes(table)
    size = values[size_index - 1]
    fingerprint = values[fingerprint_index - 1]
    if size is None or fingerprint is None:
        return None
    return (int(str(size)), str(fingerprint))


def fill_missing_fingerprints(
    conn: sqlite3.Connection,
    *,
    table: str,
    found: Mapping[str, ContentIdentity] | None = None,
) -> int:
    """지문이 없던 지정 행에 지금 내용 키와 파일 식별 값을 채움. 채운 행 수 반환

    - `found`에 그 경로가 있으면 스캔이 확인한 내용 키를 그대로 씀
    - 없으면 그 경로의 파일을 읽어 채움
    - 파일을 읽지 못한 행은 NULL로 남겨 종전처럼 경로로만 적용
    """
    rows = conn.execute(f"SELECT source_path FROM {table} WHERE fingerprint IS NULL").fetchall()
    filled: list[tuple[int, int, str, str]] = []
    for (path,) in rows:
        key = str(path)
        identity = found.get(key) if found is not None else None
        if identity is None:
            identity = file_content_identity(key)
        if identity is None:
            continue
        try:
            mtime = os.stat(key).st_mtime_ns
        except OSError:
            continue
        filled.append((identity[0], mtime, identity[1], key))
    if not filled:
        return 0
    conn.executemany(
        f"UPDATE {table} SET filesize_bytes = ?, mtime_ns = ?, fingerprint = ?"
        " WHERE source_path = ? AND fingerprint IS NULL",
        filled,
    )
    return len(filled)


def _relocated_values(values: Sequence[object], path: str, *, table: str) -> tuple[object, ...]:
    """옮기거나 복사한 행의 크기와 수정 시각을 새 경로 파일의 값으로 바꾼 값 목록

    - 내용은 같아도 복사본의 수정 시각은 다르므로, 그대로 넘기면 파일 식별 검증이 이 행을 바뀐 행으로 봄
    """
    size_index, mtime_index, _fingerprint_index = value_indexes(table)
    try:
        stat = os.stat(path)
    except OSError:
        return tuple(values)
    updated = list(values)
    updated[size_index - 1] = stat.st_size
    updated[mtime_index - 1] = stat.st_mtime_ns
    return tuple(updated)


def sync_path_designations(
    conn: sqlite3.Connection, *, table: str, found: Mapping[str, ContentIdentity]
) -> list[ContentMove[str]]:
    """스캔에서 찾은 내용 키로 지정 행을 새 경로로 옮기거나 복사

    - 이미 지정이 있는 경로는 건드리지 않아 사용자가 새로 고른 값을 덮지 않음
    - 옛 경로의 파일이 아직 있으면 원본은 그대로 두고 새 경로에 같은 지정을 복사(독립된 행)
    - 옛 경로의 파일이 사라졌으면 그 행을 새 경로로 옮김
    """
    if not found:
        return []
    fill_missing_fingerprints(conn, table=table, found=found)
    rows = designation_rows(conn, table)
    if not rows:
        return []
    values_by_path = dict(rows)
    moves = plan_content_moves(
        [(path, stored_identity(values, table=table)) for path, values in rows],
        [(path, identity) for path, identity in sorted(found.items()) if path not in values_by_path],
        source_still_present=os.path.exists,
    )
    if not moves:
        return []
    columns = DESIGNATION_VALUE_COLUMNS[table]
    insert_sql = (
        f"INSERT OR REPLACE INTO {table} (source_path, {', '.join(columns)})"
        f" VALUES ({', '.join('?' * (len(columns) + 1))})"
    )
    for move in moves:
        values = _relocated_values(values_by_path[move.source_id], move.target_id, table=table)
        if move.moved:
            conn.execute(f"DELETE FROM {table} WHERE source_path = ?", (move.source_id,))
        conn.execute(insert_sql, (move.target_id, *values))
    return moves


def content_mismatch(stored: str | None, current: ContentIdentity | None) -> bool:
    """저장된 지문과 지금 파일의 지문이 모두 알려졌고 다르면 참(크기와 수정 시각이 같아도 다른 내용)"""
    return stored is not None and current is not None and stored != current[1]


def value_indexes(table: str) -> tuple[int, int, int]:
    """경로 지정 표 행에서 (크기, 수정 시각, 지문)의 값 위치(경로 열이 0번)"""
    columns = DESIGNATION_VALUE_COLUMNS[table]
    return (
        columns.index("filesize_bytes") + 1,
        columns.index("mtime_ns") + 1,
        columns.index("fingerprint") + 1,
    )


@dataclass(frozen=True, slots=True)
class ContentMove(Generic[RowId]):
    """같은 내용으로 새 경로에 붙일 지정

    - `moved`가 참이면 옛 경로의 파일이 사라져 지정을 옮기는 것(옛 행을 지움)
    - 거짓이면 옛 경로의 파일이 아직 있어 지정을 복사하는 것(옛 행은 그대로 둠)
    """

    source_id: RowId
    target_id: RowId
    moved: bool


def plan_content_moves(
    rows: Sequence[tuple[RowId, ContentIdentity | None]],
    found: Sequence[tuple[RowId, ContentIdentity]],
    *,
    source_still_present: Callable[[RowId], bool],
) -> list[ContentMove[RowId]]:
    """내용이 같은 지정 행과 새로 나타난 경로를 짝지음

    - rows는 (행 식별자, 그 행이 남긴 내용 키)이며 내용 키를 모르는 행은 짝짓지 않음
    - found는 (새 식별자, 스캔이 확인한 내용 키)이며 호출자가 이미 지정이 없는 것만 넘김
    - 옛 경로의 파일이 아직 있으면 복사, 사라졌으면 옮김. 같은 내용의 옛 행이 여럿이면
      사라진 경로의 행을 먼저 씀
    - 한 옛 행은 한 새 경로에만 쓰임
    """
    candidates: dict[ContentIdentity, list[RowId]] = {}
    for row_id, identity in rows:
        if identity is not None:
            candidates.setdefault(identity, []).append(row_id)
    moves: list[ContentMove[RowId]] = []
    for target_id, identity in found:
        options = candidates.get(identity)
        if not options:
            continue
        gone = next((old for old in options if not source_still_present(old)), None)
        source_id = gone if gone is not None else options[0]
        options.remove(source_id)
        if not options:
            del candidates[identity]
        moves.append(ContentMove(source_id, target_id, moved=gone is not None))
    return moves


def optional_text(value: object) -> str | None:
    """SQLite 행의 NULL 허용 글자 값을 `str | None`으로 읽음"""
    return None if value is None else str(value)


def mismatched_paths(entries: Sequence[tuple[str, str | None]]) -> set[str]:
    """(경로, 저장 지문) 목록 중 지금 파일의 내용이 저장 지문과 다른 경로 집합

    - 같은 이름, 크기, 수정 시각이어도 내용이 다르면 지정을 붙이지 않음
    - 지문을 모르는 행과 파일을 읽지 못한 행은 종전처럼 경로로만 적용하므로 빼지 않음
    - 지정된 경로만 읽으므로 지정이 없으면 파일을 읽지 않음
    """
    mismatched: set[str] = set()
    for path, stored in entries:
        if stored is None:
            continue
        if content_mismatch(stored, file_content_identity(path)):
            logger.debug("영상별 지정을 건너뜁니다. 같은 경로의 영상 내용이 바뀌었습니다: %s", path)
            mismatched.add(path)
    return mismatched
