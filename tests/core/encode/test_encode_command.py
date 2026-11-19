"""재인코딩 계획의 컨테이너, 스트림 처리 규칙, 처리 표식 태그, FFmpeg 인자 구성을 검증하는 단위 테스트"""

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from src.core.app_version import APP_VERSION
from src.core.encode.encode_plan import (
    ACTION_COPY,
    ACTION_DROP,
    ACTION_ENCODE,
    ACTION_TRANSCODE_AAC,
    ACTION_TRANSCODE_MOV_TEXT,
    CONTAINER_MKV,
    CONTAINER_MP4,
    DecodeMode,
    EncodePlan,
    build_encode_plan,
    choose_container,
)
from src.core.encode.ffmpeg_command import build_ffmpeg_args, resolve_ffmpeg_executable
from src.core.encode.stream_layout import StreamLayout, parse_stream_layout
from src.core.encode_tag import ENCODE_TAG_KEY, build_encode_tag, parse_encode_tag

_TAG_TIME = datetime(2026, 11, 18, 5, 0, 0, tzinfo=timezone.utc)
_HEVC_VIDEO: dict[str, object] = {"codec_type": "video", "codec_name": "hevc", "field_order": "progressive",
               "color_primaries": "bt709", "color_transfer": "bt709", "color_space": "bt709", "color_range": "tv"}
_AAC: dict[str, object] = {"codec_type": "audio", "codec_name": "aac"}
_WMA: dict[str, object] = {"codec_type": "audio", "codec_name": "wmav2"}
_TEXT_SUB: dict[str, object] = {"codec_type": "subtitle", "codec_name": "subrip"}
_BITMAP_SUB: dict[str, object] = {"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"}
_FONT: dict[str, object] = {"codec_type": "attachment", "codec_name": "ttf"}
_DATA: dict[str, object] = {"codec_type": "data", "codec_name": "bin_data"}
_COVER: dict[str, object] = {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}}


def _layout(*streams: dict[str, object]) -> StreamLayout:
    indexed: list[dict[str, object]] = [{"index": i, **stream} for i, stream in enumerate(streams)]
    return parse_stream_layout({"streams": indexed})


def _plan(layout: StreamLayout, *, source_ext: str = ".mp4", encoder: str = "hevc_nvenc",
          quality: float = 23.9, container_policy: str = "keep", maxrate: int | None = None, fps: float = 29.97):
    return build_encode_plan(
        source_path="videos/a.mp4",
        part_path="videos/a.mp4.part",
        output_path="videos/a.mp4",
        source_ext=source_ext,
        encoder=encoder,
        quality=quality,
        fps=fps,
        layout=layout,
        container_policy=container_policy,
        tag_value=build_encode_tag(encoder, quality, _TAG_TIME),
        maxrate_kbps=maxrate,
    )


