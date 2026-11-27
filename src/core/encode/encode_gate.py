"""사후 게이트 판정 모듈: 출력 무결성, 처리 표식, 절약 목적 크기 기준을 순수 함수로 판정

- 프로세스 실행과 파일 배치는 서비스 계층이 담당하고, 여기서는 probe 결과 요약과 판정 규칙만 다룸
- 판정 순서는 무결성, 처리 표식, 크기 순서. 표식 누락 결과를 배치하면 재처리 방지가 깨지므로 크기보다 먼저 검사
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.core.encode.encode_tag_plan import parse_keyword_list
from src.core.encode_tag import (
    DEFAULT_ENCODE_TAG_NAME,
    ENCODE_TAG_MODE_OFF,
    ENCODE_TAG_MODE_VISIBLE,
    has_tag_name_keyword,
    parse_encode_tag,
)
from src.core.probe_parser import format_tags, select_video_stream, video_duration_sec
from src.core.smart_action import SmartAction

DATA_CODEC_TYPE: str = "data"
DURATION_TOLERANCE_MIN_SEC: float = 0.5
DURATION_TOLERANCE_RATIO: float = 0.005
# - 변환 우선 인코딩 결과가 원본 크기의 이 배수를 넘으면 그대로 배치하지 않고 보류(제안 2.3절 3단계)
TRANSFORM_SIZE_SURGE_RATIO: float = 1.30


class GatePurpose(Enum):
    """결과 검사 목적. 절약 목적만 결과가 원본보다 크면 폐기하고, 나머지는 급증 상한만 본다"""

    SAVING = "saving"
    TRANSFORM = "transform"
    KEYFRAME_FIX = "keyframe_fix"
    COMPAT = "compat"


class GateVerdict(Enum):
    """사후 게이트 판정 결과"""

    PASS = "pass"
    FAIL_INTEGRITY = "fail_integrity"
    FAIL_TAG = "fail_tag"
    DISCARD_LARGER = "discard_larger"
    PASS_SURGE = "pass_surge"


@dataclass(frozen=True, slots=True)
class OutputSummary:
    """출력 파일 probe에서 게이트에 필요한 값만 뽑은 요약"""

    stream_count: int
    duration_sec: float
    has_encode_tag: bool
    keywords: tuple[str, ...] = ()


def _lookup_keywords(tags: Mapping[str, str]) -> tuple[str, ...]:
    for key, value in tags.items():
        if key.lower() == "keywords":
            return parse_keyword_list(value)
    return ()


def summarize_probe(probe: Mapping[str, Any]) -> OutputSummary:
    """ffprobe JSON(streams, format)에서 스트림 수, 길이, 처리 표식, 키워드 여부를 요약"""
    raw_streams = probe.get("streams")
    streams: list[dict[str, Any]] = raw_streams if isinstance(raw_streams, list) else []
    raw_format = probe.get("format")
    fmt: Mapping[str, Any] = raw_format if isinstance(raw_format, Mapping) else {}
    tags = format_tags(fmt)
    # - 데이터 스트림은 결정 8에서 제외 대상이며, `-map_chapters 0`이면 MP4 muxer가 챕터 트랙을 데이터 스트림으로 다시 씀
    return OutputSummary(
        stream_count=sum(1 for stream in streams if stream.get("codec_type") != DATA_CODEC_TYPE),
        duration_sec=video_duration_sec(select_video_stream(streams), fmt),
        has_encode_tag=parse_encode_tag(tags) is not None,
        keywords=_lookup_keywords(tags),
    )


def purpose_for_action(action: SmartAction, *, manual_active_frame_applied: bool = False) -> GatePurpose:
    """권장 처리 종류에서 게이트 목적을 정함(결정 10)

    - 품질 인코딩(직접 지정)은 원본 화질을 바꾸는 것이 목적이므로 변환 우선
    - 사용자 지정 활성 화면을 자른 축소 인코딩도 화면을 바꾸는 것이 목적이므로 변환 우선
    - 그 밖의 축소 인코딩(출력 해상도 직접 지정, 최대 해상도 초과, 자동 후보)은 절약 목적
    """
    if action in (SmartAction.ENCODE_OPT_KF, SmartAction.ENCODE_LOW_KF):
        return GatePurpose.KEYFRAME_FIX
    if action is SmartAction.ENCODE_COMPAT:
        return GatePurpose.COMPAT
    if action is SmartAction.ENCODE_MANUAL:
        return GatePurpose.TRANSFORM
    if manual_active_frame_applied and action is SmartAction.ENCODE_DOWNSCALE:
        return GatePurpose.TRANSFORM
    return GatePurpose.SAVING


def purpose_for_evaluation(
    action: SmartAction, keyframe_exceeded: bool, *, manual_active_frame_applied: bool = False
) -> GatePurpose:
    """평가 결과에서 게이트 목적을 정함. 키프레임 간격이 초과된 영상은 판정 종류와 무관하게 KF 교정(결정 10 개정)"""
    if keyframe_exceeded:
        return GatePurpose.KEYFRAME_FIX
    return purpose_for_action(action, manual_active_frame_applied=manual_active_frame_applied)


def duration_tolerance_sec(source_duration_sec: float) -> float:
    """길이 차 허용치: 최소 0.5초와 원본 길이의 0.5% 중 큰 값"""
    return max(DURATION_TOLERANCE_MIN_SEC, source_duration_sec * DURATION_TOLERANCE_RATIO)


def _has_required_tag(output: OutputSummary, tag_mode: str, tag_name: str, tag_exempt: bool) -> bool:
    if tag_exempt or tag_mode == ENCODE_TAG_MODE_OFF:
        return True
    if not output.has_encode_tag:
        return False
    if tag_mode == ENCODE_TAG_MODE_VISIBLE:
        return has_tag_name_keyword(output.keywords, (tag_name,))
    return True


_TRANSFORM_SIZE_SURGE_PERCENT: int = round(TRANSFORM_SIZE_SURGE_RATIO * 100)


def exceeds_transform_surge(source_size_bytes: int, output_size_bytes: int) -> bool:
    """변환 우선 결과가 원본 크기의 급증 상한을 넘었는지 판정

    - 배수 대신 백분율 정수 비교를 써서 경계값(1.30x)이 부동소수점 오차로 뒤집히지 않게 함
    - 원본 크기를 모르면 비교할 수 없으므로 넘지 않은 것으로 봄
    """
    if source_size_bytes <= 0:
        return False
    return output_size_bytes * 100 > source_size_bytes * _TRANSFORM_SIZE_SURGE_PERCENT


def judge_result(
    *,
    output: OutputSummary | None,
    expected_stream_count: int,
    source_duration_sec: float,
    source_size_bytes: int,
    output_size_bytes: int,
    purpose: GatePurpose,
    tag_mode: str = ENCODE_TAG_MODE_VISIBLE,
    tag_name: str = DEFAULT_ENCODE_TAG_NAME,
    tag_exempt: bool = False,
) -> GateVerdict:
    """결과 파일을 배치해도 되는지 판정. output이 None이면 probe 실패로 무결성 실패"""
    if output is None:
        return GateVerdict.FAIL_INTEGRITY
    if output.stream_count != expected_stream_count:
        return GateVerdict.FAIL_INTEGRITY
    if abs(output.duration_sec - source_duration_sec) > duration_tolerance_sec(source_duration_sec):
        return GateVerdict.FAIL_INTEGRITY
    if not _has_required_tag(output, tag_mode, tag_name, tag_exempt):
        return GateVerdict.FAIL_TAG
    if purpose is GatePurpose.SAVING:
        if output_size_bytes >= source_size_bytes:
            return GateVerdict.DISCARD_LARGER
        return GateVerdict.PASS
    if exceeds_transform_surge(source_size_bytes, output_size_bytes):
        return GateVerdict.PASS_SURGE
    return GateVerdict.PASS
