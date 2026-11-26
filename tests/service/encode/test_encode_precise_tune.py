"""인코딩 실행 전 품질값 정밀 결정 단계 검증(결정 18, 6a-2)

- 확정 값 채택, 취소, 미수렴 유지, 진행 이벤트 필드, 결과 사유 문구를 가짜 탐색 함수로 확인
- 실제 FFmpeg 없이 실행하고 캐시는 임시 폴더에 둠
"""

import os
import tempfile
import threading
import unittest
from dataclasses import replace
from typing import Any

from src.core.encode.encode_gate import GatePurpose
from src.core.encode.encode_plan import EncodePlan, build_encode_plan
from src.core.encode.stream_layout import parse_stream_layout
from src.core.evaluation.sample_tuner import (
    COMPARE_CANDIDATE_SMALLER,
    COMPARE_ORIGINAL_NOT_LARGER,
    DECISION_KEEP_DOWNSCALE,
    DECISION_KEEP_ORIGINAL,
    REASON_CONVERGED,
    REASON_FALLBACK_BASELINE,
    DownscaleComparison,
    QualityProbe,
    TuneResult,
    compare_downscale,
    format_comparison_reason,
)
from src.service.encode.encode_events import PHASE_PRECISE_TUNE, EncodeEvent
from src.service.encode.encode_job import EncodeJob
from src.service.encode.encode_precise_tune import (
    PreciseQualityTuner,
    original_resolution_plan,
    replace_downscale_with_original,
)
from src.service.sample_tuner_service import SampleTunerCache


def _plan(tmp: str, *, quality: float = 22.0) -> EncodePlan:
    """임시 폴더 기준 인코딩 계획"""
    layout = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc"}]})
    return build_encode_plan(
        source_path=os.path.join(tmp, "in.mp4"),
        part_path=os.path.join(tmp, "out.mp4.part"),
        output_path=os.path.join(tmp, "out.mp4"),
        source_ext="mp4",
        encoder="hevc_nvenc",
        quality=quality,
        fps=30.0,
        layout=layout,
        container_policy="mp4",
        tag_value="v=1",
    )


def _job(tmp: str, *, quality: float = 22.0, source_width: int | None = None) -> EncodeJob:
    """정밀 결정 대상 절약 목적 작업"""
    return EncodeJob(
        filename="in.mp4",
        root_dir=tmp,
        source_path=os.path.join(tmp, "in.mp4"),
        source_size=1000,
        source_mtime_ns=42,
        source_duration_sec=600.0,
        source_ext="mp4",
        fps=30.0,
        quality=quality,
        purpose=GatePurpose.SAVING,
        output_size=None,
        scale_filter=None,
        maxrate_kbps=None,
        baseline_quality=22.0,
        precise_tune=True,
        source_width=source_width,
        source_height=None if source_width is None else 1600,
    )


