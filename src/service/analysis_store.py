"""비디오 메타데이터의 영구 분석 캐시(SQLite) 저장소 모듈

- 분석 결과는 내용 행(크기, 내용 지문) 하나로 저장하고, 파일명과 수정 시각은 파일명 조회 키 표에 따로 둠
- 위치 행은 (볼륨 식별자, 볼륨 기준 상대 경로)마다 하나로 두어 폴더를 옮기거나 이름을 바꿔도 같은 결과를 찾음
- 스키마는 VideoProps 필드에서 자동 생성하며 DDL 해시를 PRAGMA user_version에 기록
- 열 때 user_version과 table_info를 대조해 불일치하면 기존 행을 옮겨 현재 스키마로 맞춤
- DB 오류는 경고 로그 후 캐시 없이 계속하도록 이 모듈 안에서 흡수
"""

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from src.core.analysis.file_fingerprint import (
    FINGERPRINT_CHUNK_BYTES as FINGERPRINT_CHUNK_BYTES,
)
from src.core.analysis.file_fingerprint import (
    FINGERPRINT_DIGEST_BYTES as FINGERPRINT_DIGEST_BYTES,
)
from src.core.analysis.file_fingerprint import (
    compute_fingerprint as compute_fingerprint,
)
from src.core.analysis.probe_version_rules import KEYFRAME_MEASURE_VERSION
from src.core.analysis.volume_identity import volume_location
from src.core.models import VideoProps
from src.core.paths import get_data_dir
from src.core.probe_parser import PROBE_VERSION
from src.core.resolution_tiers import copy_manual_targets
from src.core.workspace_paths import (
    LogicalOwnerResolver,
    format_root_dirs,
    normalize_root_key,
    parse_root_dirs,
)
from src.service.analysis_store_schema import (
    ALL_COLUMN_NAMES as ALL_COLUMN_NAMES,
)
from src.service.analysis_store_schema import (
    ALL_COLUMNS as ALL_COLUMNS,
)
from src.service.analysis_store_schema import (
    ANALYSIS_TABLE_NAME as ANALYSIS_TABLE_NAME,
)
from src.service.analysis_store_schema import (
    CONTENT_KEY_COLUMN_NAMES as CONTENT_KEY_COLUMN_NAMES,
)
from src.service.analysis_store_schema import (
    CONTENT_TABLE_SQL as CONTENT_TABLE_SQL,
)
from src.service.analysis_store_schema import (
    LEGACY_CONTENT_INDEX_SQL as LEGACY_CONTENT_INDEX_SQL,
)
from src.service.analysis_store_schema import (
    LEGACY_INDEX_NAME as LEGACY_INDEX_NAME,
)
from src.service.analysis_store_schema import (
    LEGACY_KEY_COLUMN_NAMES as LEGACY_KEY_COLUMN_NAMES,
)
from src.service.analysis_store_schema import (
    LEGACY_TABLE_NAME as LEGACY_TABLE_NAME,
)
from src.service.analysis_store_schema import (
    LEGACY_TABLE_SQL as LEGACY_TABLE_SQL,
)
from src.service.analysis_store_schema import (
    LOCATION_COLUMNS as LOCATION_COLUMNS,
)
from src.service.analysis_store_schema import (
    LOCATION_CONTENT_INDEX_SQL as LOCATION_CONTENT_INDEX_SQL,
)
from src.service.analysis_store_schema import (
    LOCATION_INDEX_NAME as LOCATION_INDEX_NAME,
)
from src.service.analysis_store_schema import (
    LOCATION_KEY_COLUMN_NAMES as LOCATION_KEY_COLUMN_NAMES,
)
from src.service.analysis_store_schema import (
    LOCATION_TABLE_NAME as LOCATION_TABLE_NAME,
)
from src.service.analysis_store_schema import (
    LOCATION_TABLE_SQL as LOCATION_TABLE_SQL,
)
from src.service.analysis_store_schema import (
    PROPS_COLUMNS as PROPS_COLUMNS,
)
from src.service.analysis_store_schema import (
    SCHEMA_VERSION as SCHEMA_VERSION,
)
from src.service.analysis_store_schema import (
    STORED_PROPS_FIELDS as STORED_PROPS_FIELDS,
)
from src.service.analysis_store_schema import (
    ColumnSpec as ColumnSpec,
)
from src.service.analysis_store_schema import (
    ensure_schema as _ensure_schema,
)
from src.service.analysis_store_schema import (
    expected_table_info as expected_table_info,
)
from src.service.analysis_work_records import (
    ManualTargetSync,
    clear_manual_targets,
    clear_table_selections,
    count_manual_targets,
    count_table_selections,
    manual_target_sizes,
    read_manual_targets,
    read_table_selection,
    refresh_manual_target_identities,
    sync_manual_target_designations,
    write_manual_targets,
    write_table_selection,
)
from src.service.designation_content import ContentIdentity
from src.service.sql_transaction import transaction

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

