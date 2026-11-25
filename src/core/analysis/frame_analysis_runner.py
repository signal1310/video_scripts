"""화질 분석 실행 흐름과 결과 데이터 모델 모듈

- 1단계 9지점 키프레임 스캔, 2단계 적응형 라운드 1패스 다중 규격 SSIM, 3단계 최저 통과 후보 VMAF 순서로 조율
- 라운드 안의 구간 측정은 유휴 워커가 있으면 병렬로 실행하고, 취소는 `threading.Event`로 실행 중인 FFmpeg를 종료
- 지표 명령 구성과 파싱은 `frame_analysis.py`, `frame_fast_metrics.py`, `quality_probe.py`를 재사용
"""

from __future__ import annotations

import contextlib
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from src.core.analysis.frame_analysis import (
    CROP_SCAN_VERSION,
    DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    FRAME_ANALYSIS_VERSION,
    METRIC_CROP_ONLY,
    ROUND_POINT_INDICES,
    VMAF_SUBSAMPLE,
    ActiveRegion,
    FrameAnalysisThresholds,
    TierScore,
    analysis_version_for_mode,
    build_roundtrip_command,
    candidate_tiers_for,
    choose_round_points,
    frame_analysis_params,
    is_vr_protected,
    merge_crop_union,
    next_round_tier_keys,
    select_candidate_tier,
    ssim_stage_confirmed,
)
from src.core.analysis.frame_fast_metrics import (
    build_split_ssim_command,
    clear_metric_logs,
    parse_ssim_logs,
    ssim_log_name,
    vmaf_log_name,
)
from src.core.analysis.frame_scan import scan_keyframe_points
from src.core.command_history import (
    STAGE_FRAME_ANALYSIS,
    command_scope,
)
from src.core.command_history import (
    STAGE_VMAF as COMMAND_STAGE_VMAF,
)
from src.core.config import DEFAULT_FRAME_ANALYSIS_MODE
from src.core.quality_probe import (
    METRIC_VMAF,
    parse_vmaf_log,
    resolve_metric_name,
    vmaf_model_for,
)
from src.core.resolution_tiers import DEFAULT_SCALE_ALGORITHM, ResolutionTier
from src.core.sample_points import DEFAULT_SEGMENT_COUNT, FLAT_ROUNDTRIP_SSIM_DB, sample_segments

POLL_INTERVAL_SEC: float = 0.1
PROCESS_TERMINATE_TIMEOUT_SEC: float = 5.0
DEFAULT_ANALYSIS_TIMEOUT_SEC: float = 1800.0

# - 하단 세부 단계 표기용 코드(TERMINOLOGY 6.17절)
STAGE_SCAN: str = "scan"
STAGE_SSIM: str = "ssim"
STAGE_VMAF: str = "vmaf"
StageCallback = Callable[[str, int, int], None]

_CANCEL_MESSAGE: str = "화질 분석이 취소되었습니다"
_T = TypeVar("_T")


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

    - 파이프 대신 임시 파일을 써서 줄이 많은 표준 오류에서도 교착이 생기지 않게 함
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


def _median(values: Sequence[float]) -> float | None:
    """값 목록의 중앙값. 목록이 비면 None"""
    return statistics.median(values) if values else None


def _run_parallel(tasks: Sequence[Callable[[], _T]], workers: int) -> list[_T]:
    """라운드 안의 구간 측정을 워커 수만큼 병렬 실행하고 제출 순서대로 결과를 모음"""
    if workers <= 1 or len(tasks) <= 1:
        return [task() for task in tasks]
    with ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(task) for task in tasks]
        try:
            results = [future.result() for future in futures]
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    return results


def _measure_ssim_point(
    *,
    source_path: str,
    work_dir: str,
    runner: StderrCommandRunner,
    ffmpeg_executable: str,
    active_region: ActiveRegion,
    tiers: Sequence[ResolutionTier],
    segment: tuple[float, float],
    point_index: int,
    log_prefix: str,
    fast_keyframe_seek: bool,
    cancel_event: threading.Event | None,
    algorithm: str = DEFAULT_SCALE_ALGORITHM,
) -> dict[str, tuple[float, float]] | None:
    """구간 한 지점의 대상 규격 전체 SSIM 점수와 dB. 명령이 실패하면 None"""
    _check_cancel(cancel_event)
    log_names = {tier.key: log_prefix + ssim_log_name(point_index, tier.key) for tier in tiers}
    clear_metric_logs(work_dir, list(log_names.values()))
    start_sec, duration_sec = segment
    args = build_split_ssim_command(
        ffmpeg_executable=ffmpeg_executable,
        source_path=source_path,
        start_sec=start_sec,
        duration_sec=duration_sec,
        active_region=active_region,
        tiers=tiers,
        log_names=log_names,
        fast_keyframe_seek=fast_keyframe_seek,
        algorithm=algorithm,
    )
    with command_scope(STAGE_FRAME_ANALYSIS, args, target=source_path) as handle:
        returncode, _stderr = runner(args, work_dir)
        handle.exit_code = returncode
    if returncode != 0:
        return None
    scores = parse_ssim_logs(work_dir, log_names)
    return scores or None


