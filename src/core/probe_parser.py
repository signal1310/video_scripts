"""ffprobe JSON 응답에서 스트림, 회전, 비트레이트, 키프레임 정보 추출 파서 모듈"""

import itertools
import math
import statistics
from typing import Any

from src.core.models import BYTES_PER_MB, VideoProps

DEFAULT_FPS: float = 30.0
DEFAULT_FPS_RATIO: str = "30/1"
KEYFRAME_PROBE_WINDOWS_SEC: tuple[float, ...] = (2.5, 5.5, 10.0)
KEYFRAME_PACKET_FLAG: str = "K"
SHORT_SMALL_FOOTAGE_DURATION_SEC: float = 1.0
BITS_PER_KBIT: int = 1000
BITS_PER_BYTE: int = 8
MIN_VIDEO_BITRATE_SHARE: float = 0.85
MIN_VIDEO_KBPS: int = 1
BITRATE_TAG_KEYS: tuple[str, ...] = ("BPS", "BPS-eng")
HDR_COLOR_TRANSFERS: frozenset[str] = frozenset({"smpte2084", "arib-std-b67"})
DOVI_SIDE_DATA_MARKER: str = "dovi"
MIN_DIMENSION_PX: int = 1
HALF_CIRCLE_DEG: int = 180
FULL_CIRCLE_DEG: int = 360


def safe_dict(target: dict[str, Any] | None, key: str, default: Any = None) -> Any:
    """딕셔너리에서 지정한 키를 안전하게 조회

    대상이 딕셔너리가 아니거나 값이 None이면 기본값 반환
    """
    if isinstance(target, dict) and key in target and target[key] is not None:
        return target[key]
    return default


def safe_float(raw: Any, default: float = 0.0) -> float:
    """임의 값을 실수로 안전하게 파싱하며 실패 시 기본값을 반환"""
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default


def safe_int(raw: Any, default: int = 0) -> int:
    """임의 값을 정수로 안전하게 파싱하며 실패 시 기본값을 반환"""
    if raw is None or raw == "":
        return default
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return default


def parse_stream_bps(stream: dict[str, Any] | None) -> int | None:
    """스트림 비트레이트를 bps로 반환하며 bit_rate와 MKV BPS 태그 순으로 확인하고, 측정값이 없으면 None 반환"""
    candidates: list[Any] = [safe_dict(stream, "bit_rate", None)]
    tags = safe_dict(stream, "tags", None)
    if isinstance(tags, dict):
        candidates.extend(tags.get(key) for key in BITRATE_TAG_KEYS)
    for raw in candidates:
        bps = safe_int(raw, 0)
        if bps > 0:
            return bps
    return None


def estimate_video_bps(total_bps: int, aud_bps: int, *, audio_known: bool) -> int:
    """컨테이너 총 비트레이트에서 오디오 합을 제외해 비디오 비트레이트 산출

    모든 오디오 비트레이트를 알면 차감값을 그대로 사용하고, 일부라도 모르면 최소 비디오 비중(85%)을 하한으로 둠
    """
    if total_bps <= 0:
        return 0
    remainder = total_bps - aud_bps
    if audio_known:
        return max(remainder, 0)
    return max(remainder, int(total_bps * MIN_VIDEO_BITRATE_SHARE))


def is_hdr_stream(stream: dict[str, Any] | None) -> bool:
    """전달 함수(PQ, HLG)나 Dolby Vision 구성 레코드가 있으면 HDR로 판정"""
    if stream is None:
        return False
    transfer = str(safe_dict(stream, "color_transfer", "")).lower()
    if transfer in HDR_COLOR_TRANSFERS:
        return True
    side_data_list = safe_dict(stream, "side_data_list", None)
    if not isinstance(side_data_list, list):
        return False
    return any(
        isinstance(dat, dict)
        and DOVI_SIDE_DATA_MARKER in str(dat.get("side_data_type", "")).lower()
        for dat in side_data_list
    )


def parse_fps(fps_str: Any) -> float:
    """ffprobe의 분수형 프레임레이트 문자열(예: '30000/1001', '60/1')을 실수(float)로 변환"""
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


def _parse_rotation_entry(
    raw_deg: Any, source_prefix: str, *, invert_ccw: bool
) -> tuple[int, str] | None:
    """원시 회전 각도를 [0, 360) 시계 방향 정수와 메타데이터 출처 문자열로 파싱"""
    try:
        val = float(raw_deg)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(val):
        return None
    deg = round(val)
    raw_label = f"{int(val)}°" if val.is_integer() else f"{val:g}°"
    cw_deg = (-deg if invert_ccw else deg) % FULL_CIRCLE_DEG
    return cw_deg, f"{source_prefix} {raw_label}"


