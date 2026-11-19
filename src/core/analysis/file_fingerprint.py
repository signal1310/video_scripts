"""파일 내용 지문 산출 모듈(분석 캐시 키, 이름이 바뀌어도 같은 내용을 식별)"""

from __future__ import annotations

import hashlib

FINGERPRINT_CHUNK_BYTES: int = 64 * 1024
FINGERPRINT_DIGEST_BYTES: int = 16


def compute_fingerprint(filepath: str, filesize_bytes: int) -> str:
    """파일 앞, 뒤 청크와 크기로 내용 지문을 산출하며 수정 시각은 사용하지 않음

    이름이 바뀌어도 내용이 같으면 같은 지문이 나와 분석 결과를 재사용할 수 있음
    """
    digest = hashlib.blake2b(digest_size=FINGERPRINT_DIGEST_BYTES)
    digest.update(filesize_bytes.to_bytes(8, "little"))
    with open(filepath, "rb") as f:
        if filesize_bytes <= 2 * FINGERPRINT_CHUNK_BYTES:
            digest.update(f.read())
        else:
            digest.update(f.read(FINGERPRINT_CHUNK_BYTES))
            f.seek(filesize_bytes - FINGERPRINT_CHUNK_BYTES)
            digest.update(f.read(FINGERPRINT_CHUNK_BYTES))
    return digest.hexdigest()
