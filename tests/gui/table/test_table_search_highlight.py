"""검색어 일치 구간 분할과 셀 강조 렌더링을 픽셀과 문자열로 검증"""

import os
import unittest
from typing import Any

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem, QTableView

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.table.delegates import IconTextDelegate
from src.gui.table.table_search_highlight import (
    SEARCH_QUERY_ATTRIBUTE,
    SearchHighlightDelegate,
    draw_text_with_search,
    paint_search_cell,
    search_query_of,
    split_search_segments,
)
from src.gui.theme import ThemeManager

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])

_BACKGROUND = "#ffffff"
_KEEP: list[QStandardItemModel] = []


def _index(text: str, roles: dict[int, Any] | None = None) -> Any:
    """한 칸짜리 기본 항목 모델을 만들어 그 인덱스를 돌려줌(테스트가 끝날 때까지 보관)"""
    model = QStandardItemModel(1, 1)
    item = QStandardItem(text)
    for role, value in (roles or {}).items():
        item.setData(value, role)
    model.setItem(0, 0, item)
    _KEEP.append(model)
    return model.index(0, 0)


def _option(width: int, height: int, widget: QTableView | None = None) -> QStyleOptionViewItem:
    """셀 크기와 뷰 위젯을 담은 그리기 옵션"""
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, width, height)
    option.font = QFont()
    if widget is not None:
        option.widget = widget
    return option


def _paint_text(text: str, query: str, *, palette: Any = DARK_PALETTE) -> QImage:
    """주어진 문자열을 강조 그리기로 그린 이미지"""
    image = QImage(240, 30, QImage.Format.Format_ARGB32)
    image.fill(QColor(_BACKGROUND))
    painter = QPainter(image)
    draw_text_with_search(
        painter,
        QRectF(0, 0, 240, 30),
        text,
        query=query,
        palette=palette,
        color=QColor(palette.text_primary),
    )
    painter.end()
    return image


def _has_color(image: QImage, hex_color: str) -> bool:
    """이미지에 주어진 색 픽셀이 하나라도 있는지"""
    return any(
        image.pixelColor(x, y).name() == hex_color
        for y in range(image.height())
        for x in range(image.width())
    )


class SplitSearchSegmentsTests(unittest.TestCase):
    """검색어 일치 구간 분할이 대소문자와 반복 일치를 다루는지 검증"""

    def test_empty_query_returns_single_unmatched_segment(self) -> None:
        self.assertEqual(split_search_segments("clip.mp4", ""), [("clip.mp4", False)])

    def test_matches_are_case_insensitive_and_keep_original_text(self) -> None:
        self.assertEqual(
            split_search_segments("H264 codec", "h264"),
            [("H264", True), (" codec", False)],
        )

    def test_every_occurrence_is_split(self) -> None:
        self.assertEqual(
            split_search_segments("aXbXc", "x"),
            [("a", False), ("X", True), ("b", False), ("X", True), ("c", False)],
        )

    def test_query_without_match_returns_single_segment(self) -> None:
        self.assertEqual(split_search_segments("clip.mp4", "zzz"), [("clip.mp4", False)])


class SearchQueryPropertyTests(unittest.TestCase):
    """뷰 속성에서 읽는 검색어가 없거나 문자열이 아니면 빈 값이 되는지 검증"""

    def test_missing_attribute_reads_as_empty(self) -> None:
        self.assertEqual(search_query_of(None), "")
        self.assertEqual(search_query_of(QTableView()), "")


class PaintSearchCellTests(unittest.TestCase):
    """표 뷰 속성 검색어에 따라 셀 강조 렌더링으로 대체하는지 검증"""

    def _paint(self, query: str, text: str) -> bool:
        """검색어를 담은 위젯으로 셀 한 칸을 그려 강조 렌더링으로 대체했는지 확인

        - 옵션은 위젯을 소유하지 않으므로 위젯을 여기서 계속 붙잡아 둠
        """
        view = QTableView()
        self.addCleanup(view.deleteLater)
        setattr(view, SEARCH_QUERY_ATTRIBUTE, query)
        image = QImage(200, 30, QImage.Format.Format_ARGB32)
        image.fill(QColor(_BACKGROUND))
        painter = QPainter(image)
        try:
            return paint_search_cell(painter, _option(200, 30, view), _index(text), DARK_PALETTE)
        finally:
            painter.end()

    def test_returns_true_when_value_matches_the_view_query(self) -> None:
        self.assertTrue(self._paint("1080", "clip1080.mp4"))

    def test_returns_false_without_query_or_match(self) -> None:
        self.assertFalse(self._paint("", "clip1080.mp4"))
        self.assertFalse(self._paint("zzz", "clip1080.mp4"))

    def test_empty_text_is_not_painted(self) -> None:
        self.assertFalse(self._paint("1080", ""))


