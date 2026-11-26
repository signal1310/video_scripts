"""화질 분석 캐시 행과 분석 요청 표현, 행/결과 변환

- 저장소와 실행 서비스는 이 표현을 그대로 쓰고, SQLite 입출력만 `frame_analysis_store.py`가 맡음
- 열 순서(`RECORD_COLUMNS`)를 한 곳에 두어 스키마와 행 변환이 어긋나지 않게 함
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from src.core.analysis.frame_analysis import (
    ActiveRegion,
    TierScore,
    tier_scores_from_json,
    tier_scores_to_json,
)
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult

FileKey = tuple[str, int, int]

RECORD_COLUMNS: tuple[str, ...] = (
    "filename",
    "filesize_bytes",
    "mtime_ns",
    "active_width",
    "active_height",
    "active_x",
    "active_y",
    "has_black_bars",
    "metric_used",
    "vmaf_model",
    "tier_scores_json",
    "candidate_tier",
    "valid_segments",
    "total_segments",
    "unmeasurable",
    "is_vr_protected",
    "measured_at",
    "analysis_version",
    "margins_vary",
    "dark_outliers_ignored",
)


@dataclass(frozen=True, slots=True)
class FrameAnalysisRecord:
    """화질 분석 캐시 한 행(파일 식별 키와 측정 결과)"""

    filename: str
    filesize_bytes: int
    mtime_ns: int
    active_width: int
    active_height: int
    active_x: int
    active_y: int
    has_black_bars: bool
    metric_used: str
    vmaf_model: str
    tier_scores: tuple[TierScore, ...]
    candidate_tier: str | None
    valid_segments: int
    total_segments: int
    unmeasurable: bool
    is_vr_protected: bool
    measured_at: str
    analysis_version: int
    # - 구간별 여백 차이 여부. 기존 저장 값과 이전 DB에는 없는 정보라 기본값을 둠
    margins_vary: bool = False
    # - 어두운 근거로 지점을 무시해 안쪽 경계를 채택했는지. 기존 저장 값과 이전 DB에는 없는 정보라 기본값을 둠
    dark_outliers_ignored: bool = False

    @property
    def key(self) -> FileKey:
        """파일 식별 키(이름, 크기, 수정 시각) 반환"""
        return (self.filename, self.filesize_bytes, self.mtime_ns)

    @property
    def active_region(self) -> ActiveRegion:
        """저장된 값으로 복원한 활성 화면 영역"""
        return ActiveRegion(
            width=self.active_width,
            height=self.active_height,
            x=self.active_x,
            y=self.active_y,
            has_black_bars=self.has_black_bars,
            margins_vary=self.margins_vary,
            dark_outliers_ignored=self.dark_outliers_ignored,
        )


@dataclass(frozen=True, slots=True)
class FrameAnalysisRequest:
    """화질 분석 대상 한 개의 파일 식별 값과 측정 입력"""

    filename: str
    path: str
    width: int
    height: int
    duration_sec: float
    filesize_bytes: int
    mtime_ns: int
    has_vr_metadata: bool = False
    manual_vr: bool = False

    @property
    def key(self) -> FileKey:
        """파일 식별 키(이름, 크기, 수정 시각) 반환"""
        return (self.filename, self.filesize_bytes, self.mtime_ns)


def row_to_record(row: sqlite3.Row | tuple[object, ...]) -> FrameAnalysisRecord:
    """SQLite 행 값을 캐시 레코드로 변환"""
    values = dict(zip(RECORD_COLUMNS, row, strict=True))
    candidate = values["candidate_tier"]
    return FrameAnalysisRecord(
        filename=str(values["filename"]),
        filesize_bytes=int(values["filesize_bytes"]),
        mtime_ns=int(values["mtime_ns"]),
        active_width=int(values["active_width"]),
        active_height=int(values["active_height"]),
        active_x=int(values["active_x"]),
        active_y=int(values["active_y"]),
        has_black_bars=bool(values["has_black_bars"]),
        metric_used=str(values["metric_used"]),
        vmaf_model=str(values["vmaf_model"]),
        tier_scores=tier_scores_from_json(str(values["tier_scores_json"])),
        candidate_tier=None if candidate is None else str(candidate),
        valid_segments=int(values["valid_segments"]),
        total_segments=int(values["total_segments"]),
        unmeasurable=bool(values["unmeasurable"]),
        is_vr_protected=bool(values["is_vr_protected"]),
        measured_at=str(values["measured_at"]),
        analysis_version=int(values["analysis_version"]),
        margins_vary=bool(values["margins_vary"]),
        dark_outliers_ignored=bool(values["dark_outliers_ignored"]),
    )


def record_to_row(record: FrameAnalysisRecord) -> tuple[object, ...]:
    """캐시 레코드를 SQLite 저장 값으로 변환. 열 순서는 `RECORD_COLUMNS`와 같음"""
    return (
        record.filename,
        record.filesize_bytes,
        record.mtime_ns,
        record.active_width,
        record.active_height,
        record.active_x,
        record.active_y,
        int(record.has_black_bars),
        record.metric_used,
        record.vmaf_model,
        tier_scores_to_json(record.tier_scores),
        record.candidate_tier,
        record.valid_segments,
        record.total_segments,
        int(record.unmeasurable),
        int(record.is_vr_protected),
        record.measured_at,
        record.analysis_version,
        int(record.margins_vary),
        int(record.dark_outliers_ignored),
    )


def record_from_result(
    request: FrameAnalysisRequest,
    result: FrameAnalysisResult,
    *,
    measured_at: str,
) -> FrameAnalysisRecord:
    """분석 결과를 캐시 행으로 변환"""
    region = result.active_region
    return FrameAnalysisRecord(
        filename=request.filename,
        filesize_bytes=request.filesize_bytes,
        mtime_ns=request.mtime_ns,
        active_width=region.width,
        active_height=region.height,
        active_x=region.x,
        active_y=region.y,
        has_black_bars=region.has_black_bars,
        metric_used=result.metric_used,
        vmaf_model=result.vmaf_model,
        tier_scores=result.tier_scores,
        candidate_tier=result.candidate_tier,
        valid_segments=result.valid_segment_count,
        total_segments=result.total_segment_count,
        unmeasurable=result.unmeasurable,
        is_vr_protected=result.is_vr_protected,
        measured_at=measured_at,
        analysis_version=result.analysis_version,
        margins_vary=region.margins_vary,
        dark_outliers_ignored=region.dark_outliers_ignored,
    )


def result_from_record(record: FrameAnalysisRecord) -> FrameAnalysisResult:
    """캐시 행을 분석 결과로 복원"""
    return FrameAnalysisResult(
        active_region=record.active_region,
        metric_used=record.metric_used,
        vmaf_model=record.vmaf_model,
        tier_scores=record.tier_scores,
        candidate_tier=record.candidate_tier,
        valid_segment_count=record.valid_segments,
        total_segment_count=record.total_segments,
        unmeasurable=record.unmeasurable,
        is_vr_protected=record.is_vr_protected,
        analysis_version=record.analysis_version,
    )