class TestContainerAndStreams(unittest.TestCase):
    """결정 8의 컨테이너 선택과 스트림 처리 표"""

    def test_container_keeps_original_and_moves_other_formats_to_mp4(self) -> None:
        """MP4, MKV, MOV는 원본 형식을 유지하고 WMV 등은 MP4로 바뀜을 검증"""
        layout = _layout(_HEVC_VIDEO)
        self.assertEqual(choose_container(".mkv", layout, "keep"), "mkv")
        self.assertEqual(choose_container(".MOV", layout, "keep"), "mov")
        self.assertEqual(choose_container(".wmv", layout, "keep"), CONTAINER_MP4)
        self.assertEqual(choose_container(".mkv", layout, "mp4"), CONTAINER_MP4)

    def test_bitmap_subtitle_forces_mkv_even_for_mp4_policy(self) -> None:
        """비트맵 자막이 있으면 항상 MKV이고, 텍스트 자막은 MP4에서 mov_text로 변환됨을 검증"""
        self.assertEqual(choose_container(".mp4", _layout(_HEVC_VIDEO, _BITMAP_SUB), "mp4"), CONTAINER_MKV)
        text = _plan(_layout(_HEVC_VIDEO, _TEXT_SUB))
        self.assertEqual(text.streams[1].action, ACTION_TRANSCODE_MOV_TEXT)

    def test_audio_incompatible_with_container_is_transcoded(self) -> None:
        """WMA는 AAC로 바꾸고 AAC는 그대로 복사함을 검증"""
        plan = _plan(_layout(_HEVC_VIDEO, _WMA, _AAC))
        self.assertEqual(plan.streams[1].action, ACTION_TRANSCODE_AAC)
        self.assertEqual(plan.streams[2].action, ACTION_COPY)

    def test_font_attachment_is_copied_only_into_mkv(self) -> None:
        """글꼴 첨부는 MKV에서만 복사하고 MP4에서는 사유와 함께 제외함을 검증"""
        mkv = _plan(_layout(_HEVC_VIDEO, _WMA, _FONT), source_ext=".mkv")
        self.assertEqual(mkv.container, CONTAINER_MKV)
        self.assertEqual(mkv.streams[1].action, ACTION_TRANSCODE_AAC)
        self.assertEqual(mkv.streams[2].action, ACTION_COPY)
        mp4 = _plan(_layout(_HEVC_VIDEO, _FONT), source_ext=".mp4")
        self.assertEqual(mp4.streams[1].action, ACTION_DROP)
        self.assertIn("첨부", mp4.streams[1].reason)

    def test_cover_is_dropped_with_reason_in_mp4_and_mov_only(self) -> None:
        """MP4와 MOV 출력은 커버를 제외하고 사유를 남기며, MKV 출력은 커버를 복사함을 검증"""
        for ext in (".mp4", ".mov"):
            plan = _plan(_layout(_HEVC_VIDEO, _COVER), source_ext=ext)
            cover = plan.streams[1]
            self.assertEqual(cover.action, ACTION_DROP)
            self.assertIn("커버", cover.reason)
            self.assertNotIn("-disposition:v:1", build_ffmpeg_args(plan, "ffmpeg-custom"))
        mkv = _plan(_layout(_HEVC_VIDEO, _COVER), source_ext=".mkv")
        self.assertEqual(mkv.streams[1].action, ACTION_COPY)

    def test_cover_is_copied_and_data_and_extra_video_are_dropped(self) -> None:
        """커버는 복사하고, 데이터 스트림과 추가 영상은 제외 사유를 남김을 검증"""
        plan = _plan(_layout(_HEVC_VIDEO, _COVER, _DATA, {"codec_type": "video", "codec_name": "h264"}), source_ext=".mkv")
        actions = {s.index: s for s in plan.streams}
        self.assertEqual(actions[0].action, ACTION_ENCODE)
        self.assertEqual(actions[1].action, ACTION_COPY)
        self.assertTrue(actions[1].attached_pic)
        self.assertEqual(actions[2].action, ACTION_DROP)
        self.assertEqual(actions[3].action, ACTION_DROP)

    def test_plan_rejects_unavailable_encoder_and_missing_video(self) -> None:
        """사용 불가 인코더나 주 영상이 없으면 계획을 만들지 않음(ValueError)을 검증"""
        with self.assertRaises(ValueError):
            _plan(_layout(_HEVC_VIDEO), encoder="av1_nvenc")
        with self.assertRaises(ValueError):
            _plan(_layout(_AAC))


class TestEncodeTag(unittest.TestCase):
    """처리 표식 태그의 값 형식과 읽기 규칙(대소문자 무관, 모르는 필드 무시)"""

    def test_build_uses_fixed_field_order_and_one_decimal_quality(self) -> None:
        """값이 v, enc, q, at 순서이고 품질값이 소수 첫째 자리로 기록됨을 검증"""
        self.assertEqual(
            build_encode_tag("hevc_nvenc", 23.9, _TAG_TIME),
            f"v=1;enc=hevc_nvenc;q=23.9;at=2026-11-18T05:00:00Z;app={APP_VERSION}",
        )

    def test_parse_ignores_key_case_and_unknown_fields(self) -> None:
        """키 대소문자와 무관하게 읽고, 모르는 필드는 무시하며, 버전이 달라도 표식으로 인정됨을 검증"""
        tag = parse_encode_tag({"VIDEO_SCRIPTS_ENCODE": "v=2;enc=libx265;q=22.0;at=x;extra=1"})
        assert tag is not None
        self.assertEqual(tag.version, 2)
        self.assertEqual(tag.encoder, "libx265")
        broken = parse_encode_tag({ENCODE_TAG_KEY: "v=bad"})
        assert broken is not None
        self.assertEqual(broken.version, 0)

    def test_missing_key_is_not_a_marker(self) -> None:
        """표식 키가 없으면 None이고 comment 같은 다른 키는 표식으로 보지 않음을 검증"""
        self.assertIsNone(parse_encode_tag({"comment": "v=1;enc=hevc_nvenc"}))


