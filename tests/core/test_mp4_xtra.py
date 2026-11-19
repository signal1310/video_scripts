"""MP4 Xtra 항목의 바이트 형식, 쓰기 후 읽기 왕복, 기존 항목과 별점 보존, moov 위치에 따른 쓰기 거부를 검증하는 단위 테스트

- 기준 바이트는 Windows 속성 시스템으로 쓴 합성 MP4의 Xtra 본문(2026-11-19 실험)
"""

import os
import struct
import tempfile
import unittest

from src.core.mp4_xtra import (
    XTRA_ENCODE_DETAIL_NAME,
    XTRA_KEYWORDS_NAME,
    XTRA_RATING_NAME,
    XtraItem,
    XtraValue,
    build_xtra_payload,
    merge_xtra_items,
    parse_xtra_items,
    read_mp4_xtra_items,
    text_item,
    uint64_item,
    write_mp4_xtra,
)

# - 키워드 "alpha" 하나(Windows 쓰기 결과, 항목 41바이트)
_WINDOWS_KEYWORD_ONE: bytes = bytes.fromhex(
    "000000290000000b574d2f43617465676f72790000000100000012000861006c007000680061000000"
)
# - 별점 3개(50), 키워드 "alpha", "한글태그"(Windows 쓰기 결과, 102바이트, 별점 항목이 먼저)
_WINDOWS_RATING_AND_KEYWORDS: bytes = bytes.fromhex(
    "0000002d00000013574d2f53686172656455736572526174696e67000000010000000e00133200000000000000"
    "000000390000000b574d2f43617465676f72790000000200000012000861006c0070006800610000000000001000085cd500aedcd0f8ad0000"
)
# - 별점만 3개(50), 항목 45바이트
_WINDOWS_RATING_ONLY: bytes = bytes.fromhex(
    "0000002d00000013574d2f53686172656455736572526174696e67000000010000000e00133200000000000000"
)
# - 다시 편집한 결과(별점 5개 = 99, 키워드 "gamma", "delta", "엡실론", 118바이트)
_WINDOWS_REEDITED: bytes = bytes.fromhex(
    "0000002d00000013574d2f53686172656455736572526174696e67000000010000000e00136300000000000000"
    "000000490000000b574d2f43617465676f727900000003000000120008670061006d006d0061000000000000"
    "120008640065006c007400610000000000000e0008e1c5e4c260b80000"
)
_RATING_THREE_STARS: int = 50
_RATING_FIVE_STARS: int = 99
_KEYWORDS_ONE: list[str] = ["alpha"]
_KEYWORDS_TWO: list[str] = ["alpha", "한글태그"]
_KEYWORDS_REEDITED: list[str] = ["gamma", "delta", "엡실론"]
_DETAIL_SPEC: str = "v=1;enc=libx265;q=22.0;at=2026-11-19T00:00:00Z;app=0.1.0"
_FILE_TYPE_PAYLOAD: bytes = b"isom" + struct.pack(">I", 512) + b"isomiso2"
_MEDIA_PAYLOAD: bytes = b"m" * 300


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), box_type) + payload


def _synthetic_mp4(moov_children: bytes, media: bytes = _MEDIA_PAYLOAD) -> bytes:
    """ftyp, mdat, moov 순서의 합성 MP4. moov 본문 자식은 호출자가 지정"""
    return _box(b"ftyp", _FILE_TYPE_PAYLOAD) + _box(b"mdat", media) + _box(b"moov", moov_children)


def _moov_with_udta(xtra_raw: bytes = b"") -> bytes:
    """mvhd와 udta(meta와 선택적 Xtra)를 가진 moov 본문"""
    return _box(b"mvhd", b"\x00" * 100) + _box(b"udta", _box(b"meta", b"\x00" * 4) + xtra_raw)


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _top_level(data: bytes) -> dict[bytes, tuple[int, int]]:
    """최상위 상자 형식별 (위치, 크기) 사전"""
    result: dict[bytes, tuple[int, int]] = {}
    pos = 0
    while pos < len(data):
        size, box_type = struct.unpack(">I4s", data[pos : pos + 8])
        result[box_type] = (pos, size)
        pos += size
    return result