ANALYSIS_DB_FILENAME: str = "video_cache.sqlite3"
SQLITE_BUSY_TIMEOUT_SEC: float = 5.0
LOOKUP_CHUNK_SIZE: int = 500
SQLITE_SIDE_FILE_SUFFIXES: tuple[str, ...] = ("", "-wal", "-shm")

FileKey = tuple[str, int, int]
LocationKey = tuple[str, str]
ContentKey = tuple[int, str]


@dataclass(slots=True)
class StoredEntry:
    """분석 캐시 한 행: 파일 식별 키(이름, 크기, 수정 시각), 내용 지문, 메타데이터"""

    filename: str
    filesize_bytes: int
    mtime_ns: int
    fingerprint: str
    props: VideoProps
    probe_version: int = PROBE_VERSION
    keyframe_version: int = 0
    probe_raw: bytes | None = None
    # - 파일명 조회로 읽은 키의 저장 범위. 같은 이름, 크기, 수정 시각의 키가 작업 폴더마다 따로 있을 수 있어 호출 측이 범위로 고름
    root_scope: str = ""

    @property
    def key(self) -> FileKey:
        """파일 식별 키(이름, 크기, 수정 시각) 반환"""
        return (self.filename, self.filesize_bytes, self.mtime_ns)


@dataclass(frozen=True, slots=True)
class LocationRecord:
    """볼륨 위치 한 곳이 가리키는 내용: 볼륨 식별자, 볼륨 기준 상대 경로, 파일 크기, 수정 시각, 내용 지문"""

    volume_id: str
    volume_path: str
    filesize_bytes: int
    mtime_ns: int
    fingerprint: str


_SELECT_COLUMNS: str = ", ".join(ALL_COLUMN_NAMES)
_PLACEHOLDERS: str = ", ".join("?" * len(ALL_COLUMNS))
_LOOKUP_COLUMN_NAMES: tuple[str, ...] = ("lookup_filename", "lookup_mtime_ns", "lookup_root_scope")


def _lookup_sql(where: str) -> str:
    """파일명 조회 키 표에서 내용 행을 잇는 조회 SQL. 조회 키의 파일명, 수정 시각, 저장 범위를 함께 돌려줌"""
    columns = ", ".join(f"c.{name}" for name in ALL_COLUMN_NAMES)
    return (
        f"SELECT {columns}, k.filename AS lookup_filename, k.mtime_ns AS lookup_mtime_ns, "
        f"k.root_scope AS lookup_root_scope "
        f"FROM {LEGACY_TABLE_NAME} AS k "
        f"JOIN {ANALYSIS_TABLE_NAME} AS c "
        f"ON c.filesize_bytes = k.filesize_bytes AND c.fingerprint = k.fingerprint "
        f"WHERE {where}"
    )


def lookup_by_names_sql(placeholder_count: int) -> str:
    """파일명 IN 조회 SQL. 파일명 조회 키 표의 기본 키 접두사로 검색되어야 함"""
    return _lookup_sql(f"k.filename IN ({', '.join('?' * placeholder_count)})")


def lookup_by_sizes_sql(placeholder_count: int) -> str:
    """파일 크기 IN 조회 SQL. 파일명 조회 키 표의 크기, 지문 인덱스로 검색되어야 함

    - 내용 행을 직접 크기로 찾으면 같은 내용의 파일이 여럿일 때 행 하나가 여러 파일에 쓰이지 못하므로,
      저장된 파일명 키마다 한 행을 돌려주어 이름을 바꾼 파일끼리 하나씩 짝을 이루게 함
    """
    return _lookup_sql(f"k.filesize_bytes IN ({', '.join('?' * placeholder_count)})")


def lookup_by_contents_sql(placeholder_count: int) -> str:
    """내용 키(크기, 지문) IN 조회 SQL. 내용 행 기본 키로 검색되어야 함"""
    pairs = ", ".join("(?, ?)" for _ in range(placeholder_count))
    return f"SELECT {_SELECT_COLUMNS} FROM {ANALYSIS_TABLE_NAME} WHERE ({', '.join(CONTENT_KEY_COLUMN_NAMES)}) IN ({pairs})"


def _row_to_entry(row: sqlite3.Row | tuple[Any, ...], columns: Sequence[str]) -> StoredEntry:
    """조회 행을 StoredEntry로 바꿈. 파일명 조회 결과는 조회 키 표의 파일명과 수정 시각을 씀"""
    values = dict(zip(columns, row, strict=True))
    filename = str(values["lookup_filename"]) if "lookup_filename" in values else str(values["filename"])
    mtime_ns = int(values["lookup_mtime_ns"]) if "lookup_mtime_ns" in values else int(values["mtime_ns"])
    prop_kwargs: dict[str, Any] = {}
    for col in PROPS_COLUMNS:
        value = values[col.name]
        prop_kwargs[col.name] = bool(value) if col.py_type is bool and value is not None else value
    return StoredEntry(
        filename=filename,
        filesize_bytes=int(values["filesize_bytes"]),
        mtime_ns=mtime_ns,
        fingerprint=str(values["fingerprint"]),
        props=VideoProps(filename=filename, **prop_kwargs),
        probe_version=int(values["probe_version"]),
        keyframe_version=int(values["keyframe_version"]),
        probe_raw=bytes(values["probe_raw"]) if values["probe_raw"] is not None else None,
        root_scope=str(values["lookup_root_scope"]) if "lookup_root_scope" in values else "",
    )


