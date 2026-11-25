"""미리 보기 키 판정과 1프레임 이동 계산 검증"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QLineEdit

from src.core.analysis.frame_compare import clamp_end_position_ms, end_margin_ms
from src.gui.preview import preview_keys as keys

_APP = QApplication.instance() or QApplication([])


class SeekTests(unittest.TestCase):
    """끝 여유를 둔 이동 위치 제한과 닫기 키"""

    def test_clamp_keeps_position_inside_duration_without_the_last_frame(self) -> None:
        self.assertEqual(clamp_end_position_ms(1000 + 500, 3000, 30.0), 1500)
        self.assertEqual(clamp_end_position_ms(1000 - 5000, 3000, 30.0), 0)
        self.assertEqual(clamp_end_position_ms(10_000, 3000, 30.0), 3000 - 33)
        self.assertEqual(clamp_end_position_ms(500, 0, 30.0), 500)

    def test_end_margin_is_one_frame_and_never_zero(self) -> None:
        self.assertEqual(end_margin_ms(30.0), 33)
        self.assertEqual(end_margin_ms(24.0), 42)
        self.assertEqual(end_margin_ms(0.0), 33)
        self.assertEqual(end_margin_ms(1_000_000.0), 1)

    def test_space_and_escape_close_but_shift_space_does_not(self) -> None:
        modifier = Qt.KeyboardModifier.NoModifier
        self.assertTrue(keys.is_close_key(Qt.Key.Key_Space, modifier))
        self.assertTrue(keys.is_close_key(Qt.Key.Key_Escape, modifier))
        self.assertFalse(keys.is_close_key(Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier))
        self.assertFalse(keys.is_close_key(Qt.Key.Key_A, modifier))


class FrameStepTests(unittest.TestCase):
    """FPS 기반 한 프레임 이동 밀리초"""

    def test_frame_step_uses_the_video_fps(self) -> None:
        self.assertEqual(keys.frame_step_ms(30.0), 33)
        self.assertEqual(keys.frame_step_ms(24.0), 42)
        self.assertEqual(keys.frame_step_ms(0.0), 33)

    def test_metadata_frame_rate_is_coerced_safely(self) -> None:
        self.assertEqual(keys.coerce_frame_rate(29.97), 29.97)
        self.assertEqual(keys.coerce_frame_rate("25"), 25.0)
        self.assertEqual(keys.coerce_frame_rate("bad"), 30.0)
        self.assertEqual(keys.coerce_frame_rate(None), 30.0)
        self.assertEqual(keys.coerce_frame_rate(-1), 30.0)

    def test_resolve_prefers_table_fps_then_metadata(self) -> None:
        self.assertEqual(keys.resolve_frame_rate(60.0, 24.0), 60.0)
        self.assertEqual(keys.resolve_frame_rate(0.0, 24.0), 24.0)
        self.assertEqual(keys.resolve_frame_rate(0.0, None), 30.0)


class BlockingTests(unittest.TestCase):
    """텍스트 입력과 팝업이 키를 받아야 하는지"""

    def test_no_popup_or_text_focus_does_not_block(self) -> None:
        with (
            mock.patch.object(QApplication, "activePopupWidget", return_value=None),
            mock.patch.object(QApplication, "activeModalWidget", return_value=None),
            mock.patch.object(QApplication, "activeWindow", return_value=None),
            mock.patch.object(QApplication, "focusWidget", return_value=None),
        ):
            self.assertFalse(keys.blocks_preview_keys())

    def test_text_input_focus_blocks_preview_keys(self) -> None:
        edit = QLineEdit()
        self.addCleanup(edit.deleteLater)
        with (
            mock.patch.object(QApplication, "activePopupWidget", return_value=None),
            mock.patch.object(QApplication, "activeModalWidget", return_value=None),
            mock.patch.object(QApplication, "activeWindow", return_value=None),
            mock.patch.object(QApplication, "focusWidget", return_value=edit),
        ):
            self.assertTrue(keys.blocks_preview_keys())

    def test_active_popup_blocks_preview_keys(self) -> None:
        with (
            mock.patch.object(QApplication, "activePopupWidget", return_value=object()),
        ):
            self.assertTrue(keys.blocks_preview_keys())


class CompareActionTests(unittest.TestCase):
    """사용자 지정 화질 비교 전환 단축키 판정"""

    def _router(self, key: str, toggles: list[bool]) -> keys.PreviewKeyRouter:
        handlers = keys.PreviewActionHandlers(
            seek_by=lambda _ms: None,
            step_frame=lambda _direction: None,
            toggle_playback=lambda: None,
            toggle_mute=lambda: None,
            set_rate=lambda _rate: None,
            toggle_fullscreen=lambda: None,
            cycle_info=lambda: None,
            request_step=lambda _direction: None,
            rate=lambda: 1.0,
            hide=lambda: None,
            toggle_compare=lambda: toggles.append(True),
            toggle_crop=lambda: None,
        )
        return keys.PreviewKeyRouter(
            get_shortcuts=lambda: {"classify.compare_selected": key},
            forward_key=lambda _key: False,
            get_actions=lambda: handlers,
            is_visible=lambda: True,
            is_active=lambda: True,
            seek_seconds=lambda: 5,
        )

    def test_manual_shift_space_override_ignores_helper_modifier_flags(self) -> None:
        """Shift+Space로 직접 지정하면 넘패드 보조 플래그가 붙어도 전환으로 판정하는지 검증"""
        toggles: list[bool] = []
        router = self._router("Shift+Space", toggles)
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier,
        )
        self.assertTrue(router.handle_key(event))
        self.assertEqual(toggles, [True])

    def test_default_ctrl_b_matches_and_shift_space_is_left_alone(self) -> None:
        """기본 Ctrl+B는 전환으로 처리하고 Shift+Space는 더 이상 전환으로 보지 않는지 검증"""
        toggles: list[bool] = []
        router = self._router("Ctrl+B", toggles)
        ctrl_b = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier
        )
        self.assertTrue(router.handle_key(ctrl_b))
        shift_space = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier
        )
        self.assertFalse(router.handle_key(shift_space))
        self.assertEqual(toggles, [True])


class CropToggleActionTests(unittest.TestCase):
    """여백 미리 보기 단축키 판정과 상태 문구"""

    def _router(self, calls: list[bool]) -> keys.PreviewKeyRouter:
        handlers = keys.PreviewActionHandlers(
            seek_by=lambda _ms: None,
            step_frame=lambda _direction: None,
            toggle_playback=lambda: None,
            toggle_mute=lambda: None,
            set_rate=lambda _rate: None,
            toggle_fullscreen=lambda: None,
            cycle_info=lambda: None,
            request_step=lambda _direction: None,
            rate=lambda: 1.0,
            hide=lambda: None,
            toggle_compare=lambda: None,
            toggle_crop=lambda: calls.append(True),
        )
        return keys.PreviewKeyRouter(
            get_shortcuts=lambda: {"preview.toggle_crop": "B"},
            forward_key=lambda _key: False,
            get_actions=lambda: handlers,
            is_visible=lambda: True,
            is_active=lambda: True,
            seek_seconds=lambda: 5,
        )

    def test_b_runs_the_crop_toggle(self) -> None:
        calls: list[bool] = []
        router = self._router(calls)
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_B, Qt.KeyboardModifier.NoModifier)
        self.assertTrue(router.handle_key(event))
        self.assertEqual(calls, [True])

    def test_crop_notice_matches_the_state(self) -> None:
        self.assertEqual(keys.crop_notice(True), "여백 미리 보기")
        self.assertEqual(keys.crop_notice(False), "여백 미리 보기 해제")


class NoticeTests(unittest.TestCase):
    """재생 상태 알림 문구"""

    def test_playback_notice_matches_playing_state(self) -> None:
        self.assertEqual(keys.playback_notice(True), keys.PLAY_NOTICE)
        self.assertEqual(keys.playback_notice(False), keys.PAUSE_NOTICE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
