"""분석 방식(빠른, 정밀)별 하단 재분석 안내 개수, 예상 시간, 문구와 툴팁 계산 검증"""

import unittest

from src.core.analysis.reanalysis_notice import (
    build_reanalysis_notice_plan,
    count_keyframe_missing,
)
from tests._factories import make_eval, make_video


class ReanalysisNoticeTests(unittest.TestCase):
    """빠른 분석과 정밀 분석 모드별 재분석 안내 대상 수, 시간, 문구와 툴팁을 검증"""

    def test_count_keyframe_missing_counts_none_and_negative(self) -> None:
        evals = [
            make_eval(video=make_video(filename="a.mp4", keyframe_interval=1.0)),
            make_eval(video=make_video(filename="b.mp4", keyframe_interval=None)),
            make_eval(video=make_video(filename="c.mp4", keyframe_interval=-1.0)),
        ]
        self.assertEqual(count_keyframe_missing(evals), 2)

    def test_full_mode_combines_format_changed_and_keyframe_missing(self) -> None:
        plan = build_reanalysis_notice_plan(
            2,
            4,
            include_keyframe=True,
            fast_sec_per_item=5.0,
            full_sec_per_item=30.0,
        )
        self.assertEqual(plan.reanalyze_count, 6)
        self.assertEqual(plan.full_only_count, 0)
        self.assertEqual(plan.expected_minutes, 3)
        self.assertEqual(plan.text, "이 폴더의 영상 중 6개를 다시 분석해야 합니다. (예상 약 3분)")
        self.assertNotIn("빠른 분석 모드에서는", plan.tooltip)
        self.assertIn("키프레임 간격이 없는 영상 4개:", plan.tooltip)

    def test_fast_mode_with_both_counts_separates_fast_reanalyze_and_full_only(self) -> None:
        plan = build_reanalysis_notice_plan(
            3,
            2,
            include_keyframe=False,
            fast_sec_per_item=20.0,
            full_sec_per_item=60.0,
        )
        self.assertEqual(plan.reanalyze_count, 3)
        self.assertEqual(plan.full_only_count, 2)
        self.assertEqual(plan.expected_minutes, 1)
        self.assertEqual(
            plan.text,
            "이 폴더의 영상 중 3개를 다시 분석해야 합니다. (예상 약 1분) / 정밀 분석이 필요한 영상 2개",
        )
        self.assertIn("빠른 분석 모드에서는 분석 형식이 바뀐 영상만 다시 분석합니다.", plan.tooltip)
        self.assertIn(
            "키프레임 간격을 재지 않은 영상은 키프레임 교정 대상인지 알 수 없습니다. 정밀 분석을 하면 함께 판정합니다.",
            plan.tooltip,
        )

    def test_fast_mode_with_only_keyframe_missing_shows_full_analysis_needed(self) -> None:
        plan = build_reanalysis_notice_plan(
            0,
            4,
            include_keyframe=False,
            fast_sec_per_item=5.0,
            full_sec_per_item=30.0,
        )
        self.assertEqual(plan.reanalyze_count, 0)
        self.assertEqual(plan.full_only_count, 4)
        self.assertEqual(plan.expected_minutes, 2)
        self.assertEqual(plan.text, "정밀 분석이 필요한 영상 4개 (예상 약 2분)")
        self.assertIn("빠른 분석 모드에서는 분석 형식이 바뀐 영상만 다시 분석합니다.", plan.tooltip)

    def test_zero_counts_and_missing_timing_produce_expected_outputs(self) -> None:
        empty = build_reanalysis_notice_plan(
            0, 0, include_keyframe=False, fast_sec_per_item=1.0, full_sec_per_item=2.0
        )
        self.assertEqual((empty.reanalyze_count, empty.full_only_count, empty.text, empty.tooltip), (0, 0, "", ""))

        no_eta = build_reanalysis_notice_plan(
            0, 3, include_keyframe=False, fast_sec_per_item=None, full_sec_per_item=None
        )
        self.assertEqual(no_eta.text, "정밀 분석이 필요한 영상 3개")
        self.assertIsNone(no_eta.expected_minutes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