def _measure_vmaf_point(
    *,
    source_path: str,
    work_dir: str,
    runner: StderrCommandRunner,
    ffmpeg_executable: str,
    active_region: ActiveRegion,
    tier: ResolutionTier,
    model: str,
    segment: tuple[float, float],
    point_index: int,
    log_prefix: str,
    fast_keyframe_seek: bool,
    cancel_event: threading.Event | None,
    algorithm: str = DEFAULT_SCALE_ALGORITHM,
) -> float | None:
    """구간 한 지점의 규격 VMAF 점수. 명령이 실패하거나 로그가 없으면 None"""
    _check_cancel(cancel_event)
    log_name = log_prefix + vmaf_log_name(point_index)
    clear_metric_logs(work_dir, [log_name])
    start_sec, duration_sec = segment
    args = build_roundtrip_command(
        ffmpeg_executable=ffmpeg_executable,
        source_path=source_path,
        start_sec=start_sec,
        duration_sec=duration_sec,
        active_region=active_region,
        tier=tier,
        metric=METRIC_VMAF,
        model=model,
        log_name=log_name,
        fast_keyframe_seek=fast_keyframe_seek,
        n_subsample=VMAF_SUBSAMPLE,
        algorithm=algorithm,
    )
    with command_scope(COMMAND_STAGE_VMAF, args, target=source_path) as handle:
        returncode, _stderr = runner(args, work_dir)
        handle.exit_code = returncode
    if returncode != 0:
        return None
    log_path = Path(work_dir, log_name)
    if not log_path.is_file():
        return None
    return parse_vmaf_log(log_path.read_text(encoding="utf-8", errors="replace"))


def _run_ssim_stage(
    *,
    points: Sequence[tuple[float, float]],
    valid_points: set[int],
    candidates: Sequence[ResolutionTier],
    source_path: str,
    work_dir: str,
    runner: StderrCommandRunner,
    ffmpeg_executable: str,
    active_region: ActiveRegion,
    log_prefix: str,
    fast_keyframe_seek: bool,
    cancel_event: threading.Event | None,
    inner_workers: int,
    on_stage: StageCallback | None,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    algorithm: str = DEFAULT_SCALE_ALGORITHM,
) -> tuple[dict[str, float | None], set[int], set[int]]:
    """적응형 라운드로 규격별 SSIM 누적 중앙값과 측정 유효 지점, 단색 지점을 반환"""
    tier_keys = [tier.key for tier in candidates]
    cumulative: dict[str, list[float]] = {key: [] for key in tier_keys}
    used_points: set[int] = set()
    flat_points: set[int] = set()
    measured_points: set[int] = set()
    active_tiers: list[ResolutionTier] = list(candidates)
    total_points = len(valid_points)
    for group in ROUND_POINT_INDICES:
        targets = choose_round_points(group, valid_points - flat_points, used_points)
        if not targets:
            break
        tiers = tuple(active_tiers)
        tasks = [
            lambda index=index, tiers=tiers: _measure_ssim_point(
                source_path=source_path,
                work_dir=work_dir,
                runner=runner,
                ffmpeg_executable=ffmpeg_executable,
                active_region=active_region,
                tiers=tiers,
                segment=points[index],
                point_index=index,
                log_prefix=log_prefix,
                fast_keyframe_seek=fast_keyframe_seek,
                cancel_event=cancel_event,
                algorithm=algorithm,
            )
            for index in targets
        ]
        results = _run_parallel(tasks, inner_workers)
        for point_index, scores in zip(targets, results, strict=True):
            if scores is None:
                continue
            measured_points.add(point_index)
            if all(db >= FLAT_ROUNDTRIP_SSIM_DB for _score, db in scores.values()):
                flat_points.add(point_index)
            else:
                used_points.add(point_index)
                for key, (score, _db) in scores.items():
                    cumulative[key].append(score)
            if on_stage is not None:
                on_stage(STAGE_SSIM, len(measured_points), total_points)
        medians = {key: _median(values) for key, values in cumulative.items()}
        if ssim_stage_confirmed([medians[key] for key in tier_keys], thresholds=thresholds):
            break
        keep = set(next_round_tier_keys(active_tiers, medians, thresholds=thresholds))
        active_tiers = [tier for tier in active_tiers if tier.key in keep]
        if not active_tiers or not (valid_points - flat_points - used_points):
            break
    return ({key: _median(values) for key, values in cumulative.items()}, used_points, flat_points)


