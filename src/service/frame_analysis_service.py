"""화질 분석 배치 실행 서비스 모듈

- 캐시 저장은 `frame_analysis_store.py`의 저장소가 맡고, 이 모듈은 측정 실행과 캐시 재사용 판정만 함
- 영상 1개를 마칠 때마다 즉시 저장해 중단되어도 완료분을 잃지 않음
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import (
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)
from dataclasses import dataclass
from datetime import datetime, timezone
from multiprocessing import get_context

from src.core.analysis.frame_analysis import (
    DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    CropBox,
    EdgeDarkness,
    FrameAnalysisThresholds,
    analysis_version_for_mode,
    apply_no_bars_ratio,
    can_reuse_tier_scores,
    candidate_tiers_for,
    is_failed_crop_scan,
    is_usable_analysis_version,
    is_vr_protected,
    needs_crop_review,
    rejudge_candidate_tier,
    resolve_crop_frame,
    review_holds_automatic_crop,
)
from src.core.analysis.frame_analysis_runner import (
    FrameAnalysisResult,
    ProcessStderrRunner,
    StderrCommandRunner,
    analyze_frames,
)
from src.core.analysis.frame_scan import FrameStatsRunner, luma_frame_stats, luma_stats_task
from src.core.config import DEFAULT_FRAME_ANALYSIS_MODE
from src.core.encode.ffmpeg_command import FFMPEG_EXECUTABLE_DEFAULT
from src.core.models import ActiveFrame
from src.core.quality_probe import METRIC_VMAF, resolve_metric_name
from src.core.resolution_tiers import DEFAULT_SCALE_ALGORITHM, normalize_scale_algorithm
from src.service.frame_analysis_record import (
    FrameAnalysisRecord,
    FrameAnalysisRequest,
    record_from_result,
    result_from_record,
)
from src.service.frame_analysis_store import FrameAnalysisStore

ANALYSIS_TIMEOUT_SEC: float = 1800.0
# - 영상 단위와 영상 내부 라운드 병렬 워커 수(이름 있는 상수를 위치 인자로 넘김)
VIDEO_ANALYSIS_WORKERS: int = 4
INNER_ROUND_WORKERS_SINGLE: int = 4
INNER_ROUND_WORKERS_FEW: int = 2
FEW_VIDEO_LIMIT: int = 3
# - 풀 결과를 기다리는 단위 시간. 대기 중에는 GIL을 놓으므로 GUI 스레드가 멈추지 않음
POOL_POLL_INTERVAL_SEC: float = 0.05
PROCESS_START_METHOD: str = "spawn"


@dataclass(frozen=True, slots=True)
class CropReviewStatus:
    """영상 한 개의 어두운 근거 무시 자동 크롭 표시 상태

    - flagged: 어두운 근거로 지점을 무시해 안쪽 경계를 채택한 자동 크롭인지
    - hold_applies: 원본 교체 인코딩에서 그 자동 크롭을 보류하는지
    """

    flagged: bool
    hold_applies: bool


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


ProgressCallback = Callable[[int, int, str, FrameAnalysisResult], None]
StageProgressCallback = Callable[[str, str, int, int], None]


def _unique_requests(requests: Sequence[FrameAnalysisRequest]) -> list[FrameAnalysisRequest]:
    """같은 파일 식별 키의 중복 요청을 하나로 줄임"""
    return list({request.key: request for request in requests}.values())


def _expected_vr(request: FrameAnalysisRequest) -> bool:
    """이 요청 영상이 지금 규칙에서 VR 보호 대상인지"""
    return is_vr_protected(
        request.width,
        request.height,
        has_vr_metadata=request.has_vr_metadata,
        manual_vr=request.manual_vr,
    )


def _is_reusable(
    record: FrameAnalysisRecord | None,
    request: FrameAnalysisRequest,
    metric_name: str,
    analysis_version: int,
    *,
    crop_only: bool = False,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
) -> bool:
    """캐시 행이 지금 측정 규칙과 같아 다시 재지 않고 쓸 수 있는지

    - 검은 여백 감지는 전체 화질 분석 행이나 감지 전용 행을 모두 재사용해 기존 점수를 보존
    - 감지에 실패한 행은 다시 스캔함. 새 결과는 `crop_scan_failed` 플래그로, 이전 전체 화질 분석 행은
      측정 불가로 알아보며, 모든 지점이 검은 화면이면 감지는 성공한 것이므로 재사용함
    - 전체 화질 분석은 같은 방식 버전과 같은 지표 행만 재사용하며 감지 전용 행과 감지 실패 행은 다시 잼
    - 기준이 바뀌어도 저장된 점수로 최종 후보가 확정되면 다시 재지 않고 재판정 결과를 씀
    """
    if record is None or record.is_vr_protected != _expected_vr(request):
        return False
    if crop_only:
        if is_failed_crop_scan(record):
            return False
        return is_usable_analysis_version(record.analysis_version, analysis_version)
    if record.crop_scan_failed:
        return False
    if record.analysis_version != analysis_version or record.metric_used != metric_name:
        return False
    if not record.tier_scores:
        return True
    region = apply_no_bars_ratio(
        record.active_region, request.width, request.height, thresholds=thresholds
    )
    return can_reuse_tier_scores(
        record.tier_scores,
        use_vmaf=metric_name == METRIC_VMAF,
        thresholds=thresholds,
        all_tier_keys=tuple(tier.key for tier in candidate_tiers_for(region.width, region.height)),
    )


def _rejudged_result(
    record: FrameAnalysisRecord,
    request: FrameAnalysisRequest,
    thresholds: FrameAnalysisThresholds,
    *,
    keep_candidate: bool = False,
) -> FrameAnalysisResult:
    """캐시 결과를 현재 축소 후보 기준과 검은 여백 면적 상한으로 다시 판정

    - 저장된 규격 점수는 그대로 두고 후보만 다시 고름
    - 활성 화면 면적이 현재 상한을 넘으면 전체 프레임으로 되돌림
    - keep_candidate면 감지 전용 실행처럼 후보를 그대로 보존
    """
    base = result_from_record(record)
    region = apply_no_bars_ratio(
        base.active_region, request.width, request.height, thresholds=thresholds
    )
    return FrameAnalysisResult(
        active_region=region,
        metric_used=base.metric_used,
        vmaf_model=base.vmaf_model,
        tier_scores=base.tier_scores,
        candidate_tier=(
            base.candidate_tier
            if keep_candidate
            else rejudge_candidate_tier(
                base.tier_scores,
                base.candidate_tier,
                use_vmaf=base.metric_used == METRIC_VMAF,
                thresholds=thresholds,
            )
        ),
        valid_segment_count=base.valid_segment_count,
        total_segment_count=base.total_segment_count,
        unmeasurable=base.unmeasurable,
        is_vr_protected=base.is_vr_protected,
        crop_scan_failed=base.crop_scan_failed,
        analysis_version=base.analysis_version,
    )


class _ScanStatsPool:
    """Y 평면 판정을 자식 프로세스에서 실행하는 풀

    - 순수 파이썬 픽셀 루프가 GUI 프로세스의 GIL을 붙잡지 않게 별도 프로세스로 옮김
    - 호출 스레드는 결과 큐를 기다리며 GIL을 놓으므로, 다른 스레드와 GUI가 계속 돎
    - 사용할 때 처음 만들고 배치가 끝나거나 취소되면 닫아 워커 프로세스를 남기지 않음
    """

    def __init__(self, workers: int, *, cancel_event: threading.Event | None = None) -> None:
        self._workers: int = max(1, workers)
        self._cancel_event: threading.Event | None = cancel_event
        self._executor: ProcessPoolExecutor | None = None
        self._lock: threading.Lock = threading.Lock()

    def __call__(
        self, data: bytes, line_size: int, width: int, height: int
    ) -> tuple[CropBox | None, float, EdgeDarkness]:
        """Y 평면 한 장의 판정을 자식 프로세스에 맡기고 결과를 기다림"""
        _check_cancel(self._cancel_event)
        executor = self._live_executor()
        future: Future[tuple[CropBox | None, float, EdgeDarkness]] = executor.submit(
            luma_stats_task, (data, line_size, width, height)
        )
        return self._wait(future)

    def close(self) -> None:
        """대기 중인 작업을 버리고 워커 프로세스를 닫음. 두 번 불러도 안전"""
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    def _live_executor(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = ProcessPoolExecutor(
                    max_workers=self._workers,
                    mp_context=get_context(PROCESS_START_METHOD),
                )
            return self._executor

    def _wait(self, future: Future[tuple[CropBox | None, float, EdgeDarkness]]) -> tuple[
        CropBox | None, float, EdgeDarkness
    ]:
        while True:
            try:
                return future.result(timeout=POOL_POLL_INTERVAL_SEC)
            except FutureTimeoutError:
                _check_cancel(self._cancel_event)


def _check_cancel(cancel_event: threading.Event | None) -> None:
    """취소 신호가 서 있으면 `InterruptedError`"""
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("화질 분석이 취소되었습니다")


def _inner_round_workers(video_count: int, *, runner_injected: bool) -> int:
    """영상 수와 실행기 주입 여부로 영상 내부 라운드 병렬 워커 수 결정

    - 주입 실행기는 테스트나 다른 실행 정책을 따르므로 직렬로 둠
    - 영상이 1개면 유휴 워커를 라운드에 쓰고, 소수면 절반만 씀
    """
    if runner_injected:
        return 1
    if video_count <= 1:
        return INNER_ROUND_WORKERS_SINGLE
    if video_count <= FEW_VIDEO_LIMIT:
        return INNER_ROUND_WORKERS_FEW
    return 1


class FrameAnalysisService:
    """화질 분석 배치 실행과 캐시 재사용을 조율하는 서비스

    - 캐시가 현재 측정 규칙 버전과 같으면 다시 재지 않고 재사용
    - 영상 1개를 마칠 때마다 캐시에 저장해 취소되어도 완료분을 보존
    - 실행기 주입으로 FFmpeg 없이 단위 테스트할 수 있음
    """

    def __init__(
        self,
        store: FrameAnalysisStore,
        *,
        work_dir: str,
        ffmpeg_executable: str = FFMPEG_EXECUTABLE_DEFAULT,
        has_vmaf: bool = False,
        runner: StderrCommandRunner | None = None,
        timeout_sec: float = ANALYSIS_TIMEOUT_SEC,
        now: Callable[[], str] = _utc_now_iso,
        mode: str = DEFAULT_FRAME_ANALYSIS_MODE,
        scale_algorithm: str = DEFAULT_SCALE_ALGORITHM,
        video_workers: int = VIDEO_ANALYSIS_WORKERS,
        thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
        use_process_scan: bool | None = None,
    ) -> None:
        self._store: FrameAnalysisStore = store
        self._work_dir: str = work_dir
        self._ffmpeg_executable: str = ffmpeg_executable
        self._has_vmaf: bool = has_vmaf
        self._runner: StderrCommandRunner | None = runner
        self._timeout_sec: float = timeout_sec
        self._now: Callable[[], str] = now
        self._mode: str = mode
        self._scale_algorithm: str = normalize_scale_algorithm(scale_algorithm)
        self._analysis_version: int = analysis_version_for_mode(mode, self._scale_algorithm)
        self._video_workers: int = max(1, video_workers)
        self._thresholds: FrameAnalysisThresholds = thresholds
        # - 실행기를 주입한 테스트는 스캔도 같은 프로세스에서 돌려 실제 FFmpeg 없이 결과를 맞춤
        self._use_process_scan: bool = runner is None if use_process_scan is None else use_process_scan

    def analyze_batch(
        self,
        requests: Sequence[FrameAnalysisRequest],
        *,
        force: bool = False,
        crop_only: bool = False,
        crop_only_names: frozenset[str] = frozenset(),
        cancel_event: threading.Event | None = None,
        on_progress: ProgressCallback | None = None,
        on_stage: StageProgressCallback | None = None,
    ) -> dict[str, FrameAnalysisResult]:
        """요청 목록을 영상 단위 워커 풀로 분석하고 파일명별 결과를 반환. 취소되면 `InterruptedError`

        - crop_only면 1단계 검은 여백 스캔만 실행하고, 이미 있는 전체 화질 분석 행은 그대로 재사용
        - crop_only_names에 든 영상은 전체 실행에서도 검은 여백만 감지
        - 픽셀 판정은 배치 동안만 사는 프로세스 풀에서 돌리고, 끝나거나 취소되면 풀을 닫음
        """
        unique = _unique_requests(requests)
        total = len(unique)
        cached = {} if force else self._store.load_many([request.key for request in unique])
        results: dict[str, FrameAnalysisResult] = {}
        metric_name = resolve_metric_name(self._has_vmaf)
        os.makedirs(self._work_dir, exist_ok=True)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("화질 분석이 취소되었습니다")
        runner_injected = self._runner is not None
        inner_workers = _inner_round_workers(total, runner_injected=runner_injected)
        exec_runner = self._runner_for(cancel_event)
        pool: _ScanStatsPool | None = (
            _ScanStatsPool(self._video_workers, cancel_event=cancel_event)
            if self._use_process_scan
            else None
        )
        stats_runner: FrameStatsRunner = luma_frame_stats if pool is None else pool

        def _stage_progress(filename: str, stage: str, done: int, stage_total: int) -> None:
            if on_stage is not None:
                on_stage(filename, stage, done, stage_total)

        def _analyze_one(
            item: tuple[int, FrameAnalysisRequest],
        ) -> tuple[FrameAnalysisRequest, FrameAnalysisResult]:
            index, request = item
            request_crop_only = crop_only or request.filename in crop_only_names
            record = cached.get(request.key)
            if record is not None and _is_reusable(
                record,
                request,
                metric_name,
                self._analysis_version,
                crop_only=request_crop_only,
                thresholds=self._thresholds,
            ):
                return request, _rejudged_result(
                    record, request, self._thresholds, keep_candidate=request_crop_only
                )

            def _stage_callback(stage: str, done: int, stage_total: int) -> None:
                _stage_progress(request.filename, stage, done, stage_total)

            result = analyze_frames(
                source_path=request.path,
                width=request.width,
                height=request.height,
                duration_sec=request.duration_sec,
                work_dir=self._work_dir,
                runner=exec_runner,
                ffmpeg_executable=self._ffmpeg_executable,
                has_vmaf=self._has_vmaf,
                has_vr_metadata=request.has_vr_metadata,
                manual_vr=request.manual_vr,
                mode=self._mode,
                cancel_event=cancel_event,
                inner_workers=inner_workers,
                on_stage=_stage_callback,
                log_prefix=f"{index}_",
                prefer_pyav=not runner_injected,
                crop_only=request_crop_only,
                thresholds=self._thresholds,
                scale_algorithm=self._scale_algorithm,
                scan_stats_runner=stats_runner,
            )
            self._store.save(record_from_result(request, result, measured_at=self._now()))
            return request, result

        items = list(enumerate(unique, start=1))
        try:
            if self._video_workers <= 1 or total <= 1:
                for completed, item in enumerate(items, start=1):
                    request, result = _analyze_one(item)
                    results[request.filename] = result
                    if on_progress is not None:
                        on_progress(completed, total, request.filename, result)
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("화질 분석이 취소되었습니다")
                return results
            with ThreadPoolExecutor(self._video_workers) as threads:
                futures = [threads.submit(_analyze_one, item) for item in items]
                completed = 0
                try:
                    for future in as_completed(futures):
                        request, result = future.result()
                        completed += 1
                        results[request.filename] = result
                        if on_progress is not None:
                            on_progress(completed, total, request.filename, result)
                        if cancel_event is not None and cancel_event.is_set():
                            raise InterruptedError("화질 분석이 취소되었습니다")
                except BaseException:
                    for future in futures:
                        future.cancel()
                    raise
        finally:
            if pool is not None:
                pool.close()
        return results

    def count_cached(
        self,
        requests: Sequence[FrameAnalysisRequest],
        *,
        force: bool = False,
        crop_only: bool = False,
        crop_only_names: frozenset[str] = frozenset(),
    ) -> int:
        """이번 요청 중 지금 캐시로 재사용할 수 있는 건수"""
        unique = _unique_requests(requests)
        if force or not unique:
            return 0
        cached = self._store.load_many([request.key for request in unique])
        metric_name = resolve_metric_name(self._has_vmaf)
        return sum(
            1
            for request in unique
            if _is_reusable(
                cached.get(request.key),
                request,
                metric_name,
                self._analysis_version,
                crop_only=crop_only or request.filename in crop_only_names,
                thresholds=self._thresholds,
            )
        )

    def load_active_frames(
        self, requests: Sequence[FrameAnalysisRequest], *, crop_enabled: bool = True
    ) -> dict[str, ActiveFrame]:
        """잘라내기 확정 영상만 파일명별 활성 화면으로 반환

        - 설정 끔, VR 보호, 검은 여백 없음이면 전체 프레임 유지로 보고 넣지 않음
        - 사용자 지정 활성 화면은 자동 감지와 영상별 제외를 덮어씀
        """
        records = self._store.load_many([request.key for request in requests])
        excluded = self._store.load_crop_exclusions([request.path for request in requests])
        manual = self._store.load_manual_active_frames(
            {request.path: (request.width, request.height) for request in requests}
        )
        frames: dict[str, ActiveFrame] = {}
        for request in requests:
            record = records.get(request.key)
            if record is None or not is_usable_analysis_version(
                record.analysis_version, self._analysis_version
            ):
                continue
            frame = resolve_crop_frame(
                apply_no_bars_ratio(
                    record.active_region,
                    request.width,
                    request.height,
                    thresholds=self._thresholds,
                ),
                crop_enabled=crop_enabled,
                excluded=request.path in excluded,
                is_vr_protected=record.is_vr_protected,
                manual_frame=manual.get(request.path),
            )
            if frame is not None:
                frames[request.filename] = frame
        return frames

    def crop_review_statuses(
        self,
        requests: Sequence[FrameAnalysisRequest],
        *,
        replace_original: bool,
        crop_enabled: bool = True,
    ) -> dict[str, CropReviewStatus]:
        """어두운 근거로 지점을 무시한 자동 크롭의 표시 상태를 파일명별로 반환

        - flagged는 어두운 근거 무시로 안쪽 경계를 채택한 자동 크롭인지
        - hold_applies는 원본 교체 인코딩이라 자동 크롭을 보류하는지(사용자 지정 활성 화면이 있으면 거짓)
        - 판정에 쓸 수 없는 캐시 행(측정 버전 불일치, VR 보호, 검은 여백 면적 상한 초과)은 넣지 않음
        """
        records = self._store.load_many([request.key for request in requests])
        manual = self._store.load_manual_active_frames(
            {request.path: (request.width, request.height) for request in requests}
        )
        statuses: dict[str, CropReviewStatus] = {}
        for request in requests:
            record = records.get(request.key)
            if record is None or not is_usable_analysis_version(
                record.analysis_version, self._analysis_version
            ):
                continue
            region = apply_no_bars_ratio(
                record.active_region,
                request.width,
                request.height,
                thresholds=self._thresholds,
            )
            if not needs_crop_review(region):
                continue
            statuses[request.filename] = CropReviewStatus(
                flagged=True,
                hold_applies=review_holds_automatic_crop(
                    region,
                    replace_original=replace_original,
                    crop_enabled=crop_enabled,
                    is_vr_protected=record.is_vr_protected,
                    manual_frame=manual.get(request.path),
                ),
            )
        return statuses

    def load_results(self, requests: Sequence[FrameAnalysisRequest]) -> dict[str, FrameAnalysisResult]:
        """현재 작업 폴더의 캐시된 화질 분석 결과를 표시용으로 한 번에 읽음"""
        records = self._store.load_many([request.key for request in requests])
        results: dict[str, FrameAnalysisResult] = {}
        for request in requests:
            record = records.get(request.key)
            if record is None or not is_usable_analysis_version(
                record.analysis_version, self._analysis_version
            ):
                continue
            results[request.filename] = _rejudged_result(record, request, self._thresholds)
        return results

    def close(self) -> None:
        """캐시 저장소 연결을 닫음"""
        self._store.close()

    def _runner_for(self, cancel_event: threading.Event | None) -> StderrCommandRunner:
        if self._runner is not None:
            return self._runner
        return ProcessStderrRunner(cancel_event=cancel_event, timeout_sec=self._timeout_sec)
