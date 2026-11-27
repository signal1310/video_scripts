"""화질 비교 재생의 순수 규칙: 배속 비례 동기화 판정, 끝 도달, 실시간 축소 왕복

- 재생기 객체를 만들지 않는 값 계산만 두어 단위 테스트로 바로 확인
- 재생 엔진은 `frame_compare_player.py`가 맡고 이 모듈의 규칙을 재사용
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage

from src.core.analysis.frame_compare import reached_video_end
from src.core.resolution_tiers import (
    DEFAULT_SCALE_ALGORITHM,
    RESOLUTION_TIER_BY_KEY,
    calc_scaled_dimensions,
    normalize_scale_algorithm,
)

# - 두 파일 비교는 양쪽 프레임을 같은 시각으로 맞춰야 하므로 원본 프레임 한 장 이내만 짝으로 인정
PAIR_TOLERANCE_FRAMES: int = 1
# - 종 프레임 보관 수. 30fps 기준 약 200ms로, 8배속에서 보정이 늦어도 시간 맞춤 후보가 남음
PAIR_BUFFER_FRAMES: int = 6
# - 보관 한도 안쪽에서 보정하도록 두 프레임 시각 차가 원본 두 장을 넘으면 종 재생기를 다시 맞춤
SYNC_DRIFT_FRAMES: int = 2
# - 보정 시크 직후 연속 재시크가 반복되지 않게 쉬는 시간. 시크가 화면에 자리 잡는 시간과 맞춤
SYNC_COOLDOWN_SEC: float = 0.2
# - 고배속에서 화면 갱신이 UI 스레드를 점유하지 않도록 변환 간격을 30fps로 제한
MIN_RENDER_INTERVAL_SEC: float = 1.0 / 30.0
# - 메타데이터에 fps가 없을 때 원본 한 프레임 길이를 정하는 기준
DEFAULT_SOURCE_FPS: float = 30.0
# - Qt는 빠른 변환, 부드러운 변환 두 가지만 제공해 swscale의 11가지를 그대로 옮길 수 없음
# - 계단 현상이 의도인 fast_bilinear, neighbor만 빠른 변환으로 두고 나머지는 부드러운 변환으로 근사
# - 2160p 기준 왕복 한 장이 부드러운 변환 약 8ms로 30fps 간격(33ms) 안이라 재생에 충분
QT_FAST_ALGORITHMS: frozenset[str] = frozenset({"fast_bilinear", "neighbor"})


def source_frame_us(fps: float) -> int:
    """원본 한 프레임 길이(마이크로초). fps를 모르거나 0 이하면 기본 30fps 기준"""
    rate = fps if fps > 0 else DEFAULT_SOURCE_FPS
    return max(round(1_000_000 / rate), 1)


def pair_tolerance_us(fps: float, frames: int = PAIR_TOLERANCE_FRAMES) -> int:
    """짝으로 인정할 두 프레임의 최대 시각 차(마이크로초)"""
    return source_frame_us(fps) * frames


def closest_frame_index(
    primary_us: int, secondary_us: Sequence[int], tolerance_us: int
) -> int | None:
    """주 프레임 시각과 가장 가까운 종 프레임의 위치, 허용 폭을 넘는 프레임뿐이면 None"""
    best: int | None = None
    best_gap: int | None = None
    for index, value in enumerate(secondary_us):
        gap = abs(value - primary_us)
        if best_gap is None or gap < best_gap:
            best = index
            best_gap = gap
    if best is None or best_gap is None or best_gap > tolerance_us:
        return None
    return best


def sync_drift_exceeded(
    frame_gap_us: int, fps: float, max_frames: int = SYNC_DRIFT_FRAMES
) -> bool:
    """주/종 프레임 시각 차가 원본 몇 프레임을 넘었는지

    - 시각 차로 판정해 두 재생기의 위치 보고 지연과 무관하게 같은 지점을 비교함
    """
    return abs(frame_gap_us) > source_frame_us(fps) * max_frames


def restart_from_start(position_ms: int, duration_ms: int, fps: float = 0.0) -> bool:
    """끝까지 재생한 상태라 다시 재생할 때 처음으로 돌아가야 하는지

    - 재생기는 끝에서 한 프레임 안쪽에 머무르므로 그 안쪽도 끝으로 봄
    """
    return reached_video_end(position_ms, duration_ms, fps)


def qt_transformation_mode(algorithm: str) -> Qt.TransformationMode:
    """설정한 축소 보간 방식을 Qt 이미지 변환 모드로 사상

    - Qt는 빠른 변환과 부드러운 변환만 제공해 swscale의 11가지 방식을 그대로 쓸 수 없음
    - 계단 현상이 의도인 fast_bilinear, neighbor만 빠른 변환으로 두고 나머지는 부드러운 변환으로 근사
    """
    value = normalize_scale_algorithm(algorithm)
    if value in QT_FAST_ALGORITHMS:
        return Qt.TransformationMode.FastTransformation
    return Qt.TransformationMode.SmoothTransformation


def roundtrip_compare_frame(
    frame: QImage,
    width: int,
    height: int,
    tier_key: str,
    *,
    algorithm: str = DEFAULT_SCALE_ALGORITHM,
) -> QImage:
    """원본 프레임을 규격 크기로 줄였다가 원본 크기로 되돌린 비교 프레임

    - 정지 화면 추출과 같은 설정 보간 방식을 받아 재생과 일시중지의 화면이 어긋나지 않게 함
    - 규격을 모르거나 크기를 모르면 원본 프레임을 그대로 돌려줌
    """
    tier = RESOLUTION_TIER_BY_KEY.get(tier_key)
    if tier is None or width <= 0 or height <= 0:
        return frame
    mode = qt_transformation_mode(algorithm)
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
