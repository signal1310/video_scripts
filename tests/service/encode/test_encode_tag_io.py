"""인코딩 태그 입출력(encode_tag_io)의 원본 읽기, Xtra 쓰기, moov 예외, 게이트 조회 단위 테스트"""

import os
import struct
import tempfile
import unittest

from src.core.config import ClassifierConfig
from src.core.encode.encode_gate import GatePurpose, OutputSummary
from src.core.encode.encode_plan import CONTAINER_MKV, CONTAINER_MP4
from src.core.encode.stream_layout import parse_stream_layout
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN, ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE
from src.core.mp4_xtra import (
    XTRA_ENCODE_DETAIL_NAME,
    XTRA_KEYWORDS_NAME,
    XTRA_RATING_NAME,
    read_mp4_xtra_items,
    text_item,
    uint64_item,
    write_mp4_xtra,
)
from src.service.encode.encode_job import EncodeJob, EncodeSettings
from src.service.encode.encode_tag_io import (
    build_tagged_plan,
    merge_mp4_output_tags,
    read_source_tag_inputs,
    write_result_tags,
)


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), box_type) + payload


def _mp4_bytes(*, moov_first: bool = False) -> bytes:
    ftyp = _box(b"ftyp", b"isom")
    mdat = _box(b"mdat", b"\x00" * 32)
    moov = _box(b"moov", b"")
    return ftyp + moov + mdat if moov_first else ftyp + mdat + moov


