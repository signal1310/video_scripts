"""MP4 계열 Windows 속성 저장 영역(moov/udta/Xtra)의 읽기와 쓰기 모듈

- Xtra는 공개 문서가 없는 Microsoft 형식이며, Windows 속성 시스템으로 쓴 파일의 바이트로 구조를 확인함
- 항목: [크기 4][이름 길이 4][이름][값 개수 4][값 목록]. 값: [크기 4][형식 2][데이터]
- 모든 정수는 빅엔디언이고, 항목 크기와 값 크기는 자기 필드 4바이트를 포함함. 항목과 값 사이에 패딩 없음
- 이름은 NUL 없는 바이트 그대로(ASCII 확인). 이름 인코딩은 확정하지 않아 latin-1로 바이트를 1:1 보존
- 형식 8은 UTF-16LE 문자열(끝 NUL 포함), 형식 19는 8바이트 리틀엔디언 정수(별점 WM/SharedUserRating)
- 쓰기는 moov가 파일 끝인 파일에만 함. moov가 mdat 앞에 있으면 moov 크기 변화로 mdat 뒤쪽 절대 위치(stco, co64)가 깨지므로 쓰지 않음
- 모르는 항목과 값은 바이트 그대로 보존(읽은 뒤 다시 쓰면 원본과 같은 바이트)
"""

import io
import struct
from collections.abc import Sequence
from dataclasses import dataclass

from src.core.encode_tag import ENCODE_TAG_KEY, parse_encode_tag
from src.core.mp4_boxes import BOX_HEADER_BYTES, BoxHeader, file_size_bytes, find_box, iter_boxes

XTRA_BOX_TYPE: bytes = b"Xtra"
_FTYP_BOX_TYPE: bytes = b"ftyp"
_MDAT_BOX_TYPE: bytes = b"mdat"
_UDTA_BOX_TYPE: bytes = b"udta"
_MOOV_BOX_TYPE: bytes = b"moov"
XTRA_KEYWORDS_NAME: str = "WM/Category"
XTRA_RATING_NAME: str = "WM/SharedUserRating"
XTRA_ENCODE_DETAIL_NAME: str = "VideoScripts/Encode"
XTRA_TYPE_TEXT: int = 8
XTRA_TYPE_UINT64_LE: int = 19
_UINT32_BYTES: int = 4
_TYPE_BYTES: int = 2
_UINT64_BYTES: int = 8
_NAME_ENCODING: str = "latin-1"
_TEXT_ENCODING: str = "utf-16-le"
_TEXT_TERMINATOR: bytes = b"\x00\x00"
_MAX_BOX_SIZE32: int = 0xFFFFFFFF
_LARGE_HEADER_BYTES: int = 16


@dataclass(frozen=True, slots=True)
class XtraValue:
    """Xtra 항목의 값 하나. data는 형식 필드 뒤의 바이트 그대로"""

    type_code: int
    data: bytes

    def as_text(self) -> str | None:
        """형식 8이면 끝 NUL을 뗀 문자열, 아니면 None"""
        if self.type_code != XTRA_TYPE_TEXT:
            return None
        return self.data.decode(_TEXT_ENCODING).rstrip("\x00")

    def as_uint64(self) -> int | None:
        """형식 19이면 리틀엔디언 정수, 아니면 None"""
        if self.type_code != XTRA_TYPE_UINT64_LE or len(self.data) != _UINT64_BYTES:
            return None
        return int.from_bytes(self.data, "little")


@dataclass(frozen=True, slots=True)
class XtraItem:
    """Xtra 항목 하나. 값이 없는 항목은 쓰기 때 제거 의미로 쓰임(merge_xtra_items)"""

    name: str
    values: tuple[XtraValue, ...]

    def texts(self) -> list[str]:
        """형식 8 값을 문자열 목록으로 돌려줌(다른 형식은 건너뜀)"""
        return [text for text in (value.as_text() for value in self.values) if text is not None]


@dataclass(frozen=True, slots=True)
class XtraWriteOutcome:
    """쓰기 결과. applied가 True이면 파일이 요청한 항목 상태가 됨"""

    applied: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Mp4ProbeInfo:
    """MP4 계열 파일 한 번 열기로 읽은 Xtra 키워드, 상세 항목 여부와 mdat 크기"""

    is_mp4_family: bool
    mdat_size: int | None
    keywords: tuple[str, ...]
    has_encode_detail: bool


