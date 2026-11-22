"""사용 가이드 프로그램 안내 탭의 화질 분석과 육안 확인 절 검증"""

import unittest

from src.gui.help.help_guide_pages import build_guide_html
from src.gui.theme import LIGHT_PALETTE


class QualityGuideSectionTests(unittest.TestCase):
    """화질 분석 안내 소제목과 육안 확인 항목, 외부 플레이어 안내 검증"""

    def test_quality_section_lists_the_approved_checks(self) -> None:
        html = build_guide_html(LIGHT_PALETTE)
        self.assertIn("화질 분석과 육안 확인", html)
        self.assertIn("링잉과 할로(경계 이중선)", html)
        self.assertIn("뭉개짐과 계단 현상", html)
        self.assertIn("화면 대비 과도하게 큰 블록", html)
        self.assertIn("외부 플레이어 1:1 확인", html)


if __name__ == "__main__":
    unittest.main()
