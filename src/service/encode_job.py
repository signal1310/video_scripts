"""재인코딩 작업 입력: 설정 사본, 영상별 계획 입력, 작업 기록용 직렬화와 배치 생성

- 배치 시작 시 만든 EncodeSettings와 EncodeJob은 배치가 끝날 때까지 바뀌지 않음
"""

import dataclasses
import json
import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from src.core.config import ClassifierConfig, ExecutionConfig
from src.core.encode.encode_gate import GatePurpose, purpose_for_action
from src.core.models import VideoEvaluation
from src.core.resolution_tiers import build_scale_expression
from src.core.workspace_paths import resolve_source_filepath, split_logical_filename
from src.service.encode_history import EncodeHistory, NewItem

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
    )


def jobs_from_evaluations(
    evaluations: Iterable[VideoEvaluation], root_spec: str
) -> list[EncodeJob]:
    """인코딩 대상 판정만 골라 계획 입력을 만듦. 파일을 읽지 못하면 경고 후 제외"""
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
            logger.warning("재인코딩 대상 원본을 읽지 못해 건너뜀으로 기록합니다: %s (%s)", logical, error)
            size, mtime = 0, 0
        tier = ev.target_tier
        output_size: tuple[int, int] | None = None
        scale_filter: str | None = None
        maxrate: int | None = None
        if ev.is_downscaled and ev.target_width is not None and ev.target_height is not None and tier is not None:
            output_size = (ev.target_width, ev.target_height)
            scale_filter = build_scale_expression(tier)
            maxrate = tier.max_bitrate_kbps
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
                purpose=purpose_for_action(ev.smart_action),
                output_size=output_size,
                scale_filter=scale_filter,
                maxrate_kbps=maxrate,
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
