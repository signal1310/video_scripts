"""재인코딩 진행 창 칩과 전환 막대 검증: 칩 줄바꿈, 진행 칩의 카드 전환과 진행률, 키보드 선택, 진행과 결과 전환, 팔레트 꾸밈

- 화면은 띄우지 않고 크기 계산과 신호, 스타일시트 문자열로 확인
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QPushButton

from src.gui.dialogs.encode_progress_card import JobCard
from src.gui.dialogs.encode_progress_chips import (
    CHIP_MAX_COLUMNS,
    CHIP_MIN_WIDTH_PX,
    CHIP_SPACING_PX,
    CardChips,
    FilterChip,
    JobChip,
    SegmentSwitch,
    chip_columns,
)
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE

_APP = QApplication.instance() or QApplication([])
_ELLIPSIS: str = "…"
_LONG_NAME: str = "a-very-long-video-name-for-the-chip-that-keeps-going.mp4"
_NARROW_WIDTH_PX: int = 300
_CHIP_WIDTH_PX: int = 460
# - 이름 칸에 순번, 줄임표, 확장자가 모두 들어갈 만큼 넓은 칩 폭. 최소 칩 폭은 글꼴에 따라 확장자까지 못 담을 수 있음
_NAME_ROOMY_CHIP_WIDTH_PX: int = 200
_LARGE_FONT_PT: float = 13.0


class JobChipTests(unittest.TestCase):
    """진행 칩은 순번과 줄임 이름, 진행률 퍼센트를 보여 주고 키보드로 고를 수 있음"""

    def test_fraction_sets_the_percentage_and_clamps_missing_values_to_zero(self) -> None:
        chip = JobChip(2, "a-very-long-video-name-for-the-chip.mp4")
        chip.set_fraction(0.42)
        self.assertEqual(chip._percent.text(), "42%")
        self.assertEqual(chip._bar.value(), 42)
        chip.set_fraction(None)
        self.assertEqual(chip._percent.text(), "0%")
        chip.set_fraction(1.7)
        self.assertEqual(chip._percent.text(), "100%")

    def test_size_hints_come_from_the_inner_layout_not_the_button_text(self) -> None:
        """칩의 권장 크기는 안쪽 레이아웃의 크기와 같아야 이름과 진행 막대가 잘리지 않음"""
        chip = JobChip(1, "a.mp4")
        self.assertEqual(chip.sizeHint().height(), chip._body.sizeHint().height())
        self.assertEqual(chip.minimumSizeHint().height(), chip._body.minimumSize().height())

    def test_long_name_is_elided_to_the_chip_width_and_tooltip_keeps_the_full_name(self) -> None:
        """칩 폭에 안 맞는 이름은 가운데를 줄임표로 줄여 순번과 확장자를 남기고, 전체 이름은 툴팁과 접근성 이름에 남김"""
        chip = JobChip(4, _LONG_NAME)
        chip.resize(_NAME_ROOMY_CHIP_WIDTH_PX, chip.sizeHint().height())
        chip.show()
        QApplication.processEvents()
        text = chip._name.text()
        self.assertIn(_ELLIPSIS, text)
        self.assertTrue(text.startswith("4. "))
        self.assertTrue(text.endswith(".mp4"))
        self.assertLessEqual(chip._name.fontMetrics().horizontalAdvance(chip._name.text()), chip._name.width())
        self.assertEqual(chip.toolTip(), f"4. {_LONG_NAME}")
        self.assertEqual(chip.accessibleName(), f"4. {_LONG_NAME}")

    def test_selected_chip_name_uses_primary_text_and_bold(self) -> None:
        """선택한 칩의 이름 글자는 기본 글자 색과 굵기로 강조함"""
        chip = JobChip(1, "a.mp4")
        chip.apply_palette(DARK_PALETTE)
        self.assertIn(
            f"QPushButton#EncodeJobChip:checked QLabel#EncodeJobChipName {{ color: {DARK_PALETTE.text_primary}; font-weight: 600; }}",
            chip.styleSheet(),
        )

    def test_full_name_is_in_the_tooltip_and_accessible_name(self) -> None:
        chip = JobChip(3, "clip.mp4")
        self.assertEqual(chip.toolTip(), "3. clip.mp4")
        self.assertEqual(chip.accessibleName(), "3. clip.mp4")

    def test_space_key_selects_the_chip(self) -> None:
        """탭으로 포커스를 준 칩은 스페이스바로 선택됨"""
        chip = JobChip(1, "a.mp4")
        chip.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        chip.setFocus()
        QApplication.sendEvent(chip, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
        QApplication.sendEvent(chip, QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
        self.assertTrue(chip.isChecked())


class ChipColumnsTests(unittest.TestCase):
    """칩 한 줄의 칸 수는 칩 최소 폭과 간격으로 정해지고, 최대 칸 수를 넘지 않음"""

    def test_wide_row_takes_the_maximum_columns(self) -> None:
        self.assertEqual(chip_columns(442, CHIP_SPACING_PX, CHIP_MIN_WIDTH_PX, CHIP_MAX_COLUMNS), CHIP_MAX_COLUMNS)
        self.assertEqual(chip_columns(10_000, CHIP_SPACING_PX, CHIP_MIN_WIDTH_PX, CHIP_MAX_COLUMNS), CHIP_MAX_COLUMNS)

    def test_three_chips_need_exactly_their_minimum_widths_and_gaps(self) -> None:
        exact = CHIP_MAX_COLUMNS * CHIP_MIN_WIDTH_PX + (CHIP_MAX_COLUMNS - 1) * CHIP_SPACING_PX
        self.assertEqual(chip_columns(exact, CHIP_SPACING_PX, CHIP_MIN_WIDTH_PX, CHIP_MAX_COLUMNS), CHIP_MAX_COLUMNS)
        self.assertEqual(chip_columns(exact - 1, CHIP_SPACING_PX, CHIP_MIN_WIDTH_PX, CHIP_MAX_COLUMNS), CHIP_MAX_COLUMNS - 1)

    def test_narrow_or_empty_width_keeps_one_column(self) -> None:
        self.assertEqual(chip_columns(0, CHIP_SPACING_PX, CHIP_MIN_WIDTH_PX, CHIP_MAX_COLUMNS), 1)
        self.assertEqual(chip_columns(-20, CHIP_SPACING_PX, CHIP_MIN_WIDTH_PX, CHIP_MAX_COLUMNS), 1)


class CardChipsTests(unittest.TestCase):
    """영상 카드마다 칩 하나를 두고, 칩을 누르면 그 카드를 보여 주며, 카드가 없으면 전체를 숨김"""

    def setUp(self) -> None:
        self.chips = CardChips()
        self.first = JobCard("a.mp4")
        self.second = JobCard("b.mp4")

    def tearDown(self) -> None:
        self.chips.deleteLater()

    def test_hidden_without_cards_and_first_card_is_selected(self) -> None:
        self.assertTrue(self.chips.isHidden())
        self.chips.add_card(self.first, 1, "a.mp4")
        self.chips.add_card(self.second, 2, "b.mp4")
        self.assertFalse(self.chips.isHidden())
        self.assertEqual(self.chips.chip_count(), 2)
        self.assertIs(self.chips._stack.currentWidget(), self.first)

    def test_three_chips_share_one_row_at_equal_widths_in_a_460px_window(self) -> None:
        """칩 세 개는 한 줄에 같은 폭으로 놓이고, 칩 높이는 안쪽 레이아웃 최소 높이 이상임"""
        self.chips.resize(_CHIP_WIDTH_PX, 120)
        self.chips.show()
        self._add_three_cards()
        QApplication.processEvents()
        geometries = [chip.geometry() for _, chip in self.chips._entries]
        self.assertEqual(len({geometry.y() for geometry in geometries}), 1)
        widths = [geometry.width() for geometry in geometries]
        self.assertLessEqual(max(widths) - min(widths), 1)
        self.assertGreaterEqual(min(widths), CHIP_MIN_WIDTH_PX)
        for _, chip in self.chips._entries:
            self.assertGreaterEqual(chip.height(), chip.minimumSizeHint().height())

    def test_narrow_window_adds_a_row_and_keeps_each_chip_whole(self) -> None:
        """폭이 좁아지면 칩 줄이 두 줄로 늘고, 칩은 최소 크기보다 작아지지 않음"""
        self.chips.resize(_CHIP_WIDTH_PX, 200)
        self.chips.show()
        self._add_three_cards()
        QApplication.processEvents()
        self.chips.resize(_NARROW_WIDTH_PX, 200)
        QApplication.processEvents()
        self.assertEqual(len({chip.geometry().y() for _, chip in self.chips._entries}), 2)
        for _, chip in self.chips._entries:
            self.assertGreaterEqual(chip.width(), chip.minimumSizeHint().width())
            self.assertGreaterEqual(chip.height(), chip.minimumSizeHint().height())

    def test_chips_are_never_narrower_than_their_minimum_at_a_large_font(self) -> None:
        """글꼴이 커도 칩 칸의 폭이 칩 최소 폭보다 좁아지지 않아 이름과 퍼센트가 잘리지 않음"""
        original = QApplication.font()
        self.addCleanup(QApplication.setFont, original)
        large = QApplication.font()
        large.setPointSizeF(_LARGE_FONT_PT)
        QApplication.setFont(large)
        self.chips.resize(_CHIP_WIDTH_PX, 120)
        self.chips.show()
        self._add_three_cards()
        QApplication.processEvents()
        for _, chip in self.chips._entries:
            self.assertGreaterEqual(chip.width(), chip.minimumSizeHint().width())

    def _add_three_cards(self) -> None:
        third = JobCard("c.mp4")
        self.chips.add_card(self.first, 1, "a.mp4")
        self.chips.add_card(self.second, 2, "b.mp4")
        self.chips.add_card(third, 3, "c.mp4")

    def test_clicking_a_chip_shows_its_card(self) -> None:
        self.chips.add_card(self.first, 1, "a.mp4")
        self.chips.add_card(self.second, 2, "b.mp4")
        self.chips._entries[1][1].setChecked(True)
        self.assertIs(self.chips._stack.currentWidget(), self.second)

    def test_removing_the_shown_card_selects_the_first_remaining_chip(self) -> None:
        self.chips.add_card(self.first, 1, "a.mp4")
        self.chips.add_card(self.second, 2, "b.mp4")
        self.chips._entries[1][1].setChecked(True)
        self.chips.remove_card(self.second)
        self.assertEqual(self.chips.chip_count(), 1)
        self.assertTrue(self.chips._entries[0][1].isChecked())
        self.chips.remove_card(self.first)
        self.assertTrue(self.chips.isHidden())

    def test_progress_reaches_only_the_matching_chip(self) -> None:
        self.chips.add_card(self.first, 1, "a.mp4")
        self.chips.add_card(self.second, 2, "b.mp4")
        self.chips.set_progress(self.second, 0.5)
        self.assertEqual(self.chips._entries[1][1]._percent.text(), "50%")
        self.assertEqual(self.chips._entries[0][1]._percent.text(), "0%")

    def test_pre_encode_reaches_only_the_matching_chip_bar(self) -> None:
        """준비 단계 전환은 그 카드의 칩 막대만 주황과 기본색 사이에서 바꿈"""
        self.chips.add_card(self.first, 1, "a.mp4")
        self.chips.add_card(self.second, 2, "b.mp4")
        self.chips.apply_palette(DARK_PALETTE)
        self.chips.set_pre_encode(self.second, True)
        self.assertIn(DARK_PALETTE.accent_orange, self.chips._entries[1][1]._bar.styleSheet())
        self.assertIn(DARK_PALETTE.progress_fill, self.chips._entries[0][1]._bar.styleSheet())
        self.chips.set_pre_encode(self.second, False)
        self.assertIn(DARK_PALETTE.progress_fill, self.chips._entries[1][1]._bar.styleSheet())

    def test_single_panel_setting_hides_only_the_chip_row(self) -> None:
        self.chips.add_card(self.first, 1, "a.mp4")
        self.chips.set_tab_bar_visible(False)
        self.assertTrue(self.chips._chip_host.isHidden())
        self.assertFalse(self.chips.isHidden())

    def test_palette_styles_chips_with_tokens(self) -> None:
        self.chips.add_card(self.first, 1, "a.mp4")
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette.name):
                self.chips.apply_palette(palette)
                style = self._chip_style()
                self.assertIn(f"background: {palette.bg_card}", style)
                self.assertIn(f"border: 1px solid {palette.accent_blue}", style)
                self.assertIn(f"color: {palette.text_muted}", style)

    def _chip_style(self) -> str:
        return self.chips._entries[0][1].styleSheet()


class SegmentSwitchTests(unittest.TestCase):
    """진행과 결과 전환은 탭 막대와 같은 메서드로 쓰고, 선택이 바뀌면 신호를 보냄"""

    def test_first_tab_starts_selected_and_switching_emits_the_index(self) -> None:
        switch = SegmentSwitch()
        self.assertEqual(switch.addTab("진행"), 0)
        self.assertEqual(switch.addTab("결과 0"), 1)
        self.assertEqual(switch.currentIndex(), 0)
        emitted: list[int] = []
        switch.currentChanged.connect(emitted.append)
        switch.setCurrentIndex(1)
        self.assertEqual((switch.currentIndex(), emitted), (1, [1]))

    def test_tab_text_can_show_the_result_count(self) -> None:
        switch = SegmentSwitch()
        switch.addTab("진행")
        switch.addTab("결과 0")
        switch.setTabText(1, "결과 3")
        self.assertEqual(switch.tabText(1), "결과 3")

    def test_segment_switch_paints_its_styled_background(self) -> None:
        """Qt6가 트랙 배경과 테두리를 그리려면 스타일 배경 속성이 켜져 있어야 함"""
        switch = SegmentSwitch()
        self.assertTrue(switch.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
        self.assertEqual(switch.objectName(), "EncodeSegmentSwitch")

    def test_palette_uses_elevated_track_and_highlighted_selection(self) -> None:
        """전환 막대는 올린 바탕과 테두리로 경계를 세우고, 선택 탭은 선택 바탕과 강조 테두리로 구분함"""
        switch = SegmentSwitch()
        switch.addTab("진행")
        switch.apply_palette(DARK_PALETTE)
        self.assertIn(f"background: {DARK_PALETTE.bg_elevated}", switch.styleSheet())
        self.assertIn(f"border: 1px solid {DARK_PALETTE.border_subtle}", switch.styleSheet())
        selected_style = switch._chips[0].styleSheet()
        self.assertIn(f"background: {DARK_PALETTE.bg_selected}", selected_style)
        self.assertIn(f"border: 1px solid {DARK_PALETTE.action_primary_border}", selected_style)


class FilterChipTests(unittest.TestCase):
    """필터 칩은 누르는 칩을 고르는 상태로 두고, 팔레트 토큰으로 선택과 기본 색을 칠함"""

    def test_filter_chip_is_checkable_and_styled_with_selection_tokens(self) -> None:
        chip = FilterChip("완료 1")
        self.assertIsInstance(chip, QPushButton)
        self.assertTrue(chip.isCheckable())
        chip.apply_palette(LIGHT_PALETTE)
        self.assertIn(f"border: 1px solid {LIGHT_PALETTE.accent_blue}", chip.styleSheet())


if __name__ == "__main__":
    unittest.main(verbosity=2)
