"""재인코딩 진행 창의 영상 카드가 탭 제목을 줄이고, 일시중지 표시와 버튼 상태, 신호를 바르게 주는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses
from typing import Any

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.encode.encode_gate import GatePurpose
from src.core.encode.encode_progress import ProgressSample
from src.gui.dialogs.encode_graph_legend import GraphLegend
from src.gui.dialogs.encode_progress_card import (
    CAPTION_TIPS,
    CARD_CAPTIONS,
    ITEM_PAUSE_TIP,
    ITEM_RESUME_TIP,
    NO_EXPECTED_TIP,
    NO_POSITION_TIP,
    NO_VALUE_TIP,
    PROGRESS_STEPS,
    RETRY_POSITION_TIP,
    SAMPLE_POSITION_TIP,
    CardField,
    JobCard,
    card_fields,
    shorten_middle,
    tab_title,
)
from src.gui.widgets.bitrate_graph import LegendEntry
from src.service.encode.encode_events import PHASE_MAIN, PHASE_RETRY, PHASE_SAMPLING, TuningPhase
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

    def test_graph_legend_reuses_widgets_when_entries_unchanged(self) -> None:
        """GraphLegend.set_entries가 같은 항목으로 재호출될 때 기존 위젯을 재생성하지 않는지 검증"""
        legend = GraphLegend(self.card)
        entries = (LegendEntry(text="원본 2000kbps", color="#888888", style=Qt.PenStyle.SolidLine, width=1.0),)
        legend.set_entries(entries)
        first_children = list(legend.findChildren(QWidget))
        self.assertTrue(first_children)
        legend.set_entries(entries)
        self.assertEqual(list(legend.findChildren(QWidget)), first_children)



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

    def test_known_values_are_shown_with_caption_tips(self) -> None:
        """값이 모두 있으면 칸 13개가 캡션 순서대로 채워지고 항목 설명 툴팁이 붙는지 검증"""
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
        self.assertEqual(tuple(field.tip for field in fields), CAPTION_TIPS)

    def test_tuning_phases_format_position_and_target_quality(self) -> None:
        """샘플 확인, 품질값 상향 본 인코딩, 재시도 단계가 진행 위치와 목표 품질값 칸을 바꾸는지 검증"""
        sampling = TuningPhase(
            stage=PHASE_SAMPLING, sample_pass=1, sample_total=2, segment_done=3, segment_total=5,
            baseline_quality=22.0, current_quality=22.0,
        )
        s_fields = card_fields(_sample(), _job(), 2.0, None, None, None, "HEVC NVENC", phase=sampling)
        self.assertEqual((s_fields[0].text, s_fields[0].tip), ("샘플 1/2 확인 중 (구간 3/5)", SAMPLE_POSITION_TIP))
        self.assertEqual(s_fields[9].text, "계산 중 (기준 22.0)")

        main = TuningPhase(stage=PHASE_MAIN, baseline_quality=22.0, current_quality=26.6)
        m_fields = card_fields(_sample(), _job(), 10.0, 0.5, 2000.0, 150 * 1024 * 1024, "HEVC NVENC", phase=main)
        self.assertEqual(m_fields[9].text, "26.6 (기준 22.0)")
        self.assertIn("26.6", m_fields[9].tip)

        retry = TuningPhase(
            stage=PHASE_RETRY, baseline_quality=22.0, current_quality=27.6, previous_quality=26.9, retry_count=1
        )
        r_fields = card_fields(_sample(), _job(), 10.0, 0.5, 2000.0, 40 * 1024 * 1024, "HEVC NVENC", phase=retry)
        self.assertEqual((r_fields[0].text, r_fields[0].tip), ("00:30 / 01:00 (재시도 1/1)", RETRY_POSITION_TIP))
        self.assertEqual(r_fields[9].text, "27.6 (기준 22.0)")
        self.assertIn("26.9", r_fields[9].tip)

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


class JobCardActivationTests(unittest.TestCase):
    """카드 본문 왼쪽 더블클릭은 열기, 카드에 포커스가 있을 때 스페이스바는 미리 보기 요청"""

    def setUp(self) -> None:
        self.card = JobCard("a.mp4")
        self.activated: list[int] = []
        self.previews: list[int] = []
        self.card.activated.connect(lambda: self.activated.append(1))
        self.card.preview_requested.connect(lambda: self.previews.append(1))

    def test_left_double_click_on_the_card_body_emits_activated(self) -> None:
        event = QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            QPointF(1, 1),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(self.card, event)
        self.assertEqual(self.activated, [1])

    def test_other_buttons_double_click_is_ignored(self) -> None:
        event = QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            QPointF(1, 1),
            Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(self.card, event)
        self.assertEqual(self.activated, [])

    def test_space_requests_preview_and_other_keys_do_not(self) -> None:
        QApplication.sendEvent(
            self.card, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)
        )
        self.assertEqual(self.previews, [])
        QApplication.sendEvent(
            self.card, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
        )
        self.assertEqual(self.previews, [1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
