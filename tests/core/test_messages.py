"""판정 사유 머리글 툴팁이 분석 형식 변경 사유를 한 줄로 설명하는지 검증"""

import unittest

from src.core.config import ClassifierConfig
from src.core.messages import (
    format_keyframe_tooltip,
    format_reason_tooltip,
    format_restore_finished,
)
from src.core.smart_action import SmartAction


class RestoreFinishedTextTests(unittest.TestCase):
    """이전 분석 결과 복원 한 번이 영상별 줄 대신 남기는 요약 문장을 검증"""

    def test_summary_reports_count_and_seconds_without_encode_targets(self) -> None:
        """판정까지만 끝난 복원은 건수와 소요 시간만 남기는지 검증"""
        self.assertEqual(
            format_restore_finished(5000, 1.25),
            "이전 분석 결과 복원: 5,000개 영상 (소요 1.2초)",
        )

    def test_summary_adds_encode_target_count_when_judged(self) -> None:
        """판정까지 끝나 인코딩 대상 수를 알면 그 수를 앞에 덧붙이는지 검증"""
        self.assertEqual(
            format_restore_finished(12, 3.0, 0),
            "이전 분석 결과 복원: 12개 영상 (인코딩 대상 0개, 소요 3.0초)",
        )
        self.assertEqual(
            format_restore_finished(12, 3.0, 1500),
            "이전 분석 결과 복원: 12개 영상 (인코딩 대상 1,500개, 소요 3.0초)",
        )


class ReasonTooltipTests(unittest.TestCase):
    """판정 사유 머리글 툴팁의 줄 구성을 검증"""

    def test_legacy_format_reason_has_a_one_line_description(self) -> None:
        """분석 형식 변경 사유가 표시 이름과 짧은 설명을 가진 한 줄로 나오는지 검증"""
        lines = format_reason_tooltip(ClassifierConfig()).split("\n")
        self.assertIn(
            f"• {SmartAction.LEGACY_FORMAT.reason_label}: "
            "분석 형식이 바뀌어 다시 분석하기 전까지 인코딩 대상에서 제외합니다.",
            lines,
        )


class KeyframeTooltipTests(unittest.TestCase):
    """키프레임 간격 머리글 툴팁이 남은 경고 아이콘을 가리키는지 검증"""

    def test_tooltip_refers_to_the_warning_icon_not_a_glyph(self) -> None:
        """표 셀 문자열의 경고 기호가 사라졌으므로 툴팁은 아이콘을 안내함을 검증"""
        tip = format_keyframe_tooltip(ClassifierConfig().thresholds)
        self.assertIn("경고 아이콘", tip)
        self.assertNotIn("⚠", tip)
        self.assertIn("2.2초", tip)


if __name__ == "__main__":
    unittest.main(verbosity=2)
