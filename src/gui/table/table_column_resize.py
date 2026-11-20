"""표 헤더의 열 너비를 사용자가 끌어서 바꾸도록 하는 공용 설정

- 열 크기 모드를 Stretch나 ResizeToContents로 두면 사용자가 너비를 바꿀 수 없으므로 모든 열을 Interactive로 둠
"""

from collections.abc import Sequence

from PyQt6.QtWidgets import QHeaderView


def make_columns_resizable(header: QHeaderView, widths: Sequence[int]) -> None:
    """모든 열을 끌어서 바꿀 수 있게 하고 열마다 처음 너비를 정함

    - 열 수는 widths 길이로 정함
    - 마지막 열은 남은 폭을 채우지 않음. 채우면 조작 열이 넓어져 버튼이 한쪽으로 치우침
    """
    header.setStretchLastSection(False)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    for column, width in enumerate(widths):
        header.resizeSection(column, width)
