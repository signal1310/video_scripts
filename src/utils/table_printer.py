import unicodedata
import emoji
from typing import Any, List, Dict, Callable, Tuple
from tabulate import tabulate

from src.utils.load_env import load_env


def sanitize_text(text: str) -> str:
    """
    터미널 렌더링을 방해하는 모든 특수 문자들을 정리합니다.
    1. 이모지 ->  치환
    2. NFKC 정규화 (특수 폰트 𝑯 -> 일반 H 복원)
    3. 결합 문자(태국어 성조 등) 제거
    4. 제어 문자 및 기타 심볼 치환
    """
    if not text: return ""

    # [Step 1] 이모지를 먼저 치환 (emoji 라이브러리 활용)
    text = emoji.replace_emoji(text, replace="?")

    # [Step 2] NFKC 정규화
    # 𝑯, 𝟵, 𝕏 같은 문자를 일반 알파벳/숫자로 변환해줍니다.
    text = unicodedata.normalize('NFKC', text)

    # [Step 3] 렌더링에 방해되는 유니코드 카테고리 필터링
    # Mn: Non-spacing Mark (글자 위에 붙는 성조, 악센트 등 -> 칸 어긋남의 주범)
    # Cc: Control (제어 문자)
    # Cf: Format (서식 문자)
    # So: Symbol, Other (특수 기호들 중 NFKC로 변환 안 된 것들)
    
    clean_chars = []
    for ch in text:

        eaw = unicodedata.east_asian_width(ch)
        category = unicodedata.category(ch)

        # [Rule 1] ASCII 문자는 무조건 허용 (영어, 숫자, 기본 기호)
        if ord(ch) < 128:
            clean_chars.append(ch)
            continue

        # [Rule 2] 한글, 한자 등 확실한 2칸 문자(Wide, Fullwidth)는 허용
        # W: Wide (한글, 한자 등)
        # F: Fullwidth (전각 문자)
        if eaw in ('W', 'F'):
            clean_chars.append(ch)
            continue

        # [Rule 3] 그 외 모든 것은 '?'로 치환
        # 태국어, 아랍어, 히브리어, 특수 기호 등은 여기서 걸러집니다.
        # 이들은 보통 'N' (Neutral)이나 'A' (Ambiguous) 속성을 가짐
        clean_chars.append("?")

    return "".join(clean_chars)


def sanitize_col(table: List[Dict[str, Any]], key: str) -> None:
    """
    [전처리] 지정한 열(key)의 문자열 내 모든 이모지를 U+FFFD() 문자로 치환합니다.
    VS Code 터미널 렌더링 렉을 방지하고, 길이 계산의 정확성을 높입니다.
    """
    for row in table:
        if key in row and isinstance(row[key], str):
            # emoji.replace_emoji: 텍스트 내 이모지만 찾아 replace 인자로 변경
            # \ufffd: 유니코드 대체 문자 ()
            row[key] = sanitize_text(row[key])


def truncate_text(text: str, max_len: int) -> str:
    result: str = ""
    curr_len: int = 0
    for ch in text:
        # F, W, A: Fullwidth, Wide, Ambiguous → 2칸 취급
        ch_len: int = 2 if unicodedata.east_asian_width(ch) in ("F", "W", "A") else 1
        if curr_len + ch_len > max_len:
            cut: str = result[:-2] if len(result) >= 2 else ""
            return cut + "..."
        result += ch
        curr_len += ch_len
    return result


def truncate_col(table: List[Dict[str, Any]], key: str, max_len: int) -> None:
    """
    열의 글자 폭 기준으로 길이를 제한 (동양문자, 이모티콘 등 2글자 폭 취급)
    """
    for row in table:
        if key in row and isinstance(row[key], str):
            row[key] = truncate_text(row[key], max_len)


class TablePrinter:
    @staticmethod
    def print(table: List[Dict[str, Any]], sort_key: Callable[[Dict[str, Any]], Tuple | list] | None, filename_maxlen: int) -> None:
        """
        테이블 형식 데이터를 출력
        sort_key를 통해 정렬 후, 길이가 긴 열을 자르고 출력
        """

        data_to_print: List[Dict[str, Any]] = sorted(table, key=sort_key) if sort_key else table
        sanitize_col(data_to_print, "\n이름")
        truncate_col(data_to_print, "\n이름", filename_maxlen)

        print(tabulate(
            tabular_data=data_to_print,
            headers="keys", 
            floatfmt=load_env("TABULATE_FLOATFMT"))
        )