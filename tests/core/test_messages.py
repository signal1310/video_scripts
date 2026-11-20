"""판정 사유 머리글 툴팁이 분석 형식 변경 사유를 한 줄로 설명하는지 검증"""

import unittest

from src.core.config import ClassifierConfig
from src.core.messages import format_reason_tooltip
from src.core.smart_action import SmartAction


class ReasonTooltipTests(unittest.TestCase):
    """판정 사유 머리글 툴팁의 줄 구성을 검증"""

    def test_legacy_format_reason_has_a_one_line_description(self) -> None:
        """분석 형식 변경 사유가 표시 이름과 짧은 설명을 가진 한 줄로 나오는지 검증"""
        lines = format_reason_tooltip(ClassifierConfig()).split("\n")
        self.assertIn(
            f"• {SmartAction.LEGACY_FORMAT.reason_label}: "
            "분석 형식이 바뀌어 다시 분석하기 전까지 재인코딩 대상에서 제외합니다.",
            lines,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