class TestEncodeTagIo(unittest.TestCase):
    """원본 키워드와 별점 읽기, Xtra 쓰기, moov 위치 면제, 출력 태그 조회를 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_file(self, name: str, data: bytes) -> str:
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def _job(self, source_path: str, ext: str) -> EncodeJob:
        return EncodeJob(
            filename=os.path.basename(source_path),
            root_dir=self.root,
            source_path=source_path,
            source_size=os.path.getsize(source_path),
            source_mtime_ns=0,
            source_duration_sec=6.0,
            source_ext=ext,
            fps=30.0,
            quality=22.0,
            purpose=GatePurpose.SAVING,
            output_size=None,
            scale_filter=None,
            maxrate_kbps=None,
        )

    def test_mp4_preserves_user_keywords_and_rating_across_modes(self) -> None:
        """MP4 원본의 사용자 키워드와 별점이 Xtra 쓰기 후에도 보존됨을 검증"""
        source = self._write_file("src.mp4", _mp4_bytes())
        write_mp4_xtra(
            source,
            [
                text_item(XTRA_KEYWORDS_NAME, ["여행", "re_encoded"]),
                uint64_item(XTRA_RATING_NAME, 50),
                text_item(XTRA_ENCODE_DETAIL_NAME, ["v=1;enc=old"]),
            ],
        )
        part = self._write_file("out.mp4.part", _mp4_bytes())
        layout = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}]})
        config = ClassifierConfig()
        config.execution.encode_tag_mode = ENCODE_TAG_MODE_VISIBLE
        plan, rating = build_tagged_plan(
            job=self._job(source, "mp4"),
            settings=EncodeSettings.from_config(config),
            part_path=part,
            output_path=os.path.join(self.root, "out.mp4"),
            layout=layout,
        )
        self.assertIsNone(rating)
        exempt, reason = write_result_tags(plan, source_rating=rating)
        self.assertFalse(exempt)
        self.assertEqual(reason, "")
        items = {item.name: item for item in read_mp4_xtra_items(part)}
        self.assertEqual(items[XTRA_KEYWORDS_NAME].texts(), ["여행", "re_encoded"])
        self.assertEqual(items[XTRA_RATING_NAME].values[0].as_uint64(), 50)
        summary = merge_mp4_output_tags(part, CONTAINER_MP4, OutputSummary(2, 6.0, False))
        self.assertTrue(summary.has_encode_tag)
        self.assertEqual(summary.keywords, ("여행", "re_encoded"))

    def test_off_mode_removes_our_keyword_and_detail_from_mp4(self) -> None:
        """off 방식은 MP4에서 우리 키워드와 상세 항목을 지우고 사용자 키워드와 별점은 남김"""
        source = self._write_file("src_off.mp4", _mp4_bytes())
        write_mp4_xtra(
            source,
            [
                text_item(XTRA_KEYWORDS_NAME, ["가족", "re_encoded"]),
                uint64_item(XTRA_RATING_NAME, 99),
                text_item(XTRA_ENCODE_DETAIL_NAME, ["v=1;enc=old"]),
            ],
        )
        part = self._write_file("out_off.mp4.part", _mp4_bytes())
        layout = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}]})
        config = ClassifierConfig()
        config.execution.encode_tag_mode = ENCODE_TAG_MODE_OFF
        plan, rating = build_tagged_plan(
            job=self._job(source, "mp4"),
            settings=EncodeSettings.from_config(config),
            part_path=part,
            output_path=os.path.join(self.root, "out_off.mp4"),
            layout=layout,
        )
        write_result_tags(plan, source_rating=rating)
        items = {item.name: item for item in read_mp4_xtra_items(part)}
        self.assertEqual(items[XTRA_KEYWORDS_NAME].texts(), ["가족"])
        self.assertEqual(items[XTRA_RATING_NAME].values[0].as_uint64(), 99)
        self.assertNotIn(XTRA_ENCODE_DETAIL_NAME, items)

    def test_wmv_and_mkv_source_tags_are_read_and_transferred(self) -> None:
        """WMV 원본의 WM/Category와 별점, MKV 원본의 KEYWORDS를 읽음을 검증"""
        wmv_layout = parse_stream_layout({
            "streams": [{"index": 0, "codec_type": "video", "codec_name": "wmv2"}],
            "format": {"tags": {"WM/Category": "풍경;바다", "WM/SharedUserRating": "75"}},
        })
        kw, rating = read_source_tag_inputs("clip.wmv", "wmv", wmv_layout)
        self.assertEqual(kw, ("풍경", "바다"))
        self.assertEqual(rating, 75)

        mkv_layout = parse_stream_layout({
            "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}],
            "format": {"tags": {"KEYWORDS": "문서;re_encoded"}},
        })
        mkv_kw, mkv_rating = read_source_tag_inputs("clip.mkv", "mkv", mkv_layout)
        self.assertEqual(mkv_kw, ("문서", "re_encoded"))
        self.assertIsNone(mkv_rating)

    def test_moov_not_at_end_exempts_gate_and_returns_reason(self) -> None:
        """moov가 파일 끝이 아니면 쓰지 않고 게이트 면제와 사유를 돌려줌을 검증"""
        source = self._write_file("src.mp4", _mp4_bytes())
        part = self._write_file("faststart.mp4.part", _mp4_bytes(moov_first=True))
        with open(part, "rb") as handle:
            before = handle.read()
        layout = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}]})
        config = ClassifierConfig()
        config.execution.encode_tag_mode = ENCODE_TAG_MODE_HIDDEN
        plan, rating = build_tagged_plan(
            job=self._job(source, "mp4"),
            settings=EncodeSettings.from_config(config),
            part_path=part,
            output_path=os.path.join(self.root, "faststart.mp4"),
            layout=layout,
        )
        with self.assertLogs("src.service.encode.encode_tag_io", level="WARNING") as logs:
            exempt, reason = write_result_tags(plan, source_rating=rating)
        self.assertTrue(exempt)
        self.assertEqual(reason, "인코딩 태그를 기록하지 못함(지원하지 않는 파일 구조)")
        self.assertIn("moov", logs.output[0])
        with open(part, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_off_mode_with_empty_merged_skips_write_even_when_moov_first(self) -> None:
        """off 방식이고 남길 Xtra 항목이 없으면 moov 위치와 무관하게 미기록 사유를 붙이지 않음"""
        source = self._write_file("src_plain.mp4", _mp4_bytes())
        part = self._write_file("off_faststart.mp4.part", _mp4_bytes(moov_first=True))
        layout = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}]})
        config = ClassifierConfig()
        config.execution.encode_tag_mode = ENCODE_TAG_MODE_OFF
        plan, rating = build_tagged_plan(
            job=self._job(source, "mp4"),
            settings=EncodeSettings.from_config(config),
            part_path=part,
            output_path=os.path.join(self.root, "off_faststart.mp4"),
            layout=layout,
        )
        self.assertEqual(write_result_tags(plan, source_rating=rating), (False, ""))

    def test_mkv_skips_xtra_write_and_merge(self) -> None:
        """MKV 출력은 Xtra 쓰기와 읽기를 건너뜀을 검증"""
        source = self._write_file("src.mkv", b"mkv")
        layout = parse_stream_layout({"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}]})
        plan, rating = build_tagged_plan(
            job=self._job(source, "mkv"),
            settings=EncodeSettings.from_config(ClassifierConfig()),
            part_path=os.path.join(self.root, "out.mkv.part"),
            output_path=os.path.join(self.root, "out.mkv"),
            layout=layout,
        )
        self.assertEqual(write_result_tags(plan, source_rating=rating), (False, ""))
        summary = OutputSummary(2, 6.0, True, ("re_encoded",))
        self.assertIs(merge_mp4_output_tags(plan.part_path, CONTAINER_MKV, summary), summary)


if __name__ == "__main__":
    unittest.main(verbosity=2)
