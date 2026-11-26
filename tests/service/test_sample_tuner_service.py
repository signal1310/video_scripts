"""품질값 정밀 결정 실행 서비스와 결과 캐시 검증(결정 18)

- 가짜 프로세스 팩토리와 가짜 지표 실행기로 FFmpeg 없이 구간 선택, VMAF/SSIM 대체, 취소, 캐시를 확인
"""

import os
import sqlite3
import threading
import unittest
from collections.abc import Callable, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.core.config import CONTAINER_POLICY_MP4
from src.core.encode.encode_plan import EncodePlan, build_encode_plan
from src.core.encode.encode_process import ProcessOutcome
from src.core.encode.stream_layout import parse_stream_layout
from src.core.evaluation.sample_tuner import (
    REASON_CONVERGED,
    REASON_FALLBACK_BASELINE,
    REASON_UNMEASURABLE,
    SAMPLE_TUNER_VERSION,
    DownscaleComparison,
    QualityProbe,
    TuneResult,
    TuneTarget,
    format_comparison_reason,
)
from src.core.quality_probe import (
    LOG_NAME_SSIM,
    LOG_NAME_VMAF,
    VMAF_MODEL_4K,
    VMAF_MODEL_DEFAULT,
    VMAF_THREADS,
)
from src.service import sample_tuner_service as service
from src.service.sample_tuner_service import (
    CACHE_COLUMNS,
    SampleTunerCache,
    SampleTuneRecord,
    TuneCacheKey,
    TuneJob,
    build_segment_metric_command,
    compare_downscale_cached,
    comparison_from_record,
    comparison_plan_hash,
    plan_signature,
    probe_bitrate_kbps,
    probes_from_json,
    probes_to_json,
    record_from_comparison,
    record_from_result,
    record_to_row,
    row_to_record,
    tune_quality_cached,
    tune_quality_for_video,
    tuning_segments,
)


class _FakeProcess:
    """샘플 산출물을 지정 크기로 만들고 취소 여부를 흉내 내는 가짜 프로세스"""

    def __init__(self, args: Sequence[str], *, size: int, cancel: threading.Event | None) -> None:
        self._args: list[str] = list(args)
        self._size: int = size
        self._cancel: threading.Event | None = cancel
        self._cancelled: bool = False

    def start(self) -> None:
        Path(self._args[-1]).write_bytes(b"x" * self._size)

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        if self._cancelled:
            return ProcessOutcome(returncode=None, cancelled=True, stderr_tail=())
        return ProcessOutcome(returncode=0, cancelled=False, stderr_tail=())

    def cancel(self) -> None:
        self._cancelled = True

    def pause(self) -> None:
        """가짜 일시중지"""

    def resume(self) -> None:
        """가짜 재개"""

    def set_priority(self, name: str) -> None:
        """가짜 우선순위"""


def _plan(
    tmp: str,
    *,
    quality: float = 22.0,
    scale_filter: str | None = None,
    crop_filter: str | None = None,
) -> EncodePlan:
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
        container_policy=CONTAINER_POLICY_MP4,
        tag_value="v=1",
        scale_filter=scale_filter,
        crop_filter=crop_filter,
        output_size=(1920, 1080) if scale_filter is not None else None,
    )


def _job(**overrides: object) -> TuneJob:
    values: dict[str, object] = {
        "source_path": "C:/work/in.mp4",
        "source_size": 1000,
        "source_mtime_ns": 42,
        "source_duration_sec": 600.0,
        "encoder": "hevc_nvenc",
        "baseline_quality": 22.0,
        "width": 3840,
        "height": 2160,
    }
    values.update(overrides)
    return TuneJob(**values)  # type: ignore[arg-type]


def _factory(size: int = 10, cancel: threading.Event | None = None) -> Callable[..., _FakeProcess]:
    """산출물 크기가 같은 가짜 프로세스 팩토리"""
    def _make(args: Sequence[str], _on_line: Callable[[str], None]) -> _FakeProcess:
        return _FakeProcess(args, size=size, cancel=cancel)

    return _make


class TestSegmentSelection(unittest.TestCase):
    """공통 구간 규칙에서 대표 2~3구간을 고르고 검은 구간을 빼는지 확인"""

    def test_long_video_picks_three_segments_inside_range(self) -> None:
        segments = tuning_segments(600.0)
        self.assertEqual(len(segments), 3)
        for start, duration in segments:
            self.assertGreaterEqual(start, 60.0 - 1e-6)
            self.assertLessEqual(start + duration, 600.0 + 1e-6)

    def test_short_video_keeps_single_whole_segment(self) -> None:
        self.assertEqual(tuning_segments(12.0), ((0.0, 12.0),))

    def test_black_segments_are_skipped(self) -> None:
        segments = tuning_segments(600.0, (0.9, 0.1, 0.95))
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0], tuning_segments(600.0)[1])

    def test_all_black_keeps_first_segment(self) -> None:
        self.assertEqual(tuning_segments(600.0, (1.0, 1.0, 1.0)), (tuning_segments(600.0)[0],))

    def test_zero_duration_has_no_segments(self) -> None:
        self.assertEqual(tuning_segments(0.0), ())