class _TempFileTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def _write(self, name: str, data: bytes) -> str:
        path = os.path.join(self.root, name)
        with open(path, "wb") as f:
            f.write(data)
        return path


class TestXtraFormat(unittest.TestCase):
    """Windows가 쓴 바이트와 같은 형식으로 읽고 쓰는지 검증"""

    def test_windows_payloads_parse_to_expected_items(self) -> None:
        """Windows 바이트를 읽으면 별점과 키워드 값이 기대와 같음을 검증"""
        rating_items = parse_xtra_items(_WINDOWS_RATING_ONLY)
        self.assertEqual(rating_items[0].name, XTRA_RATING_NAME)
        self.assertEqual(rating_items[0].values[0].as_uint64(), _RATING_THREE_STARS)

        both = parse_xtra_items(_WINDOWS_RATING_AND_KEYWORDS)
        self.assertEqual([item.name for item in both], [XTRA_RATING_NAME, XTRA_KEYWORDS_NAME])
        self.assertEqual(both[1].texts(), _KEYWORDS_TWO)

        reedited = parse_xtra_items(_WINDOWS_REEDITED)
        self.assertEqual(reedited[0].values[0].as_uint64(), _RATING_FIVE_STARS)
        self.assertEqual(reedited[1].texts(), _KEYWORDS_REEDITED)

    def test_windows_payloads_round_trip_byte_exact(self) -> None:
        """읽은 항목을 다시 만들면 Windows 바이트와 한 바이트도 다르지 않음을 검증"""
        for payload in (
            _WINDOWS_KEYWORD_ONE,
            _WINDOWS_RATING_ONLY,
            _WINDOWS_RATING_AND_KEYWORDS,
            _WINDOWS_REEDITED,
        ):
            with self.subTest(length=len(payload)):
                self.assertEqual(build_xtra_payload(parse_xtra_items(payload)), payload)

    def test_builders_match_windows_bytes(self) -> None:
        """문자열 항목과 별점 항목을 만든 바이트가 Windows 쓰기 결과와 같음을 검증"""
        self.assertEqual(build_xtra_payload([text_item(XTRA_KEYWORDS_NAME, _KEYWORDS_ONE)]), _WINDOWS_KEYWORD_ONE)
        self.assertEqual(
            build_xtra_payload([uint64_item(XTRA_RATING_NAME, _RATING_THREE_STARS)]),
            _WINDOWS_RATING_ONLY,
        )
        self.assertEqual(
            build_xtra_payload([
                uint64_item(XTRA_RATING_NAME, _RATING_THREE_STARS),
                text_item(XTRA_KEYWORDS_NAME, _KEYWORDS_TWO),
            ]),
            _WINDOWS_RATING_AND_KEYWORDS,
        )

    def test_malformed_payloads_are_rejected(self) -> None:
        """항목이나 값 크기가 범위를 넘거나 끝이 맞지 않으면 ValueError임을 검증"""
        broken_item_size = struct.pack(">I", 500) + _WINDOWS_KEYWORD_ONE[4:]
        # - 값 크기 필드 위치: 항목 크기 4, 이름 길이 4, 이름 11, 값 개수 4 뒤
        value_size_offset = 4 + 4 + len(b"WM/Category") + 4
        broken_value_size = (
            _WINDOWS_KEYWORD_ONE[:value_size_offset] + struct.pack(">I", 3) + _WINDOWS_KEYWORD_ONE[value_size_offset + 4 :]
        )
        truncated = _WINDOWS_RATING_ONLY[:-3]
        zero_size = struct.pack(">I", 0) + _WINDOWS_KEYWORD_ONE[4:]
        for payload in (broken_item_size, broken_value_size, truncated, zero_size):
            with self.subTest(length=len(payload)), self.assertRaises(ValueError):
                parse_xtra_items(payload)

    def test_value_accessors_return_none_for_other_types(self) -> None:
        """형식이 다르면 문자열과 정수 접근자가 None을 돌려주고 잘못된 길이의 정수도 None임을 검증"""
        self.assertIsNone(XtraValue(19, b"\x00" * 8).as_text())
        self.assertIsNone(XtraValue(8, b"ab").as_uint64())
        self.assertIsNone(XtraValue(19, b"\x00\x00").as_uint64())


