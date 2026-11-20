"""재인코딩 진행 창 칩과 전환 막대 검증: 칩 줄바꿈, 진행 칩의 카드 전환과 진행률, 키보드 선택, 진행과 결과 전환, 팔레트 꾸밈

- 화면은 띄우지 않고 크기 계산과 신호, 스타일시트 문자열로 확인
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

from src.gui.dialogs.encode_progress_card import JobCard
from src.gui.dialogs.encode_progress_chips import CardChips, FilterChip, FlowLayout, JobChip, SegmentSwitch
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE

_APP = QApplication.instance() or QApplication([])


class FlowLayoutTests(unittest.TestCase):
    """칩은 폭이 좁아지면 다음 줄로 넘어가 높이가 늘어남"""

    def test_narrow_width_wraps_chips_onto_more_lines(self) -> None:
        host = QWidget()
        flow = FlowLayout(host)
        chips = [FilterChip(f"완료 {index}", host) for index in range(4)]
        for chip in chips:
            flow.addWidget(chip)
        wide = flow.heightForWidth(2000)
        narrow = flow.heightForWidth(chips[0].sizeHint().width() + 4)
        self.assertGreater(narrow, wide)
        self.assertEqual(flow.count(), 4)
        self.assertIsNotNone(flow.itemAt(0))
        self.assertIsNone(flow.itemAt(9))
        self.assertTrue(flow.hasHeightForWidth())
        self.assertEqual(flow.expandingDirections(), Qt.Orientation(0))
        host.setGeometry(0, 0, 2000, 200)
        flow.setGeometry(host.rect())
        self.assertGreaterEqual(flow.minimumSize().width(), chips[0].sizeHint().width())
        self.assertIsNotNone(flow.takeAt(0))
        self.assertEqual(flow.count(), 3)
        host.deleteLater()


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

    def test_palette_uses_elevated_track_and_card_selection(self) -> None:
        switch = SegmentSwitch()
        switch.addTab("진행")
        switch.apply_palette(DARK_PALETTE)
        self.assertIn(f"background: {DARK_PALETTE.bg_elevated}", switch.styleSheet())
        self.assertIn(f"background: {DARK_PALETTE.bg_card}", switch._chips[0].styleSheet())


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
