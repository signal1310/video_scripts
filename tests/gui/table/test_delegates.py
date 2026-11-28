"""PillBadgeDelegate, IconTextDelegate의 픽셀 렌더링 검증"""

import os
import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

from PyQt6.QtCore import QModelIndex, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QImage, QPainter, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from src.gui.constants import FONT_TABLE_BADGE_PT, FONT_UI_FAMILY
from src.gui.table.delegates import (
    ActiveFrameDelegate,
    IconTextDelegate,
    NameAccentDelegate,
    OutlineChipDelegate,
    PillBadgeDelegate,
    PlainTextDelegate,
    QuietDotDelegate,
)
from src.gui.table.frame_analysis_columns import (
    ACTIVE_FRAME_KEEP_FULL_TEXT,
    ACTIVE_FRAME_VR_TEXT,
    DOWNSCALE_KEEP_TEXT,
)
from src.gui.table.table_model import VideoTableModel
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
        self.badge = _index("품질 22.0 인코딩", {int(VideoTableModel.BADGE_ROLE): (_BG, _FG, _BORDER)})

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
        text_width = QFontMetrics(font).horizontalAdvance("품질 22.0 인코딩")
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

    def test_icon_is_vertically_centered_left_of_text_and_group_is_centered(self) -> None:
        """아이콘이 글자 왼쪽에 12px로 놓이고 글자와 아이콘 묶음이 셀 중앙에 위치함을 검증"""
        image = _paint(self.delegate, self.index, 200, 30)
        icon = _bbox(image, _is(_ICON_BLUE))
        text = _bbox(image, lambda c: c.name() != _WHITE and c.name() != _ICON_BLUE and c.lightness() < 128)
        assert icon is not None and text is not None
        self.assertEqual((icon[2] - icon[0] + 1, icon[3] - icon[1] + 1), (_ICON_PX, _ICON_PX))
        self.assertEqual(icon[1], (30 - _ICON_PX) // 2)
        self.assertLess(icon[2], text[0])
        self.assertLessEqual(abs(icon[0] - (199 - text[2])), 3)

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
        self.assertLess(icon[2], text[0])
        self.assertLessEqual(text[2], 69)

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


class TestRowTintDelegates(unittest.TestCase):
    """행 배경색, 왼쪽 강조 띠, 점, 테두리 칩, 활성 화면 알약 렌더링을 검증"""

    def setUp(self) -> None:
        """라이트 테마로 고정해 팔레트 토큰을 확정"""
        mgr = ThemeManager()
        self.addCleanup(mgr.set_mode, mgr.mode)
        mgr.set_mode(ThemeManager.MODE_LIGHT)
        self.palette = ThemeManager().current_palette

    def test_row_background_role_is_painted_under_badge(self) -> None:
        """BackgroundRole 행 배경이 선택, 호버가 아닐 때 알약 뒤에 칠해짐을 검증"""
        tint = "#123456"
        index = _index(
            "진행 중",
            {
                int(VideoTableModel.BADGE_ROLE): (_BG, _FG, _BORDER),
                int(Qt.ItemDataRole.BackgroundRole): QColor(tint),
            },
        )
        image = _paint(PillBadgeDelegate(), index, 200, 40)
        self.assertEqual(image.pixelColor(1, 1).name(), tint)

        selected = _paint(
            PillBadgeDelegate(), index, 200, 40, state=QStyle.StateFlag.State_Selected
        )
        self.assertEqual(
            selected.pixelColor(1, 1).name(), QColor(self.palette.bg_selected).name()
        )

    def test_accent_bar_is_painted_at_the_left_edge(self) -> None:
        """ACCENT_ROLE 띠가 파일명 셀 왼쪽에 4px로 그려지고 없으면 그리지 않음을 검증"""
        accent = "#00ff00"
        with_accent = _index("clip.mp4", {VideoTableModel.ACCENT_ROLE: QColor(accent)})
        image = _paint(NameAccentDelegate(), with_accent, 200, 30)
        bar = _bbox(image, _is(accent))
        assert bar is not None
        self.assertEqual((bar[0], bar[2]), (0, 3))
        self.assertEqual(bar[3] - bar[1] + 1, 30)

        without = _paint(NameAccentDelegate(), _index("clip.mp4", {}), 200, 30)
        self.assertIsNone(_bbox(without, _is(accent)))

    def test_accent_bar_survives_row_selection(self) -> None:
        """선택 행에서도 기본 렌더링 뒤에 띠를 덧그려 보이게 함을 검증"""
        accent = "#00ff00"
        index = _index("clip.mp4", {VideoTableModel.ACCENT_ROLE: QColor(accent)})
        image = _paint(
            NameAccentDelegate(), index, 200, 30, state=QStyle.StateFlag.State_Selected
        )
        bar = _bbox(image, _is(accent))
        assert bar is not None
        self.assertEqual((bar[0], bar[2]), (0, 3))

    def test_name_accent_delegate_ignores_missing_painter(self) -> None:
        """painter가 None이면 띠를 그리지 않음을 검증"""
        index = _index("clip.mp4", {VideoTableModel.ACCENT_ROLE: QColor("#00ff00")})
        NameAccentDelegate().paint(None, QStyleOptionViewItem(), index)

    def test_quiet_dot_draws_dot_and_text_in_badge_color(self) -> None:
        """화질 등급 점과 글자가 배지 글자색으로 칠해짐을 검증"""
        index = _index("적정", {int(VideoTableModel.BADGE_ROLE): (_BG, "#0000ff", _BORDER)})
        image = _paint(QuietDotDelegate(), index, 200, 30)
        dot = _bbox(image, _is("#0000ff"))
        assert dot is not None
        self.assertGreaterEqual(dot[0], 4)
        self.assertLessEqual(dot[0] + 8, 100)

    def test_quiet_dot_falls_back_without_badge_or_text(self) -> None:
        """배지나 표시 문자열이 없으면 기본 렌더링으로 넘김을 검증"""
        badge = {int(VideoTableModel.BADGE_ROLE): (_BG, "#0000ff", _BORDER)}
        for index in (_index("적정", {}), _index("", badge)):
            image = _paint(QuietDotDelegate(), index, 200, 30)
            self.assertIsNone(_bbox(image, _is("#0000ff")))

    def test_plain_text_uses_foreground_then_badge_then_secondary(self) -> None:
        """일반 글자 델리게이트가 글자색 우선순위를 지키는지 검증"""
        rgb_case = _index(
            "과화질",
            {
                int(Qt.ItemDataRole.ForegroundRole): QColor("#ff0000"),
                int(VideoTableModel.BADGE_ROLE): (_BG, "#0000ff", _BORDER),
            },
        )
        red = _paint(PlainTextDelegate(), rgb_case, 200, 30)
        self.assertIsNotNone(
            _bbox(red, lambda c: c.red() > 200 and c.green() < 80 and c.blue() < 80)
        )

        badge_case = _index("과화질", {int(VideoTableModel.BADGE_ROLE): (_BG, "#0000ff", _BORDER)})
        self.assertIsNotNone(_bbox(_paint(PlainTextDelegate(), badge_case, 200, 30), _is("#0000ff")))

        muted = _bbox(
            _paint(PlainTextDelegate(), _index("과화질", {}), 200, 30),
            _is(QColor(self.palette.text_secondary).name()),
        )
        self.assertIsNotNone(muted)

    def test_plain_text_falls_back_on_empty_text(self) -> None:
        """표시 문자열이 비면 기본 렌더링으로 넘김을 검증"""
        image = _paint(PlainTextDelegate(), _index("", {}), 200, 30)
        self.assertIsNone(_bbox(image, lambda c: c.name() != _WHITE))

    def test_outline_chip_is_border_only_and_fits_the_cell(self) -> None:
        """축소 후보 칩이 테두리만 있고 좁은 셀에서도 셀 안에 머묾을 검증"""
        border = QColor(self.palette.text_secondary).name()
        image = _paint(OutlineChipDelegate(), _index("1080p", {}), 200, 30)
        chip = _bbox(image, _is(border))
        assert chip is not None
        self.assertLessEqual(chip[3] - chip[1] + 1, 18)
        # - 칩 안쪽은 채우지 않아 셀 배경이 비쳐 보임
        interior = [
            image.pixelColor(x, y).name()
            for x in range(chip[0] + 2, chip[2] - 1)
            for y in range(chip[1] + 2, chip[3] - 1)
        ]
        self.assertIn(_WHITE, interior)

        narrow = _paint(OutlineChipDelegate(), _index("1080p", {}), 40, 30)
        narrow_chip = _bbox(narrow, _is(border))
        assert narrow_chip is not None
        self.assertLessEqual(narrow_chip[2], 39)
        self.assertGreaterEqual(narrow_chip[0], 0)

    def test_outline_chip_falls_back_on_empty_text(self) -> None:
        """표시 문자열이 비면 칩을 그리지 않음을 검증"""
        border = QColor(self.palette.text_secondary).name()
        image = _paint(OutlineChipDelegate(), _index("", {}), 200, 30)
        self.assertIsNone(_bbox(image, _is(border)))

    def test_active_frame_pill_is_drawn_only_for_vr_and_keep_full_text(self) -> None:
        """활성 화면 열의 VR 보호와 전체 화면 유지 표기만 알약으로 강조됨을 검증"""
        p = self.palette
        vr = _paint(ActiveFrameDelegate(), _index(ACTIVE_FRAME_VR_TEXT, {}), 200, 30)
        self.assertIsNotNone(_bbox(vr, _is(QColor(p.badge_optimal[0]).name())))
        keep_full = _paint(
            ActiveFrameDelegate(), _index(ACTIVE_FRAME_KEEP_FULL_TEXT, {}), 200, 30
        )
        self.assertIsNotNone(_bbox(keep_full, _is(QColor(p.badge_pass[0]).name())))
        other = _paint(ActiveFrameDelegate(), _index("1920x1080", {}), 200, 30)
        self.assertIsNone(_bbox(other, _is(QColor(p.badge_optimal[0]).name())))
        self.assertIsNone(_bbox(other, _is(QColor(p.badge_pass[0]).name())))

    def test_keep_full_pill_puts_the_crop_icon_left_of_the_text(self) -> None:
        """전체 화면 유지 알약이 crop-full 아이콘을 글자 왼쪽에 두고 알약 안에 담는지 검증"""
        index = _index(
            ACTIVE_FRAME_KEEP_FULL_TEXT,
            {int(Qt.ItemDataRole.DecorationRole): _solid_icon(_ICON_BLUE)},
        )
        image = _paint(ActiveFrameDelegate(), index, 200, 30)
        icon = _bbox(image, _is(_ICON_BLUE))
        pill = _bbox(image, _is(QColor(self.palette.badge_pass[0]).name()))
        text = _bbox(
            image,
            lambda c: c.name() == QColor(self.palette.badge_pass[1]).name(),
        )
        assert icon is not None and pill is not None and text is not None
        self.assertEqual((icon[2] - icon[0] + 1, icon[3] - icon[1] + 1), (_ICON_PX, _ICON_PX))
        self.assertLess(icon[2], text[0])
        self.assertGreaterEqual(icon[0], pill[0])
        self.assertLessEqual(text[2], pill[2])

    def test_active_frame_delegates_ignore_missing_painter(self) -> None:
        """painter가 None이면 알약을 그리지 않음을 검증"""
        ActiveFrameDelegate().paint(
            None, QStyleOptionViewItem(), _index(ACTIVE_FRAME_VR_TEXT, {})
        )


    def test_name_cell_keeps_text_right_of_warning_icon(self) -> None:
        """여백 확인 필요 경고 아이콘이 있는 이름 셀에서 글자가 아이콘 오른쪽에서 시작함을 검증"""
        index = _index(
            "아주 긴 파일명 테스트용 동영상 클립 이름.mp4",
            {int(Qt.ItemDataRole.DecorationRole): _solid_icon(_ICON_BLUE)},
        )
        image = _paint(NameAccentDelegate(), index, 120, 30)
        icon = _bbox(image, _is(_ICON_BLUE))
        text = _bbox(image, lambda c: c.name() != _WHITE and c.name() != _ICON_BLUE and c.lightness() < 128)
        assert icon is not None and text is not None
        self.assertEqual(icon[0], 4)
        self.assertGreaterEqual(text[0], icon[2] + 1)
        self.assertLessEqual(text[2], 119)

    def test_name_cell_keeps_text_right_of_icon_with_search_query(self) -> None:
        """검색어가 있을 때도 아이콘이 있는 이름 셀의 글자가 아이콘과 겹치지 않음을 검증"""
        index = _index(
            "아주 긴 파일명 테스트용 동영상 클립 이름.mp4",
            {int(Qt.ItemDataRole.DecorationRole): _solid_icon(_ICON_BLUE)},
        )
        view = QWidget()
        view.setProperty("search_highlight_query", "파일")
        image = QImage(120, 30, QImage.Format.Format_ARGB32)
        image.fill(QColor(_WHITE))
        painter = QPainter(image)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 120, 30)
        option.font = QFont()
        option.widget = view
        NameAccentDelegate().paint(painter, option, index)
        painter.end()
        icon = _bbox(image, _is(_ICON_BLUE))
        text = _bbox(image, lambda c: c.name() != _WHITE and c.name() != _ICON_BLUE and c.lightness() < 128)
        assert icon is not None and text is not None
        self.assertEqual(icon[0], 4)
        self.assertGreaterEqual(text[0], icon[2] + 1)

    def test_name_cell_without_icon_is_unchanged(self) -> None:
        """아이콘이 없는 이름 셀은 평소와 같은 위치에서 글자를 그림을 검증"""
        index = _index("clip.mp4", {})
        image = _paint(NameAccentDelegate(), index, 200, 30)
        text = _bbox(image, lambda c: c.name() != _WHITE and c.lightness() < 128)
        assert text is not None
        self.assertLessEqual(text[0], 6)
        self.assertGreaterEqual(text[2], 90)