def _run_vmaf_stage(
    *,
    points: Sequence[tuple[float, float]],
    valid_points: set[int],
    flat_points: set[int],
    tier: ResolutionTier,
    source_path: str,
    work_dir: str,
    runner: StderrCommandRunner,
    ffmpeg_executable: str,
    active_region: ActiveRegion,
    model: str,
    log_prefix: str,
    fast_keyframe_seek: bool,
    cancel_event: threading.Event | None,
    inner_workers: int,
    on_stage: StageCallback | None,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    algorithm: str = DEFAULT_SCALE_ALGORITHM,
) -> float | None:
    """규격 한 개의 VMAF를 적응형 라운드로 재고 누적 중앙값 반환"""
    usable_points = valid_points - flat_points
    used_points: set[int] = set()
    measured_points: set[int] = set()
    values: list[float] = []
    total_points = len(usable_points)
    for group in ROUND_POINT_INDICES:
        targets = choose_round_points(group, usable_points, used_points)
        if not targets:
            break
        tasks = [
            lambda index=index: _measure_vmaf_point(
                source_path=source_path,
                work_dir=work_dir,
                runner=runner,
                ffmpeg_executable=ffmpeg_executable,
                active_region=active_region,
                tier=tier,
                model=model,
                segment=points[index],
                point_index=index,
                log_prefix=log_prefix,
                fast_keyframe_seek=fast_keyframe_seek,
                cancel_event=cancel_event,
                algorithm=algorithm,
            )
            for index in targets
        ]
        results = _run_parallel(tasks, inner_workers)
        for point_index, score in zip(targets, results, strict=True):
            if score is None:
                continue
            measured_points.add(point_index)
            used_points.add(point_index)
            values.append(score)
            if on_stage is not None:
                on_stage(STAGE_VMAF, len(measured_points), total_points)
        median = _median(values)
        if median is not None and (
            median >= thresholds.vmaf_pass_score or median < thresholds.vmaf_fail_score
        ):
            return median
        if not (usable_points - used_points):
            break
    return _median(values)


