"""사용 가이드 용어 사전의 분류 조건, 검색, 현재 판별 기준 반영 및 표기 규칙 검증"""

import unittest

from src.core.config import ClassifierConfig, ThresholdsConfig
from src.gui.dialogs.glossary_data import GLOSSARY_CATEGORIES, GLOSSARY_TERMS
from src.gui.dialogs.glossary_view import (
    GLOSSARY_ALL_CATEGORY,
    build_glossary_html,
    filter_glossary_terms,
)
from src.gui.styles.palettes import DARK_PALETTE

_RETIRED_LABELS: tuple[str, ...] = (
    "스캔",
    "교정",
    "절감",
    "과다",
    "낮음",
    "불량",
    "소요",
    "권장 작업",
    "이동 완료",
    "이동 예정",
    "이동 안 함",
    "인코딩 불필요",
    "예상후",
    "예상절감",
    "미미",
    "실익",
    "트랜스코딩",
    "원본 유지",
    "환경설정",
    "분류 복원",
    "폴더 분류",
    "예외 목록",
)


class TestGlossaryView(unittest.TestCase):
    def test_every_term_belongs_to_a_declared_category(self) -> None:
        """모든 용어가 선언된 분류에 속하고 분류 키가 중복되지 않음을 검증"""
        category_keys = [key for key, _ in GLOSSARY_CATEGORIES]
        self.assertEqual(len(category_keys), len(set(category_keys)))
        for term in GLOSSARY_TERMS:
            with self.subTest(term=term.term):
                self.assertIn(term.category, category_keys)

    def test_filter_applies_category_and_case_insensitive_query(self) -> None:
        """분류 조건과 대소문자 무시 검색이 함께 적용되고, 일치 없으면 빈 결과를 반환함을 검증"""
        self.assertEqual(len(filter_glossary_terms("", GLOSSARY_ALL_CATEGORY)), len(GLOSSARY_TERMS))
        quality_terms = filter_glossary_terms("", "quality")
        self.assertTrue(quality_terms)
        self.assertTrue(all(term.category == "quality" for term in quality_terms))
        found = [term.term for term in filter_glossary_terms("  br  ", GLOSSARY_ALL_CATEGORY)]
        self.assertIn("비트레이트", found)
        self.assertEqual(filter_glossary_terms("존재하지않는검색어", GLOSSARY_ALL_CATEGORY), ())

    def test_descriptions_follow_current_thresholds(self) -> None:
        """설명의 기준값이 현재 판별 기준으로 채워지고 자리표시자가 화면에 남지 않음을 검증"""
        config = ClassifierConfig(thresholds=ThresholdsConfig(over_bitrate_ratio=1.5))
        rendered = build_glossary_html(GLOSSARY_TERMS, config, DARK_PALETTE)
        self.assertIn("1.5배", rendered)
        self.assertNotIn("{", rendered)
        self.assertNotIn("}", rendered)

    def test_empty_selection_shows_guidance(self) -> None:
        """검색 결과가 없으면 안내 문구를 출력함을 검증"""
        self.assertIn(
            "검색 결과가 없습니다", build_glossary_html((), ClassifierConfig(), DARK_PALETTE)
        )

    def test_visible_text_uses_standard_terms_only(self) -> None:
        """화면에 보이는 용어 설명에 폐기된 구용어가 없음을 검증"""
        rendered = build_glossary_html(GLOSSARY_TERMS, ClassifierConfig(), DARK_PALETTE)
        for label in _RETIRED_LABELS:
            with self.subTest(label=label):
                self.assertNotIn(label, rendered)

    def test_no_middle_dot_in_ui_strings(self) -> None:
        """사용자 노출 문구에 가운뎃점을 쓰지 않음을 검증(AGENTS.md 4절)"""
        rendered = build_glossary_html(GLOSSARY_TERMS, ClassifierConfig(), DARK_PALETTE)
        self.assertNotIn("·", rendered)


if __name__ == "__main__":
    unittest.main()
