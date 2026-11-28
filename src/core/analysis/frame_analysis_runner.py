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
    moving_score_stats,
    parse_ssim_logs,
    ssim_log_name,
    vmaf_log_name,
)
from src.core.analysis.frame_scan import FrameStatsRunner, luma_frame_stats, scan_keyframe_points
from src.core.analysis.hdr_tone_map import (
    TONEMAP_UNAVAILABLE_REASON,
    ToneMapUnavailableError,
    is_tonemap_filter_error,
)
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
    SsimStats,
    parse_vmaf_log,
    resolve_metric_name,
    vmaf_model_for,
)
from src.core.resolution_tiers import DEFAULT_SCALE_ALGORITHM, ResolutionTier
from src.core.sample_points import (
    DEFAULT_SEGMENT_COUNT,
    FLAT_ROUNDTRIP_SSIM_DB,
    sample_segments,
)

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
    # - 검은 여백 감지를 하나도 읽지 못했는지. 측정 불가와 달리 활성 화면 확정 자체가 실패한 상태임
    crop_scan_failed: bool = False
    analysis_version: int = FRAME_ANALYSIS_VERSION
    # - 소스가 HDR이라 톤 매핑을 거쳐 쟀는지. 캐시 재사용과 표시가 이 표식으로 HDR 결과를 구분
    hdr_tonemapped: bool = False
    # - 측정하지 못한 이유(톤 매핑 필터 없음 등). 측정 불가 표시와 함께 안내에 씀
    unmeasurable_reason: str = ""


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError(_CANCEL_MESSAGE)


def _unavailable_result(
    width: int,
    height: int,
    segment_count: int,
    *,
    vr_protected: bool,
    crop_only: bool,
    analysis_version: int,
    reason: str,
    is_hdr: bool,
) -> FrameAnalysisResult:
    """톤 매핑 필터를 쓸 수 없을 때의 측정 불가 결과

    - PQ 값을 SDR 지표로 재면 판정이 틀리므로 일부 지점만 잰 결과도 쓰지 않음
    - 활성 화면도 확정하지 못했으므로 감지 실패로 세워 다음 실행에서 다시 시도하게 함
    """
    return FrameAnalysisResult(
        active_region=ActiveRegion(width, height, 0, 0, False),
        metric_used=METRIC_CROP_ONLY if crop_only else "",
        vmaf_model="",
        tier_scores=(),
        candidate_tier=None,
        valid_segment_count=0,
        total_segment_count=segment_count,
        unmeasurable=True,
        is_vr_protected=vr_protected,
        crop_scan_failed=True,
        analysis_version=analysis_version,
        hdr_tonemapped=is_hdr,
        unmeasurable_reason=reason,
    )


def _median(values: Sequence[float]) -> float | None:
    """값 목록의 중앙값. 목록이 비면 None"""
    return statistics.median(values) if values else None


