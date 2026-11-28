"""메뉴바 컨트롤러의 메뉴 순서, 인코딩 시작 항목 상태 갱신 시점, 인코더 시험 중 주기 확인을 검증"""

import os
import time
import unittest
from collections.abc import Callable
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow

from src.core.encode.encode_start_state import ENCODE_RUNNING_TEXT, ENCODER_CHECKING_TEXT, StartState
from src.gui.menus.menu_bar import MainMenuController
from src.gui.widgets.header_bar import HeaderBar
from tests._gui_helpers import make_menu_callbacks, menu_bar_of

_APP = QApplication.instance() or QApplication([])
_WAIT_SEC: float = 3.0


def _wait_until(predicate: Callable[[], bool], timeout_sec: float = _WAIT_SEC) -> bool:
    """이벤트를 처리하며 조건이 참이 될 때까지 기다리고, 시간 안에 참이면 참을 돌려줌"""
    end = time.monotonic() + timeout_sec
    while not predicate():
        if time.monotonic() > end:
            return False
        _APP.processEvents()
        time.sleep(0.01)
    return True


class MainMenuControllerEncodeTests(unittest.TestCase):
    """메뉴바 조립 순서와 인코딩 시작 항목 상태 연동을 검증"""

    def setUp(self) -> None:
        self.window = QMainWindow()
        self.addCleanup(self.window.deleteLater)
        self.state = StartState(enabled=True, reason="", pending=False)
        self.state_calls = 0
        callbacks = make_menu_callbacks()

        def get_state() -> StartState:
            self.state_calls += 1
            return self.state

        callbacks.get_encode_state = get_state
        self.menu = MainMenuController(self.window, callbacks)

    def test_menu_bar_order_places_encode_between_analysis_and_help(self) -> None:
        """메뉴 막대 순서가 파일, 편집, 보기, 분석, 인코딩, 도움말인지 검증"""
        titles = [action.text() for action in menu_bar_of(self.window).actions()]
        self.assertEqual(titles, ["파일(&F)", "편집(&E)", "보기(&V)", "분석(&A)", "인코딩(&N)", "도움말(&H)"])

    def test_encode_menu_has_no_duplicate_progress_item(self) -> None:
        """인코딩 메뉴가 창 열기 항목 하나만 두고 진행 창 항목을 따로 두지 않는지 검증"""
        menu = menu_bar_of(self.window).actions()[4].menu()
        assert menu is not None
        texts = [action.text() for action in menu.actions()]
        self.assertEqual(texts.count("인코딩 진행 창 열기"), 0)
        self.assertEqual(texts.count("인코딩 창 열기"), 1)
        self.assertFalse(hasattr(self.menu, "act_open_encode_progress"))

    def test_refresh_applies_the_disabled_reason_to_the_start_item(self) -> None:
        """비활성 상태를 계산하면 시작 항목이 비활성이고 사유를 툴팁으로 가지는지 검증"""
        self.state = StartState(enabled=False, reason=ENCODE_RUNNING_TEXT, pending=False)
        self.menu.refresh_encode_state()
        self.assertFalse(self.menu.act_start_encode.isEnabled())
        self.assertEqual(self.menu.act_start_encode.toolTip(), ENCODE_RUNNING_TEXT)

    def test_busy_change_refreshes_the_start_item(self) -> None:
        """분석 중 상태가 바뀌면 시작 항목 상태를 다시 계산하는지 검증"""
        before = self.state_calls
        self.menu.set_busy(True)
        self.assertGreater(self.state_calls, before)

    def test_opening_the_encode_menu_refreshes_the_start_item(self) -> None:
        """인코딩 메뉴가 열릴 때 시작 항목 상태를 다시 계산하는지 검증"""
        before = self.state_calls
        encode_menu = menu_bar_of(self.window).actions()[4].menu()
        assert encode_menu is not None
        encode_menu.aboutToShow.emit()
        self.assertGreater(self.state_calls, before)

    def test_pending_trial_is_rechecked_until_it_finishes(self) -> None:
        """인코더 시험이 끝나지 않은 동안 주기 확인으로 상태를 다시 계산하고, 끝나면 활성으로 바뀌는지 검증"""
        self.state = StartState(enabled=False, reason=ENCODER_CHECKING_TEXT, pending=True)
        self.menu.refresh_encode_state()
        self.assertFalse(self.menu.act_start_encode.isEnabled())
        self.state = StartState(enabled=True, reason="", pending=False)
        self.assertTrue(_wait_until(lambda: self.menu.act_start_encode.isEnabled()))

    def test_refresh_forwards_the_start_state_to_the_header_button(self) -> None:
        """메뉴 시작 상태 계산이 상단 인코딩 시작 버튼에도 같은 상태로 전달되는지 검증"""
        header = mock.Mock(spec=HeaderBar)
        self.window.header_bar = header  # type: ignore[attr-defined]
        self.state = StartState(enabled=False, reason=ENCODE_RUNNING_TEXT, pending=False)
        self.menu.refresh_encode_state()
        header.set_encode_start_state.assert_called_once_with(self.state)

    def test_sync_frame_analysis_state_recalculates_the_encode_start_item(self) -> None:
        """화질 분석 실행 상태가 바뀌면 인코딩 시작 항목 상태도 다시 계산하는지 검증"""
        before = self.state_calls
        self.menu.sync_frame_analysis_state(has_list=True, has_selection=False, running=True)
        self.assertGreater(self.state_calls, before)

    def test_sync_frame_analysis_state_switches_the_header_stop_button(self) -> None:
        """화질 분석 실행 상태가 바뀔 때 헤더 중단 버튼에 실행 여부와 작업 종류를 전달하는지 검증"""
        header = mock.Mock(spec=HeaderBar)
        self.window.header_bar = header  # type: ignore[attr-defined]
        self.menu.sync_frame_analysis_state(
            has_list=True, has_selection=False, running=True, crop_running=True
        )
        header.set_frame_analysis_running.assert_called_once_with(True, True)
        self.menu.sync_frame_analysis_state(has_list=True, has_selection=False, running=False)
        header.set_frame_analysis_running.assert_called_with(False, False)

    def test_selection_only_update_does_not_recompute_the_encode_start_item(self) -> None:
        """선택만 바뀐 화질 분석 상태 갱신은 인코딩 시작 항목을 다시 계산하지 않는지 검증"""
        self.menu.sync_frame_analysis_state(has_list=True, has_selection=False, running=False)
        before = self.state_calls
        self.menu.sync_frame_analysis_state(has_list=True, has_selection=True, running=False)
        self.assertEqual(self.state_calls, before)
        self.menu.sync_frame_analysis_state(
            has_list=True, has_selection=True, running=False, crop_running=True
        )
        self.assertGreater(self.state_calls, before)

    def test_sync_analyze_mode_checks_all_three_ladder_items(self) -> None:
        """기본 분석 모드 동기화가 빠른 분석, 정밀 분석, 화질 분석 세 항목을 모두 반영하는지 검증"""
        for mode in ("fast", "full", "frame"):
            self.menu.sync_analyze_mode(mode)
            self.assertIn(mode, self.menu._analyze_mode_actions)
            self.assertTrue(self.menu._analyze_mode_actions[mode].isChecked())

    def test_frame_analysis_actions_follow_list_selection_and_running_state(self) -> None:
        """화질 분석과 여백 감지 항목이 목록, 선택, 실행 상태에 맞춰 비활성 사유와 함께 바뀌는지 검증"""
        from src.gui.menus.menu_bar import (
            FRAME_ANALYSIS_NO_LIST_TEXT,
            FRAME_ANALYSIS_NO_SELECTION_TEXT,
            FRAME_ANALYSIS_RUNNING_TEXT,
        )

        self.menu.sync_frame_analysis_state(has_list=False, has_selection=False, running=False)
        self.assertFalse(self.menu.act_frame_all.isEnabled())
        self.assertFalse(self.menu.act_crop_all.isEnabled())
        self.assertEqual(self.menu.act_frame_all.toolTip(), FRAME_ANALYSIS_NO_LIST_TEXT)
        self.assertEqual(self.menu.act_frame_selected.toolTip(), FRAME_ANALYSIS_NO_LIST_TEXT)
        self.assertEqual(self.menu.act_crop_all.toolTip(), FRAME_ANALYSIS_NO_LIST_TEXT)
        self.menu.sync_frame_analysis_state(has_list=True, has_selection=False, running=False)
        self.assertTrue(self.menu.act_frame_all.isEnabled())
        self.assertTrue(self.menu.act_crop_all.isEnabled())
        self.assertFalse(self.menu.act_frame_selected.isEnabled())
        self.assertFalse(self.menu.act_crop_selected.isEnabled())
        self.assertEqual(self.menu.act_frame_selected.toolTip(), FRAME_ANALYSIS_NO_SELECTION_TEXT)
        self.menu.sync_frame_analysis_state(has_list=True, has_selection=True, running=True)
        for action in (
            self.menu.act_frame_selected,
            self.menu.act_frame_all,
            self.menu.act_crop_selected,
            self.menu.act_crop_all,
        ):
            self.assertFalse(action.isEnabled())
            self.assertEqual(action.toolTip(), FRAME_ANALYSIS_RUNNING_TEXT)
        self.menu.set_busy(True)
        self.assertFalse(self.menu.act_frame_all.isEnabled())

    def test_crop_scan_running_disables_all_four_actions_with_the_crop_reason(self) -> None:
        """여백 감지가 실행 중이면 네 항목 모두 감지 중 사유로 비활성화하는지 검증"""
        from src.gui.menus.menu_bar import CROP_SCAN_RUNNING_TEXT

        self.menu.sync_frame_analysis_state(
            has_list=True, has_selection=True, running=True, crop_running=True
        )
        for action in (
            self.menu.act_frame_selected,
            self.menu.act_frame_all,
            self.menu.act_crop_selected,
            self.menu.act_crop_all,
        ):
            self.assertFalse(action.isEnabled())
            self.assertEqual(action.toolTip(), CROP_SCAN_RUNNING_TEXT)
            self.assertEqual(action.statusTip(), CROP_SCAN_RUNNING_TEXT)

    def test_busy_analysis_shows_the_analysis_reason_for_both_frame_actions(self) -> None:
        """목록과 선택이 있어도 일반 분석 중에는 두 화질 분석 항목이 분석 중 사유를 보이는지 검증"""
        from src.gui.menus.menu_bar import FRAME_ANALYSIS_BUSY_TEXT

        self.menu.sync_frame_analysis_state(has_list=True, has_selection=True, running=False)
        self.menu.set_busy(True)
        for action in (
            self.menu.act_frame_selected,
            self.menu.act_frame_all,
            self.menu.act_crop_selected,
            self.menu.act_crop_all,
        ):
            self.assertFalse(action.isEnabled())
            self.assertEqual(action.toolTip(), FRAME_ANALYSIS_BUSY_TEXT)
            self.assertEqual(action.statusTip(), FRAME_ANALYSIS_BUSY_TEXT)
    def test_data_cleanup_items_are_disabled_together_while_busy(self) -> None:
        """작업 실행 중에는 파일과 인코딩 메뉴의 데이터 정리 항목이 함께 비활성화되는지 검증"""
        self.menu.set_busy(True)
        self.assertFalse(self.menu.act_open_data_cleanup.isEnabled())
        self.assertFalse(self.menu.act_encode_data_cleanup.isEnabled())
        self.menu.set_busy(False)
        self.assertTrue(self.menu.act_open_data_cleanup.isEnabled())
        self.assertTrue(self.menu.act_encode_data_cleanup.isEnabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
