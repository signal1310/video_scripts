"""재인코딩 계획(EncodePlan)을 FFmpeg 인자 목록으로 변환하는 순수 함수 모듈

- 프로세스 실행은 담당하지 않음(encode_process 모듈 책임)
- 출력은 항상 `.part` 임시 파일에 `-f`로 형식을 명시해 쓰며, 기존 파일은 덮어쓰지 않음(-n)
"""

from src.core.encode.encode_plan import (
    ACTION_COPY,
    ACTION_DROP,
    ACTION_ENCODE,
    ACTION_TRANSCODE_AAC,
    ACTION_TRANSCODE_MOV_TEXT,
    CONTAINER_MKV,
    DecodeMode,
    EncodePlan,
)
from src.core.encode.stream_layout import KIND_ATTACHMENT, KIND_AUDIO, KIND_SUBTITLE, KIND_VIDEO
from src.core.encoder_specs import get_encoder_spec
from src.core.quality_target import to_encoder_quality

FFMPEG_EXECUTABLE_DEFAULT: str = "ffmpeg"
HW_DECODE_SOURCE_CODECS: frozenset[str] = frozenset({"hevc", "av1"})
THREADS_ARG: str = "0"
THREAD_TYPE_ARG: str = "frame"
METADATA_KEY_MP4: str = "video_scripts_encode"
METADATA_KEY_MKV: str = "VIDEO_SCRIPTS_ENCODE"
BUFSIZE_MULTIPLIER: int = 2
# - 진행 값은 stdout으로 기계 판독 형식을 내보내고, 사람이 읽는 통계 줄은 끔
PROGRESS_ARGS: tuple[str, ...] = ("-progress", "pipe:1", "-nostats")
CONTAINER_FORMAT_NAMES: dict[str, str] = {"mp4": "mp4", "mkv": "matroska", "mov": "mov"}
# - 샤나 동치 기준 HEVC 출력은 mp4/mov에서 hvc1 태그를 써야 재생기가 인식(결정 27)
HEVC_MP4_TAG: str = "hvc1"


def _decode_args(plan: EncodePlan) -> list[str]:
    """입력 디코딩 옵션. 샤나 기본 구성은 HEVC와 AV1만 하드웨어 자동 디코딩, CPU 전용은 하드웨어 디코딩을 쓰지 않음"""
    args = ["-threads", THREADS_ARG, "-thread_type", THREAD_TYPE_ARG]
    if plan.decode_mode is DecodeMode.SHANA_DEFAULT and plan.source_codec.lower() in HW_DECODE_SOURCE_CODECS:
        args += ["-hwaccel", "auto"]
    return args


def _quality_text(plan: EncodePlan, integer_scale: bool) -> str:
    value = to_encoder_quality(plan.quality, integer_scale=integer_scale)
    return str(int(value)) if integer_scale else f"{value:.1f}"


def _vopt(flag: str, out_index: int) -> str:
    """영상 옵션 이름에 출력 영상 순번을 붙임

    - 순번이 없으면 커버 복사 스트림에도 적용되어 `-level:v auto` 등에서 인코딩이 실패함
    - 이미 영상 지정자가 있는 이름(`-preset:v`)은 순번만 덧붙임
    """
    return f"{flag}:{out_index}" if ":v" in flag else f"{flag}:v:{out_index}"


def _video_encode_args(plan: EncodePlan, out_index: int) -> list[str]:
    spec = get_encoder_spec(plan.encoder)
    if spec is None or spec.quality_option is None:
        raise ValueError(f"인자 구성이 확정되지 않은 인코더입니다: {plan.encoder}")
    args = ["-c:v:" + str(out_index), plan.encoder]
    args += [_vopt(token, out_index) if token.startswith("-") else token for token in spec.base_args]
    if spec.profile is not None:
        args += [_vopt("-profile:v", out_index), spec.profile]
    args += [_vopt(spec.quality_option, out_index), _quality_text(plan, spec.integer_scale)]
    args += [_vopt("-pix_fmt", out_index), plan.pixel_format]
    if plan.scale_filter is not None:
        args += ["-filter:v:" + str(out_index), plan.scale_filter]
    if plan.maxrate_kbps is not None:
        if spec.is_nvenc:
            args += [_vopt("-b:v", out_index), "0"]
        args += [
            _vopt("-maxrate", out_index),
            f"{plan.maxrate_kbps}k",
            _vopt("-bufsize", out_index),
            f"{plan.maxrate_kbps * BUFSIZE_MULTIPLIER}k",
        ]
    if spec.family == "hevc" and plan.container != CONTAINER_MKV:
        args += ["-tag:v:" + str(out_index), HEVC_MP4_TAG]
    args += [_vopt("-g", out_index), str(plan.gop_frames)]
    color = plan.color
    for flag, value in (
        ("-color_primaries", color.primaries),
        ("-color_trc", color.transfer),
        ("-colorspace", color.space),
        ("-color_range", color.range),
    ):
        if value:
            args += [_vopt(flag, out_index), value]
    return args


def _metadata_args(plan: EncodePlan) -> list[str]:
    """처리 표식 태그. MP4와 MOV는 use_metadata_tags 플래그가 있어야 기록이 남고, MKV는 대문자 키를 사용"""
    if plan.container == CONTAINER_MKV:
        return ["-metadata", f"{METADATA_KEY_MKV}={plan.tag_value}"]
    return ["-movflags", "+use_metadata_tags", "-metadata", f"{METADATA_KEY_MP4}={plan.tag_value}"]


def resolve_ffmpeg_executable(configured_path: str) -> str:
    """설정 경로를 ffmpeg 실행 파일로 쓰고, 비어 있으면 PATH의 기본 이름을 사용"""
    stripped = configured_path.strip()
    return stripped if stripped else FFMPEG_EXECUTABLE_DEFAULT


def build_ffmpeg_args(plan: EncodePlan, ffmpeg_executable: str = FFMPEG_EXECUTABLE_DEFAULT) -> list[str]:
    """계획을 FFmpeg 인자 목록(실행 파일 포함)으로 변환"""
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
    # - MOV 입력의 타임코드 트랙(데이터 스트림)을 MP4, MOV muxer가 자동으로 되살리지 않도록 끔(결정 8 제외 규칙)
    if plan.container != CONTAINER_MKV:
        args += ["-write_tmcd", "0"]
    args += _metadata_args(plan)
    args += ["-fps_mode", "passthrough"]
    args += ["-f", CONTAINER_FORMAT_NAMES[plan.container], plan.part_path]
    return args
