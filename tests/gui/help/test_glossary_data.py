"""용어 사전의 인코딩 설명이 실제 동작(요약 탭에서 시작한 뒤 인코딩)과 맞고 예전 문장이 남지 않았는지 검증"""

import unittest

from src.gui.help.glossary_data import GLOSSARY_TERMS

_OLD_SENTENCE: str = "실제 인코딩은 아직 실행하지 않습니다"
_NEW_SENTENCE: str = "인코딩 창 요약 탭에서 시작한 뒤 실제로 인코딩합니다."


class GlossaryReencodeDescriptionTests(unittest.TestCase):
    """인코딩 용어 설명 문장을 검증"""

    def test_reencode_term_describes_the_summary_start_then_encode_flow(self) -> None:
        """인코딩 용어 설명이 요약 탭에서 시작한 뒤 실제로 인코딩한다고 적는지 검증"""
        term = next(term for term in GLOSSARY_TERMS if term.term == "인코딩")
        self.assertIn(_NEW_SENTENCE, term.desc)

    def test_no_term_keeps_the_old_not_yet_running_sentence(self) -> None:
        """예전 설명 문장이 어떤 용어에도 남아 있지 않은지 검증"""
        self.assertFalse(any(_OLD_SENTENCE in term.desc for term in GLOSSARY_TERMS))

    def test_frame_analysis_terms_are_registered(self) -> None:
        """화질 분석, 활성 화면, 축소 후보, VMAF 용어와 VR 보호 설명이 있는지 검증"""
        terms = {term.term: term for term in GLOSSARY_TERMS}
        for name in ("화질 분석", "활성 화면", "축소 후보", "VMAF"):
            self.assertIn(name, terms)
        vr_value = next(value for value in terms["활성 화면"].values if value.name == "VR 보호")
        self.assertIn("축소 후보는 정상 측정", vr_value.meaning)

    def test_compare_terms_include_modes_zoom_and_downscale_explanation(self) -> None:
        """화질 비교 용어에 모드 3종, 1:1 확대, 축소 비교 풀이가 있는지 검증"""
        term = next(term for term in GLOSSARY_TERMS if term.term == "화질 비교")
        values = {value.name: value.meaning for value in term.values}
        self.assertEqual(set(values), {"분할선", "즉시 전환", "좌우 비교", "1:1", "축소 비교"})
        self.assertIn("100%", values["1:1"])
        self.assertIn("원본 크기로 되돌린", values["축소 비교"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
