"""재인코딩 처리 표식 태그의 값 생성과 파싱 모듈

- 키는 컨테이너 전역 태그 `video_scripts_encode`이며, 값은 ASCII 세미콜론 구분 `key=value` 목록
- 읽을 때 키는 대소문자를 무시하고, 알 수 없는 필드는 무시(명세 버전 호환)
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

ENCODE_TAG_KEY: str = "video_scripts_encode"
ENCODE_TAG_VERSION: int = 1
_FIELD_SEPARATOR: str = ";"
_PAIR_SEPARATOR: str = "="
_TIME_FORMAT: str = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True, slots=True)
class EncodeTag:
    """처리 표식 태그에서 읽은 값"""

    version: int
    encoder: str
    quality: str
    at: str


def build_encode_tag(encoder: str, quality: float, at: datetime) -> str:
    """처리 표식 태그 값을 만든다. at은 UTC 기준 시각이어야 함"""
    return _FIELD_SEPARATOR.join(
        (
            f"v={ENCODE_TAG_VERSION}",
            f"enc={encoder}",
            f"q={quality:.1f}",
            f"at={at.strftime(_TIME_FORMAT)}",
        )
    )


def _lookup_tag_value(tags: Mapping[str, str]) -> str | None:
    for key, value in tags.items():
        if key.lower() == ENCODE_TAG_KEY:
            return value
    return None


def parse_encode_tag(tags: Mapping[str, str]) -> EncodeTag | None:
    """컨테이너 태그 사전에서 처리 표식을 찾아 읽는다. 표식 키가 없으면 None

    - 버전이 현재 값과 달라도 표식으로 인정해 재처리를 막음(알 수 없는 필드는 무시)
    """
    raw = _lookup_tag_value(tags)
    if raw is None:
        return None
    fields: dict[str, str] = {}
    for part in raw.split(_FIELD_SEPARATOR):
        key, sep, value = part.partition(_PAIR_SEPARATOR)
        if sep:
            fields[key.strip().lower()] = value.strip()
    try:
        version = int(fields.get("v", ""))
    except ValueError:
        # - 표식 키가 있으면 버전을 해석하지 못해도 재처리 방지를 위해 표식으로 인정
        version = 0
    return EncodeTag(
        version=version,
        encoder=fields.get("enc", ""),
        quality=fields.get("q", ""),
        at=fields.get("at", ""),
    )
