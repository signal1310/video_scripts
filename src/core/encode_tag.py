"""재인코딩 처리 표식 태그의 값 생성, 파싱과 태그 이름 검증 모듈

- 키는 컨테이너 전역 태그 `video_scripts_encode`이며, 값은 ASCII 세미콜론 구분 `key=value` 목록
- 읽을 때 키는 대소문자를 무시하고, 알 수 없는 필드는 무시(명세 버전 호환)
- 태그 이름은 탐색기 태그 칸과 MKV KEYWORDS의 키워드로 쓰이므로 세미콜론을 금지
"""

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from src.core.app_version import APP_VERSION

ENCODE_TAG_KEY: str = "video_scripts_encode"
ENCODE_TAG_VERSION: int = 1
DEFAULT_ENCODE_TAG_NAME: str = "re_encoded"
ENCODE_TAG_NAME_MAX_CHARS: int = 64
ENCODE_TAG_MODE_VISIBLE: str = "visible"
ENCODE_TAG_MODE_HIDDEN: str = "hidden"
ENCODE_TAG_MODE_OFF: str = "off"
VALID_ENCODE_TAG_MODES: tuple[str, ...] = (
    ENCODE_TAG_MODE_VISIBLE,
    ENCODE_TAG_MODE_HIDDEN,
    ENCODE_TAG_MODE_OFF,
)
_FIELD_SEPARATOR: str = ";"
_PAIR_SEPARATOR: str = "="
_TIME_FORMAT: str = "%Y-%m-%dT%H:%M:%SZ"
_CONTROL_CHARACTER_CATEGORY: str = "Cc"


@dataclass(frozen=True, slots=True)
class EncodeTag:
    """처리 표식 태그에서 읽은 값"""

    version: int
    encoder: str
    quality: str
    at: str


def build_encode_tag(encoder: str, quality: float, at: datetime) -> str:
    """처리 표식 태그 값을 만든다. at은 UTC 기준 시각이어야 함

    - app 필드는 상세 값과 재인코딩 이력에만 쓰며, 보이는 태그 이름에는 넣지 않음(태그 열에서 같은 이름으로 묶이기 위함)
    """
    return _FIELD_SEPARATOR.join(
        (
            f"v={ENCODE_TAG_VERSION}",
            f"enc={encoder}",
            f"q={quality:.1f}",
            f"at={at.strftime(_TIME_FORMAT)}",
            f"app={APP_VERSION}",
        )
    )


def with_tag_quality(tag_value: str, quality: float) -> str:
    """처리 표식 태그 문자열의 q 필드를 조정된 품질값으로 교체. 빈 문자열이면 그대로 반환"""
    if not tag_value:
        return tag_value
    parts = [
        f"q={quality:.1f}" if part.startswith("q=") else part
        for part in tag_value.split(_FIELD_SEPARATOR)
    ]
    return _FIELD_SEPARATOR.join(parts)


def normalize_tag_name(raw: str) -> str:
    """재인코딩 태그 이름을 검증하고 앞뒤 공백을 뗀 값을 돌려줌. 허용되지 않으면 ValueError

    - 빈 값, 세미콜론, 제어 문자, 최대 문자 수(ENCODE_TAG_NAME_MAX_CHARS) 초과를 거부
    """
    name = raw.strip()
    if not name:
        raise ValueError("재인코딩 태그 이름을 입력해 주세요")
    if _FIELD_SEPARATOR in name:
        raise ValueError("재인코딩 태그 이름에는 세미콜론(;)을 쓸 수 없습니다")
    if any(unicodedata.category(ch) == _CONTROL_CHARACTER_CATEGORY for ch in name):
        raise ValueError("재인코딩 태그 이름에는 제어 문자를 쓸 수 없습니다")
    if len(name) > ENCODE_TAG_NAME_MAX_CHARS:
        raise ValueError(f"재인코딩 태그 이름은 {ENCODE_TAG_NAME_MAX_CHARS}자 이하로 입력해 주세요")
    return name


def has_tag_name_keyword(keywords: Iterable[str], tag_names: Iterable[str]) -> bool:
    """키워드 중 하나가 태그 이름과 대소문자를 무시하고 같은지 검사

    - tag_names에는 현재 이름과 이전에 쓴 이름을 모두 넣음
    - 빈 이름과 빈 키워드는 일치로 보지 않음
    """
    names = {name.strip().casefold() for name in tag_names} - {""}
    return any(keyword.strip().casefold() in names for keyword in keywords)


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
