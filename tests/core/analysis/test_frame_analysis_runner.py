"""화질 분석 실행 흐름(고속 측정, 적응형 조기 확정, 취소, 측정 불가)과 프로세스 실행기 검증"""

import math
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
from src.core.quality_probe import VMAF_MODEL_4K, VMAF_MODEL_DEFAULT, ssim_db_from_score
from tests._frame_analysis_fakes import FakeStderrRunner

_DURATION_SEC: float = 20.0
_SEGMENT_COUNT: int = 9


def _tier_scores_text(text: str) -> dict[str, str]:
    """모든 축소 규격에 같은 SSIM stats 로그 전문을 주는 사전"""
    return {tier.key: text for tier in candidate_tiers_for(3840, 2160)}


def _static_mixed_log(finite_score: float, finite_lines: int, static_lines: int) -> str:
    """같은 화면(inf) 줄과 유한 점수 줄을 섞은 SSIM stats 로그"""
    finite_db = -10.0 * math.log10(1.0 - finite_score)
    lines = [f"n:{index} Y:1 U:1 V:1 All:1.000000 (inf)\n" for index in range(static_lines)]
    lines.extend(
        f"n:{index} Y:1 U:1 V:1 All:{finite_score:.6f} ({finite_db:.2f})\n"
        for index in range(static_lines, static_lines + finite_lines)
    )
    return "".join(lines)


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
        duration_sec: float = _DURATION_SEC,
    ) -> FrameAnalysisResult:
        # - 대역이 만드는 회색조 프레임을 분석 대상 크기와 맞춰, 스캔이 같은 크기를 보게 함
        runner.frame_size = (width, height)
        return analyze_frames(
            source_path="movie.mp4",
            width=width,
            height=height,
            duration_sec=duration_sec,
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
        self.assertEqual(result.analysis_version, 352)
        self.assertEqual(runner.count_commands("pgm"), _SEGMENT_COUNT)

    def test_fast_mode_uses_half_second_segments_and_fast_seek(self) -> None:
        runner = FakeStderrRunner()
        result = self.analyze(runner, mode="fast")
        self.assertEqual(result.analysis_version, 351)
        self.assertIn("-noaccurate_seek", runner.commands[0])
        metric = next(args for args in runner.commands if "-filter_complex" in args)
        self.assertEqual(metric[metric.index("-t") + 1], "0.500")

    def test_precise_mode_uses_two_second_segments_and_accurate_seek(self) -> None:
        runner = FakeStderrRunner()
        result = self.analyze(runner, mode="precise")
        self.assertEqual(result.analysis_version, 353)
        self.assertNotIn("-noaccurate_seek", runner.commands[0])
        metric = next(args for args in runner.commands if "-filter_complex" in args)
        self.assertEqual(metric[metric.index("-t") + 1], "2.000")

    def test_ssim_failure_skips_vmaf(self) -> None:
        runner = FakeStderrRunner(ssim_by_tier={"1440p": 0.98})
        result = self.analyze(runner)
        self.assertEqual(runner.count_commands("libvmaf"), 0)
        self.assertEqual(len(result.tier_scores), 7)
        self.assertIsNone(result.candidate_tier)
        self.assertFalse(result.unmeasurable)
        self.assertLess(result.tier_scores[0].ssim or 1.0, 0.996)

    def test_unconfirmed_tiers_receive_more_rounds(self) -> None:
        runner = FakeStderrRunner(ssim_by_tier={"1440p": 0.9995, "1080p": 0.997, "900p": 0.99})
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
        # - 같은 화면만 있는 지점은 평균 점수가 1이라 dB가 상한에 걸려 평탄으로 제외됨
        all_inf = "".join(f"n:{index} Y:1 U:1 V:1 All:1.000000 (inf)\n" for index in range(30))
        runner = FakeStderrRunner(ssim_text_by_tier=_tier_scores_text(all_inf))
        result = self.analyze(runner)
        self.assertTrue(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)

    def test_partially_static_point_scores_only_moving_frames(self) -> None:
        """정지 화면 프레임을 뺀 모집단이라 희석된 평균이 아니라 움직이는 프레임 손실이 점수에 남음"""
        thirty_db_score = 1.0 - 10.0 ** (-30.0 / 10.0)
        log = _static_mixed_log(thirty_db_score, finite_lines=85, static_lines=15)
        result = self.analyze(FakeStderrRunner(ssim_text_by_tier=_tier_scores_text(log)))
        self.assertFalse(result.unmeasurable)
        # - 평균이 통과 확정값을 넘어 1라운드에서 확정되므로 유효 지점은 1개
        self.assertEqual(result.valid_segment_count, 1)
        # - 같은 화면 15프레임을 빼고 움직인 85프레임만 평균하므로 희석된 0.99915가 아니라 0.999
        score = result.tier_scores[0].ssim or 0.0
        self.assertAlmostEqual(score, 0.999, places=6)
        # - 같은 화면을 빼는 해석으로도 30.0dB이며 평탄 기준 미만이라 지점이 살아남음
        self.assertAlmostEqual(ssim_db_from_score(score), 30.0, places=3)

    def test_moving_frames_under_the_bar_fail_the_static_point(self) -> None:
        """30프레임 중 26프레임이 같은 화면이고 움직인 4프레임이 기준 미만이면 그 지점은 탈락

        - 희석된 평균 0.99867은 통과 기준을 넘지만 움직이는 프레임만 보면 0.99라 통과하지 않음
        - 정지 프레임이 평균을 끌어올려 축소 손실을 가리던 회귀를 막는 확인
        """
        log = _static_mixed_log(0.99, finite_lines=4, static_lines=26)
        result = self.analyze(FakeStderrRunner(ssim_text_by_tier=_tier_scores_text(log)), has_vmaf=False)
        self.assertFalse(result.unmeasurable)
        self.assertIsNone(result.candidate_tier)
        self.assertLess(result.tier_scores[0].ssim or 1.0, 0.996)

    def test_static_point_is_flat_and_skipped_in_vmaf(self) -> None:
        """모든 규격에서 모든 프레임이 같은 화면인 지점만 정지로 보아 VMAF에서도 제외"""
        all_inf = "".join(f"n:{index} Y:1 U:1 V:1 All:1.000000 (inf)\n" for index in range(30))
        runner = FakeStderrRunner(ssim_text_by_tier=_tier_scores_text(all_inf))
        result = self.analyze(runner)
        self.assertTrue(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)
        self.assertEqual(runner.count_commands("libvmaf"), 0)

    def test_frames_identical_only_at_the_higher_tier_are_counted_as_full_score(self) -> None:
        """높은 규격에서만 같은 화면인 프레임은 그 규격에서 1.0으로 들어가고 양쪽 모집단에 포함

        - 1440p는 29프레임이 같은 화면이고 1080p는 24프레임만 같음
        - 공통 모집단은 두 규격 중 한 곳이라도 움직인 6프레임이라 후보를 잃지 않음
        """
        log_1440 = _static_mixed_log(0.99, finite_lines=1, static_lines=29)
        log_1080 = _static_mixed_log(0.9995, finite_lines=5, static_lines=24) + "".join(
            f"n:{index} Y:1 U:1 V:1 All:0.985000 (18.24)\n" for index in range(5, 6)
        )
        logs = _tier_scores_text(log_1080)
        logs["1440p"] = log_1440
        result = self.analyze(FakeStderrRunner(ssim_text_by_tier=logs))
        self.assertIsNotNone(result.candidate_tier)
        self.assertAlmostEqual(result.tier_scores[0].ssim or 0.0, (5 * 1.0 + 0.99) / 6, places=6)
        self.assertAlmostEqual(result.tier_scores[1].ssim or 0.0, (5 * 0.9995 + 0.985) / 6, places=6)

    def test_all_static_point_is_flat(self) -> None:
        all_inf = "".join(f"n:{index} Y:1 U:1 V:1 All:1.000000 (inf)\n" for index in range(100))
        runner = FakeStderrRunner(ssim_text_by_tier=_tier_scores_text(all_inf))
        result = self.analyze(runner)
        self.assertTrue(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)

    def test_black_segments_are_unmeasurable(self) -> None:
        runner = FakeStderrRunner(black_frame=True)
        result = self.analyze(runner)
        self.assertTrue(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)
        self.assertEqual(runner.count_commands("-filter_complex"), 0)

    def test_scan_failure_treats_segment_as_black(self) -> None:
        result = self.analyze(FakeStderrRunner(black_returncode=1))
        self.assertTrue(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)

    def test_full_analysis_scan_failure_is_marked_as_detection_failure(self) -> None:
        """전체 화질 분석에서 지점을 하나도 읽지 못하면 감지 실패로 표시하는지 검증"""
        result = self.analyze(FakeStderrRunner(black_returncode=1))
        self.assertTrue(result.crop_scan_failed)

    def test_full_analysis_without_valid_points_but_read_frames_is_not_a_scan_failure(self) -> None:
        """모든 지점을 읽었는데 유효 구간이 없는 전체 분석은 감지 성공으로 두는지 검증"""
        black = self.analyze(FakeStderrRunner(black_frame=True))
        self.assertTrue(black.unmeasurable)
        self.assertFalse(black.crop_scan_failed)
        self.assertFalse(black.active_region.has_black_bars)

    def test_full_analysis_metric_failure_is_not_a_scan_failure(self) -> None:
        """지표만 재지 못한 전체 분석은 감지 실패로 표시하지 않는지 검증"""
        result = self.analyze(FakeStderrRunner(fail_metric=True))
        self.assertTrue(result.unmeasurable)
        self.assertFalse(result.crop_scan_failed)

    def test_full_analysis_marks_vr_scan_failure_as_no_detection_failure(self) -> None:
        """VR 보호 영상은 전체 분석에서도 스캔이 비어 감지 실패로 세지 않는지 검증"""
        result = self.analyze(FakeStderrRunner(black_returncode=1), width=3840, height=1920)
        self.assertTrue(result.is_vr_protected)
        self.assertFalse(result.crop_scan_failed)

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
        """여백 감지 전용 실행이 1단계 스캔만 하고 축소 후보와 지표를 비우는지 검증"""
        runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        stages: list[str] = []
        result = self.analyze(
            runner,
            width=1920,
            height=1080,
            on_stage=lambda stage, _done, _total: stages.append(stage),
            crop_only=True,
        )
        self.assertEqual(runner.count_commands("pgm"), _SEGMENT_COUNT)
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

    def test_crop_only_without_valid_points_is_unmeasurable(self) -> None:
        """유효 지점이 없는 감지 전용 결과 중 스캔 실패만 측정 불가로 표시되는지 검증"""
        failed_points = self.analyze(FakeStderrRunner(black_returncode=1), crop_only=True)
        self.assertTrue(failed_points.unmeasurable)
        self.assertEqual(failed_points.valid_segment_count, 0)
        self.assertEqual(failed_points.total_segment_count, _SEGMENT_COUNT)

    def test_crop_only_all_black_points_stays_measurable(self) -> None:
        """모든 지점이 검은 화면인 감지 전용 결과는 감지에 성공했으므로 측정 불가가 아님"""
        black = self.analyze(FakeStderrRunner(black_frame=True), crop_only=True)
        self.assertFalse(black.unmeasurable)
        self.assertEqual(black.valid_segment_count, 0)
        self.assertEqual(black.total_segment_count, _SEGMENT_COUNT)
        self.assertEqual(black.active_region, ActiveRegion(3840, 2160, 0, 0, False))

    def test_crop_only_zero_duration_is_not_a_scan_failure(self) -> None:
        """구간이 없는 감지 전용 결과는 스캔 실패가 아니므로 측정 불가가 아님"""
        no_segments = self.analyze(FakeStderrRunner(), duration_sec=0.0, crop_only=True)
        self.assertFalse(no_segments.unmeasurable)
        self.assertEqual(no_segments.valid_segment_count, 0)
        self.assertEqual(no_segments.total_segment_count, 0)

    def test_crop_only_vr_with_failed_scan_is_not_a_detection_failure(self) -> None:
        """VR 보호 영상은 스캔이 비어도 감지 실패로 세지 않아 캐시를 재사용하는지 검증"""
        runner = FakeStderrRunner(black_returncode=1)
        result = self.analyze(runner, width=3840, height=1920, crop_only=True)
        self.assertTrue(result.is_vr_protected)
        self.assertFalse(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)

    def test_crop_only_partial_failure_with_black_points_is_not_a_detection_failure(self) -> None:
        """검은 화면 지점과 실패 지점이 섞여도 읽은 지점이 있으면 감지 성공으로 보는지 검증"""
        runner = FakeStderrRunner(black_frame=True)
        scanned = 0

        def fail_last_point(args: list[str]) -> None:
            nonlocal scanned
            if any(token == "pgm" for token in args):
                scanned += 1
                if scanned == _SEGMENT_COUNT:
                    runner.black_returncode = 1

        runner.on_call = fail_last_point
        result = self.analyze(runner, crop_only=True)
        self.assertEqual(runner.count_commands("pgm"), _SEGMENT_COUNT)
        self.assertFalse(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)
        self.assertEqual(result.total_segment_count, _SEGMENT_COUNT)

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

