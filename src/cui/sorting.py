"""CUI 테이블 정렬용 컬럼 래퍼, 프리셋 정렬 키 빌더 및 열 이름 매핑 모듈"""

from collections.abc import Callable
from typing import Any, Literal

from src.core.exceptions import DomainTypeError, ErrorCode
from src.core.messages import COLUMN_LABELS, ColumnId
from src.core.models import QUALITY_ZONE_SORT_RANK
from src.core.sorting import WindowsNameOrder

desc: bool = True

SortBy = Literal[
    "name",
    "width",
    "height",
    "rotate",
    "codec",
    "fps",
    "duration",
    "bitrate",
    "opt_bitrate",
    "ratio",
    "out_bitrate",
    "target_ratio",
    "zone",
    "keyframe",
    "size",
    "after_size",
    "saved",
    "action",
    "reason",
]
SortTarget = SortBy | ColumnId
RowSortKey = Callable[[dict[str, Any]], tuple[Any, ...] | list[Any]] | None

def _build_column_alias_map() -> dict[str, str]:
    """열 풀네임과 짧은 이름을 CUI 콘솔 헤더 문자열에 매핑하는 사전 생성"""
    aliases: dict[str, str] = {}
    for spec in COLUMN_LABELS.values():
        compact = spec.compact_header
        aliases[spec.full] = compact
        aliases[spec.short] = compact
    return aliases


t: dict[str, str] = _build_column_alias_map()

__all__ = [
    "UNICODE_MAX_CODEPOINT",
    "RowSortKey",
    "SortBy",
    "SortTarget",
    "WindowsNameOrder",
    "build_sort_key",
    "col",
    "desc",
    "t",
]

UNICODE_MAX_CODEPOINT: int = 0x10FFFF

_SORT_BY_COLUMN_MAP: dict[SortBy, ColumnId] = {
    "name": ColumnId.NAME,
    "width": ColumnId.WIDTH,
    "height": ColumnId.HEIGHT,
    "rotate": ColumnId.ROTATE,
    "codec": ColumnId.CODEC,
    "fps": ColumnId.FPS,
    "duration": ColumnId.DURATION,
    "bitrate": ColumnId.BITRATE,
    "opt_bitrate": ColumnId.OPT_BITRATE,
    "ratio": ColumnId.RATIO,
    "out_bitrate": ColumnId.OUT_BITRATE,
    "target_ratio": ColumnId.TARGET_RATIO,
    "zone": ColumnId.ZONE,
    "keyframe": ColumnId.KEYFRAME,
    "size": ColumnId.SIZE,
    "after_size": ColumnId.AFTER_SIZE,
    "saved": ColumnId.SAVED_SIZE,
    "action": ColumnId.ACTION,
    "reason": ColumnId.REASON,
}

_DEFAULT_DESC_COLUMNS: frozenset[ColumnId] = frozenset(
    {
        ColumnId.SAVED_SIZE,
        ColumnId.SIZE,
        ColumnId.AFTER_SIZE,
        ColumnId.RATIO,
        ColumnId.TARGET_RATIO,
        ColumnId.BITRATE,
        ColumnId.OPT_BITRATE,
        ColumnId.KEYFRAME,
        ColumnId.DURATION,
        ColumnId.FPS,
        ColumnId.WIDTH,
        ColumnId.HEIGHT,
    }
)

_ZONE_SORT_RANK: dict[str, int] = {
    zone.value: rank for zone, rank in QUALITY_ZONE_SORT_RANK.items()
}


def _str_desc(s: str) -> str:
    """단일 문자열에 대해 파이썬 sorted() 내에서 내림차순 정렬이 작동하도록 유니코드 코드포인트를 역전 변환"""
    return "".join(chr(UNICODE_MAX_CODEPOINT - ord(c)) for c in s)


def _windows_name_order_desc(parts: list[int | str]) -> list[int | str]:
    """윈도우 이름 정렬 키를 내림차순(역정렬)으로 정렬할 수 있도록 숫자 부호를 반전하고 문자 유니코드를 역전"""
    return [(-p if isinstance(p, int) else _str_desc(p)) for p in parts]


def col(
    x: str | int | float | list[int | str] | None, order_by_desc: bool = False
) -> str | int | float | list[int | str]:
    """테이블 정렬 키에서 오름차순, 내림차순 지정용 래퍼 함수

    숫자형은 부호 반전, 문자열과 자연 정렬 토큰 리스트는 전용 역전 변환을 적용하며 None은 항상 마지막에 배치
    """
    if x is None:
        return float("-inf") if order_by_desc else float("inf")
    if isinstance(x, str):
        return _str_desc(x) if order_by_desc else x
    if isinstance(x, (int, float)):
        return -x if order_by_desc else x
    raw: Any = x
    if isinstance(raw, list) and all(isinstance(e, (int, str)) for e in raw):
        return _windows_name_order_desc(raw) if order_by_desc else raw

    raise DomainTypeError(
        f"지원하지 않는 정렬 키 타입이 전달되었습니다: {type(x).__name__}",
        code=ErrorCode.INVALID_SORT_KEY,
    )


def build_sort_key(
    sort_by: SortTarget | None = None,
    descending: bool | None = None,
) -> Callable[[dict[str, Any]], tuple[Any, ...]]:
    """프리셋 열 이름 또는 ColumnId 기반 CUI 테이블 정렬 키 함수 생성 (기본: 이름 자연 정렬 오름차순)"""
    if sort_by is None:
        col_id = ColumnId.NAME
    elif isinstance(sort_by, ColumnId):
        col_id = sort_by
    else:
        col_id = _SORT_BY_COLUMN_MAP.get(sort_by, ColumnId.NAME)

    use_desc = (
        col_id in _DEFAULT_DESC_COLUMNS if descending is None else descending
    )
    name_hdr = COLUMN_LABELS[ColumnId.NAME].compact_header
    target_hdr = COLUMN_LABELS[col_id].compact_header

    if col_id is ColumnId.NAME:
        return lambda row: (col(WindowsNameOrder(str(row.get(name_hdr, ""))), use_desc),)

    if col_id is ColumnId.ZONE:
        return lambda row: (
            col(
                _ZONE_SORT_RANK.get(str(row.get(target_hdr, "")), -1),
                use_desc,
            ),
            WindowsNameOrder(str(row.get(name_hdr, ""))),
        )

    return lambda row: (
        row.get(target_hdr) is None,
        col(row.get(target_hdr), use_desc),
        WindowsNameOrder(str(row.get(name_hdr, ""))),
    )