def _entry_to_row(entry: StoredEntry) -> tuple[Any, ...]:
    """내용 행에 넣을 값을 열 순서대로 만듬. 파일명과 수정 시각은 표시, 조회용 대표 값"""
    values: dict[str, Any] = {
        "filename": entry.filename,
        "filesize_bytes": entry.filesize_bytes,
        "mtime_ns": entry.mtime_ns,
        "fingerprint": entry.fingerprint,
        "probe_version": PROBE_VERSION,
        "keyframe_version": KEYFRAME_MEASURE_VERSION if entry.props.keyframe_interval is not None else 0,
        "probe_raw": entry.probe_raw,
    }
    for col in PROPS_COLUMNS:
        values[col.name] = getattr(entry.props, col.name)
    return tuple(values[name] for name in ALL_COLUMN_NAMES)


def scope_folders(root_spec: str | Sequence[str] | None) -> frozenset[str]:
    """작업 폴더 범위를 정규화한 절대 경로 집합으로 바꿈

    - 상대 경로와 절대 경로, 구분자, 대소문자 차이를 없애 같은 폴더가 같은 값이 되게 함
    """
    if not root_spec:
        return frozenset()
    return frozenset(os.path.normcase(os.path.abspath(path)) for path in parse_root_dirs(root_spec))


def root_scope_key(root_dir: str | Sequence[str] | None) -> str:
    """조회 키 행에 남길 분석 범위 키(정렬한 정규화 절대 경로 목록), 범위를 모르면 빈 문자열

    - 폴더 순서가 달라도, 상대 경로와 절대 경로가 섞여도 같은 범위가 같은 문자열이 되게 함
    """
    return format_root_dirs(sorted(scope_folders(root_dir)))


def _scope_entries(root_spec: str) -> frozenset[tuple[tuple[str, ...], bool]]:
    """범위 문자열의 폴더별 (경로 구성 요소, 상대 경로 여부) 집합

    - 절대 경로는 정규화한 경로의 구성 요소를, 옛 상대 경로 표기는 문자열 그대로의 구성 요소를 씀
    - 상대 경로 표기는 지금 해석하면 현재 폴더 기준이 되므로 저장 당시 표기를 그대로 비교에 씀
    """
    entries: set[tuple[tuple[str, ...], bool]] = set()
    for path in parse_root_dirs(root_spec):
        relative = not os.path.isabs(path)
        normalized = os.path.normcase(os.path.normpath(path)) if relative else os.path.normcase(
            os.path.abspath(path)
        )
        parts = tuple(part for part in normalized.replace(os.sep, "/").split("/") if part)
        entries.add((parts, relative))
    return frozenset(entries)


def _folder_matches(stored: tuple[tuple[str, ...], bool], current: tuple[tuple[str, ...], bool]) -> bool:
    """저장 폴더 표기가 현재 폴더와 같은 곳인지

    - 절대 경로 표기는 정규화한 경로가 같아야 함
    - 옛 상대 경로 표기는 그때의 셸 작업 폴더를 알 수 없으므로 경로 끝 구성 요소로 비교(최선 노력)
    """
    stored_parts, stored_relative = stored
    current_parts, current_relative = current
    if stored_parts == current_parts:
        return True
    if not (stored_relative or current_relative) or not stored_parts or not current_parts:
        return False
    depth = min(len(stored_parts), len(current_parts))
    return stored_parts[-depth:] == current_parts[-depth:]


def scope_matches(stored_scope: str, scope: str) -> bool:
    """저장 범위 행이 지금 범위의 작업 폴더를 하나라도 포함하는지

    - 폴더 순서가 다른 옛 표기나 상대 경로로 저장한 옛 표기도 폴더 단위로 비교해 함께 찾음
    - 여러 폴더를 함께 훑어 저장한 범위는 그중 한 폴더만 지정해도 그 폴더의 파일이므로 함께 봄
    - 범위가 빈 저장 행은 어느 작업 폴더의 키도 아니므로 여기서 맞지 않는다고 봄
    """
    if not stored_scope or not scope:
        return False
    current = _scope_entries(scope)
    return any(
        _folder_matches(stored, folder) for stored in _scope_entries(stored_scope) for folder in current
    )



def _entry_to_legacy_row(entry: StoredEntry, scope: str) -> tuple[Any, ...]:
    """파일명 조회 키 행에 넣을 (파일명, 크기, 수정 시각, 내용 지문, 분석 범위)"""
    return (entry.filename, entry.filesize_bytes, entry.mtime_ns, entry.fingerprint, scope)


