"""미리 보기 키 판정과 1프레임 이동 계산 검증"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLineEdit

from src.gui.preview import preview_keys as keys

_APP = QApplication.instance() or QApplication([])


class SeekTests(unittest.TestCase):
    """구간 이동 위치 제한과 닫기 키"""

    def test_clamp_keeps_position_inside_duration(self) -> None:
        self.assertEqual(keys.clamp_seek_position(1000, 500, 3000), 1500)
        self.assertEqual(keys.clamp_seek_position(1000, -5000, 3000), 0)
        self.assertEqual(keys.clamp_seek_position(1000, 5000, 3000), 3000)
        self.assertEqual(keys.clamp_seek_position(1000, 500, 0), 1000)

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


class NoticeTests(unittest.TestCase):
    """재생 상태 알림 문구"""

    def test_playback_notice_matches_playing_state(self) -> None:
        self.assertEqual(keys.playback_notice(True), keys.PLAY_NOTICE)
        self.assertEqual(keys.playback_notice(False), keys.PAUSE_NOTICE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
