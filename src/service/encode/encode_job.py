"""인코딩 작업 입력: 설정 사본, 영상별 계획 입력, 작업 기록용 직렬화와 배치 생성

- 배치 시작 시 만든 EncodeSettings와 EncodeJob은 배치가 끝날 때까지 바뀌지 않음
"""

import dataclasses
import json
import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from src.core.config import BitrateModelConfig, ClassifierConfig, ExecutionConfig, ThresholdsConfig
from src.core.encode.encode_concurrency import resolve_concurrency
from src.core.encode.encode_gate import GatePurpose, purpose_for_evaluation
from src.core.encode.encode_progress import EARLY_STOP_MARGIN
from src.core.encode.precise_tune_policy import should_precise_tune
from src.core.encode.quality_tuning import SAMPLE_SAVING_DISCARD_FACTOR
from src.core.encode_tag import DEFAULT_ENCODE_TAG_NAME, ENCODE_TAG_MODE_VISIBLE
from src.core.encoder_specs import get_encoder_spec
from src.core.evaluation.size_estimator import calc_keyframe_increase_ratio, estimate_video_stream_mb
from src.core.models import VideoEvaluation
from src.core.quality_target import (
    PROTECT_CQ_DELTA_MAX,
    corrected_target_kbps,
    general_maxrate_kbps,
)
from src.core.resolution_tiers import DEFAULT_SCALE_ALGORITHM, build_scale_expression
from src.core.workspace_paths import resolve_source_filepath, split_logical_filename
from src.service.encode.encode_history import EncodeHistory, NewItem

