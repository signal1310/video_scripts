"""화질 분석 표 열 세 개의 셀 문구, 정렬 값, 필터, 툴팁 검증"""

import unittest

from src.core.analysis.frame_analysis import CROP_SCAN_VERSION, ActiveRegion, TierScore
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.config import ClassifierConfig
from src.core.models import ActiveFrame, VideoEvaluation
from src.gui.styles.palettes import DARK_PALETTE as _PALETTE
from src.gui.table.frame_analysis_columns import (
    ACTIVE_FRAME_COLUMN,
    ACTIVE_FRAME_TOTAL_TEXT,
    ACTIVE_FRAME_VR_TEXT,
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
    contiguous_row_ranges,
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


def _result(
    *,
    active: ActiveRegion | None = None,
    metric: str = "vmaf",
    candidate: str | None = "1080p",
    unmeasurable: bool = False,
    vr: bool = False,
    scores: tuple[TierScore, ...] | None = None,
    version: int = 202,
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
        analysis_version=version,
    )


class FrameAnalysisTextTests(unittest.TestCase):
    """활성 화면, 축소 후보, 측정 지표의 표시 문구 검증"""

    def test_active_frame_values(self) -> None:
        self.assertEqual(active_frame_text(None), "")
        self.assertEqual(active_frame_text(_result()), ACTIVE_FRAME_TOTAL_TEXT)
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
        self.assertEqual(result_summary_text(result), "전체, 720p, VMAF")

    def test_contiguous_rows_are_grouped_into_ranges(self) -> None:
        """행 번호가 연속 구간으로 묶이고 빈 목록은 빈 결과인지 검증"""
        self.assertEqual(contiguous_row_ranges([]), [])
        self.assertEqual(contiguous_row_ranges([4]), [(4, 4)])
        self.assertEqual(contiguous_row_ranges([7, 1, 2, 3, 9]), [(1, 3), (7, 7), (9, 9)])

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
        self.assertEqual(view.display("a.mp4", ACTIVE_FRAME_COLUMN), ACTIVE_FRAME_TOTAL_TEXT)
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

    def test_foreground_color_delegates_frame_and_output_columns(self) -> None:
        """셀 글자색 계산은 화질 분석 열과 열 정의, 재인코딩 상태를 같은 규칙으로 처리"""
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

