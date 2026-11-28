"""화질 분석 캐시 상태에서 표시 집합과 칩 필터 확인 행 고정 목록을 만드는 도우미

- 여백 확인 필요와 구간별 여백 차이 표시를 한 번에 갈라 두 칩 개수가 같은 시점에 줄어들게 함
- 확인 처리로 표시가 풀리는 행은 작업 전에 고정 목록에 담아 칩 필터 안에서 흐린 확인 아이콘으로 남김
"""

from collections.abc import Iterable, Mapping

from src.core.models import ActiveFrame
from src.gui.table.frame_analysis_columns import ACK_FULL, ACK_MANUAL, ACK_RESET
from src.service.frame_analysis_service import CropReviewStatus


class ReviewFlags:
    """아직 확인하지 않은 여백 확인 필요, 구간별 여백 차이 파일명 집합"""

    def __init__(self) -> None:
        self.review: frozenset[str] = frozenset()
        self.vary: frozenset[str] = frozenset()

    def set(self, review: frozenset[str], vary: frozenset[str]) -> None:
        """표시 집합 두 개를 그대로 교체(적재한 캐시 스냅샷 반영)"""
        self.review = review
        self.vary = vary

    def clear(self) -> None:
        """표시 집합을 모두 비움"""
        self.review = frozenset()
        self.vary = frozenset()

    def replace(self, statuses: Mapping[str, CropReviewStatus]) -> None:
        """서비스가 돌려준 상태에서 두 표시 집합을 다시 가름"""
        self.review = frozenset(name for name, status in statuses.items() if status.flagged)
        self.vary = frozenset(name for name, status in statuses.items() if status.margins_vary)

    def pinned(self, filenames: Iterable[str], *, kind: str) -> dict[str, str]:
        """표시 대상이던 영상의 확인 방법만 골라 칩 필터 고정 목록을 만듦

        - 확인 처리 뒤에는 표시가 풀리므로, 바꾸기 전에 계산한 표시 집합을 기준으로 판단
        """
        return {
            name: kind
            for name in filenames
            if name in self.review or name in self.vary
        }


def acknowledgement_kind(frame: ActiveFrame | None) -> str:
    """사용자가 고른 조치의 확인 방법(직접 지정/자동 감지로 되돌리기)"""
    return ACK_RESET if frame is None else ACK_MANUAL


__all__ = ["ACK_FULL", "ReviewFlags", "acknowledgement_kind"]
