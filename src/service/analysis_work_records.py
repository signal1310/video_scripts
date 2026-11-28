"""분석 캐시 DB의 작업 기록 테이블(출력 해상도 직접 지정, 표 선택 기억) 저장 모듈

- 분석 캐시 스키마와 무관하게 유지되어 VideoProps 변경이나 캐시 비우기로 사라지지 않음
- 정리 범위(root_dirs)에 드는 행만 세거나 지워 다른 작업 폴더의 기록을 건드리지 않음
- 작업 폴더 키는 정규화된 폴더 목록 문자열이며, 범위 판정은 저장된 키의 모든 폴더가 범위 안일 때만 참
- 출력 해상도 직접 지정은 경로(작업 폴더 키, 파일명)와 함께 내용 키(크기, 내용 지문)를 남겨,
  파일을 옮기거나 복사해도 같은 지정을 찾고 내용이 다른 파일에는 붙이지 않음
"""

import logging
import os
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

from src.core.workspace_paths import (
    is_within_roots,
    logical_name_for_path,
    normalize_root_key,
    parse_root_dirs,
    resolve_source_filepath,
)
from src.service.designation_content import (
    ContentIdentity,
    file_content_identity,
    optional_text,
    plan_content_moves,
)
from src.service.frame_override_rows import add_missing_columns, optional_int
from src.service.sql_transaction import transaction

logger = logging.getLogger(__name__)

MANUAL_TARGET_TABLE_NAME: str = "manual_target"
_MANUAL_TARGET_TABLE_SQL: str = (
    f"CREATE TABLE {MANUAL_TARGET_TABLE_NAME} (\n"
    "  root_key TEXT NOT NULL,\n"
    "  filename TEXT NOT NULL,\n"
    "  tier TEXT NOT NULL,\n"
    "  filesize_bytes INTEGER,\n"
    "  fingerprint TEXT,\n"
    "  PRIMARY KEY (root_key, filename)\n"
    ") WITHOUT ROWID"
)
# - 이 표를 만들기 전 DB에는 내용 키 열이 없으므로 열만 덧붙임
_MANUAL_TARGET_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    (MANUAL_TARGET_TABLE_NAME, "filesize_bytes", "INTEGER"),
    (MANUAL_TARGET_TABLE_NAME, "fingerprint", "TEXT"),
)
# - 같은 키의 행이 있으면 규격만 바꿔, 새 지정을 저장할 때 남아 있던 내용 키를 지우지 않음
_MANUAL_TARGET_UPSERT_SQL: str = (
    f"INSERT INTO {MANUAL_TARGET_TABLE_NAME} (root_key, filename, tier) VALUES (?, ?, ?)"
    " ON CONFLICT (root_key, filename) DO UPDATE SET tier = excluded.tier"
)

# - 출력 해상도 지정 행의 식별자(작업 폴더 키, 논리 파일명)
TargetId = tuple[str, str]


class ManualTargetRow(NamedTuple):
    """출력 해상도 직접 지정 행의 읽기 값(백업용, 경로와 저장 값 그대로)"""

    root_key: str
    filename: str
    tier: str
    filesize_bytes: int | None
    fingerprint: str | None


@dataclass(frozen=True, slots=True)
class ManualTargetSync:
    """출력 해상도 직접 지정을 내용 키로 새 경로에 붙인 결과

    - `moved`: 옛 경로의 파일이 사라져 지정을 옮긴 (옛 행, 새 행) 목록
    - `copied`: 옛 경로의 파일이 남아 있어 복사한 (옛 행, 새 행) 목록
    - `stale`: 저장한 뒤 같은 경로의 파일 내용이 바뀌어 판정에 쓰지 않을 행
    """

    moved: list[tuple[TargetId, TargetId]] = field(default_factory=list)
    copied: list[tuple[TargetId, TargetId]] = field(default_factory=list)
    stale: frozenset[TargetId] = frozenset()

    @property
    def changed(self) -> bool:
        """지정 행을 새 경로로 붙인 것이 있는지"""
        return bool(self.moved or self.copied)


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


def _manual_target_rows(store: Mapping[str, Mapping[str, str]]) -> list[tuple[str, str, str]]:
    return [(root, name, tier) for root, entries in store.items() for name, tier in entries.items()]