DEFAULT_KEYFRAME_INCREASE_RATE: float = ThresholdsConfig().keyframe_1s_increase_rate
DEFAULT_BASE_FPS: float = BitrateModelConfig().base_fps

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EncodeSettings:
    """배치 하나에 고정되는 실행 설정 사본(화면 설정과 분리)"""

    encoder: str
    container_policy: str
    result_policy: str
    archive_dirname: str
    output_dirname: str
    name_conflict: str
    process_priority: str
    ffmpeg_path: str
    ffprobe_path: str
    concurrent_jobs: int
    prevent_sleep: bool
    after_complete: str
    encode_tag_mode: str = ENCODE_TAG_MODE_VISIBLE
    encode_tag_name: str = DEFAULT_ENCODE_TAG_NAME
    # - 품질값 자동 조절 한도, 사전 예측 중단 기준, 인코딩 중 자동 중단 기준(배치 시작 시 설정 사본)
    protect_cq_delta_max: float = PROTECT_CQ_DELTA_MAX
    sample_saving_discard_factor: float = SAMPLE_SAVING_DISCARD_FACTOR
    early_stop_margin: float = EARLY_STOP_MARGIN

    @classmethod
    def from_config(cls, config: ClassifierConfig) -> "EncodeSettings":
        """현재 설정에서 사본을 만듦"""
        execution: ExecutionConfig = config.execution
        return cls(
            encoder=config.encode.encode_codec,
            container_policy=execution.container_policy,
            result_policy=execution.result_policy,
            archive_dirname=execution.archive_dirname,
            output_dirname=execution.output_dirname,
            name_conflict=execution.name_conflict,
            process_priority=execution.process_priority,
            ffmpeg_path=execution.ffmpeg_path,
            ffprobe_path=execution.ffprobe_path,
            concurrent_jobs=execution.concurrent_jobs,
            prevent_sleep=execution.prevent_sleep,
            after_complete=execution.after_complete,
            encode_tag_mode=execution.encode_tag_mode,
            encode_tag_name=execution.encode_tag_name,
            protect_cq_delta_max=execution.protect_cq_delta_max,
            sample_saving_discard_factor=execution.sample_saving_discard_factor,
            early_stop_margin=execution.early_stop_margin,
        )

    @classmethod
    def from_json(cls, text: str) -> "EncodeSettings":
        """작업 기록의 JSON에서 설정 사본을 복원"""
        raw: dict[str, Any] = json.loads(text)
        return cls(
            encoder=str(raw["encoder"]),
            container_policy=str(raw["container_policy"]),
            result_policy=str(raw["result_policy"]),
            archive_dirname=str(raw["archive_dirname"]),
            output_dirname=str(raw["output_dirname"]),
            name_conflict=str(raw["name_conflict"]),
            process_priority=str(raw["process_priority"]),
            ffmpeg_path=str(raw["ffmpeg_path"]),
            ffprobe_path=str(raw["ffprobe_path"]),
            concurrent_jobs=int(raw["concurrent_jobs"]),
            prevent_sleep=bool(raw["prevent_sleep"]),
            after_complete=str(raw["after_complete"]),
            encode_tag_mode=str(raw.get("encode_tag_mode", ENCODE_TAG_MODE_VISIBLE)),
            encode_tag_name=str(raw.get("encode_tag_name", DEFAULT_ENCODE_TAG_NAME)),
            protect_cq_delta_max=float(raw.get("protect_cq_delta_max", PROTECT_CQ_DELTA_MAX)),
            sample_saving_discard_factor=float(
                raw.get("sample_saving_discard_factor", SAMPLE_SAVING_DISCARD_FACTOR)
            ),
            early_stop_margin=float(raw.get("early_stop_margin", EARLY_STOP_MARGIN)),
        )

    def to_json(self) -> str:
        """작업 기록에 저장할 JSON 문자열"""
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class EncodeJob:
    """영상 한 개의 계획 입력. 판정 결과와 원본 식별 값을 묶은 사본"""

    filename: str
    root_dir: str
    source_path: str
    source_size: int
    source_mtime_ns: int
    source_duration_sec: float
    source_ext: str
    fps: float
    quality: float
    purpose: GatePurpose
    output_size: tuple[int, int] | None
    scale_filter: str | None
    maxrate_kbps: int | None
    # - 판정 단계의 예상 용량과 예상 절약(MB). 이전 기록에는 없으므로 None이며 화면은 정보 없음으로 표시
    expected_after_mb: float | None = None
    expected_delta_mb: float | None = None
    keyframe_increase_ratio: float = 0.0
    fallback_video_mb: float | None = None
    # - 인코더 계수로 고친 목표 비트레이트(kbps). 품질값 자동 조정과 최대 비트레이트의 기준
    corrected_target_kbps: float | None = None
    # - 잘라내기 확정 영상의 FFmpeg crop 필터. 축소와 함께 적용하면 crop 후 scale 순서
    crop_filter: str | None = None
    # - 설정의 화질 기준값(실제 기준 CQ). 품질값은 저화질 보호 오프셋만큼 기준보다 높을 수 있어
    #   자동 조정 상한은 이 값 기준으로 계산. 이전 기록에는 없으므로 None이며 그때는 quality를 기준으로 씀
    baseline_quality: float | None = None
    # - 품질값 정밀 결정(결정 18) 대상 여부. 배치를 만들 때 설정과 판정으로 정해 기록에 남김
    precise_tune: bool = False
    # - 축소 후보 비교(6a-3) 입력: 축소 전 원본(크롭) 화소 크기와 원본 해상도 기준 보정 목표 비트레이트.
    #   원본 옵션이 이기면 job의 출력 크기, scale, maxrate를 이 값으로 다시 계산. 모르면 None/0
    source_width: int | None = None
    source_height: int | None = None
    original_target_kbps: float | None = None
    # - 출력 해상도를 영상별로 직접 지정했는지. 직접 지정은 사용자 의도이므로 축소 비교를 하지 않음
    manual_downscale: bool = False
    # - 축소 비교 확정 결과(원본 유지/축소 유지). 이어서 인코딩할 때 비교를 다시 하지 않도록 기록에 남김
    downscale_decision: str = ""


