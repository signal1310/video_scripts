"""표시 값 계산과 정렬 비교 키 공용 모듈 검증"""

import unittest

from src.core.config import ClassifierConfig
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY
from src.gui.table.table_display_values import comparable_sort_key, display_text_for
from src.gui.table.table_model import VideoTableModel
from tests._factories import make_eval, make_video


class TableDisplayValuesTests(unittest.TestCase):
    """표시 값과 정렬 비교 키의 경계 조건 검증"""

    def test_display_text_prefers_pending_output_tier(self) -> None:
        ev = make_eval(video=make_video(filename="a.mp4"))
        tier = RESOLUTION_TIER_BY_KEY["720p"]
        self.assertEqual(
            display_text_for(ev, VideoTableModel.COL_OUTPUT_RES, ClassifierConfig(), {"a.mp4": tier}),
            "720p",
        )
        self.assertEqual(
            display_text_for(ev, VideoTableModel.COL_OUTPUT_RES, ClassifierConfig(), {"a.mp4": None}),
            "-",
        )
        self.assertIsNone(display_text_for(ev, 999, ClassifierConfig(), {}))

    def test_display_text_uses_column_formatter_and_numeric_format(self) -> None:
        ev = make_eval(video=make_video(filename="a.mp4"))
        cfg = ClassifierConfig()
        self.assertEqual(display_text_for(ev, VideoTableModel.COL_WIDTH, cfg, {}), "1920")
        self.assertEqual(display_text_for(ev, VideoTableModel.COL_FPS, cfg, {}), "30")

    def test_comparable_sort_key_wraps_missing_and_mixed_tokens(self) -> None:
        self.assertEqual(comparable_sort_key(None), (0,))
        self.assertEqual(comparable_sort_key([2, "a"]), (1, ((0, 2), (1, "a"))))
        self.assertEqual(comparable_sort_key("x"), (1, "x"))


if __name__ == "__main__":
    unittest.main()
