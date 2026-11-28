"""화질 분석 실행 중 워커가 넘기는 이벤트와 완료 상태 문구

- 진행, 단계 진행, 종료 이벤트는 워커 스레드가 만들어 화면 스레드로 넘기는 값만 담음
- 완료 상태 줄 문구는 실행 종류와 무관하게 한 곳에서 만들어 화면과 기록이 같은 표기를 씀
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from src.core.analysis.frame_analysis import count_failed_crop_scans
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.gui.frame_analysis_skip import SKIPPED_NOTE_TEMPLATE, SkipKind, skip_counts_text

FRAME_ANALYSIS_LABEL: str = "화질 분석"


@dataclass(frozen=True, slots=True)
class FrameAnalysisProgress:
    """화질 분석 진행 한 건의 완료 수, 전체 수, 파일 이름과 완료된 영상의 결과"""

    completed: int
    total: int
    filename: str
    result: FrameAnalysisResult | None = None


@dataclass(frozen=True, slots=True)
class FrameAnalysisStageProgress:
    """현재 영상의 세부 단계 이름과 단계 안 진행 수"""

    completed: int
    total: int
    filename: str
    stage: str
    stage_done: int
    stage_total: int


@dataclass(frozen=True, slots=True)
class FrameAnalysisFinished:
    """화질 분석 실행 종료: 캐시 결과, 취소 여부, 오류 문구, 요청 수, 캐시 재사용 수, 감지 전용 여부"""

    results: dict[str, FrameAnalysisResult]
    cancelled: bool = False
    error: str = ""
    total: int = 0
    cached_count: int = 0
    crop_only: bool = False


FrameAnalysisEvent = FrameAnalysisProgress | FrameAnalysisStageProgress | FrameAnalysisFinished
FrameAnalysisSink = Callable[[FrameAnalysisEvent], None]


def completion_status_text(
    finished: FrameAnalysisFinished,
    *,
    label: str = FRAME_ANALYSIS_LABEL,
    skip_counts: Mapping[SkipKind, int] | None = None,
) -> str:
    """화질 분석 또는 검은 여백 감지 완료 상태 줄. 캐시 재사용, 감지 실패, 건너뜀 수를 있으면 함께 알림"""
    notes = [f"캐시 재사용 {finished.cached_count}개"] if finished.cached_count > 0 else []
    if (failed := count_failed_crop_scans(finished.results.values())) > 0:
        notes.append(f"감지 실패 {failed}개")
    if skip_counts:
        total = sum(skip_counts.values())
        notes.append(SKIPPED_NOTE_TEMPLATE.format(count=total, reasons=skip_counts_text(skip_counts)))
    detail = f" ({', '.join(notes)})" if notes else ""
    return f"{label} 완료: {finished.total}개{detail}" if finished.total > 0 else ""
