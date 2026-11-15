import unittest
from typing import Any

from src.core.models import VideoProps
from src.core.probe_parser import (
    build_video_props,
    estimate_video_bps,
    extract_rotate_info,
    parse_fps,
    parse_mean_keyframe_interval,
    safe_float,
    safe_int,
)

_MB = 1024 * 1024


def _video(**fields: Any) -> dict[str, Any]:
    return {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, **fields}


def _props(streams: list[dict[str, Any]], fmt: dict[str, Any] | None = None, size: int = _MB) -> VideoProps:
    return build_video_props("clip.mp4", size, {"streams": streams, "format": fmt or {}})


class TestProbeParserHelpers(unittest.TestCase):
    """ffprobe 값 파싱 보조 함수의 경계 입력을 검증"""

    def test_safe_conversions(self) -> None:
        self.assertEqual(safe_float("2.5"), 2.5)
        for bad in (None, "", "abc", [1]):
            self.assertEqual(safe_float(bad, 7.0), 7.0)
            self.assertEqual(safe_int(bad, 7), 7)
        self.assertEqual(safe_int("3.9"), 3)
        self.assertEqual(safe_int(5), 5)

    def test_estimate_video_bps_keeps_minimum_video_share(self) -> None:
        cases = [((0, 100), 0), ((-5, 0), 0), ((1000, 100), 900), ((1000, 150), 850), ((1000, 300), 850)]
        for (total, audio), expected in cases:
            with self.subTest(total=total, audio=audio):
                self.assertEqual(estimate_video_bps(total, audio), expected)

    def test_parse_fps(self) -> None:
        self.assertAlmostEqual(parse_fps("30000/1001"), 29.97, places=2)
        self.assertEqual(parse_fps("60"), 60.0)
        for bad in ("0/1", "30/0", "-30/1", "0/30", "-5", "0", "abc", "a/b", "", None, 30):
            with self.subTest(bad=bad):
                self.assertEqual(parse_fps(bad), 30.0)

    def test_rotation_sources_and_normalization(self) -> None:
        self.assertEqual(extract_rotate_info(None), (0, None))
        self.assertEqual(extract_rotate_info({}), (0, None))
        display = {"side_data_list": ["junk", {"rotation": "bad"}, {"x": 1}, {"rotation": -90}]}
        self.assertEqual(extract_rotate_info(display), (90, "Display Matrix -90°"))
        self.assertEqual(extract_rotate_info({"side_data_list": [{"rotation": 90.5}]}), (270, "Display Matrix 90.5°"))
        self.assertEqual(extract_rotate_info({"side_data_list": [{"rotation": 0}]}), (0, "Display Matrix 0°"))
        self.assertEqual(extract_rotate_info({"tags": {"ROTATE": "270"}}), (270, "Stream Tag 270°"))
        self.assertEqual(extract_rotate_info({"tags": {"rotate": "-90"}}), (270, "Stream Tag -90°"))
        self.assertEqual(extract_rotate_info({"tags": {"rotate": "inf"}}), (0, None))
        self.assertEqual(extract_rotate_info({"tags": {"title": "90"}}), (0, None))

    def test_mean_keyframe_interval(self) -> None:
        def frames(*times: Any) -> dict[str, Any]:
            return {"frames": [{"pts_time": t} for t in times]}

        self.assertIsNone(parse_mean_keyframe_interval({}))
        self.assertIsNone(parse_mean_keyframe_interval(frames("1.0")))
        self.assertEqual(parse_mean_keyframe_interval(frames("0.0", "2.0")), 2.0)
        self.assertEqual(parse_mean_keyframe_interval(frames("0", "2", "2", "5")), 2.5)
        self.assertEqual(parse_mean_keyframe_interval(frames("0", "N/A", "x", "4")), 4.0)
        self.assertIsNone(parse_mean_keyframe_interval(frames("5", "3")))
        fallback = {"frames": [{"best_effort_timestamp_time": "1"}, {"best_effort_timestamp_time": "4"}]}
        self.assertEqual(parse_mean_keyframe_interval(fallback), 3.0)


