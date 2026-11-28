"""좁은 창에서 필터 바 버튼이 단계별로 아이콘만 남는지, 아이콘 전용 버튼이 글자를 갖지 않는지 검증"""

from __future__ import annotations

import os
import unittest
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.widgets.filter_bar import (
    COLLAPSE_LEVEL_CHIPS,
    COLLAPSE_LEVEL_DROPDOWNS,
    COLLAPSE_LEVEL_FULL,
    COMPACT_BUTTON_TEXT,
    COMPACT_TOOLTIP,
    COUNT_BADGE_OFFSET_PX,
    EXCEPTION_BUTTON_TEXT,
    REVIEW_CHIP_LABEL,
    REVIEW_CHIP_TOOLTIP,
    ROW_GROUP_TOOLTIP,
    SEARCH_COMPACT_MIN_WIDTH_PX,
    SEARCH_MIN_WIDTH_PX,
    VARY_CHIP_LABEL,
    VARY_CHIP_TOOLTIP,
    FilterTuningBar,
)
from src.gui.widgets.filter_bar_chips import CHIP_REVIEW, CHIP_VARY, count_badge_style
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])

# - 접힘 대상 여섯 버튼을 접힘 단계 순서대로 나열한 (속성 이름, 단계)
COLLAPSE_STEPS: tuple[tuple[str, int], ...] = (
    ("btn_reset_filters", COLLAPSE_LEVEL_CHIPS),
    ("btn_clear_col_filters", COLLAPSE_LEVEL_CHIPS),
    ("btn_review_filter", COLLAPSE_LEVEL_CHIPS),
    ("btn_vary_filter", COLLAPSE_LEVEL_CHIPS),
    ("btn_column_preset", COLLAPSE_LEVEL_DROPDOWNS),
    ("btn_row_group", COLLAPSE_LEVEL_DROPDOWNS),
)
# - 창 폭과 무관하게 늘 아이콘만 보이는 버튼
ICON_ONLY_BUTTONS: tuple[str, ...] = ("btn_compact", "btn_edit_exceptions")
COLLAPSE_LEVELS: list[int] = [COLLAPSE_LEVEL_FULL, COLLAPSE_LEVEL_CHIPS, COLLAPSE_LEVEL_DROPDOWNS]
WIDE_WIDTH_PX: int = 1400
# - 접힘 단계별로 그 단계가 실제 적용되는 폭을 찾을 때 내려가는 범위
PROBE_WIDTHS_PX: range = range(WIDE_WIDTH_PX, 480, -10)
CLIPPING_WIDTHS_PX: range = range(1500, 520, -20)
# - PyQt6 스텁이 QTest.mouseMove를 인스턴스 메서드로 선언해 정적 호출의 형식 검사가 실패하므로 호출 가능한 형태로 받음
_send_mouse_move: Callable[..., None] = QTest.mouseMove


def _make_bar() -> FilterTuningBar:
    """접힘 대상 버튼이 모두 보이는 상태의 필터 바를 만들어 반환"""
    bar = FilterTuningBar()
    bar.set_any_filter_active(True)
    bar.set_active_column_filter_count(2)
    bar.set_review_flagged_count(3)
    bar.set_vary_flagged_count(1)
    bar.set_exception_tokens(["alpha", "beta"])
    bar.set_row_group("column:4", label="코덱", count=12)
    return bar


