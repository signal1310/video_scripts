"""재생 순수 규칙 검증: 원본 프레임 시각 맞춤, 드리프트 판정, 끝 도달, 실시간 축소 왕복 이미지"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage

from src.gui.dialogs.frame_compare_playback_rules import (
    DEFAULT_SOURCE_FPS,
    PAIR_BUFFER_FRAMES,
    SYNC_DRIFT_FRAMES,
    closest_frame_index,
    pair_tolerance_us,
    qt_transformation_mode,
    restart_from_start,
    roundtrip_compare_frame,
    source_frame_us,
    sync_drift_exceeded,
)


def _image(width: int = 320, height: int = 180, color: str = "#FF0000") -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


class FrameTimingTests(unittest.TestCase):
    """원본 프레임 시각 단위와 짝 허용 폭"""

    def test_source_frame_length_falls_back_to_the_default_rate(self) -> None:
        self.assertEqual(source_frame_us(30.0), 33_333)
        self.assertEqual(source_frame_us(60.0), 16_667)
        self.assertEqual(source_frame_us(0.0), source_frame_us(DEFAULT_SOURCE_FPS))
        self.assertEqual(source_frame_us(-1.0), source_frame_us(DEFAULT_SOURCE_FPS))

    def test_pair_tolerance_covers_one_source_frame(self) -> None:
        self.assertEqual(pair_tolerance_us(30.0), 33_333)
        self.assertEqual(pair_tolerance_us(0.0), source_frame_us(DEFAULT_SOURCE_FPS))

    def test_buffer_holds_more_frames_than_the_drift_limit(self) -> None:
        """보정 기준보다 보관 장수가 많아야 보정이 늦어도 맞출 후보가 남음"""
        self.assertGreater(PAIR_BUFFER_FRAMES, SYNC_DRIFT_FRAMES)


class ClosestFrameTests(unittest.TestCase):
    """주 프레임 시각과 가장 가까운 종 프레임 고르기"""

    def test_closest_frame_is_chosen_inside_the_tolerance(self) -> None:
        frames = [100_000, 133_333, 166_666]
        self.assertEqual(closest_frame_index(140_000, frames, pair_tolerance_us(30.0)), 1)

    def test_out_of_tolerance_frames_are_rejected(self) -> None:
        frames = [0, 33_333]
        self.assertIsNone(closest_frame_index(200_000, frames, pair_tolerance_us(30.0)))

    def test_empty_buffer_has_no_candidate(self) -> None:
        self.assertIsNone(closest_frame_index(0, [], pair_tolerance_us(30.0)))


class DriftTests(unittest.TestCase):
    """원본 프레임 단위로 판정하는 위치 보정 기준"""

    def test_sync_drift_uses_the_frame_threshold(self) -> None:
        limit = source_frame_us(30.0) * SYNC_DRIFT_FRAMES
        self.assertFalse(sync_drift_exceeded(limit, 30.0))
        self.assertTrue(sync_drift_exceeded(limit + 1, 30.0))
        self.assertFalse(sync_drift_exceeded(-limit, 30.0))

    def test_sync_drift_threshold_shrinks_with_a_higher_source_rate(self) -> None:
        """원본 fps가 높으면 한 프레임이 짧아 보정 기준도 함께 줄어드는지 검증"""
        gap = source_frame_us(60.0) * SYNC_DRIFT_FRAMES + 1
        self.assertTrue(sync_drift_exceeded(gap, 60.0))
        self.assertFalse(sync_drift_exceeded(gap, 30.0))

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

    def test_fast_algorithms_use_the_fast_transformation(self) -> None:
        """계단 현상이 의도인 보간 방식만 빠른 변환으로 사상하는지 검증"""
        self.assertEqual(
            qt_transformation_mode("neighbor"), Qt.TransformationMode.FastTransformation
        )
        self.assertEqual(
            qt_transformation_mode("fast_bilinear"),
            Qt.TransformationMode.FastTransformation,
        )

    def test_smooth_algorithms_use_the_smooth_transformation(self) -> None:
        """정밀 보간 방식과 모르는 값은 부드러운 변환으로 사상하는지 검증"""
        for algorithm in ("bicubic", "lanczos", "spline", "area", "unknown"):
            with self.subTest(algorithm=algorithm):
                self.assertEqual(
                    qt_transformation_mode(algorithm),
                    Qt.TransformationMode.SmoothTransformation,
                )
        frame = _image(800, 450)
        self.assertEqual(
            roundtrip_compare_frame(frame, 800, 450, "480p", algorithm="lanczos").size(),
            frame.size(),
        )

    def test_roundtrip_frame_falls_back_to_the_original(self) -> None:
        frame = _image(320, 180)
        self.assertIs(roundtrip_compare_frame(frame, 320, 180, "unknown"), frame)
        self.assertIs(roundtrip_compare_frame(frame, 0, 180, "720p"), frame)


if __name__ == "__main__":
    unittest.main(verbosity=2)
