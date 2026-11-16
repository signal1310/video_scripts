import unittest
from typing import Any

from src.core.models import VideoProps
from src.core.probe_parser import (
    KEYFRAME_PROBE_READ_WINDOW_SEC,
    KEYFRAME_PROBE_WINDOWS_SEC,
    build_video_props,
    collect_keyframe_times,
    estimate_video_bps,
    extract_rotate_info,
    is_10bit_stream,
    mean_keyframe_gap,
    measure_keyframe_interval,
    parse_audio_stream_bps,
    parse_fps,
    parse_stream_bps,
    resolve_keyframe_interval,
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

    def test_estimate_video_bps_subtracts_known_audio_and_floors_unknown(self) -> None:
        known = [((0, 100), 0), ((-5, 0), 0), ((1000, 100), 900), ((1000, 150), 850), ((1000, 300), 700), ((100, 500), 0)]
        for (total, audio), expected in known:
            with self.subTest(total=total, audio=audio):
                self.assertEqual(estimate_video_bps(total, audio, audio_known=True), expected)
        unknown = [((1000, 100), 900), ((1000, 300), 850), ((1000, 0), 1000)]
        for (total, audio), expected in unknown:
            with self.subTest(total=total, audio=audio, audio_known=False):
                self.assertEqual(estimate_video_bps(total, audio, audio_known=False), expected)

    def test_stream_bps_prefers_bit_rate_then_mkv_tags(self) -> None:
        self.assertEqual(parse_stream_bps({"bit_rate": "800000"}), 800000)
        self.assertEqual(parse_stream_bps({"tags": {"BPS": "270000"}}), 270000)
        self.assertEqual(parse_stream_bps({"bit_rate": "N/A", "tags": {"BPS-eng": "128000"}}), 128000)
        self.assertEqual(parse_stream_bps({"tags": {"bps": "1"}}), None)
        self.assertIsNone(parse_stream_bps({"bit_rate": "0"}))
        self.assertIsNone(parse_stream_bps(None))

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

    def test_mean_keyframe_gap(self) -> None:
        def gap(*times: Any) -> float | None:
            return mean_keyframe_gap(collect_keyframe_times([{"pts_time": t, "flags": "K__"} for t in times]))

        self.assertIsNone(gap())
        self.assertIsNone(gap("1.0"))
        self.assertEqual(gap("0.0", "2.0"), 2.0)
        self.assertEqual(gap("0", "2", "2", "5"), 2.5)
        self.assertIsNone(gap("5", "5"))
        self.assertEqual(gap("4", "0", "2"), 2.0)

    def test_collect_keyframe_times_ignores_non_key_and_uses_dts_fallback(self) -> None:
        """비키프레임 패킷과 flags 누락을 무시하고, pts가 N/A인 키프레임은 dts로 대체하는지 검증"""
        packets = [
            {"pts_time": "0.0", "dts_time": "-0.1", "flags": "K__"},
            {"pts_time": "1.0", "flags": "___"},
            {"pts_time": "1.5", "flags": "D__"},
            {"pts_time": "N/A", "dts_time": "3.0", "flags": "K__"},
            {"pts_time": "x", "flags": "K__"},
            {"pts_time": "9.0"},
        ]
        self.assertEqual(collect_keyframe_times(packets), [0.0, 3.0])
        self.assertEqual(mean_keyframe_gap(collect_keyframe_times(packets)), 3.0)


class TestKeyframeStaging(unittest.TestCase):
    """키프레임 단계 창 적용과 비디오 스트림 필터링을 검증"""

    def test_read_window_covers_every_stage(self) -> None:
        self.assertGreater(KEYFRAME_PROBE_READ_WINDOW_SEC, KEYFRAME_PROBE_WINDOWS_SEC[-1])

    def test_resolve_short_footage_returns_duration(self) -> None:
        self.assertEqual(resolve_keyframe_interval([], 0.8), 0.8)

    def test_resolve_uses_first_stage_with_a_gap(self) -> None:
        self.assertEqual(resolve_keyframe_interval([0.0, 2.0, 4.0, 6.0, 8.0, 10.0], 20.0), 2.0)

    def test_resolve_excludes_keyframe_exactly_at_stage_end(self) -> None:
        """단계 창 끝 시각의 키프레임은 ffprobe 구간 호출과 같이 제외하는지 검증"""
        self.assertEqual(resolve_keyframe_interval([0.0, 2.5, 3.5], 20.0), 1.75)

    def test_resolve_extends_to_wider_stage_when_first_has_one_keyframe(self) -> None:
        self.assertEqual(resolve_keyframe_interval([0.0, 3.0, 6.0], 20.0), 3.0)

    def test_resolve_returns_duration_when_stage_covers_whole_video(self) -> None:
        self.assertEqual(resolve_keyframe_interval([0.0], 4.0), 4.0)

    def test_resolve_falls_back_to_last_stage_length(self) -> None:
        self.assertEqual(resolve_keyframe_interval([], 30.0), KEYFRAME_PROBE_WINDOWS_SEC[-1])

    def test_measure_filters_to_video_stream_and_skips_cover_art(self) -> None:
        """오디오와 앨범 커버 패킷을 제외하고 실제 비디오 스트림의 키프레임으로만 간격을 산출하는지 검증"""
        streams: list[dict[str, Any]] = [
            {"index": 0, "codec_type": "video", "disposition": {"attached_pic": 1}},
            {"index": 1, "codec_type": "audio"},
            {"index": 2, "codec_type": "video", "disposition": {"attached_pic": 0}},
        ]
        packets: list[dict[str, Any]] = [
            {"stream_index": 0, "pts_time": "0.0", "flags": "K__"},
            {"stream_index": 0, "pts_time": "0.1", "flags": "K__"},
            {"stream_index": 1, "pts_time": "0.5", "flags": "K__"},
            {"stream_index": 2, "pts_time": "0.0", "flags": "K__"},
            {"stream_index": 2, "pts_time": "4.0", "flags": "K__"},
        ]
        self.assertEqual(measure_keyframe_interval({"streams": streams, "packets": packets}, 20.0), 4.0)


class TestBuildVideoProps(unittest.TestCase):
    """ffprobe JSON에서 VideoProps를 만드는 비트레이트, 크기, 회전, FPS 보정을 검증"""

    def test_size_audio_and_codec_fields(self) -> None:
        props = _props([_video(bit_rate="2000000"), {"codec_type": "audio", "bit_rate": "128000"}], size=3 * _MB)
        self.assertEqual((props.vid_size_MB, props.vid_kbps, props.aud_kbps), (3.0, 2000, 128))
        self.assertEqual(props.codec, "h264")
        self.assertIsNone(props.keyframe_interval)
        self.assertEqual(_props([{"codec_type": "video"}]).codec, "unknown")

    def test_audio_kbps_sums_every_audio_track(self) -> None:
        streams = [
            _video(bit_rate="2000000"),
            {"codec_type": "audio", "bit_rate": "128000"},
            {"codec_type": "audio", "bit_rate": "64000"},
        ]
        self.assertEqual(_props(streams).aud_kbps, 192)

    def test_hdr_detection_by_transfer_or_dolby_vision_side_data(self) -> None:
        self.assertTrue(_props([_video(color_transfer="smpte2084")]).is_hdr)
        self.assertTrue(_props([_video(color_transfer="arib-std-b67")]).is_hdr)
        self.assertTrue(
            _props([_video(side_data_list=[{"side_data_type": "DOVI configuration record"}])]).is_hdr
        )
        self.assertFalse(_props([_video(color_transfer="bt709")]).is_hdr)
        self.assertFalse(
            _props([_video(side_data_list=[{"side_data_type": "Display Matrix", "rotation": -90}])]).is_hdr
        )
        self.assertFalse(_props([]).is_hdr)

    def test_video_stream_selection_skips_non_video_streams(self) -> None:
        props = _props([{"codec_type": "audio", "codec_name": "aac"}, _video(codec_name="hevc")])
        self.assertEqual(props.codec, "hevc")

    def test_video_bitrate_fallback_chain(self) -> None:
        audio = {"codec_type": "audio", "bit_rate": "128000"}
        self.assertEqual(_props([_video(bit_rate="0"), audio], {"bit_rate": "1000000"}).vid_kbps, 872)
        heavy_audio = {"codec_type": "audio", "bit_rate": "500000"}
        self.assertEqual(_props([_video(), heavy_audio], {"bit_rate": "1000000"}).vid_kbps, 500)
        # 오디오 비트레이트를 모르면 알려진 오디오만 차감하고, 비디오는 총합의 85% 이상으로 보정
        unknown_audio = {"codec_type": "audio"}
        self.assertEqual(_props([_video(), unknown_audio], {"bit_rate": "1000000"}).vid_kbps, 1000)
        # MKV는 스트림 bit_rate가 없고 BPS 태그만 있는 경우가 많음
        mkv_tags = _props(
            [_video(tags={"BPS": "270000"}), {"codec_type": "audio", "tags": {"BPS-eng": "128000"}}],
            {"bit_rate": "391501"},
        )
        self.assertEqual((mkv_tags.vid_kbps, mkv_tags.aud_kbps), (270, 128))
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

    def test_pcm_audio_bitrate_estimation_prevents_video_overestimate(self) -> None:
        """스트림 BR 미기재 pcm_* 오디오를 샘플레이트와 채널, 비트 수로 역산해 비디오 BR을 보정함을 검증(시나리오 K)"""
        pcm_stream = {
            "codec_type": "audio",
            "codec_name": "pcm_s16le",
            "channels": 2,
            "sample_rate": "48000",
            "bits_per_sample": 16,
        }
        self.assertEqual(parse_audio_stream_bps(pcm_stream), 1_536_000)
        # bit_rate가 직접 기재된 경우 기재값 우선
        pcm_with_br = {**pcm_stream, "bit_rate": "1411200"}
        self.assertEqual(parse_audio_stream_bps(pcm_with_br), 1_411_200)
        # 메타 누락 pcm 및 비-pcm 미기재는 None 반환
        self.assertIsNone(parse_audio_stream_bps({"codec_type": "audio", "codec_name": "pcm_s16le"}))
        self.assertIsNone(parse_audio_stream_bps({"codec_type": "audio", "codec_name": "aac"}))

        props = _props([_video(), pcm_stream], {"bit_rate": "2536000"})
        self.assertEqual(props.aud_kbps, 1536)
        self.assertEqual(props.vid_kbps, 1000)

    def test_10bit_stream_detection_by_pix_fmt_and_raw_sample_bits(self) -> None:
        """10-bit 이상 픽셀 포맷(p10, p12, 10le, 10be)과 bits_per_raw_sample 판별을 검증(시나리오 J)"""
        for fmt in ("yuv420p10le", "yuv420p10be", "yuv422p12le", "p010le", "gray10be"):
            with self.subTest(pix_fmt=fmt):
                self.assertTrue(is_10bit_stream(_video(pix_fmt=fmt)))
                self.assertTrue(_props([_video(pix_fmt=fmt)]).is_10bit)
        self.assertTrue(_props([_video(pix_fmt="yuv420p", bits_per_raw_sample="10")]).is_10bit)
        self.assertFalse(_props([_video(pix_fmt="yuv420p", bits_per_raw_sample="8")]).is_10bit)
        self.assertFalse(_props([_video()]).is_10bit)
        self.assertFalse(is_10bit_stream(None))


if __name__ == "__main__":
    unittest.main()
