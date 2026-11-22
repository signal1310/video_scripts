"""화질 비교 순수 로직 검증: 비교 쌍 해석, 하위 규격, FFmpeg 인자, 시점과 배율 계산

- 파일 존재 확인은 임시 폴더에서만 수행
"""

import os
import tempfile
import unittest

from src.core.analysis.frame_compare import (
    DOWNSCALE_TIP_TEMPLATE,
    MISSING_ORIGINAL_REASON,
    NO_TARGET_REASON,
    RESULT_TARGET_KEY,
    RESULT_TARGET_LABEL,
    RESULT_TARGET_TIP,
    ZOOM_FIT,
    ZOOM_ONE_TO_ONE,
    ZOOM_TWO_HUNDRED,
    FrameSource,
    ViewTransform,
    analysis_score_text,
    build_downscale_targets,
    build_frame_extract_command,
    choose_original_path,
    clamp_offset,
    clamp_scale,
    clamp_time_sec,
    clamp_transform,
    default_target_key,
    fit_offset,
    fit_scale,
    fit_transform,
    format_percent,
    format_size_bytes,
    format_timecode,
    frame_step_seconds,
    is_readable_file,
    representative_times,
    resolution_text,
    resolve_compare_plan,
    resolve_pending_paths,
    resolve_result_paths,
    result_target,
    roundtrip_filter,
    shift_time_sec,
    source_detail,
    split_ratio_for_x,
    step_zoom,
    wheel_zoom_factor,
    zoom_around,
    zoom_preset_scale,
)
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY


