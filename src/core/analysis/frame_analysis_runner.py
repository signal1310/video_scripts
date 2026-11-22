"""화질 분석 FFmpeg 실행 흐름과 결과 데이터 모델 모듈

- 구간 선택, 검은 화면 제외, 검은 띠 감지, 왕복 SSIM 선행 필터, VMAF 확인 순서를 조율
- 명령 구성과 파싱은 `frame_analysis.py`, `quality_probe.py`, `sample_points.py`를 재사용
- 취소는 `threading.Event`를 실행기와 각 단계에서 확인해 실행 중인 FFmpeg 프로세스를 즉시 종료
"""

from __future__ import annotations

import contextlib
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from src.core.analysis.frame_analysis import (
    FRAME_ANALYSIS_VERSION,
    ROUNDTRIP_SSIM_THRESHOLD,
    ROUNDTRIP_VMAF_THRESHOLD,
    ActiveRegion,
    CropBox,
    TierScore,
    build_blackdetect_command,
    build_cropdetect_command,
    build_roundtrip_command,
    candidate_tiers_for,
    is_vr_protected,
    merge_crop_union,
    parse_blackdetect_ratio,
    parse_cropdetect_boxes,
    select_candidate_tier,
)
from src.core.quality_probe import (
    LOG_NAME_SSIM,
    LOG_NAME_VMAF,
    METRIC_SSIM,
    METRIC_VMAF,
    parse_ssim_stats,
    parse_vmaf_log,
    resolve_metric_name,
    vmaf_model_for,
)
from src.core.resolution_tiers import ResolutionTier
from src.core.sample_points import (
    DEFAULT_SEGMENT_COUNT,
    DEFAULT_SEGMENT_DURATION_SEC,
    SegmentQuality,
    is_invalid_segment,
    sample_segments,
)

POLL_INTERVAL_SEC: float = 0.1
PROCESS_TERMINATE_TIMEOUT_SEC: float = 5.0
_CANCEL_MESSAGE: str = "화질 분석이 취소되었습니다"


class StderrCommandRunner(Protocol):
    """표준 오류를 함께 돌려주는 외부 명령 실행기. 취소와 제한 시간 처리는 구현이 맡음"""

    def __call__(self, args: list[str], work_dir: str) -> tuple[int, str]:
        """명령을 실행하고 종료 코드와 표준 오류를 반환"""
        ...


def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
    """실행 중인 자식 프로세스를 종료하고 회수"""
    if proc.poll() is None:
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=PROCESS_TERMINATE_TIMEOUT_SEC)


class ProcessStderrRunner:
    """표준 오류를 임시 파일로 받아 두는 기본 실행기

    - 파이프 대신 임시 파일을 써서 cropdetect처럼 줄이 많은 출력에서도 교착이 생기지 않게 함
    - 취소 신호를 받으면 실행 중인 프로세스를 kill하고 `InterruptedError`를 냄
    """

    def __init__(
        self,
        *,
        cancel_event: threading.Event | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        self._cancel_event: threading.Event | None = cancel_event
        self._timeout_sec: float | None = timeout_sec

    def __call__(self, args: list[str], work_dir: str) -> tuple[int, str]:
        """명령을 실행하고 (종료 코드, 표준 오류) 반환. 취소 시 `InterruptedError`, 제한 시간 초과 시 `TimeoutError`"""
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        with tempfile.TemporaryFile() as stderr_file:
            proc = subprocess.Popen(
                args,
                cwd=work_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
                creationflags=creationflags,
            )
            try:
                returncode = self._wait(proc)
            except BaseException:
                _terminate_process(proc)
                raise
            stderr_file.seek(0)
            return returncode, stderr_file.read().decode("utf-8", errors="replace")

    def _wait(self, proc: subprocess.Popen[bytes]) -> int:
        deadline = None if self._timeout_sec is None else time.monotonic() + self._timeout_sec
        while True:
            if self._cancel_event is not None and self._cancel_event.is_set():
                raise InterruptedError(_CANCEL_MESSAGE)
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"명령 실행 시간({self._timeout_sec:.0f}초)을 초과했습니다")
            try:
                return proc.wait(timeout=POLL_INTERVAL_SEC)
            except subprocess.TimeoutExpired:
                continue


