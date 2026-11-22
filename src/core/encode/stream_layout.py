"""ffprobe 스트림 목록 JSON을 인코딩 계획용 스트림 구조로 변환하는 파서 모듈

- 순수 함수만 포함하며 프로세스 실행은 호출자가 담당
- 인터레이스 판정(결정 15), 회전, 색 정보, VR 부가 정보(결정 27)를 함께 읽음
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from src.core.probe_parser import VR_SIDE_DATA_TYPES, format_tags

KIND_VIDEO: str = "video"
KIND_AUDIO: str = "audio"
KIND_SUBTITLE: str = "subtitle"
KIND_ATTACHMENT: str = "attachment"
KIND_DATA: str = "data"

INTERLACED_FIELD_ORDERS: frozenset[str] = frozenset({"tt", "bb", "tb", "bt"})
TEXT_SUBTITLE_CODECS: frozenset[str] = frozenset(
    {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
)
BITMAP_SUBTITLE_CODECS: frozenset[str] = frozenset(
    {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
)


@dataclass(frozen=True, slots=True)
class StreamInfo:
    """ffprobe 스트림 한 개의 인코딩 판단에 필요한 정보"""

    index: int
    kind: str
    codec_name: str
    is_attached_pic: bool = False
    field_order: str = ""
    rotation: int = 0
    color_primaries: str = ""
    color_transfer: str = ""
    color_space: str = ""
    color_range: str = ""
    side_data_types: tuple[str, ...] = ()
    tags: Mapping[str, str] = field(default_factory=dict)
    bit_rate_bps: int | None = None
    stream_bytes: int | None = None

    @property
    def is_interlaced(self) -> bool:
        """필드 순서가 인터레이스 값인지 여부(진행, 알 수 없음, 값 없음은 False)"""
        return self.field_order.lower() in INTERLACED_FIELD_ORDERS

    @property
    def is_vr(self) -> bool:
        """구면 투영 또는 입체 부가 정보가 있는지 여부"""
        return any(kind in VR_SIDE_DATA_TYPES for kind in self.side_data_types)


@dataclass(frozen=True, slots=True)
class StreamLayout:
    """영상 한 개의 전체 스트림 목록과 주 영상 스트림 조회"""

    streams: tuple[StreamInfo, ...]
    format_tags: Mapping[str, str] = field(default_factory=dict)

    @property
    def primary_video(self) -> StreamInfo | None:
        """첨부 이미지(커버)를 제외한 첫 영상 스트림, 없으면 None"""
        for stream in self.streams:
            if stream.kind == KIND_VIDEO and not stream.is_attached_pic:
                return stream
        return None

    @property
    def is_vr(self) -> bool:
        """주 영상 스트림에 VR 부가 정보가 있는지 여부"""
        video = self.primary_video
        return video is not None and video.is_vr


def _to_text(raw: Any) -> str:
    return str(raw).strip() if raw is not None else ""


def _parse_rotation(side_data: list[Mapping[str, Any]], tags: Mapping[str, str]) -> int:
    """Display Matrix 부가 정보의 회전 값을 우선하고, 없으면 rotate 태그를 사용"""
    for item in side_data:
        if "rotation" in item:
            try:
                return int(float(item["rotation"]))
            except (TypeError, ValueError):
                return 0
    raw_tag = tags.get("rotate", "0")
    try:
        return int(float(raw_tag))
    except ValueError:
        return 0


def _parse_stream(raw: Mapping[str, Any]) -> StreamInfo | None:
    kind = _to_text(raw.get("codec_type")).lower()
    if kind not in (KIND_VIDEO, KIND_AUDIO, KIND_SUBTITLE, KIND_ATTACHMENT, KIND_DATA):
        return None
    index_raw = raw.get("index")
    if not isinstance(index_raw, int):
        return None

    disposition = raw.get("disposition")
    disposition_map: Mapping[str, Any] = disposition if isinstance(disposition, Mapping) else {}
    tags_raw = raw.get("tags")
    tags: dict[str, str] = (
        {str(k): _to_text(v) for k, v in tags_raw.items()} if isinstance(tags_raw, Mapping) else {}
    )
    side_raw = raw.get("side_data_list")
    side_data: list[Mapping[str, Any]] = [
        item for item in side_raw if isinstance(item, Mapping)
    ] if isinstance(side_raw, list) else []

    return StreamInfo(
        index=index_raw,
        kind=kind,
        codec_name=_to_text(raw.get("codec_name")).lower(),
        is_attached_pic=bool(disposition_map.get("attached_pic", 0)),
        field_order=_to_text(raw.get("field_order")).lower(),
        rotation=_parse_rotation(side_data, tags) if kind == KIND_VIDEO else 0,
        color_primaries=_to_text(raw.get("color_primaries")),
        color_transfer=_to_text(raw.get("color_transfer")),
        color_space=_to_text(raw.get("color_space")),
        color_range=_to_text(raw.get("color_range")),
        side_data_types=tuple(
            _to_text(item.get("side_data_type")) for item in side_data if "side_data_type" in item
        ),
        tags=tags,
        bit_rate_bps=_parse_positive_int(raw.get("bit_rate") or tags.get("BPS") or tags.get("BPS-eng")),
        stream_bytes=_parse_positive_int(tags.get("NUMBER_OF_BYTES") or tags.get("NUMBER_OF_BYTES-eng")),
    )


def _parse_positive_int(raw: Any) -> int | None:
    """양의 정수 문자열 또는 숫자를 정수로 파싱. 없거나 잘못된 값이면 None"""
    if raw is None:
        return None
    try:
        value = int(float(str(raw).strip()))
    except ValueError:
        return None
    return value if value > 0 else None


def parse_stream_layout(probe: Mapping[str, Any]) -> StreamLayout:
    """ffprobe JSON 응답의 streams 배열과 format 태그를 StreamLayout으로 변환(인식하지 못한 종류는 제외)"""
    raw_format = probe.get("format")
    fmt_tags = format_tags(raw_format) if isinstance(raw_format, Mapping) else {}
    raw_streams = probe.get("streams")
    if not isinstance(raw_streams, list):
        return StreamLayout(streams=(), format_tags=fmt_tags)
    parsed = [
        stream
        for stream in (_parse_stream(item) for item in raw_streams if isinstance(item, Mapping))
        if stream is not None
    ]
    return StreamLayout(streams=tuple(sorted(parsed, key=lambda s: s.index)), format_tags=fmt_tags)
