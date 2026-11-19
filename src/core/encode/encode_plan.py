"""영상 한 개의 재인코딩 계획 데이터와 컨테이너, 스트림 처리 규칙을 정의하는 모듈

- 판정 결과와 스트림 목록, 설정 사본을 받아 FFmpeg 인자를 만들 수 있는 불변 계획을 산출
- 디코딩 기본 구성은 결정 14 실측 확정값(샤나 기본 구성)
"""

from dataclasses import dataclass
from enum import Enum

from src.core.config import CONTAINER_POLICY_MP4
from src.core.encode.stream_layout import (
    BITMAP_SUBTITLE_CODECS,
    KIND_ATTACHMENT,
    KIND_AUDIO,
    KIND_DATA,
    KIND_SUBTITLE,
    KIND_VIDEO,
    TEXT_SUBTITLE_CODECS,
    StreamLayout,
)
from src.core.encode_tag import DEFAULT_ENCODE_TAG_NAME, ENCODE_TAG_MODE_VISIBLE
from src.core.encoder_specs import get_encoder_spec

CONTAINER_MP4: str = "mp4"
CONTAINER_MKV: str = "mkv"
CONTAINER_MOV: str = "mov"
ORIGINAL_CONTAINERS: frozenset[str] = frozenset({CONTAINER_MP4, CONTAINER_MKV, CONTAINER_MOV})
MP4_AUDIO_CODECS: frozenset[str] = frozenset({"aac", "mp3", "ac3", "eac3", "alac", "mp2"})
WMA_AUDIO_CODECS: frozenset[str] = frozenset({"wmav1", "wmav2", "wmapro", "wmavoice"})
DEFAULT_PIXEL_FORMAT: str = "yuv420p"
MOV_TEXT_REASON: str = "텍스트 자막 mov_text 변환(서식 손실)"

ACTION_ENCODE: str = "encode"
ACTION_COPY: str = "copy"
ACTION_TRANSCODE_AAC: str = "transcode:aac"
ACTION_TRANSCODE_MOV_TEXT: str = "transcode:mov_text"
ACTION_DROP: str = "drop"


class DecodeMode(Enum):
    """디코딩 구성. 기본값은 샤나 기본 구성(결정 14 확정, HEVC와 AV1은 하드웨어 자동, 나머지는 소프트웨어)"""

    SHANA_DEFAULT = "shana_default"
    CPU_ONLY = "cpu_only"


@dataclass(frozen=True, slots=True)
class StreamAction:
    """입력 스트림 한 개의 출력 처리 방식과 사유(드롭 또는 변환일 때 기록)"""

    index: int
    kind: str
    action: str
    reason: str = ""
    attached_pic: bool = False


@dataclass(frozen=True, slots=True)
class ColorInfo:
    """출력에 명시 전달할 색 정보(빈 문자열은 전달하지 않음)"""

    primaries: str = ""
    transfer: str = ""
    space: str = ""
    range: str = ""


@dataclass(frozen=True, slots=True)
class EncodePlan:
    """영상 한 개를 인코딩하기 위해 확정된 값. FFmpeg 인자 생성의 입력"""

    source_path: str
    part_path: str
    output_path: str
    container: str
    encoder: str
    quality: float
    source_codec: str
    fps: float
    decode_mode: DecodeMode
    streams: tuple[StreamAction, ...]
    tag_value: str
    color: ColorInfo
    is_vr: bool = False
    output_size: tuple[int, int] | None = None
    scale_filter: str | None = None
    maxrate_kbps: int | None = None
    pixel_format: str = DEFAULT_PIXEL_FORMAT
    tag_mode: str = ENCODE_TAG_MODE_VISIBLE
    tag_name: str = DEFAULT_ENCODE_TAG_NAME
    tag_keywords: tuple[str, ...] = ()

    @property
    def gop_frames(self) -> int:
        """키프레임 간격 1초에 대응하는 프레임 수(round(fps), 최소 1)"""
        return max(1, round(self.fps))


def choose_container(source_ext: str, layout: StreamLayout, container_policy: str) -> str:
    """결정 8의 컨테이너 규칙으로 출력 형식을 고른다

    - 비트맵 자막이 있으면 MKV(MP4에 저장할 수 없음)
    - 항상 MP4 정책이면 MP4
    - 원본 형식 유지 정책이면 MP4, MKV, MOV 원본을 유지하고 그 밖의 형식은 MP4
    """
    if any(s.kind == KIND_SUBTITLE and s.codec_name in BITMAP_SUBTITLE_CODECS for s in layout.streams):
        return CONTAINER_MKV
    if container_policy == CONTAINER_POLICY_MP4:
        return CONTAINER_MP4
    ext = source_ext.lower().lstrip(".")
    return ext if f".{ext}" in {".mp4", ".mkv", ".mov"} else CONTAINER_MP4