def job_to_json(job: EncodeJob) -> str:
    """계획 입력을 작업 기록용 JSON으로 직렬화"""
    payload: dict[str, object] = {
        "filename": job.filename,
        "root_dir": job.root_dir,
        "source_path": job.source_path,
        "source_size": job.source_size,
        "source_mtime_ns": job.source_mtime_ns,
        "source_duration_sec": job.source_duration_sec,
        "source_ext": job.source_ext,
        "fps": job.fps,
        "quality": job.quality,
        "purpose": job.purpose.value,
        "output_size": None if job.output_size is None else list(job.output_size),
        "scale_filter": job.scale_filter,
        "maxrate_kbps": job.maxrate_kbps,
        "expected_after_mb": job.expected_after_mb,
        "expected_delta_mb": job.expected_delta_mb,
        "keyframe_increase_ratio": job.keyframe_increase_ratio,
        "fallback_video_mb": job.fallback_video_mb,
        "corrected_target_kbps": job.corrected_target_kbps,
        "crop_filter": job.crop_filter,
        "baseline_quality": job.baseline_quality,
        "precise_tune": job.precise_tune,
        "source_width": job.source_width,
        "source_height": job.source_height,
        "original_target_kbps": job.original_target_kbps,
        "manual_downscale": job.manual_downscale,
        "downscale_decision": job.downscale_decision,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def job_from_json(text: str) -> EncodeJob:
    """작업 기록의 JSON에서 계획 입력을 복원"""
    raw: dict[str, Any] = json.loads(text)
    size = raw["output_size"]
    return EncodeJob(
        filename=str(raw["filename"]),
        root_dir=str(raw["root_dir"]),
        source_path=str(raw["source_path"]),
        source_size=int(raw["source_size"]),
        source_mtime_ns=int(raw["source_mtime_ns"]),
        source_duration_sec=float(raw["source_duration_sec"]),
        source_ext=str(raw["source_ext"]),
        fps=float(raw["fps"]),
        quality=float(raw["quality"]),
        purpose=GatePurpose(str(raw["purpose"])),
        output_size=None if size is None else (int(size[0]), int(size[1])),
        scale_filter=None if raw["scale_filter"] is None else str(raw["scale_filter"]),
        maxrate_kbps=None if raw["maxrate_kbps"] is None else int(raw["maxrate_kbps"]),
        expected_after_mb=_optional_float(raw.get("expected_after_mb")),
        expected_delta_mb=_optional_float(raw.get("expected_delta_mb")),
        keyframe_increase_ratio=float(raw.get("keyframe_increase_ratio", 0.0)),
        fallback_video_mb=_optional_float(raw.get("fallback_video_mb")),
        corrected_target_kbps=_optional_float(raw.get("corrected_target_kbps")),
        crop_filter=None if raw.get("crop_filter") is None else str(raw["crop_filter"]),
        baseline_quality=_optional_float(raw.get("baseline_quality")),
        precise_tune=bool(raw.get("precise_tune", False)),
        source_width=_optional_int(raw.get("source_width")),
        source_height=_optional_int(raw.get("source_height")),
        original_target_kbps=_optional_float(raw.get("original_target_kbps")),
        manual_downscale=bool(raw.get("manual_downscale", False)),
        downscale_decision=str(raw.get("downscale_decision", "")),
    )


def _optional_int(value: object) -> int | None:
    """기록 JSON의 선택 정수 값. 없거나 null이면 None"""
    return int(value) if isinstance(value, int | float) else None


def _optional_float(value: object) -> float | None:
    """기록 JSON의 선택 실수 값. 없거나 null이면 None"""
    return float(value) if isinstance(value, int | float) else None


def jobs_from_evaluations(
    evaluations: Iterable[VideoEvaluation],
    root_spec: str,
    *,
    keyframe_increase_rate: float = DEFAULT_KEYFRAME_INCREASE_RATE,
    base_fps: float = DEFAULT_BASE_FPS,
    scale_algorithm: str = DEFAULT_SCALE_ALGORITHM,
    encoder: str = "",
    baseline_quality: float | None = None,
    config: ClassifierConfig | None = None,
) -> list[EncodeJob]:
    """인코딩 대상 판정만 골라 계획 입력을 만듦. 파일을 읽지 못하면 경고 후 제외

    - scale_algorithm은 설정의 축소 보간 방식이며, 축소 대상의 scale 필터에 그대로 넣음
    - encoder는 배치 인코더이며, 보정 목표 비트레이트와 최대 비트레이트를 이 인코더 계수로 계산
    - baseline_quality는 설정의 화질 기준값이며, 품질값 자동 조정 상한의 기준. 모르면 None
    - config를 주면 설정의 품질값 정밀 결정 적용 범위로 영상별 대상 여부를 정해 기록에 남김
    """
    jobs: list[EncodeJob] = []
    for ev in evaluations:
        if not ev.is_encode_target:
            continue
        logical = ev.video.filename
        source = resolve_source_filepath(root_spec, logical)
        root, _relative = split_logical_filename(root_spec, logical)
        try:
            stat = os.stat(source)
            size, mtime = stat.st_size, stat.st_mtime_ns
        except OSError as error:
            # 배치에서 빠지면 이유가 남지 않으므로 항목은 남기고, 실행 때 원본 확인에서 건너뜀으로 기록되게 함
            logger.warning("인코딩 대상 원본을 읽지 못해 건너뜀으로 기록합니다: %s (%s)", logical, error)
            size, mtime = 0, 0
        tier = ev.target_tier
        output_size: tuple[int, int] | None = None
        scale_filter: str | None = None
        maxrate: int | None = None
        corrected = corrected_target_kbps(ev.target_optimal_kbps, encoder or None)
        # - 축소 비교의 원본 옵션은 크롭을 적용한 화소 크기를 씀. 잘라내기 확정 영상은 활성 화면 크기
        frame = ev.active_frame
        source_width = ev.video.width if frame is None else frame.width
        source_height = ev.video.height if frame is None else frame.height
        if ev.is_downscaled and ev.target_width is not None and ev.target_height is not None and tier is not None:
            output_size = (ev.target_width, ev.target_height)
            scale_filter = build_scale_expression(tier, scale_algorithm)
            maxrate = general_maxrate_kbps(corrected, tier.max_bitrate_kbps) or None
        jobs.append(
            EncodeJob(
                filename=logical,
                root_dir=root,
                source_path=source,
                source_size=size,
                source_mtime_ns=mtime,
                source_duration_sec=ev.video.duration,
                source_ext=os.path.splitext(source)[1].lstrip(".").lower(),
                fps=ev.video.fps,
                quality=ev.applied_cq,
                purpose=purpose_for_evaluation(
                    ev.smart_action,
                    ev.is_keyframe_exceeded,
                    manual_active_frame_applied=ev.manual_active_frame_applied,
                ),
                output_size=output_size,
                scale_filter=scale_filter,
                maxrate_kbps=maxrate,
                corrected_target_kbps=corrected,
                expected_after_mb=ev.expected_after_mb,
                expected_delta_mb=ev.expected_delta_mb,
                keyframe_increase_ratio=calc_keyframe_increase_ratio(
                    ev.video.keyframe_interval,
                    keyframe_increase_rate,
                    fps=ev.video.fps,
                    base_fps=base_fps,
                    source_ratio=ev.bitrate_ratio,
                ),
                fallback_video_mb=estimate_video_stream_mb(ev.video),
                crop_filter=None if ev.active_frame is None else ev.active_frame.crop_filter_expr(),
                baseline_quality=baseline_quality,
                precise_tune=(
                    config is not None
                    and should_precise_tune(ev, config.encode.precise_quality_mode, config)
                ),
                source_width=source_width,
                source_height=source_height,
                original_target_kbps=(
                    corrected_target_kbps(ev.original_target_optimal_kbps, encoder or None)
                    if ev.original_target_optimal_kbps > 0
                    else None
                ),
                manual_downscale=ev.manual_target is not None,
            )
        )
    return jobs


def create_batch(history: EncodeHistory, settings: EncodeSettings, root_dir: str, jobs: Sequence[EncodeJob]) -> int:
    """설정 사본과 대상 항목으로 새 배치를 기록하고 배치 번호를 반환"""
    items = [
        NewItem(
            source_path=job.source_path,
            source_size=job.source_size,
            source_mtime_ns=job.source_mtime_ns,
            plan_json=job_to_json(job),
        )
        for job in jobs
    ]
    return history.create_batch(root_dir, settings.to_json(), items)


def resolve_workers(settings: EncodeSettings, cpu_count: int) -> int:
    """배치의 동시 실행 수. 설정 값과 인코더 종류, 논리 코어 수로 정함"""
    encoder_spec = get_encoder_spec(settings.encoder)
    return resolve_concurrency(
        settings.concurrent_jobs,
        is_hardware=encoder_spec is not None and encoder_spec.is_nvenc,
        cpu_count=cpu_count,
    )
