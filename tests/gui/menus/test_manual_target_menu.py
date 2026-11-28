"""출력 해상도 지정 메뉴의 항목 상태와 화질 분석 후보 적용 항목 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMenu

from src.core.models import ActiveFrame
from src.core.resolution_tiers import KEEP_RESOLUTION_KEY, RESOLUTION_TIERS
from src.core.smart_action import SmartAction
from src.gui.menus.manual_target_menu import (
    FRAME_CANDIDATE_SUFFIX,
    FRAME_CANDIDATES_LABEL,
    MANUAL_TARGET_AUTO_LABEL,
    MANUAL_TARGET_CLEAR_ALL_LABEL,
    MANUAL_TARGET_KEEP_LABEL,
    _menu_text,
    build_manual_target_options,
    fill_manual_target_menu,
    is_manual_target_blocked,
    manual_target_action_id,
    manual_target_label,
    tier_menu_label,
)
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])


class ManualTargetMenuTests(unittest.TestCase):
    """직접 지정 항목의 체크, 활성, 비활성 사유와 추가 항목 검증"""

    def test_options_cover_auto_keep_and_tiers(self) -> None:
        options = build_manual_target_options([make_eval()])
        labels = [option.label for option in options]
        self.assertEqual(labels[:3], ["인코딩 지정", MANUAL_TARGET_AUTO_LABEL, MANUAL_TARGET_KEEP_LABEL])
        self.assertTrue(build_manual_target_options([])[0].enabled is False)
        self.assertTrue(any(option.value == "720p" for option in options))
        self.assertEqual(tier_menu_label(next(t for t in RESOLUTION_TIERS if t.key == "720p")), "720p (1280x720)")

    def test_blocked_rows_disable_specs(self) -> None:
        hdr = make_eval(video=make_video(filename="hdr.mp4"), smart_action=SmartAction.PASS_HDR)
        exception = make_eval(video=make_video(filename="exc.mp4"), smart_action=SmartAction.PASS_EXCEPTION)
        self.assertTrue(is_manual_target_blocked(hdr))
        self.assertTrue(is_manual_target_blocked(exception))
        options = build_manual_target_options([hdr, exception])
        self.assertFalse(options[0].enabled)
        self.assertFalse(options[2].enabled)
        self.assertIn("예외 규칙", options[2].tooltip)

    def test_active_frame_sets_the_meaningful_downscale_limit(self) -> None:
        """잘라내기 확정 행은 원본 해상도가 아니라 활성 화면 크기로 규격 항목을 여는지 검증"""
        ev = make_eval(
            video=make_video(filename="crop.mp4", width=3840, height=2160),
            active_frame=ActiveFrame(1216, 2160, 1312, 0),
        )
        options = {option.value: option for option in build_manual_target_options([ev])}
        self.assertFalse(options["1440p"].enabled)
        self.assertTrue(options["1080p"].enabled)
        self.assertTrue(options["720p"].enabled)

    def test_action_ids_and_menu_text(self) -> None:
        self.assertEqual(manual_target_action_id(None), "manual_target.auto")
        self.assertEqual(manual_target_action_id("keep"), "manual_target.keep")
        self.assertEqual(manual_target_action_id("encode"), "manual_target.encode")
        self.assertEqual(manual_target_action_id("720p"), "manual_target.720p")
        self.assertEqual(_menu_text("자동", "Ctrl+0"), "자동\tCtrl+0")
        self.assertEqual(_menu_text("자동", ""), "자동")

    def test_encode_designation_option_state_and_tooltip(self) -> None:
        """인코딩 지정 항목이 자동보다 위에 있고, 선택 행이 모두 지정됐을 때만 체크되며 승인 툴팁을 가짐"""
        from src.core.resolution_tiers import ENCODE_DESIGNATION_KEY
        from src.gui.menus.manual_target_menu import (
            ENCODE_DESIGNATION_LABEL,
            ENCODE_DESIGNATION_TIP,
        )

        options = build_manual_target_options([make_eval()])
        self.assertEqual(options[0].value, ENCODE_DESIGNATION_KEY)
        self.assertEqual(options[0].label, ENCODE_DESIGNATION_LABEL)
        self.assertTrue(options[0].enabled)
        self.assertFalse(options[0].checked)
        designated = make_eval(manual_target=ENCODE_DESIGNATION_KEY)
        options = build_manual_target_options([designated])
        self.assertTrue(options[0].checked)
        options = build_manual_target_options([designated, make_eval()])
        self.assertFalse(options[0].checked)
        hdr = make_eval(video=make_video(filename="hdr.mp4"), smart_action=SmartAction.PASS_HDR)
        self.assertFalse(build_manual_target_options([hdr])[0].enabled)
        menu = QMenu()
        self.addCleanup(menu.deleteLater)
        fill_manual_target_menu(menu, [make_eval()], lambda _value: None)
        encode_action = next(
            action for action in menu.actions() if action.text().split("\t")[0] == ENCODE_DESIGNATION_LABEL
        )
        self.assertTrue(encode_action.isCheckable())
        self.assertEqual(encode_action.toolTip(), ENCODE_DESIGNATION_TIP)

    def test_encode_designation_action_id_has_no_default_key(self) -> None:
        """인코딩 지정 명령이 단축키 목록에 ID 전용으로 등록되고 기본 키는 없음"""
        from src.core.settings.shortcuts import DEFAULT_SHORTCUTS, SHORTCUT_SPEC_BY_ID

        spec = SHORTCUT_SPEC_BY_ID["manual_target.encode"]
        self.assertEqual(spec.group_key, "manual_target")
        self.assertEqual(DEFAULT_SHORTCUTS["manual_target.encode"], "")

    def test_fill_marks_the_frame_analysis_candidate_tier(self) -> None:
        """비교 창 출력 드롭다운에서 후보 규격만 (축소 후보) 표기를 받는지 검증"""
        menu = QMenu()
        self.addCleanup(menu.deleteLater)
        fill_manual_target_menu(menu, [make_eval()], lambda _value: None, candidate_tier="720p")
        texts = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(sum(FRAME_CANDIDATE_SUFFIX.strip() in text for text in texts), 1)
        marked = [text for text in texts if FRAME_CANDIDATE_SUFFIX.strip() in text]
        self.assertTrue(marked[0].startswith("720p"))
        self.assertEqual(manual_target_label("720p"), "720p")
        self.assertEqual(manual_target_label(KEEP_RESOLUTION_KEY), MANUAL_TARGET_KEEP_LABEL)
        self.assertEqual(manual_target_label(None), MANUAL_TARGET_AUTO_LABEL)

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
