"""화질 비교 단축키 해석 검증: 키 후보, 우선순위, 확대 별칭, 닫기 순서, 툴팁"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QLabel

from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.dialogs import frame_compare_shortcuts as shortcuts
from src.gui.dialogs.frame_compare_canvas import MODE_TOGGLE
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _event(
    key: Qt.Key,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    *,
    repeat: bool = False,
) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers, "", repeat)


def _shortcuts_with(**overrides: str) -> dict[str, str]:
    effective = dict(DEFAULT_SHORTCUTS)
    effective.update(overrides)
    return effective


class PressedCandidateTests(unittest.TestCase):
    """눌린 키가 낼 수 있는 단축키 표기"""

    def test_shift_equal_and_plus_produce_zoom_aliases(self) -> None:
        shifted = shortcuts.pressed_candidates(
            _event(Qt.Key.Key_Equal, Qt.KeyboardModifier.ShiftModifier)
        )
        self.assertIn("=", shifted)
        self.assertIn("+", shifted)
        self.assertIn("Shift+=", shifted)
        plus = shortcuts.pressed_candidates(_event(Qt.Key.Key_Plus))
        self.assertIn("+", plus)

    def test_shift_minus_and_underscore_produce_zoom_out_aliases(self) -> None:
        shifted = shortcuts.pressed_candidates(
            _event(Qt.Key.Key_Minus, Qt.KeyboardModifier.ShiftModifier)
        )
        self.assertIn("-", shifted)
        self.assertIn("_", shifted)
        self.assertIn("Shift+-", shifted)

    def test_plain_key_is_normalized(self) -> None:
        self.assertEqual(shortcuts.pressed_candidates(_event(Qt.Key.Key_C)), ("C",))
        self.assertEqual(shortcuts.pressed_candidates(_event(Qt.Key.Key_Period)), (".",))

    def test_shift_space_is_a_candidate_even_with_keypad_or_group_switch(self) -> None:
        """보조 플래그가 붙은 Shift+Space도 화질 비교 전환 후보로 넣는지 검증"""
        shifted = Qt.KeyboardModifier.ShiftModifier
        for extra in (
            Qt.KeyboardModifier.KeypadModifier,
            Qt.KeyboardModifier.GroupSwitchModifier,
        ):
            with self.subTest(extra=extra):
                candidates = shortcuts.pressed_candidates(_event(Qt.Key.Key_Space, shifted | extra))
                self.assertIn("Shift+Space", candidates)

    def test_shift_space_with_ctrl_alt_or_meta_is_not_added(self) -> None:
        """Shift 외 다른 수식키가 붙은 Space는 전환 후보로 만들지 않는지 검증"""
        shifted = Qt.KeyboardModifier.ShiftModifier
        for extra in (
            Qt.KeyboardModifier.ControlModifier,
            Qt.KeyboardModifier.AltModifier,
            Qt.KeyboardModifier.MetaModifier,
        ):
            with self.subTest(extra=extra):
                candidates = shortcuts.pressed_candidates(_event(Qt.Key.Key_Space, shifted | extra))
                self.assertNotIn("Shift+Space", candidates)


class MatchActionTests(unittest.TestCase):
    """사용자 지정 단축키와 확대, 축소 기본 키 별칭 매칭"""

    def test_compare_commands_match_their_defaults(self) -> None:
        self.assertEqual(
            shortcuts.match_action(DEFAULT_SHORTCUTS, ("1",)), "compare.mode_split"
        )
        self.assertEqual(
            shortcuts.match_action(DEFAULT_SHORTCUTS, ("C",)), "compare.toggle_original"
        )
        self.assertEqual(
            shortcuts.match_action(DEFAULT_SHORTCUTS, ("Shift+Space",)),
            "classify.compare_selected",
        )

    def test_zoom_aliases_only_apply_to_default_keys(self) -> None:
        self.assertEqual(
            shortcuts.match_action(DEFAULT_SHORTCUTS, ("Shift+=",)), "compare.zoom_in"
        )
        self.assertEqual(shortcuts.match_action(DEFAULT_SHORTCUTS, ("_",)), "compare.zoom_out")
        custom = _shortcuts_with(**{"compare.zoom_in": "Z"})
        self.assertEqual(shortcuts.match_action(custom, ("Z",)), "compare.zoom_in")
        self.assertIsNone(shortcuts.match_action(custom, ("Shift+=",)))

    def test_compare_group_wins_over_preview_keys(self) -> None:
        effective = _shortcuts_with(**{"compare.toggle_controls": "F"})
        self.assertEqual(
            shortcuts.match_action(effective, ("F",)), "compare.toggle_controls"
        )

    def test_removed_preview_keys_do_not_match(self) -> None:
        effective = _shortcuts_with(**{"preview.toggle_mute": ""})
        self.assertIsNone(shortcuts.match_action(effective, ("M",)))



class KeyControllerTests(unittest.TestCase):
    """고정 닫기 순서와 단축키 실행"""

    def setUp(self) -> None:
        self.calls: list[str] = []
        self.visible = {"menu": False, "guide": False, "fullscreen": False}
        self.focus_editor = [False]
        self.handlers = shortcuts.CompareShortcutHandlers(
            set_mode=lambda mode: self.calls.append(f"mode:{mode}"),
            toggle_original=lambda: self.calls.append("original"),
            zoom_fit=lambda: self.calls.append("fit"),
            zoom_in=lambda: self.calls.append("in"),
            zoom_out=lambda: self.calls.append("out"),
            toggle_controls=lambda: self.calls.append("controls"),
            seek=lambda direction, large: self.calls.append(f"seek:{direction}:{large}"),
            step_frame=lambda direction: self.calls.append(f"frame:{direction}"),
            toggle_playback=lambda: self.calls.append("play"),
            rate_step=lambda step: self.calls.append(f"rate:{step}"),
            rate_reset=lambda: self.calls.append("rate-reset"),
            toggle_mute=lambda: self.calls.append("mute"),
            toggle_fullscreen=lambda: self.calls.append("fullscreen"),
            request_preview=lambda: self.calls.append("preview"),
        )
        self.controller = shortcuts.CompareKeyController(
            get_shortcuts=lambda: DEFAULT_SHORTCUTS,
            get_handlers=lambda: self.handlers,
            focus_in_editor=lambda: self.focus_editor[0],
            close_menu=self._close_menu,
            close_guide=self._close_guide,
            is_fullscreen=lambda: self.visible["fullscreen"],
            toggle_fullscreen=self._toggle_fullscreen,
            close_window=lambda: self.calls.append("close"),
        )

    def _close_menu(self) -> bool:
        return self.visible["menu"]

    def _close_guide(self) -> bool:
        return self.visible["guide"]

    def _toggle_fullscreen(self) -> None:
        self.visible["fullscreen"] = False

    def test_close_key_closes_menu_then_guide_then_fullscreen_then_window(self) -> None:
        self.visible["menu"] = True
        self.assertTrue(self.controller.handle(_event(Qt.Key.Key_Space)))
        self.visible["menu"] = False
        self.visible["guide"] = True
        self.assertTrue(self.controller.handle(_event(Qt.Key.Key_Space)))
        self.visible["guide"] = False
        self.visible["fullscreen"] = True
        self.assertTrue(self.controller.handle(_event(Qt.Key.Key_Escape)))
        self.assertFalse(self.visible["fullscreen"])
        self.assertTrue(self.controller.handle(_event(Qt.Key.Key_Escape)))
        self.assertEqual(self.calls[-1], "close")

    def test_shift_space_is_not_a_close_key(self) -> None:
        self.assertTrue(
            self.controller.handle(_event(Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier))
        )
        self.assertEqual(self.calls, ["preview"])

    def test_zoom_keys_and_custom_shortcut_run_handlers(self) -> None:
        self.controller.handle(_event(Qt.Key.Key_Equal, Qt.KeyboardModifier.ShiftModifier))
        self.controller.handle(_event(Qt.Key.Key_Minus))
        self.assertEqual(self.calls, ["in", "out"])

    def test_focus_in_editor_keeps_the_key(self) -> None:
        self.focus_editor[0] = True
        self.assertFalse(self.controller.handle(_event(Qt.Key.Key_1)))
        self.assertEqual(self.calls, [])

    def test_unknown_key_is_not_handled(self) -> None:
        self.assertFalse(self.controller.handle(_event(Qt.Key.Key_F12)))

    def test_auto_repeat_does_not_flip_the_original(self) -> None:
        self.assertTrue(self.controller.handle(_event(Qt.Key.Key_C, repeat=True)))
        self.assertEqual(self.calls, [])


class ShortcutNoticeTests(unittest.TestCase):
    """단축키 실행 뒤 표시할 결과 문구"""

    def _notice(self, action_id: str, state: shortcuts.CompareNoticeState) -> list[str]:
        notices: list[str] = []
        handlers = shortcuts.CompareShortcutHandlers(
            set_mode=lambda _mode: None,
            toggle_original=lambda: None,
            zoom_fit=lambda: None,
            zoom_in=lambda: None,
            zoom_out=lambda: None,
            toggle_controls=lambda: None,
            seek=lambda _direction, _large: None,
            step_frame=lambda _direction: None,
            toggle_playback=lambda: None,
            rate_step=lambda _step: None,
            rate_reset=lambda: None,
            toggle_mute=lambda: None,
            toggle_fullscreen=lambda: None,
            request_preview=lambda: None,
            notify=notices.append,
            notice_state=lambda: state,
        )
        handlers.by_action()[action_id]()
        return notices

    def test_every_keyboard_command_reports_the_new_state(self) -> None:
        """키보드 명령이 실행 뒤 상태에 맞는 문구를 알리는지 검증"""
        state = shortcuts.CompareNoticeState(
            mode=MODE_TOGGLE,
            zoom_percent=250,
            original_pinned=True,
            collapsed=True,
            playing=True,
            rate=2.0,
            muted=True,
            fullscreen=True,
            seek_seconds=5,
        )
        expected = {
            "compare.mode_split": "분할선 모드",
            "compare.mode_toggle": "즉시 전환 모드",
            "compare.mode_side": "좌우 비교 모드",
            "compare.toggle_original": "원본 고정",
            "compare.zoom_fit": "화면 맞춤 (250%)",
            "compare.zoom_in": "확대 250%",
            "compare.zoom_out": "축소 250%",
            "compare.toggle_controls": "조작 바 접기",
            "preview.seek_backward": "구간 이동 -5초",
            "preview.seek_forward": "구간 이동 +5초",
            "preview.seek_backward_large": "구간 이동 -30초",
            "preview.seek_forward_large": "구간 이동 +30초",
            "preview.frame_backward": "이전 프레임",
            "preview.frame_forward": "다음 프레임",
            "preview.rate_down": "재생 배속: 2배",
            "preview.rate_up": "재생 배속: 2배",
            "preview.rate_reset": "재생 배속: 2배",
            "preview.toggle_playback": "일시중지",
            "preview.toggle_mute": "음소거",
            "preview.toggle_fullscreen": "전체화면",
        }
        for action_id, text in expected.items():
            with self.subTest(action=action_id):
                self.assertEqual(self._notice(action_id, state), [text])

    def test_notices_follow_the_opposite_states(self) -> None:
        """반대 상태에서는 다른 문구를 알리고 전환 모드가 아니면 원본 알림을 하지 않는지 검증"""
        state = shortcuts.CompareNoticeState()
        expected = {
            "compare.toggle_original": None,
            "compare.toggle_controls": "조작 바 펼치기",
            "preview.toggle_playback": "재생",
            "preview.toggle_mute": "음소거 해제",
            "preview.toggle_fullscreen": "창 모드",
        }
        for action_id, text in expected.items():
            with self.subTest(action=action_id):
                notices = self._notice(action_id, state)
                self.assertEqual(notices, [] if text is None else [text])

    def test_preview_switch_has_no_notice(self) -> None:
        """창 전환 명령은 문구 없이 동작만 실행하는지 검증"""
        notices = self._notice("classify.compare_selected", shortcuts.CompareNoticeState())
        self.assertEqual(notices, [])


class TimelineTooltipTests(GuiTestCase):
    """하단 재생 바 툴팁의 단축키 표기"""

    def test_tooltips_show_current_keys_and_mute_state(self) -> None:
        from src.gui.dialogs import frame_compare_layout as layout

        parent = QLabel()
        self.addCleanup(parent.deleteLater)
        timeline = layout.build_timeline(
            parent,
            lambda _step: None,
            lambda _value: None,
            lambda: None,
            lambda _rate: None,
            lambda: None,
            lambda _value: None,
            lambda: None,
            lambda: DEFAULT_SHORTCUTS,
        )
        self.addCleanup(timeline.slider.deleteLater)
        effective = _shortcuts_with(**{"preview.frame_forward": "X"})
        shortcuts.apply_timeline_tooltips(timeline, effective, muted=True, rate=2.0)
        self.assertIn("[X]", timeline.next_button.toolTip())
        self.assertIn("[M]", timeline.mute_button.toolTip())
        self.assertIn("다시 켭니다", timeline.mute_button.toolTip())
        self.assertIn("현재 2배입니다.", timeline.rate_button.toolTip())
        self.assertIn("[F]", timeline.fullscreen_button.toolTip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
