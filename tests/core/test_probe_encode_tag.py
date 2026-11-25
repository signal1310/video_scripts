"""ffprobe 컨테이너 태그에서 인코딩 기록(이미 인코딩 판정 근거)을 읽는 프로빙 파싱 검증"""

import unittest
from typing import Any

from src.core.analysis.prober import (
    FFPROBE_DEFAULT_EXTRA_ARGS,
    FFPROBE_KEYFRAME_EXTRA_ARGS,
    FFPROBE_STREAM_FORMAT_ENTRY_SPEC,
)
from src.core.encode_tag import ENCODE_TAG_KEY
from src.core.probe_parser import build_video_props

_VALID_TAG = "v=1;enc=hevc_nvenc;q=22.0;at=2026-11-18T00:00:00Z"
_FILE_SIZE_BYTES = 1_000_000


def _probe(tags: Any = None) -> dict[str, Any]:
    """최소 비디오 스트림과 컨테이너 태그를 가진 ffprobe JSON 생성, tags가 None이면 태그 블록 없음"""
    fmt: dict[str, Any] = {"duration": "10.0", "size": str(_FILE_SIZE_BYTES), "bit_rate": "800000"}
    if tags is not None:
        fmt["tags"] = tags
    stream: dict[str, Any] = {
        "index": 0,
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "avg_frame_rate": "30/1",
        "r_frame_rate": "30/1",
        "bit_rate": "700000",
        "duration": "10.0",
    }
    return {"streams": [stream], "format": fmt}


def _has_tag(tags: Any = None) -> bool:
    return build_video_props("sample.mp4", _FILE_SIZE_BYTES, _probe(tags)).has_encode_tag


class TestEncodeTagQuery(unittest.TestCase):
    """두 조회 경로가 모두 표식 키를 요청하는지 검증"""

    def test_stream_format_spec_requests_encode_tag_key(self) -> None:
        self.assertIn(f"format_tags={ENCODE_TAG_KEY}", FFPROBE_STREAM_FORMAT_ENTRY_SPEC)

    def test_both_probe_argument_sets_request_encode_tag_key(self) -> None:
        self.assertIn(f"format_tags={ENCODE_TAG_KEY}", " ".join(FFPROBE_DEFAULT_EXTRA_ARGS))
        self.assertIn(f"format_tags={ENCODE_TAG_KEY}", " ".join(FFPROBE_KEYFRAME_EXTRA_ARGS))


class TestEncodeTagParsing(unittest.TestCase):
    """컨테이너 태그 사전에서 표식 유무를 판별하는 규칙 검증"""

    def test_mp4_lowercase_key_is_read(self) -> None:
        self.assertTrue(_has_tag({ENCODE_TAG_KEY: _VALID_TAG}))

    def test_mkv_uppercase_key_is_read(self) -> None:
        self.assertTrue(_has_tag({"VIDEO_SCRIPTS_ENCODE": _VALID_TAG}))

    def test_unknown_version_and_fields_still_count_as_tag(self) -> None:
        self.assertTrue(_has_tag({ENCODE_TAG_KEY: "v=9;future=x;enc=av1_nvenc"}))

    def test_key_with_empty_value_counts_as_tag(self) -> None:
        """키 존재를 기준으로 재처리를 막으며, 값이 비어 있어도 표식으로 인정"""
        self.assertTrue(_has_tag({ENCODE_TAG_KEY: ""}))

    def test_missing_tag_block_is_not_tag(self) -> None:
        self.assertFalse(_has_tag(None))

    def test_other_tags_only_are_not_tag(self) -> None:
        self.assertFalse(_has_tag({"comment": "v=1;enc=hevc_nvenc", "title": "clip"}))

    def test_non_mapping_tag_block_is_ignored(self) -> None:
        self.assertFalse(_has_tag("broken"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
