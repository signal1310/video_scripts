"""화질 분석 실행 흐름(고속 측정, 적응형 조기 확정, 취소, 측정 불가)과 프로세스 실행기 검증"""

import sys
import tempfile
import threading
import unittest
from collections.abc import Callable

from src.core.analysis.frame_analysis import (
    CROP_SCAN_VERSION,
    METRIC_CROP_ONLY,
    ActiveRegion,
    candidate_tiers_for,
)
from src.core.analysis.frame_analysis_runner import (
    STAGE_SCAN,
    STAGE_SSIM,
    STAGE_VMAF,
    FrameAnalysisResult,
    ProcessStderrRunner,
    analyze_frames,
)
from src.core.quality_probe import VMAF_MODEL_4K, VMAF_MODEL_DEFAULT
from tests._frame_analysis_fakes import FakeStderrRunner

_DURATION_SEC: float = 20.0
_SEGMENT_COUNT: int = 9


def _tier_scores(value: tuple[float, float]) -> dict[str, tuple[float, float]]:
    """모든 축소 규격에 같은 SSIM 점수와 dB를 주는 사전"""
    return {tier.key: value for tier in candidate_tiers_for(3840, 2160)}


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
        mode: str = "balanced",
        inner_workers: int = 1,
        on_stage: Callable[[str, int, int], None] | None = None,
        crop_only: bool = False,
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
            mode=mode,
            cancel_event=cancel_event,
            inner_workers=inner_workers,
            on_stage=on_stage,
            crop_only=crop_only,
        )


