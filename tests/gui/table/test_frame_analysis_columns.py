"""화질 분석 표 열 세 개의 셀 문구, 정렬 값, 필터, 툴팁 검증"""

import unittest

from src.core.analysis.frame_analysis import ActiveRegion, TierScore
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
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
    downscale_candidate_sort_value,
    downscale_candidate_text,
    downscale_candidate_tooltip,
    is_frame_analysis_column,
    quality_metric_sort_value,
    quality_metric_text,
    quality_metric_tooltip,
    tier_display,
)
from tests._factories import make_eval, make_video


def _result(
    *,
    active: ActiveRegion | None = None,
    metric: str = "vmaf",
    candidate: str | None = "1080p",
    unmeasurable: bool = False,
    vr: bool = False,
    scores: tuple[TierScore, ...] | None = None,
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


if __name__ == "__main__":
    unittest.main()

