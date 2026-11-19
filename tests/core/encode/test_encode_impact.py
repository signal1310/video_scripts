"""내부 요소 영향 집계(encode_impact)와 mov_text 사유 기록 단위 테스트"""

import unittest

from src.core.config import CONTAINER_POLICY_KEEP, CONTAINER_POLICY_MP4
from src.core.encode.encode_impact import (
    IMPACT_ATTACHMENT_DROPPED,
    IMPACT_AUDIO_AAC,
    IMPACT_BITMAP_SUB_MKV,
    IMPACT_CONTAINER_CHANGED,
    IMPACT_COVER_KEPT,
    IMPACT_DATA_DROPPED,
    IMPACT_EXPLORER_TAGS_PRESERVED,
    IMPACT_EXTRA_VIDEO_DROPPED,
    IMPACT_SUBTITLE_MOV_TEXT,
    IMPACT_TEN_BIT_TO_EIGHT,
    parse_stream_summary_layout,
    summarize_encode_impact,
)
from src.core.encode.encode_plan import ACTION_TRANSCODE_MOV_TEXT, choose_container, plan_stream_actions
from src.core.probe_parser import summarize_streams_from_probe
from tests._factories import make_video


class TestEncodeImpact(unittest.TestCase):
    """합성 스트림 요약 항목별 집계, 빈 요약 건너뜀, 실제 인코딩 계획 일치를 검증"""

    def test_aggregates_all_impact_items_and_skips_missing_summary(self) -> None:
        mp4_summary = summarize_streams_from_probe({
            "streams": [
                {"codec_type": "video", "codec_name": "hevc"},
                {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "flac"},
                {"codec_type": "subtitle", "codec_name": "ass"},
                {"codec_type": "attachment", "codec_name": "ttf"},
                {"codec_type": "data", "codec_name": "bin_data"},
            ]
        })
        wmv_bitmap_summary = summarize_streams_from_probe({
            "streams": [
                {"codec_type": "video", "codec_name": "wmv2"},
                {"codec_type": "audio", "codec_name": "wmav2"},
                {"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"},
            ]
        })
        videos = [
            make_video(filename="rich.mp4", is_10bit=True, stream_summary=mp4_summary),
            make_video(filename="bitmap.wmv", is_10bit=False, stream_summary=wmv_bitmap_summary),
            make_video(filename="legacy.mp4", stream_summary=""),
        ]
        summary = summarize_encode_impact(videos, CONTAINER_POLICY_MP4)
        self.assertEqual(summary.total_count, 3)
        self.assertEqual(summary.analyzed_count, 2)
        self.assertEqual(summary.unknown_count, 1)
        self.assertEqual(summary.unknown_filenames, ("legacy.mp4",))

        by_key = {b.key: b.filenames for b in summary.buckets}
        by_count = {b.key: b.count for b in summary.buckets}
        self.assertEqual(by_count[IMPACT_AUDIO_AAC], 2)
        self.assertEqual(by_key[IMPACT_COVER_KEPT], ("rich.mp4",))
        self.assertEqual(by_key[IMPACT_EXPLORER_TAGS_PRESERVED], ("rich.mp4",))
        self.assertEqual(by_key[IMPACT_SUBTITLE_MOV_TEXT], ("rich.mp4",))
        self.assertEqual(by_key[IMPACT_BITMAP_SUB_MKV], ("bitmap.wmv",))
        self.assertEqual(by_key[IMPACT_ATTACHMENT_DROPPED], ("rich.mp4",))
        self.assertEqual(by_key[IMPACT_EXTRA_VIDEO_DROPPED], ("rich.mp4",))
        self.assertEqual(by_key[IMPACT_DATA_DROPPED], ("rich.mp4",))
        self.assertEqual(by_key[IMPACT_AUDIO_AAC], ("rich.mp4", "bitmap.wmv"))
        self.assertEqual(by_key[IMPACT_CONTAINER_CHANGED], ("bitmap.wmv",))
        self.assertEqual(by_key[IMPACT_TEN_BIT_TO_EIGHT], ("rich.mp4",))

    def test_matches_real_encode_plan_and_records_mov_text_reason(self) -> None:
        """집계에 쓴 스트림 요약이 실제 choose_container, plan_stream_actions 결과와 같고 mov_text 사유가 기록됨을 검증"""
        raw_summary = summarize_streams_from_probe({
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "subtitle", "codec_name": "srt"},
            ]
        })
        layout = parse_stream_summary_layout(raw_summary)
        assert layout is not None
        container = choose_container(".mp4", layout, CONTAINER_POLICY_KEEP)
        actions = plan_stream_actions(layout, container)
        sub_action = actions[2]
        self.assertEqual(sub_action.action, ACTION_TRANSCODE_MOV_TEXT)
        self.assertIn("서식 손실", sub_action.reason)

        self.assertIsNone(parse_stream_summary_layout("invalid-json"))
        self.assertIsNone(parse_stream_summary_layout("[]"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
