"""화질 분석 표 열 세 개의 셀 문구, 정렬 값, 필터, 툴팁 검증"""

import unittest

from src.core.analysis.frame_analysis import CROP_SCAN_VERSION, ActiveRegion, TierScore
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.config import ClassifierConfig
from src.core.models import ActiveFrame, VideoEvaluation
from src.gui.styles.palettes import DARK_PALETTE as _PALETTE
from src.gui.table.frame_analysis_columns import (
    ACTIVE_FRAME_COLUMN,
    ACTIVE_FRAME_FAILED_TEXT,
    ACTIVE_FRAME_FULL_TEXT,
    ACTIVE_FRAME_VR_TEXT,
    CROP_REVIEW_TEXT,
    DOWNSCALE_CANDIDATE_COLUMN,
    DOWNSCALE_KEEP_TEXT,
    DOWNSCALE_UNMEASURABLE_TEXT,
    METRIC_SSIM_FALLBACK_TEXT,
    METRIC_VMAF_TEXT,
    QUALITY_METRIC_COLUMN,
    FrameAnalysisResults,
    active_frame_sort_value,
    active_frame_text,
    active_frame_tooltip,
    crop_review_tooltip,
    downscale_candidate_sort_value,
    downscale_candidate_text,
    downscale_candidate_tooltip,
    is_frame_analysis_column,
    quality_metric_sort_value,
    quality_metric_text,
    quality_metric_tooltip,
    result_summary_text,
    tier_display,
)
from src.gui.table.table_column_formats import (
    format_output_res_display,
    output_res_sort_value,
)
from src.gui.table.table_display_values import foreground_color
from src.gui.table.table_tooltips import format_output_res_tooltip
from tests._factories import make_eval, make_video

# - 표 툴팁에 덧붙는 구간별 여백 차이 안내 문장(정확한 문구 확인용)
_MARGINS_VARY_SENTENCE: str = (
    "구간마다 검은 여백이 달라 가장 넓게 겹치는 영역만 활성 화면으로 잡았습니다. "
    "미리 보기에서 직접 지정할 수 있습니다."
)


def _result(
    *,
    active: ActiveRegion | None = None,
    metric: str = "vmaf",
    candidate: str | None = "1080p",
    unmeasurable: bool = False,
    vr: bool = False,
    scores: tuple[TierScore, ...] | None = None,
    version: int = 202,
    scan_failed: bool = False,
) -> FrameAnalysisResult:
    return FrameAnalysisResult(
        active_region=active or ActiveRegion(1920, 1080, 0, 0, False),
        metric_used=metric,
        vmaf_model="vmaf_v0.6.1",
        tier_scores=scores if scores is not None else (TierScore("1080p", 0.9983, 98.7),),
        candidate_tier=candidate,
        valid_segment_count=9,
        total_segment_count=10,
        unmeasurable=unmeasurable,
        is_vr_protected=vr,
        crop_scan_failed=scan_failed,
        analysis_version=version,
    )


