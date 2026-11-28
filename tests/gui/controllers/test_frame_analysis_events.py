"""화질 분석 이벤트 값과 완료 상태 문구 검증"""

import unittest

from src.core.analysis.frame_analysis import CROP_SCAN_VERSION, ActiveRegion
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.gui.controllers.frame_analysis_events import (
    FrameAnalysisFinished,
    FrameAnalysisProgress,
    FrameAnalysisStageProgress,
    completion_status_text,
)
from src.gui.frame_analysis_skip import SkipKind


def _result() -> FrameAnalysisResult:
    return FrameAnalysisResult(
        active_region=ActiveRegion(1920, 1080, 0, 0, False),
        metric_used="vmaf",
        vmaf_model="vmaf_v0.6.1",
        tier_scores=(),
        candidate_tier="720p",
        valid_segment_count=5,
        total_segment_count=5,
        unmeasurable=False,
        is_vr_protected=False,
    )


class EventValueTests(unittest.TestCase):
    """워커가 넘기는 진행과 종료 값이 넘긴 필드를 그대로 담는지 검증"""

    def test_progress_values_keep_their_fields(self) -> None:
        result = _result()
        progress = FrameAnalysisProgress(1, 3, "a.mp4", result)
        stage = FrameAnalysisStageProgress(1, 3, "a.mp4", "ssim", 2, 9)
        self.assertEqual((progress.completed, progress.total, progress.filename), (1, 3, "a.mp4"))
        self.assertIs(progress.result, result)
        self.assertEqual((stage.stage, stage.stage_done, stage.stage_total), ("ssim", 2, 9))

    def test_finished_defaults_describe_a_quiet_success(self) -> None:
        finished = FrameAnalysisFinished({})
        self.assertFalse(finished.cancelled)
        self.assertEqual(finished.error, "")
        self.assertEqual(finished.total, 0)
        self.assertEqual(finished.cached_count, 0)
        self.assertFalse(finished.crop_only)


class CompletionTextTests(unittest.TestCase):
    """완료 상태 줄이 캐시 재사용, 감지 실패, 건너뜀 수를 승인 문구로 알리는지 검증"""

    def test_cache_reuse_and_detection_failures_are_noted(self) -> None:
        reused = FrameAnalysisFinished({"a.mp4": _result()}, total=1, cached_count=1)
        self.assertEqual(completion_status_text(reused), "화질 분석 완료: 1개 (캐시 재사용 1개)")
        self.assertEqual(completion_status_text(FrameAnalysisFinished({})), "")

    def test_failed_crop_scans_use_the_crop_label(self) -> None:
        failed = FrameAnalysisResult(
            active_region=ActiveRegion(1920, 1080, 0, 0, False),
            metric_used="",
            vmaf_model="",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=0,
            total_segment_count=9,
            unmeasurable=True,
            is_vr_protected=False,
            analysis_version=CROP_SCAN_VERSION,
        )
        finished = FrameAnalysisFinished({"a.mp4": failed}, total=2)
        self.assertEqual(
            completion_status_text(finished, label="여백 감지"),
            "여백 감지 완료: 2개 (감지 실패 1개)",
        )

    def test_skip_counts_are_listed_with_reasons(self) -> None:
        finished = FrameAnalysisFinished({"a.mp4": _result()}, total=4)
        self.assertEqual(
            completion_status_text(finished, skip_counts={SkipKind.HDR: 1, SkipKind.EXCEPTION: 2}),
            "화질 분석 완료: 4개 (건너뜀 3개(HDR 1, 예외 규칙 2))",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
