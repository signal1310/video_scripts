"""행 번호 목록을 연속 구간으로 묶는 표 공용 도우미

- 표 모델과 그룹 프록시가 같은 규칙으로 `dataChanged` 구간을 만들어 다시 그리기 범위를 좁힘
- 연속 구간이 아니면 사이의 관계없는 행까지 다시 그리게 되므로 정렬한 뒤에 묶음
"""

from collections.abc import Iterable

RowSpan = tuple[int, int]


def contiguous_row_ranges(rows: Iterable[int]) -> list[RowSpan]:
    """행 번호 목록을 연속 구간 (첫 행, 끝 행) 목록으로 묶음, 빈 목록은 빈 결과"""
    ranges: list[RowSpan] = []
    ordered = sorted(rows)
    if not ordered:
        return ranges
    first = previous = ordered[0]
    for row in ordered[1:]:
        if row != previous + 1:
            ranges.append((first, previous))
            first = row
        previous = row
    ranges.append((first, previous))
    return ranges


__all__ = ["RowSpan", "contiguous_row_ranges"]

