"""표 모델의 재인코딩 상태 열이 배치 상태 저장소의 표기, 배지, 글자색, 툴팁을 그대로 보이는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from src.core.encode.encode_batch_book import EncodeBatchBook
from src.core.encode.encode_state import EncodeState
from src.core.workspace_paths import resolve_source_filepath
from src.gui.table.table_model import VideoTableModel
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_ROOT: str = os.path.join("work", "root")
_DISPLAY = int(Qt.ItemDataRole.DisplayRole)
_TOOLTIP = int(Qt.ItemDataRole.ToolTipRole)
_FOREGROUND = int(Qt.ItemDataRole.ForegroundRole)


class EncodeStateColumnModelTests(unittest.TestCase):
    """재인코딩 상태 열의 셀 값과 배치 상태 변경 신호를 검증"""

    def setUp(self) -> None:
        self.model = VideoTableModel()
        self.model.set_data_source(
            [make_eval(make_video(filename="a.mp4")), make_eval(make_video(filename="b.mp4"))],
            root_dir=_ROOT,
        )
        self.col = VideoTableModel.COL_ENCODE_STATE
        self.path_a = resolve_source_filepath(_ROOT, "a.mp4")
        self.book = EncodeBatchBook()
        self.book.start([self.path_a])

    def _index(self, row: int) -> QModelIndex:
        return self.model.index(row, self.col)

    def test_rows_show_nothing_before_a_batch_is_attached(self) -> None:
        """배치 저장소를 연결하기 전에는 상태 셀이 비어 있는지 검증"""
        self.assertIsNone(self.model.data(self._index(0), _DISPLAY))
        self.assertIsInstance(self.model.data(self._index(0), _TOOLTIP), str)

    def test_batch_target_shows_pending_label_with_muted_color_and_no_badge(self) -> None:
        """배치 대상 대기 행이 대기 표기와 보조 글자색을 쓰고 배지는 없는지 검증"""
        self.model.set_encode_book(self.book)
        self.assertEqual(self.model.data(self._index(0), _DISPLAY), "대기")
        self.assertIsNone(self.model.data(self._index(0), VideoTableModel.BADGE_ROLE))
        self.assertIsInstance(self.model.data(self._index(0), _FOREGROUND), QColor)
        self.assertEqual(self.model.data(self._index(0), _TOOLTIP), "재인코딩을 기다리고 있습니다.")

    def test_state_change_updates_label_badge_and_tooltip(self) -> None:
        """상태가 실패로 바뀌면 표기, 배지, 툴팁 문장이 함께 바뀌는지 검증"""
        self.book.apply(self.path_a, EncodeState.FAILED, "인코딩 실패(종료 코드 1)")
        self.model.set_encode_book(self.book)
        self.assertEqual(self.model.data(self._index(0), _DISPLAY), "실패")
        self.assertIsNotNone(self.model.data(self._index(0), VideoTableModel.BADGE_ROLE))
        self.assertIsNone(self.model.data(self._index(0), _FOREGROUND))
        self.assertEqual(
            self.model.data(self._index(0), _TOOLTIP),
            "재인코딩에 실패했습니다. 사유: 인코딩 실패(종료 코드 1).",
        )

    def test_row_outside_the_batch_has_no_label_and_keeps_the_row_tooltip(self) -> None:
        """배치 대상이 아닌 행은 상태가 비고, 툴팁은 행 전체 툴팁으로 남는지 검증"""
        self.model.set_encode_book(self.book)
        self.assertIsNone(self.model.data(self._index(1), _DISPLAY))
        self.assertIsNone(self.model.data(self._index(1), VideoTableModel.BADGE_ROLE))
        row_tip = self.model.data(self._index(1), _TOOLTIP)
        self.assertIsInstance(row_tip, str)
        self.assertNotIn("재인코딩을 기다리고 있습니다.", row_tip)

    def test_attaching_the_book_emits_a_change_for_the_state_column(self) -> None:
        """배치 저장소를 연결하면 상태 열 전체가 바뀌었다고 알리는지 검증"""
        changed: list[tuple[int, int]] = []
        self.model.dataChanged.connect(lambda top, bottom, _roles: changed.append((top.column(), bottom.row())))
        self.model.set_encode_book(self.book)
        self.assertEqual(changed, [(self.col, 1)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