@dataclass(frozen=True, slots=True)
class FrameAnalysisResult:
    """영상 한 개의 화질 분석 결과"""

    active_region: ActiveRegion
    metric_used: str
    vmaf_model: str
    tier_scores: tuple[TierScore, ...]
    candidate_tier: str | None
    valid_segment_count: int
    total_segment_count: int
    unmeasurable: bool
    is_vr_protected: bool
    analysis_version: int = FRAME_ANALYSIS_VERSION


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError(_CANCEL_MESSAGE)


def _measure_roundtrip(
    *,
    ffmpeg_executable: str,
    source_path: str,
    work_dir: str,
    runner: StderrCommandRunner,
    active_region: ActiveRegion,
    tier: ResolutionTier,
    metric: str,
    model: str,
    segment: SegmentQuality,
) -> tuple[float | None, float | None]:
    """구간 한 개의 왕복 지표 점수와 SSIM dB 반환. 측정 실패 시 `(None, None)`"""
    log_name = LOG_NAME_VMAF if metric == METRIC_VMAF else LOG_NAME_SSIM
    log_path = Path(work_dir, log_name)
    if log_path.exists():
        log_path.unlink()
    args = build_roundtrip_command(
        ffmpeg_executable=ffmpeg_executable,
        source_path=source_path,
        start_sec=segment.start_sec,
        duration_sec=segment.duration_sec,
        active_region=active_region,
        tier=tier,
        metric=metric,
        model=model,
        log_name=log_name,
    )
    returncode, _stderr = runner(args, work_dir)
    if returncode != 0 or not log_path.exists():
        return (None, None)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if metric == METRIC_VMAF:
        return (parse_vmaf_log(text), None)
    stats = parse_ssim_stats(text)
    return (stats[0], stats[1]) if stats is not None else (None, None)


def _measure_tier_metric(
    *,
    segments: list[SegmentQuality],
    tier: ResolutionTier,
    metric: str,
    ffmpeg_executable: str,
    source_path: str,
    work_dir: str,
    runner: StderrCommandRunner,
    active_region: ActiveRegion,
    model: str,
    cancel_event: threading.Event | None,
) -> list[float]:
    """유효 구간마다 규격 왕복 지표를 재고 성공한 점수만 모음"""
    values: list[float] = []
    for segment in segments:
        _check_cancel(cancel_event)
        score, _db = _measure_roundtrip(
            ffmpeg_executable=ffmpeg_executable,
            source_path=source_path,
            work_dir=work_dir,
            runner=runner,
            active_region=active_region,
            tier=tier,
            metric=metric,
            model=model,
            segment=segment,
        )
        if score is not None:
            values.append(score)
    return values