class TestProbeMeasurement(unittest.TestCase):
    """가짜 프로세스와 가짜 지표 실행기로 프로브 산출과 지표 대체, 임시 폴더 정리를 확인"""

    def test_vmaf_probe_uses_median_and_sample_bitrate(self) -> None:
        with TemporaryDirectory() as tmp:
            created: list[str] = []

            def _runner(args: list[str], work_dir: str) -> tuple[int, str]:
                created.append(work_dir)
                Path(work_dir, LOG_NAME_VMAF).write_text(
                    '{"pooled_metrics": {"vmaf": {"mean": 98.5}}}', encoding="utf-8"
                )
                return 0, ""

            probe = service._measure_probe(
                plan=_plan(tmp),
                segments=((10.0, 2.0), (20.0, 2.0), (30.0, 2.0)),
                quality=22.0,
                ffmpeg_exec="ffmpeg",
                process_factory=_factory(size=1000),
                metric_runner=_runner,
                has_vmaf=True,
                cancel_event=None,
            )
            self.assertEqual(probe.quality, 22.0)
            self.assertEqual(probe.vmaf, 98.5)
            self.assertIsNone(probe.ssim)
            self.assertGreater(probe.bitrate_kbps, 0.0)
            for work_dir in created:
                self.assertFalse(os.path.exists(work_dir))

    def test_missing_vmaf_falls_back_to_ssim(self) -> None:
        with TemporaryDirectory() as tmp:
            calls: list[str] = []

            def _runner(args: list[str], work_dir: str) -> tuple[int, str]:
                metric = LOG_NAME_VMAF if "libvmaf" in " ".join(args) else LOG_NAME_SSIM
                calls.append(metric)
                if metric == LOG_NAME_VMAF:
                    return 1, ""
                Path(work_dir, LOG_NAME_SSIM).write_text(
                    "n:1 Y:0.999 U:0.999 V:0.999 All:0.998 (54.0)\n", encoding="utf-8"
                )
                return 0, ""

            probe = service._measure_probe(
                plan=_plan(tmp),
                segments=((10.0, 2.0),),
                quality=23.0,
                ffmpeg_exec="ffmpeg",
                process_factory=_factory(),
                metric_runner=_runner,
                has_vmaf=True,
                cancel_event=None,
            )
            self.assertIsNone(probe.vmaf)
            self.assertAlmostEqual(probe.ssim or 0.0, 0.998)
            self.assertIn(LOG_NAME_SSIM, calls)

    def test_probe_upscales_for_downscale_plan_and_uses_reference_model(self) -> None:
        """축소 계획 프로브가 샘플을 원본 크기로 되돌리고 모델을 원본 높이에서 고르는지 확인"""
        with TemporaryDirectory() as tmp:
            graphs: list[str] = []

            def _runner(args: list[str], work_dir: str) -> tuple[int, str]:
                graphs.append(args[args.index("-lavfi") + 1])
                Path(work_dir, LOG_NAME_VMAF).write_text(
                    '{"pooled_metrics": {"vmaf": {"mean": 96.5}}}', encoding="utf-8"
                )
                return 0, ""

            probe = service._measure_probe(
                plan=_plan(
                    tmp,
                    scale_filter="scale=1920:1080",
                    crop_filter="crop=3840:1600:0:280",
                ),
                segments=((10.0, 2.0),),
                quality=22.0,
                ffmpeg_exec="ffmpeg",
                process_factory=_factory(),
                metric_runner=_runner,
                has_vmaf=True,
                cancel_event=None,
            )
            self.assertEqual(probe.vmaf, 96.5)
            self.assertTrue(graphs)
            self.assertIn("crop=3840:1600:0:280[ref]", graphs[0])
            self.assertIn("scale=3840:1600:flags=bicubic[dist]", graphs[0])
            self.assertIn(f"model=version={VMAF_MODEL_4K}", graphs[0])

    def test_probe_keeps_default_model_for_small_reference(self) -> None:
        """4K 미만 기준은 기본 모델을 쓰고 축소가 없으면 샘플을 되돌리지 않음"""
        with TemporaryDirectory() as tmp:
            graphs: list[str] = []

            def _runner(args: list[str], work_dir: str) -> tuple[int, str]:
                graphs.append(args[args.index("-lavfi") + 1])
                Path(work_dir, LOG_NAME_VMAF).write_text(
                    '{"pooled_metrics": {"vmaf": {"mean": 95.0}}}', encoding="utf-8"
                )
                return 0, ""

            probe = service._measure_probe(
                plan=_plan(tmp),
                segments=((10.0, 2.0),),
                quality=22.0,
                ffmpeg_exec="ffmpeg",
                process_factory=_factory(),
                metric_runner=_runner,
                has_vmaf=True,
                cancel_event=None,
                reference_size=(1920, 1080),
            )
            self.assertEqual(probe.vmaf, 95.0)
            self.assertIn(f"model=version={VMAF_MODEL_DEFAULT}", graphs[0])
            self.assertNotIn("scale=", graphs[0])

    def test_failed_segment_returns_empty_probe(self) -> None:
        with TemporaryDirectory() as tmp:
            probe = service._measure_probe(
                plan=_plan(tmp),
                segments=((10.0, 2.0), (20.0, 2.0)),
                quality=22.0,
                ffmpeg_exec="ffmpeg",
                process_factory=_factory(size=0),
                metric_runner=lambda _args, _work: (0, ""),
                has_vmaf=True,
                cancel_event=None,
            )
            self.assertFalse(probe.measurable)
            self.assertEqual(probe.bitrate_kbps, 0.0)

    def test_cancel_before_measure_returns_empty_probe(self) -> None:
        with TemporaryDirectory() as tmp:
            cancel = threading.Event()
            cancel.set()
            probe = service._measure_probe(
                plan=_plan(tmp),
                segments=((10.0, 2.0),),
                quality=22.0,
                ffmpeg_exec="ffmpeg",
                process_factory=_factory(),
                metric_runner=lambda _args, _work: (0, ""),
                has_vmaf=True,
                cancel_event=cancel,
            )
            self.assertFalse(probe.measurable)

    def test_metric_reads_sample_written_by_engine(self) -> None:
        """지표 명령의 왜곡 입력이 샘플 엔진이 실제로 쓴 구간 파일인지 확인

        - 경로 이름이 어긋나면 FFmpeg가 입력 부재로 실패해 모든 프로브가 측정 불가가 됨
        """
        with TemporaryDirectory() as tmp:
            seen_inputs: list[str] = []

            def _runner(args: list[str], work_dir: str) -> tuple[int, str]:
                distorted = args[args.index("-i") + 1]
                seen_inputs.append(distorted)
                if not os.path.exists(distorted):
                    return 1, ""
                Path(work_dir, LOG_NAME_VMAF).write_text(
                    '{"pooled_metrics": {"vmaf": {"mean": 97.0}}}', encoding="utf-8"
                )
                return 0, ""

            probe = service._measure_probe(
                plan=_plan(tmp),
                segments=((10.0, 2.0), (20.0, 2.0)),
                quality=22.0,
                ffmpeg_exec="ffmpeg",
                process_factory=_factory(),
                metric_runner=_runner,
                has_vmaf=True,
                cancel_event=None,
            )
            self.assertEqual(probe.vmaf, 97.0)
            self.assertEqual(
                [os.path.basename(path) for path in seen_inputs], ["seg_0.mp4", "seg_1.mp4"]
            )

    def test_metric_command_seeks_reference_only(self) -> None:
        args = build_segment_metric_command(
            ffmpeg_executable="ffmpeg",
            distorted_path="a.mp4",
            reference_path="b.mp4",
            start_sec=12.5,
            duration_sec=2.0,
            metric="vmaf",
            model="vmaf_v0.6.1",
            log_name="metric_vmaf.json",
        )
        self.assertEqual(args[args.index("-ss") + 1], "12.500")
        self.assertEqual(args[4], "-i")
        self.assertEqual(args[5], "a.mp4")
        self.assertIn("libvmaf=model=version=vmaf_v0.6.1", " ".join(args))

    def test_metric_command_without_crop_keeps_plain_inputs(self) -> None:
        """크롭도 축소도 없으면 기존 지표 필터 문자열을 그대로 씀"""
        args = build_segment_metric_command(
            ffmpeg_executable="ffmpeg",
            distorted_path="a.mp4",
            reference_path="b.mp4",
            start_sec=0.0,
            duration_sec=2.0,
            metric="ssim",
            model="vmaf_v0.6.1",
            log_name=LOG_NAME_SSIM,
        )
        self.assertEqual(args[args.index("-lavfi") + 1], f"[0:v][1:v]ssim=stats_file={LOG_NAME_SSIM}")

    def test_metric_command_crops_reference_for_cropped_plan(self) -> None:
        """크롭 계획이면 기준에 같은 crop을 적용하고 샘플 크기는 그대로 둠"""
        args = build_segment_metric_command(
            ffmpeg_executable="ffmpeg",
            distorted_path="a.mp4",
            reference_path="b.mp4",
            start_sec=0.0,
            duration_sec=2.0,
            metric="ssim",
            model="vmaf_v0.6.1",
            log_name=LOG_NAME_SSIM,
            crop_filter="crop=1600:900:160:0",
        )
        graph = args[args.index("-lavfi") + 1]
        self.assertEqual(
            graph, f"[1:v]crop=1600:900:160:0[ref];[0:v][ref]ssim=stats_file={LOG_NAME_SSIM}"
        )
        self.assertNotIn("scale=", graph)

    def test_metric_command_upscales_distorted_to_reference_size(self) -> None:
        """축소 계획이면 기준은 전체 프레임 그대로 두고 샘플을 기준 크기로 되돌림"""
        args = build_segment_metric_command(
            ffmpeg_executable="ffmpeg",
            distorted_path="a.mp4",
            reference_path="b.mp4",
            start_sec=0.0,
            duration_sec=2.0,
            metric="ssim",
            model="vmaf_v0.6.1",
            log_name=LOG_NAME_SSIM,
            upscale_size=(3840, 2160),
        )
        graph = args[args.index("-lavfi") + 1]
        self.assertEqual(
            graph,
            f"[0:v]scale=3840:2160:flags=bicubic[dist];[dist][1:v]ssim=stats_file={LOG_NAME_SSIM}",
        )

    def test_metric_command_combines_crop_and_upscale(self) -> None:
        """크롭과 축소가 함께 있는 계획은 crop 뒤 기준 크기로 샘플을 되돌림"""
        args = build_segment_metric_command(
            ffmpeg_executable="ffmpeg",
            distorted_path="a.mp4",
            reference_path="b.mp4",
            start_sec=0.0,
            duration_sec=2.0,
            metric="vmaf",
            model="vmaf_v0.6.1",
            log_name=LOG_NAME_VMAF,
            crop_filter="crop=1920:1080:0:0",
            upscale_size=(1920, 1080),
        )
        graph = args[args.index("-lavfi") + 1]
        self.assertEqual(
            graph,
            "[1:v]crop=1920:1080:0:0[ref];[0:v]scale=1920:1080:flags=bicubic[dist];"
            f"[dist][ref]libvmaf=model=version=vmaf_v0.6.1:n_threads={VMAF_THREADS}"
            f":log_path={LOG_NAME_VMAF}:log_fmt=json",
        )



