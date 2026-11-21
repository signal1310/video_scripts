"""FFmpeg 명령 인자 생성(ffmpeg_command)의 기록 방식 3종과 컨테이너 3종 조합 단위 테스트"""

import unittest

from src.core.config import CONTAINER_POLICY_KEEP
from src.core.encode.encode_plan import build_encode_plan
from src.core.encode.ffmpeg_command import build_ffmpeg_args, build_sample_ffmpeg_args
from src.core.encode.stream_layout import parse_stream_layout
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN, ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE

_LAYOUT = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}]})


def _make_plan(ext: str, mode: str, keywords: tuple[str, ...], detail: str) -> list[str]:
    plan = build_encode_plan(
        source_path=f"in{ext}",
        part_path=f"out{ext}.part",
        output_path=f"out{ext}",
        source_ext=ext,
        encoder="libx264",
        quality=23.0,
        fps=30.0,
        layout=_LAYOUT,
        container_policy=CONTAINER_POLICY_KEEP,
        tag_value=detail,
        tag_mode=mode,
        tag_keywords=keywords,
    )
    return build_ffmpeg_args(plan, "ffmpeg")


class TestFfmpegTagArguments(unittest.TestCase):
    """기록 방식 3종 x 컨테이너 3종(MP4, MOV, MKV)의 FFmpeg 메타데이터 인자를 검증"""

    def test_mp4_and_mov_never_use_metadata_tags_flag_or_write_xtra_keys(self) -> None:
        """MP4와 MOV는 +use_metadata_tags와 키워드, 상세 값을 FFmpeg에 넘기지 않고 옛 표식만 지움"""
        for ext in (".mp4", ".mov"):
            for mode in (ENCODE_TAG_MODE_VISIBLE, ENCODE_TAG_MODE_HIDDEN, ENCODE_TAG_MODE_OFF):
                with self.subTest(ext=ext, mode=mode):
                    args = _make_plan(ext, mode, ("kw", "re_encoded"), "v=1;enc=libx264")
                    self.assertNotIn("-movflags", args)
                    self.assertNotIn("+use_metadata_tags", args)
                    self.assertIn("video_scripts_encode=", args)
                    self.assertFalse(any(a.startswith("KEYWORDS=") for a in args))
                    self.assertFalse(any(a.startswith("VIDEO_SCRIPTS_ENCODE=") for a in args))

    def test_mkv_writes_keywords_always_and_detail_except_off(self) -> None:
        """MKV는 항상 옛 표식을 지우고 KEYWORDS를 쓰며, 상세 값은 off일 때만 생략함을 검증"""
        vis = _make_plan(".mkv", ENCODE_TAG_MODE_VISIBLE, ("여행", "re_encoded"), "v=1;enc=libx264")
        self.assertIn("video_scripts_encode=", vis)
        self.assertIn("KEYWORDS=여행;re_encoded", vis)
        self.assertIn("VIDEO_SCRIPTS_ENCODE=v=1;enc=libx264", vis)

        hid = _make_plan(".mkv", ENCODE_TAG_MODE_HIDDEN, ("여행",), "v=1;enc=libx264")
        self.assertIn("video_scripts_encode=", hid)
        self.assertIn("KEYWORDS=여행", hid)
        self.assertIn("VIDEO_SCRIPTS_ENCODE=v=1;enc=libx264", hid)

        off = _make_plan(".mkv", ENCODE_TAG_MODE_OFF, (), "")
        self.assertIn("video_scripts_encode=", off)
        self.assertIn("KEYWORDS=", off)
        self.assertFalse(any(a.startswith("VIDEO_SCRIPTS_ENCODE=") for a in off))

    def test_build_sample_ffmpeg_args_reuses_builder_with_video_only_and_seek(self) -> None:
        """샘플 인자 빌더가 본 인코딩 인자를 재사용하고 주 영상만 -ss/-t 구간으로 자르는지 검증"""
        layout = parse_stream_layout(
            {
                "streams": [
                    {"index": 0, "codec_type": "video", "codec_name": "h264"},
                    {"index": 1, "codec_type": "audio", "codec_name": "aac"},
                ]
            }
        )
        plan = build_encode_plan(
            source_path="in.mkv",
            part_path="out.mkv.part",
            output_path="out.mkv",
            source_ext=".mkv",
            encoder="hevc_nvenc",
            quality=22.0,
            fps=30.0,
            layout=layout,
            container_policy=CONTAINER_POLICY_KEEP,
            tag_value="v=1",
        )
        args = build_sample_ffmpeg_args(
            plan, start_sec=12.5, duration_sec=2.0, part_path="seg.mp4", quality=24.3
        )
        self.assertIn("-ss", args)
        self.assertIn("12.500", args)
        self.assertIn("-t", args)
        self.assertIn("2.000", args)
        self.assertIn("24.3", args)
        self.assertEqual(args.count("-map"), 1)
        self.assertNotIn("0:1", args)
        self.assertEqual(args[-1], "seg.mp4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
