"""줄바꿈 흐름 레이아웃 검증: 폭이 좁아지면 칸이 다음 줄로 넘어가 높이가 늘고, 칸 추가와 제거가 맞는지 확인"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from src.gui.dialogs.encode_flow_layout import FlowLayout
from src.gui.dialogs.encode_progress_chips import FilterChip

_APP = QApplication.instance() or QApplication([])


class FlowLayoutTests(unittest.TestCase):
    """칩은 폭이 좁아지면 다음 줄로 넘어가 높이가 늘어남"""

    def test_narrow_width_wraps_chips_onto_more_lines(self) -> None:
        host = QWidget()
        flow = FlowLayout(host)
        chips = [FilterChip(f"완료 {index}", host) for index in range(4)]
        for chip in chips:
            flow.addWidget(chip)
        wide = flow.heightForWidth(2000)
        narrow = flow.heightForWidth(chips[0].sizeHint().width() + 4)
        self.assertGreater(narrow, wide)
        self.assertEqual(flow.count(), 4)
        self.assertIsNotNone(flow.itemAt(0))
        self.assertIsNone(flow.itemAt(9))
        self.assertTrue(flow.hasHeightForWidth())
        self.assertEqual(flow.expandingDirections(), Qt.Orientation(0))
        host.setGeometry(0, 0, 2000, 200)
        flow.setGeometry(host.rect())
        self.assertGreaterEqual(flow.minimumSize().width(), chips[0].sizeHint().width())
        self.assertIsNotNone(flow.takeAt(0))
        self.assertEqual(flow.count(), 3)
        host.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
