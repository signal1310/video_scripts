"""인코딩 탭의 FFmpeg 상태와 목표 인코딩 방식 상태 문구를 계산하는 모듈

- 객체 이름(오류 스타일 여부)과 문구를 짝으로 돌려주며, 위젯에 적용하는 일은 execution_sections.py가 맡음
"""

from dataclasses import dataclass

from src.core.encode.encoder_trial import EncoderTrialCache, check_encoder
from src.core.encode.ffmpeg_capability import FfmpegCapability
from src.gui.settings.execution_texts import CODEC_AVAILABLE_TEXT, CODEC_CHECKING_TEXT, CODEC_UNAVAILABLE_PREFIX

STATUS_OBJECT_ERROR: str = "ErrorHintLabel"
STATUS_OBJECT_NORMAL: str = "SettingDesc"
_UNKNOWN_VERSION_TEXT: str = "버전을 확인하지 못해 그대로 사용합니다"


@dataclass(frozen=True, slots=True)
class CodecStatus:
    """목표 인코딩 방식 상태 줄. pending이면 시험이 끝나지 않아 다시 확인해야 함"""

    object_name: str
    text: str
    pending: bool


def ffmpeg_status(capability: FfmpegCapability) -> tuple[str, str]:
    """FFmpeg 확인 결과를 (객체 이름, 상태 문구)로 돌려줌. 사용할 수 없으면 사유를 오류 스타일로 둠"""
    if not capability.usable:
        return STATUS_OBJECT_ERROR, f"사용할 수 없음: {capability.reason}"
    if capability.version is None:
        return STATUS_OBJECT_NORMAL, f"사용 가능: {_UNKNOWN_VERSION_TEXT}"
    major, minor = capability.version
    return STATUS_OBJECT_NORMAL, f"사용 가능: FFmpeg {major}.{minor}"


def codec_status(capability: FfmpegCapability | None, codec: str, cache: EncoderTrialCache) -> CodecStatus:
    """목표 인코딩 방식을 이 PC에서 쓸 수 있는지 상태 줄로 돌려줌

    - 확인 전이면 문구가 빈 값이고, FFmpeg를 쓸 수 없으면 FFmpeg 사유를 따름
    - 인코더 목록 확인 뒤 시험 인코딩 결과를 쓰며, 시험이 끝나기 전에는 확인 중으로 두고 시험을 시작함
    """
    if capability is None:
        return CodecStatus(STATUS_OBJECT_NORMAL, "", pending=False)
    if not capability.usable:
        return CodecStatus(STATUS_OBJECT_ERROR, f"{CODEC_UNAVAILABLE_PREFIX}{capability.reason}", pending=False)
    result = check_encoder(capability, codec, cache)
    if result is None:
        return CodecStatus(STATUS_OBJECT_NORMAL, CODEC_CHECKING_TEXT, pending=True)
    if result.usable:
        return CodecStatus(STATUS_OBJECT_NORMAL, CODEC_AVAILABLE_TEXT, pending=False)
    return CodecStatus(STATUS_OBJECT_ERROR, f"{CODEC_UNAVAILABLE_PREFIX}{result.reason}", pending=False)
