"""재분석 필요 행의 파생 열 블러 대상 판정 순수 함수 검증"""

import unittest

from src.core.evaluation.reanalysis_display import is_reanalysis_blurred
from src.core.smart_action import SmartAction
from tests._factories import make_eval, make_video


class ReanalysisDisplayTests(unittest.TestCase):
    """이전 형식 또는 키프레임 미측정 행만 파생 열 블러 대상으로 판별하는지 검증"""

    def test_legacy_format_is_blurred_even_with_measured_keyframe(self) -> None:
        ev = make_eval(
            video=make_video(keyframe_interval=1.0),
            smart_action=SmartAction.LEGACY_FORMAT,
        )
        self.assertTrue(is_reanalysis_blurred(ev))

    def test_unmeasured_or_negative_keyframe_is_blurred(self) -> None:
        self.assertTrue(is_reanalysis_blurred(make_eval(video=make_video(keyframe_interval=None))))
        self.assertTrue(is_reanalysis_blurred(make_eval(video=make_video(keyframe_interval=-1.0))))

    def test_current_row_with_measured_keyframe_is_not_blurred(self) -> None:
        ev = make_eval(
            video=make_video(keyframe_interval=1.0),
            smart_action=SmartAction.ENCODE_OVER,
        )
        self.assertFalse(is_reanalysis_blurred(ev))


if __name__ == "__main__":
    unittest.main(verbosity=2)
