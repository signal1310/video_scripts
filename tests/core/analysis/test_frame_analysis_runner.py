"""화질 분석 실행 흐름(무손실 가속 측정, 취소, 측정 불가)과 프로세스 실행기 검증"""

import sys
import tempfile
import threading
import unittest

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.analysis.frame_analysis_runner import (
    FrameAnalysisResult,
    ProcessStderrRunner,
    analyze_frames,
)
from src.core.quality_probe import VMAF_MODEL_4K, VMAF_MODEL_DEFAULT
from tests._frame_analysis_fakes import FakeStderrRunner

_DURATION_SEC: float = 20.0
_SEGMENT_COUNT: int = 9


class _RunnerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work_dir = tmp.name

    def analyze(
        self,
        runner: FakeStderrRunner,
        *,
        width: int = 3840,
        height: int = 2160,
        has_vmaf: bool = True,
        cancel_event: threading.Event | None = None,
        has_vr_metadata: bool = False,
        manual_vr: bool = False,
    ) -> FrameAnalysisResult:
        return analyze_frames(
            source_path="movie.mp4",
            width=width,
            height=height,
            duration_sec=_DURATION_SEC,
            work_dir=self.work_dir,
            runner=runner,
            ffmpeg_executable="ffmpeg",
            has_vmaf=has_vmaf,
            has_vr_metadata=has_vr_metadata,
            manual_vr=manual_vr,
            cancel_event=cancel_event,
        )


