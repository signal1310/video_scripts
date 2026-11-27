"""화질 비교 단축키 해석 검증: 키 후보, 우선순위, 확대 별칭, 닫기 순서, 툴팁"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.dialogs import frame_compare_shortcuts as shortcuts
from src.gui.dialogs.frame_compare_canvas import MODE_TOGGLE

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
        """보조 플래그가 붙은 Shift+Space도 사용자 지정 전환 후보로 넣는지 검증"""
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
            shortcuts.match_action(DEFAULT_SHORTCUTS, ("Ctrl+B",)),
            "classify.compare_selected",
        )

    def test_shift_space_override_still_matches_the_compare_toggle(self) -> None:
        """사용자가 Shift+Space로 다시 지정하면 그 표기로 전환 명령이 매칭되는지 검증"""
        custom = _shortcuts_with(**{"classify.compare_selected": "Shift+Space"})
        self.assertEqual(
            shortcuts.match_action(custom, ("Shift+Space",)),
            "classify.compare_selected",
        )
        candidates = shortcuts.pressed_candidates(
            _event(
                Qt.Key.Key_Space,
                Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier,
            )
        )
        self.assertIn("Shift+Space", candidates)

    def test_zoom_aliases_only_apply_to_default_keys(self) -> None:
        self.assertEqual(
            shortcuts.match_action(DEFAULT_SHORTCUTS, ("Shift+=",)), "compare.zoom_in"
        )
        self.assertEqual(shortcuts.match_action(DEFAULT_SHORTCUTS, ("_",)), "compare.zoom_out")
        custom = _shortcuts_with(**{"compare.zoom_in": "Z"})
        self.assertEqual(shortcuts.match_action(custom, ("Z",)), "compare.zoom_in")
        self.assertIsNone(shortcuts.match_action(custom, ("Shift+=",)))

    def test_shift_b_matches_the_background_cycle_in_the_compare_window(self) -> None:
        """화질 비교 창에서도 Shift+B가 배경 색 전환 명령으로 매칭되는지 검증"""
        candidates = shortcuts.pressed_candidates(
            _event(Qt.Key.Key_B, Qt.KeyboardModifier.ShiftModifier)
        )
        self.assertIn("Shift+B", candidates)
        self.assertEqual(
            shortcuts.match_action(DEFAULT_SHORTCUTS, candidates),
            "preview.cycle_background",
        )

    def test_compare_group_wins_over_preview_keys(self) -> None:
        effective = _shortcuts_with(**{"compare.toggle_controls": "F"})
        self.assertEqual(
            shortcuts.match_action(effective, ("F",)), "compare.toggle_controls"
        )

    def test_removed_preview_keys_do_not_match(self) -> None:
        effective = _shortcuts_with(**{"preview.toggle_mute": ""})
        self.assertIsNone(shortcuts.match_action(effective, ("M",)))



class CompareShortcutPressedTests(unittest.TestCase):
    """여러 창이 함께 쓰는 화질 비교 전환 키 판정"""

    def test_default_key_matches_and_other_keys_do_not(self) -> None:
        self.assertTrue(
            shortcuts.compare_shortcut_pressed(
                _event(Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier), DEFAULT_SHORTCUTS
            )
        )
        self.assertFalse(
            shortcuts.compare_shortcut_pressed(_event(Qt.Key.Key_B), DEFAULT_SHORTCUTS)
        )

    def test_custom_key_replaces_the_default(self) -> None:
        """사용자가 키를 바꾸면 그 키만 전환으로 보고 기본 Ctrl+B는 무시하는지 검증"""
        custom = _shortcuts_with(**{"classify.compare_selected": "F5"})
        self.assertTrue(
            shortcuts.compare_shortcut_pressed(_event(Qt.Key.Key_F5), custom)
        )
        self.assertFalse(
            shortcuts.compare_shortcut_pressed(
                _event(Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier), custom
            )
        )

    def test_cleared_key_matches_nothing(self) -> None:
        """설정을 비우면 어떤 키도 전환으로 보지 않는지 검증"""
        cleared = _shortcuts_with(**{"classify.compare_selected": ""})
        self.assertFalse(
            shortcuts.compare_shortcut_pressed(
                _event(Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier), cleared
            )
        )
        self.assertFalse(shortcuts.compare_shortcut_pressed(_event(Qt.Key.Key_F5), cleared))


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
            step_video=lambda step: self.calls.append(f"video:{step}"),
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

    def test_ctrl_b_requests_the_preview_instead_of_closing(self) -> None:
        """Ctrl+B가 창을 닫지 않고 미리 보기 전환으로 동작하는지 검증"""
        self.assertTrue(
            self.controller.handle(_event(Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier))
        )
        self.assertEqual(self.calls, ["preview"])

    def test_shift_space_is_no_longer_a_compare_key_by_default(self) -> None:
        """기본 단축키가 Ctrl+B로 바뀐 뒤 Shift+Space는 미리 보기 전환으로 처리하지 않는지 검증"""
        self.assertFalse(
            self.controller.handle(_event(Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier))
        )
        self.assertEqual(self.calls, [])

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

    def test_previous_and_next_step_the_video_list(self) -> None:
        """이전, 다음 영상 명령이 창에 영상 전환을 요청하는지 검증"""
        handlers = self.handlers.by_action()
        handlers["preview.previous"]()
        handlers["preview.next"]()
        self.assertEqual(self.calls, ["video:-1", "video:1"])

    def test_default_cycle_handler_keeps_the_default_background(self) -> None:
        """배경 색 콜백을 넘기지 않은 묶음은 기본 검정을 알림 없이 돌려주는지 검증"""
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
            step_video=lambda _step: None,
            notify=notices.append,
        )
        self.assertEqual(handlers.cycle_background(), "black")
        handlers.by_action()["preview.cycle_background"]()
        self.assertEqual(notices, [])

    def test_shift_b_cycles_the_background_through_the_handler(self) -> None:
        """화질 비교 창의 Shift+B가 부모 창의 배경 색 전환을 부르고 알림은 따로 띄우는지 검증"""
        cycles: list[bool] = []
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
            step_video=lambda _step: None,
            cycle_background=lambda: cycles.append(True) or "gray",
            notify=self.calls.append,
        )
        controller = shortcuts.CompareKeyController(
            get_shortcuts=lambda: DEFAULT_SHORTCUTS,
            get_handlers=lambda: handlers,
            focus_in_editor=lambda: False,
            close_menu=lambda: False,
            close_guide=lambda: False,
            is_fullscreen=lambda: False,
            toggle_fullscreen=lambda: None,
            close_window=lambda: None,
        )
        self.assertTrue(controller.handle(_event(Qt.Key.Key_B, Qt.KeyboardModifier.ShiftModifier)))
        self.assertEqual(cycles, [True])
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
            step_video=lambda _step: None,
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
        }
        for action_id, text in expected.items():
            with self.subTest(action=action_id):
                notices = self._notice(action_id, state)
                self.assertEqual(notices, [] if text is None else [text])

    def test_preview_switch_and_fullscreen_have_no_notice(self) -> None:
        """창 전환과 전체화면 명령은 문구 없이 동작만 실행하는지 검증"""
        for action_id in ("classify.compare_selected", "preview.toggle_fullscreen"):
            with self.subTest(action=action_id):
                notices = self._notice(action_id, shortcuts.CompareNoticeState())
                self.assertEqual(notices, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