def analyze_frames(
    *,
    source_path: str,
    width: int,
    height: int,
    duration_sec: float,
    work_dir: str,
    runner: StderrCommandRunner | None = None,
    ffmpeg_executable: str,
    has_vmaf: bool,
    has_vr_metadata: bool = False,
    manual_vr: bool = False,
    mode: str = DEFAULT_FRAME_ANALYSIS_MODE,
    cancel_event: threading.Event | None = None,
    inner_workers: int = 1,
    on_stage: StageCallback | None = None,
    log_prefix: str = "",
    prefer_pyav: bool | None = None,
    timeout_sec: float = DEFAULT_ANALYSIS_TIMEOUT_SEC,
    crop_only: bool = False,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    scale_algorithm: str = DEFAULT_SCALE_ALGORITHM,
) -> FrameAnalysisResult:
    """영상 한 개의 활성 화면과 축소 후보 규격을 고속 측정

    - 실행기를 주입하지 않으면 PyAV 9지점 스캔을 쓰고, 주입하면 CLI 9지점 스캔으로 폴백
    - crop_only면 1단계 스캔과 검은 여백 합집합만 남기고 SSIM/VMAF 단계를 실행하지 않음
    - SSIM은 적응형 라운드마다 규격 전체를 한 번의 디코딩으로 재고, 확정되면 다음 라운드를 생략
    - VMAF는 SSIM 통과 규격 중 가장 낮은 규격부터 재고 탈락하면 바로 위 규격으로 역상승
    - 유효 구간이 없으면 측정 불가로 표시하고 후보는 두지 않음
    - scale_algorithm은 인코딩 축소와 같은 보간 방식을 넘겨 받아 예측과 실제 결과를 맞춤
    """
    params = frame_analysis_params(mode)
    analysis_version = analysis_version_for_mode(mode, scale_algorithm)
    _check_cancel(cancel_event)
    segments = sample_segments(duration_sec, DEFAULT_SEGMENT_COUNT, params.segment_duration_sec)
    vr_protected = is_vr_protected(width, height, has_vr_metadata=has_vr_metadata, manual_vr=manual_vr)
    injected = runner is not None
    use_pyav = (not injected) if prefer_pyav is None else prefer_pyav
    exec_runner: StderrCommandRunner = (
        runner
        if runner is not None
        else ProcessStderrRunner(cancel_event=cancel_event, timeout_sec=timeout_sec)
    )

    def _on_scan_point(done: int, total: int) -> None:
        if on_stage is not None:
            on_stage(STAGE_SCAN, done, total)

    scan = scan_keyframe_points(
        source_path=source_path,
        points=[start for start, _duration in segments],
        ffmpeg_executable=ffmpeg_executable,
        runner=exec_runner,
        work_dir=work_dir,
        fast_keyframe_seek=params.fast_keyframe_seek,
        cancel_event=cancel_event,
        on_point=_on_scan_point,
        prefer_pyav=use_pyav,
    )
    active_region = merge_crop_union(list(scan.boxes), width, height, is_vr=vr_protected, thresholds=thresholds)
    valid_points = {index for index in range(len(segments)) if index not in scan.black_points}
    if crop_only:
        return FrameAnalysisResult(
            active_region=active_region,
            metric_used=METRIC_CROP_ONLY,
            vmaf_model="",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=len(valid_points),
            total_segment_count=len(segments),
            unmeasurable=False,
            is_vr_protected=vr_protected,
            analysis_version=CROP_SCAN_VERSION,
        )
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
            analysis_version=analysis_version,
        )

    candidates = candidate_tiers_for(active_region.width, active_region.height)
    if not candidates:
        return _result(tier_scores=(), candidate_tier=None, valid_count=len(valid_points), unmeasurable=False)
    if not valid_points:
        return _result(tier_scores=(), candidate_tier=None, valid_count=0, unmeasurable=True)

    ssim_medians, used_points, flat_points = _run_ssim_stage(
        points=segments,
        valid_points=valid_points,
        candidates=candidates,
        source_path=source_path,
        work_dir=work_dir,
        runner=exec_runner,
        ffmpeg_executable=ffmpeg_executable,
        active_region=active_region,
        log_prefix=log_prefix,
        fast_keyframe_seek=params.fast_keyframe_seek,
        cancel_event=cancel_event,
        inner_workers=inner_workers,
        on_stage=on_stage,
        thresholds=thresholds,
        algorithm=scale_algorithm,
    )
    if not used_points:
        return _result(tier_scores=(), candidate_tier=None, valid_count=0, unmeasurable=True)

    ssim_scores = [TierScore(tier=tier.key, ssim=ssim_medians.get(tier.key)) for tier in candidates]
    ssim_candidate = select_candidate_tier(ssim_scores, use_vmaf=False, thresholds=thresholds)
    vmaf_medians: dict[str, float | None] = {}
    candidate_tier: str | None = None
    if ssim_candidate is None:
        candidate_tier = None
    elif not has_vmaf:
        candidate_tier = ssim_candidate
    else:
        candidate_keys = [tier.key for tier in candidates]
        prefix = candidates[: candidate_keys.index(ssim_candidate) + 1]
        for tier in reversed(prefix):
            _check_cancel(cancel_event)
            median = _run_vmaf_stage(
                points=segments,
                valid_points=valid_points,
                flat_points=flat_points,
                tier=tier,
                source_path=source_path,
                work_dir=work_dir,
                runner=exec_runner,
                ffmpeg_executable=ffmpeg_executable,
                active_region=active_region,
                model=model,
                log_prefix=log_prefix,
                fast_keyframe_seek=params.fast_keyframe_seek,
                cancel_event=cancel_event,
                inner_workers=inner_workers,
                on_stage=on_stage,
                thresholds=thresholds,
                algorithm=scale_algorithm,
            )
            vmaf_medians[tier.key] = median
            if median is not None and median >= thresholds.vmaf:
                candidate_tier = tier.key
                break

    tier_scores = tuple(
        TierScore(
            tier=tier.key,
            ssim=ssim_medians.get(tier.key),
            vmaf=vmaf_medians.get(tier.key),
        )
        for tier in candidates
        if ssim_medians.get(tier.key) is not None or vmaf_medians.get(tier.key) is not None
    )
    return _result(
        tier_scores=tier_scores,
        candidate_tier=candidate_tier,
        valid_count=len(used_points),
        unmeasurable=False,
    )
