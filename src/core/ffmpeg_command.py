"""재인코딩 계획(EncodePlan)을 FFmpeg 인자 목록으로 변환하는 순수 함수 모듈

- 프로세스 실행은 담당하지 않음(encode_process 모듈 책임)
- 출력은 항상 `.part` 임시 파일에 `-f`로 형식을 명시해 쓰며, 기존 파일은 덮어쓰지 않음(-n)
"""

from src.core.encode_plan import (
    ACTION_COPY,
    ACTION_DROP,
    ACTION_ENCODE,
    ACTION_TRANSCODE_AAC,
    ACTION_TRANSCODE_MOV_TEXT,
    CONTAINER_MKV,
    KEYFRAME_INTERVAL_SEC,
    DecodeMode,
    EncodePlan,
)
from src.core.encoder_specs import get_encoder_spec
from src.core.quality_target import to_encoder_quality
from src.core.stream_layout import KIND_ATTACHMENT, KIND_AUDIO, KIND_SUBTITLE, KIND_VIDEO

FFMPEG_EXECUTABLE_DEFAULT: str = "ffmpeg"
HW_DECODE_SOURCE_CODECS: frozenset[str] = frozenset({"hevc", "av1"})
THREADS_ARG: str = "0"
THREAD_TYPE_ARG: str = "frame"
FORCE_KEY_FRAMES_EXPR: str = f"expr:gte(t,n_forced*{KEYFRAME_INTERVAL_SEC:g})"
METADATA_KEY_MP4: str = "video_scripts_encode"
METADATA_KEY_MKV: str = "VIDEO_SCRIPTS_ENCODE"
BUFSIZE_MULTIPLIER: int = 2
# - 진행 값은 stdout으로 기계 판독 형식을 내보내고, 사람이 읽는 통계 줄은 끔
PROGRESS_ARGS: tuple[str, ...] = ("-progress", "pipe:1", "-nostats")
CONTAINER_FORMAT_NAMES: dict[str, str] = {"mp4": "mp4", "mkv": "matroska", "mov": "mov"}
# - 샤나 동치 기준 HEVC 출력은 mp4/mov에서 hvc1 태그를 써야 재생기가 인식(결정 27)
HEVC_MP4_TAG: str = "hvc1"


def _decode_args(plan: EncodePlan) -> list[str]:
    """입력 디코딩 옵션. 샤나 기본 구성은 HEVC와 AV1만 하드웨어 자동 디코딩을 사용"""
    if plan.decode_mode is not DecodeMode.SHANA_DEFAULT:
        return []
    args = ["-threads", THREADS_ARG, "-thread_type", THREAD_TYPE_ARG]
    if plan.source_codec.lower() in HW_DECODE_SOURCE_CODECS:
        args += ["-hwaccel", "auto"]
    return args


def _quality_text(plan: EncodePlan, integer_scale: bool) -> str:
    value = to_encoder_quality(plan.quality, integer_scale=integer_scale)
    return str(int(value)) if integer_scale else f"{value:.1f}"


def _video_encode_args(plan: EncodePlan, out_index: int) -> list[str]:
    spec = get_encoder_spec(plan.encoder)
    if spec is None or spec.quality_option is None:
        raise ValueError(f"인자 구성이 확정되지 않은 인코더입니다: {plan.encoder}")
    args = ["-c:v:" + str(out_index), plan.encoder, *spec.base_args]
    if spec.profile is not None:
        args += ["-profile:v", spec.profile]
    args += [spec.quality_option, _quality_text(plan, spec.integer_scale)]
    args += ["-pix_fmt", plan.pixel_format]
    if plan.scale_filter is not None:
        args += ["-filter:v:" + str(out_index), plan.scale_filter]
    if plan.maxrate_kbps is not None:
        if spec.is_nvenc:
            args += ["-b:v", "0"]
        args += ["-maxrate", f"{plan.maxrate_kbps}k", "-bufsize", f"{plan.maxrate_kbps * BUFSIZE_MULTIPLIER}k"]
    if spec.family == "hevc" and plan.container != CONTAINER_MKV:
        args += ["-tag:v:" + str(out_index), HEVC_MP4_TAG]
    args += ["-g", str(plan.gop_frames), "-force_key_frames", FORCE_KEY_FRAMES_EXPR]
    color = plan.color
    for flag, value in (
        ("-color_primaries", color.primaries),
        ("-color_trc", color.transfer),
        ("-colorspace", color.space),
        ("-color_range", color.range),
    ):
        if value:
            args += [flag, value]
    return args


def _metadata_args(plan: EncodePlan) -> list[str]:
    """처리 표식 태그. MP4와 MOV는 use_metadata_tags 플래그가 있어야 기록이 남고, MKV는 대문자 키를 사용"""
    if plan.container == CONTAINER_MKV:
        return ["-metadata", f"{METADATA_KEY_MKV}={plan.tag_value}"]
    return ["-movflags", "+use_metadata_tags", "-metadata", f"{METADATA_KEY_MP4}={plan.tag_value}"]


def build_ffmpeg_args(plan: EncodePlan, ffmpeg_executable: str = FFMPEG_EXECUTABLE_DEFAULT) -> list[str]:
    """계획을 FFmpeg 인자 목록(실행 파일 포함)으로 변환한다"""
    args: list[str] = [
        ffmpeg_executable,
        "-hide_banner",
        "-nostdin",
        "-n",
        *PROGRESS_ARGS,
        *_decode_args(plan),
    ]
    args += ["-i", plan.source_path]

    counters = {KIND_VIDEO: 0, KIND_AUDIO: 0, KIND_SUBTITLE: 0, KIND_ATTACHMENT: 0}
    stream_args: list[str] = []
    for action in plan.streams:
        if action.action == ACTION_DROP:
            continue
        args += ["-map", f"0:{action.index}"]
        out = counters.get(action.kind, 0)
        if action.kind in counters:
            counters[action.kind] = out + 1
        if action.kind == KIND_VIDEO:
            if action.action == ACTION_ENCODE:
                stream_args += _video_encode_args(plan, out)
            else:
                stream_args += [f"-c:v:{out}", ACTION_COPY]
                if action.attached_pic:
                    stream_args += [f"-disposition:v:{out}", "attached_pic"]
        elif action.kind == KIND_AUDIO:
            codec = "aac" if action.action == ACTION_TRANSCODE_AAC else ACTION_COPY
            stream_args += [f"-c:a:{out}", codec]
        elif action.kind == KIND_SUBTITLE:
            codec = "mov_text" if action.action == ACTION_TRANSCODE_MOV_TEXT else ACTION_COPY
            stream_args += [f"-c:s:{out}", codec]
        elif action.kind == KIND_ATTACHMENT:
            stream_args += [f"-c:t:{out}", ACTION_COPY]
    args += stream_args

    args += ["-map_metadata", "0", "-map_chapters", "0"]
    args += _metadata_args(plan)
    args += ["-fps_mode", "passthrough"]
    args += ["-f", CONTAINER_FORMAT_NAMES[plan.container], plan.part_path]
    return args
