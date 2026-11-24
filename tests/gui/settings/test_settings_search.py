"""설정 검색 순수 규칙(정규화, 일치 판정, 범주별 개수 집계) 검증"""

import unittest

from src.gui.settings.settings_search import (
    SearchEntry,
    count_matches,
    entry_matches,
    filter_entries,
    normalize_query,
)


def _entries() -> list[SearchEntry]:
    return [
        SearchEntry("display.size_unit", 0, "용량 표기 단위", "용량 표기 단위 표시 MB GB 자동"),
        SearchEntry("analysis.encode.crop_black_bars", 1, "검은 여백 잘라내기", "검은 여백 잘라내기 화질 분석"),
        SearchEntry("analysis.encode.roundtrip_vmaf_threshold", 1, "축소 후보 VMAF 기준", "축소 후보 VMAF 기준 화질 분석"),
        SearchEntry("execution.concurrent_jobs", 3, "동시 재인코딩 수", "동시 재인코딩 수 실행"),
    ]


class TestSettingsSearch(unittest.TestCase):
    """검색 규칙의 정규화, 일치, 집계 동작 검증"""

    def test_normalize_trims_and_lowercases(self) -> None:
        """검색어 앞뒤 공백을 지우고 소문자로 정규화하는지 검증"""
        self.assertEqual(normalize_query("  VMAF "), "vmaf")
        self.assertEqual(normalize_query(""), "")

    def test_entry_matches_any_part_of_the_text(self) -> None:
        """검색어가 이름, 설명, 카드 이름 중 어디에 있어도 일치하는지 검증"""
        entry = _entries()[2]
        self.assertTrue(entry_matches(entry, "vmaf"))
        self.assertTrue(entry_matches(entry, "축소 후보"))
        self.assertTrue(entry_matches(entry, "화질 분석"))
        self.assertFalse(entry_matches(entry, "동시"))

    def test_empty_query_matches_everything(self) -> None:
        """빈 검색어는 모든 항목과 일치하는지 검증"""
        for entry in _entries():
            self.assertTrue(entry_matches(entry, "   "))

    def test_filter_keeps_input_order(self) -> None:
        """일치 항목을 입력 순서 그대로 돌려주는지 검증"""
        found = filter_entries(_entries(), "화질 분석")
        self.assertEqual(
            [entry.key for entry in found],
            [
                "analysis.encode.crop_black_bars",
                "analysis.encode.roundtrip_vmaf_threshold",
            ],
        )

    def test_count_matches_skips_empty_categories(self) -> None:
        """범주별 일치 개수를 세고 0건 범주는 담지 않는지 검증"""
        self.assertEqual(count_matches(_entries(), "화질 분석"), {1: 2})
        self.assertEqual(count_matches(_entries(), "표기"), {0: 1})
        self.assertEqual(count_matches(_entries(), "없는검색어"), {})

    def test_count_matches_groups_across_categories(self) -> None:
        """여러 범주에 걸친 검색어의 개수를 범주별로 나눠 세는지 검증"""
        self.assertEqual(count_matches(_entries(), ""), {0: 1, 1: 2, 3: 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
