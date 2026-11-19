"""ffprobe 스트림 목록 파서의 종류 분류, 인터레이스, 회전, 색 정보, VR 부가 정보 판정을 검증하는 단위 테스트"""

import unittest

from src.core.encode.stream_layout import StreamLayout, parse_stream_layout


class TestStreamLayoutParsing(unittest.TestCase):
    """스트림 목록 JSON을 구조로 바꾸는 규칙을 검증"""

    def test_primary_video_skips_attached_cover_and_keeps_color_info(self) -> None:
        """커버 이미지를 주 영상으로 고르지 않고 색 정보와 회전을 주 영상에서 읽음을 검증"""
        layout = parse_stream_layout({
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264", "field_order": "progressive",
                 "color_primaries": "bt709", "color_transfer": "bt709", "color_space": "bt709",
                 "color_range": "tv", "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]},
                {"index": 1, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
            ]
        })
        primary = layout.primary_video
        assert primary is not None
        self.assertEqual(primary.index, 0)
        self.assertEqual(primary.rotation, -90)
        self.assertEqual(primary.color_space, "bt709")
        self.assertFalse(primary.is_interlaced)

    def test_interlaced_field_orders_are_detected(self) -> None:
        """tt, bb, tb, bt는 인터레이스이고 progressive와 unknown, 값 없음은 진행 대상임을 검증"""
        for order, expected in (("tt", True), ("bb", True), ("tb", True), ("bt", True),
                                ("progressive", False), ("unknown", False), ("", False)):
            with self.subTest(order=order):
                layout = parse_stream_layout({"streams": [
                    {"index": 0, "codec_type": "video", "codec_name": "h264", "field_order": order}
                ]})
                primary = layout.primary_video
                assert primary is not None
                self.assertEqual(primary.is_interlaced, expected)

    def test_vr_side_data_marks_primary_video(self) -> None:
        """구면 투영 또는 입체 부가 정보가 있으면 VR, 없으면 일반 영상임을 검증"""
        vr = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc",
                                               "side_data_list": [{"side_data_type": "Spherical Mapping"}]}]})
        stereo = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc",
                                                   "side_data_list": [{"side_data_type": "Stereo 3D"}]}]})
        plain = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc"}]})
        self.assertTrue(vr.is_vr)
        self.assertTrue(stereo.is_vr)
        self.assertFalse(plain.is_vr)

    def test_rotation_falls_back_to_tag_and_unknown_kinds_are_skipped(self) -> None:
        """Display Matrix가 없으면 rotate 태그를 쓰고, 모르는 스트림 종류와 잘못된 항목은 제외함을 검증"""
        layout = parse_stream_layout({
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264", "tags": {"rotate": "270"}},
                {"index": 1, "codec_type": "unknown_kind", "codec_name": "x"},
                "not-a-stream",
                {"codec_type": "audio"},
            ]
        })
        self.assertEqual(len(layout.streams), 1)
        primary = layout.primary_video
        assert primary is not None
        self.assertEqual(primary.rotation, 270)

    def test_missing_streams_key_gives_empty_layout(self) -> None:
        """streams 항목이 없거나 목록이 아니면 빈 구조이며 주 영상도 없음을 검증"""
        self.assertEqual(parse_stream_layout({}), StreamLayout(streams=()))
        self.assertIsNone(parse_stream_layout({"streams": "bad"}).primary_video)

    def test_format_tags_are_extracted_from_format_block(self) -> None:
        """format.tags에서 컨테이너 태그를 함께 꺼냄을 검증"""
        layout = parse_stream_layout({
            "streams": [],
            "format": {"tags": {"KEYWORDS": "a;b", "WM/Category": "c"}},
        })
        self.assertEqual(layout.format_tags, {"KEYWORDS": "a;b", "WM/Category": "c"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
