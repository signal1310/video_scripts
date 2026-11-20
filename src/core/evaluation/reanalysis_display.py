"""재분석이 필요한 행의 표 파생 열 표시 판정 순수 함수 모듈"""

from src.core.models import VideoEvaluation
from src.core.smart_action import SmartAction


def is_reanalysis_blurred(ev: VideoEvaluation) -> bool:
    """이전 형식(LEGACY_FORMAT)이거나 키프레임 간격이 미측정이면 파생 열 블러 대상"""
    if ev.smart_action is SmartAction.LEGACY_FORMAT:
        return True
    kf = ev.video.keyframe_interval
    return kf is None or kf < 0
