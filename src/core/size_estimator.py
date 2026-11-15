"""인코딩 후 예상 파일 크기 산출 모듈

영상 스트림 용량만 출력 코덱 기준 비트레이트 비율로 줄이고, 키프레임 간격에 따른 증가분을 더함
오디오와 컨테이너 용량은 인코딩 전후가 같다고 가정
"""

from src.core.models import BYTES_PER_MB, VideoProps
from src.core.probe_parser import BITS_PER_BYTE, BITS_PER_KBIT

KF_CLAMP_MIN_SEC: float = 1.0
KF_CLAMP_MAX_SEC: float = 10.0
DEFAULT_UNKNOWN_KF_SEC: float = 5.0


def estimate_video_stream_mb(vid: VideoProps) -> float:
    """비디오 비트레이트 기준 영상 스트림 용량(MB) 산출, 파일 전체 용량을 넘지 않도록 상한 적용

    길이를 알 수 없으면 파일 전체를 영상 스트림으로 간주
    """
    orig_mb = vid.vid_size_MB
    if vid.duration <= 0:
        return orig_mb
    stream_bytes = vid.vid_kbps * BITS_PER_KBIT * vid.duration / BITS_PER_BYTE
    return min(orig_mb, stream_bytes / BYTES_PER_MB)


def calc_keyframe_increase_ratio(
    keyframe_interval: float | None, increase_rate: float
) -> float:
    """1초 키프레임 교정으로 늘어나는 용량 비율 산출

    - 간격은 1.0~10.0초로 클램핑해 극단값이 결과를 왜곡하지 않도록 함
    - 간격 미측정 시 보수적 기본값(5초) 사용
    """
    interval = DEFAULT_UNKNOWN_KF_SEC if keyframe_interval is None else keyframe_interval
    clamped = min(max(interval, KF_CLAMP_MIN_SEC), KF_CLAMP_MAX_SEC)
    return increase_rate * (1.0 - 1.0 / clamped)


def estimate_after_size_mb(
    vid: VideoProps,
    output_ratio: float,
    keyframe_increase_rate: float,
    *,
    cap_at_original: bool,
) -> float:
    """출력 코덱 기준 비트레이트 비율(r_out)로 예상 인코딩 후 파일 크기(MB) 산출

    수식: after = other + stream * min(1, 1 / r_out) * (1 + inc(kf))
    - other: 오디오와 컨테이너 등 인코딩으로 줄지 않는 용량
    - cap_at_original: 과화질 재인코딩은 원본보다 커지지 않는 것으로 제한
    """
    orig_mb = vid.vid_size_MB
    stream_mb = estimate_video_stream_mb(vid)
    other_mb = orig_mb - stream_mb
    bitrate_factor = min(1.0, 1.0 / output_ratio)
    keyframe_factor = 1.0 + calc_keyframe_increase_ratio(
        vid.keyframe_interval, keyframe_increase_rate
    )
    after_mb = other_mb + stream_mb * bitrate_factor * keyframe_factor
    return min(after_mb, orig_mb) if cap_at_original else after_mb
