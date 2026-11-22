"""용어 사전의 재인코딩 설명이 실제 동작(시작 확인 뒤 인코딩)과 맞고 예전 문장이 남지 않았는지 검증"""

import unittest

from src.gui.help.glossary_data import GLOSSARY_TERMS

_OLD_SENTENCE: str = "실제 재인코딩은 아직 실행하지 않습니다"
_NEW_SENTENCE: str = "재인코딩 시작 확인을 거친 뒤 실제로 인코딩합니다."


class GlossaryReencodeDescriptionTests(unittest.TestCase):
    """재인코딩 용어 설명 문장을 검증"""

    def test_reencode_term_describes_the_confirm_then_encode_flow(self) -> None:
        """재인코딩 용어 설명이 시작 확인을 거쳐 실제로 인코딩한다고 적는지 검증"""
        term = next(term for term in GLOSSARY_TERMS if term.term == "재인코딩")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
