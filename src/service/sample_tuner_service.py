"""품질값 정밀 결정 실행 서비스: 대표 구간 샘플 인코딩, VMAF/SSIM 측정, 결과 캐시(결정 18)

- 판정은 `src/core/evaluation/sample_tuner.py`의 순수 이진 탐색이 맡고 이 모듈은 프로세스만 다룸
- 샘플 인코딩은 공용 엔진(`run_sample_pass`)과 본 인코딩 인자 빌더를 그대로 재사용
- 지표 명령은 `quality_probe`의 필터 구성과 로그 파서를 재사용하고 별도 파서를 두지 않음
- 취소는 `threading.Event`로 전달하며 실행기와 샘플 엔진이 자식 프로세스를 kill함
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections.abc import Callable, Sequence

from src.core.analysis.frame_analysis_runner import ProcessStderrRunner
from src.core.encode.encode_plan import EncodePlan
from src.core.encode.ffmpeg_command import FFMPEG_EXECUTABLE_DEFAULT, build_sample_ffmpeg_args
from src.core.encode.quality_tuning import sample_segments
from src.core.encoder_specs import get_encoder_spec
from src.core.evaluation.sample_tuner import (
    REASON_UNMEASURABLE,
    TUNE_MAX_PROBES,
    DownscaleComparison,
    QualityProbe,
    TuneResult,
    TuneTarget,
    compare_downscale,
    search_quality,
    tune_bounds,
)
from src.core.models import ActiveFrame
from src.core.paths import get_data_dir
from src.core.quality_probe import (
    LOG_NAME_SSIM,
    LOG_NAME_VMAF,
    METRIC_SSIM,
    METRIC_VMAF,
    VMAF_MODEL_DEFAULT,
    build_segment_metric_filter,
    parse_ssim_stats,
    parse_vmaf_log,
    vmaf_model_for,
)
from src.core.sample_points import SegmentQuality, is_invalid_segment
from src.service.analysis_store import ANALYSIS_DB_FILENAME
from src.service.encode.encode_pause import PauseControl
from src.service.encode.encode_ports import ProcessFactory, default_process_factory
from src.service.encode.encode_sample_engine import run_sample_pass, sample_segment_path
from src.service.sample_tuner_cache import (
    CACHE_COLUMNS as CACHE_COLUMNS,
)
from src.service.sample_tuner_cache import (
    SAMPLE_TUNER_TABLE_NAME as SAMPLE_TUNER_TABLE_NAME,
)
from src.service.sample_tuner_cache import (
    SampleTunerCache as SampleTunerCache,
)
from src.service.sample_tuner_cache import (
    SampleTuneRecord as SampleTuneRecord,
)
from src.service.sample_tuner_cache import (
    TuneCacheKey as TuneCacheKey,
)
from src.service.sample_tuner_cache import (
    TuneJob as TuneJob,
)
from src.service.sample_tuner_cache import (
    probes_from_json as probes_from_json,
)
from src.service.sample_tuner_cache import (
    probes_to_json as probes_to_json,
)
from src.service.sample_tuner_cache import (
    record_from_result,
    tune_cache_key,
)
from src.service.sample_tuner_cache import (
    record_to_row as record_to_row,
)
from src.service.sample_tuner_cache import (
    row_to_record as row_to_record,
)
from src.service.sample_tuner_cache import (
    source_fingerprint as source_fingerprint,
)
from src.service.sample_tuner_cache import (
    utc_now_iso as utc_now_iso,
)

SAMPLE_TUNER_TEMP_PREFIX: str = "d18_tuner_"
# - 결정 18: 대표 구간 2~3개(공통 5지점에서 균등 선택)로 프로브당 비용을 묶음
TUNABLE_SEGMENT_COUNT: int = 3
BITS_PER_BYTE: int = 8
BITS_PER_KBIT: int = 1000
PLAN_HASH_HEX_DIGITS: int = 16


def plan_signature(plan: EncodePlan) -> str:
    """크롭/축소/비트레이트 상한 계획을 캐시 키용 짧은 해시로 변환"""
    parts = "|".join(
        (
            plan.crop_filter or "",
            plan.scale_filter or "",
            "" if plan.output_size is None else f"{plan.output_size[0]}x{plan.output_size[1]}",
            "" if plan.maxrate_kbps is None else str(plan.maxrate_kbps),
            plan.pixel_format,
        )
    )
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:PLAN_HASH_HEX_DIGITS]


def tuning_segments(
    duration_sec: float,
    black_ratios: Sequence[float] | None = None,
) -> tuple[tuple[float, float], ...]:
    """공통 구간 규칙의 후보에서 대표 2~3구간을 고르고 검은 구간을 뺌

    - 20초 미만은 전체 1구간(공통 규칙), 그 밖은 10~90% 안 5지점에서 균등하게 3지점 선택
    - 검은 비율은 화질 분석 결과를 받아 쓰고, 모두 걸러지면 첫 구간을 남겨 측정 불가를 피함
    """
    segments = sample_segments(duration_sec)
    if len(segments) > TUNABLE_SEGMENT_COUNT:
        step = (len(segments) - 1) / (TUNABLE_SEGMENT_COUNT - 1)
        segments = tuple(segments[round(index * step)] for index in range(TUNABLE_SEGMENT_COUNT))
    if black_ratios is None:
        return segments
    kept: list[tuple[float, float]] = []
    for index, segment in enumerate(segments):
        ratio = black_ratios[index] if index < len(black_ratios) else 0.0
        sample = SegmentQuality(start_sec=segment[0], duration_sec=segment[1], black_ratio=ratio)
        if not is_invalid_segment(sample):
            kept.append(segment)
    return tuple(kept) if kept else segments[:1]


def probe_bitrate_kbps(total_bytes: int, sample_duration_sec: float) -> float:
    """샘플 바이트 합계를 샘플 길이로 나눈 비트레이트(kbps). 길이를 모르면 0"""
    if total_bytes <= 0 or sample_duration_sec <= 0:
        return 0.0
    return total_bytes * BITS_PER_BYTE / BITS_PER_KBIT / sample_duration_sec


def build_segment_metric_command(
    *,
    ffmpeg_executable: str,
    distorted_path: str,
    reference_path: str,
    start_sec: float,
    duration_sec: float,
    metric: str,
    model: str,
    log_name: str,
    crop_filter: str | None = None,
    upscale_size: tuple[int, int] | None = None,
) -> list[str]:
    """샘플 구간 결과와 원본 같은 구간의 지표를 재는 FFmpeg 인자 목록

    - 기준 입력에만 시크와 길이를 붙여 원본 전체를 읽지 않음
    - 샘플은 계획의 크롭과 축소를 거쳤으므로 기준에 같은 크롭을 적용하고, 축소 샘플은 기준 크기로 되돌려 비교
    - 필터 문자열은 `quality_probe.build_segment_metric_filter`를 그대로 씀
    """
    return [
        ffmpeg_executable,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        distorted_path,
        "-ss",
        f"{start_sec:.3f}",
        "-t",
        f"{duration_sec:.3f}",
        "-i",
        reference_path,
        "-lavfi",
        build_segment_metric_filter(
            metric,
            model=model,
            log_name=log_name,
            crop_filter=crop_filter,
            upscale_size=upscale_size,
        ),
        "-f",
        "null",
        "-",
    ]


def _crop_size(crop_filter: str | None) -> tuple[int, int] | None:
    """crop 필터 문자열의 결과 화소 크기. 필터가 없거나 형식이 다르면 None"""
    if not crop_filter:
        return None
    frame = ActiveFrame.from_crop_filter(crop_filter)
    return None if frame is None else (frame.width, frame.height)


def _median(values: Sequence[float]) -> float | None:
    """측정값 목록의 중앙값. 값이 없으면 None"""
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _measure_segment_metric(
    *,
    runner: Callable[[list[str], str], tuple[int, str]],
    command: list[str],
    work_dir: str,
    metric: str,
    log_name: str,
) -> float | None:
    """한 구간의 지표를 재고 로그를 파싱. 명령 실패나 로그 부재는 None

    - 파싱은 `quality_probe`의 로그 파서를 그대로 씀
    """
    log_path = os.path.join(work_dir, log_name)
    if os.path.exists(log_path):
        os.remove(log_path)
    if runner(command, work_dir)[0] != 0 or not os.path.exists(log_path):
        return None
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    if metric == METRIC_VMAF:
        return parse_vmaf_log(text)
    stats = parse_ssim_stats(text)
    return None if stats is None else stats.score


def _measure_probe(
    *,
    plan: EncodePlan,
    segments: Sequence[tuple[float, float]],
    quality: float,
    ffmpeg_exec: str,
    process_factory: ProcessFactory,
    metric_runner: Callable[[list[str], str], tuple[int, str]],
    has_vmaf: bool,
    cancel_event: threading.Event | None,
    control: PauseControl | None = None,
    batch_id: int = 0,
    reference_size: tuple[int, int] | None = None,
) -> QualityProbe:
    """품질값 하나의 표본을 만듭니다: 구간별 샘플 인코딩 뒤 VMAF(없으면 SSIM) 중앙값

    - 산출물은 임시 폴더에만 쓰고 지표 측정이 끝나면 폴더째 지움
    - 취소되면 점수 없는 프로브를 돌려주고 다음 탐색이 멈추게 함
    - VMAF 측정이 모두 실패하면 같은 산출물로 SSIM을 재서 대체(결정 18의 지표 우선순위)
    - control을 주면 샘플 프로세스 묶음을 실행 항목으로 등록해 일시중지와 중지가 전파됨
    - `reference_size`는 크롭을 적용한 원본 화소 크기. 지표 모델 선택과 축소 샘플 되돌리기에 씀
    """
    if not segments or quality <= 0:
        return QualityProbe(quality=quality)
    # - 샘플은 계획의 크롭/축소를 거치므로 crop 뒤 기준 크기에서 모델을 고르고, 축소 샘플만 그 크기로 되돌림
    resolved_reference = _crop_size(plan.crop_filter) or reference_size
    model = (
        vmaf_model_for(resolved_reference[0], resolved_reference[1])
        if resolved_reference is not None
        else VMAF_MODEL_DEFAULT
    )
    upscale_size = resolved_reference if plan.scale_filter is not None else None
    crop_filter = plan.crop_filter
    with tempfile.TemporaryDirectory(prefix=SAMPLE_TUNER_TEMP_PREFIX) as tmp_dir:
        outcome = run_sample_pass(
            batch_id=batch_id,
            source_path=plan.source_path,
            plan=plan,
            ffmpeg_exec=ffmpeg_exec,
            process_factory=process_factory,
            abort=cancel_event if cancel_event is not None else threading.Event(),
            segments=tuple(segments),
            quality=quality,
            build_args=build_sample_ffmpeg_args,
            control=control,
            temp_prefix=SAMPLE_TUNER_TEMP_PREFIX,
            output_dir=tmp_dir,
        )
        if outcome.cancelled or outcome.succeeded_segments < outcome.segment_count:
            return QualityProbe(quality=quality)
        sample_duration = sum(segment[1] for segment in segments)
        vmaf_scores = (
            _metric_scores(
                segments=segments,
                tmp_dir=tmp_dir,
                source_path=plan.source_path,
                ffmpeg_exec=ffmpeg_exec,
                metric_runner=metric_runner,
                metric=METRIC_VMAF,
                model=model,
                cancel_event=cancel_event,
                crop_filter=crop_filter,
                upscale_size=upscale_size,
            )
            if has_vmaf
            else []
        )
        if cancel_event is not None and cancel_event.is_set():
            return QualityProbe(quality=quality)
        ssim_scores = (
            []
            if vmaf_scores
            else _metric_scores(
                segments=segments,
                tmp_dir=tmp_dir,
                source_path=plan.source_path,
                ffmpeg_exec=ffmpeg_exec,
                metric_runner=metric_runner,
                metric=METRIC_SSIM,
                model=model,
                cancel_event=cancel_event,
                crop_filter=crop_filter,
                upscale_size=upscale_size,
            )
        )
        return QualityProbe(
            quality=quality,
            vmaf=_median(vmaf_scores),
            ssim=_median(ssim_scores),
            bitrate_kbps=probe_bitrate_kbps(outcome.total_bytes, sample_duration),
        )


def _metric_scores(
    *,
    segments: Sequence[tuple[float, float]],
    tmp_dir: str,
    source_path: str,
    ffmpeg_exec: str,
    metric_runner: Callable[[list[str], str], tuple[int, str]],
    metric: str,
    model: str,
    cancel_event: threading.Event | None,
    crop_filter: str | None = None,
    upscale_size: tuple[int, int] | None = None,
) -> list[float]:
    """구간별 지표 점수 목록. 취소되면 그때까지의 점수만 돌려줌

    - 왜곡 입력은 샘플 엔진이 남긴 구간 산출물 경로를 같은 함수로 만들어 읽음
    """
    log_name = LOG_NAME_VMAF if metric == METRIC_VMAF else LOG_NAME_SSIM
    scores: list[float] = []
    for index, (start_sec, duration_sec) in enumerate(segments):
        if cancel_event is not None and cancel_event.is_set():
            return scores
        score = _measure_segment_metric(
            runner=metric_runner,
            command=build_segment_metric_command(
                ffmpeg_executable=ffmpeg_exec,
                distorted_path=sample_segment_path(tmp_dir, index),
                reference_path=source_path,
                start_sec=start_sec,
                duration_sec=duration_sec,
                metric=metric,
                model=model,
                log_name=log_name,
                crop_filter=crop_filter,
                upscale_size=upscale_size,
            ),
            work_dir=tmp_dir,
            metric=metric,
            log_name=log_name,
        )
        if score is not None:
            scores.append(score)
    return scores


def tune_quality_for_video(
    *,
    job: TuneJob,
    plan: EncodePlan,
    ffmpeg_exec: str = FFMPEG_EXECUTABLE_DEFAULT,
    process_factory: ProcessFactory | None = None,
    cancel_event: threading.Event | None = None,
    target: TuneTarget | None = None,
    has_vmaf: bool = True,
    black_ratios: Sequence[float] | None = None,
    max_probes: int = TUNE_MAX_PROBES,
    delta_max: float | None = None,
    metric_runner: Callable[[list[str], str], tuple[int, str]] | None = None,
    on_probe: Callable[[int, int, float], None] | None = None,
    control: PauseControl | None = None,
    batch_id: int = 0,
) -> TuneResult:
    """영상 한 개의 품질값 정밀 결정을 실제 샘플 인코딩과 지표 측정으로 수행

    - 구간 선택은 결정 19 공통 규칙(10~90%, 20초 미만 전체, 검은 구간 제외)을 따름
    - 취소되면 그때까지 통과한 최고값을 담고 `converged=False`로 돌려줌
    - `delta_max`가 없으면 저화질 보호 기본 한도(`PROTECT_CQ_DELTA_MAX`)를 씀
    - `metric_runner`를 주면 지표 실행기를 바꿔 끼움(단위 테스트가 FFmpeg 없이 검증)
    - `on_probe`는 프로브를 재기 직전에 (차례, 전체 상한, 품질값)으로 불려 진행 표시에 씀
    - `control`을 주면 정밀 결정 샘플도 일시중지와 중지 대상이 됨
    - 기준 화소 크기를 모르는 축소 계획은 지표를 잴 수 없으므로 샘플 인코딩 전에 측정 불가로 되돌림
    """
    resolved_target = target if target is not None else TuneTarget()
    spec = get_encoder_spec(job.encoder)
    integer_scale = spec is not None and spec.integer_scale
    baseline = job.baseline_quality
    low, high = tune_bounds(baseline, **({} if delta_max is None else {"delta_max": delta_max}))
    segments = tuning_segments(job.source_duration_sec, black_ratios)
    # - 기준 크기는 크롭을 적용한 원본 화소 크기. 축소 계획이면 이 크기로 샘플을 되돌려 지표를 잼
    reference_size = (job.width, job.height) if job.width > 0 and job.height > 0 else _crop_size(plan.crop_filter)
    # - 옛 작업 JSON을 이어서 인코딩하면 원본 화소 크기가 0으로 복원됨. 축소 샘플을 되돌릴 기준이 없으면
    #   모든 지표가 크기 불일치로 실패하므로 샘플 인코딩을 쓰지 않고 기준 품질값을 유지
    if reference_size is None and plan.scale_filter is not None:
        return TuneResult(best_quality=baseline, probes=(), converged=False, reason=REASON_UNMEASURABLE)
    factory = process_factory if process_factory is not None else default_process_factory
    runner = metric_runner if metric_runner is not None else _metric_runner(cancel_event)
    probe_count = 0

    def _measure(quality: float) -> QualityProbe:
        """품질값 하나를 실제로 재서 프로브로 돌려줌. 취소면 점수 없음"""
        nonlocal probe_count
        if cancel_event is not None and cancel_event.is_set():
            return QualityProbe(quality=quality)
        probe_count += 1
        if on_probe is not None:
            on_probe(probe_count, max_probes, quality)
        return _measure_probe(
            plan=plan,
            segments=segments,
            quality=quality,
            ffmpeg_exec=ffmpeg_exec,
            process_factory=factory,
            metric_runner=runner,
            has_vmaf=has_vmaf,
            cancel_event=cancel_event,
            control=control,
            batch_id=batch_id,
            reference_size=reference_size,
        )

    return search_quality(
        _measure,
        low,
        high,
        resolved_target,
        max_probes=max_probes,
        integer_scale=integer_scale,
    )


def _metric_runner(cancel_event: threading.Event | None) -> Callable[[list[str], str], tuple[int, str]]:
    """지표 명령 실행기. 취소 신호를 넘겨 실행 중인 FFmpeg를 kill하게 함"""
    return ProcessStderrRunner(cancel_event=cancel_event)


def compare_downscale_samples(
    *,
    job: TuneJob,
    original_plan: EncodePlan,
    candidate_plan: EncodePlan,
    baseline_quality: float,
    protect_quality: float,
    ffmpeg_exec: str = FFMPEG_EXECUTABLE_DEFAULT,
    process_factory: ProcessFactory | None = None,
    cancel_event: threading.Event | None = None,
    target: TuneTarget | None = None,
    has_vmaf: bool = True,
    black_ratios: Sequence[float] | None = None,
    metric_runner: Callable[[list[str], str], tuple[int, str]] | None = None,
    candidate_probe: QualityProbe | None = None,
) -> DownscaleComparison:
    """[원본(크롭) 해상도 + 보호 품질값]과 [축소 해상도 + 기준 품질값]을 같은 구간 샘플로 비교

    - 두 계획은 원본 경로가 같아야 하고, 비트레이트는 각 계획의 샘플 산출물 실측값으로 비교
    - `candidate_probe`를 주면 이미 잰 축소 계획 프로브를 다시 인코딩하지 않음(정밀 탐색 결과 재사용)
    - 판정은 순수 함수 `compare_downscale`이 맡음
    - 기준 화소 크기는 작업 원본 크기, 없으면 크롭 뒤 크기. 그것도 없으면 축소 샘플을 되돌릴 수 없으므로
      샘플 인코딩 없이 원본 해상도 유지로 판정
    """
    factory = process_factory if process_factory is not None else default_process_factory
    runner = metric_runner if metric_runner is not None else _metric_runner(cancel_event)
    segments = tuning_segments(job.source_duration_sec, black_ratios)
    # - 옛 작업 JSON은 원본 화소 크기가 0이라, 크롭이 있으면 탐색과 같은 크롭 크기를 기준으로 씀
    reference_size = (
        (job.width, job.height) if job.width > 0 and job.height > 0 else _crop_size(candidate_plan.crop_filter)
    )
    if reference_size is None and candidate_plan.scale_filter is not None:
        # - 기준 화소 크기가 없으면 두 계획 모두 지표를 잴 수 없음. 측정 없이 원본 해상도를 유지
        return DownscaleComparison(
            False,
            QualityProbe(quality=baseline_quality),
            QualityProbe(quality=protect_quality),
            REASON_UNMEASURABLE,
        )
    original_probe = _measure_probe(
        plan=original_plan,
        segments=segments,
        quality=protect_quality,
        ffmpeg_exec=ffmpeg_exec,
        process_factory=factory,
        metric_runner=runner,
        has_vmaf=has_vmaf,
        cancel_event=cancel_event,
        reference_size=reference_size,
    )
    measured_candidate = (
        _measure_probe(
            plan=candidate_plan,
            segments=segments,
            quality=baseline_quality,
            ffmpeg_exec=ffmpeg_exec,
            process_factory=factory,
            metric_runner=runner,
            has_vmaf=has_vmaf,
            cancel_event=cancel_event,
            reference_size=reference_size,
        )
        if candidate_probe is None
        else candidate_probe
    )
    return compare_downscale(measured_candidate, original_probe, target)


def comparison_plan_hash(candidate_plan: EncodePlan, original_plan: EncodePlan) -> str:
    """축소 비교 캐시 키의 계획 해시. 두 옵션의 계획 서명을 함께 넣어 한쪽만 바뀌어도 무효화"""
    return f"{plan_signature(candidate_plan)}+{plan_signature(original_plan)}"


def record_from_comparison(
    key: TuneCacheKey,
    comparison: DownscaleComparison,
    *,
    measured_at: str | None = None,
) -> SampleTuneRecord:
    """비교 결과를 캐시 레코드로 변환. `converged` 자리에 축소 유지 여부를 담음

    - 프로브는 (축소 후보, 원본 규격) 순서로 저장하고 복원할 때 같은 순서를 씀
    """
    winner = comparison.candidate_probe if comparison.use_downscale else comparison.original_probe
    return SampleTuneRecord(
        key=key,
        best_quality=winner.quality,
        converged=comparison.use_downscale,
        reason=comparison.reason,
        probes=(comparison.candidate_probe, comparison.original_probe),
        measured_at=measured_at if measured_at is not None else utc_now_iso(),
    )


def comparison_from_record(record: SampleTuneRecord) -> DownscaleComparison | None:
    """캐시 레코드를 비교 결과로 복원. 프로브 두 개가 아니면 None(캐시 미스로 처리)"""
    if len(record.probes) != 2:
        return None
    candidate, original = record.probes
    return DownscaleComparison(record.converged, candidate, original, record.reason)


def compare_downscale_cached(
    *,
    job: TuneJob,
    candidate_plan: EncodePlan,
    original_plan: EncodePlan,
    candidate_probe: QualityProbe,
    original_quality: float,
    cache: SampleTunerCache,
    target: TuneTarget | None = None,
    process_factory: ProcessFactory | None = None,
    ffmpeg_exec: str = FFMPEG_EXECUTABLE_DEFAULT,
    cancel_event: threading.Event | None = None,
    has_vmaf: bool = True,
    metric_runner: Callable[[list[str], str], tuple[int, str]] | None = None,
) -> DownscaleComparison:
    """축소 비교를 캐시 먼저 확인하고, 없으면 원본 옵션만 샘플 인코딩해 판정한 뒤 저장

    - 캐시 키에는 두 옵션의 계획 해시를 함께 넣어 어느 한쪽 계획만 바뀌어도 다시 비교
    - 축소 옵션 프로브는 정밀 탐색이 이미 잰 값을 받아 추가 인코딩 없이 씀
    - 취소된 비교는 오염을 막으려고 저장하지 않음
    """
    resolved_target = target if target is not None else TuneTarget()
    key = tune_cache_key(
        job,
        baseline_quality=original_quality,
        plan_hash=comparison_plan_hash(candidate_plan, original_plan),
        target_key=resolved_target.cache_key,
    )
    if key is not None:
        cached = cache.load(key)
        if cached is not None:
            restored = comparison_from_record(cached)
            if restored is not None:
                return restored
    comparison = compare_downscale_samples(
        job=job,
        original_plan=original_plan,
        candidate_plan=candidate_plan,
        baseline_quality=candidate_probe.quality,
        protect_quality=original_quality,
        ffmpeg_exec=ffmpeg_exec,
        process_factory=process_factory,
        cancel_event=cancel_event,
        target=resolved_target,
        has_vmaf=has_vmaf,
        metric_runner=metric_runner,
        candidate_probe=candidate_probe,
    )
    cancelled = cancel_event is not None and cancel_event.is_set()
    if key is not None and not cancelled and comparison.original_probe.measurable:
        cache.save(record_from_comparison(key, comparison))
    return comparison


def open_default_cache() -> SampleTunerCache:
    """data 폴더의 화질 분석 캐시 DB에 정밀 결정 표를 붙인 저장소 생성, 연결은 첫 작업 때"""
    return SampleTunerCache(os.path.join(get_data_dir(), ANALYSIS_DB_FILENAME))


def tune_quality_cached(
    *,
    job: TuneJob,
    plan: EncodePlan,
    cache: SampleTunerCache,
    target: TuneTarget | None = None,
    process_factory: ProcessFactory | None = None,
    ffmpeg_exec: str = FFMPEG_EXECUTABLE_DEFAULT,
    cancel_event: threading.Event | None = None,
    has_vmaf: bool = True,
    black_ratios: Sequence[float] | None = None,
    max_probes: int = TUNE_MAX_PROBES,
    delta_max: float | None = None,
    metric_runner: Callable[[list[str], str], tuple[int, str]] | None = None,
    on_probe: Callable[[int, int, float], None] | None = None,
    control: PauseControl | None = None,
    batch_id: int = 0,
) -> TuneResult:
    """캐시를 먼저 확인하고 없으면 샘플 인코딩으로 탐색한 뒤 결과를 저장

    - 6a-2 화면과 인코딩 경로가 그대로 부를 수 있는 단일 진입점
    - 취소된 탐색 결과는 오염을 막으려고 저장하지 않음
    """
    resolved_target = target if target is not None else TuneTarget()
    key = tune_cache_key(
        job,
        baseline_quality=job.baseline_quality,
        plan_hash=plan_signature(plan),
        target_key=resolved_target.cache_key,
    )
    if key is not None:
        cached = cache.load(key)
        if cached is not None:
            return cached.result
    result = tune_quality_for_video(
        job=job,
        plan=plan,
        ffmpeg_exec=ffmpeg_exec,
        process_factory=process_factory,
        cancel_event=cancel_event,
        target=resolved_target,
        has_vmaf=has_vmaf,
        black_ratios=black_ratios,
        max_probes=max_probes,
        delta_max=delta_max,
        metric_runner=metric_runner,
        on_probe=on_probe,
        control=control,
        batch_id=batch_id,
    )
    cancelled = cancel_event is not None and cancel_event.is_set()
    if key is not None and not cancelled and result.reason != REASON_UNMEASURABLE:
        cache.save(record_from_result(key, result))
    return result
