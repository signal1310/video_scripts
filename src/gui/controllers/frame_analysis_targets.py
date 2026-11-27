"""화질 분석, 검은 여백 감지 대상 선정과 분석 요청 생성 모듈

- 표에 보이는 평가 결과에서 지금 설정으로 측정할 영상만 고르고 파일 식별 값을 읽어 요청을 만듦
- 컨트롤러는 실행과 진행 표시만 맡고, 대상 판정 규칙은 이 모듈에 모아 큐와 화면이 같은 기준을 씀
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from src.core.config import FRAME_ANALYSIS_MIN_TIER_ALL
from src.core.encode.encode_impact import parse_stream_summary_layout
from src.core.models import VideoEvaluation
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY, find_source_tier
from src.core.smart_action import SmartAction
from src.core.workspace_paths import resolve_source_filepath
from src.service.frame_analysis_service import FrameAnalysisRequest

NO_FRAME_TARGETS_TEXT: str = (
    "화질 분석 대상이 없습니다. "
    "예외 규칙, 이미 인코딩, HDR, 인터레이스, 직접 지정 영상은 건너뜁니다."
)
NO_CROP_TARGETS_TEXT: str = (
    "검은 여백 감지 대상이 없습니다. 예외 규칙, 이미 인코딩, HDR, 인터레이스 영상은 건너뜁니다."
)
_SKIP_ACTIONS: frozenset[SmartAction] = frozenset(
    {SmartAction.PASS_EXCEPTION, SmartAction.PASS_ALREADY_ENCODED, SmartAction.PASS_HDR}
)


def is_below_frame_min_tier(width: int, height: int, min_tier: str) -> bool:
    """원본 해상도 등급이 화질 분석 대상 해상도 미만인지 판정

    - 전체(all)는 어떤 해상도도 미만이 아님
    - 규격 경계는 해상도 규격 탐색과 같은 정의를 씀
    """
    if min_tier == FRAME_ANALYSIS_MIN_TIER_ALL:
        return False
    limit = RESOLUTION_TIER_BY_KEY.get(min_tier)
    if limit is None:
        return False
    source = find_source_tier(width, height)
    return source is None or source.short_side < limit.short_side


def is_interlaced(ev: VideoEvaluation) -> bool:
    """원본이 인터레이스 영상인지 스트림 요약의 필드 순서로 판정"""
    layout = parse_stream_summary_layout(ev.video.stream_summary)
    return layout is not None and layout.is_interlaced


def skip_for_frame_analysis(ev: VideoEvaluation) -> bool:
    """전체 화질 분석에서 자동으로 건너뛸 영상인지 판정

    - 예외 규칙, 이미 인코딩, HDR 보존, 인터레이스 원본은 화질 분석 대상이 아님
    - 출력 해상도를 직접 지정한 영상(축소 안 함 포함)은 판정을 덮어쓰므로 측정하지 않음
    """
    return ev.smart_action in _SKIP_ACTIONS or is_interlaced(ev) or bool(ev.manual_target)


def skip_for_crop_scan(ev: VideoEvaluation) -> bool:
    """검은 여백 감지에서 자동으로 건너뛸 영상인지 판정

    - 직접 지정 영상은 판정을 덮어쓰지 않으므로 검은 여백은 정상 감지
    """
    return ev.smart_action in _SKIP_ACTIONS or is_interlaced(ev)


def frame_estimate_text(
    evaluations: Sequence[VideoEvaluation], min_tier: str, *, skip: bool = True
) -> str:
    """분석 모드 팝업에 쓸 화질 분석 대상 수 문구. 건너뛸 영상을 빼고 셈"""
    count = sum(
        1
        for ev in evaluations
        if not is_below_frame_min_tier(ev.video.width, ev.video.height, min_tier)
        and not (skip and skip_for_frame_analysis(ev))
    )
    return f"대상 {count}개"


def build_requests(
    evaluations: Sequence[VideoEvaluation],
    root_dir: str,
    overrides: frozenset[str],
) -> list[FrameAnalysisRequest]:
    """평가 결과와 원본 파일 정보로 화질 분석 요청 목록 생성, 없는 파일은 건너뜀"""
    requests: list[FrameAnalysisRequest] = []
    for ev in evaluations:
        path = resolve_source_filepath(root_dir, ev.video.filename)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        layout = parse_stream_summary_layout(ev.video.stream_summary)
        requests.append(
            FrameAnalysisRequest(
                filename=ev.video.filename,
                path=path,
                width=ev.video.width,
                height=ev.video.height,
                duration_sec=ev.video.duration,
                filesize_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                has_vr_metadata=layout is not None and layout.is_vr,
                manual_vr=path in overrides,
            )
        )
    return requests
