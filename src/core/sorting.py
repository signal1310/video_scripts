import re


def WindowsNameOrder(name: str) -> list[int | str]:
    """
    - 파일명을 윈도우 탐색기의 자연 정렬(Natural Sort) 규칙에 맞추어 분할.
    - 숫자와 문자가 섞인 문자열에서 숫자를 정수로 해석하여 'vid_2'가 'vid_10'보다 앞에 오도록 처리.
    """
    return [
        int(token) if token.isdigit() else token.lower()
        for token in re.split(r"(\d+)", str(name))
    ]


def compare_natural_tokens(left: list[int | str], right: list[int | str]) -> bool:
    """
    - WindowsNameOrder 토큰 리스트 간의 타입 안전(int < str 우선) 비교를 수행.
    """
    for l_tok, r_tok in zip(left, right, strict=False):
        if l_tok == r_tok:
            continue
        if isinstance(l_tok, int) and isinstance(r_tok, int):
            return l_tok < r_tok
        if isinstance(l_tok, str) and isinstance(r_tok, str):
            return l_tok < r_tok
        return isinstance(l_tok, int)
    return len(left) < len(right)