def extract_rotate_info(vid_stream: dict[str, Any] | None) -> tuple[int, str | None]:
    """비디오 스트림의 회전 메타데이터를 (시계 방향 정규화 각도, 원시 메타 출처) 쌍으로 반환

    Display Matrix는 FFmpeg 규약상 반시계(CCW) 부호이므로 부호를 반전하고,
    tags.rotate(대소문자 무관)는 시계 방향(CW) 기준으로 [0, 360) 정규화
    """
    if not vid_stream:
        return 0, None

    side_data_list = safe_dict(vid_stream, "side_data_list", None)
    if isinstance(side_data_list, list):
        for dat in side_data_list:
            if isinstance(dat, dict) and "rotation" in dat:
                parsed = _parse_rotation_entry(
                    dat["rotation"], "Display Matrix", invert_ccw=True
                )
                if parsed is not None:
                    return parsed

    tags = safe_dict(vid_stream, "tags", None)
    if isinstance(tags, dict):
        for key, raw_val in tags.items():
            if str(key).lower() == "rotate":
                parsed = _parse_rotation_entry(
                    raw_val, "Stream Tag", invert_ccw=False
                )
                if parsed is not None:
                    return parsed

    return 0, None


def get_rotate_type(vid_stream: dict[str, Any] | None) -> int:
    """비디오 스트림의 메타데이터를 컨테이너 무관 시계 방향 각도(0, 90, 180, 270)로 정규화"""
    rotate_deg, _ = extract_rotate_info(vid_stream)
    return rotate_deg


def _parse_packet_time(raw: Any) -> float | None:
    """패킷 타임스탬프 문자열을 실수로 변환하며 N/A나 무효 값은 None 반환"""
    if raw is None or raw == "N/A":
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def parse_mean_keyframe_interval(probe: dict[str, Any]) -> float | None:
    """ffprobe 패킷 출력에서 키프레임 패킷의 간격 산술 평균(초)을 산출하며 표본 부족 시 None 반환

    - 패킷은 디코드 순서로 출력되어 B-프레임이 있으면 pts가 뒤섞이므로 정렬 후 간격 계산
    - pts가 없는 키프레임 패킷은 dts로 대체
    """
    keyframes: list[float] = []
    for packet in probe.get("packets", []):
        flags = packet.get("flags")
        if not isinstance(flags, str) or KEYFRAME_PACKET_FLAG not in flags:
            continue
        ts = _parse_packet_time(packet.get("pts_time"))
        if ts is None:
            ts = _parse_packet_time(packet.get("dts_time"))
        if ts is not None:
            keyframes.append(ts)

    keyframes.sort()
    intervals: list[float] = [j - i for i, j in itertools.pairwise(keyframes) if j > i]
    if intervals:
        return statistics.mean(intervals)
    return None


def build_video_props(
    filename: str,
    filesize_bytes: int,
    probe: dict[str, Any],
) -> VideoProps:
    """ffprobe JSON 결과와 파일 크기로 VideoProps 객체 생성

    앨범 커버 스트림 제외, 컨테이너 비트레이트 역산 및 회전 보정 수행
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

    aud_values: list[int | None] = [
        parse_stream_bps(s) for s in streams if s.get("codec_type") == "audio"
    ]
    audio_known: bool = all(value is not None for value in aud_values)
    aud_bps: int = sum(value for value in aud_values if value is not None)
    aud_kbps: int = round(aud_bps / BITS_PER_KBIT)

    vid_size_mb: float = filesize_bytes / BYTES_PER_MB

    raw_duration = (
        safe_dict(vid_stream, "duration", None)
        or safe_dict(fmt, "duration", None)
        or 0.0
    )
    duration: float = max(safe_float(raw_duration, 0.0), 0.0)

    vid_bps: int = parse_stream_bps(vid_stream) or 0
    if vid_bps <= 0:
        fmt_bps = safe_int(safe_dict(fmt, "bit_rate", None), 0)
        vid_bps = estimate_video_bps(fmt_bps, aud_bps, audio_known=audio_known)

    if vid_bps <= 0 and duration > 0:
        total_bps = int((filesize_bytes * BITS_PER_BYTE) / duration)
        vid_bps = estimate_video_bps(total_bps, aud_bps, audio_known=audio_known)

    vid_kbps: int = max(round(vid_bps / BITS_PER_KBIT), MIN_VIDEO_KBPS)

    raw_w: int = safe_int(safe_dict(vid_stream, "width", 0), 0)
    raw_h: int = safe_int(safe_dict(vid_stream, "height", 0), 0)
    rotate_type, rotate_source = extract_rotate_info(vid_stream)

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
        rotate_source=rotate_source,
        is_hdr=is_hdr_stream(vid_stream),
    )
