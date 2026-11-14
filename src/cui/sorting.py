"""CUI 테이블 정렬용 컬럼 래퍼 및 내림차순 변환 유틸리티 모듈"""

from typing import Any

from src.core.exceptions import DomainTypeError, ErrorCode
from src.core.messages import COLUMN_LABELS, ColumnId
from src.core.sorting import WindowsNameOrder

desc: bool = True


def _build_column_alias_map() -> dict[str, str]:
    """일반(GUI)·축약(CUI)·별칭 키를 CUI 콘솔 헤더 문자열에 매핑하는 사전 생성"""
    aliases: dict[str, str] = {}
    for col_id, spec in COLUMN_LABELS.items():
        if col_id is ColumnId.STATUS:
            continue
        compact = spec.compact_header
        aliases[spec.full] = compact
        aliases[spec.short] = compact

    extra_aliases: tuple[tuple[str, ColumnId], ...] = (
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
    return aliases


t: dict[str, str] = _build_column_alias_map()

__all__ = ["UNICODE_MAX_CODEPOINT", "WindowsNameOrder", "col", "desc", "t"]


UNICODE_MAX_CODEPOINT: int = 0x10FFFF


def _str_desc(s: str) -> str:
    """단일 문자열에 대해 파이썬 sorted() 내에서 내림차순 정렬이 작동하도록 유니코드 코드포인트를 역전 변환"""
    return "".join(chr(UNICODE_MAX_CODEPOINT - ord(c)) for c in s)


def _windows_name_order_desc(parts: list[int | str]) -> list[int | str]:
    """윈도우 이름 정렬 키를 내림차순(역정렬)으로 정렬할 수 있도록 숫자 부호를 반전하고 문자 유니코드를 역전"""
    return [(-p if isinstance(p, int) else _str_desc(p)) for p in parts]


def col(
    x: str | int | float | list[int | str], order_by_desc: bool = False
) -> str | int | float | list[int | str]:
    """테이블 정렬 키에서 오름차순·내림차순 지정용 래퍼 함수

    숫자형은 부호 반전, 문자열과 자연 정렬 토큰 리스트는 전용 역전 변환 적용
    """
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
