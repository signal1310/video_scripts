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
# - 품질값 1 변화당 비트레이트 배수의 지수(2^(-delta/K)). 초기값이며 4단계에서 인코더별로 실측
QUALITY_BITRATE_K: float = 6.0
# - 5단계 연속 보호 품질값의 상한 차이. 초기값
PROTECT_CQ_DELTA_MAX: float = 6.0
# - 정수 척도 인코더의 반올림 보정값. round()의 짝수 반올림(0.5 경계) 대신 올림 방향으로 통일
_INTEGER_SCALE_HALF: float = 0.5


def clamp_baseline_cq(value: float) -> float:
    """설정 파일에서 읽은 화질 기준값을 유효 범위로 제한하며, NaN, 무한대는 기본값으로 대체

    - 극단값이나 NaN이 남으면 지수 계산에서 비트레이트가 비정상적으로 커지거나 0으로 떨어지므로 로드 시점에 막음
    """
    if not math.isfinite(value):
        return BASELINE_CQ_DEFAULT
    return min(max(value, BASELINE_CQ_MIN), BASELINE_CQ_MAX)


def calc_base_bitrate_kbps(base_target_kbps: float, baseline_cq: float) -> float:
    """기준 품질값에서의 기준 비트레이트 B_base(Q) = B_ref * 2^((22.0 - Q) / K) 산출

    - 품질값이 낮아질수록(고화질) 기준 비트레이트가 올라감
    """
    return base_target_kbps * 2.0 ** ((REFERENCE_CQ - baseline_cq) / QUALITY_BITRATE_K)


def quality_size_factor(delta_cq: float) -> float:
    """출력 품질값과 기준 품질값의 차이(delta)에 따른 영상 스트림 용량 계수 2^(-delta/K) 산출

    - delta > 0(저화질 방향)이면 1보다 작아지고, delta = 0이면 1
    """
    return 2.0 ** (-delta_cq / QUALITY_BITRATE_K)


def protect_cq_value(baseline_cq: float) -> float:
    """기준 품질값에서 저화질 보호 품질값(기준 + 보호 오프셋) 산출"""
    return baseline_cq + PROTECT_CQ_OFFSET


def to_encoder_quality(quality: float, *, integer_scale: bool) -> float:
    """인코더 척도 품질값으로 변환하며, 정수 척도 인코더는 마지막 단계에서만 반올림

    - 초기값은 모든 인코더 항등 매핑이며 실측 보정은 4단계에서 수행
    """
    if integer_scale:
        return float(int(quality + _INTEGER_SCALE_HALF))
    return quality


def format_cq_value(quality: float) -> str:
    """품질값 표기는 소수 첫째 자리로 고정(예: 22.0, 23.9)"""
    return f"{quality:.1f}"
