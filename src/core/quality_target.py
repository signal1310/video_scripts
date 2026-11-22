"""기준 품질값, 품질값 차이에 따른 비트레이트와 용량 계수, 인코더 척도 매핑 모듈

- 순수 함수와 상수만 포함하며 판정 엔진, 용량 추정, 표시 문구가 같은 정의를 공유
- 품질값은 기본 인코더(hevc_nvenc -cq) 척도이며 값이 낮을수록 고화질
"""

import math

BASELINE_CQ_DEFAULT: float = 22.0
BASELINE_CQ_MIN: float = 16.0
BASELINE_CQ_MAX: float = 30.0
# - base_target_kbps는 척도 22.0에서의 720p 30fps H.264 적정 비트레이트로 고정하고, 기준 품질값이 바뀌면 B_base(Q)로 환산
REFERENCE_CQ: float = 22.0
# - 저화질 보호 품질값 오프셋. 3단계에서 샤나 동치 값으로 고정
PROTECT_CQ_OFFSET: float = 4.0
# - 품질값 1 변화당 비트레이트 배수의 지수(2^(-delta/K)). 미등록 인코더의 기본값
QUALITY_BITRATE_K: float = 6.0
# - 인코더별 지수. 4a-1 실측 중앙값(12.16절)을 반올림한 확정값
ENCODER_QUALITY_BITRATE_K: dict[str, float] = {
    "hevc_nvenc": 5.8,
    "libx265": 5.7,
    "h264_nvenc": 6.2,
    "libx264": 6.9,
    "libsvtav1": 12.9,
}
# - 같은 VMAF 매핑 뒤에도 남는 인코더 출력 크기 편차(실제/예측 중앙값, 12.17절 확정)
ENCODER_SIZE_FACTORS: dict[str, float] = {
    "hevc_nvenc": 2.60,
    "h264_nvenc": 2.41,
    "libx265": 1.66,
    "libx264": 2.36,
    "libsvtav1": 2.32,
}
# - 기준 인코더(hevc_nvenc) 품질값을 같은 VMAF의 인코더 척도로 옮기는 선형 매핑(기울기, 절편)
ENCODER_QUALITY_MAPPINGS: dict[str, tuple[float, float]] = {
    "libx265": (0.43, 8.8),
    "libx264": (0.45, 9.45),
    "h264_nvenc": (0.93, 2.95),
    "libsvtav1": (1.25, -16.0),
}
ENCODER_QUALITY_RANGES: dict[str, tuple[float, float]] = {
    "hevc_nvenc": (0.0, 51.0),
    "libx265": (0.0, 51.0),
    "libx264": (0.0, 51.0),
    "h264_nvenc": (0.0, 51.0),
    "libsvtav1": (0.0, 63.0),
}
DEFAULT_ENCODER_QUALITY_RANGE: tuple[float, float] = (0.0, 51.0)
# - 5단계 연속 보호 품질값의 상한 차이. 초기값
PROTECT_CQ_DELTA_MAX: float = 6.0
# - 정수 척도 인코더의 반올림 보정값. round()의 짝수 반올림(0.5 경계) 대신 올림 방향으로 통일
_INTEGER_SCALE_HALF: float = 0.5


def quality_bitrate_k(encoder: str | None = None) -> float:
    """인코더별 비트레이트 지수 K 반환. 미등록 이름과 None은 기존 기본값"""
    if encoder is None:
        return QUALITY_BITRATE_K
    return ENCODER_QUALITY_BITRATE_K.get(encoder, QUALITY_BITRATE_K)


def encoder_quality_scale(encoder: str | None = None) -> float:
    """기준 척도 품질값 1 변화가 인코더 척도에서 갖는 기울기 반환. 매핑이 없으면 1.0"""
    mapping = ENCODER_QUALITY_MAPPINGS.get(encoder or "")
    if mapping is None:
        return 1.0
    return mapping[0]


def effective_quality_bitrate_k(encoder: str | None = None) -> float:
    """기준 척도 품질값 차이에 바로 쓸 수 있는 유효 비트레이트 지수 K / scale 반환

    - 기준 척도에서 delta만큼 움직이면 인코더 척도에서는 delta * scale만큼 움직이므로 지수는 delta * scale / K
    - K를 scale로 나눠 두면 호출자가 기준 척도 delta를 그대로 넣을 수 있음
    """
    return quality_bitrate_k(encoder) / encoder_quality_scale(encoder)