class _Files(unittest.TestCase):
    """임시 폴더에 비교용 파일을 만드는 기반"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def make(self, name: str) -> str:
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(b"x")
        return path


class PairResolutionTests(_Files):
    """완료, 교체 대기 비교 쌍과 원본 선택 규칙"""

    def test_archive_preferred_then_source(self) -> None:
        archive = self.make("archive.mp4")
        source = self.make("source.mp4")
        self.assertEqual(choose_original_path(source, archive), archive)
        self.assertEqual(choose_original_path(source, None), source)
        self.assertIsNone(choose_original_path(os.path.join(self.root, "nope.mp4"), ""))

    def test_done_result_needs_original_and_output(self) -> None:
        output = self.make("out.mp4")
        source = self.make("source.mp4")
        self.assertEqual(resolve_result_paths(source, output, None), (source, output))
        self.assertIsNone(resolve_result_paths(source, os.path.join(self.root, "nope.mp4"), None))
        self.assertIsNone(resolve_result_paths(os.path.join(self.root, "gone.mp4"), output, None))

    def test_pending_result_needs_source_and_part(self) -> None:
        source = self.make("source.mp4")
        part = self.make("source.part")
        self.assertEqual(resolve_pending_paths(source, part), (source, part))
        self.assertIsNone(resolve_pending_paths(source, ""))
        self.assertIsNone(resolve_pending_paths(os.path.join(self.root, "gone.mp4"), part))

    def test_readable_file_and_plan_keys(self) -> None:
        source = self.make("source.mp4")
        self.assertTrue(is_readable_file(source))
        self.assertFalse(is_readable_file(None))
        self.assertFalse(is_readable_file(self.root))


class DownscaleTargetTests(_Files):
    """하위 규격 목록과 기본 선택"""

    def test_four_k_lists_every_lower_tier_from_high_to_low(self) -> None:
        source = self.make("source.mp4")
        targets = build_downscale_targets(source, 3840, 2160)
        self.assertEqual([item.key for item in targets], ["1440p", "1080p", "900p", "720p", "540p", "480p", "360p"])
        first = targets[0]
        self.assertEqual(first.label, "1440p 축소 비교")
        self.assertEqual(first.detail, "2560x1440")
        self.assertEqual(first.tooltip, DOWNSCALE_TIP_TEMPLATE.format(tier="1440p"))
        self.assertTrue(first.tier is not None)
        self.assertEqual(first.path, source)

    def test_default_prefers_result_then_candidate_then_first(self) -> None:
        result = FrameSource(key=RESULT_TARGET_KEY, path="out.mp4", label=RESULT_TARGET_LABEL)
        targets = (result, FrameSource(key="1080p", path="s", label="a"), FrameSource(key="720p", path="s", label="b"))
        self.assertEqual(default_target_key(targets, "720p"), RESULT_TARGET_KEY)
        self.assertEqual(default_target_key(targets[1:], "720p"), "720p")
        self.assertEqual(default_target_key(targets[1:], "360p"), "1080p")
        self.assertIsNone(default_target_key((), "720p"))

    def test_plan_reports_missing_original_and_no_target(self) -> None:
        missing = resolve_compare_plan(
            source_path=os.path.join(self.root, "gone.mp4"), width=3840, height=2160, size_bytes=1
        )
        self.assertFalse(missing.can_compare)
        self.assertEqual(missing.reason, MISSING_ORIGINAL_REASON)
        self.assertEqual(missing.original.label, "원본")

        small = self.make("small.mp4")
        plan = resolve_compare_plan(source_path=small, width=640, height=360, size_bytes=10)
        self.assertFalse(plan.can_compare)
        self.assertEqual(plan.reason, NO_TARGET_REASON)
        self.assertIsNone(plan.target("anything"))

    def test_plan_puts_result_first_and_uses_candidate_default(self) -> None:
        source = self.make("source.mp4")
        result = result_target("out.mp4", width=1920, height=1080, size_bytes=580 * 1024 * 1024)
        plan = resolve_compare_plan(
            source_path=source,
            width=3840,
            height=2160,
            size_bytes=1024**3,
            result=result,
            candidate_tier="1080p",
        )
        self.assertTrue(plan.can_compare)
        self.assertEqual(plan.targets[0].key, RESULT_TARGET_KEY)
        self.assertEqual(plan.target(None), result)
        self.assertEqual(plan.default_key, RESULT_TARGET_KEY)
        downscale = plan.target("1080p")
        assert downscale is not None
        self.assertEqual(downscale.label, "1080p 축소 비교")
        self.assertEqual(result.detail, "1920x1080 580 MB")
        self.assertEqual(result.tooltip, RESULT_TARGET_TIP)


class FormattingTests(unittest.TestCase):
    """용량, 해상도, 시점, 점수 문구"""

    def test_sizes_use_auto_unit_and_ascii_x(self) -> None:
        self.assertEqual(format_size_bytes(int(1.42 * 1024**3)), "1.42 GB")
        self.assertEqual(format_size_bytes(580 * 1024 * 1024), "580 MB")
        self.assertEqual(resolution_text(3840, 2160), "3840x2160")
        self.assertEqual(source_detail(1920, 1080, 100 * 1024 * 1024), "1920x1080 100 MB")

    def test_timecode_and_percent(self) -> None:
        self.assertEqual(format_timecode(83.4), "01:23.400")
        self.assertEqual(format_timecode(3723.5), "1:02:03.500")
        self.assertEqual(format_timecode(-1), "00:00.000")
        self.assertEqual(format_percent(1.0), "100%")
        self.assertEqual(format_percent(0.255), "26%")

    def test_analysis_score_lists_measured_metrics_only(self) -> None:
        self.assertEqual(analysis_score_text("1080p", 98.7, 0.9983), "화질 분석 1080p: VMAF 98.7, SSIM 0.9983")
        self.assertEqual(analysis_score_text("1080p", None, 0.998), "화질 분석 1080p: SSIM 0.9980")
        self.assertEqual(analysis_score_text("1080p", None, None), "")


class CommandTests(unittest.TestCase):
    """FFmpeg 동일 시점 프레임 추출 인자"""

    def test_original_command_seeks_before_input_and_writes_one_png(self) -> None:
        source = FrameSource(key="original", path="in.mp4", label="원본")
        args = build_frame_extract_command(
            ffmpeg_executable="ffmpeg",
            source=source,
            time_sec=83.4,
            output_path="out.png",
            frame_width=3840,
            frame_height=2160,
        )
        self.assertEqual(args[:4], ["ffmpeg", "-hide_banner", "-nostdin", "-y"])
        self.assertEqual(args[args.index("-ss") + 1], "83.400")
        self.assertLess(args.index("-ss"), args.index("-i"))
        self.assertNotIn("-vf", args)
        self.assertEqual(args[-5:], ["-c:v", "png", "-f", "image2", "out.png"])

    def test_downscale_command_adds_roundtrip_filter(self) -> None:
        tier = RESOLUTION_TIER_BY_KEY["1080p"]
        source = FrameSource(key="1080p", path="in.mp4", label="1080p 축소 비교", tier_key="1080p")
        args = build_frame_extract_command(
            ffmpeg_executable="ffmpeg",
            source=source,
            time_sec=-1,
            output_path="out.png",
            frame_width=3840,
            frame_height=2160,
        )
        self.assertEqual(args[args.index("-ss") + 1], "0.000")
        self.assertEqual(args[args.index("-vf") + 1], roundtrip_filter(3840, 2160, tier))
        self.assertEqual(
            roundtrip_filter(3840, 2160, tier),
            "scale=1920:1080:flags=bicubic,scale=3840:2160:flags=bicubic",
        )


class TimeTests(unittest.TestCase):
    """1프레임, 구간 이동과 대표 시점 계산"""

    def test_frame_step_falls_back_to_one_over_thirty(self) -> None:
        self.assertAlmostEqual(frame_step_seconds(30.0), 1 / 30)
        self.assertAlmostEqual(frame_step_seconds(0.0), 1 / 30)
        self.assertAlmostEqual(frame_step_seconds(-1.0), 1 / 30)

    def test_shift_and_clamp(self) -> None:
        self.assertEqual(clamp_time_sec(-3, 100), 0.0)
        self.assertEqual(clamp_time_sec(120, 100), 100.0)
        self.assertEqual(shift_time_sec(95, 10, 100), 100.0)
        self.assertEqual(shift_time_sec(2, -10, 100), 0.0)

    def test_representative_times_span_ten_to_ninety_percent(self) -> None:
        times = representative_times(100.0)
        self.assertEqual(len(times), 9)
        self.assertAlmostEqual(times[0], 10.0)
        self.assertAlmostEqual(times[-1], 90.0)
        self.assertAlmostEqual(times[4], 50.0)
        self.assertEqual(representative_times(0.0), ())
        self.assertEqual(representative_times(100.0, 0), ())
        self.assertEqual(representative_times(100.0, 1), (10.0,))


class ZoomTests(unittest.TestCase):
    """맞춤, 배율 버튼, 커서 기준 확대와 팬 제한"""

    def test_fit_and_presets(self) -> None:
        self.assertEqual(fit_scale(960, 540, 3840, 2160), 0.25)
        self.assertEqual(fit_scale(0, 0, 3840, 2160), 1.0)
        self.assertEqual(fit_offset(960, 480), 240)
        self.assertEqual(zoom_preset_scale(ZOOM_FIT, 960, 540, 3840, 2160), 0.25)
        self.assertEqual(zoom_preset_scale(ZOOM_ONE_TO_ONE, 960, 540, 3840, 2160), 1.0)
        self.assertEqual(zoom_preset_scale(ZOOM_TWO_HUNDRED, 960, 540, 3840, 2160), 2.0)
        self.assertEqual(zoom_preset_scale("unknown", 960, 540, 3840, 2160), 1.0)

    def test_step_zoom_respects_limits(self) -> None:
        self.assertAlmostEqual(step_zoom(1.0, 1), 1.25)
        self.assertAlmostEqual(step_zoom(1.0, -1), 0.8)
        self.assertEqual(step_zoom(100.0, 1), 8.0)
        self.assertEqual(step_zoom(0.001, -1), 0.05)
        self.assertEqual(clamp_scale(0.0), 0.05)

    def test_offsets_center_small_images_and_clamp_large(self) -> None:
        self.assertEqual(clamp_offset(50, 100, 300), 100)
        self.assertEqual(clamp_offset(-50, 1000, 300), -50)
        self.assertEqual(clamp_offset(-900, 1000, 300), -700)

    def test_zoom_around_keeps_the_cursor_pixel(self) -> None:
        transform = ViewTransform(scale=0.5, offset_x=0, offset_y=0)
        moved = zoom_around(
            transform,
            2.0,
            cursor_x=100,
            cursor_y=50,
            image_width=1000,
            image_height=1000,
            view_width=500,
            view_height=500,
        )
        self.assertAlmostEqual(moved.scale, 1.0)
        self.assertAlmostEqual((100 - moved.offset_x) / moved.scale, 200)
        self.assertAlmostEqual((50 - moved.offset_y) / moved.scale, 100)
        clamped = clamp_transform(
            ViewTransform(scale=1.0, offset_x=-2000, offset_y=-2000), 1000, 1000, 100, 100
        )
        self.assertEqual((clamped.offset_x, clamped.offset_y), (-900, -900))
        fitted = fit_transform(200, 100, 1000, 1000)
        self.assertEqual(fitted.scale, 0.1)
        self.assertAlmostEqual(fitted.offset_x, 50)

    def test_split_ratio_and_wheel_factor(self) -> None:
        self.assertAlmostEqual(split_ratio_for_x(50, 100), 0.5)
        self.assertAlmostEqual(split_ratio_for_x(-10, 100), 0.05)
        self.assertAlmostEqual(split_ratio_for_x(500, 100), 0.95)
        self.assertAlmostEqual(split_ratio_for_x(10, 0), 0.5)
        self.assertGreater(wheel_zoom_factor(120), 1.0)
        self.assertLess(wheel_zoom_factor(-120), 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
