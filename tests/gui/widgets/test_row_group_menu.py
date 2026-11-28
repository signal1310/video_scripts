"""행 묶기 드롭다운 행의 체크 표시, 이름, 오른쪽 알약 배지와 테마 반영 검증"""

from __future__ import annotations

import os
import unittest
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QMenu, QPushButton

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.widgets.filter_bar import FilterTuningBar
from src.gui.widgets.filter_bar_chips import FilterBarCountBadge
from src.gui.widgets.row_group_menu import (
    ROW_GROUP_BADGE_GAP_PX,
    ROW_GROUP_CARET_RESERVE_PX,
    RowGroupMenuEntry,
    RowGroupMenuRow,
    build_row_group_action,
    place_row_group_button_badge,
    row_group_action_row,
    row_group_button_padding_style,
    set_highlighted_action,
)
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])
# - PyQt6 스텁이 QTest 정적 메서드를 인스턴스 메서드로 선언해 형식 검사가 실패하므로 호출 가능한 형태로 받음
_send_key_click: Callable[..., None] = QTest.keyClick
_send_mouse_click: Callable[..., None] = QTest.mouseClick


def _check_label(action: QAction) -> QLabel:
    """행 안의 체크 표시 자리 라벨을 반환"""
    row = row_group_action_row(action)
    assert row is not None
    label = row.findChild(QLabel, "RowGroupMenuCheck")
    assert label is not None
    return label