class TestAnalyzeFrames(_RunnerTestCase):
    """스캔, SSIM 적응형 라운드, VMAF 확정 흐름 검증"""

    def test_all_tiers_pass_returns_lowest_candidate(self) -> None:
        runner = FakeStderrRunner()
        result = self.analyze(runner)
        self.assertFalse(result.unmeasurable)
        self.assertEqual(result.candidate_tier, "360p")
        self.assertEqual(result.metric_used, "vmaf")
        self.assertEqual(result.vmaf_model, VMAF_MODEL_4K)
        self.assertEqual(len(result.tier_scores), 7)
        self.assertEqual(result.tier_scores[-1].tier, "360p")
        self.assertEqual(result.tier_scores[-1].vmaf, 99.0)
        self.assertEqual(result.valid_segment_count, 1)
        self.assertEqual(result.total_segment_count, _SEGMENT_COUNT)
        self.assertEqual(result.analysis_version, 212)
        self.assertEqual(runner.count_commands("blackframe"), _SEGMENT_COUNT)

    def test_fast_mode_uses_half_second_segments_and_fast_seek(self) -> None:
        runner = FakeStderrRunner()
        result = self.analyze(runner, mode="fast")
        self.assertEqual(result.analysis_version, 211)
        self.assertIn("-noaccurate_seek", runner.commands[0])
        metric = next(args for args in runner.commands if "-filter_complex" in args)
        self.assertEqual(metric[metric.index("-t") + 1], "0.500")

    def test_precise_mode_uses_two_second_segments_and_accurate_seek(self) -> None:
        runner = FakeStderrRunner()
        result = self.analyze(runner, mode="precise")
        self.assertEqual(result.analysis_version, 213)
        self.assertNotIn("-noaccurate_seek", runner.commands[0])
        metric = next(args for args in runner.commands if "-filter_complex" in args)
        self.assertEqual(metric[metric.index("-t") + 1], "2.000")

    def test_ssim_failure_skips_vmaf(self) -> None:
        runner = FakeStderrRunner(ssim_by_tier={"1440p": (0.98, 18.0)})
        result = self.analyze(runner)
        self.assertEqual(runner.count_commands("libvmaf"), 0)
        self.assertEqual(len(result.tier_scores), 7)
        self.assertIsNone(result.candidate_tier)
        self.assertFalse(result.unmeasurable)
        self.assertLess(result.tier_scores[0].ssim or 1.0, 0.996)

    def test_unconfirmed_tiers_receive_more_rounds(self) -> None:
        runner = FakeStderrRunner(
            ssim_by_tier={"1440p": (0.9995, 30.0), "1080p": (0.997, 20.0), "900p": (0.99, 18.0)}
        )
        result = self.analyze(runner, has_vmaf=False)
        self.assertEqual(result.candidate_tier, "1080p")
        measured = runner.tiers_measured("ssim")
        self.assertGreaterEqual(measured.count("1080p"), 4)
        self.assertGreaterEqual(measured.count("900p"), 4)
        self.assertEqual(measured.count("720p"), 1)

    def test_vmaf_tier_falls_back_up_one_tier(self) -> None:
        runner = FakeStderrRunner(vmaf_by_tier={"360p": 97.0})
        result = self.analyze(runner)
        self.assertEqual(result.candidate_tier, "480p")
        self.assertEqual({score.tier: score.vmaf for score in result.tier_scores}["360p"], 97.0)

    def test_vmaf_early_fail_stops_after_one_point(self) -> None:
        runner = FakeStderrRunner(vmaf_by_tier={"360p": 96.0, "480p": 99.6})
        result = self.analyze(runner)
        self.assertEqual(result.candidate_tier, "480p")
        self.assertEqual(runner.tiers_measured("vmaf"), ["360p", "480p"])

    def test_vmaf_early_pass_stops_after_one_point(self) -> None:
        runner = FakeStderrRunner(vmaf_by_tier={"360p": 99.6})
        result = self.analyze(runner)
        self.assertEqual(result.candidate_tier, "360p")
        self.assertEqual(runner.tiers_measured("vmaf"), ["360p"])

    def test_vmaf_absent_uses_ssim_only(self) -> None:
        runner = FakeStderrRunner()
        result = self.analyze(runner, has_vmaf=False)
        self.assertEqual(result.metric_used, "ssim")
        self.assertEqual(runner.count_commands("libvmaf"), 0)
        self.assertEqual(result.candidate_tier, "360p")
        self.assertTrue(all(score.vmaf is None for score in result.tier_scores))

    def test_flat_segments_are_unmeasurable(self) -> None:
        runner = FakeStderrRunner(ssim_by_tier=_tier_scores((0.9995, 45.0)))
        result = self.analyze(runner)
        self.assertTrue(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)

    def test_black_segments_are_unmeasurable(self) -> None:
        runner = FakeStderrRunner(black_ratio=0.6)
        result = self.analyze(runner)
        self.assertTrue(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)
        self.assertEqual(runner.count_commands("-filter_complex"), 0)

    def test_scan_failure_treats_segment_as_black(self) -> None:
        result = self.analyze(FakeStderrRunner(black_returncode=1))
        self.assertTrue(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)

    def test_metric_failure_is_unmeasurable(self) -> None:
        result = self.analyze(FakeStderrRunner(fail_metric=True))
        self.assertTrue(result.unmeasurable)

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

    def test_crop_only_scan_runs_no_metric_commands_and_keeps_only_crop_fields(self) -> None:
        """검은 여백 감지 전용 실행이 1단계 스캔만 하고 축소 후보와 지표를 비우는지 검증"""
        runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        stages: list[str] = []
        result = self.analyze(
            runner,
            width=1920,
            height=1080,
            on_stage=lambda stage, _done, _total: stages.append(stage),
            crop_only=True,
        )
        self.assertEqual(runner.count_commands("blackframe"), _SEGMENT_COUNT)
        self.assertEqual(runner.count_commands("-filter_complex"), 0)
        self.assertEqual(stages, [STAGE_SCAN] * _SEGMENT_COUNT)
        self.assertEqual(result.active_region, ActiveRegion(720, 1080, 600, 0, True))
        self.assertEqual(result.metric_used, METRIC_CROP_ONLY)
        self.assertEqual(result.vmaf_model, "")
        self.assertEqual(result.tier_scores, ())
        self.assertIsNone(result.candidate_tier)
        self.assertEqual(result.valid_segment_count, _SEGMENT_COUNT)
        self.assertEqual(result.total_segment_count, _SEGMENT_COUNT)
        self.assertFalse(result.unmeasurable)
        self.assertEqual(result.analysis_version, CROP_SCAN_VERSION)

    def test_crop_only_keeps_vr_protection(self) -> None:
        """감지 전용 실행도 VR 보호 결과와 전체 프레임을 유지하는지 검증"""
        result = self.analyze(
            FakeStderrRunner(boxes=[(720, 1080, 600, 0)]),
            width=3840,
            height=1920,
            crop_only=True,
        )
        self.assertTrue(result.is_vr_protected)
        self.assertEqual(result.active_region, ActiveRegion(3840, 1920, 0, 0, False))

    def test_vr_forces_full_frame_even_with_boxes(self) -> None:
        runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        result = self.analyze(runner, width=3840, height=1920)
        self.assertTrue(result.is_vr_protected)
        self.assertEqual(result.active_region, ActiveRegion(3840, 1920, 0, 0, False))
        self.assertEqual(result.vmaf_model, VMAF_MODEL_4K)

    def test_manual_vr_flag_is_protected(self) -> None:
        result = self.analyze(FakeStderrRunner(), manual_vr=True, width=1920, height=1080)
        self.assertTrue(result.is_vr_protected)
        self.assertEqual(result.vmaf_model, VMAF_MODEL_DEFAULT)

    def test_stage_callback_reports_scan_ssim_and_vmaf(self) -> None:
        stages: list[str] = []
        self.analyze(
            FakeStderrRunner(vmaf_by_tier={"360p": 99.6}),
            on_stage=lambda stage, _done, _total: stages.append(stage),
        )
        self.assertEqual(stages[0], STAGE_SCAN)
        self.assertIn(STAGE_SSIM, stages)
        self.assertIn(STAGE_VMAF, stages)

    def test_inner_workers_parallel_path_returns_same_result(self) -> None:
        result = self.analyze(FakeStderrRunner(vmaf_by_tier={"360p": 99.6}), inner_workers=2)
        self.assertEqual(result.candidate_tier, "360p")

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
        self.assertGreaterEqual(runner.count_commands("-filter_complex"), 1)


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