class FrameAnalysisTextTests(unittest.TestCase):
    """활성 화면, 축소 후보, 측정 지표의 표시 문구 검증"""

    def test_active_frame_values(self) -> None:
        self.assertEqual(active_frame_text(None), "")
        self.assertEqual(active_frame_text(_result()), ACTIVE_FRAME_FULL_TEXT)
        self.assertEqual(
            active_frame_text(_result(active=ActiveRegion(1920, 800, 0, 140, True))), "1920x800"
        )
        self.assertEqual(active_frame_text(_result(vr=True)), ACTIVE_FRAME_VR_TEXT)

    def test_downscale_candidate_values(self) -> None:
        self.assertEqual(downscale_candidate_text(None), "")
        self.assertEqual(downscale_candidate_text(_result(candidate="720p")), "720p")
        self.assertEqual(downscale_candidate_text(_result(candidate=None)), DOWNSCALE_KEEP_TEXT)
        self.assertEqual(
            downscale_candidate_text(_result(unmeasurable=True, candidate=None)),
            DOWNSCALE_UNMEASURABLE_TEXT,
        )
        self.assertEqual(tier_display("unknown"), "unknown")
        self.assertEqual(tier_display("2160p"), "4K")

    def test_quality_metric_values(self) -> None:
        self.assertEqual(quality_metric_text(None), "")
        self.assertEqual(quality_metric_text(_result(metric="vmaf")), METRIC_VMAF_TEXT)
        self.assertEqual(quality_metric_text(_result(metric="ssim")), METRIC_SSIM_FALLBACK_TEXT)
        self.assertEqual(quality_metric_text(_result(metric="other")), "other")

    def test_result_summary_reuses_the_column_texts(self) -> None:
        """한 줄 요약이 표 열 세 문구를 쉼표로 이어 붙이는지 검증"""
        self.assertEqual(result_summary_text(None), "")
        result = _result(candidate="720p")
        self.assertEqual(
            result_summary_text(result),
            f"{active_frame_text(result)}, {downscale_candidate_text(result)}, "
            f"{quality_metric_text(result)}",
        )
        self.assertEqual(result_summary_text(result), "-, 720p, VMAF")

    def test_sort_values_distinguish_missing_keep_and_candidates(self) -> None:
        self.assertEqual(active_frame_sort_value(None), (0, 0))
        self.assertEqual(active_frame_sort_value(_result()), (1, 1920 * 1080))
        self.assertEqual(downscale_candidate_sort_value(None), (0, 0))
        self.assertEqual(downscale_candidate_sort_value(_result(unmeasurable=True, candidate=None)), (1, 0))
        self.assertEqual(downscale_candidate_sort_value(_result(candidate=None)), (2, 0))
        self.assertEqual(downscale_candidate_sort_value(_result(candidate="1080p")), (3, 1080))
        self.assertEqual(downscale_candidate_sort_value(_result(candidate="unknown")), (3, 0))
        self.assertEqual(quality_metric_sort_value(None), (0, ""))
        self.assertEqual(quality_metric_sort_value(_result(metric="ssim")), (1, "ssim"))
        self.assertEqual(quality_metric_sort_value(_result(metric="vmaf")), (2, "vmaf"))