class TestMergeItems(unittest.TestCase):
    """기존 항목과 갱신 항목의 병합 규칙"""

    def test_merge_replaces_same_name_and_keeps_unknown_items(self) -> None:
        """같은 이름은 갱신 항목으로 바꾸고 모르는 항목과 값은 바이트 그대로 보존함을 검증"""
        unknown = uint64_item("Vendor/Key", 7)
        base = [uint64_item(XTRA_RATING_NAME, 25), unknown]
        merged = merge_xtra_items(base, [text_item(XTRA_KEYWORDS_NAME, _KEYWORDS_ONE)])
        self.assertEqual([item.name for item in merged], [XTRA_RATING_NAME, "Vendor/Key", XTRA_KEYWORDS_NAME])
        self.assertEqual(merged[1], unknown)

    def test_empty_update_removes_the_item(self) -> None:
        """값이 없는 갱신 항목은 그 이름의 항목을 제거함을 검증"""
        merged = merge_xtra_items([uint64_item(XTRA_RATING_NAME, 25)], [XtraItem(name=XTRA_RATING_NAME, values=())])
        self.assertEqual(merged, [])


class TestMp4XtraFile(_TempFileTestCase):
    """합성 MP4 파일에 대한 쓰기와 읽기"""

    def test_write_then_read_round_trip_keeps_media_and_offsets(self) -> None:
        """쓰기 후 항목을 같은 값으로 읽고, mdat 바이트와 위치, moov 끝 위치가 유지됨을 검증"""
        path = self._write("clip.mp4", _synthetic_mp4(_moov_with_udta()))
        items = [
            uint64_item(XTRA_RATING_NAME, _RATING_THREE_STARS),
            text_item(XTRA_KEYWORDS_NAME, _KEYWORDS_TWO),
            text_item(XTRA_ENCODE_DETAIL_NAME, [_DETAIL_SPEC]),
        ]
        before_top = _top_level(_read_bytes(path))
        outcome = write_mp4_xtra(path, items)
        self.assertTrue(outcome.applied)
        data = _read_bytes(path)
        after_top = _top_level(data)
        self.assertEqual(after_top[b"mdat"], before_top[b"mdat"])
        self.assertEqual(after_top[b"moov"][0] + after_top[b"moov"][1], len(data))
        self.assertEqual(read_mp4_xtra_items(path), items)
        self.assertEqual(read_mp4_xtra_items(path)[2].texts(), [_DETAIL_SPEC])

    def test_write_preserves_unknown_items_and_replaces_managed_ones(self) -> None:
        """기존 Xtra의 모르는 항목은 바이트 그대로 두고, 관리 항목만 바꿈을 검증"""
        existing = build_xtra_payload([
            uint64_item(XTRA_RATING_NAME, _RATING_FIVE_STARS),
            uint64_item("Vendor/Key", 7),
            text_item(XTRA_KEYWORDS_NAME, ["old"]),
        ])
        path = self._write("existing.mp4", _synthetic_mp4(_moov_with_udta(_box(b"Xtra", existing))))
        current = read_mp4_xtra_items(path)
        updated = merge_xtra_items(current, [text_item(XTRA_KEYWORDS_NAME, _KEYWORDS_REEDITED)])
        self.assertTrue(write_mp4_xtra(path, updated).applied)
        result = read_mp4_xtra_items(path)
        self.assertEqual(result[0], uint64_item(XTRA_RATING_NAME, _RATING_FIVE_STARS))
        self.assertEqual(result[1], uint64_item("Vendor/Key", 7))
        self.assertEqual(result[2].texts(), _KEYWORDS_REEDITED)

    def test_write_creates_udta_when_absent(self) -> None:
        """udta가 없는 moov에는 udta를 새로 만들어 Xtra를 넣음을 검증"""
        path = self._write("bare.mov", _synthetic_mp4(_box(b"mvhd", b"\x00" * 100)))
        self.assertTrue(write_mp4_xtra(path, [text_item(XTRA_KEYWORDS_NAME, _KEYWORDS_ONE)]).applied)
        self.assertEqual(read_mp4_xtra_items(path)[0].texts(), _KEYWORDS_ONE)

    def test_empty_items_remove_xtra_box(self) -> None:
        """빈 목록을 쓰면 Xtra 상자가 사라지고 나머지 udta 자식은 그대로임을 검증"""
        path = self._write("removed.mp4", _synthetic_mp4(_moov_with_udta(_box(b"Xtra", _WINDOWS_RATING_ONLY))))
        self.assertTrue(write_mp4_xtra(path, []).applied)
        self.assertEqual(read_mp4_xtra_items(path), [])
        data = _read_bytes(path)
        self.assertNotIn(b"Xtra", data)
        self.assertIn(b"meta", data)

    def test_same_items_leave_file_unchanged(self) -> None:
        """같은 항목을 다시 쓰면 파일 바이트가 바뀌지 않고 변경 없음으로 보고됨을 검증"""
        path = self._write("same.mp4", _synthetic_mp4(_moov_with_udta()))
        items = [text_item(XTRA_KEYWORDS_NAME, _KEYWORDS_ONE)]
        self.assertTrue(write_mp4_xtra(path, items).applied)
        once = _read_bytes(path)
        outcome = write_mp4_xtra(path, items)
        self.assertTrue(outcome.applied)
        self.assertEqual(outcome.reason, "변경 없음")
        self.assertEqual(_read_bytes(path), once)

    def test_moov_before_media_is_not_written(self) -> None:
        """moov가 mdat 앞에 있으면 쓰지 않고 사유를 돌려주며 파일이 바뀌지 않음을 검증"""
        moov_first = _box(b"ftyp", _FILE_TYPE_PAYLOAD) + _box(b"moov", _moov_with_udta()) + _box(b"mdat", _MEDIA_PAYLOAD)
        path = self._write("moov_first.mp4", moov_first)
        outcome = write_mp4_xtra(path, [text_item(XTRA_KEYWORDS_NAME, _KEYWORDS_ONE)])
        self.assertFalse(outcome.applied)
        self.assertIn("파일 끝", outcome.reason)
        self.assertEqual(_read_bytes(path), moov_first)

    def test_missing_moov_is_not_written(self) -> None:
        """moov가 없는 파일은 쓰지 않고 사유를 돌려줌을 검증, 읽기는 빈 목록"""
        data = _box(b"ftyp", _FILE_TYPE_PAYLOAD) + _box(b"mdat", _MEDIA_PAYLOAD)
        path = self._write("no_moov.mp4", data)
        self.assertFalse(write_mp4_xtra(path, [text_item(XTRA_KEYWORDS_NAME, _KEYWORDS_ONE)]).applied)
        self.assertEqual(read_mp4_xtra_items(path), [])

    def test_read_without_udta_returns_empty_list(self) -> None:
        """udta와 Xtra가 없는 파일은 빈 목록을 돌려줌을 검증"""
        path = self._write("plain.mp4", _synthetic_mp4(_box(b"mvhd", b"\x00" * 100)))
        self.assertEqual(read_mp4_xtra_items(path), [])

    def test_read_mp4_probe_info_reads_mdat_size_keywords_and_detail_in_one_open(self) -> None:
        """한 번 열기로 mdat 크기, Xtra 키워드, 상세 항목 존재 여부와 비-MP4 구분을 읽음을 검증"""
        from src.core.mp4_xtra import read_mp4_probe_info

        mp4 = self._write("probe.mp4", _synthetic_mp4(_box(b"mvhd", b"\x00" * 16)))
        write_mp4_xtra(
            mp4,
            [
                text_item(XTRA_KEYWORDS_NAME, ["여행", "re_encoded"]),
                text_item(XTRA_ENCODE_DETAIL_NAME, ["v=1;enc=hevc_nvenc;q=22.0"]),
            ],
        )
        info = read_mp4_probe_info(mp4)
        self.assertTrue(info.is_mp4_family)
        self.assertEqual(info.mdat_size, 8 + len(_MEDIA_PAYLOAD))
        self.assertEqual(info.keywords, ("여행", "re_encoded"))
        self.assertTrue(info.has_encode_detail)

        mkv = self._write("other.mkv", b"\x1a\x45\xdf\xa3" + b"\x00" * 16)
        mkv_info = read_mp4_probe_info(mkv)
        self.assertFalse(mkv_info.is_mp4_family)
        self.assertIsNone(mkv_info.mdat_size)


if __name__ == "__main__":
    unittest.main(verbosity=2)
