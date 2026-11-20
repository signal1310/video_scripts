"""이전 결과 폐기 기록 판정 검증: 같은 원본은 제외, 크기나 수정 시각이 다르면 대상, 직접 지정이면 대상, 기록이 없으면 대상"""

import unittest

from src.core.encode.encode_previous_discard import DiscardFact, is_previously_discarded

_SIZE: int = 1000
_MTIME: int = 5_000


class IsPreviouslyDiscardedTests(unittest.TestCase):
    """이전 폐기 기록과 이번 원본을 비교해 재인코딩 대상에서 뺄지 정함"""

    def setUp(self) -> None:
        self.fact = DiscardFact(_SIZE, _MTIME)

    def test_same_size_and_mtime_is_excluded(self) -> None:
        self.assertTrue(is_previously_discarded(_SIZE, _MTIME, self.fact, None))

    def test_changed_size_or_mtime_makes_the_source_a_target_again(self) -> None:
        self.assertFalse(is_previously_discarded(_SIZE + 1, _MTIME, self.fact, None))
        self.assertFalse(is_previously_discarded(_SIZE, _MTIME + 1, self.fact, None))

    def test_manual_target_is_a_target_even_with_the_same_source(self) -> None:
        self.assertFalse(is_previously_discarded(_SIZE, _MTIME, self.fact, "1080p"))
        self.assertFalse(is_previously_discarded(_SIZE, _MTIME, self.fact, "축소 안 함"))

    def test_no_record_is_a_target(self) -> None:
        self.assertFalse(is_previously_discarded(_SIZE, _MTIME, None, None))


if __name__ == "__main__":
    unittest.main()
