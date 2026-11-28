"""두 필터 칩과 프록시, 표 모델, 필터 요약을 잇는 배선 검증"""

import unittest
from typing import cast

from src.gui.controllers.chip_filter_sync import ChipFilterWindow, attach_chip_filters


class _Signal:
    """connect만 지원하는 가짜 시그널"""

    def __init__(self) -> None:
        self.slots: list[object] = []

    def connect(self, slot: object) -> None:
        self.slots.append(slot)

    def emit(self, *args: object) -> None:
        for slot in list(self.slots):
            assert callable(slot)
            slot(*args)


class _FakeFilterBar:
    """칩 변화 신호만 가진 필터 바 대역"""

    def __init__(self) -> None:
        self.chip_filters_changed = _Signal()


class _FakeProxy:
    """칩 필터 상태와 고정 목록을 흉내 내는 프록시 대역"""

    def __init__(self) -> None:
        self.sticky_changed = _Signal()
        self.chip_filter_active: bool = False
        self.sticky: dict[str, str] = {}
        self.applied: list[tuple[bool, bool]] = []

    def set_chip_filters(self, *, review: bool, vary: bool) -> None:
        self.applied.append((review, vary))
        self.chip_filter_active = review or vary
        if not self.chip_filter_active:
            self.sticky.clear()

    def sticky_acknowledged(self) -> dict[str, str]:
        return dict(self.sticky)


class _FakeTableModel:
    """칩 필터 반영만 기록하는 표 모델 대역"""

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, str], bool]] = []

    def set_chip_filters(self, acknowledged: dict[str, str], *, active: bool) -> None:
        self.calls.append((dict(acknowledged), active))


class _FakeWindow:
    """배선에 필요한 부품만 가진 창 대역"""

    def __init__(self) -> None:
        self.filter_bar = _FakeFilterBar()
        self.proxy_model = _FakeProxy()
        self.table_model = _FakeTableModel()


class ChipFilterSyncTests(unittest.TestCase):
    """칩 변화가 프록시와 표 모델에 함께 전달되는지 검증"""

    def setUp(self) -> None:
        self.window = _FakeWindow()
        self.summaries: list[int] = []
        attach_chip_filters(
            cast("ChipFilterWindow", self.window), lambda: self.summaries.append(1)
        )

    def test_chip_toggle_updates_proxy_table_model_and_summary(self) -> None:
        self.window.filter_bar.chip_filters_changed.emit(True, False)
        self.assertEqual(self.window.proxy_model.applied, [(True, False)])
        self.assertEqual(self.window.table_model.calls, [({}, True)])
        self.assertEqual(self.summaries, [1])

    def test_sticky_change_republishes_acknowledged_kinds(self) -> None:
        self.window.filter_bar.chip_filters_changed.emit(False, True)
        self.window.proxy_model.sticky["vary.mp4"] = "manual"
        self.window.proxy_model.sticky_changed.emit()
        self.assertEqual(
            self.window.table_model.calls[-1], ({"vary.mp4": "manual"}, True)
        )

    def test_turning_both_chips_off_reports_inactive_cells(self) -> None:
        self.window.filter_bar.chip_filters_changed.emit(True, True)
        self.window.proxy_model.sticky["review.mp4"] = "reset"
        self.window.proxy_model.sticky_changed.emit()
        self.window.filter_bar.chip_filters_changed.emit(False, False)
        self.assertEqual(self.window.table_model.calls[-1], ({}, False))


if __name__ == "__main__":
    unittest.main()