class _QualityTracker:
    """가짜 팩토리와 지표 실행기가 한 프로브의 품질값을 주고받는 공용 보관소

    - 샘플 엔진이 구간 병렬 인코딩을 마친 뒤 지표를 재는 순서를 그대로 이용
    """

    def __init__(self, *, at_baseline: float = 99.5, slope: float = 0.4) -> None:
        self._at_baseline: float = at_baseline
        self._slope: float = slope
        self.pending: list[float] = []

    def factory(self, args: Sequence[str], _on_line: Callable[[str], None]) -> _FakeProcess:
        """인자에서 이번 프로브의 품질값을 읽어 가짜 프로세스 생성"""
        for index, item in enumerate(args):
            if item in ("-cq", "-crf", "-qp") and index + 1 < len(args):
                self.pending.append(float(args[index + 1]))
                break
        return _FakeProcess(args, size=1000, cancel=None)

    def runner(self, args: list[str], work_dir: str) -> tuple[int, str]:
        """대기 중인 품질값 기준으로 VMAF 로그를 씀"""
        quality = self.pending.pop(0) if self.pending else 22.0
        vmaf = self._at_baseline - self._slope * (quality - 22.0)
        Path(work_dir, LOG_NAME_VMAF).write_text(
            f'{{"pooled_metrics": {{"vmaf": {{"mean": {vmaf:.4f}}}}}}}', encoding="utf-8"
        )
        return 0, ""