class TestFfmpegArguments(unittest.TestCase):
    """계획을 FFmpeg 인자로 바꾸는 규칙(4.3절 인자, 결정 7, 결정 8, 결정 27, 결정 9 표식)"""

    def _args(self, plan: EncodePlan) -> list[str]:
        return build_ffmpeg_args(plan, "ffmpeg-custom")

    def test_nvenc_quality_keyframe_and_gop_arguments(self) -> None:
        """NVENC 인자에 소수 품질값, 자동 레벨, 1초 키프레임, passthrough가 들어감을 검증"""
        args = self._args(_plan(_layout(_HEVC_VIDEO), quality=23.9, fps=29.97))
        self.assertEqual(args[0], "ffmpeg-custom")
        self.assertEqual(args[args.index("-cq:v:0") + 1], "23.9")
        self.assertEqual(args[args.index("-level:v:0") + 1], "auto")
        self.assertEqual(args[args.index("-g:v:0") + 1], "30")
        self.assertNotIn("-force_key_frames", args)
        self.assertEqual(args[args.index("-fps_mode") + 1], "passthrough")

    def test_hevc_mp4_gets_hvc1_tag_and_metadata_flag(self) -> None:
        """HEVC MP4 출력은 hvc1 태그와 use_metadata_tags 플래그로 표식을 기록함을 검증"""
        args = self._args(_plan(_layout(_HEVC_VIDEO)))
        self.assertEqual(args[args.index("-tag:v:0") + 1], "hvc1")
        self.assertIn("+use_metadata_tags", args)
        self.assertIn(f"video_scripts_encode=v=1;enc=hevc_nvenc;q=23.9;at=2026-11-18T05:00:00Z;app={APP_VERSION}", args)
        self.assertEqual(args[args.index("-f") + 1], "mp4")

    def test_mkv_uses_uppercase_metadata_key_without_movflags(self) -> None:
        """MKV 출력은 대문자 키를 쓰고 use_metadata_tags 플래그를 쓰지 않음을 검증"""
        args = self._args(_plan(_layout(_HEVC_VIDEO), source_ext=".mkv"))
        self.assertNotIn("-movflags", args)
        self.assertTrue(any(a.startswith("VIDEO_SCRIPTS_ENCODE=") for a in args))
        self.assertEqual(args[args.index("-f") + 1], "matroska")

    def test_mp4_disables_timecode_track_but_mkv_does_not(self) -> None:
        """MP4 출력은 타임코드 트랙을 끄고(데이터 스트림 제외 규칙), MKV 출력에는 넣지 않음을 검증"""
        mp4 = self._args(_plan(_layout(_HEVC_VIDEO)))
        self.assertEqual(mp4[mp4.index("-write_tmcd") + 1], "0")
        mkv = self._args(_plan(_layout(_HEVC_VIDEO), source_ext=".mkv"))
        self.assertNotIn("-write_tmcd", mkv)

    def test_h264_and_x265_do_not_get_hevc_tag(self) -> None:
        """HEVC가 아닌 인코더에는 hvc1 태그를 붙이지 않고, libx265 품질은 -crf:v로 씀을 검증"""
        h264 = self._args(_plan(_layout(_HEVC_VIDEO), encoder="h264_nvenc"))
        self.assertNotIn("-tag:v:0", h264)
        x265 = self._args(_plan(_layout(_HEVC_VIDEO), encoder="libx265", quality=22.0))
        self.assertEqual(x265[x265.index("-crf:v:0") + 1], "22.0")
        self.assertNotIn("-level:v:0", x265)

    def test_maxrate_for_nvenc_sets_zero_bitrate_and_double_bufsize(self) -> None:
        """축소 경로의 NVENC는 -b:v 0과 -maxrate, 2배 -bufsize를 쓰고, libx265는 -b:v를 쓰지 않음을 검증"""
        nvenc = self._args(_plan(_layout(_HEVC_VIDEO), maxrate=8000))
        self.assertEqual(nvenc[nvenc.index("-b:v:0") + 1], "0")
        self.assertEqual(nvenc[nvenc.index("-maxrate:v:0") + 1], "8000k")
        self.assertEqual(nvenc[nvenc.index("-bufsize:v:0") + 1], "16000k")
        x265 = self._args(_plan(_layout(_HEVC_VIDEO), encoder="libx265", maxrate=8000))
        self.assertNotIn("-b:v:0", x265)

    def test_decode_options_follow_shana_default_only_for_hw_codecs(self) -> None:
        """샤나 기본 디코딩은 HEVC에만 하드웨어 자동을 쓰고 H.264에는 쓰지 않으며, 스레드 옵션은 항상 붙음을 검증"""
        hevc_args = self._args(_plan(_layout(_HEVC_VIDEO)))
        self.assertLess(hevc_args.index("-hwaccel"), hevc_args.index("-i"))
        h264_plan = _plan(_layout({**_HEVC_VIDEO, "codec_name": "h264"}))
        self.assertIs(h264_plan.decode_mode, DecodeMode.SHANA_DEFAULT)
        h264_args = self._args(h264_plan)
        self.assertNotIn("-hwaccel", h264_args)
        self.assertEqual(h264_args[h264_args.index("-threads") + 1], "0")

    def test_cpu_only_decode_never_uses_hardware_decoding(self) -> None:
        """하드웨어 디코딩 실패 후 재시도 구성은 HEVC라도 하드웨어 자동 옵션을 붙이지 않음"""
        plan = replace(_plan(_layout(_HEVC_VIDEO)), decode_mode=DecodeMode.CPU_ONLY)
        args = self._args(plan)
        self.assertNotIn("-hwaccel", args)
        self.assertEqual(args[args.index("-threads") + 1], "0")

    def test_ffmpeg_executable_falls_back_to_path_name_when_blank(self) -> None:
        self.assertEqual(resolve_ffmpeg_executable("  "), "ffmpeg")
        self.assertEqual(resolve_ffmpeg_executable(" C:/tools/ffmpeg.exe "), "C:/tools/ffmpeg.exe")

    def test_stream_specifiers_follow_output_order(self) -> None:
        """커버, 오디오, 자막의 출력 스트림 지정자가 종류별 순서로 붙음을 검증"""
        args = self._args(_plan(_layout(_HEVC_VIDEO, _COVER, _AAC, _TEXT_SUB), source_ext=".mkv"))
        self.assertEqual(args[args.index("-disposition:v:1") + 1], "attached_pic")
        self.assertEqual(args[args.index("-c:a:0") + 1], ACTION_COPY)
        mp4_args = self._args(_plan(_layout(_HEVC_VIDEO, _AAC, _TEXT_SUB)))
        self.assertEqual(mp4_args[mp4_args.index("-c:s:0") + 1], "mov_text")

    def test_encoder_options_are_scoped_to_primary_video_stream(self) -> None:
        """인코딩 옵션은 0번 영상 출력에만 붙어 커버 복사 스트림에 적용되지 않음을 검증"""
        args = self._args(_plan(_layout(_HEVC_VIDEO, _COVER), source_ext=".mkv"))
        self.assertNotIn("-level:v", args)
        self.assertNotIn("-preset:v", args)
        self.assertEqual(args[args.index("-preset:v:0") + 1], "p4")
        self.assertEqual(args[args.index("-c:v:1") + 1], ACTION_COPY)

    def test_color_flags_are_passed_when_known(self) -> None:
        """색 정보가 있는 스트림은 출력에 명시 전달됨을 검증"""
        args = self._args(_plan(_layout(_HEVC_VIDEO)))
        self.assertEqual(args[args.index("-color_primaries:v:0") + 1], "bt709")
        self.assertEqual(args[args.index("-color_range:v:0") + 1], "tv")


if __name__ == "__main__":
    unittest.main(verbosity=2)
