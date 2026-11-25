"""인코딩 실행 전 품질값 정밀 결정 단계 검증(결정 18, 6a-2)

- 확정 값 채택, 취소, 미수렴 유지, 진행 이벤트 필드, 결과 사유 문구를 가짜 탐색 함수로 확인
- 실제 FFmpeg 없이 실행하고 캐시는 임시 폴더에 둠
"""

import os
import tempfile
import threading
import unittest
from typing import Any

from src.core.encode.encode_gate import GatePurpose
from src.core.encode.encode_plan import EncodePlan, build_encode_plan
from src.core.encode.stream_layout import parse_stream_layout
from src.core.evaluation.sample_tuner import (
    REASON_CONVERGED,
    REASON_FALLBACK_BASELINE,
    QualityProbe,
    TuneResult,
)
from src.service.encode.encode_events import PHASE_PRECISE_TUNE, EncodeEvent
from src.service.encode.encode_job import EncodeJob
from src.service.encode.encode_precise_tune import PreciseQualityTuner
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
        output_size=(1920, 1080),
    )


def _job(tmp: str, *, quality: float = 22.0) -> EncodeJob:
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
        output_size=(1920, 1080),
        scale_filter="scale=1920:1080",
        maxrate_kbps=None,
        baseline_quality=22.0,
        precise_tune=True,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
