import itertools
import statistics
from typing import Any

from src.core.models import BYTES_PER_MB, VideoProps

DEFAULT_FPS: float = 30.0
DEFAULT_FPS_RATIO: str = "30/1"
KEYFRAME_PROBE_WINDOWS_SEC: tuple[float, ...] = (2.5, 5.5, 10.0)
SHORT_SMALL_FOOTAGE_DURATION_SEC: float = 1.0
BITS_PER_KBIT: int = 1000
BITS_PER_BYTE: int = 8
MIN_VIDEO_BITRATE_SHARE: float = 0.85
MIN_VIDEO_KBPS: int = 1
MIN_DIMENSION_PX: int = 1
HALF_CIRCLE_DEG: int = 180


def safe_dict(target: dict[str, Any] | None, key: str, default: Any = None) -> Any:
    """
    - 딕셔너리 객체에서 지정한 키를 조회.
    - 대상이 딕셔너리가 아니거나 해당 키의 값이 None인 경우에는 기본값을 반환.
    """
    if isinstance(target, dict) and key in target and target[key] is not None:
        return target[key]
    return default


def safe_float(raw: Any, default: float = 0.0) -> float:
    """임의 값을 실수로 안전하게 파싱하며 실패 시 기본값을 반환."""
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default


def safe_int(raw: Any, default: int = 0) -> int:
    """임의 값을 정수로 안전하게 파싱하며 실패 시 기본값을 반환."""
    if raw is None or raw == "":
        return default
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return default


def estimate_video_bps(total_bps: int, aud_bps: int) -> int:
    """컨테이너 총 비트레이트에서 오디오 비중을 제외하고 최소 비디오 비중(85%)을 보장."""
    if total_bps <= 0:
        return 0
    return max(total_bps - aud_bps, int(total_bps * MIN_VIDEO_BITRATE_SHARE))


def parse_fps(fps_str: Any) -> float:
    """
    - ffprobe의 분수형 프레임레이트 문자열(예: '30000/1001', '60/1')을 실수(float)로 변환.
    """
    if not fps_str or not isinstance(fps_str, str):
        return DEFAULT_FPS
    try:
        if "/" in fps_str:
            num_str, den_str = fps_str.split("/", 1)
            num, den = float(num_str), float(den_str)
            if den <= 0 or num <= 0:
                return DEFAULT_FPS
            return num / den
        val = float(fps_str)
        return val if val > 0 else DEFAULT_FPS
    except (ValueError, ZeroDivisionError):
        return DEFAULT_FPS


def get_rotate_type(vid_stream: dict[str, Any] | None) -> int:
    """
    - 비디오 스트림의 side_data_list(Display Matrix) 또는 tags 태그로부터 회전 각도를 추출함.
    - 시계 방향 회전 메타데이터가 감지되면 정수 각도(예: 90, 180, 270)를 반환하고, 없으면 0을 반환.
    """
    if not vid_stream:
        return 0

    side_data_list = safe_dict(vid_stream, "side_data_list", None)
    if isinstance(side_data_list, list):
        for dat in side_data_list:
            if isinstance(dat, dict) and "rotation" in dat:
                try:
                    return int(float(dat["rotation"]))
                except (ValueError, TypeError):
                    pass

    tags = safe_dict(vid_stream, "tags", None)
    if isinstance(tags, dict) and "rotate" in tags:
        try:
            return int(float(tags["rotate"]))
        except (ValueError, TypeError):
            pass

    return 0


def parse_mean_keyframe_interval(probe: dict[str, Any]) -> float | None:
    """ffprobe 프레임 출력에서 키프레임 간격들의 산술 평균(초)을 산출하며 표본 부족 시 None을 반환."""
    keyframes: list[float] = []
    for frame in probe.get("frames", []):
        ts = frame.get("pts_time") or frame.get("best_effort_timestamp_time")
        if ts is not None and ts != "N/A":
            try:
                keyframes.append(float(ts))
            except (ValueError, TypeError):
                continue

    if len(keyframes) >= 2:
        intervals: list[float] = [
            j - i for i, j in itertools.pairwise(keyframes) if j > i
        ]
        if intervals:
            return statistics.mean(intervals)
    return None


def build_video_props(
    filename: str,
    filesize_bytes: int,
    probe: dict[str, Any],
) -> VideoProps:
    """
    - ffprobe JSON 결과와 파일 크기로부터 VideoProps 메타데이터 객체를 생성.
    - 앨범 커버 이미지(attached_pic) 스트림을 제외하고, 컨테이너 비트레이트 역산 및 회전 보정을 수행.
    """
    streams: list[dict[str, Any]] = probe.get("streams", [])
    fmt: dict[str, Any] = probe.get("format", {})

    vid_stream: dict[str, Any] | None = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and safe_dict(s.get("disposition"), "attached_pic", 0) != 1
        ),
        None,
    )
    if vid_stream is None:
        vid_stream = next(
            (s for s in streams if s.get("codec_type") == "video"), None
        )

    aud_stream: dict[str, Any] | None = next(
        (s for s in streams if s.get("codec_type") == "audio"), None
    )

    vid_size_mb: float = filesize_bytes / BYTES_PER_MB

    raw_duration = (
        safe_dict(vid_stream, "duration", None)
        or safe_dict(fmt, "duration", None)
        or 0.0
    )
    duration: float = max(safe_float(raw_duration, 0.0), 0.0)

    aud_bps: int = safe_int(safe_dict(aud_stream, "bit_rate", 0), 0)
    aud_kbps: int = round(aud_bps / BITS_PER_KBIT)

    vid_bps: int = safe_int(safe_dict(vid_stream, "bit_rate", None), 0)
    if vid_bps <= 0:
        fmt_bps = safe_int(safe_dict(fmt, "bit_rate", None), 0)
        vid_bps = estimate_video_bps(fmt_bps, aud_bps)

    if vid_bps <= 0 and duration > 0:
        total_bps = int((filesize_bytes * BITS_PER_BYTE) / duration)
        vid_bps = estimate_video_bps(total_bps, aud_bps)

    vid_kbps: int = max(round(vid_bps / BITS_PER_KBIT), MIN_VIDEO_KBPS)

    raw_w: int = safe_int(safe_dict(vid_stream, "width", 0), 0)
    raw_h: int = safe_int(safe_dict(vid_stream, "height", 0), 0)
    rotate_type: int = get_rotate_type(vid_stream)

    if abs(rotate_type) % HALF_CIRCLE_DEG != 0 and raw_w > 0 and raw_h > 0:
        width, height = raw_h, raw_w
    else:
        width, height = max(raw_w, MIN_DIMENSION_PX), max(raw_h, MIN_DIMENSION_PX)

    fps_str = (
        safe_dict(vid_stream, "avg_frame_rate", None)
        if safe_dict(vid_stream, "avg_frame_rate", "0/0") not in ("0/0", "0", None)
        else safe_dict(vid_stream, "r_frame_rate", DEFAULT_FPS_RATIO)
    )
    fps: float = parse_fps(fps_str)
    codec: str = str(safe_dict(vid_stream, "codec_name", "unknown"))

    return VideoProps(
        filename=filename,
        width=width,
        height=height,
        rotate_type=rotate_type,
        fps=fps,
        vid_kbps=vid_kbps,
        aud_kbps=aud_kbps,
        vid_size_MB=vid_size_mb,
        duration=duration,
        codec=codec,
        keyframe_interval=None,
    )
