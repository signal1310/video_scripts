"""출력 해상도 지정 메뉴의 항목 상태와 화질 분석 후보 적용 항목 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMenu

from src.core.resolution_tiers import RESOLUTION_TIERS
from src.core.smart_action import SmartAction
from src.gui.menus.manual_target_menu import (
    FRAME_CANDIDATES_LABEL,
    MANUAL_TARGET_AUTO_LABEL,
    MANUAL_TARGET_CLEAR_ALL_LABEL,
    MANUAL_TARGET_KEEP_LABEL,
    _menu_text,
    build_manual_target_options,
    fill_manual_target_menu,
    is_manual_target_blocked,
    manual_target_action_id,
    tier_menu_label,
)
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])


class ManualTargetMenuTests(unittest.TestCase):
    """직접 지정 항목의 체크, 활성, 비활성 사유와 추가 항목 검증"""

    def test_options_cover_auto_keep_and_tiers(self) -> None:
        options = build_manual_target_options([make_eval()])
        labels = [option.label for option in options]
        self.assertEqual(labels[:2], [MANUAL_TARGET_AUTO_LABEL, MANUAL_TARGET_KEEP_LABEL])
        self.assertTrue(build_manual_target_options([])[0].enabled is False)
        self.assertTrue(any(option.value == "720p" for option in options))
        self.assertEqual(tier_menu_label(next(t for t in RESOLUTION_TIERS if t.key == "720p")), "720p (1280x720)")

    def test_blocked_rows_disable_specs(self) -> None:
        hdr = make_eval(video=make_video(filename="hdr.mp4"), smart_action=SmartAction.PASS_HDR)
        exception = make_eval(video=make_video(filename="exc.mp4"), smart_action=SmartAction.PASS_EXCEPTION)
        self.assertTrue(is_manual_target_blocked(hdr))
        self.assertTrue(is_manual_target_blocked(exception))
        options = build_manual_target_options([hdr, exception])
        self.assertFalse(options[1].enabled)
        self.assertIn("예외 규칙", options[1].tooltip)

    def test_action_ids_and_menu_text(self) -> None:
        self.assertEqual(manual_target_action_id(None), "manual_target.auto")
        self.assertEqual(manual_target_action_id("keep"), "manual_target.keep")
        self.assertEqual(manual_target_action_id("720p"), "manual_target.720p")
        self.assertEqual(_menu_text("자동", "Ctrl+0"), "자동\tCtrl+0")
        self.assertEqual(_menu_text("자동", ""), "자동")

    def test_fill_adds_candidate_and_clear_entries(self) -> None:
        menu = QMenu()
        self.addCleanup(menu.deleteLater)
        chosen: list[str | None] = []
        clears: list[bool] = []
        applies: list[bool] = []
        fill_manual_target_menu(
            menu,
            [make_eval()],
            chosen.append,
            on_clear_all=lambda: clears.append(True),
            on_apply_candidates=lambda: applies.append(True),
            shortcuts={"manual_target.auto": "Ctrl+0"},
        )
        texts = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertIn(FRAME_CANDIDATES_LABEL, texts)
        self.assertIn(MANUAL_TARGET_CLEAR_ALL_LABEL, texts)
        next(action for action in menu.actions() if action.text().startswith(FRAME_CANDIDATES_LABEL)).trigger()
        next(action for action in menu.actions() if action.text() == MANUAL_TARGET_CLEAR_ALL_LABEL).trigger()
        self.assertEqual((applies, clears), ([True], [True]))


if __name__ == "__main__":
    unittest.main()
