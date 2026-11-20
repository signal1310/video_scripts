"""이전 결과 폐기 기록의 재사용 판정: 원본 크기와 수정 시각이 폐기 때와 같으면 다시 재인코딩하지 않음

- 영상별 직접 지정이 있으면 기록과 무관하게 대상으로 넣음(재처리 방지와 같은 방식)
- 기록 조회와 원본 파일 상태 읽기는 서비스가 맡고, 이 모듈은 사실 값만 비교
"""

from dataclasses import dataclass

PREVIOUS_DISCARD_LABEL: str = "이전 폐기"
PREVIOUS_DISCARD_TIP: str = (
    "이전 재인코딩에서 결과가 원본보다 커서 폐기된 영상입니다. 원본이 바뀌지 않아 이번에도 재인코딩 대상에서 뺐습니다. "
    "영상별로 직접 지정하면 다시 대상에 넣습니다."
)


@dataclass(frozen=True, slots=True)
class DiscardFact:
    """폐기 때 기록한 원본 크기와 수정 시각"""

    source_size: int
    source_mtime_ns: int


def is_previously_discarded(
    source_size: int,
    source_mtime_ns: int,
    fact: DiscardFact | None,
    manual_target: str | None,
) -> bool:
    """이번 원본이 이전 폐기 기록과 같으면 참. 기록이 없거나, 원본이 바뀌었거나, 직접 지정이 있으면 거짓"""
    if fact is None or manual_target is not None:
        return False
    return fact.source_size == source_size and fact.source_mtime_ns == source_mtime_ns
