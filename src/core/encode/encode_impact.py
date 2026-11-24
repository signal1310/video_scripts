"""재인코딩 대상 영상의 저장된 스트림 요약으로 내부 요소 영향 집계를 계산하는 순수 함수 모듈

- 실제 인코딩과 같은 choose_container, plan_stream_actions를 적용해 기준 정의를 하나로 유지
- stream_summary가 비어 있거나 해석할 수 없는 행은 정보 없음으로 건너뜀
"""

import json
import os
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from src.core.encode.encode_plan import (
    ACTION_COPY,
    ACTION_DROP,
    ACTION_TRANSCODE_AAC,
    ACTION_TRANSCODE_MOV_TEXT,
    CONTAINER_MKV,
    CONTAINER_MOV,
    CONTAINER_MP4,
    choose_container,
    plan_stream_actions,
)
from src.core.encode.stream_layout import (
    BITMAP_SUBTITLE_CODECS,
    KIND_ATTACHMENT,
    KIND_DATA,
    KIND_SUBTITLE,
    KIND_VIDEO,
    StreamInfo,
    StreamLayout,
)
from src.core.models import VideoProps

IMPACT_COVER_KEPT: str = "cover_kept"
IMPACT_EXPLORER_TAGS_PRESERVED: str = "explorer_tags_preserved"
IMPACT_SUBTITLE_MOV_TEXT: str = "subtitle_mov_text"
IMPACT_BITMAP_SUB_MKV: str = "bitmap_subtitle_mkv"
IMPACT_ATTACHMENT_DROPPED: str = "attachment_dropped"
IMPACT_EXTRA_VIDEO_DROPPED: str = "extra_video_dropped"
IMPACT_DATA_DROPPED: str = "data_dropped"
IMPACT_AUDIO_AAC: str = "audio_transcode_aac"
IMPACT_CONTAINER_CHANGED: str = "container_changed"
IMPACT_TEN_BIT_TO_EIGHT: str = "ten_bit_to_eight"
IMPACT_VR_STEREO_LOST: str = "vr_stereo_lost"

IMPACT_LABELS: tuple[tuple[str, str], ...] = (
    (IMPACT_COVER_KEPT, "커버 유지"),
    (IMPACT_EXPLORER_TAGS_PRESERVED, "탐색기 태그와 별점 보존 대상"),
    (IMPACT_SUBTITLE_MOV_TEXT, "텍스트 자막 서식 손실"),
    (IMPACT_BITMAP_SUB_MKV, "비트맵 자막 때문에 MKV로 출력"),
    (IMPACT_ATTACHMENT_DROPPED, "첨부 파일 제외"),
    (IMPACT_EXTRA_VIDEO_DROPPED, "추가 영상 스트림 제외"),
    (IMPACT_DATA_DROPPED, "데이터 스트림 제외"),
    (IMPACT_AUDIO_AAC, "오디오 AAC로 변환"),
    (IMPACT_CONTAINER_CHANGED, "출력 형식 변경"),
    (IMPACT_TEN_BIT_TO_EIGHT, "10비트를 8비트로 변환"),
    (IMPACT_VR_STEREO_LOST, "VR 입체 정보 소실"),
)

_EXPLORER_TAG_SOURCE_EXTS: frozenset[str] = frozenset({"mp4", "m4v", "mov", "3gp", "wmv", "asf"})


@dataclass(frozen=True, slots=True)
class ImpactBucket:
    """내부 요소 영향 항목 한 가지의 라벨과 해당 파일 목록"""

    key: str
    label: str
    filenames: tuple[str, ...]

    @property
    def count(self) -> int:
        """영향받는 영상 수"""
        return len(self.filenames)


@dataclass(frozen=True, slots=True)
class EncodeImpactSummary:
    """전체 대상 영상의 내부 요소 영향 집계 결과"""

    total_count: int
    analyzed_count: int
    unknown_filenames: tuple[str, ...]
    buckets: tuple[ImpactBucket, ...]

    @property
    def unknown_count(self) -> int:
        """스트림 요약이 없어 건너뛴 영상 수"""
        return len(self.unknown_filenames)


