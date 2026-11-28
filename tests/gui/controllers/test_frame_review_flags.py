"""여백 확인/구간별 여백 차이 표시 집합과 칩 필터 고정 목록 도우미 검증"""

import unittest

from src.core.models import ActiveFrame
from src.gui.controllers.frame_review_flags import (
    ACK_FULL,
    ACK_MANUAL,
    ACK_RESET,
    ReviewFlags,
    acknowledgement_kind,
)
from src.service.frame_analysis_service import CropReviewStatus


def _status(*, flagged: bool = False, vary: bool = False) -> CropReviewStatus:
    """확인 필요 표시만 지정한 상태 값"""
    return CropReviewStatus(flagged=flagged, hold_applies=flagged, margins_vary=vary)


class ReviewFlagsTests(unittest.TestCase):
    """집합 교체, 비우기, 확인 방법 분류 검증"""

    def setUp(self) -> None:
        self.flags = ReviewFlags()

    def test_starts_empty(self) -> None:
        self.assertEqual(self.flags.review, frozenset())
        self.assertEqual(self.flags.vary, frozenset())

    def test_set_and_clear_replace_both_sets(self) -> None:
        self.flags.set(frozenset({"a"}), frozenset({"b"}))
        self.assertEqual(self.flags.review, frozenset({"a"}))
        self.assertEqual(self.flags.vary, frozenset({"b"}))
        self.flags.clear()
        self.assertEqual(self.flags.review, frozenset())
        self.assertEqual(self.flags.vary, frozenset())

    def test_replace_splits_service_statuses(self) -> None:
        self.flags.replace(
            {
                "review.mp4": _status(flagged=True),
                "vary.mp4": _status(vary=True),
                "both.mp4": _status(flagged=True, vary=True),
                "none.mp4": _status(),
            }
        )
        self.assertEqual(self.flags.review, frozenset({"review.mp4", "both.mp4"}))
        self.assertEqual(self.flags.vary, frozenset({"vary.mp4", "both.mp4"}))

    def test_pinned_keeps_only_rows_that_were_flagged(self) -> None:
        self.flags.set(frozenset({"review.mp4"}), frozenset({"vary.mp4"}))
        pinned = self.flags.pinned(
            ["review.mp4", "vary.mp4", "plain.mp4"], kind=ACK_FULL
        )
        self.assertEqual(pinned, {"review.mp4": ACK_FULL, "vary.mp4": ACK_FULL})

    def test_acknowledgement_kind_maps_frame_to_manual_or_reset(self) -> None:
        self.assertEqual(ACK_RESET, acknowledgement_kind(None))
        self.assertEqual(ACK_MANUAL, acknowledgement_kind(ActiveFrame(1920, 1080, 0, 0)))


if __name__ == "__main__":
    unittest.main()
