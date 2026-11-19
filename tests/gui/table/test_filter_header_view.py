"""FilterHeaderView의 필터 버튼 배치, 클릭 판정, 활성 표시, 호버 렌더링 검증"""

import os
import unittest

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QStandardItemModel
from PyQt6.QtWidgets import QApplication, QHeaderView

from src.gui.styles.icons import get_active_palette
from src.gui.table.widgets.filter_header_view import FilterHeaderView
from tests._gui_helpers import GuiTestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])

_HEIGHT = 34
_WIDE, _NARROW, _TOO_NARROW = 80, 40, 37


def _mouse(event_type: QMouseEvent.Type, pos: QPoint, *, button: Qt.MouseButton = Qt.MouseButton.LeftButton) -> QMouseEvent:
    point = QPointF(pos)
    held = Qt.MouseButton.NoButton if event_type == QMouseEvent.Type.MouseButtonRelease else button
    return QMouseEvent(event_type, point, point, button, held, Qt.KeyboardModifier.NoModifier)


class TestFilterHeaderView(GuiTestCase):
    """열 폭과 헤더 높이에 따른 필터 버튼 영역과 상호작용을 검증"""

    def _header(self, widths: tuple[int, ...] = (_WIDE, _NARROW, _TOO_NARROW), height: int = _HEIGHT) -> FilterHeaderView:
        header = FilterHeaderView()
        model = QStandardItemModel(0, len(widths))
        model.setHorizontalHeaderLabels([f"열{i}" for i in range(len(widths))])
        header.setModel(model)
        header.setFixedHeight(height)
        header.setStretchLastSection(False)
        for col, width in enumerate(widths):
            header.resizeSection(col, width)
        header.resize(sum(widths) + 10, height)
        header.show()
        _APP.processEvents()
        self.addCleanup(header.close)
        return header

    def test_button_rect_size_and_position_by_section_width_and_height(self) -> None:
        """버튼 크기가 열 폭과 헤더 높이로 결정되고 우하단 여백에 맞춰 배치됨을 검증"""
        header = self._header()
        self.assertEqual(header.filter_button_rect(0), QRect(58, 11, 15, 15))
        narrow_x = header.sectionViewportPosition(1)
        self.assertEqual(header.filter_button_rect(1), QRect(narrow_x + 21, 14, 12, 12))
        self.assertFalse(header.filter_button_rect(2).isValid())

        short = self._header(height=20)
        self.assertEqual(short.filter_button_rect(0), QRect(61, 4, 12, 12))

    def test_button_rect_is_invalid_for_hidden_or_unknown_columns(self) -> None:
        """숨김 열과 범위 밖 열에는 버튼 영역이 없음을 검증"""
        header = self._header()
        header.setSectionHidden(0, True)
        for col in (0, -1, 99):
            with self.subTest(col=col):
                self.assertFalse(header.filter_button_rect(col).isValid())
                self.assertFalse(header.filter_button_global_rect(col).isValid())

    def test_boundary_widths_for_button_visibility_and_size(self) -> None:
        """버튼이 보이는 최소 폭(38)과 소형 버튼 최대 폭(55), 대형 전환 폭(56)의 경계를 검증"""
        header = self._header(widths=(37, 38, 55, 56))
        sizes = [header.filter_button_rect(col).width() if header.filter_button_rect(col).isValid() else 0 for col in range(4)]
        self.assertEqual(sizes, [0, 12, 12, 15])

    def test_global_rect_and_popover_anchor_map_from_viewport(self) -> None:
        """전역 버튼 영역과 팝오버 기준점이 뷰포트 좌표에서 올바르게 변환됨을 검증"""
        header = self._header()
        local = header.filter_button_rect(0)
        global_rect = header.filter_button_global_rect(0)
        self.assertEqual(global_rect.topLeft(), header.mapToGlobal(local.topLeft()))
        self.assertEqual(global_rect.size(), local.size())

        second_x = header.sectionViewportPosition(1)
        self.assertEqual(header.section_anchor_global(1), header.mapToGlobal(QPoint(second_x, _HEIGHT)))
        header.setSectionHidden(1, True)
        self.assertEqual(header.section_anchor_global(1), header.mapToGlobal(QPoint(0, _HEIGHT)))

    def test_click_on_button_emits_only_for_left_press_and_release_on_same_button(self) -> None:
        """좌클릭을 같은 버튼에서 떼야 시그널이 방출되고, 다른 곳에서 떼거나 우클릭이면 방출하지 않음을 검증"""
        header = self._header()
        clicked: list[int] = []
        header.filter_clicked.connect(lambda col, _pt: clicked.append(col))
        on_button = header.filter_button_rect(0).center()
        elsewhere = QPoint(30, 10)
        press, release = QMouseEvent.Type.MouseButtonPress, QMouseEvent.Type.MouseButtonRelease

        header.mousePressEvent(_mouse(press, on_button))
        header.mouseReleaseEvent(_mouse(release, on_button))
        self.assertEqual(clicked, [0])

        header.mousePressEvent(_mouse(press, on_button))
        header.mouseReleaseEvent(_mouse(release, elsewhere))
        header.mousePressEvent(_mouse(press, on_button, button=Qt.MouseButton.RightButton))
        header.mouseReleaseEvent(_mouse(release, on_button, button=Qt.MouseButton.RightButton))
        self.assertEqual(clicked, [0])

        narrow_center = header.filter_button_rect(1).center()
        header.mousePressEvent(_mouse(press, narrow_center))
        header.mouseReleaseEvent(_mouse(release, narrow_center))
        self.assertEqual(clicked, [0, 1])

    def test_active_columns_are_copied_and_deduplicated(self) -> None:
        """활성 열 집합은 사본으로 반환되고 같은 집합 재설정은 변경으로 취급하지 않음을 검증"""
        header = self._header()
        header.set_active_filter_columns({1, 2})
        snapshot = header.active_filter_columns()
        snapshot.add(0)
        self.assertEqual(header.active_filter_columns(), {1, 2})
        header.set_active_filter_columns({2, 1})
        self.assertEqual(header.active_filter_columns(), {1, 2})

    def _titled_header(self, title: str, width: int) -> FilterHeaderView:
        header = self._header(widths=(width,))
        header_model = header.model()
        assert isinstance(header_model, QStandardItemModel)
        header_model.setHorizontalHeaderLabels([title])
        return header

    def test_section_height_grows_when_title_wraps(self) -> None:
        """긴 제목은 좁은 열에서 여러 줄로 감싸 높이가 커지고, 넓은 열에서는 줄어듦을 검증"""
        title = "아주 긴 헤더 제목 문구 테스트"
        narrow = self._titled_header(title, 60).sectionSizeFromContents(0).height()
        wide = self._titled_header(title, 600).sectionSizeFromContents(0).height()
        self.assertGreater(narrow, wide)

    def test_empty_title_keeps_base_section_size(self) -> None:
        """제목이 없으면 기본 섹션 크기를 그대로 사용함을 검증"""
        header = self._titled_header("", 60)
        self.assertEqual(header.sectionSizeFromContents(0), QHeaderView.sectionSizeFromContents(header, 0))

    def test_sort_indicator_reserves_width_and_never_shrinks_height(self) -> None:
        """정렬 표시가 있는 열은 화살표 폭만큼 줄바꿈이 앞당겨져 높이가 같거나 커지며, 어떤 폭에서는 실제로 커짐을 검증"""
        header = self._titled_header("가나다라 마바사아", 60)
        grew = False
        for width in range(40, 300, 2):
            header.resizeSection(0, width)
            header.setSortIndicatorShown(False)
            plain = header.sectionSizeFromContents(0).height()
            header.setSortIndicatorShown(True)
            header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
            marked = header.sectionSizeFromContents(0).height()
            self.assertGreaterEqual(marked, plain, width)
            grew = grew or marked > plain
        self.assertTrue(grew)

    def _render(self, header: FilterHeaderView) -> QImage:
        _APP.processEvents()
        return header.grab().toImage()

    def test_active_column_draws_accent_bar_at_bottom(self) -> None:
        """활성 필터 열만 하단에 강조선을 그림을 검증"""
        header = self._header()
        accent = QColor(get_active_palette().accent_blue).name()
        probe = QPoint(5, _HEIGHT - 1)

        self.assertNotEqual(self._render(header).pixelColor(probe).name(), accent)
        header.set_active_filter_columns({0})
        self.assertEqual(self._render(header).pixelColor(probe).name(), accent)
        other = QPoint(header.sectionViewportPosition(1) + 5, _HEIGHT - 1)
        self.assertNotEqual(self._render(header).pixelColor(other).name(), accent)

    def test_hover_draws_filter_button_and_leave_removes_it(self) -> None:
        """섹션 위로 마우스를 올리면 필터 버튼이 그려지고 헤더를 벗어나면 사라짐을 검증"""
        header = self._header()
        center = header.filter_button_rect(0).center()
        idle = self._render(header).pixelColor(center)

        header.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, center, button=Qt.MouseButton.NoButton))
        self.assertNotEqual(self._render(header).pixelColor(center), idle)

        header.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertEqual(self._render(header).pixelColor(center), idle)


if __name__ == "__main__":
    unittest.main()
