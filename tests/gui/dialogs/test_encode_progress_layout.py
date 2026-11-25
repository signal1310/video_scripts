"""인코딩 진행 창 위젯 배치 검증: 머리글 버튼과 종료 배너의 동작 연결, 상단 접기, 진행과 결과 탭 전환, 빈 상태 안내 문구"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from src.gui.dialogs.encode_progress_layout import (
    COLLAPSE_ICON_COLLAPSED,
    COLLAPSE_ICON_EXPANDED,
    COLLAPSE_NAME,
    EXPAND_NAME,
    IDLE_TEXT,
    PRIMARY_BUTTON_WIDTH_PX,
    SUMMARY_TAB_INDEX,
    ProgressActions,
    build_progress_widgets,
    build_progress_window_qss,
    set_collapse_look,
    set_top_area_collapsed,
)
from src.gui.dialogs.encode_summary_view import BRIEF_CARD_RADIUS_PX
from src.gui.styles.palettes import LIGHT_PALETTE

_APP = QApplication.instance() or QApplication([])


class _FakeMedia:
    """요약 탭 영상 행이 쓰는 동작을 받기만 하는 대역"""

    def open_file(self, path: str) -> None:
        """영상 열기"""

    def preview_file(self, path: str) -> None:
        """영상 미리 보기"""

    def compare_files(self, source: str) -> None:
        """화질 비교"""


class ProgressLayoutTests(unittest.TestCase):
    """배치 위젯이 받은 동작을 버튼과 배너에 연결하고, 탭 이름과 쪽 전환을 기본값으로 만드는지 검증"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.toggle = mock.Mock()
        self.stop_after = mock.Mock()
        self.stop_all = mock.Mock()
        self.cancel_close = mock.Mock()
        self.toggle_collapsed = mock.Mock()
        self.actions = ProgressActions(
            toggle_start=self.toggle,
            request_stop_after=self.stop_after,
            stop_all=self.stop_all,
            cancel_close=self.cancel_close,
        )
        self.widgets = build_progress_widgets(self.parent, self.actions, _FakeMedia(), self.toggle_collapsed)

    def test_header_buttons_call_their_actions(self) -> None:
        """시작/일시중지, 현재 영상 후 중지, 전체 중지 버튼이 각 동작을 한 번씩 호출함"""
        self.widgets.start_btn.click()
        self.widgets.after_btn.setEnabled(True)
        self.widgets.stop_all_btn.setEnabled(True)
        self.widgets.after_btn.click()
        self.widgets.stop_all_btn.click()
        self.toggle.assert_called_once()
        self.stop_after.assert_called_once()
        self.stop_all.assert_called_once()

    def test_collapse_button_has_its_own_callback_and_accessible_name(self) -> None:
        """상단 접기 버튼은 따로 받은 동작을 부르고 접힘 전에는 접기 이름과 닫기 아이콘을 씀"""
        self.assertEqual(self.widgets.collapse_btn.accessibleName(), COLLAPSE_NAME)
        self.widgets.collapse_btn.click()
        # - clicked 시그널은 눌림 상태를 함께 넘기므로 인자 수신 여부 대신 호출 횟수만 확인
        self.assertEqual(self.toggle_collapsed.call_count, 1)
        self.toggle.assert_not_called()

    def test_collapse_look_switches_icon_and_tooltip(self) -> None:
        """접힘 상태에 따라 접기 버튼의 접근성 이름과 툴팁이 펼치기로 바뀌고 되돌아옴"""
        button = self.widgets.collapse_btn
        set_collapse_look(button, collapsed=True)
        self.assertEqual(button.accessibleName(), EXPAND_NAME)
        self.assertIn("다시 표시", button.toolTip())
        set_collapse_look(button, collapsed=False)
        self.assertEqual(button.accessibleName(), COLLAPSE_NAME)
        self.assertIn("한 줄 요약으로 접", button.toolTip())
        self.assertNotEqual(COLLAPSE_ICON_EXPANDED, COLLAPSE_ICON_COLLAPSED)

    def test_set_top_area_collapsed_keeps_only_the_percent_in_the_slim_bar(self) -> None:
        """펼친 상태는 시간 줄과 전체 막대를, 접힌 상태는 진행률만 보이고 배치 전에는 모두 숨김"""
        set_top_area_collapsed(self.widgets, visible=True, collapsed=False)
        self.assertFalse(self.widgets.elapsed.isHidden())
        self.assertFalse(self.widgets.remaining.isHidden())
        self.assertFalse(self.widgets.overall_bar.isHidden())
        self.assertTrue(self.widgets.percent.isHidden())
        set_top_area_collapsed(self.widgets, visible=True, collapsed=True)
        self.assertTrue(self.widgets.elapsed.isHidden())
        self.assertTrue(self.widgets.remaining.isHidden())
        self.assertTrue(self.widgets.overall_bar.isHidden())
        self.assertFalse(self.widgets.percent.isHidden())
        set_top_area_collapsed(self.widgets, visible=False, collapsed=False)
        self.assertTrue(self.widgets.elapsed.isHidden())
        self.assertTrue(self.widgets.overall_bar.isHidden())
        self.assertTrue(self.widgets.percent.isHidden())

    def test_totals_panel_sits_inside_the_progress_page(self) -> None:
        """합계 칸은 진행 탭 안쪽에 있어 창 바깥 여백이 늘지 않음"""
        progress_page = self.widgets.pages.widget(1)
        self.assertIsNotNone(progress_page)
        assert progress_page is not None
        self.assertIn(self.widgets.totals, progress_page.findChildren(type(self.widgets.totals)))

    def test_start_button_is_the_emphasized_primary_button(self) -> None:
        """첫 버튼은 강조 상태이고 폭이 좁은 대신, 두 번째와 세 번째 버튼은 진행 전에 비활성인지 검증"""
        self.assertEqual(self.widgets.start_btn.property("primary"), "true")
        self.assertEqual(self.widgets.start_btn.minimumWidth(), PRIMARY_BUTTON_WIDTH_PX)
        self.assertLessEqual(self.widgets.start_btn.minimumWidth(), self.widgets.start_btn.maximumWidth())
        self.assertEqual(self.widgets.start_btn.accessibleName(), "인코딩 시작")
        self.assertFalse(self.widgets.after_btn.isEnabled())
        self.assertFalse(self.widgets.stop_all_btn.isEnabled())

    def test_brief_tile_rule_draws_each_tile_as_a_bordered_card(self) -> None:
        """브리핑 2x2 타일은 창 QSS에서 올린 바탕과 테두리, 카드 모서리 반경으로 칠함"""
        sheet = build_progress_window_qss(LIGHT_PALETTE)
        self.assertIn("QFrame#EncodeBriefTile {", sheet)
        self.assertIn(f"background-color: {LIGHT_PALETTE.bg_elevated};", sheet)
        self.assertIn(f"border: 1px solid {LIGHT_PALETTE.border_subtle};", sheet)
        self.assertIn(f"border-radius: {BRIEF_CARD_RADIUS_PX}px;", sheet)

    def test_close_banner_buttons_reach_their_actions(self) -> None:
        """종료 배너의 전체 중지와 종료 예약 취소 신호가 동작에 연결됨"""
        self.widgets.banner.stop_now_requested.emit()
        self.widgets.banner.cancel_requested.emit()
        self.stop_all.assert_called_once_with()
        self.cancel_close.assert_called_once_with()

    def test_view_switch_starts_on_summary_and_changes_the_page(self) -> None:
        """요약 탭이 처음 선택되고, 진행과 결과 탭으로 바꾸면 해당 쪽이 보임. 결과 탭 이름은 개수를 붙임"""
        self.assertEqual(self.widgets.view_switch.tabText(SUMMARY_TAB_INDEX), "요약")
        self.assertEqual(self.widgets.view_switch.tabText(1), "진행")
        self.assertEqual(self.widgets.view_switch.tabText(2), "결과 0")
        self.assertEqual(self.widgets.pages.currentIndex(), 0)
        self.widgets.view_switch.setCurrentIndex(1)
        self.assertEqual(self.widgets.pages.currentIndex(), 1)
        self.widgets.view_switch.setCurrentIndex(2)
        self.assertEqual(self.widgets.pages.currentIndex(), 2)

    def test_idle_notice_text_is_the_approved_sentence(self) -> None:
        """진행 중인 영상이 없을 때의 안내 문구는 승인된 문장을 그대로 씀"""
        self.assertEqual(self.widgets.idle.text(), IDLE_TEXT)


if __name__ == "__main__":
    unittest.main()
