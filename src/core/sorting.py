"""윈도우 탐색기 방식 자연 정렬 토큰 생성 및 비교 유틸리티 모듈"""

import re
from collections.abc import Sequence
from functools import lru_cache

_NATURAL_SPLIT_RE: re.Pattern[str] = re.compile(r"(\d+)")
_SORT_CACHE_MAXSIZE: int = 16384


@lru_cache(maxsize=_SORT_CACHE_MAXSIZE)
def _tokenize_windows_name(name: str) -> tuple[int | str, ...]:
    """정규식 캡처 그룹의 짝, 홀수 인덱스 패리티를 이용해 숫자, 문자열 토큰을 캐싱 분할"""
    return tuple(
        int(token) if (idx & 1) else token.lower()
        for idx, token in enumerate(_NATURAL_SPLIT_RE.split(name))
    )


def WindowsNameOrder(name: str) -> list[int | str]:
    """파일명을 윈도우 탐색기 자연 정렬 규칙에 맞게 분할

    숫자와 문자가 섞인 문자열에서 숫자 토큰을 정수로 변환해 수치 순서대로 정렬
    """
    return list(_tokenize_windows_name(str(name)))


def natural_sort_key(name: str) -> list[tuple[int, int | str]]:
    """sorted() 등 키 기반 자연 정렬에 사용하는 타입 안전 토큰 시퀀스 생성"""
    return [
        (0, tok) if isinstance(tok, int) else (1, tok)
        for tok in _tokenize_windows_name(str(name))
    ]


def compare_natural_order(
    left: Sequence[int | str],
    right: Sequence[int | str],
) -> int:
    """WindowsNameOrder 토큰 시퀀스 간 단일 패스 3방향 비교(-1, 0, 1) 수행"""
    for l_tok, r_tok in zip(left, right, strict=False):
        if l_tok == r_tok:
            continue
        if isinstance(l_tok, int) and isinstance(r_tok, int):
            return -1 if l_tok < r_tok else 1
        if isinstance(l_tok, str) and isinstance(r_tok, str):
            return -1 if l_tok < r_tok else 1
        return -1 if isinstance(l_tok, int) else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def compare_natural_tokens(left: list[int | str], right: list[int | str]) -> bool:
    """WindowsNameOrder 토큰 리스트 간의 타입 안전(int < str 우선) 비교를 수행"""
    return compare_natural_order(left, right) < 0
