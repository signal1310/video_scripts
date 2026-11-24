"""내부 요소 영향 집계(encode_impact)와 mov_text 사유 기록 단위 테스트"""

import unittest
from typing import Any

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
    IMPACT_LABELS,
    IMPACT_SUBTITLE_MOV_TEXT,
    IMPACT_TEN_BIT_TO_EIGHT,
    IMPACT_VR_STEREO_LOST,
    parse_stream_summary_layout,
    summarize_encode_impact,
)
from src.core.encode.encode_plan import ACTION_TRANSCODE_MOV_TEXT, choose_container, plan_stream_actions
from src.core.probe_parser import summarize_streams_from_probe
from tests._factories import make_video


def _video_with_side_data(side_data_type: str) -> dict[str, Any]:
    """부가 정보 한 개를 가진 영상 스트림 원시 딕셔너리"""
    return {
        "codec_type": "video",
        "codec_name": "h264",
        "side_data_list": [{"side_data_type": side_data_type}],
    }


def _video_with_field_order(field_order: str) -> dict[str, object]:
    """필드 순서를 가진 영상 스트림 원시 딕셔너리"""
    return {"codec_type": "video", "codec_name": "h264", "field_order": field_order}


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
        summary = summarize_encode_impact(videos, CONTAINER_POLICY_MP4, "h264")
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

    def test_vr_stereo_lost_counts_side_data_and_manual_protection_in_mp4(self) -> None:
        """구면 투영 부가 정보나 수동 VR 보호가 있고 MP4로 출력하면 VR 입체 정보 소실로 집계"""
        vr_summary = summarize_streams_from_probe(
            {"streams": [_video_with_side_data("Spherical Mapping")]}
        )
        videos = [
            make_video(filename="vr_meta.mp4", stream_summary=vr_summary),
            make_video(
                filename="manual.mp4",
                stream_summary=summarize_streams_from_probe({"streams": [{"codec_type": "video", "codec_name": "h264"}]}),
            ),
        ]
        summary = summarize_encode_impact(
            videos, CONTAINER_POLICY_MP4, "h264", vr_filenames=frozenset({"manual.mp4"})
        )
        bucket = next(b for b in summary.buckets if b.key == IMPACT_VR_STEREO_LOST)
        self.assertEqual(bucket.filenames, ("vr_meta.mp4", "manual.mp4"))
        layout = parse_stream_summary_layout(vr_summary)
        self.assertIsNotNone(layout)
        assert layout is not None
        self.assertTrue(layout.is_vr)
        self.assertFalse(layout.is_interlaced)

    def test_stream_summary_layout_reports_interlaced_video(self) -> None:
        """저장된 스트림 요약의 필드 순서로 인터레이스 원본을 판정하는지 검증"""
        summary = summarize_streams_from_probe({"streams": [_video_with_field_order("tt")]})
        layout = parse_stream_summary_layout(summary)
        assert layout is not None
        self.assertTrue(layout.is_interlaced)
        progressive = summarize_streams_from_probe(
            {"streams": [_video_with_field_order("progressive")]}
        )
        progressive_layout = parse_stream_summary_layout(progressive)
        assert progressive_layout is not None
        self.assertFalse(progressive_layout.is_interlaced)

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
        container = choose_container(".mp4", layout, CONTAINER_POLICY_KEEP, "h264")
        actions = plan_stream_actions(layout, container)
        sub_action = actions[2]
        self.assertEqual(sub_action.action, ACTION_TRANSCODE_MOV_TEXT)
        self.assertIn("서식 손실", sub_action.reason)

        self.assertIsNone(parse_stream_summary_layout("invalid-json"))
        self.assertIsNone(parse_stream_summary_layout("[]"))


class TestImpactLabels(unittest.TestCase):
    """내부 요소 집계 항목 이름이 승인된 문구와 같은지 검증"""

    def test_labels_match_approved_wording(self) -> None:
        """재인코딩 시작 확인 창에 쓰는 항목 이름이 승인 문구(11.1절)와 일치하는지 검증"""
        self.assertEqual(
            [label for _key, label in IMPACT_LABELS],
            [
                "커버 유지",
                "탐색기 태그와 별점 보존 대상",
                "텍스트 자막 서식 손실",
                "비트맵 자막 때문에 MKV로 출력",
                "첨부 파일 제외",
                "추가 영상 스트림 제외",
                "데이터 스트림 제외",
                "오디오 AAC로 변환",
                "출력 형식 변경",
                "10비트를 8비트로 변환",
                "VR 입체 정보 소실",
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