def _entry_to_legacy_prune(entry: StoredEntry, scope: str) -> tuple[Any, ...]:
    """같은 범위의 같은 파일명에 남은 옛 조회 키(교체 전 파일)를 지울 때 쓰는 (분석 범위, 파일명, 크기, 수정 시각)"""
    return (scope, entry.filename, entry.filesize_bytes, entry.mtime_ns)


def _entry_to_migrated_prune(entry: StoredEntry) -> tuple[Any, ...]:
    """같은 파일의 마이그레이션 전 빈 범위 키를 지울 때 쓰는 (파일명, 크기, 수정 시각)"""
    return (entry.filename, entry.filesize_bytes, entry.mtime_ns)


def _location_to_row(record: LocationRecord) -> tuple[Any, ...]:
    """위치 행에 넣을 값을 열 순서대로 만듬"""
    return (record.volume_id, record.volume_path, record.filesize_bytes, record.mtime_ns, record.fingerprint)


def _row_to_location(row: sqlite3.Row | tuple[Any, ...]) -> LocationRecord:
    """위치 행을 LocationRecord로 바꿈"""
    volume_id, volume_path, filesize_bytes, mtime_ns, fingerprint = row
    return LocationRecord(str(volume_id), str(volume_path), int(filesize_bytes), int(mtime_ns), str(fingerprint))


CONTENT_UPSERT_SQL: str = (
    f"INSERT INTO {ANALYSIS_TABLE_NAME} ({_SELECT_COLUMNS}) VALUES ({_PLACEHOLDERS}) "
    f"ON CONFLICT ({', '.join(CONTENT_KEY_COLUMN_NAMES)}) DO UPDATE SET "
    + ", ".join(
        [
            # - 키프레임 없이 다시 분석한 결과가 저장된 키프레임 값을 지우지 않도록 최신 측정 값만 덮어씀
            f"keyframe_version = MAX(excluded.keyframe_version, {ANALYSIS_TABLE_NAME}.keyframe_version)",
            f"keyframe_interval = CASE WHEN excluded.keyframe_version >= {ANALYSIS_TABLE_NAME}.keyframe_version "
            f"THEN excluded.keyframe_interval ELSE {ANALYSIS_TABLE_NAME}.keyframe_interval END",
        ]
        + [
            f"{name} = excluded.{name}"
            for name in ALL_COLUMN_NAMES
            if name not in CONTENT_KEY_COLUMN_NAMES and name not in ("keyframe_version", "keyframe_interval")
        ]
    )
    # - 옛 측정 번호로 새 측정 결과를 되돌리지 않도록 같거나 새 측정일 때만 덮어씀
    + f" WHERE excluded.probe_version >= {ANALYSIS_TABLE_NAME}.probe_version"
)
LEGACY_UPSERT_SQL: str = (
    f"INSERT OR REPLACE INTO {LEGACY_TABLE_NAME} (filename, filesize_bytes, mtime_ns, fingerprint, root_scope) "
    f"VALUES (?, ?, ?, ?, ?)"
)
DELETE_LEGACY_KEY_SQL: str = (
    f"DELETE FROM {LEGACY_TABLE_NAME} "
    f"WHERE filename = ? AND filesize_bytes = ? AND mtime_ns = ? AND root_scope = ?"
)
# - 같은 범위(같은 작업 폴더 목록)의 같은 경로에 다른 파일이 놓이면 그 이름의 옛 키는 지금 파일을 가리키지 않으므로 남기지 않음
# - 범위를 함께 보아 다른 작업 폴더가 저장한 같은 이름의 키는 지우지 않음(단일 작업 폴더는 접두사 없는 이름이 겹칠 수 있음)
LEGACY_PRUNE_SQL: str = (
    f"DELETE FROM {LEGACY_TABLE_NAME} WHERE root_scope = ? AND filename = ? "
    f"AND (filesize_bytes != ? OR mtime_ns != ?)"
)
# - 같은 파일을 마이그레이션 전에 저장한 빈 범위 키는 범위가 없어 프루닝에 걸리지 않으므로, 범위를 아는 저장 때 같은 파일의 키를 함께 지움
# - 범위를 모르는 저장(빈 범위)과 파일명만 같은 다른 파일의 빈 범위 키는 남김
MIGRATED_KEY_PRUNE_SQL: str = (
    f"DELETE FROM {LEGACY_TABLE_NAME} WHERE root_scope = '' AND filename = ? "
    f"AND filesize_bytes = ? AND mtime_ns = ?"
)
LOCATION_UPSERT_SQL: str = (
    f"INSERT OR REPLACE INTO {LOCATION_TABLE_NAME} "
    f"(volume_id, volume_path, filesize_bytes, mtime_ns, fingerprint) VALUES (?, ?, ?, ?, ?)"
)


