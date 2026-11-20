"""이번 실행 범위 검증: 시작 때 대기나 진행 중인 항목만 대상으로 세고, 이전 실행에서 끝난 항목과 건너뜀은 뺌. 인코딩을 시작하지 않고 끝난 항목은 대상과 끝난 수에서 모두 뺌"""

import unittest

from src.core.encode.encode_progress_run import RunScope
from src.core.encode.encode_state import EncodeState

_ITEMS_AT_START = [
    ("a.mp4", EncodeState.DONE, ""),
    ("b.mp4", EncodeState.SKIPPED, ""),
    ("c.mp4", EncodeState.PENDING, ""),
    ("d.mp4", EncodeState.PENDING, ""),
]


class RunScopeTests(unittest.TestCase):
    """이번 실행의 대상과 끝난 수, 평균 속도의 처리한 길이를 같은 범위로 맞춤"""

    def setUp(self) -> None:
        self.scope = RunScope()
        self.scope.start(_ITEMS_AT_START)

    def test_only_items_pending_or_running_at_start_count_as_this_run(self) -> None:
        after = [
            ("a.mp4", EncodeState.DONE, ""),
            ("b.mp4", EncodeState.SKIPPED, ""),
            ("c.mp4", EncodeState.DONE, ""),
            ("d.mp4", EncodeState.RUNNING, ""),
        ]
        self.assertEqual(self.scope.counts(after, {"c.mp4": 30.0, "d.mp4": 10.0}), (2, 1))

    def test_skipped_during_the_run_leaves_the_count(self) -> None:
        after = [
            ("a.mp4", EncodeState.DONE, ""),
            ("b.mp4", EncodeState.SKIPPED, ""),
            ("c.mp4", EncodeState.RUNNING, ""),
            ("d.mp4", EncodeState.SKIPPED, ""),
        ]
        self.assertEqual(self.scope.counts(after, {"c.mp4": 10.0}), (1, 0))

    def test_item_that_failed_before_starting_leaves_the_count(self) -> None:
        """진행 이벤트 없이 실패한 항목은 대상 수와 끝난 수에서 모두 빠짐"""
        after = [
            ("c.mp4", EncodeState.FAILED, ""),
            ("d.mp4", EncodeState.RUNNING, ""),
        ]
        self.assertEqual(self.scope.counts(after, {"d.mp4": 0.0}), (1, 0))

    def test_encoded_positions_use_the_position_each_item_reached(self) -> None:
        """이번 실행에서 인코딩을 거친 항목은 원본 길이가 아니라 마지막 진행 위치를 씀. 이전 실행과 건너뜀, 진행 중 항목은 뺌"""
        after = [
            ("a.mp4", EncodeState.DONE, ""),
            ("b.mp4", EncodeState.SKIPPED, ""),
            ("c.mp4", EncodeState.DISCARDED, ""),
            ("d.mp4", EncodeState.RUNNING, ""),
        ]
        positions = {"a.mp4": 600.0, "b.mp4": 600.0, "c.mp4": 40.0, "d.mp4": 30.0}
        self.assertEqual(self.scope.encoded_positions(after, positions), [40.0])
        self.assertEqual(self.scope.encoded_positions(after, {}), [])


if __name__ == "__main__":
    unittest.main()
