"""인코딩 계획(encode_plan)의 컨테이너, 스트림 처리, 커버 유지 규칙 단위 테스트"""

import unittest

from src.core.config import CONTAINER_POLICY_KEEP, CONTAINER_POLICY_MP4
from src.core.encode.encode_plan import (
    ACTION_COPY,
    ACTION_DROP,
    ACTION_ENCODE,
    ACTION_TRANSCODE_AAC,
    ACTION_TRANSCODE_MOV_TEXT,
    CONTAINER_MKV,
    CONTAINER_MP4,
    build_encode_plan,
    choose_container,
    plan_stream_actions,
)
from src.core.encode.stream_layout import parse_stream_layout

_VIDEO: dict[str, object] = {"codec_type": "video", "codec_name": "h264", "field_order": "progressive"}
_COVER: dict[str, object] = {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}}
_PGS: dict[str, object] = {"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"}
_ASS: dict[str, object] = {"codec_type": "subtitle", "codec_name": "ass"}
_FLAC: dict[str, object] = {"codec_type": "audio", "codec_name": "flac"}
_FONT: dict[str, object] = {"codec_type": "attachment", "codec_name": "ttf"}
_DATA: dict[str, object] = {"codec_type": "data", "codec_name": "bin_data"}


class TestEncodePlan(unittest.TestCase):
    """컨테이너 선택과 스트림 처리 규칙을 검증"""

    def test_choose_container_respects_bitmap_subtitles_and_policy(self) -> None:
        self.assertEqual(choose_container(".mp4", parse_stream_layout({"streams": [{**_VIDEO, "index": 0}, {**_PGS, "index": 1}]}), CONTAINER_POLICY_MP4, "hevc"), CONTAINER_MKV)
        self.assertEqual(choose_container(".mov", parse_stream_layout({"streams": [{**_VIDEO, "index": 0}]}), CONTAINER_POLICY_KEEP, "hevc"), "mov")
        self.assertEqual(choose_container(".avi", parse_stream_layout({"streams": [{**_VIDEO, "index": 0}]}), CONTAINER_POLICY_KEEP, "hevc"), CONTAINER_MP4)

    def test_plan_stream_actions_keeps_cover_in_mp4_mov_and_mkv(self) -> None:
        """MP4, MOV, MKV 모두에서 커버를 복사하고, 첨부와 데이터는 제외함을 검증"""
        layout = parse_stream_layout({
            "streams": [
                {**_VIDEO, "index": 0},
                {**_COVER, "index": 1},
                {**_FLAC, "index": 2},
                {**_ASS, "index": 3},
                {**_FONT, "index": 4},
                {**_DATA, "index": 5},
            ]
        })
        mp4_actions = {a.index: a for a in plan_stream_actions(layout, CONTAINER_MP4)}
        self.assertEqual(mp4_actions[0].action, ACTION_ENCODE)
        self.assertEqual(mp4_actions[1].action, ACTION_COPY)
        self.assertTrue(mp4_actions[1].attached_pic)
        self.assertEqual(mp4_actions[2].action, ACTION_TRANSCODE_AAC)
        self.assertEqual(mp4_actions[3].action, ACTION_TRANSCODE_MOV_TEXT)
        self.assertEqual(mp4_actions[4].action, ACTION_DROP)
        self.assertEqual(mp4_actions[5].action, ACTION_DROP)

    def test_build_encode_plan_populates_tag_fields(self) -> None:
        layout = parse_stream_layout({"streams": [{**_VIDEO, "index": 0}]})
        plan = build_encode_plan(
            source_path="in.mp4",
            part_path="out.mp4.part",
            output_path="out.mp4",
            source_ext=".mp4",
            encoder="hevc_nvenc",
            quality=22.0,
            fps=30.0,
            layout=layout,
            container_policy=CONTAINER_POLICY_MP4,
            tag_value="v=1",
            tag_mode="hidden",
            tag_name="custom_tag",
            tag_keywords=("가족",),
        )
        self.assertEqual(plan.tag_mode, "hidden")
        self.assertEqual(plan.tag_name, "custom_tag")
        self.assertEqual(plan.tag_keywords, ("가족",))
        self.assertEqual(plan.gop_frames, 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
