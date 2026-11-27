"""이전 결과 폐기 기록의 재사용 판정: 원본 크기와 수정 시각, 인코더, 화질 기준값이 폐기 때와 같으면 다시 인코딩하지 않음

- 영상별 직접 지정이 있으면 기록과 무관하게 대상으로 넣음(재처리 방지와 같은 방식)
- 키프레임 초과 영상은 절약 목적이 아니라 교정 목적이므로 기록과 무관하게 대상으로 넣음(결정 10 개정)
- 기록 조회와 원본 파일 상태 읽기는 서비스가 맡고, 이 모듈은 사실 값만 비교
"""

from dataclasses import dataclass

PREVIOUS_DISCARD_LABEL: str = "이전 폐기"
PREVIOUS_DISCARD_TIP: str = (
    "이전 인코딩에서 결과가 원본보다 커서 폐기된 영상입니다. 원본과 인코더, 화질 기준값이 그대로여서 이번에도 인코딩 대상에서 제외했습니다. "
    "영상별로 직접 지정하거나 인코더나 화질 기준값을 바꾸면 다시 대상에 넣습니다."
)


@dataclass(frozen=True, slots=True)
class DiscardFact:
    """폐기 때 기록한 원본 크기와 수정 시각, 인코더, 적용 화질 기준값"""

    source_size: int
    source_mtime_ns: int
    encoder: str
    quality: float


def is_previously_discarded(
    source_size: int,
    source_mtime_ns: int,
    fact: DiscardFact | None,
    manual_target: str | None,
    *,
    encoder: str,
    quality: float,
    keyframe_exceeded: bool,
) -> bool:
    """이번 원본이 이전 폐기 기록과 같으면 참. 기록이 없거나, 원본, 인코더, 화질 기준값이 다르거나, 직접 지정이나 키프레임 초과가 있으면 거짓"""
    if fact is None or manual_target is not None or keyframe_exceeded:
        return False
    return (
        fact.source_size == source_size
        and fact.source_mtime_ns == source_mtime_ns
        and fact.encoder == encoder
        and fact.quality == quality
    )
