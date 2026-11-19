"""MP4 상자 머리말 탐색(크기 0, 64비트 크기, 범위 검사, 잘림)을 합성 바이트로 검증하는 단위 테스트"""

import io
import struct
import unittest

from src.core.mp4_boxes import (
    BOX_HEADER_BYTES,
    BOX_LARGE_HEADER_BYTES,
    file_size_bytes,
    find_box,
    iter_boxes,
    read_box_header,
)


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", BOX_HEADER_BYTES + len(payload), box_type) + payload


class TestBoxHeaders(unittest.TestCase):
    """상자 머리말 해석과 순회"""

    def test_iter_boxes_lists_siblings_in_order_with_offsets(self) -> None:
        """같은 단계 상자를 순서, 위치, 본문 시작 위치와 함께 돌려줌을 검증"""
        data = _box(b"ftyp", b"isom") + _box(b"mdat", b"0123456789")
        f = io.BytesIO(data)
        boxes = list(iter_boxes(f, 0, len(data)))
        self.assertEqual([box.box_type for box in boxes], [b"ftyp", b"mdat"])
        self.assertEqual(boxes[0].offset, 0)
        self.assertEqual(boxes[1].offset, boxes[0].size)
        self.assertEqual(boxes[1].payload_offset, boxes[1].offset + BOX_HEADER_BYTES)
        self.assertEqual(boxes[1].end_offset, len(data))

    def test_size_zero_box_extends_to_container_end(self) -> None:
        """크기 0 상자는 상위 범위 끝까지로 해석함을 검증"""
        data = struct.pack(">I4s", 0, b"mdat") + b"x" * 20
        box = read_box_header(io.BytesIO(data), 0, len(data))
        self.assertEqual(box.size, len(data))
        self.assertEqual(box.header_size, BOX_HEADER_BYTES)

    def test_large_size_marker_reads_64_bit_size(self) -> None:
        """크기 1 상자는 뒤따르는 64비트 크기와 16바이트 머리말로 해석함을 검증"""
        payload = b"y" * 30
        data = struct.pack(">I4sQ", 1, b"mdat", BOX_LARGE_HEADER_BYTES + len(payload)) + payload
        box = read_box_header(io.BytesIO(data), 0, len(data))
        self.assertEqual(box.size, len(data))
        self.assertEqual(box.header_size, BOX_LARGE_HEADER_BYTES)
        self.assertEqual(box.payload_offset, BOX_LARGE_HEADER_BYTES)

    def test_box_beyond_container_end_is_rejected(self) -> None:
        """본문이 잘려 상자 크기가 범위를 넘으면 ValueError임을 검증"""
        data = struct.pack(">I4s", 100, b"mdat") + b"z" * 20
        with self.assertRaises(ValueError):
            read_box_header(io.BytesIO(data), 0, len(data))

    def test_size_smaller_than_header_is_rejected(self) -> None:
        """상자 크기가 머리말보다 작으면 ValueError임을 검증(무한 순회 방지)"""
        data = struct.pack(">I4s", 4, b"free") + b"abcd"
        with self.assertRaises(ValueError):
            read_box_header(io.BytesIO(data), 0, len(data))

    def test_truncated_header_is_rejected(self) -> None:
        """머리말이 파일 끝에서 잘리면 ValueError임을 검증"""
        with self.assertRaises(ValueError):
            read_box_header(io.BytesIO(b"\x00\x00\x00"), 0, 3)
        with self.assertRaises(ValueError):
            read_box_header(io.BytesIO(struct.pack(">I4s", 1, b"mdat")), 0, 8)


class TestBoxSearch(unittest.TestCase):
    """최상위 상자 찾기와 파일 크기"""

    def test_find_box_returns_first_match_or_none(self) -> None:
        """처음 나오는 형식의 상자를 돌려주고, 없으면 None임을 검증"""
        data = _box(b"free", b"") + _box(b"mdat", b"aa") + _box(b"mdat", b"bbbb")
        f = io.BytesIO(data)
        found = find_box(f, 0, len(data), b"mdat")
        assert found is not None
        self.assertEqual(found.payload_offset - found.offset, BOX_HEADER_BYTES)
        self.assertEqual(found.size, BOX_HEADER_BYTES + 2)
        self.assertIsNone(find_box(f, 0, len(data), b"moov"))

    def test_file_size_bytes_returns_length_and_moves_to_end(self) -> None:
        """파일 크기를 돌려주고 읽기 위치를 끝으로 옮김을 검증"""
        f = io.BytesIO(b"0123456789")
        self.assertEqual(file_size_bytes(f), 10)
        self.assertEqual(f.tell(), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
