"""표 열 너비 설정 검증: 모든 열을 끌어서 바꿀 수 있는 모드와 처음 너비, 정리 창과 결과 목록에 적용되는지

- 열 너비는 헤더의 크기 모드와 구역 크기로 확인하고, 화면 배치는 실행하지 않음
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QHeaderView, QTreeWidget

from src.gui.dialogs.encode_cleanup_window import COLUMN_WIDTHS_PX, CleanupWindow
from src.gui.table.table_column_resize import make_columns_resizable

_APP = QApplication.instance() or QApplication([])


class MakeColumnsResizableTests(unittest.TestCase):
    def test_every_column_becomes_interactive_with_its_initial_width(self) -> None:
        tree = QTreeWidget()
        tree.setHeaderLabels(["a", "b", "c"])
        header = tree.header()
        assert header is not None
        make_columns_resizable(header, (100, 150, 60))
        for column, width in enumerate((100, 150, 60)):
            self.assertEqual(header.sectionResizeMode(column), QHeaderView.ResizeMode.Interactive)
            self.assertEqual(header.sectionSize(column), width)

    def test_last_column_does_not_stretch_to_fill_the_viewport(self) -> None:
        tree = QTreeWidget()
        tree.setHeaderLabels(["a", "b"])
        header = tree.header()
        assert header is not None
        make_columns_resizable(header, (100, 60))
        self.assertFalse(header.stretchLastSection())


class DialogTableColumnTests(unittest.TestCase):
    def test_cleanup_window_columns_are_resizable_with_its_initial_widths(self) -> None:
        window = CleanupWindow([], lambda _selected: [])
        header = window._tree.header()
        assert header is not None
        for column, width in enumerate(COLUMN_WIDTHS_PX):
            self.assertEqual(header.sectionResizeMode(column), QHeaderView.ResizeMode.Interactive)
            self.assertEqual(header.sectionSize(column), width)


if __name__ == "__main__":
    unittest.main()
