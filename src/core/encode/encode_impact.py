"""인코딩 대상 영상의 저장된 스트림 요약으로 내부 요소 영향 집계를 계산하는 순수 함수 모듈

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
from src.core.models import VideoEvaluation

IMPACT_CROP_BARS: str = "crop_black_bars"
IMPACT_DOWNSCALE_RESOLUTION: str = "downscale_resolution"
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

# - 영상 변환 항목. 항목 이름에 해당 수와 전체 대상을 함께 표기함
TRANSFORM_IMPACT_KEYS: tuple[str, ...] = (IMPACT_CROP_BARS, IMPACT_DOWNSCALE_RESOLUTION)
TRANSFORM_TITLE_TEMPLATE: str = "{label} ({count}/{total}개)"
IMPACT_TITLE_TEMPLATE: str = "{label} ({count}개)"

IMPACT_LABELS: tuple[tuple[str, str], ...] = (
    (IMPACT_CROP_BARS, "검은 여백 잘라내기"),
    (IMPACT_DOWNSCALE_RESOLUTION, "해상도 축소"),
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
    """내부 요소 영향 항목 한 가지의 라벨과 해당 파일 목록

    - details는 파일별 변환 내역(예: `1920x1080 -> 1920x800`)이며 대부분 항목은 비어 있음
    """

    key: str
    label: str
    filenames: tuple[str, ...]
    details: tuple[tuple[str, str], ...] = ()

    @property
    def count(self) -> int:
        """영향받는 영상 수"""
        return len(self.filenames)

    def detail_of(self, filename: str) -> str:
        """파일의 변환 내역. 없으면 빈 문자열"""
        for name, detail in self.details:
            if name == filename:
                return detail
        return ""


def is_transform_impact(key: str) -> bool:
    """영상 변환 항목(검은 여백 잘라내기, 해상도 축소)인지"""
    return key in TRANSFORM_IMPACT_KEYS


def impact_title(bucket: ImpactBucket, total_count: int) -> str:
    """집계 항목 제목. 영상 변환 항목은 해당 수와 전체 수를 함께 표기"""
    if is_transform_impact(bucket.key):
        return TRANSFORM_TITLE_TEMPLATE.format(
            label=bucket.label, count=bucket.count, total=total_count
        )
    return IMPACT_TITLE_TEMPLATE.format(label=bucket.label, count=bucket.count)


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


def _crop_detail(ev: VideoEvaluation) -> str:
    """검은 여백 잘라내기 내역. 원본 크기에서 활성 화면 크기로"""
    active = ev.active_frame
    if active is None:
        return ""
    video = ev.video
    return f"{video.width}x{video.height} -> {active.width}x{active.height}"


def _downscale_detail(ev: VideoEvaluation) -> str:
    """해상도 축소 내역. 기준 크기(활성 화면)에서 출력 크기와 규격으로"""
    if ev.target_width is None or ev.target_height is None:
        return ""
    video = ev.video
    active = ev.active_frame
    base_width = active.width if active is not None else video.width
    base_height = active.height if active is not None else video.height
    tier = ev.target_tier.display if ev.target_tier is not None else f"{ev.target_width}x{ev.target_height}"
    return f"{base_width}x{base_height} -> {ev.target_width}x{ev.target_height} ({tier})"


def restrict_summary(summary: EncodeImpactSummary, filenames: Collection[str]) -> EncodeImpactSummary:
    """집계 결과를 주어진 파일만 남기도록 좁힘

    - 시작 전 대기열에서 체크를 푼 영상을 요약 탭의 집계와 트리에서 빼는 데 씀
    """
    allowed = set(filenames)
    buckets = tuple(
        ImpactBucket(
            key=bucket.key,
            label=bucket.label,
            filenames=tuple(name for name in bucket.filenames if name in allowed),
            details=tuple((name, detail) for name, detail in bucket.details if name in allowed),
        )
        for bucket in summary.buckets
    )
    return EncodeImpactSummary(
        total_count=len(allowed),
        analyzed_count=min(summary.analyzed_count, len(allowed)),
        unknown_filenames=tuple(name for name in summary.unknown_filenames if name in allowed),
        buckets=buckets,
    )


def summarize_encode_impact(
    evaluations: Sequence[VideoEvaluation],
    container_policy: str,
    family: str,
    vr_filenames: Collection[str] = (),
) -> EncodeImpactSummary:
    """저장된 스트림 요약에 실제 인코딩 계획 함수를 적용해 내부 요소별 영향 집계를 반환

    - 영상 변환 항목(검은 여백 잘라내기, 해상도 축소)은 판정 결과의 활성 화면과 출력 크기를 그대로 씀
    """
    buckets_map: dict[str, list[str]] = {key: [] for key, _label in IMPACT_LABELS}
    details_map: dict[str, list[tuple[str, str]]] = {key: [] for key, _label in IMPACT_LABELS}
    unknown: list[str] = []
    analyzed = 0

    for ev in evaluations:
        video = ev.video
        if ev.active_frame is not None:
            buckets_map[IMPACT_CROP_BARS].append(video.filename)
            details_map[IMPACT_CROP_BARS].append((video.filename, _crop_detail(ev)))
        if ev.target_width is not None and ev.target_height is not None:
            buckets_map[IMPACT_DOWNSCALE_RESOLUTION].append(video.filename)
            details_map[IMPACT_DOWNSCALE_RESOLUTION].append((video.filename, _downscale_detail(ev)))
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
        ImpactBucket(
            key=key,
            label=label,
            filenames=tuple(buckets_map[key]),
            details=tuple(details_map[key]),
        )
        for key, label in IMPACT_LABELS
    )
    return EncodeImpactSummary(
        total_count=len(evaluations),
        analyzed_count=analyzed,
        unknown_filenames=tuple(unknown),
        buckets=buckets,
    )