def analyze_frames(
    *,
    source_path: str,
    width: int,
    height: int,
    duration_sec: float,
    work_dir: str,
    runner: StderrCommandRunner,
    ffmpeg_executable: str,
    has_vmaf: bool,
    has_vr_metadata: bool = False,
    manual_vr: bool = False,
    cancel_event: threading.Event | None = None,
    segment_count: int = DEFAULT_SEGMENT_COUNT,
    segment_duration_sec: float = DEFAULT_SEGMENT_DURATION_SEC,
) -> FrameAnalysisResult:
    """영상 한 개의 활성 화면과 축소 후보 규격을 측정

    - 검은 화면 구간과 단색 구간을 빼고 유효 구간 중앙값으로 판정
    - 후보 규격은 높은 규격부터 검사하며 SSIM이 기준 미만이면 VMAF를 건너뛰고 하위 규격 탐색을 멈춤
    - 유효 구간이 없으면 측정 불가로 표시하고 후보는 두지 않음
    """
    segments = sample_segments(duration_sec, segment_count, segment_duration_sec)
    vr_protected = is_vr_protected(width, height, has_vr_metadata=has_vr_metadata, manual_vr=manual_vr)
    black_valid: list[SegmentQuality] = []
    for start_sec, duration in segments:
        _check_cancel(cancel_event)
        segment = SegmentQuality(start_sec=start_sec, duration_sec=duration)
        returncode, stderr = runner(
            build_blackdetect_command(
                ffmpeg_executable=ffmpeg_executable,
                source_path=source_path,
                start_sec=start_sec,
                duration_sec=duration,
            ),
            work_dir,
        )
        black_ratio = parse_blackdetect_ratio(stderr, duration) if returncode == 0 else 1.0
        judged = replace(segment, black_ratio=black_ratio)
        if not is_invalid_segment(judged):
            black_valid.append(judged)

    boxes: list[CropBox] = []
    if not vr_protected:
        for segment in black_valid:
            _check_cancel(cancel_event)
            returncode, stderr = runner(
                build_cropdetect_command(
                    ffmpeg_executable=ffmpeg_executable,
                    source_path=source_path,
                    start_sec=segment.start_sec,
                    duration_sec=segment.duration_sec,
                ),
                work_dir,
            )
            if returncode == 0:
                boxes.extend(parse_cropdetect_boxes(stderr))
    active_region = merge_crop_union(boxes, width, height, is_vr=vr_protected)
    model = vmaf_model_for(active_region.width, active_region.height)
    metric_used = resolve_metric_name(has_vmaf)

    def _result(
        *,
        tier_scores: tuple[TierScore, ...],
        candidate_tier: str | None,
        valid_count: int,
        unmeasurable: bool,
    ) -> FrameAnalysisResult:
        return FrameAnalysisResult(
            active_region=active_region,
            metric_used=metric_used,
            vmaf_model=model,
            tier_scores=tier_scores,
            candidate_tier=candidate_tier,
            valid_segment_count=valid_count,
            total_segment_count=len(segments),
            unmeasurable=unmeasurable,
            is_vr_protected=vr_protected,
        )

    candidates = candidate_tiers_for(active_region.width, active_region.height)
    if not candidates:
        return _result(tier_scores=(), candidate_tier=None, valid_count=len(black_valid), unmeasurable=False)

    lowest_tier = candidates[-1]
    valid_segments: list[SegmentQuality] = []
    lowest_ssim_scores: list[float] = []
    for segment in black_valid:
        _check_cancel(cancel_event)
        score, ssim_db = _measure_roundtrip(
            ffmpeg_executable=ffmpeg_executable,
            source_path=source_path,
            work_dir=work_dir,
            runner=runner,
            active_region=active_region,
            tier=lowest_tier,
            metric=METRIC_SSIM,
            model=model,
            segment=segment,
        )
        if score is None or ssim_db is None:
            continue
        judged = replace(segment, roundtrip_ssim_db=ssim_db)
        if is_invalid_segment(judged):
            continue
        valid_segments.append(judged)
        lowest_ssim_scores.append(score)

    if not valid_segments:
        return _result(tier_scores=(), candidate_tier=None, valid_count=0, unmeasurable=True)

    tier_scores: list[TierScore] = []
    for tier in candidates:
        _check_cancel(cancel_event)
        if tier is lowest_tier:
            ssim_median: float | None = statistics.median(lowest_ssim_scores)
        else:
            ssim_values = _measure_tier_metric(
                segments=valid_segments,
                tier=tier,
                metric=METRIC_SSIM,
                ffmpeg_executable=ffmpeg_executable,
                source_path=source_path,
                work_dir=work_dir,
                runner=runner,
                active_region=active_region,
                model=model,
                cancel_event=cancel_event,
            )
            ssim_median = statistics.median(ssim_values) if ssim_values else None
        if ssim_median is None or ssim_median < ROUNDTRIP_SSIM_THRESHOLD:
            tier_scores.append(TierScore(tier=tier.key, ssim=ssim_median))
            break
        vmaf_median: float | None = None
        if has_vmaf:
            vmaf_values = _measure_tier_metric(
                segments=valid_segments,
                tier=tier,
                metric=METRIC_VMAF,
                ffmpeg_executable=ffmpeg_executable,
                source_path=source_path,
                work_dir=work_dir,
                runner=runner,
                active_region=active_region,
                model=model,
                cancel_event=cancel_event,
            )
            vmaf_median = statistics.median(vmaf_values) if vmaf_values else None
            if vmaf_median is None or vmaf_median < ROUNDTRIP_VMAF_THRESHOLD:
                tier_scores.append(TierScore(tier=tier.key, ssim=ssim_median, vmaf=vmaf_median))
                break
        tier_scores.append(TierScore(tier=tier.key, ssim=ssim_median, vmaf=vmaf_median))

    return _result(
        tier_scores=tuple(tier_scores),
        candidate_tier=select_candidate_tier(tier_scores, use_vmaf=has_vmaf),
        valid_count=len(valid_segments),
        unmeasurable=False,
    )