class TestOutlineChipKeepText(unittest.TestCase):
    """축소 후보 열에서 유지 값은 칩 없이 옅은 일반 글자로 그려짐을 검증"""

    def setUp(self) -> None:
        """테마를 고정하고 팔레트를 확정"""
        mgr = ThemeManager()
        self.addCleanup(mgr.set_mode, mgr.mode)

    def _chip_calls(self, text: str) -> list[object]:
        """drawRoundedRect 호출을 기록하며 텍스트를 그린 뒤 호출 인자 목록을 반환"""
        calls: list[object] = []
        original = QPainter.drawRoundedRect

        def spy(self_painter: QPainter, *args: object) -> None:
            calls.append(args)
            original(self_painter, *args)  # type: ignore[arg-type]

        with mock.patch.object(QPainter, "drawRoundedRect", spy):
            _paint(OutlineChipDelegate(), _index(text, {}), 200, 30)
        return calls

    def _check_keep_text(self) -> None:
        """유지는 칩을 그리지 않고 text_muted 색의 굵지 않은 글자로 그려짐을 검증"""
        p = ThemeManager().current_palette
        keep_index = _index(DOWNSCALE_KEEP_TEXT, {})
        self.assertEqual(self._chip_calls(DOWNSCALE_KEEP_TEXT), [])
        image = _paint(OutlineChipDelegate(), keep_index, 200, 30)
        text = _bbox(image, _is(QColor(p.text_muted).name()))
        assert text is not None
        # - 칩 테두리가 없어 글자만 셀 안에 남음
        self.assertGreaterEqual(text[0], 0)
        self.assertLessEqual(text[2], 199)

        plain = QImage(200, 30, QImage.Format.Format_ARGB32)
        plain.fill(QColor(_WHITE))
        painter = QPainter(plain)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 200, 30)
        option.font = QFont()
        OutlineChipDelegate().paint(painter, option, keep_index)
        painter.end()
        self.assertFalse(option.font.bold())

    def test_keep_text_in_dark_palette(self) -> None:
        """다크 테마에서 유지가 칩 없이 옅은 글자로 그려짐을 검증"""
        ThemeManager().set_mode(ThemeManager.MODE_DARK)
        self._check_keep_text()

    def test_keep_text_in_light_palette(self) -> None:
        """라이트 테마에서 유지가 칩 없이 옅은 글자로 그려짐을 검증"""
        ThemeManager().set_mode(ThemeManager.MODE_LIGHT)
        self._check_keep_text()

    def test_tier_text_keeps_the_chip(self) -> None:
        """후보 규격 값은 기존 칩 렌더링을 유지함을 검증"""
        for mode in (ThemeManager.MODE_DARK, ThemeManager.MODE_LIGHT):
            ThemeManager().set_mode(mode)
            self.assertEqual(len(self._chip_calls("720p")), 1)


if __name__ == "__main__":
    unittest.main()
