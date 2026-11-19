"""재인코딩 이력의 결과 지문 산출 모듈(이동, 이름 변경, 탐색기 태그 편집 뒤에도 같은 값)

- 파일 수정 시각과 경로는 쓰지 않음(결과의 수정 시각은 원본 값으로 맞추고, 일부 복사 도구가 바꿈)
- MP4 계열(첫 상자 ftyp): mdat 상자 크기와 mdat 본문 앞뒤 구간. 탐색기에서 태그를 편집하면 파일 끝의 moov만 바뀌고 mdat은 그대로
- 그 밖의 형식: 파일 크기와 파일 앞뒤 구간
- 앞뒤 구간만 읽으므로 대용량 결과에서도 I/O가 작음
"""

import hashlib
from dataclasses import dataclass
from typing import BinaryIO

from src.core.mp4_boxes import BOX_HEADER_BYTES, file_size_bytes, find_box

ENCODE_FINGERPRINT_EDGE_BYTES: int = 1024 * 1024
ENCODE_FINGERPRINT_DIGEST_BYTES: int = 16
_FILE_TYPE_BOX: bytes = b"ftyp"
_MEDIA_DATA_BOX: bytes = b"mdat"
_FILE_TYPE_OFFSET: int = 4
_LENGTH_BYTES: int = 8


@dataclass(frozen=True, slots=True)
class EncodeFingerprint:
    """재인코딩 이력의 결과 지문

    - size는 조회 크기 열 값(MP4 계열은 mdat 상자 크기, 그 밖은 파일 크기)
    - digest는 본문 앞뒤 구간의 blake2b 해시 16바이트(16진수)
    """

    size: int
    digest: str


def compute_encode_fingerprint(filepath: str) -> EncodeFingerprint:
    """결과 파일의 지문을 산출. MP4 계열인데 mdat 상자가 없으면 ValueError

    - mdat을 찾지 못한 MP4 계열을 파일 크기 지문으로 바꿔 쓰면 같은 파일에 다른 키가 생기므로 실패로 둠
    """
    with open(filepath, "rb") as f:
        file_size = file_size_bytes(f)
        if _is_iso_base_media(f, file_size):
            media = find_box(f, 0, file_size, _MEDIA_DATA_BOX)
            if media is None:
                raise ValueError("MP4 계열 파일에 mdat 상자가 없습니다")
            return EncodeFingerprint(
                size=media.size,
                digest=_digest_region(f, media.payload_offset, media.end_offset),
            )
        return EncodeFingerprint(size=file_size, digest=_digest_region(f, 0, file_size))


def _is_iso_base_media(f: BinaryIO, file_size: int) -> bool:
    if file_size < BOX_HEADER_BYTES:
        return False
    f.seek(0)
    return f.read(BOX_HEADER_BYTES)[_FILE_TYPE_OFFSET:] == _FILE_TYPE_BOX


def _digest_region(f: BinaryIO, start: int, end: int) -> str:
    """구간 길이와 구간 앞뒤 1MiB(짧으면 전체)의 blake2b 해시를 16진수로 돌려줌"""
    length = end - start
    digest = hashlib.blake2b(digest_size=ENCODE_FINGERPRINT_DIGEST_BYTES)
    digest.update(length.to_bytes(_LENGTH_BYTES, "little"))
    if length <= 2 * ENCODE_FINGERPRINT_EDGE_BYTES:
        f.seek(start)
        digest.update(f.read(length))
    else:
        f.seek(start)
        digest.update(f.read(ENCODE_FINGERPRINT_EDGE_BYTES))
        f.seek(end - ENCODE_FINGERPRINT_EDGE_BYTES)
        digest.update(f.read(ENCODE_FINGERPRINT_EDGE_BYTES))
    return digest.hexdigest()
