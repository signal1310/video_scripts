"""재인코딩 메뉴의 단일 창 열기 항목, 상태 반영과 콜백 연결을 검증"""

import os
import unittest
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget

from src.core.encode.encode_start_state import ENCODE_RUNNING_TEXT, StartState
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS, SHORTCUT_SPEC_BY_ID
from src.gui.menus.menu_base import MenuContext
from src.gui.menus.menu_encode import (
    DATA_CLEANUP_DESCRIPTION,
    DATA_CLEANUP_TEXT,
    ENCODE_MENU_TITLE,
    OPEN_WINDOW_TEXT,
    START_DESCRIPTION,
    apply_start_state,
    build_encode_menu,
)
from tests._gui_helpers import make_menu_callbacks, menu_bar_of, release_modifier_keys

_APP = QApplication.instance() or QApplication([])
# - PyQt6 스텁이 QTest 정적 함수 형식 검사에서 실패하므로(tests/_gui_helpers.py와 같은 방식) 호출 가능한 형태로 받음
_send_key_sequence: Callable[..., None] = QTest.keySequence
_wait_for_active: Callable[..., bool] = QTest.qWaitForWindowActive


class EncodeMenuTests(unittest.TestCase):
    """재인코딩 메뉴 조립과 시작 항목 상태 반영을 검증"""

    def setUp(self) -> None:
        self.window = QMainWindow()
        self.addCleanup(self.window.deleteLater)
        self.started: list[str] = []
        self.callbacks = make_menu_callbacks()
        self.callbacks.on_start_encode = lambda: self.started.append("start")
        self.callbacks.on_open_encode_settings = lambda: self.started.append("settings")
        self.ctx = MenuContext(self.window)
        self.menu = build_encode_menu(self.ctx, menu_bar_of(self.window), self.callbacks)

    def test_menu_title_and_items_are_in_order(self) -> None:
        """재인코딩 메뉴가 창 열기, 설정, 데이터 정리 세 항목을 이 순서로 가지는지 검증"""
        titles = [action.text() for action in menu_bar_of(self.window).actions()]
        self.assertIn(ENCODE_MENU_TITLE, titles)
        self.assertEqual(
            [a.text() for a in self.menu.menu.actions() if not a.isSeparator()],
            [
                OPEN_WINDOW_TEXT,
                "재인코딩 설정...",
                DATA_CLEANUP_TEXT,
            ],
        )
        self.assertEqual(DATA_CLEANUP_TEXT, "데이터 정리 및 초기화...")
        self.assertEqual(
            DATA_CLEANUP_DESCRIPTION,
            "기록, 캐시, 임시 파일과 화면 배치, 설정을 한 창에서 골라 정리합니다.",
        )

    def test_separator_sits_between_settings_and_data_cleanup(self) -> None:
        """설정 항목과 데이터 정리 항목 사이에 구분선이 하나 있는지 검증"""
        actions = self.menu.menu.actions()
        self.assertEqual(len(actions), 4)
        self.assertIs(actions[0], self.menu.act_start)
        self.assertIs(actions[1], self.menu.act_settings)
        self.assertTrue(actions[2].isSeparator())
        self.assertIs(actions[3], self.menu.act_data_cleanup)

    def test_window_item_opens_the_window_under_the_start_shortcut(self) -> None:
        """창 열기 항목이 encode.start와 Ctrl+G 하나로만 연결되는지 검증"""
        self.assertEqual(OPEN_WINDOW_TEXT, "재인코딩 창 열기")
        self.assertIs(self.ctx.actions["encode.start"], self.menu.act_start)
        self.assertEqual(DEFAULT_SHORTCUTS["encode.start"], "Ctrl+G")

    def test_duplicate_progress_item_and_shortcut_are_gone(self) -> None:
        """중복 진행 창 항목과 Ctrl+Shift+G 단축키가 카탈로그에서 사라졌는지 검증"""
        self.assertEqual(
            [a.text() for a in self.menu.menu.actions()].count("재인코딩 진행 창 열기"), 0
        )
        self.assertNotIn("encode.open_progress", SHORTCUT_SPEC_BY_ID)
        self.assertNotIn("Ctrl+Shift+G", DEFAULT_SHORTCUTS.values())

    def test_tooltips_are_visible_for_disabled_reasons(self) -> None:
        """비활성 사유를 툴팁으로 보이도록 메뉴의 툴팁 표시가 켜져 있는지 검증"""
        self.assertTrue(self.menu.menu.toolTipsVisible())

    def test_items_are_registered_under_their_shortcut_ids(self) -> None:
        """남은 항목이 단축키 명령 ID로 등록되어 단축키 컨트롤러가 찾을 수 있는지 검증"""
        self.assertIs(self.ctx.actions["encode.start"], self.menu.act_start)
        self.assertIs(self.ctx.actions["encode.open_settings"], self.menu.act_settings)
        self.assertIs(self.ctx.actions["encode.open_data_cleanup"], self.menu.act_data_cleanup)

    def test_disabled_state_shows_the_reason_in_tooltip_and_status_tip(self) -> None:
        """비활성 상태는 사유를 툴팁과 상태 표시줄 문구로 같이 보이는지 검증"""
        apply_start_state(self.menu.act_start, StartState(enabled=False, reason=ENCODE_RUNNING_TEXT, pending=False))
        self.assertFalse(self.menu.act_start.isEnabled())
        self.assertEqual(self.menu.act_start.toolTip(), ENCODE_RUNNING_TEXT)
        self.assertEqual(self.menu.act_start.statusTip(), ENCODE_RUNNING_TEXT)

    def test_enabled_state_shows_the_description(self) -> None:
        """활성 상태는 기능 설명을 툴팁과 상태 표시줄 문구로 보이는지 검증"""
        apply_start_state(self.menu.act_start, StartState(enabled=True, reason="", pending=False))
        self.assertTrue(self.menu.act_start.isEnabled())
        self.assertEqual(self.menu.act_start.statusTip(), START_DESCRIPTION)

    def test_triggering_the_items_calls_their_callbacks(self) -> None:
        """두 항목을 실행하면 각 콜백이 호출되는지 검증"""
        self.menu.act_start.trigger()
        self.menu.act_settings.trigger()
        self.assertEqual(self.started, ["start", "settings"])

    def test_window_shortcut_opens_the_window_while_running(self) -> None:
        """진행 중에도 Ctrl+G가 창 열기 콜백을 부르는지 검증"""
        target = QWidget()
        self.addCleanup(target.deleteLater)
        self.addCleanup(release_modifier_keys)
        key = DEFAULT_SHORTCUTS["encode.start"]
        target.show()
        target.activateWindow()
        _wait_for_active(target)
        target.addAction(self.menu.act_start)
        self.menu.act_start.setShortcut(QKeySequence(key))
        apply_start_state(
            self.menu.act_start, StartState(enabled=True, reason=ENCODE_RUNNING_TEXT, pending=False)
        )
        _send_key_sequence(target, key)
        self.assertEqual(self.started, ["start"])



class DataCleanupItemTests(unittest.TestCase):
    """재인코딩 메뉴의 데이터 정리 항목이 파일 메뉴와 같은 콜백을 부르는지 검증"""

    def test_data_cleanup_item_opens_the_same_dialog_callback(self) -> None:
        window = QMainWindow()
        self.addCleanup(window.deleteLater)
        fired: list[str] = []
        callbacks = make_menu_callbacks()
        callbacks.on_open_data_cleanup = lambda: fired.append("cleanup")
        menu = build_encode_menu(MenuContext(window), menu_bar_of(window), callbacks)
        menu.act_data_cleanup.trigger()
        self.assertEqual(fired, ["cleanup"])
        self.assertEqual(menu.act_data_cleanup.statusTip(), DATA_CLEANUP_DESCRIPTION)


if __name__ == "__main__":
    unittest.main(verbosity=2)