def encoder_size_factor(encoder: str | None = None) -> float:
    """매핑 적용 뒤 인코더 출력 크기 보정 계수 반환. 미등록 이름과 None은 1.0"""
    if encoder is None:
        return 1.0
    return ENCODER_SIZE_FACTORS.get(encoder, 1.0)


def clamp_baseline_cq(value: float) -> float:
    """설정 파일에서 읽은 화질 기준값을 유효 범위로 제한하며, NaN, 무한대는 기본값으로 대체

    - 극단값이나 NaN이 남으면 지수 계산에서 비트레이트가 비정상적으로 커지거나 0으로 떨어지므로 로드 시점에 막음
    """
    if not math.isfinite(value):
        return BASELINE_CQ_DEFAULT
    return min(max(value, BASELINE_CQ_MIN), BASELINE_CQ_MAX)


def calc_base_bitrate_kbps(
    base_target_kbps: float, baseline_cq: float, *, encoder: str | None = None
) -> float:
    """기준 품질값에서의 기준 비트레이트 B_base(Q) = B_ref * 2^((22.0 - Q) / (K / scale)) 산출

    - 품질값이 낮아질수록(고화질) 기준 비트레이트가 올라감
    - baseline_cq는 기준 인코더 척도 값이므로 인코더 척도 기울기 scale로 환산한 유효 지수를 씀
    - encoder를 주면 그 인코더의 실측 K와 scale을 쓰고, None과 기준 인코더는 scale 1.0
    """
    return base_target_kbps * 2.0 ** (
        (REFERENCE_CQ - baseline_cq) / effective_quality_bitrate_k(encoder)
    )


def quality_size_factor(delta_cq: float, *, encoder: str | None = None) -> float:
    """출력 품질값과 기준 품질값의 차이(delta)에 따른 영상 스트림 용량 계수 2^(-delta/(K / scale)) 산출

    - delta > 0(저화질 방향)이면 1보다 작아지고, delta = 0이면 1
    - delta는 기준 인코더 척도 값이므로 인코더 척도 기울기 scale로 환산한 유효 지수를 씀
    - encoder를 주면 그 인코더의 실측 K와 scale을 쓰고, None과 기준 인코더는 scale 1.0
    """
    return 2.0 ** (-delta_cq / effective_quality_bitrate_k(encoder))


def protect_cq_value(baseline_cq: float) -> float:
    """기준 품질값에서 저화질 보호 품질값(기준 + 보호 오프셋) 산출"""
    return baseline_cq + PROTECT_CQ_OFFSET


def to_encoder_quality(
    quality: float,
    *,
    encoder: str | None = None,
    integer_scale: bool = False,
    quality_min: float | None = None,
    quality_max: float | None = None,
) -> float:
    """기준 척도 품질값을 같은 VMAF의 인코더 척도로 변환

    - encoder가 None이거나 hevc_nvenc이면 기준 척도 그대로(기존 호출 하위 호환)
    - 그 밖의 인코더는 같은 VMAF 선형 매핑을 적용하고 인코더 척도 범위로 클램핑
    - 정수 척도 인코더는 매핑과 클램핑을 마친 뒤 마지막 단계에서만 반올림
    """
    mapping = ENCODER_QUALITY_MAPPINGS.get(encoder or "")
    if mapping is None:
        return float(int(quality + _INTEGER_SCALE_HALF)) if integer_scale else quality
    scale, offset = mapping
    range_min, range_max = ENCODER_QUALITY_RANGES.get(encoder or "", DEFAULT_ENCODER_QUALITY_RANGE)
    low = range_min if quality_min is None else quality_min
    high = range_max if quality_max is None else quality_max
    mapped = min(max(offset + scale * quality, low), high)
    return float(int(mapped + _INTEGER_SCALE_HALF)) if integer_scale else mapped


def format_cq_value(quality: float) -> str:
    """품질값 표기는 소수 첫째 자리로 고정(예: 22.0, 23.9)"""
    return f"{quality:.1f}"
