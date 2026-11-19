"""PillBadgeDelegate, IconTextDelegate의 픽셀 렌더링 검증"""

import os
import unittest
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QModelIndex, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QImage, QPainter, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from src.gui.constants import FONT_TABLE_BADGE_PT, FONT_UI_FAMILY
from src.gui.table.delegates import IconTextDelegate, PillBadgeDelegate
from src.gui.table_model import VideoTableModel
from src.gui.theme import ThemeManager

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])

_BG, _FG, _BORDER = "#ff0000", "#ffffff", "#00ff00"
_WHITE = "#ffffff"
_ICON_BLUE = "#0000ff"
_ICON_PX = 12
_BORDER_PX = 2  # 알약 테두리가 안팎으로 1px씩 번져 측정 영역이 커짐
_Box = tuple[int, int, int, int]


def _index(text: str, roles: dict[int, Any]) -> QModelIndex:
    model = QStandardItemModel(1, 1)
    item = QStandardItem(text)
    for role, value in roles.items():
        item.setData(value, role)
    model.setItem(0, 0, item)
    _KEEP.append(model)
    return model.index(0, 0)


_KEEP: list[QStandardItemModel] = []


def _paint(
    delegate: QStyledItemDelegate,
    index: QModelIndex,
    width: int,
    height: int,
    *,
    state: QStyle.StateFlag = QStyle.StateFlag.State_Enabled,
) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(_WHITE))
    painter = QPainter(image)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, width, height)
    option.font = QFont()
    option.state = state
    delegate.paint(painter, option, index)
    painter.end()
    return image


def _bbox(image: QImage, match: Callable[[QColor], bool]) -> _Box | None:
    xs: list[int] = []
    ys: list[int] = []
    for y in range(image.height()):
        for x in range(image.width()):
            if match(image.pixelColor(x, y)):
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _is(hex_color: str) -> Callable[[QColor], bool]:
    return lambda color: color.name() == hex_color


def _solid_icon(hex_color: str) -> QIcon:
    pixmap = QPixmap(_ICON_PX, _ICON_PX)
    pixmap.fill(QColor(hex_color))
    return QIcon(pixmap)


class TestPillBadgeDelegate(unittest.TestCase):
    """뱃지 정보가 있으면 중앙 정렬 알약을 그리고, 없으면 기본 렌더링으로 위임함을 검증"""

    def setUp(self) -> None:
        """뱃지 정보가 담긴 인덱스와 델리게이트를 준비"""
        self.delegate = PillBadgeDelegate()
        self.badge = _index("CQ22.0 재인코딩", {int(VideoTableModel.BADGE_ROLE): (_BG, _FG, _BORDER)})

    def test_pill_is_centered_with_capped_height(self) -> None:
        """알약이 셀 가로, 세로 중앙에 놓이고 높이는 22px로 제한됨을 검증"""
        image = _paint(self.delegate, self.badge, 200, 40)
        box = _bbox(image, lambda c: c.name() != _WHITE)
        assert box is not None
        left, top, right, bottom = box
        self.assertLessEqual(abs(left - (199 - right)), 2)
        self.assertLessEqual(abs(top - (39 - bottom)), 1)
        self.assertEqual(bottom - top + 1, 22 + _BORDER_PX)
        self.assertIn(_BG, {image.pixelColor(left + 6, 20).name(), image.pixelColor(left + 7, 20).name()})

    def test_pill_width_follows_text_and_corners_are_rounded(self) -> None:
        """알약 폭이 글자 폭에 좌우 안쪽 여백 10px씩을 더한 값이고, 모서리는 둥글게 비어 있음을 검증"""
        font = QFont(FONT_UI_FAMILY, FONT_TABLE_BADGE_PT, QFont.Weight.Bold)
        text_width = QFontMetrics(font).horizontalAdvance("CQ22.0 재인코딩")
        image = _paint(self.delegate, self.badge, 300, 40)
        box = _bbox(image, lambda c: c.name() != _WHITE)
        assert box is not None
        self.assertEqual(box[2] - box[0] + 1, text_width + 20 + _BORDER_PX)
        self.assertEqual(image.pixelColor(box[0], box[1]).name(), _WHITE)

    def test_long_badge_text_is_elided_inside_the_pill(self) -> None:
        """좁은 셀에서 글자가 알약 밖으로 나가지 않도록 말줄임함을 검증"""
        long_badge = _index("아주 긴 뱃지 텍스트 문구입니다", {int(VideoTableModel.BADGE_ROLE): (_BG, "#000000", _BORDER)})
        image = _paint(self.delegate, long_badge, 80, 30)
        pill = _bbox(image, lambda c: c.name() != _WHITE)
        text = _bbox(image, _is("#000000"))
        assert pill is not None and text is not None
        self.assertGreaterEqual(text[0], pill[0] + 5)
        self.assertLessEqual(text[2], pill[2] - 5)

    def test_pill_height_and_width_shrink_to_cell(self) -> None:
        """셀이 작으면 여백 8px을 남기도록 알약 높이와 폭이 줄어듦을 검증"""
        short = _paint(self.delegate, self.badge, 200, 20)
        box = _bbox(short, lambda c: c.name() != _WHITE)
        assert box is not None
        self.assertEqual(box[3] - box[1] + 1, 12 + _BORDER_PX)

        narrow = _paint(self.delegate, self.badge, 50, 30)
        box = _bbox(narrow, lambda c: c.name() != _WHITE)
        assert box is not None
        self.assertEqual(box[2] - box[0] + 1, 42 + _BORDER_PX)

    def test_state_background_is_painted_behind_pill(self) -> None:
        """선택, 호버 상태의 배경색이 알약 뒤 셀 전체에 칠해짐을 검증"""
        palette = ThemeManager().current_palette
        selected = _paint(self.delegate, self.badge, 200, 40, state=QStyle.StateFlag.State_Selected)
        hovered = _paint(self.delegate, self.badge, 200, 40, state=QStyle.StateFlag.State_MouseOver)
        self.assertEqual(selected.pixelColor(1, 1).name(), QColor(palette.bg_selected).name())
        self.assertEqual(hovered.pixelColor(1, 1).name(), QColor(palette.bg_hover).name())
        self.assertEqual(_paint(self.delegate, self.badge, 200, 40).pixelColor(1, 1).name(), _WHITE)

    def test_without_badge_data_falls_back_to_default_painting(self) -> None:
        """뱃지 정보나 텍스트가 없으면 알약을 그리지 않고 기본 델리게이트로 렌더링함을 검증"""
        plain = _index("텍스트", {})
        empty = _index("", {int(VideoTableModel.BADGE_ROLE): (_BG, _FG, _BORDER)})
        for index in (plain, empty):
            image = _paint(self.delegate, index, 200, 40)
            self.assertIsNone(_bbox(image, _is(_BG)))

    def test_paint_ignores_missing_painter(self) -> None:
        """painter가 None이면 아무것도 하지 않음을 검증"""
        self.delegate.paint(None, QStyleOptionViewItem(), self.badge)


