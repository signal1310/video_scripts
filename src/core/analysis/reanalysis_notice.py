"""분석 방식(빠른, 정밀)에 따른 하단 재분석 안내 개수, 예상 시간, 문구 계산 순수 함수 모듈"""

from collections.abc import Iterable
from dataclasses import dataclass

from src.core.messages import (
    format_full_analysis_needed,
    format_reanalysis_breakdown,
    format_reanalysis_notice,
)
from src.core.models import SECONDS_PER_MINUTE, VideoEvaluation


@dataclass(frozen=True, slots=True)
class ReanalysisNoticePlan:
    """하단 재분석 안내 문구와 툴팁, 분석 방식별 대상 수"""

    reanalyze_count: int
    full_only_count: int
    expected_minutes: int | None
    text: str
    tooltip: str


def count_keyframe_missing(evaluations: Iterable[VideoEvaluation]) -> int:
    """평가 목록에서 키프레임 간격이 미측정인 영상 수를 반환"""
    return sum(
        1
        for ev in evaluations
        if ev.video.keyframe_interval is None or ev.video.keyframe_interval < 0
    )


def _estimate_minutes(count: int, sec_per_item: float | None) -> int | None:
    if count <= 0 or sec_per_item is None or sec_per_item <= 0:
        return None
    return max(1, round((count * sec_per_item) / SECONDS_PER_MINUTE))


def build_reanalysis_notice_plan(
    format_changed: int,
    keyframe_missing: int,
    *,
    include_keyframe: bool,
    fast_sec_per_item: float | None,
    full_sec_per_item: float | None,
) -> ReanalysisNoticePlan:
    """분석 방식(빠른/정밀)에 맞춰 재분석 대상 수, 예상 시간, 안내 문구와 툴팁을 생성"""
    if include_keyframe:
        reanalyze_count = format_changed + keyframe_missing
        if reanalyze_count <= 0:
            return ReanalysisNoticePlan(0, 0, None, "", "")
        minutes = _estimate_minutes(reanalyze_count, full_sec_per_item)
        return ReanalysisNoticePlan(
            reanalyze_count=reanalyze_count,
            full_only_count=0,
            expected_minutes=minutes,
            text=format_reanalysis_notice(reanalyze_count, minutes),
            tooltip=format_reanalysis_breakdown(
                format_changed, keyframe_missing, include_keyframe=True
            ),
        )

    if format_changed <= 0 and keyframe_missing <= 0:
        return ReanalysisNoticePlan(0, 0, None, "", "")

    if format_changed > 0:
        minutes = _estimate_minutes(format_changed, fast_sec_per_item)
        base_text = format_reanalysis_notice(format_changed, minutes)
        text = (
            f"{base_text} / {format_full_analysis_needed(keyframe_missing, None)}"
            if keyframe_missing > 0
            else base_text
        )
    else:
        minutes = _estimate_minutes(keyframe_missing, full_sec_per_item)
        text = format_full_analysis_needed(keyframe_missing, minutes)

    return ReanalysisNoticePlan(
        reanalyze_count=format_changed,
        full_only_count=keyframe_missing,
        expected_minutes=minutes,
        text=text,
        tooltip=format_reanalysis_breakdown(
            format_changed, keyframe_missing, include_keyframe=False
        ),
    )