class HighlightPaintingTests(unittest.TestCase):
    """일치 구간에만 팔레트 토큰 배경이 칠해지는지 픽셀로 검증"""

    def test_matched_segment_gets_badge_pending_background(self) -> None:
        image = _paint_text("clip1080.mp4", "1080")
        self.assertTrue(_has_color(image, QColor(DARK_PALETTE.badge_pending[0]).name()))

    def test_no_matching_query_keeps_plain_background(self) -> None:
        image = _paint_text("clip1080.mp4", "")
        self.assertFalse(_has_color(image, QColor(DARK_PALETTE.badge_pending[0]).name()))

    def test_light_palette_uses_its_own_token(self) -> None:
        image = _paint_text("clip1080.mp4", "1080", palette=LIGHT_PALETTE)
        self.assertTrue(_has_color(image, QColor(LIGHT_PALETTE.badge_pending[0]).name()))


class SearchHighlightDelegateTests(unittest.TestCase):
    """델리게이트가 강조 대상이 아니면 기본 렌더링으로 넘기는지 검증"""

    def test_paint_falls_back_to_default_rendering(self) -> None:
        delegate = SearchHighlightDelegate()
        image = QImage(120, 30, QImage.Format.Format_ARGB32)
        image.fill(QColor(_BACKGROUND))
        painter = QPainter(image)
        try:
            delegate.paint(painter, _option(120, 30), _index("clip1080.mp4"))
        finally:
            painter.end()
        self.assertEqual(image.size().width(), 120)


class ViewSearchHighlightTests(unittest.TestCase):
    """표 뷰가 프록시 검색어를 강조 속성으로 옮기고 기본 델리게이트로 강조를 그리는지 검증"""

    def _view(self) -> Any:
        from src.gui.table.table_model import VideoTableModel
        from src.gui.table.video_table_view import VideoTableView
        from tests.gui.table.test_column_sizing import _evaluations

        view = VideoTableView()
        self.addCleanup(view.close)
        model = view.table_model
        assert isinstance(model, VideoTableModel)
        model.set_data_source(_evaluations(2), root_dir="C:/work")
        return view

    def test_proxy_search_text_reaches_the_view_attribute(self) -> None:
        view = self._view()
        view.proxy_model.set_search_text("  clip  ")
        self.assertEqual(search_query_of(view), "clip")
        view.proxy_model.set_search_text("")
        self.assertEqual(search_query_of(view), "")

    def test_column_without_custom_delegate_highlights_through_the_view(self) -> None:
        from PyQt6.QtCore import Qt

        from src.gui.table.table_model import VideoTableModel

        view = self._view()
        index = view.table_model.index(0, VideoTableModel.COL_WIDTH)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        self.assertTrue(text)
        self.assertIsInstance(view.itemDelegate(), SearchHighlightDelegate)
        view.proxy_model.set_search_text(text[:2])
        image = QImage(120, 30, QImage.Format.Format_ARGB32)
        image.fill(QColor(_BACKGROUND))
        painter = QPainter(image)
        try:
            matched = paint_search_cell(painter, _option(120, 30, view), index, DARK_PALETTE)
        finally:
            painter.end()
        self.assertTrue(matched)


if __name__ == "__main__":
    unittest.main(verbosity=2)





class MatchedCellRowToneTests(unittest.TestCase):
    """검색어가 일치한 셀도 이웃 셀처럼 모델 행 배경을 그대로 칠하는지 검증"""

    def test_matched_cell_keeps_the_model_background(self) -> None:
        view = QTableView()
        self.addCleanup(view.deleteLater)
        setattr(view, SEARCH_QUERY_ATTRIBUTE, "zz")
        row_tone = "#3d2b0d"
        image = QImage(200, 30, QImage.Format.Format_ARGB32)
        image.fill(QColor(_BACKGROUND))
        painter = QPainter(image)
        try:
            painted = paint_search_cell(
                painter,
                _option(200, 30, view),
                _index("zz clip", {Qt.ItemDataRole.BackgroundRole: QColor(row_tone)}),
                DARK_PALETTE,
            )
        finally:
            painter.end()
        self.assertTrue(painted)
        self.assertEqual(image.pixelColor(195, 2).name(), row_tone)


class IconTextFallbackHighlightTests(unittest.TestCase):
    """아이콘 없는 값을 그리는 아이콘 글자 델리게이트도 검색어를 강조하는지 검증"""

    def test_plain_value_without_icon_is_highlighted(self) -> None:
        view = QTableView()
        self.addCleanup(view.deleteLater)
        setattr(view, SEARCH_QUERY_ATTRIBUTE, "1920")
        delegate = IconTextDelegate(view)
        image = QImage(200, 30, QImage.Format.Format_ARGB32)
        image.fill(QColor(_BACKGROUND))
        painter = QPainter(image)
        try:
            delegate.paint(painter, _option(200, 30, view), _index("1920x800"))
        finally:
            painter.end()
        self.assertTrue(_has_color(image, QColor(ThemeManager().current_palette.badge_pending[0]).name()))
