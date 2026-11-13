"""CUI 테이블 정렬용 컬럼 래퍼 및 내림차순 변환 유틸리티 모듈"""

from typing import Any

from src.core.exceptions import DomainTypeError, ErrorCode
from src.core.sorting import WindowsNameOrder

desc: bool = True

t: dict[str, str] = {
    "이름": "\n이름",
    "W": "\nW",
    "H": "\nH",
    "코덱": "\n코덱",
    "FPS": "\nFPS",
    "길이": "길이\n(초)",
    "용량": "용량\n(MB)",
    "b-rate": "\nb-rate",
    "비트레이트": "\nb-rate",
    "최적 b-rate": "최적\nb-rate",
    "최적 비트레이트": "최적\nb-rate",
    "b-rate 비율": "b-rate\n비율",
    "비트레이트 비율": "b-rate\n비율",
    "화질 배수": "b-rate\n비율",
    "화질 등급": "화질\n등급",
    "KF 간격": "KF\n간격",
    "키프레임 간격": "KF\n간격",
    "KF": "KF\n간격",
    "키프레임": "KF\n간격",
    "예상 용량": "예상후\n(MB)",
    "예상 절감": "예상절감\n(MB)",
    "처방": "인코딩\n처방",
    "인코딩 처방": "인코딩\n처방",
}

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
