"""재인코딩 시작 메뉴 항목의 활성 여부와 비활성 사유를 계산하는 순수 함수 모듈

- 사유 문구는 IMPLEMENTATION_PLAN 8절의 승인 문구를 씀
- 항목은 숨기지 않고 비활성으로 두며, 사유는 메뉴 툴팁과 상태 표시줄에 표시됨
"""

from dataclasses import dataclass

from src.core.encode.encoder_trial import EncoderTrialResult
from src.core.encode.ffmpeg_capability import FfmpegCapability

ANALYSIS_BUSY_TEXT: str = "분석이 끝난 뒤 시작할 수 있습니다."
ENCODE_RUNNING_TEXT: str = "재인코딩이 진행 중입니다."
NO_TARGET_TEXT: str = "재인코딩할 영상이 없습니다."
FFMPEG_UNUSABLE_PREFIX: str = "FFmpeg를 쓸 수 없어 시작할 수 없습니다: "
ENCODER_CHECKING_TEXT: str = "인코더를 확인하는 중입니다."


@dataclass(frozen=True, slots=True)
class StartState:
    """재인코딩 시작 항목 상태. reason은 비활성일 때만 비어 있지 않고, pending이면 시험이 끝나지 않아 다시 확인해야 함"""

    enabled: bool
    reason: str
    pending: bool


def evaluate_start_state(
    *,
    analysis_busy: bool,
    encode_running: bool,
    has_targets: bool,
    capability: FfmpegCapability,
    encoder: EncoderTrialResult | None,
) -> StartState:
    """조건을 승인된 순서(분석, 진행, 대상, FFmpeg, 인코더)로 검사해 첫 사유를 돌려줌

    - encoder가 None이면 인코더 시험이 끝나지 않은 것으로 봄(pending)
    """
    if analysis_busy:
        return StartState(enabled=False, reason=ANALYSIS_BUSY_TEXT, pending=False)
    if encode_running:
        return StartState(enabled=False, reason=ENCODE_RUNNING_TEXT, pending=False)
    if not has_targets:
        return StartState(enabled=False, reason=NO_TARGET_TEXT, pending=False)
    if not capability.usable:
        return StartState(enabled=False, reason=f"{FFMPEG_UNUSABLE_PREFIX}{capability.reason}", pending=False)
    if encoder is None:
        return StartState(enabled=False, reason=ENCODER_CHECKING_TEXT, pending=True)
    if not encoder.usable:
        return StartState(enabled=False, reason=encoder.reason, pending=False)
    return StartState(enabled=True, reason="", pending=False)
