"""테이블 셀 포맷 모듈의 인코딩 상태 배지와 대기 글자색, 시간 표시 함수를 검증"""

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
from src.core.messages import LABEL_EMPTY_CELL
from src.core.smart_action import ActionBadgeTone, SmartAction
from src.gui.styles.palettes import DARK_PALETTE
from src.gui.table.table_column_formats import (
    BADGE_BY_REASON,
    KEYFRAME_EXCEEDED_ICON,
    OUTPUT_RES_NOT_DOWNSCALED_CELL,
    action_badge,
    action_foreground,
    encode_state_badge,
    encode_state_foreground,
    encode_state_icon_name,
    format_duration,
    format_quality_value_display,
    keyframe_icon_name,
    reason_foreground,
)
from src.gui.table.table_model import VideoTableModel
from src.gui.table.table_tooltips import format_row_tooltip
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])


class EncodeStateBadgeTests(unittest.TestCase):
    """인코딩 상태 표기별 배지 토큰과 대기 글자색을 검증"""

    def test_each_running_or_finished_label_maps_to_its_badge_token(self) -> None:
        """진행 중부터 교체 대기까지 표기마다 승인된 배지 토큰을 쓰는지 검증"""
        p = DARK_PALETTE
        expected = {
            STATUS_RUNNING: p.badge_encode_running,
            STATUS_RUNNING_PAUSED: p.badge_pending,
            STATUS_DONE: p.badge_pass,
            STATUS_DISCARDED: p.badge_low_kf_encode,
            STATUS_FAILED: p.badge_over,
            STATUS_SKIPPED: p.badge_pass,
            STATUS_REPLACE_PENDING: p.badge_exception,
        }
        for label, token in expected.items():
            with self.subTest(label=label):
                self.assertEqual(encode_state_badge(label, p), token)

    def test_running_badge_is_emerald_and_done_badge_is_neutral(self) -> None:
        """진행 중은 에메랄드, 완료는 중립 회색이라 선택 파랑과 구분되는지 검증"""
        p = DARK_PALETTE
        self.assertNotEqual(encode_state_badge(STATUS_RUNNING, p), p.badge_low)
        self.assertNotEqual(encode_state_badge(STATUS_DONE, p), p.badge_moved)
        self.assertEqual(encode_state_badge(STATUS_RUNNING, p), p.badge_encode_running)
        self.assertEqual(encode_state_badge(STATUS_DONE, p), p.badge_pass)

    def test_done_label_uses_the_check_icon_and_others_do_not(self) -> None:
        """완료 표기만 체크 아이콘을 쓰고 진행 중, 대기, 미표기는 아이콘이 없는지 검증"""
        self.assertEqual(encode_state_icon_name(STATUS_DONE), "check")
        for label in (STATUS_RUNNING, STATUS_RUNNING_PAUSED, STATUS_PENDING, STATUS_FAILED, None):
            with self.subTest(label=label):
                self.assertIsNone(encode_state_icon_name(label))

    def test_pending_or_missing_label_has_no_badge(self) -> None:
        """대기와 배치 대상이 아닌 행은 배지 없이 None을 돌려주는지 검증"""
        self.assertIsNone(encode_state_badge(STATUS_PENDING, DARK_PALETTE))
        self.assertIsNone(encode_state_badge(None, DARK_PALETTE))

    def test_pending_label_uses_the_muted_text_color_and_others_do_not(self) -> None:
        """대기만 보조 글자색을 쓰고 배지가 있는 표기는 글자색을 따로 정하지 않는지 검증"""
        self.assertEqual(encode_state_foreground(STATUS_PENDING, DARK_PALETTE), DARK_PALETTE.text_muted)
        self.assertIsNone(encode_state_foreground(STATUS_DONE, DARK_PALETTE))
        self.assertIsNone(encode_state_foreground(None, DARK_PALETTE))