class FrameAnalysisTooltipTests(unittest.TestCase):
    """축소 후보 점수 줄, 유효 구간, SSIM 대체와 VR 보호 툴팁 검증"""

    def test_active_frame_tooltips(self) -> None:
        self.assertIn("화질 분석 전", active_frame_tooltip(None))
        self.assertIn("축소 후보는 정상 측정", active_frame_tooltip(_result(vr=True)))
        self.assertIn(
            "검은 띠를 뺀", active_frame_tooltip(_result(active=ActiveRegion(1920, 800, 0, 140, True)))
        )
        self.assertIn("검은 띠가 없어", active_frame_tooltip(_result()))

    def test_crop_off_tooltip_says_the_bars_will_not_be_cropped(self) -> None:
        """검은 여백 잘라내기를 끄면 활성 화면 크기 툴팁이 자르지 않는다고 알리는지 검증"""
        cropped = _result(active=ActiveRegion(1920, 800, 0, 140, True))
        tip = active_frame_tooltip(cropped, crop_enabled=False)
        self.assertIn("검은 여백 잘라내기 설정이 꺼져 있어", tip)
        self.assertNotIn("검은 여백 잘라내기 설정이 꺼져 있어", active_frame_tooltip(cropped))
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": cropped})
        self.assertIn("잘라내기 설정이 꺼져 있어", view.tooltip("a.mp4", ACTIVE_FRAME_COLUMN, crop_enabled=False))
        self.assertNotIn("잘라내기 설정이 꺼져 있어", view.tooltip("a.mp4", ACTIVE_FRAME_COLUMN))
        view.set_manual_frames({"a.mp4": ActiveFrame(1600, 1000, 260, 40)})
        manual_off = view.tooltip("a.mp4", ACTIVE_FRAME_COLUMN, crop_enabled=False)
        self.assertIn("사용자 지정 활성 화면입니다.", manual_off)
        self.assertIn("잘라내기 설정이 꺼져 있어", manual_off)

    def test_crop_off_excluded_row_keeps_the_exclusion_reason(self) -> None:
        """잘라내기 설정이 꺼져 있어도 영상별 제외 사유를 함께 보여 주는지 검증"""
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": _result(active=ActiveRegion(1920, 800, 0, 140, True))})
        view.set_crop_excluded(["a.mp4"])
        tip = view.tooltip("a.mp4", ACTIVE_FRAME_COLUMN, crop_enabled=False)
        self.assertIn("검은 여백 잘라내기 제외됨 (원본 유지)", tip)
        self.assertIn("검은 여백 잘라내기 설정이 꺼져 있어", tip)
        not_excluded = FrameAnalysisResults()
        not_excluded.set_results({"a.mp4": _result(active=ActiveRegion(1920, 800, 0, 140, True))})
        other_tip = not_excluded.tooltip("a.mp4", ACTIVE_FRAME_COLUMN, crop_enabled=False)
        self.assertNotIn("검은 여백 잘라내기 제외됨 (원본 유지)", other_tip)
        self.assertIn("검은 여백 잘라내기 설정이 꺼져 있어", other_tip)

    def test_downscale_tooltips_include_scores_and_valid_segments(self) -> None:
        tip = downscale_candidate_tooltip(_result(candidate="1080p"))
        self.assertIn("- 1080p: VMAF 98.7, SSIM 0.9983", tip)
        self.assertIn("유효 구간: 9/10", tip)
        self.assertNotIn("SSIM으로 대신", tip)
        ssim_tip = downscale_candidate_tooltip(_result(metric="ssim", candidate=None))
        self.assertIn("유지", ssim_tip)
        self.assertIn("SSIM으로 대신", ssim_tip)
        self.assertIn("- 점수 없음", downscale_candidate_tooltip(_result(scores=())))
        unmeasurable = downscale_candidate_tooltip(_result(unmeasurable=True, candidate=None))
        self.assertIn("판정하지 못했습니다", unmeasurable)

    def test_metric_tooltips(self) -> None:
        self.assertIn("화질 분석 전", quality_metric_tooltip(None))
        self.assertIn("VMAF", quality_metric_tooltip(_result(metric="vmaf")))
        self.assertIn("SSIM", quality_metric_tooltip(_result(metric="ssim")))

    def test_margins_vary_sentence_is_appended_only_without_a_manual_frame(self) -> None:
        """구간별 여백 차이 안내는 자동 판정 툴팁에 한 문장 붙고 직접 지정 시 생략됨"""
        varying = _result(
            active=ActiveRegion(1920, 1080, 0, 0, False, margins_vary=True),
        )
        self.assertIn(_MARGINS_VARY_SENTENCE, active_frame_tooltip(varying))
        self.assertIn("검은 띠가 없어", active_frame_tooltip(varying))
        self.assertNotIn(_MARGINS_VARY_SENTENCE, active_frame_tooltip(_result()))
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": varying})
        self.assertIn(_MARGINS_VARY_SENTENCE, view.tooltip("a.mp4", ACTIVE_FRAME_COLUMN))
        self.assertEqual(view.display("a.mp4", ACTIVE_FRAME_COLUMN), ACTIVE_FRAME_FULL_TEXT)
        view.set_manual_frames({"a.mp4": ActiveFrame(1600, 1000, 260, 40)})
        manual_tip = view.tooltip("a.mp4", ACTIVE_FRAME_COLUMN)
        self.assertNotIn(_MARGINS_VARY_SENTENCE, manual_tip)
        self.assertIn("사용자 지정 활성 화면입니다.", manual_tip)
        self.assertIn(
            _MARGINS_VARY_SENTENCE,
            active_frame_tooltip(_result(active=ActiveRegion(1440, 1080, 240, 0, True, True)), excluded=True),
        )

    def test_crop_only_rows_show_blank_cells_with_dedicated_tooltips(self) -> None:
        """검은 여백 감지 전용 행이 유지 대신 빈칸과 전용 툴팁을 쓰는지 검증"""
        result = _result(
            active=ActiveRegion(720, 1080, 600, 0, True),
            metric="",
            candidate=None,
            scores=(),
            version=CROP_SCAN_VERSION,
        )
        self.assertEqual(active_frame_text(result), "720x1080")
        self.assertEqual(downscale_candidate_text(result), "")
        self.assertEqual(downscale_candidate_sort_value(result), (0, 0))
        candidate_tip = downscale_candidate_tooltip(result)
        self.assertEqual(
            candidate_tip,
            "검은 여백만 감지했습니다. 축소 후보를 확인하려면 화질 분석을 실행하세요.",
        )
        self.assertEqual(quality_metric_text(result), "")
        self.assertEqual(quality_metric_sort_value(result), (0, ""))
        self.assertEqual(
            quality_metric_tooltip(result),
            "검은 여백만 감지했습니다. 측정 지표를 보려면 화질 분석을 실행하세요.",
        )
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": result})
        self.assertIsNone(view.candidate_tier("a.mp4"))
        self.assertEqual(view.display("a.mp4", DOWNSCALE_CANDIDATE_COLUMN), "")
        self.assertEqual(view.candidates([make_eval(video=make_video(filename="a.mp4"))]), {})

    def test_failed_crop_only_row_says_detection_failed(self) -> None:
        """스캔에 실패한 감지 전용 행이 검은 띠 없음 대신 감지 실패를 보여 주는지 검증"""
        result = _result(
            active=ActiveRegion(1920, 1080, 0, 0, False),
            metric="",
            candidate=None,
            scores=(),
            unmeasurable=True,
            version=CROP_SCAN_VERSION,
        )
        self.assertEqual(active_frame_text(result), ACTIVE_FRAME_FAILED_TEXT)
        self.assertEqual(
            active_frame_tooltip(result),
            "검은 여백을 감지하지 못했습니다. 영상을 읽지 못한 지점이 있어 전체 화면을 그대로 씁니다. "
            "다시 감지하면 재시도합니다.",
        )
        self.assertNotIn("검은 띠가 없어", active_frame_tooltip(result))
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": result})
        self.assertEqual(view.display("a.mp4", ACTIVE_FRAME_COLUMN), ACTIVE_FRAME_FAILED_TEXT)
        self.assertEqual(view.tooltip("a.mp4", ACTIVE_FRAME_COLUMN), active_frame_tooltip(result))
        self.assertIsNone(view.foreground("a.mp4", ACTIVE_FRAME_COLUMN, _PALETTE))

    def test_failed_crop_only_row_keeps_vr_and_manual_wording(self) -> None:
        """감지 실패 표시가 VR 보호와 사용자 지정 활성 화면 문구를 덮지 않는지 검증"""
        failed = _result(
            active=ActiveRegion(3840, 1920, 0, 0, False),
            metric="",
            candidate=None,
            scores=(),
            unmeasurable=True,
            vr=True,
            version=CROP_SCAN_VERSION,
        )
        self.assertEqual(active_frame_text(failed), ACTIVE_FRAME_VR_TEXT)
        self.assertNotIn("감지하지 못했습니다", active_frame_tooltip(failed))
        manual = _result(
            active=ActiveRegion(1920, 1080, 0, 0, False),
            metric="",
            candidate=None,
            scores=(),
            unmeasurable=True,
            version=CROP_SCAN_VERSION,
        )
        self.assertIn("사용자 지정 활성 화면입니다.", active_frame_tooltip(manual, manual=True))

    def test_full_analysis_unmeasurable_keeps_no_bars_wording(self) -> None:
        """전체 화질 분석의 측정 불가는 감지 실패가 아니므로 활성 화면 문구를 바꾸지 않음"""
        result = _result(unmeasurable=True, candidate=None)
        self.assertEqual(active_frame_text(result), ACTIVE_FRAME_FULL_TEXT)
        self.assertIn("검은 띠가 없어", active_frame_tooltip(result))

    def test_failed_crop_only_metric_tooltip_replaces_detection_notice(self) -> None:
        """감지에 실패한 감지 전용 행의 측정 지표 툴팁이 감지 성공 안내를 쓰지 않는지 검증"""
        result = _result(
            metric="",
            candidate=None,
            scores=(),
            unmeasurable=True,
            version=CROP_SCAN_VERSION,
        )
        tip = quality_metric_tooltip(result)
        self.assertEqual(
            tip, "검은 여백 감지에 실패해 측정 지표를 확인하지 못했습니다. 다시 감지하면 재시도합니다."
        )
        self.assertNotIn("검은 여백만 감지했습니다", tip)
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": result})
        self.assertEqual(view.tooltip("a.mp4", QUALITY_METRIC_COLUMN), tip)

    def test_full_analysis_scan_failure_says_detection_failed(self) -> None:
        """감지에 실패한 전체 화질 분석 행도 감지 실패로 알리고 재측정을 안내하는지 검증"""
        result = _result(unmeasurable=True, candidate=None, scan_failed=True)
        self.assertEqual(active_frame_text(result), ACTIVE_FRAME_FAILED_TEXT)
        self.assertNotIn("검은 띠가 없어", active_frame_tooltip(result))
        self.assertIn("검은 여백을 감지하지 못했습니다", active_frame_tooltip(result))
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": result})
        self.assertEqual(view.display("a.mp4", ACTIVE_FRAME_COLUMN), ACTIVE_FRAME_FAILED_TEXT)
        self.assertEqual(view.tooltip("a.mp4", ACTIVE_FRAME_COLUMN), active_frame_tooltip(result))
        self.assertEqual(
            view.tooltip("a.mp4", QUALITY_METRIC_COLUMN), quality_metric_tooltip(result)
        )

    def test_failed_full_analysis_metric_text_is_blank(self) -> None:
        """감지에 실패한 전체 화질 분석 행의 측정 지표 셀을 비우는지 검증"""
        result = _result(metric="vmaf", unmeasurable=True, candidate=None, scan_failed=True)
        self.assertEqual(quality_metric_text(result), "")
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": result})
        self.assertEqual(view.display("a.mp4", QUALITY_METRIC_COLUMN), "")
        self.assertEqual(view.tooltip("a.mp4", QUALITY_METRIC_COLUMN), quality_metric_tooltip(result))

    def test_failed_crop_only_candidate_tooltip_replaces_detection_notice(self) -> None:
        """감지에 실패한 감지 전용 행의 축소 후보 툴팁이 감지 성공 안내를 쓰지 않는지 검증"""
        result = _result(
            metric="",
            candidate=None,
            scores=(),
            unmeasurable=True,
            version=CROP_SCAN_VERSION,
        )
        tip = downscale_candidate_tooltip(result)
        self.assertEqual(
            tip, "검은 여백 감지에 실패해 축소 후보를 확인하지 못했습니다. 다시 감지하면 재시도합니다."
        )
        self.assertNotIn("검은 여백만 감지했습니다", tip)
        succeeded = _result(
            metric="",
            candidate=None,
            scores=(),
            version=CROP_SCAN_VERSION,
        )
        self.assertEqual(
            downscale_candidate_tooltip(succeeded),
            "검은 여백만 감지했습니다. 축소 후보를 확인하려면 화질 분석을 실행하세요.",
        )


