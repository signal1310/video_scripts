"""행 묶기 상태에서 검색어 필터 적용 비용과 묶음 재구성 횟수 검증

- 필터 재평가는 한 번의 묶음으로 알려지고 그룹 프록시는 묶음이 끝날 때 한 번만 다시 만듦
- 묶음 종료 직후 그룹 모델은 이미 새 필터 결과를 담고 있어야 함
"""

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.models import VideoEvaluation
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_grouping import VideoGroupProxyModel
from src.gui.table.table_model import VideoTableModel
from src.gui.widgets.filter_bar import SEARCH_DEBOUNCE_MS, FilterTuningBar
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_ROOT_DIR: str = "C:/work"
_ROW_COUNT: int = 5000
_GROUP_COUNT: int = 300
# - 오프스크린 실행에서도 넉넉한 상한, 실제 측정값은 그보다 훨씬 작음
_FILTER_TIME_LIMIT_SEC: float = 1.5


def _grouped_evaluations(rows: int, groups: int) -> list[VideoEvaluation]:
    """폴더가 groups개로 나뉘고 코덱이 섞인 평가 목록"""
    return [
        make_eval(
            video=make_video(
                filename=f"folder{index % groups:03d}/clip{index:05d}.mp4",
                codec="h264" if index % 3 else "hevc",
            )
        )
        for index in range(rows)
    ]


def _make_chain(
    evaluations: list[VideoEvaluation],
) -> tuple[VideoTableModel, VideoSortFilterProxyModel, VideoGroupProxyModel]:
    """원본 표 모델, 필터 프록시, 폴더 묶기 그룹 프록시를 차례로 연결한 고리"""
    model = VideoTableModel(config=ClassifierConfig())
    model.set_data_source(evaluations, root_dir=_ROOT_DIR)
    proxy = VideoSortFilterProxyModel()
    proxy.setSourceModel(model)
    group = VideoGroupProxyModel()
    group.setSourceModel(proxy)
    group.set_group_by("folder")
    return model, proxy, group


def _visible_rows(group: VideoGroupProxyModel) -> list[tuple[bool, str]]:
    """표시 행마다 그룹 머리글 여부와 표시 문자열"""
    rows: list[tuple[bool, str]] = []
    for row in range(group.rowCount()):
        text = group.index(row, 0).data(Qt.ItemDataRole.DisplayRole)
        rows.append((group.is_group_row(row), "" if text is None else str(text)))
    return rows


def _wait_for_events(predicate: Callable[[], bool], timeout_sec: float) -> None:
    """predicate가 참이 될 때까지 이벤트 루프를 돌림"""
    deadline = time.perf_counter() + timeout_sec
    while not predicate() and time.perf_counter() < deadline:
        _APP.processEvents()
        time.sleep(0.005)


