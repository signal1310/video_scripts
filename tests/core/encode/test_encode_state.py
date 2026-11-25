"""인코딩 항목 상태 코드와 배치 상태 상수, 재개 대상 상태 묶음을 검증"""

import unittest

from src.core.encode.encode_state import (
    BATCH_STATE_CLOSED,
    BATCH_STATE_OPEN,
    UNFINISHED_STATES,
    EncodeState,
)


class EncodeStateTests(unittest.TestCase):
    """상태 코드 값과 재개 대상 묶음을 검증"""

    def test_stored_codes_are_the_persisted_english_values(self) -> None:
        """저장되는 상태 코드 값이 작업 기록의 영문 코드와 같은지 검증"""
        self.assertEqual(
            {state.value for state in EncodeState},
            {"pending", "running", "done", "discarded", "failed", "skipped", "replace_pending"},
        )

    def test_unfinished_states_are_pending_and_running_only(self) -> None:
        """재개 대상이 대기와 진행 중뿐이고 끝난 상태는 포함하지 않는지 검증"""
        self.assertEqual(UNFINISHED_STATES, frozenset({EncodeState.PENDING, EncodeState.RUNNING}))
        self.assertNotIn(EncodeState.REPLACE_PENDING, UNFINISHED_STATES)

    def test_batch_state_codes_are_open_and_closed(self) -> None:
        """배치 상태 코드가 열림과 닫힘 두 값인지 검증"""
        self.assertEqual(BATCH_STATE_OPEN, "open")
        self.assertEqual(BATCH_STATE_CLOSED, "closed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
