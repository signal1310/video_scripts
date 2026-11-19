"""재인코딩 서비스의 원본 태그 읽기, MP4/MOV Xtra 쓰기, 게이트용 출력 태그 조회 모듈

- 원본 MP4/MOV는 Xtra의 WM/Category와 나머지 항목을 읽고, MKV는 KEYWORDS, WMV/ASF는 WM/Category와 별점을 읽음
- FFmpeg 완료 뒤 MP4/MOV .part에 Xtra를 합쳐 쓰고, moov가 파일 끝이 아니면 쓰지 않고 게이트 태그 확인을 면제
"""

import dataclasses
import logging
import os
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

from src.core.encode.encode_gate import OutputSummary
from src.core.encode.encode_plan import CONTAINER_MOV, CONTAINER_MP4, EncodePlan, build_encode_plan
from src.core.encode.encode_tag_plan import parse_keyword_list, plan_encode_tags
from src.core.encode.stream_layout import StreamLayout
from src.core.encode_tag import ENCODE_TAG_KEY, ENCODE_TAG_MODE_OFF, build_encode_tag, parse_encode_tag
from src.core.mp4_xtra import (
    XTRA_ENCODE_DETAIL_NAME,
    XTRA_KEYWORDS_NAME,
    XTRA_RATING_NAME,
    XtraItem,
    merge_xtra_items,
    read_mp4_xtra_items,
    text_item,
    uint64_item,
    write_mp4_xtra,
)
from src.service.encode.encode_job import EncodeJob, EncodeSettings

logger = logging.getLogger(__name__)

MP4_XTRA_EXTS: frozenset[str] = frozenset({"mp4", "m4v", "mov", "3gp"})
ASF_EXTS: frozenset[str] = frozenset({"wmv", "asf"})
MKV_EXTS: frozenset[str] = frozenset({"mkv", "webm"})


def _lookup_ci(tags: Mapping[str, str], target: str) -> str | None:
    target_lower = target.lower()
    for key, value in tags.items():
        if key.lower() == target_lower:
            return value
    return None


def _read_xtra_safe(filepath: str) -> list[XtraItem]:
    try:
        return read_mp4_xtra_items(filepath)
    except (OSError, ValueError) as error:
        logger.warning("MP4 Xtra 태그를 읽지 못했습니다: %s (%s)", filepath, error)
        return []


def _parse_rating(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    return value if value >= 0 else None


def read_source_tag_inputs(
    source_path: str, source_ext: str, layout: StreamLayout
) -> tuple[tuple[str, ...], int | None]:
    """원본 컨테이너에 맞는 위치에서 키워드 목록과(WMV/ASF일 때) 별점을 읽음"""
    ext = source_ext.lower().lstrip(".")
    if ext in MP4_XTRA_EXTS:
        keywords: list[str] = []
        for item in _read_xtra_safe(source_path):
            if item.name == XTRA_KEYWORDS_NAME:
                keywords.extend(item.texts())
        return tuple(keywords), None
    if ext in ASF_EXTS:
        return parse_keyword_list(_lookup_ci(layout.format_tags, XTRA_KEYWORDS_NAME)), _parse_rating(
            _lookup_ci(layout.format_tags, XTRA_RATING_NAME)
        )
    if ext in MKV_EXTS:
        return parse_keyword_list(_lookup_ci(layout.format_tags, "KEYWORDS")), None
    return (), None


def write_result_tags(plan: EncodePlan, *, source_rating: int | None = None) -> tuple[bool, str]:
    """FFmpeg가 끝난 MP4/MOV .part에 Xtra를 씀. 미적용이면 (True, 사유)를 돌려 게이트를 면제"""
    if plan.container not in (CONTAINER_MP4, CONTAINER_MOV):
        return False, ""
    source_ext = os.path.splitext(plan.source_path)[1].lstrip(".").lower()
    base_items = _read_xtra_safe(plan.source_path) if source_ext in MP4_XTRA_EXTS else []
    detail_values: tuple[str, ...] = (
        (plan.tag_value,) if plan.tag_mode != ENCODE_TAG_MODE_OFF and plan.tag_value else ()
    )
    updates: list[XtraItem] = [
        text_item(XTRA_KEYWORDS_NAME, plan.tag_keywords),
        text_item(XTRA_ENCODE_DETAIL_NAME, detail_values),
    ]
    if source_rating is not None:
        updates.append(uint64_item(XTRA_RATING_NAME, source_rating))
    merged = merge_xtra_items(base_items, updates)
    if plan.tag_mode == ENCODE_TAG_MODE_OFF and not merged:
        return False, ""
    outcome = write_mp4_xtra(plan.part_path, merged)
    if outcome.applied:
        return False, ""
    return True, f"재인코딩 태그 미기록({outcome.reason})"


def merge_mp4_output_tags(part_path: str, container: str, summary: OutputSummary) -> OutputSummary:
    """MP4/MOV 출력의 Xtra 키워드와 상세 값을 읽어 게이트 요약에 반영"""
    if container not in (CONTAINER_MP4, CONTAINER_MOV):
        return summary
    keywords: list[str] = []
    has_detail = False
    for item in read_mp4_xtra_items(part_path):
        if item.name == XTRA_KEYWORDS_NAME:
            keywords.extend(item.texts())
        elif item.name == XTRA_ENCODE_DETAIL_NAME:
            for text in item.texts():
                if parse_encode_tag({ENCODE_TAG_KEY: text}) is not None:
                    has_detail = True
                    break
    return dataclasses.replace(summary, has_encode_tag=has_detail, keywords=tuple(keywords))


def build_tagged_plan(
    *,
    job: EncodeJob,
    settings: EncodeSettings,
    part_path: str,
    output_path: str,
    layout: StreamLayout,
    known_tag_names: Iterable[str] = (),
) -> tuple[EncodePlan, int | None]:
    """원본 태그를 읽어 재인코딩 태그 계획을 세우고 EncodePlan과 원본 별점을 돌려줌"""
    source_keywords, source_rating = read_source_tag_inputs(job.source_path, job.source_ext, layout)
    tag_plan = plan_encode_tags(
        mode=settings.encode_tag_mode,
        tag_name=settings.encode_tag_name,
        source_keywords=source_keywords,
        known_tag_names=known_tag_names,
        detail=build_encode_tag(settings.encoder, job.quality, datetime.now(timezone.utc)),
    )
    plan = build_encode_plan(
        source_path=job.source_path,
        part_path=part_path,
        output_path=output_path,
        source_ext=job.source_ext,
        encoder=settings.encoder,
        quality=job.quality,
        fps=job.fps,
        layout=layout,
        container_policy=settings.container_policy,
        tag_value=tag_plan.detail or "",
        output_size=job.output_size,
        scale_filter=job.scale_filter,
        maxrate_kbps=job.maxrate_kbps,
        tag_mode=tag_plan.mode,
        tag_name=tag_plan.tag_name,
        tag_keywords=tag_plan.keywords,
    )
    return plan, source_rating
