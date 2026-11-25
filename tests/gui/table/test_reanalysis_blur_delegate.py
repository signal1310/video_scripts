"""재분석 필요 행의 파생 열 블러와 호버 해제 델리게이트 검증"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, QRect, Qt
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QMouseEvent,
    QPainter,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QTableView

from src.core.smart_action import SmartAction
from src.gui.table import reanalysis_blur_delegate as blur_mod
from src.gui.table.delegates import (
    ActiveFrameDelegate,
    IconTextDelegate,
    NameAccentDelegate,
    OutlineChipDelegate,
    PillBadgeDelegate,
    PlainTextDelegate,
)
from src.gui.table.reanalysis_blur_delegate import (
    BLURRED_DERIVED_COLS,
    BlurredZoneDelegate,
    install_table_delegates,
)
from src.gui.table.table_model import VideoTableModel
from src.gui.table.video_table_view import VideoTableView
from src.gui.theme import ThemeManager
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_WHITE = "#ffffff"


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
                idx = self.view.group_model.index(row, col)
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
        row1_rect = self.view.visualRect(self.view.group_model.index(1, VideoTableModel.COL_NAME))
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
        idx = self.view.group_model.index(1, VideoTableModel.COL_SAVED_SIZE)
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
            self.view.group_model.index(1, VideoTableModel.COL_NAME)
        ).center()
        row0_center = self.view.visualRect(
            self.view.group_model.index(0, VideoTableModel.COL_NAME)
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


class InstalledDelegateTests(unittest.TestCase):
    """열마다 단계별 델리게이트가 연결되고 행 강조 배경이 선택 상태를 지키는지 검증"""

    def setUp(self) -> None:
        self.view = VideoTableView()
        self.addCleanup(self.view.deleteLater)
        self.view.table_model.set_data_source(
            [make_eval(video=make_video(filename="normal.mp4"))], root_dir="C:/work"
        )

    def test_each_column_gets_the_expected_delegate(self) -> None:
        """알약, 점, 일반 글자, 칩, 알약 활성 화면, 아이콘 열의 델리게이트 종류를 검증"""
        expected = {
            VideoTableModel.COL_ACTION: PillBadgeDelegate,
            VideoTableModel.COL_ENCODE_STATE: PillBadgeDelegate,
            VideoTableModel.COL_ZONE: BlurredZoneDelegate,
            VideoTableModel.COL_REASON: PlainTextDelegate,
            VideoTableModel.COL_DOWNSCALE_CANDIDATE: OutlineChipDelegate,
            VideoTableModel.COL_ACTIVE_FRAME: ActiveFrameDelegate,
            VideoTableModel.COL_ROTATE: IconTextDelegate,
            VideoTableModel.COL_KEYFRAME: IconTextDelegate,
            VideoTableModel.COL_NAME: NameAccentDelegate,
        }
        for col, delegate_type in expected.items():
            with self.subTest(col=col):
                self.assertIsInstance(self.view.itemDelegateForColumn(col), delegate_type)

    def test_install_keeps_the_existing_name_delegate(self) -> None:
        """설치 함수가 뷰가 이미 둔 파일명 델리게이트를 교체하지 않는지 검증"""
        existing = self.view.name_delegate
        install_table_delegates(self.view)
        self.assertIs(
            self.view.name_delegate,
            self.view.itemDelegateForColumn(VideoTableModel.COL_NAME),
        )
        self.assertIs(self.view.name_delegate, existing)

    def test_install_returns_the_badge_icon_and_hover_parts(self) -> None:
        """설치 함수가 알약 델리게이트, 아이콘 델리게이트, 호버 추적기를 돌려주고 열에 연결하는지 검증"""
        table = QTableView()
        self.addCleanup(table.deleteLater)
        badge, icon_text, hover = install_table_delegates(table)
        self.assertIsInstance(badge, PillBadgeDelegate)
        self.assertIsInstance(icon_text, IconTextDelegate)
        self.assertIsInstance(hover, blur_mod.RowHoverTracker)
        self.assertIs(table.itemDelegateForColumn(VideoTableModel.COL_ACTION), badge)
        self.assertIs(table.itemDelegateForColumn(VideoTableModel.COL_ENCODE_STATE), badge)
        self.assertIs(table.itemDelegateForColumn(VideoTableModel.COL_KEYFRAME), icon_text)

    def test_blurred_cell_background_uses_the_row_tint(self) -> None:
        """블러 셀 배경이 선택, 호버가 아니면 행 강조색을, 아니면 교대 행 색을 쓰는지 검증"""
        palette = ThemeManager().current_palette
        tint = QColor("#123456")
        model = QStandardItemModel(2, 1)
        for row in range(2):
            model.setItem(row, 0, QStandardItem(f"row{row}"))
        model.setData(model.index(1, 0), tint, int(Qt.ItemDataRole.BackgroundRole))
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 40, 20)

        def paint(row: int) -> str:
            image = QImage(40, 20, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.white)
            painter = QPainter(image)
            try:
                blur_mod._paint_cell_background(
                    painter, option, model.index(row, 0), palette
                )
            finally:
                painter.end()
            return image.pixelColor(1, 1).name()

        self.assertEqual(paint(1), tint.name())
        model.setData(model.index(1, 0), None, int(Qt.ItemDataRole.BackgroundRole))
        self.assertEqual(paint(1), QColor(palette.bg_table_alt).name())
        # - 짝수 행은 교대 배경도 없어 아무것도 칠하지 않음
        self.assertEqual(paint(0), _WHITE)
        _KEEP_MODELS.append(model)


_KEEP_MODELS: list[QStandardItemModel] = []


if __name__ == "__main__":
    unittest.main(verbosity=2)