def _is_audio_compatible(codec: str, container: str) -> bool:
    if container == CONTAINER_MKV:
        return codec not in WMA_AUDIO_CODECS
    return codec in MP4_AUDIO_CODECS


def plan_stream_actions(layout: StreamLayout, container: str) -> tuple[StreamAction, ...]:
    """스트림마다 인코딩, 복사, 변환, 제외를 정한다(결정 8 표 기준)

    - 주 영상만 인코딩하고, 추가 영상 스트림은 제외. 커버(첨부 이미지)는 복사
    - MKV 전용 첨부 파일과 비트맵 자막은 MP4 출력에서 제외(사유 기록)
    """
    primary = layout.primary_video
    actions: list[StreamAction] = []
    for stream in layout.streams:
        if stream.kind == KIND_VIDEO:
            if stream.is_attached_pic:
                actions.append(StreamAction(stream.index, KIND_VIDEO, ACTION_COPY, attached_pic=True))
            elif primary is not None and stream.index == primary.index:
                actions.append(StreamAction(stream.index, KIND_VIDEO, ACTION_ENCODE))
            else:
                actions.append(StreamAction(stream.index, KIND_VIDEO, ACTION_DROP, "추가 영상 스트림 제외"))
        elif stream.kind == KIND_AUDIO:
            if _is_audio_compatible(stream.codec_name, container):
                actions.append(StreamAction(stream.index, KIND_AUDIO, ACTION_COPY))
            else:
                actions.append(
                    StreamAction(stream.index, KIND_AUDIO, ACTION_TRANSCODE_AAC, f"{stream.codec_name} 비호환, AAC 변환")
                )
        elif stream.kind == KIND_SUBTITLE:
            if stream.codec_name in BITMAP_SUBTITLE_CODECS:
                actions.append(StreamAction(stream.index, KIND_SUBTITLE, ACTION_COPY))
            elif stream.codec_name in TEXT_SUBTITLE_CODECS and container != CONTAINER_MKV:
                actions.append(StreamAction(stream.index, KIND_SUBTITLE, ACTION_TRANSCODE_MOV_TEXT, MOV_TEXT_REASON))
            else:
                actions.append(StreamAction(stream.index, KIND_SUBTITLE, ACTION_COPY))
        elif stream.kind == KIND_ATTACHMENT:
            if container == CONTAINER_MKV:
                actions.append(StreamAction(stream.index, KIND_ATTACHMENT, ACTION_COPY))
            else:
                actions.append(StreamAction(stream.index, KIND_ATTACHMENT, ACTION_DROP, "MP4는 첨부 파일을 저장하지 않음"))
        elif stream.kind == KIND_DATA:
            actions.append(StreamAction(stream.index, KIND_DATA, ACTION_DROP, "데이터 스트림 제외"))
    return tuple(actions)


def build_encode_plan(
    *,
    source_path: str,
    part_path: str,
    output_path: str,
    source_ext: str,
    encoder: str,
    quality: float,
    fps: float,
    layout: StreamLayout,
    container_policy: str,
    tag_value: str,
    output_size: tuple[int, int] | None = None,
    scale_filter: str | None = None,
    maxrate_kbps: int | None = None,
    tag_mode: str = ENCODE_TAG_MODE_VISIBLE,
    tag_name: str = DEFAULT_ENCODE_TAG_NAME,
    tag_keywords: tuple[str, ...] = (),
) -> EncodePlan:
    """판정과 스트림 목록으로 인코딩 계획을 만든다. 주 영상이 없거나 인코더가 사용 불가면 ValueError"""
    spec = get_encoder_spec(encoder)
    if spec is None or not spec.available:
        raise ValueError(f"사용할 수 없는 인코더입니다: {encoder}")
    primary = layout.primary_video
    if primary is None:
        raise ValueError("주 영상 스트림이 없어 인코딩 계획을 만들 수 없습니다")
    container = choose_container(source_ext, layout, container_policy)
    color = ColorInfo(
        primaries=primary.color_primaries,
        transfer=primary.color_transfer,
        space=primary.color_space,
        range=primary.color_range,
    )
    return EncodePlan(
        source_path=source_path,
        part_path=part_path,
        output_path=output_path,
        container=container,
        encoder=encoder,
        quality=quality,
        source_codec=primary.codec_name,
        fps=fps,
        decode_mode=DecodeMode.SHANA_DEFAULT,
        streams=plan_stream_actions(layout, container),
        tag_value=tag_value,
        color=color,
        is_vr=layout.is_vr,
        output_size=output_size,
        scale_filter=scale_filter,
        maxrate_kbps=maxrate_kbps,
        tag_mode=tag_mode,
        tag_name=tag_name,
        tag_keywords=tag_keywords,
    )
