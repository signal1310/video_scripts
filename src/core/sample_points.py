"""공통 샘플 구간 선택, 무효 구간 판정, 유효 구간 중앙값 집계 순수 함수 모듈

- 결정 19(축소 손실 측정)와 결정 33(키프레임 교정 품질값 탐색)이 같은 구간 규칙을 공유
- 무효 구간 임계값은 12.15절까지의 초기값이며 4단계 실측 보정 대상
"""

from collections.abc import Sequence
from dataclasses import dataclass

# - 결정 19 공통 기본: 9지점 x 2초
DEFAULT_SEGMENT_COUNT: int = 9
DEFAULT_SEGMENT_DURATION_SEC: float = 2.0
# - 결정 33 기본: 5지점 x 2초(12.15절 실측)
SAMPLE_SEGMENT_COUNT: int = 5
SAMPLE_SEGMENT_DURATION_SEC: float = DEFAULT_SEGMENT_DURATION_SEC
SAMPLE_SHORT_VIDEO_THRESHOLD_SEC: float = 20.0
SAMPLE_RANGE_START_RATIO: float = 0.10
SAMPLE_RANGE_END_RATIO: float = 0.90
# - 초기값, 4단계 보정 대상: 검은 화면 비율이 이 값 이상인 구간은 변별력이 없어 제외
BLACK_RATIO_INVALID_THRESHOLD: float = 0.50
# - 초기값, 4단계 보정 대상: 최저 규격 왕복 SSIM이 이 dB 이상이면 축소 손실이 드러나지 않아 제외
FLAT_ROUNDTRIP_SSIM_DB: float = 40.0


def sample_segments(
    duration_sec: float,
    count: int = DEFAULT_SEGMENT_COUNT,
    segment_sec: float = DEFAULT_SEGMENT_DURATION_SEC,
) -> tuple[tuple[float, float], ...]:
    """결정 19 공통 구간 규칙에 따라 (시작 시각, 길이) 쌍을 반환. 20초 미만은 전체 1구간

    - 10%~90% 구간에 균등 배치하고, 마지막 구간이 영상 끝을 넘지 않게 자름
    - 간격이 구간 길이보다 짧아지는 짧은 영상은 구간 수를 줄여 겹침을 없앰
    """
    if duration_sec <= 0:
        return ()
    if duration_sec < SAMPLE_SHORT_VIDEO_THRESHOLD_SEC or count <= 1 or duration_sec <= segment_sec:
        return ((0.0, duration_sec),)
    start_bound = duration_sec * SAMPLE_RANGE_START_RATIO
    end_bound = max(start_bound, duration_sec * SAMPLE_RANGE_END_RATIO - segment_sec)
    span = end_bound - start_bound
    # - 간격이 구간 길이보다 짧으면 같은 프레임을 두 번 재므로 배치 구간 수를 간격에 맞춰 줄임
    fit_count = int(span // segment_sec) + 1
    count = min(count, fit_count)
    segments: list[tuple[float, float]] = []
    for index in range(count):
        ratio = index / (count - 1) if count > 1 else 0.0
        start = min(max(0.0, start_bound + span * ratio), max(0.0, duration_sec - segment_sec))
        segments.append((start, segment_sec))
    return tuple(segments)


@dataclass(frozen=True, slots=True)
class SegmentQuality:
    """구간 한 개의 무효 판정 입력과 측정 지표"""

    start_sec: float
    duration_sec: float
    black_ratio: float = 0.0
    roundtrip_ssim_db: float | None = None
    metric_value: float | None = None


def is_invalid_segment(sample: SegmentQuality) -> bool:
    """검은 화면 비율과 왕복 SSIM 기준으로 변별력이 없는 구간을 판정

    - 임계값은 초기값, 4단계 보정 대상
    - 왕복 SSIM을 재지 않은 구간은 검은 화면 비율만으로 판정
    """
    if sample.black_ratio >= BLACK_RATIO_INVALID_THRESHOLD:
        return True
    return sample.roundtrip_ssim_db is not None and sample.roundtrip_ssim_db >= FLAT_ROUNDTRIP_SSIM_DB


def median_valid_metric(samples: Sequence[SegmentQuality]) -> float | None:
    """유효 구간의 측정 지표 중앙값. 유효 구간이나 지표가 없으면 None"""
    values = sorted(
        sample.metric_value
        for sample in samples
        if sample.metric_value is not None and not is_invalid_segment(sample)
    )
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2 == 1:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2.0