class TestIconTextDelegate(unittest.TestCase):
    """텍스트와 아이콘을 나란히 중앙에 배치하고, 아이콘이 없으면 기본 렌더링으로 위임함을 검증"""

    def setUp(self) -> None:
        """라이트 테마로 고정한 뒤 파란 아이콘이 있는 인덱스와 델리게이트를 준비"""
        mgr = ThemeManager()
        self.addCleanup(mgr.set_mode, mgr.mode)
        mgr.set_mode(ThemeManager.MODE_LIGHT)
        self.delegate = IconTextDelegate()
        self.index = _index("12.5", {int(Qt.ItemDataRole.DecorationRole): _solid_icon(_ICON_BLUE)})

    def test_icon_is_vertically_centered_right_of_text_and_group_is_centered(self) -> None:
        """아이콘이 글자 오른쪽에 12px로 놓이고 글자와 아이콘 묶음이 셀 중앙에 위치함을 검증"""
        image = _paint(self.delegate, self.index, 200, 30)
        icon = _bbox(image, _is(_ICON_BLUE))
        text = _bbox(image, lambda c: c.name() != _WHITE and c.name() != _ICON_BLUE and c.lightness() < 128)
        assert icon is not None and text is not None
        self.assertEqual((icon[2] - icon[0] + 1, icon[3] - icon[1] + 1), (_ICON_PX, _ICON_PX))
        self.assertEqual(icon[1], (30 - _ICON_PX) // 2)
        self.assertGreater(icon[0], text[2])
        self.assertLessEqual(abs(text[0] - (199 - icon[2])), 3)

    def test_long_text_is_elided_and_icon_stays_inside_cell(self) -> None:
        """좁은 셀에서는 글자를 말줄임하고 아이콘은 셀 밖으로 나가지 않음을 검증"""
        long_index = _index("아주 긴 텍스트 값 1234567890", {int(Qt.ItemDataRole.DecorationRole): _solid_icon(_ICON_BLUE)})
        image = _paint(self.delegate, long_index, 70, 30)
        icon = _bbox(image, _is(_ICON_BLUE))
        assert icon is not None
        self.assertLessEqual(icon[2], 69)
        self.assertGreaterEqual(icon[0], 4)
        text = _bbox(image, lambda c: c.name() != _WHITE and c.name() != _ICON_BLUE and c.lightness() < 128)
        assert text is not None
        self.assertGreaterEqual(text[0], 4)
        self.assertLess(text[2], icon[0])

    def test_foreground_role_colors_text(self) -> None:
        """ForegroundRole 색상이 글자색으로 쓰이고, 없으면 팔레트 기본 글자색을 씀을 검증"""
        colored = _index(
            "12.5",
            {
                int(Qt.ItemDataRole.DecorationRole): _solid_icon(_ICON_BLUE),
                int(Qt.ItemDataRole.ForegroundRole): QColor("#ff0000"),
            },
        )
        red = _bbox(_paint(self.delegate, colored, 200, 30), lambda c: c.red() > 200 and c.green() < 80 and c.blue() < 80)
        self.assertIsNotNone(red)

        default = _paint(self.delegate, self.index, 200, 30)
        self.assertIsNone(_bbox(default, lambda c: c.red() > 200 and c.green() < 80 and c.blue() < 80))

    def test_state_background_and_fallback_without_icon(self) -> None:
        """선택 배경이 칠해지고, 아이콘이 없으면 아이콘 없이 기본 렌더링함을 검증"""
        palette = ThemeManager().current_palette
        selected = _paint(self.delegate, self.index, 200, 30, state=QStyle.StateFlag.State_Selected)
        self.assertEqual(selected.pixelColor(1, 1).name(), QColor(palette.bg_selected).name())

        no_icon = _paint(self.delegate, _index("12.5", {}), 200, 30)
        self.assertIsNone(_bbox(no_icon, _is(_ICON_BLUE)))
        null_icon = _paint(self.delegate, _index("12.5", {int(Qt.ItemDataRole.DecorationRole): QIcon()}), 200, 30)
        self.assertIsNone(_bbox(null_icon, _is(_ICON_BLUE)))

    def test_paint_ignores_missing_painter(self) -> None:
        """painter가 None이면 아무것도 하지 않음을 검증"""
        self.delegate.paint(None, QStyleOptionViewItem(), self.index)


if __name__ == "__main__":
    unittest.main()