def _is_flat_roundtrip(stats: SsimStats) -> bool:
    """같은 화면이 아닌 프레임만 모은 점수로 축소 손실이 드러나지 않는지 판정

    - 같은 화면 프레임을 모두 뺀 모집단이라 정지 프레임이 평균을 끌어올리지 않음
    - dB가 기준 이상이면 평탄이며, 평탄 지점은 VMAF 단계에서도 뺌
    """
    return stats.db >= FLAT_ROUNDTRIP_SSIM_DB


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
    tonemap: bool = False,
) -> dict[str, SsimStats] | None:
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
        tonemap=tonemap,
    )
    with command_scope(
        STAGE_FRAME_ANALYSIS, args, target=source_path, source_path=source_path
    ) as handle:
        returncode, stderr = runner(args, work_dir)
        handle.exit_code = returncode
    if returncode != 0:
        if tonemap and is_tonemap_filter_error(stderr):
            raise ToneMapUnavailableError(TONEMAP_UNAVAILABLE_REASON)
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
    tonemap: bool = False,
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
        tonemap=tonemap,
    )
    with command_scope(
        COMMAND_STAGE_VMAF, args, target=source_path, source_path=source_path
    ) as handle:
        returncode, stderr = runner(args, work_dir)
        handle.exit_code = returncode
    if returncode != 0:
        if tonemap and is_tonemap_filter_error(stderr):
            raise ToneMapUnavailableError(TONEMAP_UNAVAILABLE_REASON)
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
    tonemap: bool = False,
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
                tonemap=tonemap,
            )
            for index in targets
        ]
        results = _run_parallel(tasks, inner_workers)
        for point_index, scores in zip(targets, results, strict=True):
            if scores is None:
                continue
            measured_points.add(point_index)
            # - 같은 화면 프레임은 점수에서 빠지고, 모든 규격에서 모두 같은 화면인 지점만 정지로 봄
            population = moving_score_stats(scores)
            if not population or all(_is_flat_roundtrip(stats) for stats in population.values()):
                flat_points.add(point_index)
            else:
                used_points.add(point_index)
                for key, stats in population.items():
                    cumulative[key].append(stats.score)
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
    tonemap: bool = False,
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
                tonemap=tonemap,
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
    scan_stats_runner: FrameStatsRunner = luma_frame_stats,
    is_hdr: bool = False,
) -> FrameAnalysisResult:
    """영상 한 개의 활성 화면과 축소 후보 규격을 고속 측정

    - 실행기를 주입하지 않으면 PyAV 9지점 스캔을 쓰고, 주입하면 CLI 9지점 스캔으로 폴백
    - is_hdr이면 스캔과 지표 명령 모두 SDR 톤 매핑을 거쳐 PQ 휘도와 지표를 SDR과 같은 기준으로 잼
    - 톤 매핑 필터가 없는 빌드면 PQ 값을 그대로 재지 않고 측정 불가와 사유를 남김
    - crop_only면 1단계 스캔과 검은 여백 합집합만 남기고 SSIM/VMAF 단계를 실행하지 않음
    - 읽은 지점이 하나도 없으면 두 경로 모두 `crop_scan_failed`를 세워 감지 실패를 표시와 캐시 재사용에 씀
    - 감지 전용 실행의 측정 불가는 스캔 실패로만 세움(모든 지점이 검은 화면이면 감지는 성공)
    - SSIM은 적응형 라운드마다 규격 전체를 한 번의 디코딩으로 재고, 확정되면 다음 라운드를 생략
    - VMAF는 SSIM 통과 규격 중 가장 낮은 규격부터 재고 탈락하면 바로 위 규격으로 역상승
    - 유효 구간이 없으면 측정 불가로 표시하고 후보는 두지 않음
    - scale_algorithm은 인코딩 축소와 같은 보간 방식을 넘겨 받아 예측과 실제 결과를 맞춤
    - scan_stats_runner는 1단계 픽셀 판정만 대신하게 해, 순수 파이썬 픽셀 루프를 GUI 프로세스 밖에서 돌림
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

    try:
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
            tonemap=is_hdr,
            stats_runner=scan_stats_runner,
        )
    except ToneMapUnavailableError as error:
        return _unavailable_result(
            width,
            height,
            len(segments),
            vr_protected=vr_protected,
            crop_only=crop_only,
            analysis_version=CROP_SCAN_VERSION if crop_only else analysis_version,
            reason=str(error),
            is_hdr=is_hdr,
        )
    active_region = merge_crop_union(
        list(scan.boxes),
        width,
        height,
        is_vr=vr_protected,
        thresholds=thresholds,
        darkness=list(scan.darkness),
    )
    failed_points = scan.failed_points
    # - 프레임을 받은 지점은 검은 화면이어도 읽은 것이라 감지 성공 여부는 이 집합으로 정함
    read_points = {index for index in range(len(segments)) if index not in failed_points}
    valid_points = read_points - scan.black_points
    # - 읽은 지점이 하나도 없을 때만 감지 실패. 모든 지점이 검은 화면이거나 구간이 없으면 감지는 성공한 것임
    # - VR 보호 영상은 감지 결과를 쓰지 않으므로 스캔이 비어도 실패로 세지 않음
    scan_failed = not vr_protected and bool(failed_points) and not read_points
    if crop_only:
        return FrameAnalysisResult(
            active_region=active_region,
            metric_used=METRIC_CROP_ONLY,
            vmaf_model="",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=len(valid_points),
            total_segment_count=len(segments),
            unmeasurable=scan_failed,
            is_vr_protected=vr_protected,
            crop_scan_failed=scan_failed,
            analysis_version=CROP_SCAN_VERSION,
            hdr_tonemapped=is_hdr,
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
            crop_scan_failed=scan_failed,
            analysis_version=analysis_version,
            hdr_tonemapped=is_hdr,
        )

    candidates = candidate_tiers_for(active_region.width, active_region.height)
    if not candidates:
        return _result(tier_scores=(), candidate_tier=None, valid_count=len(valid_points), unmeasurable=False)
    if not valid_points:
        return _result(tier_scores=(), candidate_tier=None, valid_count=0, unmeasurable=True)

    def _tonemap_failure(error: ToneMapUnavailableError) -> FrameAnalysisResult:
        return _unavailable_result(
            width,
            height,
            len(segments),
            vr_protected=vr_protected,
            crop_only=False,
            analysis_version=analysis_version,
            reason=str(error),
            is_hdr=is_hdr,
        )

    try:
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
            tonemap=is_hdr,
        )
    except ToneMapUnavailableError as error:
        return _tonemap_failure(error)
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
            try:
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
                    tonemap=is_hdr,
                )
            except ToneMapUnavailableError as error:
                return _tonemap_failure(error)
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
