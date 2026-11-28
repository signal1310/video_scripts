"""화질 분석, 검은 여백 감지 대상 선정과 분석 요청 생성 모듈

- 표에 보이는 평가 결과에서 지금 설정으로 측정할 영상만 고르고 파일 식별 값을 읽어 요청을 만듦
- 컨트롤러는 실행과 진행 표시만 맡고, 대상 판정 규칙은 이 모듈에 모아 큐와 화면이 같은 기준을 씀
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence

from src.core.analysis.file_fingerprint import compute_fingerprint
from src.core.config import FRAME_ANALYSIS_MIN_TIER_ALL
from src.core.encode.encode_impact import parse_stream_summary_layout
from src.core.models import VideoEvaluation
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY, find_source_tier
from src.core.workspace_paths import resolve_source_filepath
from src.gui.frame_analysis_skip import skip_reason_for_batch_analysis
from src.service.frame_analysis_service import FrameAnalysisRequest

NO_FRAME_TARGETS_TEXT: str = (
    "화질 분석 대상이 없습니다. "
    "예외 규칙, 이미 인코딩, HDR, 인터레이스, 직접 지정 영상은 건너뜁니다."
)
NO_CROP_TARGETS_TEXT: str = (
    "여백 감지 대상이 없습니다. 예외 규칙, 이미 인코딩, HDR, 인터레이스 영상은 건너뜁니다."
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


def frame_estimate_text(
    evaluations: Sequence[VideoEvaluation], min_tier: str, *, skip: bool = True
) -> str:
    """분석 모드 팝업에 쓸 화질 분석 대상 수 문구. 자동 배치가 건너뛸 영상을 빼고 셈"""
    count = sum(
        1
        for ev in evaluations
        if not is_below_frame_min_tier(ev.video.width, ev.video.height, min_tier)
        and not (skip and skip_reason_for_batch_analysis(ev) is not None)
    )
    return f"대상 {count}개"


def build_requests(
    evaluations: Sequence[VideoEvaluation],
    root_dir: str,
    overrides: frozenset[str],
    *,
    cached: Sequence[FrameAnalysisRequest] = (),
    is_cancelled: Callable[[], bool] | None = None,
) -> list[FrameAnalysisRequest]:
    """평가 결과와 원본 파일 정보로 화질 분석 요청 목록 생성, 없는 파일은 건너뜀

    - 내용 지문은 여기서 파일을 읽어 채우고, 읽지 못하면 비워 두어 실행 서비스가 다시 시도하게 함
    - cached에 같은 파일(이름, 크기, 수정 시각)의 요청이 있으면 읽지 않고 그 지문을 씀
    - is_cancelled가 참이 되면 남은 파일을 읽지 않고 지금까지 만든 목록을 돌려줌
    """
    known = {request.key: request.fingerprint for request in cached if request.fingerprint}
    requests: list[FrameAnalysisRequest] = []
    for ev in evaluations:
        if is_cancelled is not None and is_cancelled():
            break
        path = resolve_source_filepath(root_dir, ev.video.filename)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        key = (ev.video.filename, stat.st_size, stat.st_mtime_ns)
        fingerprint = known.get(key)
        if fingerprint is None:
            try:
                fingerprint = compute_fingerprint(path, stat.st_size)
            except OSError:
                fingerprint = ""
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
                is_hdr=ev.video.is_hdr,
                fingerprint=fingerprint,
            )
        )
    return requests