class FrameAnalysisResultsTests(unittest.TestCase):
    """표 모델 보조가 파일명별 결과와 수동 VR 보호를 조회하는지 검증"""

    def test_display_filter_sort_and_tooltip(self) -> None:
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": _result(candidate="720p")})
        view.set_manual_vr(["a.mp4"])
        self.assertTrue(view.has_results)
        self.assertTrue(view.is_manual_vr("a.mp4"))
        self.assertIsNotNone(view.result("a.mp4"))
        self.assertEqual(view.candidate_tier("a.mp4"), "720p")
        self.assertEqual(view.display("a.mp4", ACTIVE_FRAME_COLUMN), ACTIVE_FRAME_FULL_TEXT)
        self.assertEqual(view.filter_value("a.mp4", DOWNSCALE_CANDIDATE_COLUMN), "720p")
        self.assertEqual(view.sort_value("a.mp4", QUALITY_METRIC_COLUMN), (2, "vmaf"))
        self.assertIn("유효 구간", view.tooltip("a.mp4", DOWNSCALE_CANDIDATE_COLUMN))
        self.assertEqual(len(view.candidates([make_eval(video=make_video(filename="a.mp4"))])), 1)
        self.assertEqual(view.display("missing.mp4", ACTIVE_FRAME_COLUMN), "")

    def test_candidates_skip_missing_and_unmeasurable_rows(self) -> None:
        view = FrameAnalysisResults()
        view.set_results(
            {
                "a.mp4": _result(candidate="720p"),
                "b.mp4": _result(candidate=None),
                "c.mp4": _result(unmeasurable=True, candidate="720p"),
            }
        )
        evals = [make_eval(video=make_video(filename=name)) for name in ("a.mp4", "b.mp4", "c.mp4", "d.mp4")]
        self.assertEqual(view.candidates(evals), {"a.mp4": "720p"})

    def test_frame_analysis_column_set(self) -> None:
        self.assertTrue(is_frame_analysis_column(ACTIVE_FRAME_COLUMN))
        self.assertTrue(is_frame_analysis_column(DOWNSCALE_CANDIDATE_COLUMN))
        self.assertTrue(is_frame_analysis_column(QUALITY_METRIC_COLUMN))
        self.assertFalse(is_frame_analysis_column(18))

    def test_crop_excluded_active_frame_is_muted_with_reason_tooltip(self) -> None:
        """잘라내기 제외 영상은 같은 크기 문구에 흐린 색과 제외 사유 툴팁만 붙임"""
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": _result(active=ActiveRegion(1600, 1000, 260, 40, True))})
        view.set_crop_excluded(["a.mp4"])
        self.assertEqual(view.display("a.mp4", ACTIVE_FRAME_COLUMN), "1600x1000")
        self.assertEqual(
            view.tooltip("a.mp4", ACTIVE_FRAME_COLUMN),
            "검은 여백 잘라내기 제외됨 (원본 유지)",
        )
        self.assertIsNotNone(view.foreground("a.mp4", ACTIVE_FRAME_COLUMN, _PALETTE))
        view.set_crop_excluded([])
        self.assertIsNone(view.foreground("a.mp4", ACTIVE_FRAME_COLUMN, _PALETTE))
        self.assertNotEqual(
            view.tooltip("a.mp4", ACTIVE_FRAME_COLUMN),
            "검은 여백 잘라내기 제외됨 (원본 유지)",
        )

    def test_manual_active_frame_is_shown_with_extra_tooltip_sentence(self) -> None:
        """사용자 지정 활성 화면은 지정 크기를 보여 주고 툴팁에 문장 한 줄을 덧붙임"""
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": _result(active=ActiveRegion(1600, 1000, 260, 40, True))})
        manual = ActiveFrame(1280, 720, 0, 180)
        view.set_manual_frames({"a.mp4": manual})
        self.assertEqual(view.display("a.mp4", ACTIVE_FRAME_COLUMN), "1280x720")
        self.assertEqual(view.filter_value("a.mp4", ACTIVE_FRAME_COLUMN), "1280x720")
        self.assertEqual(view.sort_value("a.mp4", ACTIVE_FRAME_COLUMN), (1, 1280 * 720))
        self.assertTrue(
            view.tooltip("a.mp4", ACTIVE_FRAME_COLUMN).endswith("사용자 지정 활성 화면입니다.")
        )
        view.set_crop_excluded(["a.mp4"])
        self.assertIsNone(view.foreground("a.mp4", ACTIVE_FRAME_COLUMN, _PALETTE))
        view.set_manual_frames({})
        self.assertEqual(view.display("a.mp4", ACTIVE_FRAME_COLUMN), "1600x1000")

    def test_manual_active_frame_without_result_still_shows_size_and_tooltip(self) -> None:
        """분석 결과가 없어도 직접 지정 크기와 안내를 보여 줌"""
        view = FrameAnalysisResults()
        view.set_manual_frames({"a.mp4": ActiveFrame(1280, 720, 0, 180)})
        self.assertEqual(view.display("a.mp4", ACTIVE_FRAME_COLUMN), "1280x720")
        self.assertIn("사용자 지정 활성 화면입니다.", view.tooltip("a.mp4", ACTIVE_FRAME_COLUMN))

    def test_manual_active_frame_yields_to_vr_protection_text(self) -> None:
        """VR 보호 영상은 직접 지정이 있어도 VR 보호 문구와 툴팁을 유지함"""
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": _result(vr=True)})
        view.set_manual_frames({"a.mp4": ActiveFrame(1280, 720, 0, 180)})
        self.assertEqual(view.display("a.mp4", ACTIVE_FRAME_COLUMN), ACTIVE_FRAME_VR_TEXT)
        self.assertNotIn("사용자 지정 활성 화면입니다.", view.tooltip("a.mp4", ACTIVE_FRAME_COLUMN))

    def test_crop_off_setting_mutes_black_bar_rows_but_keeps_manual_frames(self) -> None:
        """검은 여백 잘라내기가 꺼져 있으면 검은 띠가 있는 활성 화면 셀을 흐리게 표시하는지 검증"""
        view = FrameAnalysisResults()
        view.set_results(
            {
                "a.mp4": _result(active=ActiveRegion(1600, 1000, 260, 40, True)),
                "b.mp4": _result(),
            }
        )
        ev_a = make_eval(video=make_video(filename="a.mp4"))
        ev_b = make_eval(video=make_video(filename="b.mp4"))
        self.assertIsNone(view.foreground("a.mp4", ACTIVE_FRAME_COLUMN, _PALETTE))
        self.assertEqual(
            view.foreground("a.mp4", ACTIVE_FRAME_COLUMN, _PALETTE, crop_enabled=False),
            _PALETTE.text_muted,
        )
        self.assertEqual(
            foreground_color(ev_a, ACTIVE_FRAME_COLUMN, _PALETTE, frame_view=view,
                             encode_label=None, encode_state_col=99, crop_enabled=False),
            _PALETTE.text_muted,
        )
        self.assertIsNone(
            foreground_color(ev_a, ACTIVE_FRAME_COLUMN, _PALETTE, frame_view=view,
                             encode_label=None, encode_state_col=99)
        )
        self.assertIsNone(
            foreground_color(ev_b, ACTIVE_FRAME_COLUMN, _PALETTE, frame_view=view,
                             encode_label=None, encode_state_col=99, crop_enabled=False),
            "검은 띠가 없는 행은 흐리게 표시하지 않음",
        )
        view.set_manual_frames({"a.mp4": ActiveFrame(1600, 1000, 260, 40)})
        self.assertIsNone(view.foreground("a.mp4", ACTIVE_FRAME_COLUMN, _PALETTE, crop_enabled=False))

    def test_foreground_color_delegates_frame_and_output_columns(self) -> None:
        """셀 글자색 계산은 화질 분석 열과 열 정의, 인코딩 상태를 같은 규칙으로 처리"""
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": _result(active=ActiveRegion(1600, 1000, 260, 40, True))})
        view.set_crop_excluded(["a.mp4"])
        ev = make_eval(video=make_video(filename="a.mp4"))
        self.assertEqual(
            foreground_color(ev, ACTIVE_FRAME_COLUMN, _PALETTE, frame_view=view,
                             encode_label=None, encode_state_col=99),
            _PALETTE.text_muted,
        )
        self.assertIsNone(
            foreground_color(ev, DOWNSCALE_CANDIDATE_COLUMN, _PALETTE, frame_view=view,
                             encode_label=None, encode_state_col=99)
        )
        self.assertIsNone(
            foreground_color(ev, 0, _PALETTE, frame_view=view, encode_label=None, encode_state_col=99)
        )


