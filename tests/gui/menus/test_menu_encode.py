"""재인코딩 메뉴의 항목 구성, 시작 항목 활성 상태와 사유 표시, 콜백 연결을 검증"""

import os
import unittest
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget

from src.core.encode.encode_start_state import ENCODE_RUNNING_TEXT, StartState
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.menus.menu_base import MenuContext
from src.gui.menus.menu_encode import (
    ENCODE_MENU_TITLE,
    PROGRESS_DESCRIPTION,
    PROGRESS_DISABLED_TEXT,
    START_DESCRIPTION,
    apply_progress_state,
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
        self.callbacks.on_open_encode_progress = lambda: self.started.append("progress")
        self.ctx = MenuContext(self.window)
        self.menu = build_encode_menu(self.ctx, menu_bar_of(self.window), self.callbacks)

    def test_menu_title_and_items_are_in_order(self) -> None:
        """재인코딩 메뉴가 메뉴 막대에 있고 시작, 설정 항목을 이 순서로 가지는지 검증"""
        titles = [action.text() for action in menu_bar_of(self.window).actions()]
        self.assertIn(ENCODE_MENU_TITLE, titles)
        self.assertEqual(
            [a.text() for a in self.menu.menu.actions()],
            ["재인코딩 시작...", "재인코딩 진행 창 열기", "재인코딩 설정..."],
        )

    def test_tooltips_are_visible_for_disabled_reasons(self) -> None:
        """비활성 사유를 툴팁으로 보이도록 메뉴의 툴팁 표시가 켜져 있는지 검증"""
        self.assertTrue(self.menu.menu.toolTipsVisible())

    def test_items_are_registered_under_their_shortcut_ids(self) -> None:
        """두 항목이 단축키 명령 ID로 등록되어 단축키 컨트롤러가 찾을 수 있는지 검증"""
        self.assertIs(self.ctx.actions["encode.start"], self.menu.act_start)
        self.assertIs(self.ctx.actions["encode.open_settings"], self.menu.act_settings)
        self.assertIs(self.ctx.actions["encode.open_progress"], self.menu.act_progress)

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
        """세 항목을 실행하면 각 콜백이 호출되는지 검증"""
        self.menu.act_start.trigger()
        self.menu.act_progress.trigger()
        self.menu.act_settings.trigger()
        self.assertEqual(self.started, ["start", "progress", "settings"])

    def test_progress_item_is_disabled_with_reason_until_a_batch_starts(self) -> None:
        """배치 전에는 진행 창 항목이 비활성이고 사유를 툴팁으로 보이며, 배치 뒤에는 활성이고 설명을 보이는지 검증"""
        apply_progress_state(self.menu.act_progress, has_batch=False)
        self.assertFalse(self.menu.act_progress.isEnabled())
        self.assertEqual(self.menu.act_progress.toolTip(), PROGRESS_DISABLED_TEXT)
        apply_progress_state(self.menu.act_progress, has_batch=True)
        self.assertTrue(self.menu.act_progress.isEnabled())
        self.assertEqual(self.menu.act_progress.toolTip(), PROGRESS_DESCRIPTION)
        self.assertEqual(self.menu.act_progress.statusTip(), PROGRESS_DESCRIPTION)

    def test_progress_shortcut_key_opens_the_window_only_when_enabled(self) -> None:
        """기본 키 Ctrl+Shift+G가 활성 항목에서만 콜백을 부르고, 비활성 항목에서는 아무 일도 없는지 검증"""
        target = QWidget()
        self.addCleanup(target.deleteLater)
        self.addCleanup(release_modifier_keys)
        key = DEFAULT_SHORTCUTS["encode.open_progress"]
        target.show()
        target.activateWindow()
        _wait_for_active(target)
        target.addAction(self.menu.act_progress)
        self.menu.act_progress.setShortcut(QKeySequence(key))
        apply_progress_state(self.menu.act_progress, has_batch=False)
        _send_key_sequence(target, key)
        self.assertEqual(self.started, [])
        apply_progress_state(self.menu.act_progress, has_batch=True)
        _send_key_sequence(target, key)
        self.assertEqual(self.started, ["progress"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
