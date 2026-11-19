"""항목 실행 결과 값 검증: 기본값과 조기 중단 기록 보유"""

import unittest

from src.core.encode.encode_progress import EarlyStopHit
from src.core.encode.encode_state import EncodeState
from src.service.encode.encode_outcome import EncodeAttempt, ItemOutcome


class TestItemOutcome(unittest.TestCase):
    """항목 결과 값의 기본값과 조기 중단 기록"""

    def test_defaults_carry_no_early_stop_record(self) -> None:
        """사유, 로그, 조기 중단 기록은 기본적으로 비어 있음"""
        outcome = ItemOutcome(EncodeState.DONE)
        self.assertEqual(outcome.reason, "")
        self.assertIsNone(outcome.early_stop)

    def test_early_stop_record_is_kept_on_outcome_and_attempt(self) -> None:
        """조기 중단 기록은 실행 결과와 항목 결과에 그대로 담김"""
        hit = EarlyStopHit(progress=0.12, estimate_bytes=1300)
        attempt = EncodeAttempt(early_stop=hit)
        outcome = ItemOutcome(EncodeState.DISCARDED, "사유", early_stop=attempt.early_stop)
        self.assertEqual(outcome.early_stop, hit)
        self.assertFalse(attempt.cancelled)
        self.assertIsNone(attempt.failure)


if __name__ == "__main__":
    unittest.main(verbosity=2)
