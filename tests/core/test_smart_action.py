"""판정의 표시 이름, 재인코딩 여부, 열 필터 사유 목록이 분석 형식 변경 판정을 포함하는지 검증"""

import unittest

from src.core.smart_action import SMART_ACTION_REASONS, ActionBadgeTone, SmartAction


class LegacyFormatActionTests(unittest.TestCase):
    """분석 형식 변경 판정의 재인코딩 여부와 표시 문구를 검증"""

    def test_legacy_format_is_a_pass_action_with_short_reason_label(self) -> None:
        """분석 형식 변경 판정이 재인코딩 대상이 아니고 짧은 사유 표기를 가지는지 검증"""
        self.assertFalse(SmartAction.LEGACY_FORMAT.is_encode)
        self.assertEqual(SmartAction.LEGACY_FORMAT.badge_tone, ActionBadgeTone.PASS)
        self.assertEqual(SmartAction.LEGACY_FORMAT.reason_label, "분석 형식 변경")

    def test_reason_filter_choices_are_unique_and_include_the_legacy_label(self) -> None:
        """열 필터 사유 목록에 중복이 없고 분석 형식 변경 사유가 들어 있는지 검증"""
        self.assertEqual(len(SMART_ACTION_REASONS), len(set(SMART_ACTION_REASONS)))
        self.assertIn("분석 형식 변경", SMART_ACTION_REASONS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