class TestAnalyzeFrames(_RunnerTestCase):
    """구간 선택부터 축소 후보 판정까지의 흐름 검증"""

    def test_all_tiers_pass_returns_lowest_candidate(self) -> None:
        runner = FakeStderrRunner()
        result = self.analyze(runner)
        self.assertFalse(result.unmeasurable)
        self.assertEqual(result.candidate_tier, "360p")
        self.assertEqual(result.metric_used, "vmaf")
        self.assertEqual(result.vmaf_model, VMAF_MODEL_4K)
        self.assertEqual(len(result.tier_scores), 7)
        self.assertTrue(all(score.vmaf == 99.0 for score in result.tier_scores))
        self.assertEqual(result.valid_segment_count, _SEGMENT_COUNT)
        self.assertEqual(result.total_segment_count, _SEGMENT_COUNT)
        self.assertEqual(result.analysis_version, 1)

    def test_ssim_failure_skips_vmaf_and_stops(self) -> None:
        runner = FakeStderrRunner(ssim_by_tier={"1440p": (0.98, 18.0)})
        result = self.analyze(runner)
        self.assertEqual(runner.count_commands("libvmaf"), 0)
        self.assertEqual(len(result.tier_scores), 1)
        self.assertIsNone(result.candidate_tier)
        self.assertFalse(result.unmeasurable)

    def test_early_stop_when_lower_tier_fails(self) -> None:
        runner = FakeStderrRunner(ssim_by_tier={"1440p": (0.9995, 30.0), "1080p": (0.99, 20.0)})
        result = self.analyze(runner)
        self.assertEqual([score.tier for score in result.tier_scores], ["1440p", "1080p"])
        self.assertEqual(result.candidate_tier, "1440p")
        self.assertEqual(set(runner.tiers_measured("ssim")), {"1440p", "1080p", "360p"})

    def test_vmaf_below_threshold_stops(self) -> None:
        runner = FakeStderrRunner(vmaf_by_tier={"1440p": 97.0})
        result = self.analyze(runner)
        self.assertEqual([score.tier for score in result.tier_scores], ["1440p"])
        self.assertEqual(result.tier_scores[0].vmaf, 97.0)
        self.assertIsNone(result.candidate_tier)

    def test_vmaf_absent_uses_ssim_only(self) -> None:
        runner = FakeStderrRunner()
        result = self.analyze(runner, has_vmaf=False)
        self.assertEqual(result.metric_used, "ssim")
        self.assertEqual(runner.count_commands("libvmaf"), 0)
        self.assertEqual(result.candidate_tier, "360p")
        self.assertTrue(all(score.vmaf is None for score in result.tier_scores))

    def test_black_segments_are_unmeasurable(self) -> None:
        runner = FakeStderrRunner(black_ratio=0.6)
        result = self.analyze(runner)
        self.assertTrue(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)
        self.assertEqual(result.total_segment_count, _SEGMENT_COUNT)
        self.assertEqual(runner.count_commands("-filter_complex"), 0)

    def test_blackdetect_failure_treats_segment_as_black(self) -> None:
        runner = FakeStderrRunner(black_returncode=1)
        self.assertTrue(self.analyze(runner).unmeasurable)

    def test_flat_segments_are_unmeasurable(self) -> None:
        runner = FakeStderrRunner(ssim_by_tier={"360p": (0.9995, 45.0)})
        result = self.analyze(runner)
        self.assertTrue(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)
        self.assertEqual(set(runner.tiers_measured("ssim")), {"360p"})

    def test_metric_failure_is_unmeasurable(self) -> None:
        runner = FakeStderrRunner(fail_metric=True)
        self.assertTrue(self.analyze(runner).unmeasurable)

    def test_no_candidate_tier_for_small_frame(self) -> None:
        runner = FakeStderrRunner()
        result = self.analyze(runner, width=320, height=240)
        self.assertFalse(result.unmeasurable)
        self.assertIsNone(result.candidate_tier)
        self.assertEqual(result.tier_scores, ())
        self.assertEqual(runner.count_commands("-filter_complex"), 0)

    def test_black_bars_detected_as_active_region(self) -> None:
        runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        result = self.analyze(runner, width=1920, height=1080)
        self.assertEqual(result.active_region, ActiveRegion(720, 1080, 600, 0, True))
        self.assertFalse(result.is_vr_protected)

    def test_empty_crop_result_keeps_full_frame(self) -> None:
        runner = FakeStderrRunner()
        result = self.analyze(runner, width=1920, height=1080)
        self.assertEqual(result.active_region, ActiveRegion(1920, 1080, 0, 0, False))
        self.assertEqual(result.vmaf_model, VMAF_MODEL_DEFAULT)

    def test_vr_ratio_skips_cropdetect(self) -> None:
        runner = FakeStderrRunner()
        result = self.analyze(runner, width=3840, height=1920)
        self.assertTrue(result.is_vr_protected)
        self.assertEqual(runner.count_commands("cropdetect"), 0)
        self.assertEqual(result.active_region, ActiveRegion(3840, 1920, 0, 0, False))

    def test_vr_metadata_skips_cropdetect(self) -> None:
        runner = FakeStderrRunner()
        result = self.analyze(runner, has_vr_metadata=True)
        self.assertTrue(result.is_vr_protected)
        self.assertEqual(runner.count_commands("cropdetect"), 0)

    def test_manual_vr_flag_is_protected(self) -> None:
        self.assertTrue(self.analyze(FakeStderrRunner(), manual_vr=True).is_vr_protected)

    def test_cancel_before_start_raises(self) -> None:
        runner = FakeStderrRunner()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            self.analyze(runner, cancel_event=cancel)
        self.assertEqual(runner.commands, [])

    def test_cancel_during_metric_raises(self) -> None:
        cancel = threading.Event()

        def _set_on_metric(args: list[str]) -> None:
            if "-filter_complex" in args:
                cancel.set()

        runner = FakeStderrRunner(on_call=_set_on_metric)
        with self.assertRaises(InterruptedError):
            self.analyze(runner, cancel_event=cancel)
        self.assertEqual(runner.count_commands("-filter_complex"), 1)


class TestProcessStderrRunner(_RunnerTestCase):
    """기본 실행기의 표준 오류 수집, 취소, 제한 시간 검증"""

    def test_captures_stderr_and_return_code(self) -> None:
        runner = ProcessStderrRunner()
        returncode, stderr = runner(
            [sys.executable, "-c", "import sys; sys.stderr.write('hello'); sys.exit(3)"],
            self.work_dir,
        )
        self.assertEqual(returncode, 3)
        self.assertEqual(stderr.strip(), "hello")

    def test_cancel_event_raises_interrupted_error(self) -> None:
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            ProcessStderrRunner(cancel_event=cancel)([sys.executable, "-c", "pass"], self.work_dir)

    def test_timeout_raises_timeout_error(self) -> None:
        runner = ProcessStderrRunner(timeout_sec=0.1)
        with self.assertRaises(TimeoutError):
            runner([sys.executable, "-c", "import time; time.sleep(5)"], self.work_dir)


if __name__ == "__main__":
    unittest.main()
