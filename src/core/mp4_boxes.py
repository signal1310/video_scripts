"""MP4 계열 상자(atom) 머리말 탐색 모듈

- 상자 머리말(크기, 형식)만 읽고 본문은 건너뛰어 파일 전체를 읽지 않음
- 같은 단계의 상자만 순서대로 다루며, 하위 상자는 호출자가 본문 범위를 넘겨 탐색
- 형식이 잘못되면 ValueError이며, 호출자가 잘린 파일을 정상으로 오인하지 않도록 예외로 알림
"""

import os
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO

BOX_HEADER_BYTES: int = 8
BOX_LARGE_HEADER_BYTES: int = 16
_BOX_SIZE_STRUCT: str = ">I4s"
_LARGE_SIZE_STRUCT: str = ">Q"
_LARGE_SIZE_MARKER: int = 1
_TO_CONTAINER_END_MARKER: int = 0


@dataclass(frozen=True, slots=True)
class BoxHeader:
    """상자 머리말 정보. size는 머리말을 포함한 상자 전체 크기"""

    box_type: bytes
    offset: int
    size: int
    header_size: int

    @property
    def payload_offset(self) -> int:
        """본문 시작 위치"""
        return self.offset + self.header_size

    @property
    def end_offset(self) -> int:
        """상자 끝 위치(다음 형제 상자의 시작 위치)"""
        return self.offset + self.size


def file_size_bytes(f: BinaryIO) -> int:
    """파일 크기를 구하고 읽기 위치는 파일 끝으로 둠"""
    return f.seek(0, os.SEEK_END)


def _read_exact(f: BinaryIO, count: int, offset: int) -> bytes:
    data = f.read(count)
    if len(data) < count:
        raise ValueError(f"상자 머리말이 잘렸습니다(위치 {offset})")
    return data


def read_box_header(f: BinaryIO, offset: int, container_end: int) -> BoxHeader:
    """offset 위치의 상자 머리말을 읽음

    - 크기 1은 뒤따르는 64비트 크기, 크기 0은 상위 범위 끝까지
    - 상자가 상위 범위를 넘으면 ValueError
    """
    f.seek(offset)
    size32, box_type = struct.unpack(_BOX_SIZE_STRUCT, _read_exact(f, BOX_HEADER_BYTES, offset))
    header_size = BOX_HEADER_BYTES
    size = size32
    if size32 == _LARGE_SIZE_MARKER:
        (size,) = struct.unpack(_LARGE_SIZE_STRUCT, _read_exact(f, 8, offset))
        header_size = BOX_LARGE_HEADER_BYTES
    elif size32 == _TO_CONTAINER_END_MARKER:
        size = container_end - offset
    if size < header_size or offset + size > container_end:
        raise ValueError(f"상자 크기가 범위를 벗어났습니다({box_type!r}, 위치 {offset})")
    return BoxHeader(box_type=box_type, offset=offset, size=size, header_size=header_size)


def iter_boxes(f: BinaryIO, start: int, end: int) -> Iterator[BoxHeader]:
    """start부터 end까지 같은 단계의 상자 머리말을 순서대로 돌려줌"""
    offset = start
    while offset < end:
        box = read_box_header(f, offset, end)
        yield box
        offset = box.end_offset


def find_box(f: BinaryIO, start: int, end: int, box_type: bytes) -> BoxHeader | None:
    """같은 단계에서 box_type 상자 중 처음 것을 찾음. 없으면 None"""
    for box in iter_boxes(f, start, end):
        if box.box_type == box_type:
            return box
    return None