class CropReviewMarkerTests(unittest.TestCase):
    """여백 확인 필요 표시의 툴팁과 필터 값을 검증"""

    def _flagged(self) -> FrameAnalysisResult:
        return _result(active=ActiveRegion(1836, 1008, 42, 36, True, dark_outliers_ignored=True))

    def test_tooltip_has_title_reason_hold_and_solve(self) -> None:
        tip = crop_review_tooltip(self._flagged().active_region)
        lines = tip.split("\n")
        self.assertEqual(lines[0], CROP_REVIEW_TEXT)
        self.assertIn("검은 여백을 찾을 때 일부 지점의 여백이 어두워 판단에서 제외했습니다.", lines[1])
        self.assertIn("활성 화면(1836x1008)", lines[1])
        self.assertEqual(
            lines[2],
            "활성 화면을 직접 지정할 때까지 자동으로 잡은 검은 여백을 잘라내지 않고 전체 화면으로 인코딩합니다.",
        )
        self.assertEqual(
            lines[3],
            "미리 보기 창에서 활성 화면을 확인해 주세요. 직접 지정하거나 자동 감지로 되돌리면 이 경고가 사라집니다.",
        )

    def test_tooltip_keeps_the_margins_vary_sentence_after_the_first_paragraph(self) -> None:
        varying = _result(
            active=ActiveRegion(
                1836, 1008, 42, 36, True, margins_vary=True, dark_outliers_ignored=True
            )
        )
        lines = crop_review_tooltip(varying.active_region).split("\n")
        self.assertEqual(lines[2], _MARGINS_VARY_SENTENCE)
        self.assertEqual(len(lines), 5)

    def test_filter_values_keep_the_size_and_add_the_review_name(self) -> None:
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": self._flagged(), "b.mp4": _result()})
        view.set_review_flagged(["a.mp4"])
        self.assertTrue(view.is_review_flagged("a.mp4"))
        self.assertEqual(view.review_flagged_count, 1)
        self.assertEqual(view.filter_value("a.mp4", ACTIVE_FRAME_COLUMN), "1836x1008")
        self.assertEqual(
            view.filter_values("a.mp4", ACTIVE_FRAME_COLUMN), ("1836x1008", CROP_REVIEW_TEXT)
        )
        self.assertEqual(view.filter_values("b.mp4", ACTIVE_FRAME_COLUMN), ("-",))
        self.assertEqual(view.filter_values("a.mp4", QUALITY_METRIC_COLUMN), ("VMAF",))

    def test_flag_does_not_change_cells_of_unflagged_or_reviewed_rows(self) -> None:
        view = FrameAnalysisResults()
        view.set_results({"a.mp4": self._flagged()})
        self.assertEqual(view.display("a.mp4", ACTIVE_FRAME_COLUMN), "1836x1008")
        view.set_review_flagged([])
        self.assertEqual(view.filter_values("a.mp4", ACTIVE_FRAME_COLUMN), ("1836x1008",))
        view.set_review_flagged(["a.mp4"])
        view.set_manual_frames({"a.mp4": ActiveFrame(1600, 1000, 260, 40)})
        self.assertEqual(view.display("a.mp4", ACTIVE_FRAME_COLUMN), "1600x1000")


