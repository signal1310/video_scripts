"""품질값 정밀 결정 대상 선택 규칙(결정 18, 6a-2)

- 적용 범위 설정과 판정 결과만 보는 순수 함수. 프로세스 실행이나 화면 의존 없음
- 게이트 목적 판정은 `encode_gate.purpose_for_evaluation` 한 곳을 그대로 씀(기준 정의 하나)
"""

from src.core.config import (
    PRECISE_QUALITY_MODE_ALL,
    PRECISE_QUALITY_MODE_SELECTIVE,
    ClassifierConfig,
)
from src.core.encode.encode_gate import GatePurpose, purpose_for_evaluation
from src.core.models import VideoEvaluation

# - 선택 적용에서 정밀 결정 대상을 넓히는 판정 경계 여유. 비율이 경계에 이만큼 가까우면 판정이 흔들릴 수 있음
PRECISE_TUNE_BOUNDARY_MARGIN: float = 0.15
# - 경계 여유 비교의 부동소수점 오차 허용치. 비율을 더하고 빼는 계산에서 정확히 경계인 값이 빠지지 않게 함
PRECISE_TUNE_COMPARE_TOLERANCE: float = 1e-9


def should_precise_tune(ev: VideoEvaluation, mode: str, config: ClassifierConfig) -> bool:
    """품질값 정밀 결정을 이 영상에 적용할지 판정

    - `off`는 항상 거짓, `all`은 절약 목적 인코딩 대상 전부
    - `selective`는 절약 목적 중 축소 후보이거나 비트레이트 비율이 과화질/저화질 경계 여유 안쪽인 영상
    - 키프레임 교정(결정 33)과 호환 인코딩은 절약 목적이 아니므로 건드리지 않음
    """
    if mode != PRECISE_QUALITY_MODE_ALL and mode != PRECISE_QUALITY_MODE_SELECTIVE:
        return False
    if not ev.is_encode_target:
        return False
    if purpose_for_evaluation(ev.smart_action, ev.is_keyframe_exceeded) is not GatePurpose.SAVING:
        return False
    if mode == PRECISE_QUALITY_MODE_ALL:
        return True
    if ev.is_downscaled:
        return True
    ratio = ev.bitrate_ratio
    limit = PRECISE_TUNE_BOUNDARY_MARGIN + PRECISE_TUNE_COMPARE_TOLERANCE
    over = config.thresholds.over_bitrate_ratio
    low = config.thresholds.low_bitrate_ratio
    return abs(ratio - over) <= limit or abs(ratio - low) <= limit
