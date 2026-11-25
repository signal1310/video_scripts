"""화질 비교 재생의 순수 규칙: 배속 비례 동기화 판정, 끝 도달, 실시간 축소 왕복

- 재생기 객체를 만들지 않는 값 계산만 두어 단위 테스트로 바로 확인
- 재생 엔진은 `frame_compare_player.py`가 맡고 이 모듈의 규칙을 재사용
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage

from src.core.analysis.frame_compare import reached_video_end
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY, calc_scaled_dimensions

SYNC_DRIFT_MS: int = 120
# - 고배속에서 화면 갱신이 UI 스레드를 점유하지 않도록 변환 간격을 30fps로 제한
MIN_RENDER_INTERVAL_SEC: float = 1.0 / 30.0
# - 종 재생기 보정 시크 직후 연속 재시크가 반복되지 않게 쉬는 시간
SYNC_COOLDOWN_SEC: float = 0.6


def sync_drift_exceeded(primary_ms: int, secondary_ms: int, rate: float = 1.0) -> bool:
    """두 재생기 위치 차가 배속에 비례한 보정 기준을 넘었는지"""
    threshold_ms = max(SYNC_DRIFT_MS, round(SYNC_DRIFT_MS * max(rate, 1.0)))
    return abs(primary_ms - secondary_ms) > threshold_ms


def restart_from_start(position_ms: int, duration_ms: int, fps: float = 0.0) -> bool:
    """끝까지 재생한 상태라 다시 재생할 때 처음으로 돌아가야 하는지

    - 재생기는 끝에서 한 프레임 안쪽에 머무르므로 그 안쪽도 끝으로 봄
    """
    return reached_video_end(position_ms, duration_ms, fps)


def roundtrip_compare_frame(
    frame: QImage, width: int, height: int, tier_key: str, *, fast: bool = False
) -> QImage:
    """원본 프레임을 규격 크기로 줄였다가 원본 크기로 되돌린 비교 프레임

    - fast가 참이면 실시간 재생용으로 빠른 보간을 쓰고, 정지 화면은 정밀 보간을 씀
    - 규격을 모르거나 크기를 모르면 원본 프레임을 그대로 돌려줌
    """
    tier = RESOLUTION_TIER_BY_KEY.get(tier_key)
    if tier is None or width <= 0 or height <= 0:
        return frame
    mode = Qt.TransformationMode.FastTransformation if fast else Qt.TransformationMode.SmoothTransformation
    scaled_width, scaled_height = calc_scaled_dimensions(width, height, tier)
    small = frame.scaled(
        scaled_width,
        scaled_height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        mode,
    )
    return small.scaled(
        width,
        height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        mode,
    )
