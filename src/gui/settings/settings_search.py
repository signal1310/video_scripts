"""환경 설정 통합 검색의 순수 규칙 모듈

- 검색어 정규화, 항목 일치 판정, 범주별 일치 개수 집계만 담당하며 Qt에 의존하지 않음
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchEntry:
    """검색 대상 설정 한 항목(행 키, 범주 인덱스, 표시 이름, 검색 본문)"""

    key: str
    category: int
    title: str
    text: str


def normalize_query(text: str) -> str:
    """검색어의 앞뒤 공백을 지우고 소문자로 정규화"""
    return text.strip().lower()


def entry_matches(entry: SearchEntry, query: str) -> bool:
    """정규화된 검색어가 항목 본문에 들어 있는지 판정(빈 검색어는 모든 항목과 일치)"""
    needle = normalize_query(query)
    if not needle:
        return True
    return needle in entry.text.lower()


def filter_entries(entries: list[SearchEntry], query: str) -> list[SearchEntry]:
    """검색어와 일치하는 항목만 입력 순서대로 반환"""
    return [entry for entry in entries if entry_matches(entry, query)]


def count_matches(entries: list[SearchEntry], query: str) -> dict[int, int]:
    """범주 인덱스별 일치 개수 집계(일치 0건 범주는 담지 않음)"""
    counts: dict[int, int] = {}
    for entry in filter_entries(entries, query):
        counts[entry.category] = counts.get(entry.category, 0) + 1
    return counts