def read_mp4_probe_info(filepath: str) -> Mp4ProbeInfo:
    """파일을 한 번만 열어 MP4 여부, mdat 크기, Xtra 키워드와 상세 항목 존재 여부를 읽음"""
    with open(filepath, "rb") as f:
        file_size = file_size_bytes(f)
        if file_size < BOX_HEADER_BYTES:
            return Mp4ProbeInfo(False, None, (), False)
        f.seek(0)
        if f.read(BOX_HEADER_BYTES)[4:] != _FTYP_BOX_TYPE:
            return Mp4ProbeInfo(False, None, (), False)
        mdat_size: int | None = None
        moov: BoxHeader | None = None
        for box in iter_boxes(f, 0, file_size):
            if box.box_type == _MDAT_BOX_TYPE and mdat_size is None:
                mdat_size = box.size
            elif box.box_type == _MOOV_BOX_TYPE and moov is None:
                moov = box
            if mdat_size is not None and moov is not None:
                break
        keywords: tuple[str, ...] = ()
        has_detail = False
        if moov is not None:
            udta = find_box(f, moov.payload_offset, moov.end_offset, _UDTA_BOX_TYPE)
            if udta is not None:
                xtra = find_box(f, udta.payload_offset, udta.end_offset, XTRA_BOX_TYPE)
                if xtra is not None:
                    f.seek(xtra.payload_offset)
                    items = parse_xtra_items(f.read(xtra.end_offset - xtra.payload_offset))
                    kw_list: list[str] = []
                    for item in items:
                        if item.name == XTRA_KEYWORDS_NAME:
                            kw_list.extend(item.texts())
                        elif item.name == XTRA_ENCODE_DETAIL_NAME and any(
                            parse_encode_tag({ENCODE_TAG_KEY: t}) is not None for t in item.texts()
                        ):
                            has_detail = True
                    keywords = tuple(kw_list)
        return Mp4ProbeInfo(True, mdat_size, keywords, has_detail)


def text_item(name: str, texts: Sequence[str]) -> XtraItem:
    """문자열 값 여러 개를 가진 항목을 만듦"""
    return XtraItem(
        name=name,
        values=tuple(XtraValue(XTRA_TYPE_TEXT, text.encode(_TEXT_ENCODING) + _TEXT_TERMINATOR) for text in texts),
    )


def uint64_item(name: str, value: int) -> XtraItem:
    """8바이트 리틀엔디언 정수 값 하나를 가진 항목을 만듦"""
    return XtraItem(name=name, values=(XtraValue(XTRA_TYPE_UINT64_LE, value.to_bytes(_UINT64_BYTES, "little")),))


def merge_xtra_items(base: Sequence[XtraItem], updates: Sequence[XtraItem]) -> list[XtraItem]:
    """같은 이름의 기존 항목을 갱신 항목으로 바꾸고 나머지(모르는 항목 포함)는 그대로 둠

    - 갱신 항목의 값이 비어 있으면 그 이름의 항목을 제거
    """
    update_names = {item.name for item in updates}
    merged = [item for item in base if item.name not in update_names]
    merged.extend(item for item in updates if item.values)
    return merged


def parse_xtra_items(payload: bytes) -> list[XtraItem]:
    """Xtra 상자 본문을 항목 목록으로 읽음. 형식이 맞지 않으면 ValueError"""
    items: list[XtraItem] = []
    pos = 0
    while pos < len(payload):
        item_end = pos + _read_uint32(payload, pos, "항목 크기")
        name_length = _read_uint32(payload, pos + _UINT32_BYTES, "이름 길이")
        name_start = pos + 2 * _UINT32_BYTES
        name_end = name_start + name_length
        count_pos = name_end
        if item_end > len(payload) or item_end < count_pos + _UINT32_BYTES:
            raise ValueError("Xtra 항목 크기가 범위를 벗어났습니다")
        name = payload[name_start:name_end].decode(_NAME_ENCODING)
        value_count = _read_uint32(payload, count_pos, "값 개수")
        values: list[XtraValue] = []
        value_pos = count_pos + _UINT32_BYTES
        for _ in range(value_count):
            value_size = _read_uint32(payload, value_pos, "값 크기")
            value_end = value_pos + value_size
            if value_size < _UINT32_BYTES + _TYPE_BYTES or value_end > item_end:
                raise ValueError("Xtra 값 크기가 범위를 벗어났습니다")
            type_code = int.from_bytes(payload[value_pos + _UINT32_BYTES : value_pos + _UINT32_BYTES + _TYPE_BYTES], "big")
            values.append(XtraValue(type_code, payload[value_pos + _UINT32_BYTES + _TYPE_BYTES : value_end]))
            value_pos = value_end
        if value_pos != item_end:
            raise ValueError("Xtra 항목 끝이 값 목록과 맞지 않습니다")
        items.append(XtraItem(name=name, values=tuple(values)))
        pos = item_end
    return items


def build_xtra_payload(items: Sequence[XtraItem]) -> bytes:
    """항목 목록을 Xtra 상자 본문 바이트로 만듦. parse_xtra_items의 역연산"""
    return b"".join(_build_item(item) for item in items)