class _TunerCase(unittest.TestCase):
    """임시 폴더, 캐시, 가짜 탐색 함수 준비"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp: str = self._tmp.name
        self.events: list[EncodeEvent] = []
        self.calls: list[dict[str, Any]] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _tuner(self, result: TuneResult, *, abort: threading.Event | None = None) -> PreciseQualityTuner:
        cache = SampleTunerCache(os.path.join(self.tmp, "video_cache.sqlite3"))

        def _tune(**kwargs: Any) -> TuneResult:
            self.calls.append(kwargs)
            on_probe = kwargs.get("on_probe")
            if on_probe is not None:
                for index in range(1, len(result.probes) + 1):
                    on_probe(index, len(result.probes), 0.0)
            return result

        return PreciseQualityTuner(
            ffmpeg_exec="ffmpeg",
            process_factory=lambda _args, _line: None,  # type: ignore[arg-type,return-value]
            abort=abort if abort is not None else threading.Event(),
            emit=self.events.append,
            cache=cache,
            tune=_tune,
        )


class TestPreciseQualityTuner(_TunerCase):
    """확정 값 채택과 미수렴 유지, 취소, 진행 이벤트를 확인"""

    def test_converged_result_is_adopted_with_reason(self) -> None:
        result = TuneResult(
            best_quality=24.0,
            probes=(QualityProbe(quality=24.0, vmaf=98.5, ssim=0.998),),
            converged=True,
            reason=REASON_CONVERGED,
        )
        outcome = self._tuner(result)(7, _job(self.tmp), _plan(self.tmp))
        assert outcome is not None
        self.assertEqual(outcome.adopted_quality, 24.0)
        self.assertEqual(outcome.previous_quality, 22.0)
        self.assertIn("품질값 정밀 결정: 22.0 -> 24.0", outcome.reason)
        self.assertIn("품질값 24.0 확인", outcome.reason)

    def test_unconverged_result_keeps_the_planned_quality(self) -> None:
        result = TuneResult(
            best_quality=22.0,
            probes=(QualityProbe(quality=22.0, vmaf=80.0, ssim=0.99),),
            converged=False,
            reason=REASON_FALLBACK_BASELINE,
        )
        outcome = self._tuner(result)(7, _job(self.tmp), _plan(self.tmp))
        assert outcome is not None
        self.assertEqual(outcome.adopted_quality, 22.0)
        self.assertEqual(outcome.reason, "품질값 22.0 유지 (기준 미달)")

    def test_tune_job_carries_cropped_source_size(self) -> None:
        """정밀 결정 입력이 축소 결과 크기가 아니라 크롭한 원본 크기를 담는지 확인

        - 지표 기준 크기와 VMAF 모델을 원본에서 골라야 축소 계획의 측정이 성립
        """
        result = TuneResult(
            best_quality=24.0,
            probes=(QualityProbe(quality=24.0, vmaf=98.5, ssim=0.998),),
            converged=True,
            reason=REASON_CONVERGED,
        )
        self._tuner(result)(7, _job(self.tmp, source_width=3840), _plan(self.tmp))
        tune_job = self.calls[0]["job"]
        self.assertEqual((tune_job.width, tune_job.height), (3840, 1600))

    def test_tune_job_size_is_zero_when_source_size_is_unknown(self) -> None:
        """원본 크기를 모르면 지표 기준 크기를 0으로 두어 계획의 crop에서 유도하게 함"""
        result = TuneResult(
            best_quality=24.0,
            probes=(QualityProbe(quality=24.0, vmaf=98.5, ssim=0.998),),
            converged=True,
            reason=REASON_CONVERGED,
        )
        self._tuner(result)(7, _job(self.tmp), _plan(self.tmp))
        tune_job = self.calls[0]["job"]
        self.assertEqual((tune_job.width, tune_job.height), (0, 0))

    def test_cancel_returns_none(self) -> None:
        abort = threading.Event()
        abort.set()
        result = TuneResult(best_quality=23.0, probes=(), converged=True, reason=REASON_CONVERGED)
        self.assertIsNone(self._tuner(result, abort=abort)(7, _job(self.tmp), _plan(self.tmp)))

    def test_phase_events_carry_probe_progress_and_final_quality(self) -> None:
        result = TuneResult(
            best_quality=23.5,
            probes=(
                QualityProbe(quality=25.0, vmaf=97.0, ssim=0.997),
                QualityProbe(quality=23.5, vmaf=98.4, ssim=0.998),
            ),
            converged=True,
            reason=REASON_CONVERGED,
        )
        self._tuner(result)(3, _job(self.tmp), _plan(self.tmp))
        phases = [event.phase for event in self.events if event.phase is not None]
        self.assertTrue(all(phase.stage == PHASE_PRECISE_TUNE for phase in phases))
        first = phases[0]
        self.assertEqual((first.probe_index, first.probe_total), (0, 4))
        self.assertIsNone(first.precise_quality)
        final = phases[-1]
        self.assertEqual(final.probe_index, 2)
        self.assertEqual(final.precise_quality, 23.5)
        self.assertIn("품질값 정밀 결정", final.precise_reason)
        self.assertEqual([event.batch_id for event in self.events], [3] * len(self.events))

    def test_tuner_passes_cancel_and_callback_hooks_to_the_service(self) -> None:
        result = TuneResult(
            best_quality=22.0,
            probes=(QualityProbe(quality=22.0, vmaf=99.0, ssim=0.999),),
            converged=True,
            reason=REASON_CONVERGED,
        )
        abort = threading.Event()
        self._tuner(result, abort=abort)(5, _job(self.tmp), _plan(self.tmp))
        self.assertTrue(self.calls)
        call = self.calls[0]
        self.assertIs(call["cancel_event"], abort)
        self.assertEqual(call["batch_id"], 5)
        self.assertEqual(call["job"].encoder, "hevc_nvenc")
        self.assertEqual(call["job"].baseline_quality, 22.0)
        self.assertTrue(callable(call["on_probe"]))


def _converged(quality: float) -> TuneResult:
    """수렴한 탐색 결과: 그 품질값으로 목표를 넘긴 프로브 하나"""
    return TuneResult(
        best_quality=quality,
        probes=(QualityProbe(quality=quality, vmaf=98.5, ssim=0.998, bitrate_kbps=900.0),),
        converged=True,
        reason=REASON_CONVERGED,
    )


def _downscale_plan(tmp: str) -> EncodePlan:
    """축소 후보 계획. 원본은 4K, 출력은 1080p"""
    layout = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc"}]})
    return build_encode_plan(
        source_path=os.path.join(tmp, "in.mp4"),
        part_path=os.path.join(tmp, "out.mp4.part"),
        output_path=os.path.join(tmp, "out.mp4"),
        source_ext="mp4",
        encoder="hevc_nvenc",
        quality=22.0,
        fps=30.0,
        layout=layout,
        container_policy="mp4",
        tag_value="v=1",
        output_size=(1920, 1080),
        scale_filter="scale=1920:1080",
        maxrate_kbps=5400,
    )


def _downscale_job(tmp: str) -> EncodeJob:
    """축소 인코딩 대상 절약 목적 작업. 판정 보호 품질값 26.0, 기준 22.0"""
    return EncodeJob(
        filename="in.mp4",
        root_dir=tmp,
        source_path=os.path.join(tmp, "in.mp4"),
        source_size=1000,
        source_mtime_ns=42,
        source_duration_sec=600.0,
        source_ext="mp4",
        fps=30.0,
        quality=26.0,
        purpose=GatePurpose.SAVING,
        output_size=(1920, 1080),
        scale_filter="scale=1920:1080",
        maxrate_kbps=5400,
        corrected_target_kbps=3000.0,
        baseline_quality=22.0,
        precise_tune=True,
        source_width=3840,
        source_height=2160,
        original_target_kbps=4500.0,
    )


class TestDownscaleComparisonWiring(_TunerCase):
    """축소 후보와 원본 규격 비교를 정밀 결정 단계가 실행하고 계획에 반영하는지 확인"""

    def _tuner_with_compare(
        self,
        comparing: DownscaleComparison | None,
        result: TuneResult | None = None,
    ) -> PreciseQualityTuner:
        cache = SampleTunerCache(os.path.join(self.tmp, "video_cache.sqlite3"))
        settled = result if result is not None else _converged(24.0)
        self.compare_calls: list[dict[str, Any]] = []

        def _tune(**_kwargs: Any) -> TuneResult:
            return settled

        def _compare(**kwargs: Any) -> DownscaleComparison:
            self.compare_calls.append(kwargs)
            assert comparing is not None
            return comparing

        return PreciseQualityTuner(
            ffmpeg_exec="ffmpeg",
            process_factory=lambda _args, _line: None,  # type: ignore[arg-type,return-value]
            abort=threading.Event(),
            emit=self.events.append,
            cache=cache,
            tune=_tune,
            compare=_compare,
        )

    def test_original_wins_and_returns_the_original_maxrate(self) -> None:
        """원본 옵션이 이기면 원본 화소 크기와 규격 상한 최대 비트레이트를 돌려줌"""
        comparison = DownscaleComparison(
            False,
            QualityProbe(quality=22.0, vmaf=99.0, ssim=0.999, bitrate_kbps=900.0),
            QualityProbe(quality=26.0, vmaf=99.0, ssim=0.999, bitrate_kbps=800.0),
            COMPARE_ORIGINAL_NOT_LARGER,
        )
        outcome = self._tuner_with_compare(comparison)(7, _downscale_job(self.tmp), _downscale_plan(self.tmp))
        assert outcome is not None
        self.assertEqual(outcome.decision, DECISION_KEEP_ORIGINAL)
        # - 원본 4K는 2160p 규격 상한(16600) 안이므로 보정 목표 4500 x 1.5를 100k 올림한 6800
        self.assertEqual(outcome.maxrate_kbps, 6800)
        self.assertIn("원본 해상도 유지", outcome.reason)
        self.assertEqual(outcome.adopted_quality, 24.0)
        self.assertEqual(len(self.compare_calls), 1)
        call = self.compare_calls[0]
        self.assertIsNone(call["original_plan"].scale_filter)
        self.assertEqual(call["original_plan"].output_size, (3840, 2160))
        self.assertEqual(call["candidate_plan"].scale_filter, "scale=1920:1080")
        self.assertEqual(call["original_quality"], 26.0)
        self.assertEqual(call["candidate_probe"].quality, 24.0)

    def test_downscale_wins_and_keeps_the_planned_plan(self) -> None:
        comparison = DownscaleComparison(
            True,
            QualityProbe(quality=22.0, vmaf=99.0, ssim=0.999, bitrate_kbps=900.0),
            QualityProbe(quality=26.0, vmaf=99.0, ssim=0.999, bitrate_kbps=1200.0),
            COMPARE_CANDIDATE_SMALLER,
        )
        outcome = self._tuner_with_compare(comparison)(7, _downscale_job(self.tmp), _downscale_plan(self.tmp))
        assert outcome is not None
        self.assertEqual(outcome.decision, DECISION_KEEP_DOWNSCALE)
        self.assertIsNone(outcome.maxrate_kbps)
        self.assertIn(format_comparison_reason(comparison), outcome.reason)
        self.assertIn("품질값 24.0 확인", outcome.reason)

    def test_tie_keeps_the_original_resolution(self) -> None:
        """비트레이트가 같으면 해상도를 지키려고 원본을 고름"""
        comparison = compare_downscale(
            QualityProbe(quality=22.0, vmaf=99.0, ssim=0.999, bitrate_kbps=900.0),
            QualityProbe(quality=26.0, vmaf=99.0, ssim=0.999, bitrate_kbps=900.0),
        )
        self.assertFalse(comparison.use_downscale)
        outcome = self._tuner_with_compare(comparison)(7, _downscale_job(self.tmp), _downscale_plan(self.tmp))
        assert outcome is not None
        self.assertEqual(outcome.decision, DECISION_KEEP_ORIGINAL)

    def test_manual_output_resolution_skips_the_comparison(self) -> None:
        tuner = self._tuner_with_compare(None)
        job = replace(_downscale_job(self.tmp), manual_downscale=True)
        outcome = tuner(7, job, _downscale_plan(self.tmp))
        assert outcome is not None
        self.assertEqual(outcome.decision, "")
        self.assertEqual(self.compare_calls, [])
        self.assertIn("품질값 24.0 확인", outcome.reason)

    def test_unconverged_result_leaves_the_planned_option_unchanged(self) -> None:
        """정밀 탐색이 값을 못 정하면 비교 근거가 없으므로 확정 기록 없이 계획을 그대로 씀"""
        result = TuneResult(
            best_quality=22.0,
            probes=(QualityProbe(quality=22.0, vmaf=80.0, ssim=0.99),),
            converged=False,
            reason=REASON_FALLBACK_BASELINE,
        )
        tuner = self._tuner_with_compare(None, result)
        outcome = tuner(7, _downscale_job(self.tmp), _downscale_plan(self.tmp))
        assert outcome is not None
        self.assertEqual(outcome.decision, "")
        self.assertEqual(outcome.adopted_quality, 26.0)
        self.assertEqual(self.compare_calls, [])

    def test_cancel_during_the_comparison_returns_none(self) -> None:
        abort = threading.Event()
        cache = SampleTunerCache(os.path.join(self.tmp, "video_cache.sqlite3"))

        def _compare(**_kwargs: Any) -> DownscaleComparison:
            abort.set()
            return DownscaleComparison(True, QualityProbe(quality=22.0), QualityProbe(quality=26.0))

        tuner = PreciseQualityTuner(
            ffmpeg_exec="ffmpeg",
            process_factory=lambda _args, _line: None,  # type: ignore[arg-type,return-value]
            abort=abort,
            emit=self.events.append,
            cache=cache,
            tune=lambda **_kwargs: _converged(24.0),
            compare=_compare,
        )
        self.assertIsNone(tuner(7, _downscale_job(self.tmp), _downscale_plan(self.tmp)))

    def test_phase_event_carries_the_resolution_decision(self) -> None:
        comparison = DownscaleComparison(
            False,
            QualityProbe(quality=22.0, vmaf=99.0, ssim=0.999, bitrate_kbps=900.0),
            QualityProbe(quality=26.0, vmaf=99.0, ssim=0.999, bitrate_kbps=800.0),
            COMPARE_ORIGINAL_NOT_LARGER,
        )
        self._tuner_with_compare(comparison)(7, _downscale_job(self.tmp), _downscale_plan(self.tmp))
        phases = [event.phase for event in self.events if event.phase is not None]
        assert phases[-1] is not None
        self.assertEqual(phases[-1].precise_resolution, DECISION_KEEP_ORIGINAL)
        self.assertIn("원본 해상도 유지", phases[-1].precise_reason)


class TestOriginalResolutionReplacement(unittest.TestCase):
    """원본 해상도 확정이 계획과 작업의 scale, 해상도, 최대 비트레이트를 되돌리는지 확인"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp: str = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_original_plan_keeps_crop_and_drops_scale(self) -> None:
        plan = replace(_downscale_plan(self.tmp), crop_filter="crop=1920:1080:0:0")
        original = original_resolution_plan(plan, _downscale_job(self.tmp), 5000)
        self.assertIsNone(original.scale_filter)
        self.assertEqual(original.output_size, (3840, 2160))
        self.assertEqual(original.maxrate_kbps, 5000)
        self.assertEqual(original.crop_filter, "crop=1920:1080:0:0")

    def test_replacement_updates_job_and_plan(self) -> None:
        job, plan = replace_downscale_with_original(
            _downscale_job(self.tmp), _downscale_plan(self.tmp), 900
        )
        self.assertIsNone(plan.scale_filter)
        self.assertEqual(plan.output_size, (3840, 2160))
        self.assertEqual(plan.maxrate_kbps, 900)
        self.assertIsNone(job.scale_filter)
        self.assertEqual(job.output_size, (3840, 2160))
        self.assertEqual(job.maxrate_kbps, 900)
        self.assertEqual(job.corrected_target_kbps, 4500.0)
        self.assertEqual(job.downscale_decision, DECISION_KEEP_ORIGINAL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