def _ensure_manual_target_table(conn: sqlite3.Connection) -> bool:
    """직접 지정 테이블이 없으면 만들고, 이번 호출에서 새로 만들었는지 반환

    - 분석 캐시 스키마 검사와 무관하게 유지되어 VideoProps 변경으로 사라지지 않음
    - 이 표를 만들기 전 DB에는 내용 키 열이 없으므로 열을 덧붙임
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (MANUAL_TARGET_TABLE_NAME,)
    ).fetchone()
    if exists is None:
        conn.execute(_MANUAL_TARGET_TABLE_SQL)
        return True
    add_missing_columns(conn, _MANUAL_TARGET_ADDED_COLUMNS)
    return False


def read_manual_targets(conn: sqlite3.Connection, legacy: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """작업 폴더별 출력 해상도 직접 지정 맵을 읽고, 테이블을 처음 만드는 때만 옛 맵(legacy)을 옮김

    - 테이블 생성과 옛 맵 이전을 한 트랜잭션으로 묶어 중간 실패 시 이전이 반복되지 않게 함
    """
    with transaction(conn):
        if _ensure_manual_target_table(conn):
            conn.executemany(_MANUAL_TARGET_UPSERT_SQL, _manual_target_rows(legacy))
        rows = conn.execute(
            f"SELECT root_key, filename, tier FROM {MANUAL_TARGET_TABLE_NAME}"
        ).fetchall()
    result: dict[str, dict[str, str]] = {}
    for root, name, tier in rows:
        result.setdefault(str(root), {})[str(name)] = str(tier)
    return result


def write_manual_targets(conn: sqlite3.Connection, store: Mapping[str, Mapping[str, str]]) -> None:
    """작업 폴더별 직접 지정 맵 전체를 한 트랜잭션으로 교체 저장

    - 저장되지 않은 행만 지우고 나머지는 규격만 갱신해, 남는 지정의 내용 키를 잃지 않음
    - 내용 키가 없던 행은 지금 파일에서 읽어 채워, 파일을 옮기거나 복사해도 지정이 따라가게 함
    - 이미 내용 키가 있는 행은 그대로 둬, 같은 경로의 내용이 바뀐 행이 이 저장 때문에 다시 붙지 않게 함
      (사용자가 그 영상에 지정을 새로 고르면 `refresh_manual_target_identities`가 키를 다시 읽음)
    """
    rows = _manual_target_rows(store)
    keep = {(root, name) for root, name, _tier in rows}
    with transaction(conn):
        _ensure_manual_target_table(conn)
        existing = conn.execute(
            f"SELECT root_key, filename FROM {MANUAL_TARGET_TABLE_NAME}"
        ).fetchall()
        dropped = [(str(root), str(name)) for root, name in existing if (str(root), str(name)) not in keep]
        conn.executemany(
            f"DELETE FROM {MANUAL_TARGET_TABLE_NAME} WHERE root_key = ? AND filename = ?",
            dropped,
        )
        conn.executemany(_MANUAL_TARGET_UPSERT_SQL, rows)
        fill_manual_target_identities(conn)


def read_table_selection(conn: sqlite3.Connection, root_key: str) -> set[str]:
    """작업 폴더에 마지막으로 선택되어 있던 영상 파일명 집합을 읽음"""
    with transaction(conn):
        conn.execute(_TABLE_SELECTION_TABLE_SQL)
        rows = conn.execute(
            f"SELECT filename FROM {TABLE_SELECTION_TABLE_NAME} WHERE root_key = ?",
            (root_key,),
        ).fetchall()
    return {str(row[0]) for row in rows}


def write_table_selection(conn: sqlite3.Connection, root_key: str, filenames: Iterable[str]) -> None:
    """작업 폴더에 선택된 영상 파일명 집합을 한 트랜잭션으로 교체 저장"""
    entries = [(root_key, name) for name in filenames if name]
    with transaction(conn):
        conn.execute(_TABLE_SELECTION_TABLE_SQL)
        conn.execute(
            f"DELETE FROM {TABLE_SELECTION_TABLE_NAME} WHERE root_key = ?",
            (root_key,),
        )
        if entries:
            conn.executemany(_TABLE_SELECTION_INSERT_SQL, entries)


def _root_key_in_scope(root_key: str, root_dirs: Sequence[str]) -> bool:
    """저장된 작업 폴더 키가 정리 범위 안인지. 키는 정규화된 폴더 목록 문자열"""
    keys = parse_root_dirs(root_key)
    return bool(keys) and all(is_within_roots(key, root_dirs) for key in keys)


def _manual_target_keys(conn: sqlite3.Connection, root_dirs: Sequence[str] | None) -> list[tuple[str, str]]:
    """정리 범위에 드는 직접 지정 행의 (작업 폴더 키, 파일명) 목록"""
    _ensure_manual_target_table(conn)
    rows = conn.execute(f"SELECT root_key, filename FROM {MANUAL_TARGET_TABLE_NAME}").fetchall()
    return [
        (str(row[0]), str(row[1]))
        for row in rows
        if root_dirs is None or _root_key_in_scope(str(row[0]), root_dirs)
    ]


def _table_selection_keys(conn: sqlite3.Connection, root_dirs: Sequence[str] | None) -> list[tuple[str, str]]:
    """정리 범위에 드는 표 선택 행의 (작업 폴더 키, 파일명) 목록"""
    conn.execute(_TABLE_SELECTION_TABLE_SQL)
    rows = conn.execute(f"SELECT root_key, filename FROM {TABLE_SELECTION_TABLE_NAME}").fetchall()
    return [
        (str(row[0]), str(row[1]))
        for row in rows
        if root_dirs is None or _root_key_in_scope(str(row[0]), root_dirs)
    ]


def manual_target_sizes(conn: sqlite3.Connection) -> set[int]:
    """직접 지정 행이 남긴 파일 크기 집합

    - 내용이 같은 파일은 크기도 같으므로, 스캔이 이 크기의 파일만 지문을 확인하게 좁힘
    - 내용 키가 없던 예전 행은 그 경로의 파일을 읽어 한 번 채워, 옛 지정도 새 경로를 따라가게 함
      (파일이 없거나 읽지 못한 행은 NULL로 남아 종전처럼 경로로만 적용)
    """
    _ensure_manual_target_table(conn)
    fill_manual_target_identities(conn)
    rows = conn.execute(
        f"SELECT DISTINCT filesize_bytes FROM {MANUAL_TARGET_TABLE_NAME} WHERE filesize_bytes IS NOT NULL"
    ).fetchall()
    return {int(str(row[0])) for row in rows}


def count_manual_targets(conn: sqlite3.Connection, root_dirs: Sequence[str] | None = None) -> int:
    """영상별 출력 해상도 직접 지정 건수. root_dirs를 주면 그 작업 폴더 키의 지정만 셈"""
    return len(_manual_target_keys(conn, root_dirs))


def read_manual_target_rows(conn: sqlite3.Connection) -> list[ManualTargetRow]:
    """출력 해상도 직접 지정 행 전체를 저장 값 그대로 읽음. 백업에 씀"""
    _ensure_manual_target_table(conn)
    rows = conn.execute(
        f"SELECT root_key, filename, tier, filesize_bytes, fingerprint FROM {MANUAL_TARGET_TABLE_NAME}"
    ).fetchall()
    return [
        ManualTargetRow(str(row[0]), str(row[1]), str(row[2]), optional_int(row[3]), optional_text(row[4]))
        for row in rows
    ]


def clear_manual_targets(
    conn: sqlite3.Connection,
    root_dirs: Sequence[str] | None = None,
    filenames: Sequence[str] | None = None,
) -> int:
    """영상별 직접 지정을 정리 범위에서 지우고 지운 건수 반환

    - filenames를 주면 정리 범위 안에서 그 파일명 행만 지움
    """
    keys = _manual_target_keys(conn, root_dirs)
    if filenames is not None:
        wanted = set(filenames)
        keys = [key for key in keys if key[1] in wanted]
    if not keys:
        return 0
    with transaction(conn):
        conn.executemany(
            f"DELETE FROM {MANUAL_TARGET_TABLE_NAME} WHERE root_key = ? AND filename = ?",
            keys,
        )
    return len(keys)


def _manual_target_path(root_key: str, filename: str) -> str | None:
    """저장된 (작업 폴더 키, 파일명)이 가리키는 절대 경로. 만들 수 없으면 None

    - 키는 정규화된 폴더 목록 문자열이라 작업 폴더가 하나면 그대로 잇고, 여럿이면 라벨 접두사를 다시 붙임
    """
    if not root_key or not filename:
        return None
    try:
        return resolve_source_filepath(root_key, filename)
    except (OSError, ValueError):
        return None


def _manual_target_identity(row: Sequence[object]) -> ContentIdentity | None:
    """직접 지정 행의 (크기, 지문). 둘 중 하나라도 없으면 None"""
    size, fingerprint = row[3], row[4]
    if size is None or fingerprint is None:
        return None
    return (int(str(size)), str(fingerprint))


def refresh_manual_target_identities(
    conn: sqlite3.Connection, targets: Sequence[tuple[str, str]]
) -> int:
    """지정 행의 내용 키를 지금 파일에서 다시 읽어 채움. 채운 행 수 반환

    - 사용자가 지정을 저장할 때 반영해, 같은 경로의 내용이 바뀐 뒤에도 새 지정이 그 내용에 붙음
    - 파일을 읽지 못하는 행(지워졌거나 옮겨진 행)은 저장 값을 그대로 두어 다음 스캔이 새 경로를 찾게 함
    """
    updates: list[tuple[int, str, str, str]] = []
    for root_key, filename in targets:
        path = _manual_target_path(root_key, filename)
        if path is None:
            continue
        identity = file_content_identity(path)
        if identity is None:
            continue
        updates.append((identity[0], identity[1], root_key, filename))
    if not updates:
        return 0
    conn.executemany(
        f"UPDATE {MANUAL_TARGET_TABLE_NAME} SET filesize_bytes = ?, fingerprint = ?"
        " WHERE root_key = ? AND filename = ?",
        updates,
    )
    return len(updates)


def fill_manual_target_identities(
    conn: sqlite3.Connection,
    *,
    found: Mapping[str, ContentIdentity] | None = None,
) -> int:
    """내용 키가 없던 직접 지정 행에 지금 파일의 (크기, 지문)을 채움. 채운 행 수 반환

    - 스캔이 그 경로의 내용 키를 확인했으면 파일을 읽지 않고 그 값을 씀
    - 파일을 읽지 못한 행은 NULL로 남겨 종전처럼 경로로만 적용
    """
    _ensure_manual_target_table(conn)
    rows = conn.execute(
        f"SELECT root_key, filename FROM {MANUAL_TARGET_TABLE_NAME} WHERE fingerprint IS NULL"
    ).fetchall()
    updates: list[tuple[int, str, str, str]] = []
    for root_key, filename in rows:
        path = _manual_target_path(str(root_key), str(filename))
        if path is None:
            continue
        identity = found.get(path) if found is not None else None
        if identity is None:
            identity = file_content_identity(path)
        if identity is None:
            continue
        updates.append((identity[0], identity[1], str(root_key), str(filename)))
    if not updates:
        return 0
    conn.executemany(
        f"UPDATE {MANUAL_TARGET_TABLE_NAME} SET filesize_bytes = ?, fingerprint = ?"
        " WHERE root_key = ? AND filename = ? AND fingerprint IS NULL",
        updates,
    )
    return len(updates)


def _scanned_targets(found: Mapping[str, ContentIdentity], root_spec: str) -> dict[TargetId, ContentIdentity]:
    """스캔이 확인한 경로를 현재 작업 폴더의 직접 지정 행 식별자로 바꿈

    - 식별자는 저장 키와 같은 정규화 폴더 목록 문자열과 논리 파일명이라 저장 행과 그대로 비교됨
    - 작업 폴더 범위 밖 경로는 지정을 붙일 수 없으므로 빠짐
    """
    key = normalize_root_key(root_spec)
    targets: dict[TargetId, ContentIdentity] = {}
    for path, identity in found.items():
        located = logical_name_for_path(path, root_spec)
        if located is not None:
            targets[(key, located[1])] = identity
    return targets


def sync_manual_target_designations(
    conn: sqlite3.Connection, *, found: Mapping[str, ContentIdentity], root_spec: str
) -> ManualTargetSync:
    """출력 해상도 직접 지정을 내용 키로 새 경로(다른 작업 폴더 포함)에 옮기거나 복사

    - 옛 경로의 파일이 사라졌으면 그 행을 현재 작업 폴더의 새 행으로 옮김
    - 옛 경로의 파일이 남아 있으면(복사본) 원본 행은 그대로 두고 새 행을 만듬
    - 이미 지정이 있는 경로는 건드리지 않아 사용자가 고른 규격을 덮지 않음
    - 저장한 뒤 같은 경로의 파일 내용이 바뀐 행은 옮기지 않고 `stale`로 알려 판정에서 뺌
    - 스캔이 확인한 내용이 없어도 `stale` 판정은 함(크기까지 바뀐 파일도 옛 지정을 쓰지 않게)
    """
    if not found:
        with transaction(conn):
            _ensure_manual_target_table(conn)
            existing = conn.execute(
                f"SELECT root_key, filename, tier, filesize_bytes, fingerprint FROM {MANUAL_TARGET_TABLE_NAME}"
            ).fetchall()
            stale_only = _stale_targets(existing, root_spec)
        return ManualTargetSync(stale=stale_only)
    scanned = _scanned_targets(found, root_spec)
    with transaction(conn):
        _ensure_manual_target_table(conn)
        fill_manual_target_identities(conn, found=found)
        rows = conn.execute(
            f"SELECT root_key, filename, tier, filesize_bytes, fingerprint FROM {MANUAL_TARGET_TABLE_NAME}"
        ).fetchall()
        tiers = {(str(row[0]), str(row[1])): str(row[2]) for row in rows}
        moves = plan_content_moves(
            [((str(row[0]), str(row[1])), _manual_target_identity(row)) for row in rows],
            sorted(
                (target, identity) for target, identity in scanned.items() if target not in tiers
            ),
            source_still_present=_source_present,
        )
        moved: list[tuple[TargetId, TargetId]] = []
        copied: list[tuple[TargetId, TargetId]] = []
        for move in moves:
            identity = scanned[move.target_id]
            if move.moved:
                conn.execute(
                    f"DELETE FROM {MANUAL_TARGET_TABLE_NAME} WHERE root_key = ? AND filename = ?",
                    move.source_id,
                )
                moved.append((move.source_id, move.target_id))
            else:
                copied.append((move.source_id, move.target_id))
            conn.execute(
                f"INSERT INTO {MANUAL_TARGET_TABLE_NAME}"
                " (root_key, filename, tier, filesize_bytes, fingerprint) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (root_key, filename) DO UPDATE SET tier = excluded.tier",
                (*move.target_id, tiers[move.source_id], identity[0], identity[1]),
            )
        stale = _stale_targets(rows, root_spec)
    return ManualTargetSync(moved=moved, copied=copied, stale=stale)


def _source_present(source: TargetId) -> bool:
    """옛 직접 지정 행의 파일이 아직 있는지

    - 경로를 만들 수 없으면 지우지 않도록 있는 것으로 봄(모르는 상태에서 행을 잃지 않게)
    """
    path = _manual_target_path(*source)
    return True if path is None else os.path.exists(path)


def _stale_targets(rows: Sequence[Sequence[object]], root_spec: str) -> frozenset[TargetId]:
    """저장한 뒤 같은 경로의 파일 내용이 바뀐 현재 작업 폴더의 직접 지정 행

    - 범위 밖 작업 폴더 행은 이번 판정에 쓰이지 않으므로 파일을 읽지 않음
    - 크기가 다르면 지문을 계산하지 않고 바뀐 것으로 봄(지정 영상마다 파일을 읽지 않게)
    """
    key = normalize_root_key(root_spec)
    stale: set[TargetId] = set()
    for row in rows:
        target = (str(row[0]), str(row[1]))
        identity = _manual_target_identity(row)
        if target[0] != key or identity is None:
            continue
        path = _manual_target_path(*target)
        if path is None:
            continue
        try:
            size = os.stat(path).st_size
        except OSError:
            continue
        if size == identity[0]:
            current = file_content_identity(path)
            if current is None or current[1] == identity[1]:
                continue
        logger.debug(
            "출력 해상도 직접 지정을 건너뜁니다. 같은 경로의 영상 내용이 바뀌었습니다: %s", path
        )
        stale.add(target)
    return frozenset(stale)


def count_table_selections(conn: sqlite3.Connection, root_dirs: Sequence[str] | None = None) -> int:
    """표 선택 기억(작업 폴더별 선택 파일명) 건수. root_dirs를 주면 그 작업 폴더 키만 셈"""
    return len(_table_selection_keys(conn, root_dirs))


def clear_table_selections(conn: sqlite3.Connection, root_dirs: Sequence[str] | None = None) -> int:
    """표 선택 기억을 정리 범위에서 지우고 지운 건수 반환"""
    keys = _table_selection_keys(conn, root_dirs)
    if not keys:
        return 0
    with transaction(conn):
        conn.executemany(
            f"DELETE FROM {TABLE_SELECTION_TABLE_NAME} WHERE root_key = ? AND filename = ?",
            keys,
        )
    return len(keys)