def read_mp4_xtra_items(filepath: str) -> list[XtraItem]:
    """파일의 moov/udta/Xtra 항목을 읽음. 상자가 없으면 빈 목록(파일 본문은 읽지 않음)"""
    with open(filepath, "rb") as f:
        file_size = file_size_bytes(f)
        moov = find_box(f, 0, file_size, _MOOV_BOX_TYPE)
        if moov is None:
            return []
        udta = find_box(f, moov.payload_offset, moov.end_offset, _UDTA_BOX_TYPE)
        if udta is None:
            return []
        xtra = find_box(f, udta.payload_offset, udta.end_offset, XTRA_BOX_TYPE)
        if xtra is None:
            return []
        f.seek(xtra.payload_offset)
        return parse_xtra_items(f.read(xtra.end_offset - xtra.payload_offset))


def write_mp4_xtra(filepath: str, items: Sequence[XtraItem]) -> XtraWriteOutcome:
    """파일의 Xtra를 items 상태로 바꿈(빈 목록이면 Xtra 제거). moov가 파일 끝이 아니면 쓰지 않음

    - 바뀌는 범위는 moov 상자 전체. 앞의 mdat과 그 안의 절대 위치는 그대로
    - 호출자는 결과 파일(.part)에 쓰고, 원본은 바꾸지 않음
    """
    with open(filepath, "r+b") as f:
        file_size = file_size_bytes(f)
        moov = find_box(f, 0, file_size, _MOOV_BOX_TYPE)
        if moov is None:
            return XtraWriteOutcome(applied=False, reason="moov 상자가 없습니다")
        if moov.end_offset != file_size:
            return XtraWriteOutcome(applied=False, reason="moov 상자가 파일 끝에 있지 않습니다")
        f.seek(moov.payload_offset)
        moov_payload = f.read(moov.end_offset - moov.payload_offset)
        new_moov_payload = _moov_with_xtra(moov_payload, items)
        if new_moov_payload == moov_payload:
            return XtraWriteOutcome(applied=True, reason="변경 없음")
        f.seek(moov.offset)
        f.write(_box_bytes(_MOOV_BOX_TYPE, new_moov_payload))
        f.truncate()
    return XtraWriteOutcome(applied=True, reason="")


def _moov_with_xtra(moov_payload: bytes, items: Sequence[XtraItem]) -> bytes:
    """moov 본문에서 udta의 Xtra를 items로 바꾼 본문을 만듦(udta가 없으면 만듦)"""
    xtra_raw = _box_bytes(XTRA_BOX_TYPE, build_xtra_payload(items)) if items else b""
    children: list[bytes] = []
    has_udta = False
    for header, raw in _split_boxes(moov_payload):
        if header.box_type != _UDTA_BOX_TYPE:
            children.append(raw)
            continue
        has_udta = True
        udta_payload = raw[header.header_size :]
        kept = [child_raw for child_header, child_raw in _split_boxes(udta_payload) if child_header.box_type != XTRA_BOX_TYPE]
        children.append(_box_bytes(_UDTA_BOX_TYPE, b"".join(kept) + xtra_raw))
    if not has_udta and xtra_raw:
        children.append(_box_bytes(_UDTA_BOX_TYPE, xtra_raw))
    return b"".join(children)


def _split_boxes(data: bytes) -> list[tuple[BoxHeader, bytes]]:
    """상자 목록을 (머리말 정보, 상자 바이트 전체) 쌍으로 나눔"""
    buffer = io.BytesIO(data)
    return [(box, data[box.offset : box.end_offset]) for box in iter_boxes(buffer, 0, len(data))]


def _box_bytes(box_type: bytes, payload: bytes) -> bytes:
    """상자 머리말과 본문을 합침. 전체 크기가 32비트를 넘으면 64비트 크기 머리말"""
    total = BOX_HEADER_BYTES + len(payload)
    if total <= _MAX_BOX_SIZE32:
        return struct.pack(">I4s", total, box_type) + payload
    return struct.pack(">I4sQ", 1, box_type, _LARGE_HEADER_BYTES + len(payload)) + payload


def _build_item(item: XtraItem) -> bytes:
    name = item.name.encode(_NAME_ENCODING)
    values = b"".join(_build_value(value) for value in item.values)
    body = struct.pack(">I", len(name)) + name + struct.pack(">I", len(item.values)) + values
    return struct.pack(">I", _UINT32_BYTES + len(body)) + body


def _build_value(value: XtraValue) -> bytes:
    size = _UINT32_BYTES + _TYPE_BYTES + len(value.data)
    return struct.pack(">IH", size, value.type_code) + value.data


def _read_uint32(payload: bytes, pos: int, field_name: str) -> int:
    if pos + _UINT32_BYTES > len(payload):
        raise ValueError(f"Xtra {field_name} 필드가 잘렸습니다")
    (value,) = struct.unpack(">I", payload[pos : pos + _UINT32_BYTES])
    return value