class TestTuneQualityForVideo(unittest.TestCase):
    """탐색 서비스가 실제 샘플 경로와 취소, 지표 대체를 올바로 엮는지 확인"""

    def test_converged_search_returns_passing_quality(self) -> None:
        with TemporaryDirectory() as tmp:
            tracker = _QualityTracker()
            result = tune_quality_for_video(
                job=_job(),
                plan=_plan(tmp),
                process_factory=tracker.factory,
                metric_runner=tracker.runner,
            )
            self.assertTrue(result.converged)
            self.assertEqual(result.reason, REASON_CONVERGED)
            self.assertLessEqual(len(result.probes), 4)
            self.assertIsNotNone(result.best_probe)
            self.assertGreater(result.best_quality, 22.0)

    def test_downscale_plan_uses_job_size_for_metric_geometry(self) -> None:
        """축소 계획 탐색이 TuneJob의 원본 크기로 지표 기하를 맞추는지 확인"""
        with TemporaryDirectory() as tmp:
            graphs: list[str] = []
            tracker = _QualityTracker()

            def _runner(args: list[str], work_dir: str) -> tuple[int, str]:
                graphs.append(args[args.index("-lavfi") + 1])
                return tracker.runner(args, work_dir)

            result = tune_quality_for_video(
                job=_job(),
                plan=_plan(tmp, scale_filter="scale=1920:1080", crop_filter="crop=3840:1600:0:280"),
                process_factory=tracker.factory,
                metric_runner=_runner,
            )
            self.assertTrue(result.converged)
            self.assertTrue(graphs)
            for graph in graphs:
                self.assertIn("scale=3840:1600:flags=bicubic[dist]", graph)
                self.assertIn("crop=3840:1600:0:280[ref]", graph)

    def test_downscale_plan_without_source_size_skips_measurement(self) -> None:
        """원본 화소 크기를 모르는 축소 계획은 샘플 인코딩 없이 측정 불가로 되돌리는지 확인"""
        with TemporaryDirectory() as tmp:
            calls: list[str] = []

            def _factory(args: Sequence[str], _on_line: Callable[[str], None]) -> _FakeProcess:
                calls.append("factory")
                return _FakeProcess(args, size=10, cancel=None)

            def _runner(_args: list[str], _work_dir: str) -> tuple[int, str]:
                calls.append("runner")
                return 0, ""

            result = tune_quality_for_video(
                job=_job(width=0, height=0),
                plan=_plan(tmp, scale_filter="scale=1920:1080"),
                process_factory=_factory,
                metric_runner=_runner,
            )
            self.assertEqual(calls, [])
            self.assertFalse(result.converged)
            self.assertEqual(result.reason, REASON_UNMEASURABLE)
            self.assertEqual(result.best_quality, 22.0)
            self.assertEqual(result.probes, ())

    def test_cancelled_search_returns_unconverged(self) -> None:
        with TemporaryDirectory() as tmp:
            cancel = threading.Event()
            cancel.set()
            result = tune_quality_for_video(
                job=_job(),
                plan=_plan(tmp),
                process_factory=_factory(),
                metric_runner=lambda _args, _work: (0, ""),
                cancel_event=cancel,
            )
            self.assertFalse(result.converged)

    def test_unmeasurable_scores_fall_back_to_baseline(self) -> None:
        with TemporaryDirectory() as tmp:
            result = tune_quality_for_video(
                job=_job(),
                plan=_plan(tmp),
                process_factory=_factory(),
                metric_runner=lambda _args, _work: (1, ""),
            )
            self.assertFalse(result.converged)
            self.assertEqual(result.best_quality, 22.0)

    def test_low_curve_falls_back_to_baseline(self) -> None:
        with TemporaryDirectory() as tmp:
            tracker = _QualityTracker(at_baseline=80.0)
            result = tune_quality_for_video(
                job=_job(),
                plan=_plan(tmp),
                process_factory=tracker.factory,
                metric_runner=tracker.runner,
            )
            self.assertFalse(result.converged)
            self.assertEqual(result.reason, REASON_FALLBACK_BASELINE)
            self.assertEqual(result.best_quality, 22.0)

    def test_probe_callback_reports_index_and_total(self) -> None:
        """탐색이 프로브를 재기 전에 차례와 상한, 품질값을 콜백으로 알리는지 확인"""
        with TemporaryDirectory() as tmp:
            tracker = _QualityTracker()
            seen: list[tuple[int, int, float]] = []
            result = tune_quality_for_video(
                job=_job(),
                plan=_plan(tmp),
                process_factory=tracker.factory,
                metric_runner=tracker.runner,
                on_probe=lambda index, total, quality: seen.append((index, total, quality)),
            )
            self.assertEqual([index for index, _total, _q in seen], list(range(1, len(result.probes) + 1)))
            self.assertTrue(all(total == 4 for _index, total, _q in seen))
            self.assertGreater(seen[0][2], 0.0)

    def test_cancel_mid_search_leaves_no_temp_dirs(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            cancel = threading.Event()
            tracker = _QualityTracker()

            def _runner(args: list[str], work_dir: str) -> tuple[int, str]:
                cancel.set()
                return tracker.runner(args, work_dir)

            with mock.patch("tempfile.tempdir", tmp):
                result = tune_quality_for_video(
                    job=_job(),
                    plan=_plan(tmp),
                    process_factory=tracker.factory,
                    metric_runner=_runner,
                    cancel_event=cancel,
                )
            self.assertFalse(result.converged)
            leftovers = [path.name for path in tmp_root.iterdir() if path.name.startswith("d18_tuner_")]
            self.assertEqual(leftovers, [])


def _cache_key(**overrides: object) -> TuneCacheKey:
    values: dict[str, object] = {
        "source_path": "C:/work/in.mp4",
        "filesize_bytes": 1000,
        "mtime_ns": 42,
        "encoder": "hevc_nvenc",
        "baseline_quality": 22.0,
        "plan_hash": "abc123",
        "target_key": TuneTarget().cache_key,
    }
    values.update(overrides)
    return TuneCacheKey(**values)  # type: ignore[arg-type]


class TestSampleTunerCache(unittest.TestCase):
    """캐시 왕복, 버전 무효화, 옛 DB 호환, 계획 해시 반영을 확인"""

    def _cache(self, tmp: str) -> SampleTunerCache:
        return SampleTunerCache(os.path.join(tmp, "video_cache.sqlite3"))

    def test_roundtrip_preserves_probes_and_reason(self) -> None:
        with TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            key = _cache_key()
            result = tune_quality_for_video(
                job=_job(),
                plan=_plan(tmp),
                process_factory=_QualityTracker().factory,
                metric_runner=_QualityTracker().runner,
            )
            cache.save(record_from_result(key, result, measured_at="2026-11-25T00:00:00Z"))
            loaded = cache.load(key)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.key, key)
            self.assertEqual(loaded.result, result)
            self.assertEqual(loaded.measured_at, "2026-11-25T00:00:00Z")
            self.assertEqual(cache.count(), 1)
            self.assertEqual(cache.clear(), 1)
            self.assertEqual(cache.count(), 0)

    def test_version_bump_invalidates_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            key = _cache_key()
            result = tune_quality_for_video(
                job=_job(),
                plan=_plan(tmp),
                process_factory=_QualityTracker().factory,
                metric_runner=_QualityTracker().runner,
            )
            cache.save(record_from_result(key, result))
            bumped = _cache_key(tuner_version=key.tuner_version + 1)
            self.assertIsNone(cache.load(bumped))

    def test_stale_version_two_rows_are_not_reused(self) -> None:
        """지표 모델과 기하 선택이 바뀌기 전 판(2)의 캐시 행을 재사용하지 않는지 확인"""
        with TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            self.assertEqual(_cache_key().tuner_version, SAMPLE_TUNER_VERSION)
            self.assertGreater(SAMPLE_TUNER_VERSION, 2)
            stale = SampleTuneRecord(
                key=_cache_key(tuner_version=2),
                best_quality=24.0,
                converged=True,
                reason=REASON_CONVERGED,
                probes=(QualityProbe(quality=24.0, vmaf=97.0, ssim=0.997),),
                measured_at="2026-01-01T00:00:00Z",
            )
            cache.save(stale)
            self.assertEqual(cache.count(), 1)
            self.assertIsNone(cache.load(_cache_key()))

    def test_plan_hash_and_target_change_the_key(self) -> None:
        with TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            key = _cache_key()
            result = tune_quality_for_video(
                job=_job(),
                plan=_plan(tmp),
                process_factory=_QualityTracker().factory,
                metric_runner=_QualityTracker().runner,
            )
            cache.save(record_from_result(key, result))
            self.assertIsNone(cache.load(_cache_key(plan_hash="other")))
            self.assertIsNone(cache.load(_cache_key(target_key="99.000/0.990000")))

    def test_old_db_without_table_still_works(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "video_cache.sqlite3")
            legacy = sqlite3.connect(db_path)
            legacy.execute("CREATE TABLE video_props (filename TEXT PRIMARY KEY)")
            legacy.execute("INSERT INTO video_props VALUES ('a.mp4')")
            legacy.commit()
            legacy.close()
            cache = SampleTunerCache(db_path)
            self.assertEqual(cache.count(), 0)
            self.assertIsNone(cache.load(_cache_key()))

    def test_row_roundtrip_follows_columns(self) -> None:
        record = record_from_result(
            _cache_key(),
            TuneResult(
                best_quality=22.5,
                probes=(QualityProbe(quality=22.5, vmaf=98.7, ssim=0.997, bitrate_kbps=1500.0),),
                converged=True,
                reason=REASON_CONVERGED,
            ),
            measured_at="2026-11-25T00:00:00Z",
        )
        row = record_to_row(record)
        self.assertEqual(len(row), len(CACHE_COLUMNS))
        self.assertEqual(row_to_record(row), record)

    def test_probes_json_roundtrip_and_bad_json(self) -> None:
        probes = (
            QualityProbe(quality=22.5, vmaf=98.7, ssim=None, bitrate_kbps=1234.5),
            QualityProbe(quality=23.0, vmaf=None, ssim=0.998, bitrate_kbps=900.0),
        )
        self.assertEqual(probes_from_json(probes_to_json(probes)), probes)
        self.assertEqual(probes_from_json("not json"), ())
        self.assertEqual(probes_from_json('{"a": 1}'), ())

    def test_plan_signature_changes_with_scale_and_crop(self) -> None:
        with TemporaryDirectory() as tmp:
            plain = plan_signature(_plan(tmp))
            scaled = plan_signature(_plan(tmp, scale_filter="scale=1920:1080"))
            self.assertNotEqual(plain, scaled)
            self.assertEqual(plain, plan_signature(_plan(tmp)))

    def test_probe_bitrate_zero_without_duration(self) -> None:
        self.assertEqual(probe_bitrate_kbps(1000, 0.0), 0.0)
        self.assertEqual(probe_bitrate_kbps(0, 2.0), 0.0)
        self.assertAlmostEqual(probe_bitrate_kbps(250_000, 2.0), 1000.0)

    def test_cached_entry_skips_measurement(self) -> None:
        with TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            calls: list[float] = []

            def _runner(args: list[str], work_dir: str) -> tuple[int, str]:
                calls.append(1.0)
                return _QualityTracker().runner(args, work_dir)

            tracker = _QualityTracker()
            first = tune_quality_cached(
                job=_job(),
                plan=_plan(tmp),
                cache=cache,
                process_factory=tracker.factory,
                metric_runner=tracker.runner,
            )
            second = tune_quality_cached(
                job=_job(),
                plan=_plan(tmp),
                cache=cache,
                process_factory=_QualityTracker().factory,
                metric_runner=_runner,
            )
            self.assertEqual(first, second)
            self.assertEqual(calls, [])
            self.assertEqual(cache.count(), 1)

    def test_cancelled_result_is_not_saved(self) -> None:
        with TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            cancel = threading.Event()
            cancel.set()
            tune_quality_cached(
                job=_job(),
                plan=_plan(tmp),
                cache=cache,
                process_factory=_factory(),
                metric_runner=lambda _args, _work: (1, ""),
                cancel_event=cancel,
            )
            self.assertEqual(cache.count(), 0)

    def test_cancel_mid_comparison_leaves_no_temp_dirs(self) -> None:
        """비교 중 취소되면 샘플 임시 폴더를 남기지 않고 원본 옵션 결과도 저장하지 않음"""
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            cache = self._cache(tmp)
            cancel = threading.Event()
            tracker = _QualityTracker()

            def _runner(args: list[str], work_dir: str) -> tuple[int, str]:
                cancel.set()
                return tracker.runner(args, work_dir)

            with mock.patch("tempfile.tempdir", tmp):
                comparison = compare_downscale_cached(
                    job=_job(),
                    candidate_plan=_plan(tmp, scale_filter="scale=1920:1080"),
                    original_plan=_plan(tmp),
                    candidate_probe=QualityProbe(quality=24.0, vmaf=98.5, ssim=0.998, bitrate_kbps=900.0),
                    original_quality=26.0,
                    cache=cache,
                    process_factory=tracker.factory,
                    metric_runner=_runner,
                    cancel_event=cancel,
                )
            self.assertTrue(cancel.is_set())
            self.assertFalse(comparison.original_probe.measurable)
            leftovers = [path.name for path in tmp_root.iterdir() if path.name.startswith("d18_tuner_")]
            self.assertEqual(leftovers, [])
            self.assertEqual(cache.count(), 0)

        with TemporaryDirectory() as tmp:
            with mock.patch.object(service, "get_data_dir", return_value=tmp):
                cache = service.open_default_cache()
            self.assertEqual(cache.db_path, os.path.join(tmp, "video_cache.sqlite3"))

    def test_default_metric_runner_carries_cancel_event(self) -> None:
        runner = service._metric_runner(None)
        self.assertTrue(callable(runner))


