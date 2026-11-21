"""샘플 인코딩 구성, 지표 파싱, 기울기/비율 집계, 프로세스 실행기 단위 테스트"""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from src.core.encode.encode_plan import EncodePlan, build_encode_plan
from src.core.encode.stream_layout import parse_stream_layout
from src.core.quality_probe import (
    FOUR_K_MIN_LONG_SIDE,
    LOG_NAME_SSIM,
    LOG_NAME_VMAF,
    METRIC_SSIM,
    METRIC_VMAF,
    SSIM_DB_MAX,
    VMAF_MODEL_4K,
    VMAF_MODEL_DEFAULT,
    ProcessCommandRunner,
    build_metric_command,
    build_metric_filter,
    encode_sample_segments,
    fit_quality_slope_k,
    measure_metric,
    median_size_ratio,
    parse_ssim_stats,
    parse_vmaf_log,
    resolve_metric_name,
    vmaf_model_for,
)


def _build_plan() -> EncodePlan:
    layout = parse_stream_layout(
        {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]}
    )
    return build_encode_plan(
        source_path="src.mp4",
        part_path="out.part.mp4",
        output_path="out.mp4",
        source_ext="mp4",
        encoder="hevc_nvenc",
        quality=22.0,
        fps=30.0,
        layout=layout,
        container_policy="mp4",
        tag_value="",
    )


class _RecordingRunner:
    """호출 인자를 기록하고 지정한 파일을 만드는 가짜 실행기"""

    def __init__(self, *, returncode: int = 0, writes: str | None = None, content: str = "") -> None:
        self.calls: list[list[str]] = []
        self._returncode = returncode
        self._writes = writes
        self._content = content

    def __call__(self, args: list[str], work_dir: str) -> int:
        self.calls.append(list(args))
        if self._writes is not None:
            Path(work_dir, self._writes).write_text(self._content, encoding="utf-8")
        return self._returncode


