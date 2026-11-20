"""이번 실행 범위 검증: 시작 때 대기나 진행 중인 항목만 대상으로 세고, 이전 실행에서 끝난 항목과 건너뜀은 뺌"""

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
    """이번 실행의 대상과 끝난 수, 평균 속도의 길이 입력을 같은 범위로 맞춤"""

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
        self.assertEqual(self.scope.counts(after), (2, 1))

    def test_skipped_during_the_run_leaves_the_count(self) -> None:
        after = [
            ("a.mp4", EncodeState.DONE, ""),
            ("b.mp4", EncodeState.SKIPPED, ""),
            ("c.mp4", EncodeState.RUNNING, ""),
            ("d.mp4", EncodeState.SKIPPED, ""),
        ]
        self.assertEqual(self.scope.counts(after), (1, 0))

    def test_encoded_durations_skip_previous_runs_and_skipped_items(self) -> None:
        after = [
            ("a.mp4", EncodeState.DONE, ""),
            ("b.mp4", EncodeState.SKIPPED, ""),
            ("c.mp4", EncodeState.DONE, ""),
            ("d.mp4", EncodeState.DISCARDED, ""),
        ]
        durations = {"a.mp4": 600.0, "b.mp4": 600.0, "c.mp4": 60.0, "d.mp4": 30.0}
        self.assertEqual(self.scope.encoded_durations(after, durations), [60.0, 30.0])
        self.assertEqual(self.scope.encoded_durations(after, {}), [])


if __name__ == "__main__":
    unittest.main()
