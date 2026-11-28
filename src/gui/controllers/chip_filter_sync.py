"""검색창 오른쪽 두 필터 칩(여백 확인 필요, 구간별 여백 차이)을 프록시와 표 모델에 연결하는 모듈

- 두 칩은 OR로 합쳐 표를 거르고, 칩이 하나라도 켜져 있는 동안 확인한 행은 흐린 확인 아이콘으로 고정
- 고정 목록이 바뀌면 표 모델에 확인 방법을 다시 넘겨 아이콘과 툴팁을 맞춤
"""

from collections.abc import Callable
from typing import Protocol

from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_model import VideoTableModel
from src.gui.widgets.filter_bar import FilterTuningBar


class ChipFilterWindow(Protocol):
    """두 칩을 연결할 창의 부품"""

    filter_bar: FilterTuningBar
    proxy_model: VideoSortFilterProxyModel
    table_model: VideoTableModel


def attach_chip_filters(window: ChipFilterWindow, refresh_summary: Callable[[], None]) -> None:
    """두 칩의 켜짐 상태와 확인 행 고정 목록을 프록시, 표 모델, 필터 요약에 연결"""

    def sync_acknowledged() -> None:
        window.table_model.set_chip_filters(
            window.proxy_model.sticky_acknowledged(),
            active=window.proxy_model.chip_filter_active,
        )

    def on_chips_changed(review: bool, vary: bool) -> None:
        window.proxy_model.set_chip_filters(review=review, vary=vary)
        sync_acknowledged()
        refresh_summary()

    window.filter_bar.chip_filters_changed.connect(on_chips_changed)
    window.proxy_model.sticky_changed.connect(sync_acknowledged)


__all__ = ["ChipFilterWindow", "attach_chip_filters"]
