"""윈도우 탐색기 방식 자연 정렬 토큰 생성 및 비교 유틸리티 모듈"""

import re


def WindowsNameOrder(name: str) -> list[int | str]:
    """파일명을 윈도우 탐색기 자연 정렬 규칙에 맞게 분할

    숫자와 문자가 섞인 문자열에서 숫자 토큰을 정수로 변환해 수치 순서대로 정렬
    """
    return [
        int(token) if token.isdigit() else token.lower()
        for token in re.split(r"(\d+)", str(name))
    ]


def natural_sort_key(name: str) -> list[tuple[int, int | str]]:
    """sorted() 등 키 기반 자연 정렬에 사용하는 타입 안전 토큰 시퀀스 생성"""
    return [
        (0, tok) if isinstance(tok, int) else (1, tok)
        for tok in WindowsNameOrder(name)
    ]


def compare_natural_tokens(left: list[int | str], right: list[int | str]) -> bool:
    """WindowsNameOrder 토큰 리스트 간의 타입 안전(int < str 우선) 비교를 수행"""
    for l_tok, r_tok in zip(left, right, strict=False):
        if l_tok == r_tok:
            continue
        if isinstance(l_tok, int) and isinstance(r_tok, int):
            return l_tok < r_tok
        if isinstance(l_tok, str) and isinstance(r_tok, str):
            return l_tok < r_tok
        return isinstance(l_tok, int)
    return len(left) < len(right)
