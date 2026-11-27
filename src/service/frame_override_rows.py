"""경로별 직접 지정 행(사용자 지정 활성 화면, 잘라내기 제외)의 파일 식별 검증 모듈

- 지정은 경로 하나로만 묶이므로, 같은 경로의 영상이 바뀌면 저장 값을 그대로 쓰면 안 됨
- 분석 캐시와 같은 파일 식별 값(크기, 수정 시각)을 함께 남기고 읽을 때 지금 파일과 비교
- 예전 스키마 행은 식별 값이 NULL이라 처음 읽을 때 지금 값으로 채워 이후 비교에 씀
- 식별 값이 지금 파일과 다른 행은 지우지 않고 그 조회에서만 건너뜀. 원본 교체 인코딩 뒤에는 경로의
  파일이 결과이고 보관 원본을 띄우는 비교가 그 행을 쓰며, 남은 행은 적용되지 않다가 다시 지정하면 덮임
- 다른 파일을 화면에 띄우는 조회는 그 파일의 식별 값을 지정해 맞는 행만 쓰고, 경로 소유자의 행은
  지우거나 채우지 않음
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Mapping, Sequence

from src.core.analysis.frame_analysis import ActiveRegion, normalize_manual_active_frame
from src.core.models import ActiveFrame

logger = logging.getLogger(__name__)

# - 저장해 둔 식별 값을 채울 때 쓰는 UPDATE 문 열 이름
IDENTITY_COLUMNS: tuple[str, str] = ("filesize_bytes", "mtime_ns")
# - 조회 때 읽은 행일 때만 지우도록 비교할 열. 값 순서는 `validated_manual_rows`가 돌려주는
#   지울 행의 값 순서와 같음
MANUAL_FRAME_MATCH_COLUMNS: tuple[str, ...] = (
    "source_path",
    "manual_width",
    "manual_height",
    "manual_x",
    "manual_y",
    "filesize_bytes",
    "mtime_ns",
)
# - 여백 확인 표시에서 지울 행을 고를 때 비교할 열. 값 순서는 `validated_review_ack_rows`가 돌려주는 값과 같음
REVIEW_ACK_MATCH_COLUMNS: tuple[str, ...] = (
    "source_path",
    "region_width",
    "region_height",
    "region_x",
    "region_y",
    "filesize_bytes",
    "mtime_ns",
)


def add_missing_columns(
    conn: sqlite3.Connection, additions: Sequence[tuple[str, str, str]]
) -> None:
    """이전 DB의 지정 테이블에 없는 열을 덧붙임

    - `CREATE TABLE IF NOT EXISTS`는 기존 테이블 구조를 바꾸지 않으므로 열을 따로 확인
    - 기존 행은 기본값이나 NULL을 받아 읽기가 그대로 성공함
    - 확인과 ALTER 사이에 다른 스레드가 같은 열을 먼저 만들면 "duplicate column name"이 나므로
      그 경우만 넘어가고 나머지 오류는 그대로 올림
    """
    present: dict[str, set[str]] = {}
    for table, name, definition in additions:
        columns = present.get(table)
        if columns is None:
            columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
            present[table] = columns
        if name not in columns:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            except sqlite3.OperationalError as error:
                if "duplicate column name" not in str(error).lower():
                    raise
            columns.add(name)


def file_identity(path: str) -> tuple[int, int] | None:
    """파일의 지금 크기와 수정 시각. 읽지 못하면 None"""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def identity_check(
    path: str,
    stored_size: int | None,
    stored_mtime: int | None,
    identity_override: tuple[int, int] | None = None,
) -> tuple[bool, tuple[int, int, str] | None, bool]:
    """저장된 파일 식별 값이 지금 파일과 다른지, 지금 값을 채워야 하는지 판정

    - 반환: 파일이 바뀌었는지, (크기, 수정 시각, 경로) 채울 값(채울 필요 없으면 None),
      지금 파일을 읽었는지
    - `identity_override`는 경로의 파일 대신 화면에 띄우는 다른 파일의 (크기, 수정 시각). 보관 원본을
      띄우는 비교처럼 결과 파일 경로의 지정을 그 파일과 맞춰 볼 때 씀
    - 두 값이 모두 없는 예전 행은 비교할 수 없으므로 바뀌지 않은 것으로 보고 지금 값을 채움
    - 한 값만 있는 행은 어느 파일과도 맞춰 볼 수 없어 바뀐 것으로 봄(채우는 조건과 같은 기준)
    - 지금 파일을 읽지 못하면 비교할 수 없으므로 바뀌지 않은 것으로 보고 읽지 못했음을 알림
    - 읽지 못한 경우는 호출자가 프레임 크기 검증과 식별 값 채우기를 건너뛸 수 있도록 따로 알림
    """
    identity = file_identity(path) if identity_override is None else identity_override
    if identity is None:
        return False, None, False
    if stored_size is None and stored_mtime is None:
        return False, (identity[0], identity[1], path), True
    if stored_size is None or stored_mtime is None:
        return True, None, True
    return (stored_size, stored_mtime) != identity, None, True


def optional_int(value: object) -> int | None:
    """SQLite 행의 NULL 허용 정수 값을 `int | None`으로 읽음"""
    return None if value is None else int(str(value))


def row_int(value: object) -> int:
    """SQLite 행의 정수 값을 `int`로 읽음"""
    return int(str(value))


def validated_manual_rows(
    rows: Sequence[Sequence[object]],
    frame_sizes: Mapping[str, tuple[int, int]],
    *,
    identity_overrides: Mapping[str, tuple[int, int]] | None = None,
) -> tuple[dict[str, ActiveFrame], list[Sequence[object]], list[tuple[int, int, str]]]:
    """저장된 사용자 지정 활성 화면 행을 지금 파일 기준으로 걸러 냄

    - 반환: 남길 행의 경로별 활성 화면, 지울 행(조회 때 읽은 값 그대로), 지금 식별 값을 채울
      (크기, 수정 시각, 경로)
    - 지울 행을 경로 대신 값 그대로 돌려주는 이유: 조회와 정리 사이에 같은 경로로 새 지정이
      저장되면 경로만 보고 지울 때 그 새 행을 잃음. 정리는 값이 그대로인 행에만 적용
    - 저장 시점 식별 값이 지금 파일과 다르면 그 조회에서만 건너뛰고 행은 지우지 않음. 원본 교체
      인코딩 뒤에는 경로의 파일이 결과이고 보관 원본을 띄우는 비교가 이 행을 씀
    - `identity_overrides`에 경로가 있으면 다른 파일을 띄우는 조회이므로 행 소유 파일 대신 그 식별
      값과 맞춰 봄. 맞지 않는 행은 결과 파일 경로의 지정일 수 있어 지우지도, 식별 값을 채우지도 않고
      이번 조회에서만 건너뜀
    - 지금 프레임 크기로 `normalize_manual_active_frame`을 다시 적용해 벗어난 사각형을 버림
    - 지금 프레임 크기를 모르면(가로나 세로가 0 이하) 비교할 수 없으므로 저장 값을 그대로 둠
    - 파일을 읽지 못하면 지금 상태를 알 수 없으므로 행을 그대로 두고 식별 값도 채우지 않음
    """
    frames: dict[str, ActiveFrame] = {}
    dropped: list[Sequence[object]] = []
    stamped: list[tuple[int, int, str]] = []
    for row in rows:
        path = str(row[0])
        override = None if identity_overrides is None else identity_overrides.get(path)
        # - 다른 파일용 조회는 행 소유 경로의 파일과 맞춰 보지 않으므로 정리와 채우기를 하지 않음
        cleanup = override is None
        stale, stamp, readable = identity_check(
            path, optional_int(row[5]), optional_int(row[6]), override
        )
        if stale:
            logger.debug("사용자 지정 활성 화면을 건너뜁니다. 영상 파일이 바뀌었습니다: %s", path)
            continue
        if not readable:
            # - 파일을 읽지 못하면 지금 프레임 크기를 알 수 없어 저장 사각형을 검증하지 않고 보존
            frames[path] = ActiveFrame(
                width=row_int(row[1]),
                height=row_int(row[2]),
                x=row_int(row[3]),
                y=row_int(row[4]),
            )
            continue
        width, height = frame_sizes[path]
        if width <= 0 or height <= 0:
            # - 프레임 크기를 모르면 저장 사각형이 벗어났는지 판정할 수 없어 사용자 지정을 보존
            frames[path] = ActiveFrame(
                width=row_int(row[1]),
                height=row_int(row[2]),
                x=row_int(row[3]),
                y=row_int(row[4]),
            )
        else:
            try:
                current = normalize_manual_active_frame(
                    ActiveFrame(
                        width=row_int(row[1]),
                        height=row_int(row[2]),
                        x=row_int(row[3]),
                        y=row_int(row[4]),
                    ),
                    width,
                    height,
                )
            except ValueError:
                if cleanup:
                    logger.info(
                        "사용자 지정 활성 화면을 지웠습니다. 영상 프레임(%sx%s)을 벗어났습니다: %s",
                        width,
                        height,
                        path,
                    )
                    dropped.append(row)
                continue
            if current is None:
                if cleanup:
                    logger.info(
                        "사용자 지정 활성 화면을 지웠습니다. 영상 프레임 전체와 같습니다: %s", path
                    )
                    dropped.append(row)
                continue
            frames[path] = current
        if stamp is not None and cleanup:
            stamped.append(stamp)
    return frames, dropped, stamped


def validated_crop_exclusion_rows(
    rows: Sequence[Sequence[object]],
    *,
    identity_overrides: Mapping[str, tuple[int, int]] | None = None,
) -> tuple[list[str], list[tuple[int, int, str]]]:
    """저장된 잘라내기 제외 행 중 지금 파일에도 유효한 경로만 남김

    - 반환: 유지할 경로, 지금 식별 값을 채울 (크기, 수정 시각, 경로)
    - `identity_overrides`의 처리는 `validated_manual_rows`와 같음
    - 식별 값을 채우는 것도 행 소유 파일을 읽는 조회에서만 함. 다른 파일의 식별 값으로 채우면 그
      값이 남아 다음 정상 조회에서 이 행이 무시됨
    """
    kept: list[str] = []
    stamped: list[tuple[int, int, str]] = []
    for row in rows:
        path = str(row[0])
        override = None if identity_overrides is None else identity_overrides.get(path)
        stale, stamp, _readable = identity_check(
            path, optional_int(row[2]), optional_int(row[3]), override
        )
        if stale:
            logger.debug("검은 여백 잘라내기 제외를 건너뜁니다. 영상 파일이 바뀌었습니다: %s", path)
            continue
        kept.append(path)
        if stamp is not None and override is None:
            stamped.append(stamp)
    return kept, stamped


def review_region_box(region: ActiveRegion) -> tuple[int, int, int, int]:
    """확인 표시를 비교할 활성 화면 사각형(너비, 높이, x, y)

    - 플래그(margins_vary, dark_outliers_ignored)는 비교에서 빼고 자리만 봄
    """
    return (region.width, region.height, region.x, region.y)


def validated_review_ack_rows(
    rows: Sequence[Sequence[object]],
    regions: Mapping[str, tuple[int, int, int, int]],
    *,
    identity_overrides: Mapping[str, tuple[int, int]] | None = None,
) -> tuple[list[str], list[Sequence[object]], list[tuple[int, int, str]]]:
    """저장된 여백 확인 표시 행 중 지금도 유효한 경로만 남김

    - 반환: 유지할 경로, 지울 행(조회 때 읽은 값 그대로), 지금 식별 값을 채울 (크기, 수정 시각, 경로)
    - 확인 표시는 파일이 바뀌면 되돌려야 하므로, 식별 값이 지금 파일과 다른 행은 그 조회에서 건너뛰고 지움
    - 자동 감지 영역이 저장 값과 달라졌으면 새 분석이 다른 영역으로 같은 경고를 냈다는 뜻이므로 지움
    - 영역을 모르는 경로(분석 결과 없음)는 비교할 수 없어 지움
    - `identity_overrides`의 처리는 `validated_manual_rows`와 같음. 다른 파일을 띄우는 조회에서 식별 값이
      맞지 않는 행은 결과 파일용 확인 표시일 수 있어 지우지 않고 그 조회에서만 건너뜀
    """
    kept: list[str] = []
    dropped: list[Sequence[object]] = []
    stamped: list[tuple[int, int, str]] = []
    for row in rows:
        path = str(row[0])
        stored = (row_int(row[1]), row_int(row[2]), row_int(row[3]), row_int(row[4]))
        override = None if identity_overrides is None else identity_overrides.get(path)
        stale, stamp, _readable = identity_check(
            path, optional_int(row[5]), optional_int(row[6]), override
        )
        if stale or regions.get(path) != stored:
            # - 다른 파일을 띄우는 조회는 행 소유 파일과 맞춰 보는 것이 아니므로 지우지 않고 건너뜀
            if override is None:
                dropped.append(row)
            else:
                logger.debug(
                    "여백 확인 표시를 건너뜁니다. 띄우는 파일과 저장 값이 다릅니다: %s", path
                )
            continue
        kept.append(path)
        if stamp is not None and override is None:
            stamped.append(stamp)
    return kept, dropped, stamped


def _match_clause(columns: Sequence[str]) -> str:
    """NULL도 같은 값으로 비교하는 조건식. 값 자리 표시자는 열 순서대로 받음"""
    return " AND ".join(f"{column} IS ?" for column in columns)


def write_row_changes(
    conn: sqlite3.Connection,
    *,
    table: str,
    stamped: Sequence[tuple[int, int, str]],
    drop_columns: Sequence[str] = (),
    dropped: Sequence[Sequence[object]] = (),
) -> None:
    """걸러 낸 행을 조회 때 값이 그대로일 때만 지우고, 식별 값이 없던 행에 지금 값을 채움

    - 조회와 정리 사이에 같은 경로로 새 지정이 저장될 수 있으므로 경로만으로 지우지 않음
    - 식별 값 채우기도 값이 아직 NULL인 행에만 적용해 새로 저장한 행의 값을 덮지 않음
    - 지우는 행은 프레임을 벗어난 사용자 지정처럼 저장 값을 그대로 쓸 수 없을 때만 넘김
    """
    if not dropped and not stamped:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if dropped:
            conn.executemany(
                f"DELETE FROM {table} WHERE {_match_clause(drop_columns)}",
                [tuple(row) for row in dropped],
            )
        if stamped:
            conn.executemany(
                f"UPDATE {table} SET {IDENTITY_COLUMNS[0]} = ?, {IDENTITY_COLUMNS[1]} = ?"
                f" WHERE source_path = ?"
                f" AND {IDENTITY_COLUMNS[0]} IS NULL AND {IDENTITY_COLUMNS[1]} IS NULL",
                stamped,
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
