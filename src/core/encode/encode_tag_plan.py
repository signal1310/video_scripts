"""인코딩 결과에 기록할 키워드와 상세 값을 기록 방식에 따라 계산하는 순수 함수 모듈

- visible: 원본 키워드에서 우리 태그 이름(현재와 이전, 대소문자 무시)을 뺀 뒤 현재 이름을 더함
- hidden: 원본 키워드에서 우리 태그 이름만 빼고 상세 값만 기록
- off: 원본 키워드에서 우리 태그 이름만 빼고 상세 값은 기록하지 않음
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from src.core.encode_tag import (
    ENCODE_TAG_MODE_HIDDEN,
    ENCODE_TAG_MODE_VISIBLE,
    VALID_ENCODE_TAG_MODES,
    normalize_tag_name,
)

KEYWORD_SEPARATOR: str = ";"


@dataclass(frozen=True, slots=True)
class EncodeTagPlan:
    """결과 파일에 기록할 인코딩 태그 계산 결과"""

    mode: str
    tag_name: str
    keywords: tuple[str, ...]
    detail: str | None


def parse_keyword_list(raw: str | None) -> tuple[str, ...]:
    """세미콜론으로 구분된 키워드 문자열을 공백을 뗀 튜플로 나눔"""
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(KEYWORD_SEPARATOR) if part.strip())


def format_mkv_keywords(keywords: Sequence[str]) -> str:
    """키워드 목록을 MKV KEYWORDS 태그 문자열(세미콜론 연결)로 만듦"""
    return KEYWORD_SEPARATOR.join(keywords)


def plan_encode_tags(
    *,
    mode: str,
    tag_name: str,
    source_keywords: Iterable[str],
    known_tag_names: Iterable[str] = (),
    detail: str = "",
) -> EncodeTagPlan:
    """기록 방식과 원본 키워드 목록으로 결과 키워드와 상세 값을 계산"""
    if mode not in VALID_ENCODE_TAG_MODES:
        raise ValueError(f"지원하지 않는 인코딩 태그 기록 방식입니다: {mode}")
    clean_name = normalize_tag_name(tag_name)
    excluded = {clean_name.casefold()}
    for known in known_tag_names:
        stripped = known.strip()
        if stripped:
            excluded.add(stripped.casefold())

    seen: set[str] = set()
    kept: list[str] = []
    for raw in source_keywords:
        keyword = raw.strip()
        if not keyword:
            continue
        folded = keyword.casefold()
        if folded in excluded or folded in seen:
            continue
        seen.add(folded)
        kept.append(keyword)

    if mode == ENCODE_TAG_MODE_VISIBLE:
        kept.append(clean_name)
        result_detail: str | None = detail
    elif mode == ENCODE_TAG_MODE_HIDDEN:
        result_detail = detail
    else:
        result_detail = None

    return EncodeTagPlan(
        mode=mode,
        tag_name=clean_name,
        keywords=tuple(kept),
        detail=result_detail,
    )
