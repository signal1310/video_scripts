"""화질 분석 건너뜀 사유 판정과 사유 문구 검증"""

import unittest
from unittest import mock

from src.core.encode.encode_impact import parse_stream_summary_layout
from src.core.smart_action import SmartAction
from src.gui.frame_analysis_skip import (
    SKIP_KIND_STATUS_LABELS,
    SkipKind,
    count_skip_reasons,
    is_interlaced,
    select_run_targets,
    skip_counts_text,
    skip_reason_for_crop_scan,
    skip_reason_for_frame_analysis,
    skip_reason_tooltip,
)
from tests._factories import make_eval, make_video

_INTERLACED_SUMMARY = (
    '[{"kind":"video","codec":"h264","attached_pic":false,"field_order":"tt"}]'
)


class SkipReasonTests(unittest.TestCase):
    """화질 분석 건너뛰기 사유 판정 검증"""

    def test_reason_per_skip_kind(self) -> None:
        """각 건너뛰기 규칙이 정확한 사유를 돌려주고 대상 영상은 None을 돌려줌"""
        cases = [
            (SmartAction.PASS_EXCEPTION, SkipKind.EXCEPTION),
            (SmartAction.PASS_ALREADY_ENCODED, SkipKind.ALREADY_ENCODED),
            (SmartAction.PASS_HDR, SkipKind.HDR),
        ]
        for action, kind in cases:
            with self.subTest(action=action):
                self.assertEqual(skip_reason_for_frame_analysis(make_eval(smart_action=action)), kind)
        interlaced = make_eval(video=make_video(stream_summary=_INTERLACED_SUMMARY))
        self.assertEqual(skip_reason_for_frame_analysis(interlaced), SkipKind.INTERLACED)
        self.assertEqual(
            skip_reason_for_frame_analysis(make_eval(manual_target="720p")),
            SkipKind.MANUAL_TARGET,
        )
        self.assertIsNone(skip_reason_for_frame_analysis(make_eval()))

    def test_skip_predicates_agree_with_reasons(self) -> None:
        """예/아니오 판정이 사유 판정과 같은 기준인지 검증"""
        hdr = make_eval(smart_action=SmartAction.PASS_HDR)
        self.assertIsNotNone(skip_reason_for_frame_analysis(hdr))
        self.assertIsNotNone(skip_reason_for_crop_scan(hdr))
        self.assertIsNotNone(skip_reason_for_frame_analysis(make_eval(manual_target="keep")))
        self.assertIsNone(skip_reason_for_crop_scan(make_eval(manual_target="keep")))
        self.assertIsNone(skip_reason_for_frame_analysis(make_eval()))
        self.assertIsNone(skip_reason_for_crop_scan(make_eval()))

    def test_count_skip_reasons_counts_each_kind_in_first_seen_order(self) -> None:
        """건너뛴 영상 수를 사유별로 세고 처음 나타난 사유부터 나열"""
        evals = [
            make_eval(smart_action=SmartAction.PASS_HDR),
            make_eval(smart_action=SmartAction.PASS_EXCEPTION),
            make_eval(smart_action=SmartAction.PASS_HDR),
            make_eval(),
        ]
        counts = count_skip_reasons(
            [skip_reason_for_frame_analysis(ev) for ev in evals]
        )
        self.assertEqual(list(counts), [SkipKind.HDR, SkipKind.EXCEPTION])
        self.assertEqual(counts, {SkipKind.HDR: 2, SkipKind.EXCEPTION: 1})
        self.assertEqual(skip_counts_text(counts), "HDR 2, 예외 규칙 1")
        self.assertEqual(count_skip_reasons([skip_reason_for_frame_analysis(make_eval())]), {})

    def test_crop_scan_reason_keeps_manual_targets(self) -> None:
        """검은 여백 감지는 직접 지정 영상을 건너뛰지 않음"""
        self.assertIsNone(skip_reason_for_crop_scan(make_eval(manual_target="720p")))
        self.assertEqual(
            skip_reason_for_crop_scan(make_eval(smart_action=SmartAction.PASS_HDR)), SkipKind.HDR
        )

    def test_status_labels_cover_all_kinds(self) -> None:
        """완료 상태 줄 문구가 다섯 사유를 모두 이름 지음"""
        self.assertEqual(
            set(SKIP_KIND_STATUS_LABELS),
            set(SkipKind),
        )
        self.assertEqual(SKIP_KIND_STATUS_LABELS[SkipKind.HDR], "HDR")

    def test_tooltip_names_the_reason_in_a_polite_sentence(self) -> None:
        """사유 툴팁이 존댓말 한 문장으로 그 행을 건너뛴 이유를 알림"""
        tip = skip_reason_tooltip(SkipKind.HDR)
        self.assertIn("HDR", tip)
        self.assertTrue(tip.endswith("않습니다."))
        for kind in SkipKind:
            with self.subTest(kind=kind):
                self.assertTrue(skip_reason_tooltip(kind))

    def test_interlaced_check_parses_each_summary_once(self) -> None:
        """같은 스트림 요약 문자열은 표 재적재와 대상 선정에서 다시 해석하지 않음"""
        ev = make_eval(video=make_video(stream_summary=_INTERLACED_SUMMARY))
        # - 첫 호출로 캐시를 채운 뒤 두 번째 호출부터 해석이 일어나지 않는지 봄
        self.assertTrue(is_interlaced(ev))
        with mock.patch(
            "src.gui.frame_analysis_skip.parse_stream_summary_layout",
            wraps=parse_stream_summary_layout,
        ) as parse:
            self.assertTrue(is_interlaced(ev))
            self.assertIs(skip_reason_for_frame_analysis(ev), SkipKind.INTERLACED)
        self.assertEqual(parse.call_count, 0)

    def test_select_run_targets_keeps_every_forced_crop_row(self) -> None:
        """강제 여백 감지는 HDR을 포함한 모든 행을 측정하고 건너뜀을 세지 않음"""
        evals = [make_eval(smart_action=SmartAction.PASS_HDR), make_eval(manual_target="720p")]
        reasons = [skip_reason_for_crop_scan(ev) for ev in evals]
        selection = select_run_targets(evals, reasons, crop_only=True, force=True)
        self.assertEqual(selection.targets, tuple(evals))
        self.assertEqual(selection.skipped_counts, {})
        self.assertEqual(selection.forced_hdr_count, 0)

    def test_select_run_targets_drops_only_hdr_when_forced(self) -> None:
        """강제 화질 분석은 HDR만 빼고 나머지 사유의 행은 측정하며 그 수를 따로 알림"""
        hdr = make_eval(smart_action=SmartAction.PASS_HDR)
        excluded = make_eval(smart_action=SmartAction.PASS_EXCEPTION)
        evals = [hdr, excluded]
        reasons = [skip_reason_for_frame_analysis(ev) for ev in evals]
        selection = select_run_targets(evals, reasons, crop_only=False, force=True)
        self.assertEqual(selection.targets, (excluded,))
        self.assertEqual(selection.skipped_counts, {})
        self.assertEqual(selection.forced_hdr_count, 1)

    def test_select_run_targets_skips_every_reason_when_not_forced(self) -> None:
        """자동 실행은 모든 사유의 행을 빼고 사유별 개수를 완료 줄에 넘김"""
        hdr = make_eval(smart_action=SmartAction.PASS_HDR)
        target = make_eval()
        evals = [hdr, target]
        reasons = [skip_reason_for_frame_analysis(ev) for ev in evals]
        selection = select_run_targets(evals, reasons, crop_only=False, force=False)
        self.assertEqual(selection.targets, (target,))
        self.assertEqual(selection.skipped_counts, {SkipKind.HDR: 1})
        self.assertEqual(selection.forced_hdr_count, 0)


if __name__ == "__main__":
    unittest.main()
