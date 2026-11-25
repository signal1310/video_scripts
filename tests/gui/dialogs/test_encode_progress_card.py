"""인코딩 진행 창의 영상 카드가 탭 제목을 줄이고, 일시중지 표시와 버튼 상태, 신호를 바르게 주는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses
from typing import Any

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QFont, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

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
    SAMPLE_PREDICT_TIP,
    CardField,
    JobCard,
    apply_card_phase,
    bar_style,
    card_fields,
    field_columns,
    shorten_middle,
    tab_title,
)
from src.gui.dialogs.encode_progress_window import WINDOW_MIN_WIDTH_PX
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.widgets.bitrate_graph import LegendEntry
from src.service.encode.encode_events import (
    PHASE_MAIN,
    PHASE_RETRY,
    PHASE_SAMPLE_PREDICT,
    PHASE_SAMPLING,
    TuningPhase,
)
from src.service.encode.encode_job import EncodeJob

_APP = QApplication.instance() or QApplication([])
_LARGE_FONT_PT: float = 13.0
# - 창 최소 폭과, 13pt 값이 한 줄에 들어가는 넉넉한 카드 폭
_CARD_MIN_WIDTH_PX: int = 460
_CARD_ROOMY_WIDTH_PX: int = 800


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

    def test_job_card_children_all_have_parent_and_same_window(self) -> None:
        """JobCard 내부에 생성된 모든 하위 위젯이 명시적 부모와 같은 최상위 창을 가지는지 검증"""
        children = self.card.findChildren(QWidget)
        self.assertTrue(children)
        for child in children:
            self.assertIsNotNone(child.parentWidget())
            self.assertIs(child.window(), self.card.window())

    def test_position_values_stay_on_one_line_at_a_13pt_font(self) -> None:
        """13pt에서도 값 칸이 두 줄로 접히지 않고, 카드를 넉넉히 넓히면 위치 문구가 한 줄 폭 안에 들어감"""
        for widget in self.card._values:
            widget.setFont(self._large_font())
        self.card.show()
        self.addCleanup(self.card.hide)
        label = self.card._values[0]
        for text in ("사전 예측 (2/4)", "품질 시험 1/2 (3/5)", "00:30 / 01:00"):
            with self.subTest(text=text):
                self.card.set_fields(
                    (CardField(text, ""), *(CardField("값", "") for _ in range(len(CARD_CAPTIONS) - 1)))
                )
                # - 창 최소 폭에서는 값 칸이 한 줄 높이를 넘지 않음
                self.card.resize(_CARD_MIN_WIDTH_PX, self.card.sizeHint().height())
                _APP.processEvents()
                self.assertEqual(label.text(), text)
                self.assertLess(label.height(), label.fontMetrics().height() * 2)
                # - 카드를 넓히면 늘림 비율로 값을 나눠 가져 위치 문구가 한 줄에 그려짐
                self.card.resize(_CARD_ROOMY_WIDTH_PX, self.card.sizeHint().height())
                _APP.processEvents()
                self.assertGreaterEqual(label.width(), label.fontMetrics().horizontalAdvance(text))
                self.assertLess(label.height(), label.fontMetrics().height() * 2)

    def test_value_labels_do_not_inflate_the_card_minimum_width(self) -> None:
        """값 칸이 글자 폭을 최소 폭으로 잡지 않아 카드 최소 폭이 창 최소 폭 안에 머묾"""
        self.card.set_fields(
            (
                CardField("123.4 MB / 5.6 GB (재시도 1/1)", ""),
                *(CardField("값", "") for _ in range(len(CARD_CAPTIONS) - 1)),
            )
        )
        self.assertLessEqual(self.card.minimumSizeHint().width(), WINDOW_MIN_WIDTH_PX)

    def test_field_columns_put_every_value_in_the_wide_column(self) -> None:
        """카드 격자는 짝수 열을 캡션, 홀수 열을 값으로 써 값 칸이 남는 폭을 모두 받음"""
        self.assertEqual(field_columns(), (1, 3))

    def test_pre_encode_phase_colors_the_bar_orange_and_main_uses_the_progress_fill(self) -> None:
        """본 인코딩 전 준비 단계는 진행 막대를 강조 주황으로, 본 인코딩은 기본 채움색으로 칠함"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette.name):
                self.card.apply_palette(palette)
                self.card.set_pre_encode(True)
                self.assertIn(palette.accent_orange, self.card._bar.styleSheet())
                self.card.set_pre_encode(False)
                self.assertIn(palette.progress_fill, self.card._bar.styleSheet())
                self.assertIn(palette.accent_orange, bar_style(palette, pre_encode=True))

    def test_repeated_pre_encode_does_not_reapply_the_bar_style(self) -> None:
        """같은 준비 단계 값을 다시 넘겨도 스타일시트를 새로 파싱하지 않고, 값이 바뀌거나 테마를 다시 적용할 때만 칠함"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette.name):
                self.card.apply_palette(palette)
                calls: list[str] = []
                original = self.card._bar.setStyleSheet
                self.card._bar.setStyleSheet = calls.append  # type: ignore[method-assign]
                try:
                    self.card.set_pre_encode(True)
                    self.card.set_pre_encode(True)
                finally:
                    self.card._bar.setStyleSheet = original
                self.assertEqual(len(calls), 1)
                self.assertIn(palette.accent_orange, calls[0])
                self.card.set_pre_encode(False)
                self.assertIn(palette.progress_fill, self.card._bar.styleSheet())
                # - 테마를 다시 적용하면 지금 값의 색을 새 팔레트로 다시 칠함
                other = LIGHT_PALETTE if palette is DARK_PALETTE else DARK_PALETTE
                self.card.apply_palette(other)
                self.assertIn(other.progress_fill, self.card._bar.styleSheet())

    def test_apply_card_phase_switches_the_card_and_chip_bar_colors(self) -> None:
        """품질 시험 이벤트는 카드와 칩 막대를 주황으로, 본 인코딩 이벤트는 기본색으로 되돌림"""
        self.card.apply_palette(DARK_PALETTE)
        chip_states: list[bool] = []
        apply_card_phase(
            self.card,
            _job(),
            "HEVC NVENC",
            TuningPhase(stage=PHASE_SAMPLING, segment_done=1, segment_total=5, baseline_quality=22.0),
            set_chip_phase=lambda _badge, _tip: None,
            set_chip_progress=lambda _frac: None,
            set_chip_pre_encode=chip_states.append,
        )
        self.assertIn(DARK_PALETTE.accent_orange, self.card._bar.styleSheet())
        self.assertEqual(chip_states, [True])
        apply_card_phase(
            self.card,
            _job(),
            "HEVC NVENC",
            TuningPhase(stage=PHASE_MAIN, baseline_quality=22.0, current_quality=22.0),
            set_chip_phase=lambda _badge, _tip: None,
            set_chip_progress=lambda _frac: None,
            set_chip_pre_encode=chip_states.append,
        )
        self.assertIn(DARK_PALETTE.progress_fill, self.card._bar.styleSheet())
        self.assertEqual(chip_states, [True, False])

    def _large_font(self) -> QFont:
        """13pt 크기의 카드 글꼴"""
        font = QFont(self.card.font())
        font.setPointSizeF(_LARGE_FONT_PT)
        return font



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
        self.assertEqual(s_fields[0].text, "품질 시험 1/2 (3/5)")
        self.assertTrue(s_fields[0].tip.startswith(SAMPLE_POSITION_TIP))
        self.assertIn("지금 1/2번째 시험이며 대표 구간 3/5을 마쳤습니다.", s_fields[0].tip)
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

    def test_saving_sample_prediction_keeps_the_fixed_quality_and_segment_position(self) -> None:
        """절약 목적 샘플 예측은 고정 품질값과 구간 진행 위치를 보여 주고 계산 중 표기를 쓰지 않음"""
        predict = TuningPhase(
            stage=PHASE_SAMPLE_PREDICT, segment_done=2, segment_total=5,
            baseline_quality=22.0, current_quality=22.0,
        )
        fields = card_fields(_sample(), _job(), 1.0, None, None, None, "HEVC NVENC", phase=predict)
        self.assertEqual(fields[0].text, "사전 예측 (2/5)")
        self.assertTrue(fields[0].tip.startswith(SAMPLE_PREDICT_TIP))
        self.assertIn("대표 구간 2/5을 마쳤습니다.", fields[0].tip)
        self.assertEqual(fields[9].text, "22.0")
        self.assertNotIn("계산 중", fields[9].text)

    def test_saving_adjustment_shows_new_quality_and_notice(self) -> None:
        """절약 목적 사전 예측으로 품질값을 올리면 목표 품질값 칸과 안내에 조정 전후가 보임"""
        adjusted = TuningPhase(
            stage=PHASE_MAIN, baseline_quality=22.0, current_quality=24.1, adjusted_quality=24.1
        )
        fields = card_fields(
            _sample(), _job(), 10.0, 0.5, 2000.0, 150 * 1024 * 1024, "HEVC NVENC", phase=adjusted
        )
        self.assertEqual(fields[9].text, "24.1 (자동 조정)")
        self.assertIn("24.1", fields[9].tip)

        card = JobCard("clip.mp4")
        self.addCleanup(card.deleteLater)
        phase_texts: list[str] = []
        apply_card_phase(
            card,
            _job(),
            "HEVC NVENC",
            adjusted,
            set_chip_phase=lambda badge, tip: phase_texts.append(badge),
            set_chip_progress=lambda _frac: None,
            set_chip_pre_encode=lambda _pre: None,
        )
        notice = card.findChild(QLabel, "EncodeRetryNotice")
        assert notice is not None
        self.assertEqual(notice.text(), "품질값 자동 조정 CQ22.0 -> CQ24.1")
        self.assertFalse(notice.isHidden())

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