class TestCompareDownscaleSamples(unittest.TestCase):
    """원본(크롭)+보호 품질값과 축소+기준 품질값을 같은 구간으로 비교하는지 확인"""

    def test_smaller_passing_candidate_wins(self) -> None:
        with TemporaryDirectory() as tmp:
            def _factory(args: Sequence[str], _on_line: Callable[[str], None]) -> _FakeProcess:
                """축소 계획이면 작은 산출물을 만들어 축소 후보가 더 작게 나오게 함"""
                scaled = any("scale=1920:1080" in item for item in args)
                return _FakeProcess(args, size=1000 if scaled else 4000, cancel=None)

            tracker = _QualityTracker(at_baseline=99.0, slope=0.1)
            comparison = service.compare_downscale_samples(
                job=_job(),
                original_plan=_plan(tmp, quality=26.0),
                candidate_plan=_plan(tmp, quality=22.0, scale_filter="scale=1920:1080"),
                baseline_quality=22.0,
                protect_quality=26.0,
                process_factory=_factory,
                metric_runner=tracker.runner,
            )
            self.assertTrue(comparison.use_downscale)
            self.assertEqual(comparison.reason, "candidate_smaller")
            self.assertLess(comparison.candidate_probe.bitrate_kbps, comparison.original_probe.bitrate_kbps)

    def test_missing_source_size_keeps_original_without_measurement(self) -> None:
        """원본 화소 크기가 없으면 샘플 인코딩 없이 원본 해상도를 유지하는지 확인"""
        with TemporaryDirectory() as tmp:
            calls: list[str] = []

            def _factory(args: Sequence[str], _on_line: Callable[[str], None]) -> _FakeProcess:
                calls.append("factory")
                return _FakeProcess(args, size=1000, cancel=None)

            def _runner(_args: list[str], _work_dir: str) -> tuple[int, str]:
                calls.append("runner")
                return 0, ""

            comparison = service.compare_downscale_samples(
                job=_job(width=0, height=0),
                original_plan=_plan(tmp, quality=26.0),
                candidate_plan=_plan(tmp, quality=22.0, scale_filter="scale=1920:1080"),
                baseline_quality=22.0,
                protect_quality=26.0,
                process_factory=_factory,
                metric_runner=_runner,
            )
            self.assertEqual(calls, [])
            self.assertFalse(comparison.use_downscale)
            self.assertEqual(comparison.reason, REASON_UNMEASURABLE)
            self.assertEqual(comparison.candidate_probe.quality, 22.0)
            self.assertEqual(comparison.original_probe.quality, 26.0)
            self.assertEqual(
                format_comparison_reason(comparison),
                "품질값 정밀 결정: 원본 해상도 유지 (화질 측정 불가)",
            )

    def test_legacy_job_with_crop_measures_with_crop_geometry(self) -> None:
        """원본 화소 크기가 0인 옛 작업 JSON도 크롭이 있으면 크롭 크기로 지표를 재는지 확인"""
        with TemporaryDirectory() as tmp:
            graphs: list[str] = []
            tracker = _QualityTracker(at_baseline=99.0, slope=0.1)

            def _runner(args: list[str], work_dir: str) -> tuple[int, str]:
                graphs.append(args[args.index("-lavfi") + 1])
                return tracker.runner(args, work_dir)

            comparison = service.compare_downscale_samples(
                job=_job(width=0, height=0),
                original_plan=_plan(tmp, quality=26.0, crop_filter="crop=3840:1600:0:280"),
                candidate_plan=_plan(
                    tmp,
                    quality=22.0,
                    scale_filter="scale=1920:1080",
                    crop_filter="crop=3840:1600:0:280",
                ),
                baseline_quality=22.0,
                protect_quality=26.0,
                process_factory=_factory(size=1000),
                metric_runner=_runner,
            )
            self.assertTrue(graphs)
            for graph in graphs:
                self.assertIn("crop=3840:1600:0:280[ref]", graph)
            self.assertTrue(any("scale=3840:1600:flags=bicubic[dist]" in graph for graph in graphs))
            self.assertTrue(comparison.original_probe.measurable)
            self.assertNotEqual(comparison.reason, REASON_UNMEASURABLE)