def _row_top_center_color(row: RowGroupMenuRow) -> QColor:
    """행 위쪽 가운데 픽셀 색. 이름과 알약이 닿지 않는 자리라 배경색을 읽을 수 있음"""
    image = row.grab().toImage()
    return image.pixelColor(image.width() // 2, 2)


class TestRowGroupMenuRow(GuiTestCase):
    """묶기 메뉴 행이 이름과 오른쪽 알약만 그리고 체크, 강조, 테마를 따르는지 검증"""

    def _menu(self) -> QMenu:
        """스타일시트를 적용한 빈 메뉴를 만들어 정리를 등록하고 반환"""
        menu = QMenu()
        menu.setStyleSheet(build_stylesheet(DARK_PALETTE))
        self.addCleanup(menu.close)
        return menu

    def test_row_draws_only_the_name(self) -> None:
        """행 이름에 개수가 섞이지 않고 오른쪽 알약이 그 개수를 따로 보이는지 검증"""
        menu = self._menu()
        action = build_row_group_action(
            menu, RowGroupMenuEntry("코덱", "column:4", count=12), DARK_PALETTE
        )
        menu.addAction(action)
        row = row_group_action_row(action)
        assert row is not None
        self.assertEqual(action.text(), "코덱")
        self.assertEqual(row.name_text, "코덱")
        self.assertFalse(row.badge.isHidden())
        self.assertEqual(row.badge.count, 12)
        self.assertEqual(row.objectName(), "RowGroupMenuRow")

    def test_zero_count_hides_the_badge(self) -> None:
        """그룹 수가 0인 항목은 알약을 숨기고 이름만 남기는지 검증"""
        menu = self._menu()
        action = build_row_group_action(
            menu, RowGroupMenuEntry("폴더", "folder", count=0), DARK_PALETTE
        )
        menu.addAction(action)
        row = row_group_action_row(action)
        assert row is not None
        self.assertTrue(row.badge.isHidden())
        self.assertEqual(row.badge.count, 0)

        row.set_count(4)
        self.assertFalse(row.badge.isHidden())
        self.assertEqual(row.badge.count, 4)

    def test_badge_stays_right_aligned_for_different_name_lengths(self) -> None:
        """이름 길이가 달라도 알약이 행 오른쪽 끝에 붙는지 검증"""
        menu = self._menu()
        menu.addAction(
            build_row_group_action(menu, RowGroupMenuEntry("짧음", "a", count=3), DARK_PALETTE)
        )
        menu.addAction(
            build_row_group_action(
                menu, RowGroupMenuEntry("아주 긴 범주형 열 이름", "b", count=7), DARK_PALETTE
            )
        )
        menu.show()
        _APP.processEvents()
        short_row = row_group_action_row(menu.actions()[0])
        long_row = row_group_action_row(menu.actions()[1])
        assert short_row is not None and long_row is not None
        for row in (short_row, long_row):
            layout = row.layout()
            assert layout is not None
            right_end = row.width() - layout.contentsMargins().right()
            self.assertEqual(row.badge.x() + row.badge.width(), right_end)
        # - 행 폭이 같으므로 이름 길이와 관계없이 알약이 같은 오른쪽 끝에 붙음
        self.assertEqual(short_row.badge.x(), long_row.badge.x())

    def test_checked_row_shows_a_check_icon(self) -> None:
        """선택된 항목만 체크 아이콘을 그리고 선택이 풀리면 지우는지 검증"""
        menu = self._menu()
        action = build_row_group_action(
            menu, RowGroupMenuEntry("폴더", "folder", checked=True), DARK_PALETTE
        )
        menu.addAction(action)
        row = row_group_action_row(action)
        assert row is not None
        self.assertTrue(row.checked)
        self.assertFalse(_check_label(action).pixmap().isNull())

        action.setChecked(False)
        self.assertFalse(row.checked)
        self.assertTrue(_check_label(action).pixmap().isNull())

    def test_disabled_command_row_follows_the_action(self) -> None:
        """꺼진 명령은 행도 꺼져 보이게 그리고 켜면 함께 풀리는지 검증"""
        menu = self._menu()
        entry = RowGroupMenuEntry("모든 그룹 접기", "collapse", checkable=False, enabled=False)
        action = build_row_group_action(menu, entry, DARK_PALETTE)
        menu.addAction(action)
        row = row_group_action_row(action)
        assert row is not None
        self.assertFalse(action.isEnabled())
        self.assertFalse(row.isEnabled())

        action.setEnabled(True)
        self.assertTrue(row.isEnabled())

    def test_highlighted_row_uses_the_selected_background(self) -> None:
        """메뉴가 가리키는 행만 `bg_selected`로 칠하고 닫으면 지우는지 검증"""
        menu = self._menu()
        first = build_row_group_action(
            menu, RowGroupMenuEntry("폴더", "folder", count=2), DARK_PALETTE
        )
        second = build_row_group_action(
            menu, RowGroupMenuEntry("코덱", "column:4", count=5), DARK_PALETTE
        )
        menu.addAction(first)
        menu.addAction(second)
        menu.show()
        _APP.processEvents()

        set_highlighted_action(menu, second)
        _APP.processEvents()
        first_row = row_group_action_row(first)
        second_row = row_group_action_row(second)
        assert first_row is not None and second_row is not None
        self.assertFalse(first_row.highlighted)
        self.assertTrue(second_row.highlighted)
        self.assertEqual(
            _row_top_center_color(second_row).name(), DARK_PALETTE.bg_selected.lower()
        )

        set_highlighted_action(menu, None)
        self.assertFalse(second_row.highlighted)
        self.assertNotEqual(
            _row_top_center_color(second_row).name(), DARK_PALETTE.bg_selected.lower()
        )

    def test_apply_theme_recolors_the_badge(self) -> None:
        """테마를 바꾸면 행 알약 배지가 새 팔레트 토큰으로 다시 칠해지는지 검증"""
        menu = self._menu()
        action = build_row_group_action(
            menu, RowGroupMenuEntry("폴더", "folder", count=4), DARK_PALETTE
        )
        menu.addAction(action)
        row = row_group_action_row(action)
        assert row is not None
        for palette in (LIGHT_PALETTE, DARK_PALETTE):
            row.apply_theme(palette)
            self.assertIn(palette.bg_elevated, row.badge.styleSheet())
            self.assertIn(palette.text_secondary, row.badge.styleSheet())


class TestRowGroupMenuInFilterBar(GuiTestCase):
    """필터 바 그룹 드롭다운이 행 배지, 강조, 클릭 동작을 그대로 살리는지 검증"""

    def _bar(self) -> FilterTuningBar:
        """묶기 항목과 그룹 수를 채운 필터 바를 만들어 정리를 등록하고 반환"""
        bar = FilterTuningBar()
        self.addCleanup(bar.close)
        bar.set_row_group_options([("folder", "폴더"), ("column:4", "코덱")])
        bar.set_row_group_counts({"folder": 2, "column:4": 0})
        return bar

    def test_menu_rows_keep_plain_action_text(self) -> None:
        """메뉴 동작 글자가 이름뿐이라 hovered 시그널과 접근성 이름이 개수를 섞지 않는지 검증"""
        bar = self._bar()
        self.assertEqual(
            [act.text() for act in bar._row_group_menu.actions() if act.text()],
            ["없음", "폴더", "코덱", "모든 그룹 펼치기", "모든 그룹 접기"],
        )

    def test_hovered_signal_highlights_exactly_one_row(self) -> None:
        """메뉴 hovered 시그널이 그 항목 행만 강조하고 닫으면 지우는지 검증"""
        bar = self._bar()
        bar._row_group_menu.hovered.emit(bar._row_group_actions["folder"])
        rows = {key: row_group_action_row(act) for key, act in bar._row_group_actions.items()}
        assert all(row is not None for row in rows.values())
        folder_row = rows["folder"]
        codec_row = rows["column:4"]
        none_row = rows[""]
        assert folder_row is not None and codec_row is not None and none_row is not None
        self.assertTrue(folder_row.highlighted)
        self.assertFalse(codec_row.highlighted)
        self.assertFalse(none_row.highlighted)

        bar._row_group_menu.aboutToHide.emit()
        self.assertFalse(folder_row.highlighted)

    def test_keyboard_navigation_highlights_the_active_row(self) -> None:
        """키보드로 항목을 옮기면 그 행이 강조되고 클릭으로 기존 동작이 실행되는지 검증"""
        bar = self._bar()
        selected: list[str] = []
        bar.row_group_selected.connect(selected.append)
        bar._row_group_menu.popup(bar.mapToGlobal(bar.rect().bottomLeft()))
        _APP.processEvents()
        self.assertTrue(bar._row_group_menu.isVisible())

        _send_key_click(bar._row_group_menu, Qt.Key.Key_Down)
        _APP.processEvents()
        active = bar._row_group_menu.activeAction()
        assert active is not None
        row = row_group_action_row(active)
        assert row is not None
        self.assertTrue(row.highlighted)

        geometry = bar._row_group_menu.actionGeometry(active)
        _send_mouse_click(bar._row_group_menu, Qt.MouseButton.LeftButton, pos=geometry.center())
        _APP.processEvents()
        self.assertFalse(bar._row_group_menu.isVisible())
        # - 마우스 이동 없이 첫 항목(`없음`)이 선택돼 빈 키가 나가고 메뉴가 닫힘
        self.assertEqual(selected, [""])

    def test_button_padding_style_reserves_the_badge_spot(self) -> None:
        """그룹 버튼 오른쪽 여백이 알약 몫만큼만 잡히고 0이면 비는지 검증"""
        self.assertEqual(row_group_button_padding_style(0), "")
        style = row_group_button_padding_style(30)
        self.assertIn("QPushButton#RowGroupButton", style)
        self.assertIn("padding-right: 30px;", style)

    def test_button_badge_is_hidden_without_a_group(self) -> None:
        """그룹을 고르지 않았을 때 그룹 버튼 알약이 숨고 여백도 남지 않는지 검증"""
        bar = self._bar()
        self.assertTrue(bar._row_group_badge.isHidden())
        self.assertEqual(bar.btn_row_group.styleSheet(), "")
        self.assertEqual(bar.btn_row_group.text(), "그룹: 없음")

        bar.set_row_group("folder", label="폴더", count=3)
        self.assertFalse(bar._row_group_badge.isHidden())
        self.assertEqual(bar._row_group_badge.count, 3)

    def test_place_badge_keeps_it_inside_the_button(self) -> None:
        """알약 배지가 버튼 오른쪽 캐럿 앞에 놓여 밖으로 나가지 않는지 검증"""
        button = QPushButton("그룹: 폴더")
        button.setObjectName("RowGroupButton")
        self.addCleanup(button.close)
        button.resize(160, 28)
        badge = FilterBarCountBadge(button)
        badge.set_count(4)
        badge.setVisible(True)
        place_row_group_button_badge(button, badge)
        self.assertGreaterEqual(badge.x(), 0)
        self.assertLessEqual(badge.x() + badge.width(), button.width())
        self.assertEqual(
            badge.x(),
            button.width() - ROW_GROUP_CARET_RESERVE_PX - ROW_GROUP_BADGE_GAP_PX - badge.width(),
        )
        self.assertEqual(badge.y(), (button.height() - badge.height()) // 2)


if __name__ == "__main__":
    unittest.main()
