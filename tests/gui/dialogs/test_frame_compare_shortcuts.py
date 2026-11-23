"""화질 비교 단축키 해석 검증: 키 후보, 우선순위, 확대 별칭, 닫기 순서, 툴팁"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QLabel

from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.dialogs import frame_compare_shortcuts as shortcuts
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