def _chunks(items: Sequence[_T], size: int) -> Iterable[Sequence[_T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _probe_scope_rows(
    conn: sqlite3.Connection, root_dirs: Sequence[str] | None
) -> tuple[list[tuple[FileKey, str, str]], list[LocationRecord]]:
    """정리 범위의 파일명 조회 키 행과 위치 행 목록(위치는 작업 폴더의 볼륨 접두사로 소속을 판정)

    - 키 행은 저장 범위 문자열까지 함께 돌려주어 같은 이름의 다른 범위 행을 지우지 않게 함
    - 저장 범위가 있는 키는 그 범위로 소속을 판정해, 다른 작업 폴더의 같은 이름 키를 이 범위로 세지 않음
    """
    scope = "" if root_dirs is None else root_scope_key(root_dirs)
    owner = None if root_dirs is None else LogicalOwnerResolver(root_dirs)
    legacy = [
        ((str(name), int(size), int(mtime)), str(fingerprint), str(stored_scope))
        for name, size, mtime, fingerprint, stored_scope in conn.execute(
            f"SELECT filename, filesize_bytes, mtime_ns, fingerprint, root_scope FROM {LEGACY_TABLE_NAME}"
        )
        if owner is None or _key_in_scope(str(name), str(stored_scope), scope, owner)
    ]
    columns = ", ".join(col.name for col in LOCATION_COLUMNS)
    locations = [_row_to_location(row) for row in conn.execute(f"SELECT {columns} FROM {LOCATION_TABLE_NAME}")]
    if root_dirs is None:
        return legacy, locations
    prefixes = [(location.volume_id, location.volume_path) for location in map(volume_location, root_dirs)]
    return legacy, [record for record in locations if _is_location_in_scope(record, prefixes)]


def _key_in_scope(name: str, stored_scope: str, scope: str, owner: LogicalOwnerResolver) -> bool:
    """조회 키 행이 정리 범위에 드는지. 저장 범위가 있으면 그 범위로, 옛 빈 범위 행은 폴더 내용으로 판정"""
    if stored_scope:
        return scope_matches(stored_scope, scope)
    return owner.owner(name) is not None


def _is_location_in_scope(record: LocationRecord, prefixes: Sequence[LocationKey]) -> bool:
    """볼륨 위치가 작업 폴더 접두사 안인지 판정(이름 일부만 겹치는 경우 제외)"""
    return any(
        record.volume_id == volume_id
        and (not prefix or record.volume_path == prefix or record.volume_path.startswith(prefix + "/"))
        for volume_id, prefix in prefixes
    )


def _probe_cache_count(legacy: Sequence[tuple[FileKey, str, str]], locations: Sequence[LocationRecord]) -> int:
    """정리 범위의 분석 캐시 행 수(파일명 키 행과, 파일명 키로 찾을 수 없는 위치 행을 합침)"""
    covered = {(size, fingerprint) for (_name, size, _mtime), fingerprint, _scope in legacy}
    return len(legacy) + sum(1 for record in locations if (record.filesize_bytes, record.fingerprint) not in covered)


def _delete_scoped_keys(conn: sqlite3.Connection, delete_keys: Sequence[FileKey], scope: str) -> None:
    """파일 식별 키의 조회 키 행 중 주어진 범위의 행과 범위를 모르는 같은 파일 행만 지움

    - 같은 이름, 크기, 수정 시각의 키가 다른 작업 폴더에도 있을 수 있으므로 저장 범위를 함께 확인
    - 범위를 모르는 옛 행(빈 범위)은 같은 파일이면 함께 지움. 그 밖의 다른 범위 행은 남김
    - 범위를 주지 않은 호출은 언제나 범위가 빈 같은 파일 행만 지움
    """
    if not delete_keys:
        return
    for chunk in _chunks(delete_keys, LOOKUP_CHUNK_SIZE):
        params = [value for key in chunk for value in key]
        marks = ", ".join("(?, ?, ?)" for _ in chunk)
        rows = conn.execute(
            f"SELECT filename, filesize_bytes, mtime_ns, root_scope FROM {LEGACY_TABLE_NAME} "
            f"WHERE (filename, filesize_bytes, mtime_ns) IN ({marks})",
            params,
        ).fetchall()
        doomed = [
            (str(name), int(size), int(mtime), str(stored))
            for name, size, mtime, stored in rows
            if stored == "" or (scope and scope_matches(str(stored), scope))
        ]
        conn.executemany(DELETE_LEGACY_KEY_SQL, doomed)


def _delete_location_keys(conn: sqlite3.Connection, keys: Sequence[LocationKey]) -> None:
    """위치 키에 해당하는 위치 행을 지움(호출자가 트랜잭션을 연다)"""
    for chunk in _chunks(keys, LOOKUP_CHUNK_SIZE):
        row_placeholders = ", ".join("(?, ?)" for _ in chunk)
        params = [value for key in chunk for value in key]
        conn.execute(
            f"DELETE FROM {LOCATION_TABLE_NAME} WHERE (volume_id, volume_path) IN ({row_placeholders})",
            params,
        )


def _transactional_delete_locations(conn: sqlite3.Connection, keys: Sequence[LocationKey]) -> None:
    """위치 행 삭제를 한 트랜잭션으로 처리"""
    with transaction(conn):
        _delete_location_keys(conn, keys)


ORPHAN_CONTENT_ROWS_SQL: str = (
    f"DELETE FROM {ANALYSIS_TABLE_NAME} WHERE NOT EXISTS ("
    f"SELECT 1 FROM {LEGACY_TABLE_NAME} AS k "
    f"WHERE k.filesize_bytes = {ANALYSIS_TABLE_NAME}.filesize_bytes "
    f"AND k.fingerprint = {ANALYSIS_TABLE_NAME}.fingerprint) "
    # - 위치 행만 남은 내용도 다시 찾을 수 있으므로 함께 살핌(옛 이름 키를 지운 뒤에도 같은 파일이 재프로빙되지 않게)
    f"AND NOT EXISTS (SELECT 1 FROM {LOCATION_TABLE_NAME} AS l "
    f"WHERE l.filesize_bytes = {ANALYSIS_TABLE_NAME}.filesize_bytes "
    f"AND l.fingerprint = {ANALYSIS_TABLE_NAME}.fingerprint)"
)


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
        """파일명 기준 일치 행을 조회하며 모든 측정 버전의 행을 반환(재사용 판정은 probe_version_rules에서)"""
        names = list(dict.fromkeys(filenames))
        columns = (*ALL_COLUMN_NAMES, *_LOOKUP_COLUMN_NAMES)

        def _query(conn: sqlite3.Connection) -> list[StoredEntry]:
            found: list[StoredEntry] = []
            for chunk in _chunks(names, LOOKUP_CHUNK_SIZE):
                cursor = conn.execute(lookup_by_names_sql(len(chunk)), list(chunk))
                found.extend(_row_to_entry(row, columns) for row in cursor.fetchall())
            return found

        if not names:
            return []
        return self._run(_query, [])

    def fetch_by_sizes(self, sizes: Sequence[int]) -> list[StoredEntry]:
        """파일 크기 기준 일치 행을 조회하며 모든 측정 버전의 행을 반환(재사용 판정은 probe_version_rules에서)"""
        unique_sizes = list(dict.fromkeys(sizes))
        columns = (*ALL_COLUMN_NAMES, *_LOOKUP_COLUMN_NAMES)

        def _query(conn: sqlite3.Connection) -> list[StoredEntry]:
            found: list[StoredEntry] = []
            for chunk in _chunks(unique_sizes, LOOKUP_CHUNK_SIZE):
                cursor = conn.execute(lookup_by_sizes_sql(len(chunk)), list(chunk))
                found.extend(_row_to_entry(row, columns) for row in cursor.fetchall())
            return found

        if not unique_sizes:
            return []
        return self._run(_query, [])

    def fetch_by_contents(self, keys: Sequence[ContentKey]) -> list[StoredEntry]:
        """내용 키(크기, 내용 지문)에 해당하는 내용 행을 조회(없는 키는 결과에서 빠짐)

        - 위치 행이 가리키는 내용을 찾을 때 씀. 파일명과 수정 시각은 대표 값이므로 호출자가 현재 파일 값으로 바꿔 씀
        """
        unique = list(dict.fromkeys(keys))

        def _query(conn: sqlite3.Connection) -> list[StoredEntry]:
            found: list[StoredEntry] = []
            for chunk in _chunks(unique, LOOKUP_CHUNK_SIZE):
                params = [value for key in chunk for value in key]
                cursor = conn.execute(lookup_by_contents_sql(len(chunk)), params)
                found.extend(_row_to_entry(row, ALL_COLUMN_NAMES) for row in cursor.fetchall())
            return found

        if not unique:
            return []
        return self._run(_query, [])

    def fetch_locations(self, keys: Sequence[LocationKey]) -> list[LocationRecord]:
        """볼륨 위치 키에 해당하는 위치 행을 조회(없는 키는 결과에서 빠짐)"""
        unique = list(dict.fromkeys(keys))
        columns = ", ".join(col.name for col in LOCATION_COLUMNS)

        def _query(conn: sqlite3.Connection) -> list[LocationRecord]:
            found: list[LocationRecord] = []
            for chunk in _chunks(unique, LOOKUP_CHUNK_SIZE):
                row_placeholders = ", ".join("(?, ?)" for _ in chunk)
                params = [value for key in chunk for value in key]
                rows = conn.execute(
                    f"SELECT {columns} FROM {LOCATION_TABLE_NAME} "
                    f"WHERE (volume_id, volume_path) IN ({row_placeholders})",
                    params,
                ).fetchall()
                found.extend(_row_to_location(row) for row in rows)
            return found

        if not unique:
            return []
        return self._run(_query, [])

    def record_locations(self, records: Sequence[LocationRecord]) -> None:
        """파일이 확인된 위치를 저장하며, 같은 경로의 행은 현재 크기, 수정 시각, 내용 지문으로 바꿈"""
        if not records:
            return
        rows = [_location_to_row(record) for record in records]

        def _write(conn: sqlite3.Connection) -> None:
            with transaction(conn):
                conn.executemany(LOCATION_UPSERT_SQL, rows)

        self._run(_write, None)

    def remove_locations(self, keys: Sequence[LocationKey]) -> None:
        """지정한 위치 키의 위치 행을 지움(그 위치에 다른 파일이 놓였거나 파일이 옮겨진 경우)"""
        unique = list(dict.fromkeys(keys))
        if not unique:
            return
        self._run(lambda conn: _transactional_delete_locations(conn, unique), None)

    def apply(
        self, delete_keys: Sequence[FileKey], entries: Sequence[StoredEntry], root_dir: str | None = None
    ) -> None:
        """지정 파일명 키를 지우고 항목을 내용 행과 조회 키 행에 저장(같은 내용은 최신 측정 값만 유지)

        - root_dir은 이번 저장이 분석한 작업 폴더 범위. 같은 이름의 옛 키는 이 범위 안에서만 지움
        - 범위를 주지 않으면(범위를 모르는 저장, 인코딩 후 삭제) 같은 이름의 다른 범위 키를 지우지 않음
        """
        if not delete_keys and not entries:
            return
        scope = root_scope_key(root_dir)
        rows = [_entry_to_row(entry) for entry in entries]
        key_rows = [_entry_to_legacy_row(entry, scope) for entry in entries]
        prune_rows = [_entry_to_legacy_prune(entry, scope) for entry in entries]
        migrated_rows = [_entry_to_migrated_prune(entry) for entry in entries] if scope else []

        def _write(conn: sqlite3.Connection) -> None:
            with transaction(conn):
                _delete_scoped_keys(conn, delete_keys, scope)
                conn.executemany(CONTENT_UPSERT_SQL, rows)
                conn.executemany(LEGACY_PRUNE_SQL, prune_rows)
                conn.executemany(MIGRATED_KEY_PRUNE_SQL, migrated_rows)
                conn.executemany(LEGACY_UPSERT_SQL, key_rows)
                # - 조회 키가 하나도 남지 않은 내용 행은 다시 찾을 수 없으므로 지움(이름을 바꾸거나 지운 파일)
                conn.execute(ORPHAN_CONTENT_ROWS_SQL)

        self._run(_write, None)

    def count(self) -> int:
        """저장된 파일명 조회 키 수 반환(파일마다 한 행, 같은 내용이 여러 위치에 있으면 각각 셈)"""
        return self._run(
            lambda conn: int(conn.execute(f"SELECT COUNT(*) FROM {LEGACY_TABLE_NAME}").fetchone()[0]),
            0,
        )

    def count_contents(self) -> int:
        """저장된 내용 행 수 반환(내용이 같으면 위치나 이름이 달라도 하나)"""
        return self._run(
            lambda conn: int(conn.execute(f"SELECT COUNT(*) FROM {ANALYSIS_TABLE_NAME}").fetchone()[0]),
            0,
        )

    def clear(self) -> int:
        """분석 결과 행, 파일명 조회 키, 위치 행을 모두 삭제하고 지운 캐시 행 수 반환"""

        def _delete(conn: sqlite3.Connection) -> int:
            with transaction(conn):
                legacy, locations = _probe_scope_rows(conn, None)
                conn.execute(f"DELETE FROM {ANALYSIS_TABLE_NAME}")
                conn.execute(f"DELETE FROM {LEGACY_TABLE_NAME}")
                conn.execute(f"DELETE FROM {LOCATION_TABLE_NAME}")
            return _probe_cache_count(legacy, locations)

        return self._run(_delete, 0)

    def count_for_roots(self, root_dirs: Sequence[str] | None = None) -> int:
        """영상 분석 캐시 행 수 반환(root_dirs를 주면 그 작업 폴더 소속만). 파일명 키가 없는 위치 행도 셈"""
        return self._run(lambda conn: _probe_cache_count(*_probe_scope_rows(conn, root_dirs)), 0)

    def clear_for_roots(self, root_dirs: Sequence[str] | None = None) -> int:
        """영상 분석 캐시 행과 위치 행을 지우고 지운 행 수 반환(root_dirs를 주면 그 작업 폴더 소속만)

        - 같은 이름, 크기, 수정 시각의 키가 다른 작업 폴더에도 있으므로 저장 범위까지 함께 맞춰 지움
        """
        legacy, locations = self._run(lambda conn: _probe_scope_rows(conn, root_dirs), ([], []))
        if not legacy and not locations:
            return 0
        keys = [(*key, scope) for key, _fingerprint, scope in legacy]
        location_keys = [(record.volume_id, record.volume_path) for record in locations]

        def _delete(conn: sqlite3.Connection) -> int:
            with transaction(conn):
                conn.executemany(DELETE_LEGACY_KEY_SQL, keys)
                _delete_location_keys(conn, location_keys)
                conn.execute(ORPHAN_CONTENT_ROWS_SQL)
            return _probe_cache_count(legacy, locations)

        return self._run(_delete, 0)

    def count_manual_targets(self, root_dirs: Sequence[str] | None = None) -> int:
        """영상별 출력 해상도 직접 지정 건수. root_dirs를 주면 그 작업 폴더 키의 지정만 셈"""
        return self._run(lambda conn: count_manual_targets(conn, root_dirs), 0)

    def manual_target_sizes(self) -> set[int]:
        """출력 해상도 직접 지정 행이 남긴 파일 크기 집합(스캔 지문 대조 범위 축소용)"""
        return self._run_isolated(manual_target_sizes, set())

    def sync_manual_target_designations(
        self, found: Mapping[str, ContentIdentity], root_dir: str
    ) -> ManualTargetSync:
        """스캔이 확인한 내용 키로 출력 해상도 직접 지정을 새 경로(다른 작업 폴더 포함)에 붙임"""
        return self._run_isolated(
            lambda conn: sync_manual_target_designations(conn, found=found, root_spec=root_dir),
            ManualTargetSync(),
        )

    def clear_manual_targets(self, root_dirs: Sequence[str] | None = None) -> int:
        """영상별 출력 해상도 직접 지정을 지우고 지운 건수 반환"""
        return self._run(lambda conn: clear_manual_targets(conn, root_dirs), 0)

    def count_table_selections(self, root_dirs: Sequence[str] | None = None) -> int:
        """표 선택 기억(작업 폴더별 선택 파일명) 건수. root_dirs를 주면 그 작업 폴더 키만 셈"""
        return self._run(lambda conn: count_table_selections(conn, root_dirs), 0)

    def clear_table_selections(self, root_dirs: Sequence[str] | None = None) -> int:
        """표 선택 기억을 지우고 지운 건수 반환"""
        return self._run(lambda conn: clear_table_selections(conn, root_dirs), 0)

    def read_manual_targets(
        self, legacy: Mapping[str, Mapping[str, str]]
    ) -> dict[str, dict[str, str]]:
        """작업 폴더별 출력 해상도 직접 지정 맵을 전용 테이블에서 읽음

        - 테이블을 처음 만드는 때만 설정 파일에 남은 옛 맵(legacy)을 옮김
        - DB를 쓸 수 없으면 legacy 사본을 반환해 현재 세션의 지정이 사라지지 않게 함
        """
        fallback = copy_manual_targets(legacy)
        return self._run(lambda conn: read_manual_targets(conn, fallback), fallback)

    def write_manual_targets(self, store: Mapping[str, Mapping[str, str]]) -> None:
        """작업 폴더별 직접 지정 맵 전체를 전용 테이블에 한 트랜잭션으로 교체 저장"""
        self._run(lambda conn: write_manual_targets(conn, store), None)

    def refresh_manual_target_identities(
        self, root_dir: str, filenames: Iterable[str]
    ) -> int:
        """지정 행의 내용 키를 지금 파일에서 다시 읽어 채움. 채운 행 수 반환

        - 사용자가 그 영상에 지정을 새로 고를 때 불러, 같은 경로의 내용이 바뀐 뒤에도 새 지정이 붙게 함
        - 파일을 읽지 못하면 저장 값을 그대로 두어 다음 스캔이 그 내용의 새 경로를 찾게 함
        """
        key = normalize_root_key(root_dir)
        targets = [(key, name) for name in dict.fromkeys(filenames) if name]
        if not targets:
            return 0
        return self._run_isolated(
            lambda conn: refresh_manual_target_identities(conn, targets), 0
        )

    def read_table_selection(self, root_key: str) -> set[str]:
        """작업 폴더에 마지막으로 선택되어 있던 영상 파일명 집합을 전용 테이블에서 읽음"""
        if not root_key:
            return set()
        return self._run(lambda conn: read_table_selection(conn, root_key), set())

    def write_table_selection(self, root_key: str, filenames: Iterable[str]) -> None:
        """작업 폴더에 선택된 영상 파일명 집합을 전용 테이블에 한 트랜잭션으로 교체 저장"""
        if not root_key:
            return
        names = list(dict.fromkeys(filenames))
        self._run(lambda conn: write_table_selection(conn, root_key, names), None)

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

    def _run_isolated(self, action: Callable[[sqlite3.Connection], _T], default: _T) -> _T:
        """이번 호출 전용 연결로 작업하고 바로 닫음

        - 판정, 스캔처럼 한 번 부르는 조회에 오래 사는 연결을 쓰면 DB 파일 핸들이 남아
          작업 폴더 정리와 테스트 정리 단계를 막음
        - 연결 실패는 종전과 같이 경고만 남기고 기본값을 돌려줌(저장소 자체는 비활성화하지 않음)
        """
        with self._lock:
            if self._disabled:
                return default
            try:
                conn = self._open_with_recovery()
            except (sqlite3.Error, OSError) as e:
                logger.warning("분석 캐시 DB 작업에 실패해 캐시 없이 계속합니다: %s", e)
                return default
            try:
                return action(conn)
            except (sqlite3.Error, OSError) as e:
                logger.warning("분석 캐시 DB 작업에 실패해 캐시 없이 계속합니다: %s", e)
                return default
            finally:
                conn.close()

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
            _ensure_schema(conn, log=logger)
        except BaseException:
            conn.close()
            raise
        return conn


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

