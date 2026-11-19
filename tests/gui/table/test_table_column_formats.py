"""테이블 셀 포맷 모듈의 재인코딩 상태 배지와 대기 글자색, 시간 표시 함수를 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.encode.encode_batch_book import (
    STATUS_DISCARDED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REPLACE_PENDING,
    STATUS_RUNNING,
    STATUS_RUNNING_PAUSED,
    STATUS_SKIPPED,
)
from src.gui.styles.palettes import DARK_PALETTE
from src.gui.table.table_column_formats import (
    OUTPUT_RES_NOT_DOWNSCALED_CELL,
    encode_state_badge,
    encode_state_foreground,
    format_duration,
)

_APP = QApplication.instance() or QApplication([])


class EncodeStateBadgeTests(unittest.TestCase):
    """재인코딩 상태 표기별 배지 토큰과 대기 글자색을 검증"""

    def test_each_running_or_finished_label_maps_to_its_badge_token(self) -> None:
        """진행 중부터 교체 대기까지 표기마다 승인된 배지 토큰을 쓰는지 검증"""
        p = DARK_PALETTE
        expected = {
            STATUS_RUNNING: p.badge_low,
            STATUS_RUNNING_PAUSED: p.badge_pending,
            STATUS_DONE: p.badge_moved,
            STATUS_DISCARDED: p.badge_low_kf_encode,
            STATUS_FAILED: p.badge_over,
            STATUS_SKIPPED: p.badge_pass,
            STATUS_REPLACE_PENDING: p.badge_exception,
        }
        for label, token in expected.items():
            with self.subTest(label=label):
                self.assertEqual(encode_state_badge(label, p), token)

    def test_pending_or_missing_label_has_no_badge(self) -> None:
        """대기와 배치 대상이 아닌 행은 배지 없이 None을 돌려주는지 검증"""
        self.assertIsNone(encode_state_badge(STATUS_PENDING, DARK_PALETTE))
        self.assertIsNone(encode_state_badge(None, DARK_PALETTE))

    def test_pending_label_uses_the_muted_text_color_and_others_do_not(self) -> None:
        """대기만 보조 글자색을 쓰고 배지가 있는 표기는 글자색을 따로 정하지 않는지 검증"""
        self.assertEqual(encode_state_foreground(STATUS_PENDING, DARK_PALETTE), DARK_PALETTE.text_muted)
        self.assertIsNone(encode_state_foreground(STATUS_DONE, DARK_PALETTE))
        self.assertIsNone(encode_state_foreground(None, DARK_PALETTE))


class DurationFormatTests(unittest.TestCase):
    """길이 표시 함수가 시, 분, 초 형식을 만드는지 검증"""

    def test_duration_drops_the_hour_field_under_an_hour(self) -> None:
        """1시간 미만은 MM:SS, 이상은 HH:MM:SS로 나오는지 검증"""
        self.assertEqual(format_duration(59.0), "00:59")
        self.assertEqual(format_duration(3725.0), "01:02:05")

    def test_not_downscaled_cell_is_the_dash(self) -> None:
        """축소하지 않은 출력 해상도 칸은 대시 한 글자인지 검증"""
        self.assertEqual(OUTPUT_RES_NOT_DOWNSCALED_CELL, "-")


if __name__ == "__main__":
    unittest.main(verbosity=2)
