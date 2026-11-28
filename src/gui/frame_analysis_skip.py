"""화질 분석 건너뜀 사유 판정과 사유 문구 모듈

- 예외 규칙, 이미 인코딩, HDR 보존, 인터레이스, 직접 지정의 다섯 사유를 한 곳에서 정의해
  대상 선정과 표 표시가 같은 기준을 씀
- Qt에 의존하지 않아 gui.table과 gui.controllers가 같이 임포트해도 순환이 없음
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

from src.core.encode.encode_impact import parse_stream_summary_layout
from src.core.models import VideoEvaluation
from src.core.smart_action import SmartAction

# - 표 재적재와 실행 대상 선정이 같은 영상의 스트림 요약을 되풀이해 해석하므로 결과를 재사용.
#   키가 요약 JSON 문자열 자체라 파일이 바뀌면 자연히 다른 항목이 되고, 항목 수만 제한해 둠
STREAM_LAYOUT_CACHE_SIZE: int = 4096


class SkipKind(Enum):
    """화질 분석에서 영상을 건너뛰는 사유 종류"""

    EXCEPTION = "exception"
    ALREADY_ENCODED = "already_encoded"
    HDR = "hdr"
    INTERLACED = "interlaced"
    MANUAL_TARGET = "manual_target"


# - 완료 상태 줄의 사유별 표기(TERMINOLOGY 6.15절)
SKIP_KIND_STATUS_LABELS: dict[SkipKind, str] = {
    SkipKind.EXCEPTION: "예외 규칙",
    SkipKind.ALREADY_ENCODED: "이미 인코딩",
    SkipKind.HDR: "HDR",
    SkipKind.INTERLACED: "인터레이스",
    SkipKind.MANUAL_TARGET: "직접 지정",
}

# - 축소 후보 셀의 사유별 툴팁 문장(TERMINOLOGY 6.15절)
_SKIP_KIND_TOOLTIPS: dict[SkipKind, str] = {
    SkipKind.EXCEPTION: "이 영상은 예외 규칙 대상이라 화질 분석을 하지 않습니다.",
    SkipKind.ALREADY_ENCODED: "이 영상은 이미 인코딩한 영상이라 화질 분석을 다시 하지 않습니다.",
    SkipKind.HDR: "HDR 영상은 원본을 그대로 보존하므로 화질 분석을 하지 않습니다.",
    SkipKind.INTERLACED: "인터레이스 영상은 원본을 그대로 보존하므로 화질 분석을 하지 않습니다.",
    SkipKind.MANUAL_TARGET: "이 영상은 출력 해상도를 직접 지정했으므로 화질 분석을 하지 않습니다.",
}


def is_interlaced(ev: VideoEvaluation) -> bool:
    """원본이 인터레이스 영상인지 스트림 요약의 필드 순서로 판정"""
    return _is_interlaced_summary(ev.video.stream_summary)


@lru_cache(maxsize=STREAM_LAYOUT_CACHE_SIZE)
def _is_interlaced_summary(stream_summary: str) -> bool:
    """스트림 요약 JSON 문자열의 인터레이스 여부"""
    layout = parse_stream_summary_layout(stream_summary)
    return layout is not None and layout.is_interlaced


def skip_reason_for_frame_analysis(ev: VideoEvaluation) -> SkipKind | None:
    """전체 화질 분석에서 건너뛰는 영상의 사유, 대상이면 None

    - 예외 규칙, 이미 인코딩, HDR 보존, 인터레이스 원본은 화질 분석 대상이 아님
    - 출력 해상도를 직접 지정한 영상(축소 안 함 포함)은 판정을 덮어쓰므로 측정하지 않음
    """
    if ev.smart_action == SmartAction.PASS_EXCEPTION:
        return SkipKind.EXCEPTION
    if ev.smart_action == SmartAction.PASS_ALREADY_ENCODED:
        return SkipKind.ALREADY_ENCODED
    if ev.smart_action == SmartAction.PASS_HDR:
        return SkipKind.HDR
    if is_interlaced(ev):
        return SkipKind.INTERLACED
    if ev.manual_target:
        return SkipKind.MANUAL_TARGET
    return None


def skip_reason_for_crop_scan(ev: VideoEvaluation) -> SkipKind | None:
    """검은 여백 감지에서 건너뛰는 영상의 사유, 대상이면 None

    - 직접 지정 영상은 판정을 덮어쓰지 않으므로 검은 여백은 정상 감지
    """
    reason = skip_reason_for_frame_analysis(ev)
    if reason is SkipKind.MANUAL_TARGET:
        return None
    return reason


def skip_reason_tooltip(reason: SkipKind) -> str:
    """사유 종류의 축소 후보 셀 툴팁 문장"""
    return _SKIP_KIND_TOOLTIPS[reason]


# - 완료 상태 줄의 건너뜀 안내 문구와 수동 실행의 HDR 안내. 사유는 처음 나타난 순서로 나열
SKIPPED_NOTE_TEMPLATE: str = "건너뜀 {count}개({reasons})"
MANUAL_HDR_SKIP_NOTICE: str = "HDR 영상 {count}개는 건너뛰었습니다."


def skip_counts_text(counts: Mapping[SkipKind, int]) -> str:
    """건너뜀 사유별 개수 문구. 시작한 배치에서 먼저 나타난 사유부터 나열"""
    return ", ".join(f"{SKIP_KIND_STATUS_LABELS[kind]} {count}" for kind, count in counts.items())


def count_skip_reasons(reasons: Iterable[SkipKind | None]) -> dict[SkipKind, int]:
    """영상별 사유 목록(대상은 None)의 사유별 개수. 처음 나타난 사유부터 나열"""
    counts: dict[SkipKind, int] = {}
    for reason in reasons:
        if reason is not None:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


@dataclass(frozen=True, slots=True)
class RunSelection:
    """실행 대상과 완료 상태 줄에 알릴 사유별 건너뜀 수, 강제 실행이 뺀 HDR 수"""

    targets: tuple[VideoEvaluation, ...]
    skipped_counts: dict[SkipKind, int]
    forced_hdr_count: int = 0


def select_run_targets(
    evals: Sequence[VideoEvaluation],
    reasons: Sequence[SkipKind | None],
    *,
    crop_only: bool,
    force: bool,
) -> RunSelection:
    """영상별 사유로 실행 대상과 건너뜀 집계를 한 번에 계산

    - 강제 실행은 예외 규칙, 이미 인코딩, 인터레이스, 직접 지정을 무시하고 측정
    - 화질 분석의 강제 실행만 HDR을 빼며, 그 수는 시작 안내로 알려 완료 줄과 겹치지 않게 함
    - 여백 감지는 HDR 축소 판정과 무관하므로 강제 실행에서 HDR도 측정
    """
    if not force:
        targets = tuple(ev for ev, reason in zip(evals, reasons, strict=True) if reason is None)
        return RunSelection(targets, count_skip_reasons(reasons))
    if crop_only:
        return RunSelection(tuple(evals), {})
    targets = tuple(
        ev for ev, reason in zip(evals, reasons, strict=True) if reason is not SkipKind.HDR
    )
    return RunSelection(targets, {}, len(evals) - len(targets))
