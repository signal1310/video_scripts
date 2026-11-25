"""품질값 정밀 결정 대상 선택 규칙 검증(결정 18)

- 적용 범위 설정(사용 안 함, 모든 대상, 선택 적용)과 비트레이트 경계 여유로 대상을 고르는지 확인
"""

import unittest
from typing import Any

from src.core.config import (
    PRECISE_QUALITY_MODE_ALL,
    PRECISE_QUALITY_MODE_OFF,
    PRECISE_QUALITY_MODE_SELECTIVE,
    ClassifierConfig,
)
from src.core.encode.precise_tune_policy import (
    PRECISE_TUNE_BOUNDARY_MARGIN,
    should_precise_tune,
)
from src.core.models import VideoEvaluation
from src.core.smart_action import SmartAction
from tests._factories import make_eval


class TestShouldPreciseTune(unittest.TestCase):
    """적용 범위 설정과 판정 경계 여유로 정밀 결정 대상을 고르는지 확인"""

    def _eval(self, **overrides: Any) -> VideoEvaluation:
        return make_eval(**overrides)

    def test_off_never_selects_and_all_selects_every_saving_target(self) -> None:
        ev = self._eval()
        config = ClassifierConfig()
        self.assertFalse(should_precise_tune(ev, PRECISE_QUALITY_MODE_OFF, config))
        self.assertFalse(should_precise_tune(ev, "unknown", config))
        self.assertTrue(should_precise_tune(ev, PRECISE_QUALITY_MODE_ALL, config))

    def test_keyframe_fix_and_non_encode_targets_are_excluded(self) -> None:
        config = ClassifierConfig()
        keyframe = self._eval(is_keyframe_exceeded=True, smart_action=SmartAction.ENCODE_OVER)
        self.assertFalse(should_precise_tune(keyframe, PRECISE_QUALITY_MODE_ALL, config))
        low_kf = self._eval(smart_action=SmartAction.ENCODE_LOW_KF, is_keyframe_exceeded=True)
        self.assertFalse(should_precise_tune(low_kf, PRECISE_QUALITY_MODE_ALL, config))
        passed = self._eval(smart_action=SmartAction.PASS_OPTIMAL, bitrate_ratio=1.0)
        self.assertFalse(should_precise_tune(passed, PRECISE_QUALITY_MODE_ALL, config))
        compat = self._eval(smart_action=SmartAction.ENCODE_COMPAT, bitrate_ratio=0.4)
        self.assertFalse(should_precise_tune(compat, PRECISE_QUALITY_MODE_ALL, config))

    def test_selective_takes_downscale_candidates_and_boundary_ratios(self) -> None:
        config = ClassifierConfig()
        margin = PRECISE_TUNE_BOUNDARY_MARGIN
        over = config.thresholds.over_bitrate_ratio
        low = config.thresholds.low_bitrate_ratio
        downscale = self._eval(smart_action=SmartAction.ENCODE_DOWNSCALE, bitrate_ratio=3.0)
        at_over = self._eval(bitrate_ratio=over + margin)
        at_low_edge = self._eval(bitrate_ratio=low + margin)
        inside = self._eval(bitrate_ratio=(over + low) / 2.0)
        for ev, expected in (
            (downscale, True),
            (at_over, True),
            (at_low_edge, True),
            (inside, False),
            (self._eval(bitrate_ratio=over + margin * 2), False),
            (self._eval(bitrate_ratio=low - margin * 2), False),
        ):
            with self.subTest(ratio=ev.bitrate_ratio, action=ev.smart_action):
                self.assertEqual(should_precise_tune(ev, PRECISE_QUALITY_MODE_SELECTIVE, config), expected)

    def test_boundary_margin_constant_is_the_documented_value(self) -> None:
        self.assertAlmostEqual(PRECISE_TUNE_BOUNDARY_MARGIN, 0.15)


if __name__ == "__main__":
    unittest.main(verbosity=2)
