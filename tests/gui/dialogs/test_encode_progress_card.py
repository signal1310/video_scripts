"""재인코딩 진행 창의 영상 카드가 탭 제목을 줄이고, 일시중지 표시와 버튼 상태, 신호를 바르게 주는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses
from typing import Any

from PyQt6.QtWidgets import QApplication

from src.core.encode.encode_gate import GatePurpose
from src.core.encode.encode_progress import ProgressSample
from src.gui.dialogs.encode_progress_card import (
    CARD_CAPTIONS,
    ITEM_PAUSE_TIP,
    ITEM_RESUME_TIP,
    NO_EXPECTED_TIP,
    NO_POSITION_TIP,
    NO_VALUE_TIP,
    PROGRESS_STEPS,
    CardField,
    JobCard,
    card_fields,
    shorten_middle,
    tab_title,
)
from src.service.encode.encode_job import EncodeJob

_APP = QApplication.instance() or QApplication([])


class TabTitleTests(unittest.TestCase):
    """탭 제목의 줄임과 순서 번호 표기를 검증"""

    def test_long_names_are_shortened_in_the_middle_and_short_names_are_kept(self) -> None:
        """긴 파일 이름은 가운데를 줄이고 짧은 이름은 그대로 두며, 탭 제목에 순서 번호가 붙는지 검증"""
        self.assertEqual(shorten_middle("video10_normal_phone.mp4", 12), "video1…e.mp4")
        self.assertEqual(shorten_middle("short.mp4", 12), "short.mp4")
        self.assertEqual(shorten_middle("abcdef", 1), "…")
        self.assertEqual(tab_title(3, "video1.mp4"), "3. video1.mp4")


class JobCardTests(unittest.TestCase):
    """카드의 일시중지 표시, 버튼 상태와 신호, 진행 값 설정을 검증"""

    def setUp(self) -> None:
        self.card = JobCard("a.mp4")

    def test_pause_state_changes_icon_tooltip_and_hides_the_notice_on_resume(self) -> None:
        """일시중지 상태에서 툴팁이 재개 문장으로 바뀌고, 재개하면 장시간 안내가 숨겨지는지 검증"""
        self.card.set_long_pause(True)
        self.card.set_paused(True)
        self.assertTrue(self.card.is_paused)
        self.assertEqual(self.card._pause_btn.toolTip(), ITEM_RESUME_TIP)
        self.assertFalse(self.card._notice.isHidden())
        self.card.set_paused(False)
        self.assertFalse(self.card.is_paused)
        self.assertEqual(self.card._pause_btn.toolTip(), ITEM_PAUSE_TIP)
        self.assertTrue(self.card._notice.isHidden())

    def test_pause_button_can_be_disabled_during_a_batch_pause(self) -> None:
        """전체 일시중지에 맞춰 개별 일시중지 버튼을 끄고 켤 수 있는지 검증"""
        self.card.set_pause_enabled(False)
        self.assertFalse(self.card._pause_btn.isEnabled())
        self.card.set_pause_enabled(True)
        self.assertTrue(self.card._pause_btn.isEnabled())

    def test_buttons_and_notice_link_emit_their_signals(self) -> None:
        """일시중지, 중지 버튼과 장시간 안내 링크가 각자의 신호를 내는지 검증"""
        received: list[str] = []
        self.card.pause_toggled.connect(lambda: received.append("pause"))
        self.card.stop_requested.connect(lambda: received.append("stop"))
        self.card.stop_after_requested.connect(lambda: received.append("stop_after"))
        self.card._pause_btn.click()
        self.card._stop_btn.click()
        self.card._notice.linkActivated.emit("stop-after")
        self.assertEqual(received, ["pause", "stop", "stop_after"])

    def test_progress_and_values_show_the_given_text(self) -> None:
        """진행 막대는 비율을 따르고 값이 없으면 0이며, 수치 칸은 캡션 순서대로 채워지는지 검증"""
        self.card.set_progress(0.5)
        self.assertEqual(self.card._bar.value(), PROGRESS_STEPS // 2)
        self.card.set_progress(None)
        self.assertEqual(self.card._bar.value(), 0)
        fields = tuple(CardField(f"값{index}", f"사유{index}") for index in range(len(CARD_CAPTIONS)))
        self.card.set_fields(fields)
        self.assertEqual(self.card._values[0].text(), "값0")
        self.assertEqual(self.card._values[12].text(), "값12")
        self.assertEqual(self.card._values[12].toolTip(), "사유12")



def _job(**overrides: Any) -> EncodeJob:
    """계획 입력 기본값에 덮어쓸 값을 적용한 작업"""
    base = EncodeJob(
        filename="clip.mp4",
        root_dir="/root",
        source_path="/root/clip.mp4",
        source_size=100 * 1024 * 1024,
        source_mtime_ns=1,
        source_duration_sec=60.0,
        source_ext="mp4",
        fps=30.0,
        quality=22.0,
        purpose=GatePurpose.SAVING,
        output_size=(1280, 720),
        scale_filter=None,
        maxrate_kbps=None,
        expected_after_mb=398.0,
        expected_delta_mb=12.0,
    )
    return dataclasses.replace(base, **overrides)


def _sample(**overrides: Any) -> ProgressSample:
    base = ProgressSample(
        frame=900,
        fps=30.0,
        quality=22.0,
        bitrate_kbps=2000.0,
        total_size_bytes=10 * 1024 * 1024,
        out_time_sec=30.0,
        speed=1.25,
        finished=False,
    )
    return dataclasses.replace(base, **overrides)


class CardFieldsTests(unittest.TestCase):
    """카드 칸 문구와 값 없음 사유 툴팁을 검증"""

    def test_known_values_are_shown_without_tips(self) -> None:
        """값이 모두 있으면 칸 13개가 캡션 순서대로 채워지고 사유 툴팁이 비어 있는지 검증"""
        fields = card_fields(_sample(), _job(), 24.0, 0.5, 2100.0, 200 * 1024 * 1024, "HEVC NVENC")
        self.assertEqual(len(fields), len(CARD_CAPTIONS))
        self.assertEqual(fields[0].text, "00:30 / 01:00")
        self.assertEqual(fields[1].text, "1.25배, 30fps")
        self.assertEqual(fields[2].text, "900")
        self.assertEqual(fields[3].text, "22.0")
        self.assertEqual(fields[6].text, "10.0 MB")
        self.assertEqual(fields[7].text, "200.0 MB")
        self.assertEqual(fields[8].text, "398.0 MB")
        self.assertEqual(fields[9].text, "22.0")
        self.assertEqual(fields[10].text, "720p")
        self.assertEqual(fields[11].text, "HEVC NVENC")
        self.assertEqual(fields[12].text, "2100kbps")
        self.assertTrue(all(field.tip == "" for field in fields))

    def test_missing_position_and_projection_explain_why(self) -> None:
        """진행 위치가 없으면 위치와 예상 최종 크기가 정보 없음이고 각각 사유를 보여 주는지 검증"""
        fields = card_fields(_sample(out_time_sec=None), _job(), 24.0, None, None, None, "HEVC NVENC")
        self.assertEqual((fields[0].text, fields[0].tip), ("정보 없음", NO_POSITION_TIP))
        self.assertEqual((fields[7].text, fields[7].tip), ("정보 없음", NO_EXPECTED_TIP))
        self.assertEqual((fields[12].text, fields[12].tip), ("정보 없음", NO_VALUE_TIP))

    def test_legacy_job_without_expected_size_and_no_scale_resolution(self) -> None:
        """이전 기록의 판정 예상 용량은 정보 없음으로, 축소하지 않은 항목의 해상도는 축소 안 함으로 나오는지 검증"""
        fields = card_fields(_sample(), _job(expected_after_mb=None, output_size=None), 24.0, 0.5, None, None, "H")
        self.assertEqual((fields[8].text, fields[8].tip), ("정보 없음", NO_VALUE_TIP))
        self.assertEqual(fields[10].text, "축소 안 함")


if __name__ == "__main__":
    unittest.main(verbosity=2)
