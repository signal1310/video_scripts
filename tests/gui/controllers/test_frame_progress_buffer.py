"""단계 진행 묶음 타이머 보조 검증"""

import logging
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.gui import diagnostics_qt
from src.gui.controllers.frame_progress_buffer import TIMINGS_LOGGER_NAME, FrameProgressBuffer, StageProgress

_APP = QApplication.instance() or QApplication([])


def _progress(done: int = 1) -> StageProgress:
    """검증용 단계 진행 값"""
    return StageProgress(0, 3, "a.mp4", "vmaf", done, 9)


class FrameProgressBufferTests(unittest.TestCase):
    """묶음 주기 동안 마지막 값만 남기고 한 번만 넘기는지 검증"""

    def test_submit_keeps_only_the_last_value_until_flush(self) -> None:
        """여러 값이 묶이는 동안 싱크를 부르지 않고 마지막 값만 남기는지 검증"""
        received: list[StageProgress] = []
        buffer = FrameProgressBuffer(received.append, 300)
        self.assertEqual(buffer.interval_ms, 300)
        self.assertEqual(buffer.pending, None)
        self.assertFalse(buffer.is_bundling())
        for done in range(1, 10):
            buffer.submit(_progress(done))
        self.assertEqual(received, [])
        self.assertTrue(buffer.is_bundling())
        self.assertEqual(buffer.pending, _progress(9))
        buffer.flush()
        self.assertEqual(received, [_progress(9)])
        self.assertFalse(buffer.is_bundling())
        self.assertIsNone(buffer.pending)

    def test_flush_without_pending_does_nothing(self) -> None:
        """묶인 값이 없으면 싱크를 부르지 않는지 검증"""
        received: list[StageProgress] = []
        buffer = FrameProgressBuffer(received.append, 300)
        buffer.flush()
        self.assertEqual(received, [])

    def test_drop_discards_the_bundled_value(self) -> None:
        """drop은 타이머를 멈추고 묶인 값을 버리는지 검증"""
        received: list[StageProgress] = []
        buffer = FrameProgressBuffer(received.append, 300)
        buffer.submit(_progress())
        self.assertTrue(buffer.is_bundling())
        buffer.drop()
        self.assertFalse(buffer.is_bundling())
        self.assertIsNone(buffer.pending)
        buffer.flush()
        self.assertEqual(received, [])

    def test_new_submit_after_flush_starts_again(self) -> None:
        """플러시 뒤 새 값이 오면 다시 묶는지 검증"""
        received: list[StageProgress] = []
        buffer = FrameProgressBuffer(received.append, 300)
        buffer.submit(_progress(1))
        buffer.flush()
        buffer.submit(_progress(2))
        self.assertTrue(buffer.is_bundling())
        buffer.flush()
        self.assertEqual([p.stage_done for p in received], [1, 2])

    def test_stage_changes_and_drop_log_stage_timings_at_debug_in_developer_mode(self) -> None:
        """개발자 모드에서 단계가 바뀌거나 묶음을 버릴 때 이전 단계 소요가 DEBUG로 남는지 검증"""
        buffer = FrameProgressBuffer(lambda _progress_value: None, 300)
        with (
            patch.object(diagnostics_qt, "_developer_mode", True),
            self.assertLogs(TIMINGS_LOGGER_NAME, level=logging.DEBUG) as captured,
        ):
            buffer.submit(StageProgress(0, 3, "a.mp4", "scan", 1, 9))
            buffer.submit(StageProgress(0, 3, "a.mp4", "vmaf", 1, 9))
            buffer.drop()
        self.assertEqual(len(captured.output), 2)
        self.assertTrue(all("단계 소요" in line for line in captured.output))

    def test_stage_timings_are_silent_when_developer_mode_is_off(self) -> None:
        """개발자 모드가 아니면 같은 단계 전환에서도 소요 기록을 남기지 않는지 검증"""
        buffer = FrameProgressBuffer(lambda _progress_value: None, 300)
        with (
            patch.object(diagnostics_qt, "_developer_mode", False),
            self.assertNoLogs(TIMINGS_LOGGER_NAME, level=logging.DEBUG),
        ):
            buffer.submit(StageProgress(0, 3, "a.mp4", "scan", 1, 9))
            buffer.submit(StageProgress(0, 3, "a.mp4", "vmaf", 1, 9))
            buffer.drop()


if __name__ == "__main__":
    unittest.main()