def parse_stream_summary_layout(stream_summary: str) -> StreamLayout | None:
    """저장된 stream_summary JSON을 StreamLayout으로 변환. 비어 있거나 잘못된 형식이면 None"""
    if not stream_summary:
        return None
    try:
        raw = json.loads(stream_summary)
    except ValueError:
        return None
    if not isinstance(raw, list) or not raw:
        return None
    streams: list[StreamInfo] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, Mapping):
            return None
        kind = str(item.get("kind", "")).lower()
        codec = str(item.get("codec", "")).lower()
        if not kind:
            return None
        side_raw = item.get("side_data_types")
        side_types = (
            tuple(str(value) for value in side_raw if isinstance(value, str))
            if isinstance(side_raw, list)
            else ()
        )
        streams.append(
            StreamInfo(
                index=idx,
                kind=kind,
                codec_name=codec,
                is_attached_pic=bool(item.get("attached_pic", False)),
                side_data_types=side_types,
                field_order=str(item.get("field_order", "")).lower(),
            )
        )
    return StreamLayout(streams=tuple(streams))


def summarize_encode_impact(
    videos: Sequence[VideoProps],
    container_policy: str,
    family: str,
    vr_filenames: Collection[str] = (),
) -> EncodeImpactSummary:
    """저장된 스트림 요약에 실제 인코딩 계획 함수를 적용해 내부 요소별 영향 집계를 반환"""
    buckets_map: dict[str, list[str]] = {key: [] for key, _label in IMPACT_LABELS}
    unknown: list[str] = []
    analyzed = 0

    for video in videos:
        layout = parse_stream_summary_layout(video.stream_summary)
        if layout is None:
            unknown.append(video.filename)
            continue
        analyzed += 1
        source_ext = os.path.splitext(video.filename)[1].lstrip(".").lower()
        container = choose_container(source_ext, layout, container_policy, family)
        actions = plan_stream_actions(layout, container)

        if any(a.attached_pic and a.action == ACTION_COPY for a in actions):
            buckets_map[IMPACT_COVER_KEPT].append(video.filename)
        if source_ext in _EXPLORER_TAG_SOURCE_EXTS and container in (CONTAINER_MP4, CONTAINER_MOV):
            buckets_map[IMPACT_EXPLORER_TAGS_PRESERVED].append(video.filename)
        if any(a.action == ACTION_TRANSCODE_MOV_TEXT for a in actions):
            buckets_map[IMPACT_SUBTITLE_MOV_TEXT].append(video.filename)
        if (
            source_ext != CONTAINER_MKV
            and container == CONTAINER_MKV
            and any(s.kind == KIND_SUBTITLE and s.codec_name in BITMAP_SUBTITLE_CODECS for s in layout.streams)
        ):
            buckets_map[IMPACT_BITMAP_SUB_MKV].append(video.filename)
        if any(a.kind == KIND_ATTACHMENT and a.action == ACTION_DROP for a in actions):
            buckets_map[IMPACT_ATTACHMENT_DROPPED].append(video.filename)
        if any(a.kind == KIND_VIDEO and a.action == ACTION_DROP for a in actions):
            buckets_map[IMPACT_EXTRA_VIDEO_DROPPED].append(video.filename)
        if any(a.kind == KIND_DATA and a.action == ACTION_DROP for a in actions):
            buckets_map[IMPACT_DATA_DROPPED].append(video.filename)
        if any(a.action == ACTION_TRANSCODE_AAC for a in actions):
            buckets_map[IMPACT_AUDIO_AAC].append(video.filename)
        if source_ext != container:
            buckets_map[IMPACT_CONTAINER_CHANGED].append(video.filename)
        if video.is_10bit:
            buckets_map[IMPACT_TEN_BIT_TO_EIGHT].append(video.filename)
        if (layout.is_vr or video.filename in vr_filenames) and container == CONTAINER_MP4:
            buckets_map[IMPACT_VR_STEREO_LOST].append(video.filename)

    buckets = tuple(
        ImpactBucket(key=key, label=label, filenames=tuple(buckets_map[key]))
        for key, label in IMPACT_LABELS
    )
    return EncodeImpactSummary(
        total_count=len(videos),
        analyzed_count=analyzed,
        unknown_filenames=tuple(unknown),
        buckets=buckets,
    )
