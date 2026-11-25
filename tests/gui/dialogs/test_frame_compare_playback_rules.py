"""재생 순수 규칙 검증: 배속 비례 드리프트, 끝 도달, 실시간 축소 왕복 이미지"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage

from src.gui.dialogs.frame_compare_playback_rules import (
    SYNC_DRIFT_MS,
    restart_from_start,
    roundtrip_compare_frame,
    sync_drift_exceeded,
)


def _image(width: int = 320, height: int = 180, color: str = "#FF0000") -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


class DriftTests(unittest.TestCase):
    """배속에 비례하는 위치 보정 기준"""

    def test_sync_drift_uses_the_configured_threshold(self) -> None:
        self.assertFalse(sync_drift_exceeded(1000, 1000))
        self.assertFalse(sync_drift_exceeded(1000, 1000 + SYNC_DRIFT_MS))
        self.assertTrue(sync_drift_exceeded(1000, 1000 + SYNC_DRIFT_MS + 1))

    def test_sync_drift_threshold_scales_with_the_playback_rate(self) -> None:
        """배속이 높을수록 위치 차이 허용 폭이 커지는지 검증"""
        self.assertFalse(sync_drift_exceeded(1000, 1000 + SYNC_DRIFT_MS, 8.0))
        self.assertTrue(sync_drift_exceeded(1000, 1000 + SYNC_DRIFT_MS * 8 + 1, 8.0))
        self.assertFalse(sync_drift_exceeded(1000, 1100, 1.0))

    def test_restart_from_start_only_when_duration_is_known(self) -> None:
        self.assertFalse(restart_from_start(0, 0))
        self.assertFalse(restart_from_start(900, 1000))
        self.assertTrue(restart_from_start(1000, 1000))
        # - 재생기는 끝에서 한 프레임 안쪽에 머무르므로 그 안쪽도 끝으로 봄
        self.assertTrue(restart_from_start(1000 - 33, 1000, 30.0))
        self.assertFalse(restart_from_start(1000 - 34, 1000, 30.0))


class RoundtripTests(unittest.TestCase):
    """축소 왕복 이미지의 크기 유지와 규격 없음 대체"""

    def test_roundtrip_frame_keeps_the_original_size(self) -> None:
        frame = _image(1600, 900)
        result = roundtrip_compare_frame(frame, 1600, 900, "720p")
        self.assertEqual(result.size(), frame.size())
        self.assertIsNot(result, frame)

    def test_fast_roundtrip_keeps_the_original_size(self) -> None:
        """실시간 재생용 빠른 왕복도 원본 크기를 유지하는지 검증"""
        frame = _image(800, 450)
        result = roundtrip_compare_frame(frame, 800, 450, "480p", fast=True)
        self.assertEqual(result.size(), frame.size())

    def test_roundtrip_frame_falls_back_to_the_original(self) -> None:
        frame = _image(320, 180)
        self.assertIs(roundtrip_compare_frame(frame, 320, 180, "unknown"), frame)
        self.assertIs(roundtrip_compare_frame(frame, 0, 180, "720p"), frame)


if __name__ == "__main__":
    unittest.main(verbosity=2)
