"""인코딩 후 예상 파일 크기 산출 모듈

영상 스트림 용량만 출력 코덱 기준 비트레이트 비율 및 코덱 전환 계수로 조정하고, 키프레임 간격에 따른 증가분을 더함
오디오와 컨테이너 용량은 인코딩 전후가 같다고 가정
"""

from src.core.models import BYTES_PER_MB, VideoProps
from src.core.probe_parser import BITS_PER_BYTE, BITS_PER_KBIT

KF_CLAMP_MIN_SEC: float = 1.0
KF_CLAMP_MAX_SEC: float = 10.0
DEFAULT_UNKNOWN_KF_SEC: float = 5.0
DEFAULT_BASE_FPS: float = 30.0
# - FPS 보정과 정적 화면 가중치는 미실측 추정치로, 샘플 인코딩 기반 보정(TODO.md 2절) 전까지 예상 용량에만 반영
FPS_KF_CORRECTION_EXPONENT: float = 0.5
FPS_KF_FACTOR_MIN: float = 0.5
FPS_KF_FACTOR_MAX: float = 1.5
STATIC_SCREEN_RATIO_THRESHOLD: float = 0.35
STATIC_SCREEN_RATIO_FLOOR: float = 0.15


def _stream_mb_at_kbps(kbps: float, duration: float) -> float:
    """비트레이트(kbps)와 길이(초)로 영상 스트림 용량(MB) 산출"""
    stream_bytes = kbps * BITS_PER_KBIT * duration / BITS_PER_BYTE
    return stream_bytes / BYTES_PER_MB


def estimate_video_stream_mb(vid: VideoProps) -> float:
    """비디오 비트레이트 기준 영상 스트림 용량(MB) 산출, 파일 전체 용량을 넘지 않도록 상한 적용

    길이를 알 수 없으면 파일 전체를 영상 스트림으로 간주
    """
    orig_mb = vid.vid_size_MB
    if vid.duration <= 0:
        return orig_mb
    return min(orig_mb, _stream_mb_at_kbps(vid.vid_kbps, vid.duration))


def calc_keyframe_increase_ratio(
    keyframe_interval: float | None,
    increase_rate: float,
    *,
    fps: float = DEFAULT_BASE_FPS,
    base_fps: float = DEFAULT_BASE_FPS,
    source_ratio: float | None = None,
) -> float:
    """1초 키프레임 재인코딩으로 늘어나는 용량 비율 산출

    - 간격은 1.0~10.0초로 클램핑해 극단값이 결과를 왜곡하지 않도록 함
    - 간격 미측정 시 보수적 기본값(5초) 사용
    - FPS 보정((base_fps / fps)^0.5, 0.5~1.5 클램핑)과 정적 화면(r_quality < 0.35) 가중치 반영
    """
    interval = DEFAULT_UNKNOWN_KF_SEC if keyframe_interval is None else keyframe_interval
    clamped = min(max(interval, KF_CLAMP_MIN_SEC), KF_CLAMP_MAX_SEC)
    base_inc = increase_rate * (1.0 - 1.0 / clamped)
    if base_inc <= 0.0:
        return 0.0

    if fps > 0 and base_fps > 0:
        raw_fps_factor = (base_fps / fps) ** FPS_KF_CORRECTION_EXPONENT
        fps_factor = min(max(raw_fps_factor, FPS_KF_FACTOR_MIN), FPS_KF_FACTOR_MAX)
    else:
        fps_factor = 1.0

    static_factor = (
        STATIC_SCREEN_RATIO_THRESHOLD / max(source_ratio, STATIC_SCREEN_RATIO_FLOOR)
        if source_ratio is not None and source_ratio < STATIC_SCREEN_RATIO_THRESHOLD
        else 1.0
    )
    return base_inc * fps_factor * static_factor


def estimate_after_size_mb(
    vid: VideoProps,
    output_ratio: float,
    keyframe_increase_rate: float,
    *,
    cap_at_original: bool,
    source_ratio: float | None = None,
    codec_weight_ratio: float = 1.0,
    base_fps: float = DEFAULT_BASE_FPS,
    max_video_kbps: float | None = None,
) -> float:
    """출력 코덱 기준 비트레이트 비율(r_out)과 코덱 전환 비율로 예상 인코딩 후 파일 크기(MB) 산출

    - other: 오디오와 컨테이너 등 인코딩으로 줄지 않는 용량
    - C_src >= C_target(codec_weight_ratio <= 1.0): min(1, 1 / r_out, C_target / C_src) 적용
    - C_src < C_target이고 r_out < 1.0(코덱 역전): min(1 / r_out, C_target / C_src) 적용
    - cap_at_original: 과화질 재인코딩은 원본보다 커지지 않는 것으로 제한
    - max_video_kbps: 최대 비트레이트(-maxrate)로 영상 스트림 비트레이트 상한 적용, 키프레임 증가분 포함 후 적용
    """
    orig_mb = vid.vid_size_MB
    stream_mb = estimate_video_stream_mb(vid)
    other_mb = orig_mb - stream_mb
    inv_output = (1.0 / output_ratio) if output_ratio > 0 else 1.0
    if codec_weight_ratio > 1.0 and output_ratio < 1.0:
        bitrate_factor = min(inv_output, codec_weight_ratio)
    else:
        bitrate_factor = min(1.0, inv_output, codec_weight_ratio)
    keyframe_factor = 1.0 + calc_keyframe_increase_ratio(
        vid.keyframe_interval,
        keyframe_increase_rate,
        fps=vid.fps,
        base_fps=base_fps,
        source_ratio=source_ratio,
    )
    stream_after_mb = stream_mb * bitrate_factor * keyframe_factor
    if max_video_kbps is not None and vid.duration > 0:
        stream_after_mb = min(
            stream_after_mb, _stream_mb_at_kbps(max_video_kbps, vid.duration)
        )
    after_mb = other_mb + stream_after_mb
    return min(after_mb, orig_mb) if cap_at_original else after_mb
