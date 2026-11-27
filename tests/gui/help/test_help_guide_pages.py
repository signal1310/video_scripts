"""사용 가이드 프로그램 안내 탭의 화질 분석과 육안 확인 절 검증"""

import unittest

from src.core.quality_target import MAXRATE_HEADROOM_MULTIPLIER, MAXRATE_ROUND_KBPS
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
        self.assertIn("화질 비교 창", html)
        self.assertIn("분할선, 즉시 전환, 좌우 비교", html)
        self.assertIn("외부 플레이어도 함께", html)

    def test_maxrate_feature_limits_scope_to_downscale(self) -> None:
        """최대 비트레이트 안내가 일반 인코딩에는 적용되지 않고 배지 여유 기준을 밝히는지 검증"""
        html = build_guide_html(LIGHT_PALETTE)
        self.assertIn("크기를 줄이지 않는 일반 인코딩에는 이 상한을 걸지 않습니다", html)
        self.assertIn(f"{MAXRATE_HEADROOM_MULTIPLIER:.1f}배", html)
        self.assertIn(f"{MAXRATE_ROUND_KBPS}k 단위", html)
        self.assertIn("판정 시점의 축소 후보 여유로 계산", html)
        self.assertNotIn("정밀 결정의 축소 비교에서", html)


if __name__ == "__main__":
    unittest.main()
