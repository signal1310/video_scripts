"""재인코딩 탭의 FFmpeg 상태와 목표 인코딩 방식 상태 문구를 계산하는 모듈

- 객체 이름(오류 스타일 여부)과 문구를 짝으로 돌려주며, 위젯에 적용하는 일은 execution_sections.py가 맡음
"""

from src.core.encode.ffmpeg_capability import FfmpegCapability
from src.gui.settings.execution_texts import CODEC_AVAILABLE_TEXT, CODEC_UNAVAILABLE_PREFIX

STATUS_OBJECT_ERROR: str = "ErrorHintLabel"
STATUS_OBJECT_NORMAL: str = "SettingDesc"
_UNKNOWN_VERSION_TEXT: str = "버전을 확인하지 못해 그대로 사용합니다"


def ffmpeg_status(capability: FfmpegCapability) -> tuple[str, str]:
    """FFmpeg 확인 결과를 (객체 이름, 상태 문구)로 돌려줌. 사용할 수 없으면 사유를 오류 스타일로 둠"""
    if not capability.usable:
        return STATUS_OBJECT_ERROR, f"사용할 수 없음: {capability.reason}"
    if capability.version is None:
        return STATUS_OBJECT_NORMAL, f"사용 가능: {_UNKNOWN_VERSION_TEXT}"
    major, minor = capability.version
    return STATUS_OBJECT_NORMAL, f"사용 가능: FFmpeg {major}.{minor}"


def codec_status(capability: FfmpegCapability | None, codec: str) -> tuple[str, str]:
    """목표 인코딩 방식을 이 PC에서 쓸 수 있는지 (객체 이름, 문구)로 돌려줌

    - FFmpeg를 쓸 수 없으면 FFmpeg 사유를 따르고, 확인 전이거나 인코더가 FFmpeg 목록에 없으면 문구가 빈 값
    - 인코더가 목록에 없을 때의 사유 문구는 승인 전이라 아직 두지 않음
    """
    if capability is None:
        return STATUS_OBJECT_NORMAL, ""
    if not capability.usable:
        return STATUS_OBJECT_ERROR, f"{CODEC_UNAVAILABLE_PREFIX}{capability.reason}"
    if codec in capability.encoders:
        return STATUS_OBJECT_NORMAL, CODEC_AVAILABLE_TEXT
    return STATUS_OBJECT_NORMAL, ""