class OutputResolutionDisplayTests(unittest.TestCase):
    """출력 해상도 열 표기 정규화: 실제 최종 픽셀, 여백 단계 툴팁, 그대로면 `-` 검증"""

    def _cropped(self) -> VideoEvaluation:
        return make_eval(
            video=make_video(filename="a.mp4", width=1920, height=1080),
            active_frame=ActiveFrame(1600, 1000, 260, 40),
        )

    def test_crop_only_shows_active_pixels_and_sorts_by_area(self) -> None:
        """축소 없이 여백만 잘라낸 영상은 활성 화면 픽셀과 그 면적으로 표시, 정렬"""
        ev = self._cropped()
        self.assertEqual(format_output_res_display(ev, ClassifierConfig()), "1600x1000")
        self.assertEqual(output_res_sort_value(ev), 1600 * 1000)

    def test_unchanged_output_shows_dash(self) -> None:
        """여백도 축소도 적용되지 않으면 `-`로 표시"""
        ev = make_eval(video=make_video(filename="a.mp4"))
        self.assertEqual(format_output_res_display(ev, ClassifierConfig()), "-")
        self.assertEqual(output_res_sort_value(ev), 0)

    def test_tooltip_lists_crop_step(self) -> None:
        """툴팁은 원본에서 활성 화면으로 바뀐 크기를 함께 보여 줌"""
        tip = format_output_res_tooltip(self._cropped(), ClassifierConfig())
        self.assertIn("검은 여백 잘라내기: 1920x1080 -> 1600x1000", tip)
        self.assertIn("해상도는 줄이지 않습니다.", tip)


if __name__ == "__main__":
    unittest.main()