class TestCompareDownscaleCache(unittest.TestCase):
    """축소 비교 캐시가 두 옵션 계획 해시로 키를 만들고, 재실행 때 샘플 인코딩을 건너뛰는지 확인"""

    def _cache(self, tmp: str) -> SampleTunerCache:
        return SampleTunerCache(os.path.join(tmp, "video_cache.sqlite3"))

    def test_comparison_roundtrip_and_cache_hit(self) -> None:
        with TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            comparison = DownscaleComparison(
                True,
                QualityProbe(quality=22.0, vmaf=99.0, ssim=0.999, bitrate_kbps=900.0),
                QualityProbe(quality=26.0, vmaf=99.0, ssim=0.999, bitrate_kbps=1200.0),
                "candidate_smaller",
            )
            key = TuneCacheKey(
                source_path="C:/work/in.mp4",
                filesize_bytes=1000,
                mtime_ns=42,
                encoder="hevc_nvenc",
                baseline_quality=26.0,
                plan_hash="a" * 16 + "+" + "b" * 16,
                target_key=TuneTarget().cache_key,
            )
            cache.save(record_from_comparison(key, comparison))
            record = cache.load(key)
            assert record is not None
            restored = comparison_from_record(record)
            assert restored is not None
            self.assertEqual(restored, comparison)
            self.assertEqual(restored.candidate_probe.bitrate_kbps, 900.0)

    def test_single_probe_record_is_treated_as_a_miss(self) -> None:
        record = SampleTuneRecord(
            key=_cache_key(),
            best_quality=24.0,
            converged=True,
            reason=REASON_CONVERGED,
            probes=(QualityProbe(quality=24.0),),
            measured_at="2026-01-01T00:00:00Z",
        )
        self.assertIsNone(comparison_from_record(record))

    def test_plan_hash_combines_both_options(self) -> None:
        with TemporaryDirectory() as tmp:
            candidate = _plan(tmp, scale_filter="scale=1920:1080")
            original = _plan(tmp)
            combined = comparison_plan_hash(candidate, original)
            self.assertEqual(combined, f"{plan_signature(candidate)}+{plan_signature(original)}")
            other = comparison_plan_hash(_plan(tmp, scale_filter="scale=1280:720"), original)
            self.assertNotEqual(combined, other)

    def test_cached_comparison_skips_sample_encoding(self) -> None:
        """두 번째 비교는 캐시를 먼저 확인해 샘플 인코딩을 한 번도 하지 않음"""
        with TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            probe = QualityProbe(quality=24.0, vmaf=98.5, ssim=0.998, bitrate_kbps=900.0)
            tracker = _QualityTracker(at_baseline=99.0, slope=0.1)
            first = compare_downscale_cached(
                job=_job(),
                candidate_plan=_plan(tmp, scale_filter="scale=1920:1080"),
                original_plan=_plan(tmp),
                candidate_probe=probe,
                original_quality=26.0,
                cache=cache,
                process_factory=tracker.factory,
                metric_runner=tracker.runner,
            )
            second = compare_downscale_cached(
                job=_job(),
                candidate_plan=_plan(tmp, scale_filter="scale=1920:1080"),
                original_plan=_plan(tmp),
                candidate_probe=probe,
                original_quality=26.0,
                cache=cache,
                process_factory=_QualityTracker().factory,
                metric_runner=lambda _args, _work: (1, ""),
            )
            self.assertEqual(first, second)
            self.assertTrue(first.original_probe.measurable)

    def test_cancelled_comparison_is_not_saved(self) -> None:
        with TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            cancel = threading.Event()
            cancel.set()
            compare_downscale_cached(
                job=_job(),
                candidate_plan=_plan(tmp, scale_filter="scale=1920:1080"),
                original_plan=_plan(tmp),
                candidate_probe=QualityProbe(quality=24.0, vmaf=98.5, ssim=0.998),
                original_quality=26.0,
                cache=cache,
                process_factory=_factory(),
                metric_runner=lambda _args, _work: (1, ""),
                cancel_event=cancel,
            )
            self.assertEqual(cache.count(), 0)








