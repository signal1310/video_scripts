"""CUI 테이블 정렬용 컬럼 래퍼, 프리셋 정렬 키 빌더 및 타입 안전 별칭 매핑 모듈"""

from collections.abc import Callable
from typing import Any, Literal, TypedDict, cast

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

CuiColumnKeyMap = TypedDict(
    "CuiColumnKeyMap",
    {
        "이름": str,
        "가로": str,
        "너비": str,
        "W": str,
        "세로": str,
        "높이": str,
        "H": str,
        "회전": str,
        "코덱": str,
        "FPS": str,
        "길이": str,
        "비트레이트": str,
        "BR": str,
        "br": str,
        "b-rate": str,
        "최적 비트레이트": str,
        "최적 BR": str,
        "최적 br": str,
        "최적 b-rate": str,
        "비트레이트 비율": str,
        "BR 비율": str,
        "br 비율": str,
        "b-rate 비율": str,
        "화질 배수": str,
        "화질 등급": str,
        "키프레임 간격": str,
        "KF 간격": str,
        "KF": str,
        "키프레임": str,
        "용량": str,
        "예상 용량": str,
        "예상후": str,
        "예상 절감": str,
        "예상절감": str,
        "권장 작업": str,
        "조치": str,
        "사유": str,
        "판정 사유": str,
    },
)


def _build_column_alias_map() -> CuiColumnKeyMap:
    """일반(GUI), 축약(CUI), 별칭 키를 CUI 콘솔 헤더 문자열에 매핑하는 사전 생성"""
    aliases: dict[str, str] = {}
    for col_id, spec in COLUMN_LABELS.items():
        if col_id is ColumnId.STATUS:
            continue
        compact = spec.compact_header
        aliases[spec.full] = compact
        aliases[spec.short] = compact

    extra_aliases: tuple[tuple[str, ColumnId], ...] = (
        ("가로", ColumnId.WIDTH),
        ("세로", ColumnId.HEIGHT),
        ("W", ColumnId.WIDTH),
        ("H", ColumnId.HEIGHT),
        ("br", ColumnId.BITRATE),
        ("b-rate", ColumnId.BITRATE),
        ("최적 br", ColumnId.OPT_BITRATE),
        ("최적 b-rate", ColumnId.OPT_BITRATE),
        ("br 비율", ColumnId.RATIO),
        ("b-rate 비율", ColumnId.RATIO),
        ("화질 배수", ColumnId.RATIO),
        ("KF", ColumnId.KEYFRAME),
        ("키프레임", ColumnId.KEYFRAME),
        ("조치", ColumnId.ACTION),
        ("판정 사유", ColumnId.REASON),
    )
    for alias, col_id in extra_aliases:
        aliases[alias] = COLUMN_LABELS[col_id].compact_header
    return cast("CuiColumnKeyMap", aliases)


t: CuiColumnKeyMap = _build_column_alias_map()

__all__ = [
    "UNICODE_MAX_CODEPOINT",
    "CuiColumnKeyMap",
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
        col_id = ColumnId.NAME if sort_by is ColumnId.STATUS else sort_by
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
