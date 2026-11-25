"""인코딩 진행 창의 이번 실행 범위: 시작 때 대기나 진행 중인 항목만 이번 실행의 대상으로 셈

- 전체 남은 시간의 대상 수와 끝난 수, 평균 속도의 처리한 영상 길이를 같은 범위로 맞춤
- 이전 실행에서 끝난 항목과 건너뜀은 범위에서 뺌
"""

from collections.abc import Iterable, Mapping

from src.core.encode.encode_batch_book import ENCODED_STATES
from src.core.encode.encode_progress_view import run_counts
from src.core.encode.encode_state import EncodeState

RUN_START_STATES: frozenset[EncodeState] = frozenset({EncodeState.PENDING, EncodeState.RUNNING})
RunItem = tuple[str, EncodeState, str]


class RunScope:
    """이번 실행의 대상 경로 집합. 새 실행을 시작할 때 한 번 정하고, 이후 상태 변화는 같은 집합 안에서만 셈"""

    def __init__(self) -> None:
        self._paths: frozenset[str] = frozenset()

    def start(self, items: Iterable[RunItem]) -> None:
        """새 실행을 시작할 때 대기나 진행 중인 항목을 이번 실행의 대상으로 기록"""
        self._paths = frozenset(path for path, state, _reason in items if state in RUN_START_STATES)

    def counts(self, items: Iterable[RunItem], positions: Mapping[str, float]) -> tuple[int, int]:
        """이번 실행의 대상 수와 인코딩을 끝낸 수. positions에 없는 항목은 인코딩을 시작하지 않은 것으로 봄"""
        return run_counts((state, path in positions) for path, state, _reason in items if path in self._paths)

    def encoded_positions(self, items: Iterable[RunItem], positions: Mapping[str, float]) -> list[float]:
        """이번 실행에서 인코딩을 거친 항목이 도달한 마지막 진행 위치(초). 원본 길이가 아니라 실제로 처리한 길이이며, 위치가 없는 항목은 뺌"""
        return [
            positions[path]
            for path, state, _reason in items
            if path in self._paths and state in ENCODED_STATES and path in positions
        ]