def _button_center_color(button: QPushButton) -> QColor:
    """버튼 위쪽 가운데 픽셀 색. 아이콘과 글자가 닿지 않는 자리라 배경색을 읽을 수 있음"""
    image = button.grab().toImage()
    return image.pixelColor(image.width() // 2, 6)


class TestFilterBarCollapse(GuiTestCase):
    """필터 바의 폭에 따른 단계별 접힘, 접근성 이름, 툴팁, 잘림 여부를 검증"""

    def _shown_bar(self, width: int = WIDE_WIDTH_PX) -> FilterTuningBar:
        """지정 폭으로 보여 준 필터 바를 만들어 정리를 등록하고 반환"""
        bar = _make_bar()
        self.addCleanup(bar.close)
        bar.show()
        bar.resize(width, 44)
        _APP.processEvents()
        return bar

    def test_wide_width_keeps_every_full_label(self) -> None:
        """넓은 폭에서도 아이콘 전용 버튼은 글자가 없고, 나머지는 지금처럼 전체 문구를 보이는지 검증"""
        bar = self._shown_bar()
        self.assertEqual(bar.collapse_level, COLLAPSE_LEVEL_FULL)
        self.assertEqual(bar.btn_compact.text(), "")
        self.assertEqual(bar.btn_edit_exceptions.text(), "")
        self.assertEqual(bar.btn_reset_filters.text(), "필터 초기화")
        self.assertEqual(bar.btn_clear_col_filters.text(), "열 필터 2개 ×")
        self.assertEqual(bar.btn_review_filter.text(), f"{REVIEW_CHIP_LABEL} 3")
        self.assertEqual(bar.btn_vary_filter.text(), f"{VARY_CHIP_LABEL} 1")
        self.assertEqual(bar.btn_column_preset.text(), "열: 기본")
        self.assertEqual(bar.btn_row_group.text(), "그룹: 코덱 (12)")
        self.assertFalse(bar._clear_col_filters_badge.isVisible())
        self.assertFalse(bar._review_filter_badge.isVisible())
        self.assertFalse(bar._vary_filter_badge.isVisible())
        # - 예외 규칙 개수는 넓은 폭에서도 배지로 보임
        self.assertTrue(bar._exception_badge.isVisible())
        self.assertEqual(bar._exception_badge.count, 2)

    def test_level_grows_as_the_bar_narrows(self) -> None:
        """폭을 줄이면 접힘 단계가 0에서 2까지 차례로 올라가는지 검증"""
        bar = self._shown_bar()
        levels: list[int] = []
        for width in range(WIDE_WIDTH_PX, 500, -20):
            bar.resize(width, 44)
            _APP.processEvents()
            if not levels or levels[-1] != bar.collapse_level:
                levels.append(bar.collapse_level)
        self.assertEqual(levels, COLLAPSE_LEVELS)

    def _level_widths(self, bar: FilterTuningBar) -> dict[int, int]:
        """각 접힘 단계가 처음 나타나는 폭을 폭을 줄여 가며 찾아 반환"""
        widths: dict[int, int] = {}
        for width in PROBE_WIDTHS_PX:
            bar.resize(width, 44)
            _APP.processEvents()
            widths.setdefault(bar.collapse_level, width)
        self.assertEqual(sorted(widths), COLLAPSE_LEVELS)
        return widths

    def _at_level(self, bar: FilterTuningBar, level: int) -> None:
        """지정 단계가 적용되는 폭으로 필터 바를 맞춤"""
        width = self._level_widths(bar)[level]
        bar.resize(width, 44)
        _APP.processEvents()
        self.assertEqual(bar.collapse_level, level)

    def test_each_step_collapses_only_its_buttons(self) -> None:
        """각 단계에서 그 단계까지의 버튼만 글자가 비고, 나머지는 전체 문구를 유지하는지 검증"""
        bar = self._shown_bar()
        full_labels = {name: getattr(bar, name).text() for name, _step in COLLAPSE_STEPS}
        widths = self._level_widths(bar)
        for level in sorted(widths):
            bar.resize(widths[level], 44)
            _APP.processEvents()
            self.assertEqual(bar.collapse_level, level)
            for name, step in COLLAPSE_STEPS:
                button = getattr(bar, name)
                if step <= level:
                    self.assertEqual(button.text(), "", f"{name} at level {level}")
                else:
                    self.assertEqual(button.text(), full_labels[name], f"{name} at level {level}")

    def test_collapsed_buttons_keep_accessible_name_and_tooltip(self) -> None:
        """접힌 버튼도 전체 이름을 접근성 이름으로 유지하고 툴팁이 비지 않는지 검증"""
        bar = self._shown_bar(width=620)
        self.assertEqual(bar.collapse_level, COLLAPSE_LEVEL_DROPDOWNS)
        self.assertEqual(bar.btn_compact.accessibleName(), COMPACT_BUTTON_TEXT)
        self.assertEqual(bar.btn_edit_exceptions.accessibleName(), EXCEPTION_BUTTON_TEXT)
        self.assertEqual(bar.btn_reset_filters.accessibleName(), "필터 초기화")
        self.assertEqual(bar.btn_clear_col_filters.accessibleName(), "열 필터 해제")
        self.assertEqual(bar.btn_review_filter.accessibleName(), REVIEW_CHIP_LABEL)
        self.assertEqual(bar.btn_vary_filter.accessibleName(), VARY_CHIP_LABEL)
        self.assertEqual(bar.btn_column_preset.accessibleName(), "열: 기본")
        self.assertEqual(bar.btn_row_group.accessibleName(), "그룹: 코덱 (12)")
        for name, _step in COLLAPSE_STEPS:
            self.assertTrue(getattr(bar, name).toolTip(), name)
        for name in ICON_ONLY_BUTTONS:
            self.assertTrue(getattr(bar, name).toolTip(), name)

    def test_dropdown_tooltips_carry_the_current_value(self) -> None:
        """접힌 열, 그룹 드롭다운 툴팁에 지금 값이 붙고 넓은 폭에서는 원래 툴팁만 남는지 검증"""
        bar = self._shown_bar(width=620)
        self.assertIn("열: 기본", bar.btn_column_preset.toolTip())
        self.assertIn("그룹: 코덱 (12)", bar.btn_row_group.toolTip())
        self.assertIn(ROW_GROUP_TOOLTIP, bar.btn_row_group.toolTip())

        bar.resize(WIDE_WIDTH_PX, 44)
        _APP.processEvents()
        self.assertEqual(bar.collapse_level, COLLAPSE_LEVEL_FULL)
        self.assertNotIn("현재 값", bar.btn_column_preset.toolTip())
        self.assertEqual(bar.btn_row_group.toolTip(), ROW_GROUP_TOOLTIP)
        self.assertEqual(bar.btn_review_filter.toolTip(), REVIEW_CHIP_TOOLTIP)
        self.assertEqual(bar.btn_vary_filter.toolTip(), VARY_CHIP_TOOLTIP)

    def test_chip_badges_show_the_count_only_while_collapsed(self) -> None:
        """열 필터 칩과 두 필터 칩이 접힌 동안에만 개수 배지를 보이는지 검증"""
        bar = self._shown_bar()
        self._at_level(bar, COLLAPSE_LEVEL_CHIPS)
        self.assertTrue(bar._clear_col_filters_badge.isVisible())
        self.assertTrue(bar._review_filter_badge.isVisible())
        self.assertTrue(bar._vary_filter_badge.isVisible())
        self.assertEqual(bar._clear_col_filters_badge.count, 2)
        self.assertEqual(bar._review_filter_badge.count, 3)
        self.assertEqual(bar._vary_filter_badge.count, 1)

        bar.set_active_column_filter_count(0)
        _APP.processEvents()
        self.assertFalse(bar._clear_col_filters_badge.isVisible())

        bar.resize(WIDE_WIDTH_PX, 44)
        _APP.processEvents()
        self.assertFalse(bar._review_filter_badge.isVisible())
        self.assertFalse(bar._vary_filter_badge.isVisible())

    def test_search_keeps_its_minimum_width_until_the_last_step(self) -> None:
        """검색창이 마지막 단계 전까지 기본 최소 폭을 지키고 마지막에만 더 줄어드는지 검증"""
        bar = self._shown_bar()
        self._at_level(bar, COLLAPSE_LEVEL_CHIPS)
        self.assertEqual(bar.search_edit.minimumWidth(), SEARCH_MIN_WIDTH_PX)

        self._at_level(bar, COLLAPSE_LEVEL_DROPDOWNS)
        self.assertEqual(bar.search_edit.minimumWidth(), SEARCH_COMPACT_MIN_WIDTH_PX)

    def test_longer_exception_label_recollapses_at_the_wide_boundary(self) -> None:
        """넓은 단계 경계 폭에서 예외 규칙 개수가 늘어도 버튼이 다시 접히지 않고 배지로만 보이는지 검증"""
        bar = self._shown_bar()
        boundary = WIDE_WIDTH_PX
        for width in PROBE_WIDTHS_PX:
            bar.resize(width, 44)
            _APP.processEvents()
            if bar.collapse_level != 0:
                break
            boundary = width
        bar.resize(boundary, 44)
        _APP.processEvents()
        self.assertEqual(bar.collapse_level, 0)
        bar.set_disabled_exception_count(3)
        _APP.processEvents()
        button = bar.btn_edit_exceptions
        self.assertEqual(button.text(), "")
        self.assertEqual(bar.collapse_level, COLLAPSE_LEVEL_FULL)
        self.assertIn("비활성 규칙: 3개", button.toolTip())

    def test_no_button_text_is_wider_than_its_geometry(self) -> None:
        """어느 폭에서도 글자가 남아 있는 버튼의 폭이 sizeHint보다 작지 않은지 검증"""
        bar = self._shown_bar()
        clipped: list[str] = []
        for width in CLIPPING_WIDTHS_PX:
            bar.resize(width, 44)
            _APP.processEvents()
            for name, _step in COLLAPSE_STEPS:
                button = getattr(bar, name)
                if button.text() and button.geometry().width() < button.sizeHint().width():
                    clipped.append(f"{name}@{width}")
        self.assertEqual(clipped, [])

    def test_minimum_size_allows_collapsing_into_a_narrow_window(self) -> None:
        """최소 크기가 모든 글자를 편 폭이 아니라 가장 깊이 접힌 폭이라 좁은 창에서도 잘리지 않는지 검증"""
        bar = self._shown_bar()
        full_requirement = bar._required_width()
        self.assertEqual(bar.collapse_level, COLLAPSE_LEVEL_FULL)
        self.assertLess(bar.minimumSizeHint().width(), full_requirement)
        self.assertLess(bar.minimumSizeHint().width(), 700)

    def test_widening_restores_the_full_labels(self) -> None:
        """좁혔다가 다시 넓히면 모든 버튼이 전체 문구로 돌아오는지 검증"""
        bar = self._shown_bar()
        bar = self._shown_bar(width=560)
        _APP.processEvents()
        self.assertGreaterEqual(bar.collapse_level, COLLAPSE_LEVEL_CHIPS)
        self.assertEqual(bar.btn_column_preset.text(), "")

        bar.resize(WIDE_WIDTH_PX, 44)
        _APP.processEvents()
        self.assertEqual(bar.collapse_level, COLLAPSE_LEVEL_FULL)
        self.assertEqual(bar.btn_column_preset.text(), "열: 기본")
        self.assertEqual(bar.btn_row_group.text(), "그룹: 코덱 (12)")
        self.assertEqual(bar.btn_compact.text(), "")

    def test_labels_follow_later_state_changes(self) -> None:
        """접힌 뒤 프리셋, 그룹, 규칙 수가 바뀌어도 다시 펼치면 새 문구와 배지가 나오는지 검증"""
        bar = self._shown_bar(width=620)
        bar.set_column_preset("quality")
        bar.set_row_group("folder", label="폴더", count=4)
        bar.set_exception_tokens(["a", "b", "c"])
        _APP.processEvents()
        self.assertEqual(bar.btn_column_preset.text(), "")
        self.assertEqual(bar.btn_row_group.text(), "")
        self.assertEqual(bar.btn_edit_exceptions.text(), "")
        self.assertEqual(bar._exception_badge.count, 3)
        self.assertIn("열: 화질, 해상도", bar.btn_column_preset.toolTip())
        self.assertIn("그룹: 폴더 (4)", bar.btn_row_group.toolTip())

        bar.resize(WIDE_WIDTH_PX, 44)
        _APP.processEvents()
        self.assertEqual(bar.btn_column_preset.text(), "열: 화질, 해상도")
        self.assertEqual(bar.btn_row_group.text(), "그룹: 폴더 (4)")
        self.assertEqual(bar.btn_edit_exceptions.text(), "")
        self.assertEqual(bar._exception_badge.count, 3)

    def test_flat_button_states_use_palette_tokens(self) -> None:
        """필터 바 버튼이 두 테마에서 쉼은 투명, 호버는 bg_hover, 켜짐은 bg_selected로 그려지는지 검증"""
        from src.gui.styles.stylesheet import build_stylesheet

        for palette in (LIGHT_PALETTE, DARK_PALETTE):
            bar = _make_bar()
            self.addCleanup(bar.close)
            bar.setStyleSheet(build_stylesheet(palette))
            bar.show()
            bar.resize(WIDE_WIDTH_PX, 44)
            _APP.processEvents()

            button = bar.btn_column_preset
            self.assertEqual(bar.collapse_level, COLLAPSE_LEVEL_FULL)
            # - 쉼 상태 배경은 투명이라 따로 칠하지 않고 부모 필터 바의 카드 배경이 그대로 보임
            rest = _button_center_color(button)
            self.assertEqual((rest.alpha(), rest.name()), (0, "#000000"))

            _send_mouse_move(bar.search_edit, QPoint(4, 4))
            _APP.processEvents()
            _send_mouse_move(button, QPoint(button.width() // 2, button.height() // 2))
            _APP.processEvents()
            self.assertEqual(_button_center_color(button).name(), palette.bg_hover.lower())

            _send_mouse_move(bar.search_edit, QPoint(4, 4))
            _APP.processEvents()
            bar.btn_compact.setChecked(True)
            _APP.processEvents()
            self.assertEqual(_button_center_color(bar.btn_compact).name(), palette.bg_selected.lower())
            bar.set_compact(False)
            bar.hide()

    def test_chip_border_and_fill_survive_the_flat_button_rule(self) -> None:
        """여백 확인 칩은 평면 버튼 규칙보다 구체적인 선택자로 경고색 배경과 테두리를 유지하는지 검증"""
        from src.gui.styles.stylesheet import build_stylesheet

        for palette in (LIGHT_PALETTE, DARK_PALETTE):
            bar = _make_bar()
            self.addCleanup(bar.close)
            bar.setStyleSheet(build_stylesheet(palette))
            bar.show()
            bar.resize(WIDE_WIDTH_PX, 44)
            _APP.processEvents()
            image = bar.btn_review_filter.grab().toImage()
            filled = {
                image.pixelColor(x, image.height() // 2).name()
                for x in range(image.width())
            }
            self.assertIn(palette.badge_pending[0].lower(), filled)
            self.assertIn(palette.badge_pending[1].lower(), filled)

    def test_vary_chip_border_and_fill_use_the_taupe_tokens(self) -> None:
        """구간별 여백 차이 칩이 갈색 팔레트 토큰으로 테두리와 배경을 유지하는지 검증"""
        from src.gui.styles.stylesheet import build_stylesheet

        for palette in (LIGHT_PALETTE, DARK_PALETTE):
            bar = _make_bar()
            self.addCleanup(bar.close)
            bar.setStyleSheet(build_stylesheet(palette))
            bar.show()
            bar.resize(WIDE_WIDTH_PX, 44)
            _APP.processEvents()
            image = bar.btn_vary_filter.grab().toImage()
            filled = {
                image.pixelColor(x, image.height() // 2).name()
                for x in range(image.width())
            }
            self.assertIn(palette.badge_vary[0].lower(), filled)
            self.assertIn(palette.badge_vary[1].lower(), filled)

    def test_vary_chip_count_visibility_and_collapse(self) -> None:
        """구간별 여백 차이 칩이 개수 0이면 숨고, 개수가 있으면 접힘 때 배지로만 남는지 검증"""
        bar = _make_bar()
        self.addCleanup(bar.close)
        self.addCleanup(bar.hide)
        self.assertEqual(bar.btn_vary_filter.text(), f"{VARY_CHIP_LABEL} 1")
        self.assertEqual(bar.btn_vary_filter.accessibleName(), VARY_CHIP_LABEL)
        self.assertEqual(bar.btn_vary_filter.objectName(), "VaryFilterChip")

        bar.show()
        self._at_level(bar, COLLAPSE_LEVEL_CHIPS)
        self.assertEqual(bar.btn_vary_filter.text(), "")
        self.assertTrue(bar._vary_filter_badge.isVisible())
        self.assertEqual(bar._vary_filter_badge.count, 1)

        bar.set_vary_flagged_count(0)
        self.assertTrue(bar.btn_vary_filter.isHidden())

        bar.set_vary_flagged_count(2)
        self.assertFalse(bar.btn_vary_filter.isHidden())
        self._at_level(bar, COLLAPSE_LEVEL_FULL)
        self.assertEqual(bar.btn_vary_filter.text(), f"{VARY_CHIP_LABEL} 2")

    def test_chip_filters_changed_reports_both_chips(self) -> None:
        """두 칩 중 하나를 눌러도 두 칩의 켜짐 상태가 한 번에 알려지는지 검증"""
        bar = FilterTuningBar()
        self.addCleanup(bar.close)
        bar.set_review_flagged_count(1)
        bar.set_vary_flagged_count(1)
        events: list[tuple[bool, bool]] = []
        bar.chip_filters_changed.connect(lambda review, vary: events.append((review, vary)))

        bar.btn_vary_filter.click()
        bar.btn_review_filter.click()
        self.assertEqual(events, [(False, True), (True, True)])

        # - 초기화는 시그널 없이 두 칩을 함께 끔
        bar.clear_chip_filters()
        self.assertFalse(bar.review_filter_enabled)
        self.assertFalse(bar.vary_filter_enabled)
        self.assertEqual(events, [(False, True), (True, True)])

    def test_chip_badge_styles_use_their_own_palette_tokens(self) -> None:
        """두 칩 접힘 배지가 각자의 팔레트 토큰을 쓰는지 검증"""
        review_css = count_badge_style(DARK_PALETTE, CHIP_REVIEW)
        vary_css = count_badge_style(DARK_PALETTE, CHIP_VARY)
        clear_css = count_badge_style(DARK_PALETTE, "clear")
        self.assertIn(DARK_PALETTE.badge_pending[2], review_css)
        self.assertIn(DARK_PALETTE.badge_vary[2], vary_css)
        self.assertIn(DARK_PALETTE.accent_blue, clear_css)
        self.assertIn(f"padding: 0px {2}px;", review_css)

    def test_compact_checked_state_survives_collapse(self) -> None:
        """좁게 보기 버튼이 아이콘만으로도 켜짐 상태를 유지하고 접근성 이름을 지키는지 검증"""
        bar = self._shown_bar()
        bar.btn_compact.click()
        bar.resize(620, 44)
        _APP.processEvents()
        self.assertEqual(bar.btn_compact.text(), "")
        self.assertEqual(bar.btn_compact.accessibleName(), COMPACT_BUTTON_TEXT)
        self.assertEqual(bar.btn_compact.toolTip(), COMPACT_TOOLTIP)
        self.assertTrue(bar.btn_compact.isChecked())
        self.assertTrue(bar.is_compact())
        bar.set_compact(False)
        self.assertFalse(bar.btn_compact.isChecked())

    def test_hidden_before_show_keeps_full_labels(self) -> None:
        """화면에 보이기 전에는 폭과 무관하게 모든 문구를 그대로 두는지 검증"""
        bar = _make_bar()
        self.addCleanup(bar.close)
        bar.resize(520, 44)
        self.assertFalse(bar.isVisible())
        self.assertEqual(bar.collapse_level, COLLAPSE_LEVEL_FULL)
        self.assertEqual(bar.btn_compact.text(), "")
        self.assertEqual(bar.btn_column_preset.text(), "열: 기본")

    def test_icon_only_buttons_never_show_text_at_any_width(self) -> None:
        """좁게 보기와 예외 규칙 버튼이 보통 폭과 좁은 폭 모두에서 글자 없이 아이콘만 보이는지 검증"""
        bar = self._shown_bar()
        for width in (WIDE_WIDTH_PX, 900, 700, 620, 520):
            bar.resize(width, 44)
            _APP.processEvents()
            for name in ICON_ONLY_BUTTONS:
                button = getattr(bar, name)
                self.assertEqual(button.text(), "", f"{name}@{width}")
                self.assertFalse(button.icon().isNull(), f"{name}@{width}")
                self.assertGreaterEqual(button.width(), button.iconSize().width(), f"{name}@{width}")
        self.assertEqual(bar.btn_compact.accessibleName(), COMPACT_BUTTON_TEXT)
        self.assertEqual(bar.btn_edit_exceptions.accessibleName(), EXCEPTION_BUTTON_TEXT)

    def test_exception_badge_follows_the_active_rule_count(self) -> None:
        """예외 규칙 배지가 활성 규칙이 있을 때만 보이고 개수를 따르며, 다 지우면 숨는지 검증"""
        bar = self._shown_bar()
        self.assertTrue(bar._exception_badge.isVisible())
        self.assertEqual(bar._exception_badge.count, 2)

        bar.set_exception_tokens(["only"])
        _APP.processEvents()
        self.assertTrue(bar._exception_badge.isVisible())
        self.assertEqual(bar._exception_badge.count, 1)
        self.assertIn("활성 규칙 (1개): only", bar.btn_edit_exceptions.toolTip())

        bar.set_exception_tokens([])
        _APP.processEvents()
        self.assertFalse(bar._exception_badge.isVisible())
        self.assertEqual(bar._exception_badge.count, 0)

        # - 비활성 규칙만 있어도 활성 수가 0이라 배지는 숨기고 툴팁에만 알림
        bar.set_disabled_exception_count(2)
        _APP.processEvents()
        self.assertFalse(bar._exception_badge.isVisible())
        self.assertIn("비활성 규칙: 2개", bar.btn_edit_exceptions.toolTip())

    def test_exception_button_sits_between_search_chips_and_view_controls(self) -> None:
        """예외 규칙 버튼이 검색과 필터 칩 뒤, 뷰 컨트롤 앞에 구분선으로 분리되어 놓이는지 검증"""
        bar = self._shown_bar()
        layout = bar.layout()
        assert layout is not None
        order: list[QWidget] = []
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                order.append(widget)
        exceptions_at = order.index(bar.btn_edit_exceptions)
        self.assertIs(order[exceptions_at - 1], bar._row_divider)
        self.assertIs(order[exceptions_at + 1], bar._exceptions_divider)
        self.assertEqual(order[exceptions_at + 2], bar.btn_column_preset)
        self.assertLess(order.index(bar.btn_review_filter), exceptions_at)

    def test_chip_resize_repositions_the_badge(self) -> None:
        """개수 배지를 겹쳐 둔 버튼 폭이 바뀌면 배지를 다시 붙이는지 검증"""
        bar = self._shown_bar(width=620)
        event = QEvent(QEvent.Type.Resize)
        bar.btn_clear_col_filters.resize(80, 22)
        self.assertFalse(bar.eventFilter(bar.btn_clear_col_filters, event))
        self.assertEqual(
            bar._clear_col_filters_badge.x(),
            80 - bar._clear_col_filters_badge.width() - COUNT_BADGE_OFFSET_PX,
        )
        self.assertFalse(bar.eventFilter(bar.btn_review_filter, event))
        self.assertFalse(bar.eventFilter(bar.btn_edit_exceptions, event))
        badge = bar._exception_badge
        self.assertGreaterEqual(badge.x(), 0)
        self.assertLessEqual(badge.x() + badge.width(), bar.btn_edit_exceptions.width())
        self.assertFalse(bar.eventFilter(bar.search_edit, event))

    def test_apply_theme_recolors_icons_and_badges(self) -> None:
        """테마를 바꾸면 접힌 상태를 유지한 채 아이콘과 배지 색이 새 팔레트를 따르는지 검증"""
        bar = self._shown_bar()
        self._at_level(bar, COLLAPSE_LEVEL_CHIPS)
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            bar.apply_theme(palette)
            self.assertFalse(bar.btn_column_preset.icon().isNull())
            self.assertFalse(bar.btn_clear_col_filters.icon().isNull())
            self.assertIn(palette.accent_blue, bar._clear_col_filters_badge.styleSheet())
            self.assertIn(palette.badge_pending[2], bar._review_filter_badge.styleSheet())
            self.assertIn(palette.badge_vary[2], bar._vary_filter_badge.styleSheet())
            self.assertIn(palette.accent_blue, bar._exception_badge.styleSheet())
            self.assertEqual(bar.btn_edit_exceptions.text(), "")
        self.assertEqual(bar.collapse_level, COLLAPSE_LEVEL_CHIPS)


if __name__ == "__main__":
    unittest.main()