class GroupFilterBatchTests(unittest.TestCase):
    """검색어 필터 한 번에 그룹을 한 번만 다시 만드는지 검증"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.evaluations = _grouped_evaluations(_ROW_COUNT, _GROUP_COUNT)
        cls.model, cls.proxy, cls.group = _make_chain(cls.evaluations)
        cls.rebuilds: list[int] = []
        cls.group.groups_rebuilt.connect(lambda: cls.rebuilds.append(1))
        cls.resets: list[int] = []
        cls.group.modelReset.connect(lambda: cls.resets.append(1))

    def setUp(self) -> None:
        self.group.expand_all_groups()
        self.proxy.set_search_text("")
        self.rebuilds.clear()
        self.resets.clear()

    def test_single_rebuild_per_search_change(self) -> None:
        """검색어 적용과 해제가 각각 그룹 재구성 한 번으로 끝나고 시간 상한 안에 드는지 검증"""
        timings: list[str] = []
        for term in ("hevc", "", "clip000"):
            self.rebuilds.clear()
            self.resets.clear()
            started = time.perf_counter()
            self.proxy.set_search_text(term)
            elapsed = time.perf_counter() - started
            timings.append(f"{term!r} {elapsed * 1000:.1f}ms rows={self.group.rowCount()}")
            self.assertEqual(
                (len(self.rebuilds), len(self.resets)),
                (1, 1),
                f"검색어 {term!r}에서 그룹 재구성이 한 번이 아님",
            )
            self.assertLess(elapsed, _FILTER_TIME_LIMIT_SEC, f"검색어 {term!r} 적용 {elapsed:.3f}s")
        print(f"\n5000행 300그룹 검색 적용: {', '.join(timings)}")

    def test_rows_after_batch_match_fresh_rebuild(self) -> None:
        """묶음이 끝난 그룹 모델의 행이 같은 필터를 처음부터 적용한 결과와 같은지 검증"""
        for term in ("hevc", "", "clip0012"):
            self.proxy.set_search_text(term)
            _model, fresh_proxy, fresh_group = _make_chain(self.evaluations)
            fresh_proxy.set_search_text(term)
            self.assertEqual(_visible_rows(self.group), _visible_rows(fresh_group))

    def test_model_is_current_right_after_batch(self) -> None:
        """묶음 종료 직후 그룹 모델이 새 결과를 담아 낡은 행을 보여 주지 않는지 검증"""
        seen: list[tuple[int, int]] = []

        def _capture() -> None:
            seen.append((len(self.resets), self.group.rowCount()))

        self.proxy.filter_batch_finished.connect(_capture)
        self.addCleanup(self.proxy.filter_batch_finished.disconnect, _capture)
        self.proxy.set_search_text("hevc")
        matched = [ev for ev in self.evaluations if ev.video.codec == "hevc"]
        expected_headers = len({ev.video.filename.split("/")[0] for ev in matched})
        self.assertEqual(
            seen,
            [(1, expected_headers + len(matched))],
            "묶음이 끝난 시점의 그룹 모델 행 수가 새 필터 결과와 다름",
        )

    def test_collapsed_groups_stay_collapsed(self) -> None:
        """검색어를 바꿔도 접어 둔 그룹이 접힌 채로 남는지 검증"""
        self.proxy.set_search_text("")
        self.group.toggle_group(0)
        self.assertTrue(self.group.is_collapsed_row(0))
        expanded_rows = self.group.rowCount()
        self.proxy.set_search_text("folder000")
        self.assertEqual(self.group.group_count, 1)
        self.assertTrue(self.group.is_collapsed_row(0))
        self.assertEqual(self.group.rowCount(), 1)
        self.assertLess(self.group.rowCount(), expanded_rows)


class FilterBarDebounceTests(unittest.TestCase):
    """검색 입력 묶기와 즉시 반영 경로 검증"""

    def setUp(self) -> None:
        self.bar = FilterTuningBar()
        self.addCleanup(self.bar.close)
        self.events: list[int] = []
        self.bar.filter_changed.connect(lambda: self.events.append(1))

    def test_rapid_typing_emits_once(self) -> None:
        """글자를 빠르게 이어 치면 입력이 멈춘 뒤 마지막 한 번만 알리는지 검증"""
        for text in ("c", "cl", "cli", "clip"):
            self.bar.search_edit.setText(text)
        self.assertEqual(self.events, [])
        _wait_for_events(lambda: bool(self.events), (SEARCH_DEBOUNCE_MS / 1000.0) * 6)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.bar.search_edit.text(), "clip")

    def test_clear_applies_immediately(self) -> None:
        """지우기는 기다리지 않고 바로 한 번 알리는지 검증"""
        self.bar.search_edit.setText("clip")
        self.events.clear()
        self.bar.search_edit.clear()
        self.assertEqual(len(self.events), 1)
        self.bar.search_edit.clear()
        self.assertEqual(len(self.events), 1)

    def test_flush_pending_applies_typed_text(self) -> None:
        """대기 중인 입력을 flush_pending으로 바로 반영하고 두 번째 호출은 아무것도 하지 않는지 검증"""
        self.bar.search_edit.setText("clip")
        self.assertEqual(self.events, [])
        self.bar.flush_pending()
        self.assertEqual(len(self.events), 1)
        self.bar.flush_pending()
        self.assertEqual(len(self.events), 1)

    def test_sync_state_cancels_pending_text(self) -> None:
        """저장값을 시그널 없이 반영하면 대기 중이던 입력 알림이 남지 않는지 검증"""
        self.bar.search_edit.setText("clip")
        self.bar.sync_filter_state(search="saved", exception_tokens=[])
        self.assertEqual(self.bar.search_edit.text(), "saved")
        self.bar.flush_pending()
        self.assertEqual(self.events, [])


class FilterBarWithGroupedTableTests(unittest.TestCase):
    """필터 바 입력이 묶인 표에 한 번만 반영되는지 검증"""

    def test_typing_applies_once_to_grouped_model(self) -> None:
        """검색창에 이어 치고 flush하면 그룹 재구성이 한 번뿐인지 검증"""
        evaluations = _grouped_evaluations(600, 40)
        _model, proxy, group = _make_chain(evaluations)
        bar = FilterTuningBar()
        self.addCleanup(bar.close)
        rebuilds: list[int] = []
        group.groups_rebuilt.connect(lambda: rebuilds.append(1))
        bar.filter_changed.connect(lambda: proxy.set_search_text(bar.search_edit.text()))
        for text in ("h", "h2", "h26", "h264"):
            bar.search_edit.setText(text)
        bar.flush_pending()
        self.assertEqual(len(rebuilds), 1)
        expected = sum(1 for ev in evaluations if ev.video.codec == "h264")
        video_rows = sum(1 for row in range(group.rowCount()) if not group.is_group_row(row))
        self.assertEqual(video_rows, expected)

    def test_source_without_batch_signals_still_rebuilds(self) -> None:
        """묶음 신호가 없는 원본을 연결해도 행 변경마다 다시 만드는지 검증"""
        source = VideoTableModel(config=ClassifierConfig())
        group = VideoGroupProxyModel()
        group.setSourceModel(source)
        group.set_group_by("folder")
        rebuilds: list[int] = []
        group.groups_rebuilt.connect(lambda: rebuilds.append(1))
        source.set_data_source(_grouped_evaluations(30, 3), root_dir=_ROOT_DIR)
        self.assertEqual(len(rebuilds), 1)
        self.assertEqual(group.group_count, 3)
        group.setSourceModel(None)
        self.assertEqual(group.rowCount(), 0)
