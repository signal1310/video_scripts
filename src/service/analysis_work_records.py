"""분석 캐시 DB의 작업 기록 테이블(출력 해상도 직접 지정, 표 선택 기억) 저장 모듈

- 분석 캐시 스키마와 무관하게 유지되어 VideoProps 변경이나 캐시 비우기로 사라지지 않음
- 정리 범위(root_dirs)에 드는 행만 세거나 지워 다른 작업 폴더의 기록을 건드리지 않음
- 작업 폴더 키는 정규화된 폴더 목록 문자열이며, 범위 판정은 저장된 키의 모든 폴더가 범위 안일 때만 참
"""

import sqlite3
from collections.abc import Iterable, Mapping, Sequence

from src.core.workspace_paths import is_within_roots, parse_root_dirs
from src.service.sql_transaction import transaction

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
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (MANUAL_TARGET_TABLE_NAME,)
    ).fetchone()
    if exists is None:
        conn.execute(_MANUAL_TARGET_TABLE_SQL)
        return True
    return False


def read_manual_targets(conn: sqlite3.Connection, legacy: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """작업 폴더별 출력 해상도 직접 지정 맵을 읽고, 테이블을 처음 만드는 때만 옛 맵(legacy)을 옮김

    - 테이블 생성과 옛 맵 이전을 한 트랜잭션으로 묶어 중간 실패 시 이전이 반복되지 않게 함
    """
    with transaction(conn):
        if _ensure_manual_target_table(conn):
            conn.executemany(_MANUAL_TARGET_INSERT_SQL, _manual_target_rows(legacy))
        rows = conn.execute(
            f"SELECT root_key, filename, tier FROM {MANUAL_TARGET_TABLE_NAME}"
        ).fetchall()
    result: dict[str, dict[str, str]] = {}
    for root, name, tier in rows:
        result.setdefault(str(root), {})[str(name)] = str(tier)
    return result


def write_manual_targets(conn: sqlite3.Connection, store: Mapping[str, Mapping[str, str]]) -> None:
    """작업 폴더별 직접 지정 맵 전체를 한 트랜잭션으로 교체 저장"""
    with transaction(conn):
        _ensure_manual_target_table(conn)
        conn.execute(f"DELETE FROM {MANUAL_TARGET_TABLE_NAME}")
        conn.executemany(_MANUAL_TARGET_INSERT_SQL, _manual_target_rows(store))


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


def count_manual_targets(conn: sqlite3.Connection, root_dirs: Sequence[str] | None = None) -> int:
    """영상별 출력 해상도 직접 지정 건수. root_dirs를 주면 그 작업 폴더 키의 지정만 셈"""
    return len(_manual_target_keys(conn, root_dirs))


def clear_manual_targets(conn: sqlite3.Connection, root_dirs: Sequence[str] | None = None) -> int:
    """영상별 출력 해상도 직접 지정을 정리 범위에서 지우고 지운 건수 반환"""
    keys = _manual_target_keys(conn, root_dirs)
    if not keys:
        return 0
    with transaction(conn):
        conn.executemany(
            f"DELETE FROM {MANUAL_TARGET_TABLE_NAME} WHERE root_key = ? AND filename = ?",
            keys,
        )
    return len(keys)


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
