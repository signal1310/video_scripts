"""재인코딩 진행 창 하단 패널의 전체 속도 칸과 결과 목록 표시를 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.encode.encode_progress_values import ResultLine
from src.gui.dialogs.encode_progress_panels import RESULT_HEADERS, ResultList, TotalsPanel

_APP = QApplication.instance() or QApplication([])


class TotalsPanelTests(unittest.TestCase):
    """전체 속도와 완료분 절약 칸이 받은 문구를 그대로 보여 주는지 검증"""

    def test_values_are_shown_as_given(self) -> None:
        panel = TotalsPanel()
        panel.set_values("현재 1.00배, 평균 0.50배", "실제 1.0 GB, 예상 1.2 GB")
        self.assertEqual(panel.speed_text(), "현재 1.00배, 평균 0.50배")
        self.assertEqual(panel.saving_text(), "실제 1.0 GB, 예상 1.2 GB")


class ResultListTests(unittest.TestCase):
    """결과 표 머리글과 줄, 파일 칸의 결과 경로 두 줄 표시를 검증"""

    def test_headers_rows_and_result_path_line(self) -> None:
        """표 머리글 네 칸과 줄 값이 맞고, 결과 경로가 있는 줄은 파일 칸에 두 줄로 나오는지 검증"""
        results = ResultList()
        self.assertEqual(results._table.columnCount(), len(RESULT_HEADERS))
        results.set_lines(
            [
                ResultLine("a.mp4", "결과: _재인코딩결과/a.mp4", "완료", "100.0 MB → 40.0 MB (-60%)", ""),
                ResultLine("b.mp4", "", "실패", "—", "인코딩 실패(종료 코드 1)"),
            ]
        )
        self.assertEqual(results.row_count(), 2)
        self.assertEqual(results.cell_text(0, 0), "a.mp4\n결과: _재인코딩결과/a.mp4")
        self.assertEqual(results.cell_text(1, 0), "b.mp4")
        self.assertEqual(results.cell_text(1, 3), "인코딩 실패(종료 코드 1)")
        results.set_lines([])
        self.assertEqual(results.row_count(), 0)
        self.assertEqual(results.cell_text(0, 0), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