class ActionAndReasonToneTests(unittest.TestCase):
    """권장 처리 알약과 판정 사유 글자색이 3단계 시각 위계를 지키는지 검증"""

    def test_pass_action_has_no_pill_and_uses_muted_text(self) -> None:
        """원본 보존 행은 채운 알약 없이 흐린 글자로만 표시하는지 검증"""
        ev = make_eval(video=make_video(filename="pass.mp4"), smart_action=SmartAction.PASS_OPTIMAL)
        self.assertIs(ev.action_badge_tone, ActionBadgeTone.PASS)
        self.assertIsNone(action_badge(ev, DARK_PALETTE))
        self.assertEqual(action_foreground(ev, DARK_PALETTE), DARK_PALETTE.text_muted)

    def test_encode_action_keeps_its_pill_and_default_text_color(self) -> None:
        """인코딩 행은 배지 토큰을 그대로 쓰고 글자색은 지정하지 않는지 검증"""
        ev = make_eval(video=make_video(filename="enc.mp4"), smart_action=SmartAction.ENCODE_OVER)
        self.assertIs(ev.action_badge_tone, ActionBadgeTone.STD_ENCODE)
        self.assertEqual(action_badge(ev, DARK_PALETTE), DARK_PALETTE.badge_std_encode)
        self.assertIsNone(action_foreground(ev, DARK_PALETTE))

    def test_reason_foreground_borrows_the_reason_badge_text_color(self) -> None:
        """판정 사유는 알약 없이 사유 배지의 글자색만 빌려 쓰고, 매핑에 없는 사유는 중립색을 쓰는지 검증"""
        over = make_eval(video=make_video(filename="over.mp4"), smart_action=SmartAction.ENCODE_OVER)
        self.assertEqual(over.reason_label, SmartAction.ENCODE_OVER.reason_label)
        self.assertEqual(reason_foreground(over, DARK_PALETTE), DARK_PALETTE.badge_over[1])
        hdr = make_eval(video=make_video(filename="hdr.mp4"), smart_action=SmartAction.PASS_HDR)
        self.assertNotIn(hdr.reason_label, BADGE_BY_REASON)
        self.assertEqual(reason_foreground(hdr, DARK_PALETTE), DARK_PALETTE.badge_pass[1])


class KeyframeIconTests(unittest.TestCase):
    """키프레임 간격 초과가 경고 아이콘 이름으로만 표현되는지 검증"""

    def test_exceeded_interval_returns_the_warning_icon(self) -> None:
        """허용 상한을 넘은 행만 경고 삼각형 아이콘 이름을 돌려주는지 검증"""
        exceeded = make_eval(video=make_video(filename="kf.mp4"), is_keyframe_exceeded=True)
        self.assertEqual(keyframe_icon_name(exceeded), KEYFRAME_EXCEEDED_ICON)

        normal = make_eval(video=make_video(filename="ok.mp4"), is_keyframe_exceeded=False)
        self.assertIsNone(keyframe_icon_name(normal))


class DurationFormatTests(unittest.TestCase):
    """길이 표시 함수가 시, 분, 초 형식을 만드는지 검증"""

    def test_duration_drops_the_hour_field_under_an_hour(self) -> None:
        """1시간 미만은 MM:SS, 이상은 HH:MM:SS로 나오는지 검증"""
        self.assertEqual(format_duration(59.0), "00:59")
        self.assertEqual(format_duration(3725.0), "01:02:05")

    def test_not_downscaled_cell_is_the_dash(self) -> None:
        """축소하지 않은 출력 해상도 칸은 대시 한 글자인지 검증"""
        self.assertEqual(OUTPUT_RES_NOT_DOWNSCALED_CELL, "-")


class EmptyQualityValueCellTests(unittest.TestCase):
    """품질값이 없는 칸 표기가 공용 상수 하나에서 오는지 검증"""

    def test_gui_quality_cell_and_tooltip_use_the_shared_empty_label(self) -> None:
        """표 셀과 행 툴팁의 빈 품질값 표기가 공용 상수를 그대로 쓰는지 검증"""
        kept = make_eval(
            video=make_video(filename="kept.mp4"), smart_action=SmartAction.PASS_OPTIMAL
        )
        display = format_quality_value_display(kept, VideoTableModel().config)
        self.assertEqual(display, LABEL_EMPTY_CELL)
        self.assertIn(f"품질값: {LABEL_EMPTY_CELL}", format_row_tooltip(kept, "C:/work", "HEVC"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