class TestQualityProbe(unittest.TestCase):
    """지표 선택과 파싱, 집계, 샘플 인코딩 인자 재사용을 검증"""

    def test_vmaf_model_and_metric_selection(self) -> None:
        self.assertEqual(vmaf_model_for(3840, 2160), VMAF_MODEL_4K)
        self.assertEqual(vmaf_model_for(3840, FOUR_K_MIN_LONG_SIDE), VMAF_MODEL_4K)
        self.assertEqual(vmaf_model_for(3840, 1920), VMAF_MODEL_4K)
        self.assertEqual(vmaf_model_for(3840, 1600), VMAF_MODEL_4K)
        self.assertEqual(vmaf_model_for(1920, 1080), VMAF_MODEL_DEFAULT)
        self.assertEqual(vmaf_model_for(2560, 1440), VMAF_MODEL_DEFAULT)
        self.assertEqual(resolve_metric_name(True), METRIC_VMAF)
        self.assertEqual(resolve_metric_name(False), METRIC_SSIM)

    def test_metric_filter_and_command(self) -> None:
        vmaf_filter = build_metric_filter(METRIC_VMAF, model=VMAF_MODEL_4K, log_name=LOG_NAME_VMAF)
        self.assertIn("libvmaf", vmaf_filter)
        self.assertIn(f"model=version={VMAF_MODEL_4K}", vmaf_filter)
        self.assertIn(f"log_path={LOG_NAME_VMAF}", vmaf_filter)
        self.assertNotIn("log_path", build_metric_filter(METRIC_VMAF))
        ssim_filter = build_metric_filter(METRIC_SSIM, log_name=LOG_NAME_SSIM)
        self.assertIn(f"ssim=stats_file={LOG_NAME_SSIM}", ssim_filter)
        self.assertEqual(build_metric_filter(METRIC_SSIM), "[0:v][1:v]ssim")
        with self.assertRaises(ValueError):
            build_metric_filter("psnr")
        command = build_metric_command(
            ffmpeg_executable="ffmpeg",
            distorted_path="a.mp4",
            reference_path="b.mp4",
            metric=METRIC_SSIM,
            model=VMAF_MODEL_DEFAULT,
            log_name=LOG_NAME_SSIM,
        )
        self.assertEqual(command[:4], ["ffmpeg", "-hide_banner", "-nostdin", "-y"])
        self.assertEqual(command[command.index("-i") + 1], "a.mp4")
        self.assertEqual(command[-2:], ["null", "-"])

    def test_parse_vmaf_log_and_ssim_stats(self) -> None:
        log = json.dumps({"pooled_metrics": {"vmaf": {"mean": 94.25, "min": 90.0}}})
        self.assertEqual(parse_vmaf_log(log), 94.25)
        self.assertIsNone(parse_vmaf_log("not json"))
        self.assertIsNone(parse_vmaf_log(json.dumps({"pooled_metrics": {}})))
        self.assertIsNone(parse_vmaf_log(json.dumps({"pooled_metrics": {"vmaf": {"mean": "x"}}})))
        stats = "n:1 Y:0.99 U:0.99 V:0.99 All:0.980 (17.0)\nn:2 Y:0.99 U:0.99 V:0.99 All:0.990 (20.0)\n"
        parsed = parse_ssim_stats(stats)
        assert parsed is not None
        self.assertAlmostEqual(parsed[0], 0.985, places=4)
        self.assertAlmostEqual(parsed[1], 18.5, places=4)
        self.assertIsNone(parse_ssim_stats("n:1 Y:0.99"))

    def test_parse_ssim_infinity_is_capped_for_flat_segments(self) -> None:
        inf_only = "n:1 Y:1.000000 U:1.000000 V:1.000000 All:1.000000 (inf)\n"
        parsed = parse_ssim_stats(inf_only)
        assert parsed is not None
        self.assertAlmostEqual(parsed[0], 1.0)
        self.assertAlmostEqual(parsed[1], SSIM_DB_MAX)
        self.assertGreaterEqual(parsed[1], 40.0)
        mixed = (
            "n:1 Y:1.0 U:1.0 V:1.0 All:1.000000 (inf)\n"
            "n:2 Y:0.9 U:0.9 V:0.9 All:0.980000 (17.0)\n"
        )
        parsed_mixed = parse_ssim_stats(mixed)
        assert parsed_mixed is not None
        self.assertAlmostEqual(parsed_mixed[0], 0.99, places=4)
        self.assertAlmostEqual(parsed_mixed[1], (SSIM_DB_MAX + 17.0) / 2.0, places=4)

    def test_fit_slope_k_matches_exponential_model(self) -> None:
        points = [(q, 2.0 ** ((22.0 - q) / 6.0) * 1000.0) for q in (18.0, 22.0, 26.0, 30.0)]
        slope = fit_quality_slope_k(points)
        assert slope is not None
        self.assertAlmostEqual(slope, 6.0, places=6)
        self.assertIsNone(fit_quality_slope_k([(22.0, 100.0)]))
        self.assertIsNone(fit_quality_slope_k([(22.0, 100.0), (22.0, 200.0)]))
        self.assertIsNone(fit_quality_slope_k([(22.0, 100.0), (26.0, 200.0)]))
        self.assertIsNone(fit_quality_slope_k([(22.0, 0.0), (26.0, 0.0)]))

    def test_median_size_ratio(self) -> None:
        self.assertEqual(median_size_ratio([200.0, 300.0, 100.0], [100.0, 100.0, 100.0]), 2.0)
        self.assertEqual(median_size_ratio([100.0, 300.0], [100.0, 100.0]), 2.0)
        self.assertIsNone(median_size_ratio([100.0], [100.0, 200.0]))
        self.assertIsNone(median_size_ratio([0.0], [100.0]))
        self.assertIsNone(median_size_ratio([100.0], [0.0]))

    def test_encode_sample_segments_reuses_product_arguments(self) -> None:
        part_sizes = [1234, 5678]
        calls: list[list[str]] = []

        def runner(args: list[str], work_dir: str) -> int:
            calls.append(list(args))
            Path(args[-1]).write_bytes(b"x" * part_sizes[len(calls) - 1])
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            sizes = encode_sample_segments(
                plan=_build_plan(),
                segments=((6.0, 2.0), (12.0, 2.0)),
                quality=23.5,
                work_dir=tmp,
                runner=runner,
            )
            self.assertEqual(sizes, part_sizes)
            self.assertTrue(all(Path(args[-1]).parent == Path(tmp) for args in calls))
        self.assertEqual(len(calls), 2)
        self.assertIn("-ss", calls[0])
        self.assertIn("6.000", calls[0])
        self.assertIn("-t", calls[0])
        self.assertIn("2.000", calls[0])
        self.assertIn("hevc_nvenc", calls[0])
        self.assertIn("-cq:v:0", calls[0])
        self.assertIn("23.5", calls[0])

    def test_encode_sample_segments_marks_failed_segment_zero(self) -> None:
        def failing_runner(args: list[str], work_dir: str) -> int:
            return 1

        with tempfile.TemporaryDirectory() as tmp:
            sizes = encode_sample_segments(
                plan=_build_plan(),
                segments=((0.0, 2.0),),
                quality=22.0,
                work_dir=tmp,
                runner=failing_runner,
            )
        self.assertEqual(sizes, [0])

    def test_measure_metric_reads_vmaf_and_ssim_logs(self) -> None:
        vmaf_json = json.dumps({"pooled_metrics": {"vmaf": {"mean": 91.5}}})
        ssim_log = "n:1 Y:0.9 U:0.9 V:0.9 All:0.970 (15.0)\n"
        with tempfile.TemporaryDirectory() as tmp:
            runner = _RecordingRunner(writes=LOG_NAME_VMAF, content=vmaf_json)
            self.assertEqual(
                measure_metric(
                    distorted_path="a.mp4",
                    reference_path="b.mp4",
                    work_dir=tmp,
                    metric=METRIC_VMAF,
                    runner=runner,
                ),
                91.5,
            )
            runner = _RecordingRunner(writes=LOG_NAME_SSIM, content=ssim_log)
            self.assertEqual(
                measure_metric(
                    distorted_path="a.mp4",
                    reference_path="b.mp4",
                    work_dir=tmp,
                    metric=METRIC_SSIM,
                    runner=runner,
                ),
                0.97,
            )
            self.assertIsNone(
                measure_metric(
                    distorted_path="a.mp4",
                    reference_path="b.mp4",
                    work_dir=tmp,
                    metric=METRIC_SSIM,
                    runner=_RecordingRunner(returncode=1, writes=LOG_NAME_SSIM, content=ssim_log),
                )
            )
            self.assertIsNone(
                measure_metric(
                    distorted_path="a.mp4",
                    reference_path="b.mp4",
                    work_dir=tmp,
                    metric=METRIC_VMAF,
                    runner=_RecordingRunner(),
                )
            )

    def test_process_command_runner_runs_times_out_and_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = ProcessCommandRunner()
            self.assertEqual(runner([sys.executable, "-c", "print(1)"], tmp), 0)
            with self.assertRaises(TimeoutError):
                ProcessCommandRunner(timeout_sec=0.2)([sys.executable, "-c", "import time; time.sleep(5)"], tmp)
            cancel_event = threading.Event()
            cancel_event.set()
            with self.assertRaises(InterruptedError):
                ProcessCommandRunner(cancel_event=cancel_event)([sys.executable, "-c", "import time; time.sleep(5)"], tmp)