class TestBuildVideoProps(unittest.TestCase):
    """ffprobe JSON에서 VideoProps를 만드는 비트레이트, 크기, 회전, FPS 보정을 검증"""

    def test_size_audio_and_codec_fields(self) -> None:
        props = _props([_video(bit_rate="2000000"), {"codec_type": "audio", "bit_rate": "128000"}], size=3 * _MB)
        self.assertEqual((props.vid_size_MB, props.vid_kbps, props.aud_kbps), (3.0, 2000, 128))
        self.assertEqual(props.codec, "h264")
        self.assertIsNone(props.keyframe_interval)
        self.assertEqual(_props([{"codec_type": "video"}]).codec, "unknown")

    def test_video_stream_selection_skips_non_video_streams(self) -> None:
        props = _props([{"codec_type": "audio", "codec_name": "aac"}, _video(codec_name="hevc")])
        self.assertEqual(props.codec, "hevc")

    def test_video_bitrate_fallback_chain(self) -> None:
        audio = {"codec_type": "audio", "bit_rate": "128000"}
        self.assertEqual(_props([_video(bit_rate="0"), audio], {"bit_rate": "1000000"}).vid_kbps, 872)
        heavy_audio = {"codec_type": "audio", "bit_rate": "500000"}
        self.assertEqual(_props([_video(), heavy_audio], {"bit_rate": "1000000"}).vid_kbps, 850)
        by_size = _props([_video()], {"duration": "10"}, size=10 * _MB)
        self.assertEqual(by_size.vid_kbps, 8389)
        self.assertEqual(_props([_video()], {"duration": "0"}).vid_kbps, 1)

    def test_duration_prefers_stream_and_never_negative(self) -> None:
        self.assertEqual(_props([_video(duration="5")], {"duration": "9"}).duration, 5.0)
        self.assertEqual(_props([_video()], {"duration": "9"}).duration, 9.0)
        self.assertEqual(_props([_video(duration="-3")]).duration, 0.0)
        self.assertEqual(_props([_video(duration="N/A")]).duration, 0.0)

    def test_dimensions_swap_only_for_quarter_turn_rotation(self) -> None:
        quarter = _props([_video(tags={"rotate": "90"})])
        self.assertEqual((quarter.width, quarter.height, quarter.rotate_type), (1080, 1920, 90))
        half = _props([_video(tags={"rotate": "180"})])
        self.assertEqual((half.width, half.height), (1920, 1080))
        no_width = _props([_video(width=0, tags={"rotate": "90"})])
        self.assertEqual((no_width.width, no_width.height), (1, 1080))
        no_height = _props([_video(height=0, tags={"rotate": "90"})])
        self.assertEqual((no_height.width, no_height.height), (1920, 1))
        missing = _props([{"codec_type": "video"}])
        self.assertEqual((missing.width, missing.height), (1, 1))

    def test_fps_prefers_average_over_nominal_rate(self) -> None:
        for avg, nominal, expected in [
            ("60/1", "30/1", 60.0),
            ("0/0", "25/1", 25.0),
            ("0", "25/1", 25.0),
            (None, "25/1", 25.0),
            ("0/0", None, 30.0),
        ]:
            with self.subTest(avg=avg, nominal=nominal):
                stream = _video(**{k: v for k, v in (("avg_frame_rate", avg), ("r_frame_rate", nominal)) if v is not None})
                self.assertEqual(_props([stream]).fps, expected)

    def test_attached_picture_is_skipped_unless_only_video(self) -> None:
        cover = _video(codec_name="mjpeg", disposition={"attached_pic": 1})
        self.assertEqual(_props([cover, _video(codec_name="h264")]).codec, "h264")
        self.assertEqual(_props([cover]).codec, "mjpeg")


if __name__ == "__main__":
    unittest.main()
