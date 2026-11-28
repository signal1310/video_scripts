"""표에서 보여 줄 예정 FFmpeg 명령 계산 모듈

- 실제 배치가 쓰는 계획 입력(`jobs_from_evaluations`)과 계획 빌더(`build_encode_plan`)를 그대로 재사용해 명령이 어긋나지 않게 함
- FFmpeg, ffprobe를 실행하지 않고 파일 존재 확인과 통계 조회만 함(화면 스레드에서 호출 가능)
- 스트림 구성 요약이 없는 영상은 주 영상 한 개로 근사하고, 그 사실을 결과에 표시
- 인코딩 대상이 아닌 보존, 재분석 필요 행은 예정 명령을 만들지 않음(배치가 그 행을 인코딩하지 않으므로)
"""

import os
from dataclasses import dataclass

from src.core.config import ClassifierConfig
from src.core.encode.encode_impact import parse_stream_summary_layout
from src.core.encode.encode_paths import part_path_of, resolve_output_path
from src.core.encode.encode_plan import EncodePlan, build_encode_plan, choose_container
from src.core.encode.ffmpeg_command import (
    build_ffmpeg_args,
    format_command_line,
    resolve_ffmpeg_executable,
    summarize_video_args,
)
from src.core.encode.stream_layout import KIND_VIDEO, StreamInfo, StreamLayout
from src.core.encode_tag import ENCODE_TAG_MODE_VISIBLE
from src.core.encoder_specs import get_encode_codec_family
from src.core.models import VideoEvaluation
from src.service.encode.encode_job import EncodeJob, EncodeSettings, jobs_from_evaluations
from src.service.encode.encode_reasons import NAME_EXISTS

FALLBACK_VIDEO_INDEX: int = 0


@dataclass(frozen=True, slots=True)
class PlannedCommand:
    """표에 보여 줄 예정 명령. approximate는 스트림 구성을 추정했다는 뜻

    - 명령을 만들지 않는 사유가 있으면 skip_reason에 두고 command_line과 summary를 비움
    """

    command_line: str
    summary: str
    approximate: bool
    skip_reason: str = ""

    @property
    def display_text(self) -> str:
        """인자 열에 보여 줄 한 줄. 명령이 없으면 사유"""
        return self.summary or self.skip_reason


def _layout_for_evaluation(ev: VideoEvaluation) -> tuple[StreamLayout, bool]:
    """저장된 스트림 요약으로 스트림 구성을 복원. 요약이 없으면 주 영상 하나로 근사"""
    layout = parse_stream_summary_layout(ev.video.stream_summary)
    if layout is not None:
        return layout, False
    fallback = StreamLayout(
        streams=(StreamInfo(index=FALLBACK_VIDEO_INDEX, kind=KIND_VIDEO, codec_name=ev.video.codec.lower()),)
    )
    return fallback, True


def _plan_for_job(
    job: EncodeJob, settings: EncodeSettings, layout: StreamLayout
) -> EncodePlan | None:
    """배치가 쓸 결과 경로와 계획을 같은 정책으로 계산. 같은 이름 충돌은 실제 존재 여부로 판단

    - 같은 이름 건너뜀처럼 쓸 수 있는 결과 경로가 없으면 None(배치도 그 항목을 건너뜀)
    - 별도 폴더에 저장 + 덮어쓰기는 기존 결과 파일이 있어도 대상 경로를 유지(배치가 교체함)
    """
    container = choose_container(
        job.source_ext, layout, settings.container_policy, get_encode_codec_family(settings.encoder)
    )
    final = resolve_output_path(
        source_path=job.source_path,
        root_dir=job.root_dir,
        output_dirname=settings.output_dirname,
        container=container,
        name_conflict=settings.name_conflict,
        result_policy=settings.result_policy,
        encoder=settings.encoder,
        quality=job.quality,
        exists=os.path.exists,
    )
    if final is None:
        return None
    keywords = (settings.encode_tag_name,) if settings.encode_tag_mode == ENCODE_TAG_MODE_VISIBLE else ()
    return build_encode_plan(
        source_path=job.source_path,
        part_path=part_path_of(final),
        output_path=final,
        source_ext=job.source_ext,
        encoder=settings.encoder,
        quality=job.quality,
        fps=job.fps,
        layout=layout,
        container_policy=settings.container_policy,
        tag_value="",
        output_size=job.output_size,
        scale_filter=job.scale_filter,
        crop_filter=job.crop_filter,
        maxrate_kbps=job.maxrate_kbps,
        tag_mode=settings.encode_tag_mode,
        tag_name=settings.encode_tag_name,
        tag_keywords=keywords,
    )


def planned_command_for_evaluation(
    ev: VideoEvaluation, config: ClassifierConfig, *, root_dir: str = ""
) -> PlannedCommand | None:
    """영상 한 개의 예정 FFmpeg 명령과 인자 요약. 계획 자체를 만들 수 없거나 인코딩 대상이 아니면 None

    - 인코딩 대상이 아닌 행은 배치가 인코딩하지 않으므로 예정 명령을 만들지 않음
    - 스트림 구성 요약이 없으면 주 영상 한 개로 근사하고 approximate를 참으로 표시
    - 결과 경로를 쓸 수 없어 배치가 건너뛸 항목은 빈 명령과 skip_reason을 돌려줌
    """
    if not ev.is_encode_target:
        return None
    settings = EncodeSettings.from_config(config)
    jobs = jobs_from_evaluations(
        [ev],
        root_dir,
        keyframe_increase_rate=config.thresholds.keyframe_1s_increase_rate,
        base_fps=config.bitrate_model.base_fps,
        scale_algorithm=config.encode.scale_algorithm,
        encoder=settings.encoder,
    )
    if not jobs:
        return None
    layout, approximate = _layout_for_evaluation(ev)
    try:
        plan = _plan_for_job(jobs[0], settings, layout)
    except ValueError:
        return None
    if plan is None:
        return PlannedCommand(command_line="", summary="", approximate=approximate, skip_reason=NAME_EXISTS)
    args = build_ffmpeg_args(plan, resolve_ffmpeg_executable(settings.ffmpeg_path))
    return PlannedCommand(
        command_line=format_command_line(args),
        summary=summarize_video_args(plan),
        approximate=approximate,
    )


def plan_approximation_note(planned: PlannedCommand) -> str:
    """근사한 계획임을 알리는 꼬리 문구, 정확한 계획이면 빈 문자열"""
    return " (스트림 구성을 추정한 예정 명령)" if planned.approximate else ""
