"""재인코딩 시작 전 대기열 목록(encode_pending_list)의 채우기, 제외 체크, 테마 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QListWidget, QListWidgetItem

from src.gui.dialogs.encode_pending_list import PENDING_TITLE, PendingQueueList
from src.gui.styles.palettes import DARK_PALETTE

_APP = QApplication.instance() or QApplication([])


class PendingQueueListTests(unittest.TestCase):
    """대기열 목록이 체크 상태로 제외 목록을 만들고 팔레트를 적용하는지 검증"""

    def setUp(self) -> None:
        self.list = PendingQueueList()
        self.addCleanup(self.list.deleteLater)

    def _items(self) -> QListWidget:
        widget = self.list.findChild(QListWidget, "EncodePendingList")
        assert widget is not None
        return widget

    def _row(self, index: int) -> QListWidgetItem:
        """대기열 행 항목(없으면 테스트 실패)"""
        item = self._items().item(index)
        assert item is not None
        return item

    def test_paths_fill_rows_checked_and_excluded_stays_empty(self) -> None:
        """경로를 채우면 모두 체크되고 파일 이름만 보이며, 제외 목록은 비어 있음"""
        emitted: list[bool] = []
        self.list.exclusion_changed.connect(lambda: emitted.append(True))
        self.list.set_paths([os.path.join("C:/work", "a.mp4"), os.path.join("C:/work", "b.mp4")])
        self.assertEqual(self.list.count(), 2)
        self.assertEqual(self.list.names(), ["a.mp4", "b.mp4"])
        self.assertEqual(self.list.excluded_paths(), frozenset())
        self.assertEqual(len(emitted), 1)
        self.assertFalse(self._items().isHidden())
        self.assertEqual(self._row(0).toolTip(), "이번 재인코딩 대상입니다. 체크를 해제하면 이번 배치에서 제외합니다.")
        self.assertEqual(self._row(0).checkState(), Qt.CheckState.Checked)

    def test_unchecking_a_row_excludes_that_path(self) -> None:
        """체크를 해제한 영상만 제외 목록에 들어가고 변경 신호를 보냄"""
        self.list.set_paths([os.path.join("C:/work", "a.mp4"), os.path.join("C:/work", "b.mp4")])
        emitted: list[bool] = []
        self.list.exclusion_changed.connect(lambda: emitted.append(True))
        self._row(1).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(self.list.excluded_paths(), frozenset({os.path.join("C:/work", "b.mp4")}))
        self.assertEqual(emitted, [True])
        title = self.list.findChild(QLabel, "GroupHeaderTitle")
        assert title is not None
        self.assertEqual(title.text(), PENDING_TITLE)

    def test_clear_hides_the_list_and_palette_sets_tokens(self) -> None:
        """대기열을 비우면 목록이 숨고, 팔레트 적용이 목록 스타일에 토큰을 반영"""
        self.list.set_paths([os.path.join("C:/work", "a.mp4")])
        self.list.apply_palette(DARK_PALETTE)
        self.assertIn(DARK_PALETTE.bg_card, self._items().styleSheet())
        self.list.clear()
        self.assertEqual(self.list.count(), 0)
        self.assertTrue(self._items().isHidden())
        title = self.list.findChild(QLabel, "GroupHeaderTitle")
        assert title is not None
        self.assertTrue(title.isHidden())


if __name__ == "__main__":
    unittest.main(verbosity=2)
