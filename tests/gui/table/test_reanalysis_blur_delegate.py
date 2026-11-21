"""재분석 필요 행의 파생 열 블러와 호버 해제 델리게이트 검증"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QCursor, QImage, QMouseEvent, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from src.core.smart_action import SmartAction
from src.gui.table import reanalysis_blur_delegate as blur_mod
from src.gui.table.reanalysis_blur_delegate import BLURRED_DERIVED_COLS
from src.gui.table.table_model import VideoTableModel
from src.gui.table.video_table_view import VideoTableView
from src.gui.theme import ThemeManager
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])


class ReanalysisBlurDelegateTests(unittest.TestCase):
    """이전 형식과 키프레임 미측정 행의 파생 열만 비호버 상태에서 블러로 그리는지 검증"""

    def setUp(self) -> None:
        self.view = VideoTableView()
        self.addCleanup(self.view.deleteLater)
        evals = [
            make_eval(
                video=make_video(filename="normal.mp4", keyframe_interval=1.0),
                smart_action=SmartAction.ENCODE_OVER,
            ),
            make_eval(
                video=make_video(filename="legacy.mp4", keyframe_interval=1.0),
                smart_action=SmartAction.LEGACY_FORMAT,
            ),
            make_eval(
                video=make_video(filename="fast.mp4", keyframe_interval=None),
                smart_action=SmartAction.PASS_OPTIMAL,
            ),
        ]
        self.view.table_model.set_data_source(evals, root_dir="C:/work")
        self.view.resize(1200, 300)

    def _paint_row(self, row: int) -> list[int]:
        blurred_cols: list[int] = []
        image = QImage(120, 32, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        orig = blur_mod.paint_blurred_cell

        def _spy(p: QPainter, opt: QStyleOptionViewItem, fn: object, *, col: int) -> None:
            blurred_cols.append(col)
            orig(p, opt, fn)  # type: ignore[arg-type]

        try:
            for col in BLURRED_DERIVED_COLS:
                idx = self.view.proxy_model.index(row, col)
                delegate = self.view.itemDelegateForColumn(col)
                assert delegate is not None
                opt = QStyleOptionViewItem()
                opt.rect = QRect(0, 0, 120, 32)
                with mock.patch.object(
                    blur_mod,
                    "paint_blurred_cell",
                    side_effect=lambda p, o, f, c=col: _spy(p, o, f, col=c),
                ):
                    delegate.paint(painter, opt, idx)
        finally:
            painter.end()
        return blurred_cols

    def test_blurred_only_for_reanalysis_rows_and_cleared_on_hover(self) -> None:
        self.assertEqual(self._paint_row(0), [])
        self.assertEqual(set(self._paint_row(1)), set(BLURRED_DERIVED_COLS))
        self.assertEqual(set(self._paint_row(2)), set(BLURRED_DERIVED_COLS))

        self.view._hover_tracker.set_hovered_row(1)
        self.assertEqual(self._paint_row(1), [])
        self.assertEqual(set(self._paint_row(2)), set(BLURRED_DERIVED_COLS))

        self.view._hover_tracker.set_hovered_row(-1)
        self.assertEqual(set(self._paint_row(1)), set(BLURRED_DERIVED_COLS))

    def test_hover_tracker_updates_row_on_viewport_mouse_move_and_leave(self) -> None:
        self.view.show()
        _APP.processEvents()
        row1_rect = self.view.visualRect(self.view.proxy_model.index(1, VideoTableModel.COL_NAME))
        move_event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(row1_rect.center()),
            QPointF(row1_rect.center()),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.view._hover_tracker.eventFilter(self.view.viewport(), move_event)
        self.assertEqual(self.view._hover_tracker.hovered_row, 1)

        self.view._hover_tracker.eventFilter(self.view.viewport(), QEvent(QEvent.Type.Leave))
        self.assertEqual(self.view._hover_tracker.hovered_row, -1)

    def test_selected_blurred_value_cell_uses_primary_text_color(self) -> None:
        idx = self.view.proxy_model.index(1, VideoTableModel.COL_SAVED_SIZE)
        delegate = self.view.itemDelegateForColumn(VideoTableModel.COL_SAVED_SIZE)
        assert delegate is not None
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 120, 32)
        opt.state |= QStyle.StateFlag.State_Selected
        pen_colors: list[QColor] = []

        def _capture_pen(_p: QPainter, _o: QStyleOptionViewItem, fn: object) -> None:
            pix = QPixmap(120, 32)
            buf_p = QPainter(pix)
            try:
                fn(buf_p, QRect(0, 0, 120, 32))  # type: ignore[operator]
                pen_colors.append(buf_p.pen().color())
            finally:
                buf_p.end()

        image = QImage(120, 32, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        try:
            with mock.patch.object(blur_mod, "paint_blurred_cell", side_effect=_capture_pen):
                delegate.paint(painter, opt, idx)
        finally:
            painter.end()

        expected = QColor(ThemeManager().current_palette.text_primary)
        self.assertEqual(pen_colors, [expected])

    def test_hover_tracker_refreshes_on_model_reset_and_layout_change(self) -> None:
        self.view.show()
        _APP.processEvents()
        viewport = self.view.viewport()
        assert viewport is not None
        row1_center = self.view.visualRect(
            self.view.proxy_model.index(1, VideoTableModel.COL_NAME)
        ).center()
        row0_center = self.view.visualRect(
            self.view.proxy_model.index(0, VideoTableModel.COL_NAME)
        ).center()

        self.view._hover_tracker.set_hovered_row(1)
        with mock.patch.object(QCursor, "pos", return_value=viewport.mapToGlobal(row0_center)):
            self.view.proxy_model.layoutChanged.emit()
        self.assertEqual(self.view._hover_tracker.hovered_row, 0)

        outside = viewport.mapToGlobal(row1_center)
        outside.setX(outside.x() - 1000)
        with mock.patch.object(QCursor, "pos", return_value=outside):
            self.view.table_model.set_data_source([], root_dir="C:/work")
        self.assertEqual(self.view._hover_tracker.hovered_row, -1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
