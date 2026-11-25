"""표 공용 행 구간 도우미 검증"""

import unittest

from src.gui.table.row_spans import contiguous_row_ranges


class ContiguousRowRangesTests(unittest.TestCase):
    """행 번호 목록이 연속 구간으로 묶이는지 검증"""

    def test_contiguous_rows_are_grouped_into_ranges(self) -> None:
        """행 번호가 연속 구간으로 묶이고 빈 목록은 빈 결과인지 검증"""
        self.assertEqual(contiguous_row_ranges([]), [])
        self.assertEqual(contiguous_row_ranges([4]), [(4, 4)])
        self.assertEqual(contiguous_row_ranges([7, 1, 2, 3, 9]), [(1, 3), (7, 7), (9, 9)])


if __name__ == "__main__":
    unittest.main()
