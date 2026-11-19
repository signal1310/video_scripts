"""표 정렬을 원본 모델의 파이썬 정렬로 옮긴 뒤의 순서, 안정성, 선택 유지를 검증하는 GUI 테스트"""

import os
import unittest

from PyQt6.QtCore import QPersistentModelIndex, Qt
from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.evaluation.evaluator import VideoEvaluator
from src.core.models import VideoEvaluation
from src.core.sorting import natural_sort_key
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table_model import VideoTableModel
from tests._factories import make_video

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def _evaluations(names: list[str]) -> list[VideoEvaluation]:
    videos = [make_video(filename=name) for name in names]
    return VideoEvaluator.evaluate_all("C:/work", videos, ClassifierConfig()).evaluations


def _visible_names(proxy: VideoSortFilterProxyModel) -> list[str]:
    return [
        proxy.index(row, VideoTableModel.COL_NAME).data(Qt.ItemDataRole.DisplayRole)
        for row in range(proxy.rowCount())
    ]


class TestPythonSortOrder(unittest.TestCase):
    """단일 정렬이 자연 정렬과 같은 순서를 내고 정렬 해제 시 적재 순서로 돌아가는지 검증"""

    def setUp(self) -> None:
        self.names = ["clip10.mp4", "clip2.mp4", "Clip1.mp4", "clip1.mp4", "a.mp4"]
        self.model = VideoTableModel()
        self.model.set_data_source(_evaluations(self.names), root_dir="C:/work")
        self.proxy = VideoSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)

    def test_ascending_name_sort_matches_natural_order(self) -> None:
        """이름 열 오름차순이 자연 정렬 기준과 같은 순서인지 확인"""
        self.proxy.sort(VideoTableModel.COL_NAME, Qt.SortOrder.AscendingOrder)
        expected = sorted(self.names, key=lambda n: natural_sort_key(n))
        self.assertEqual([ev.video.filename for ev in self.model.evaluations], expected)
        self.assertEqual(_visible_names(self.proxy), expected)

    def test_descending_name_sort_keeps_load_order_for_ties(self) -> None:
        """이름 열 내림차순이 자연 정렬 역순이며 동률(대소문자만 다른 이름)은 적재 순서를 유지하는지 확인"""
        self.proxy.sort(VideoTableModel.COL_NAME, Qt.SortOrder.DescendingOrder)
        expected = sorted(self.names, key=lambda n: natural_sort_key(n), reverse=True)
        self.assertEqual(_visible_names(self.proxy), expected)

    def test_clear_sort_restores_load_order(self) -> None:
        """정렬 해제 후 표시 순서가 분석 결과의 적재 순서로 돌아가는지 확인"""
        self.proxy.sort(VideoTableModel.COL_NAME, Qt.SortOrder.DescendingOrder)
        self.proxy.sort(-1)
        self.assertEqual(_visible_names(self.proxy), self.names)
        self.assertEqual(self.proxy.sort_stack, [])


class TestMultiColumnStability(unittest.TestCase):
    """다중 열 정렬이 주 정렬 뒤 보조 정렬을 따르고, 동률은 적재 순서를 유지하는지 검증"""

    def test_equal_keys_keep_load_order_across_sort_passes(self) -> None:
        """동률 행 두 개가 적재 순서대로 남고, 보조 열 정렬을 더해도 같은 순서를 유지하는지 확인"""
        model = VideoTableModel()
        first, second, other = _evaluations(["same.mp4", "same.mp4", "other.mp4"])
        model.set_data_source([first, second, other], root_dir="C:/work")
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.sort(VideoTableModel.COL_NAME, Qt.SortOrder.AscendingOrder)
        expected = [other, first, second]
        self.assertTrue(all(a is b for a, b in zip(model.evaluations, expected, strict=True)))
        proxy.sort(VideoTableModel.COL_SIZE, Qt.SortOrder.AscendingOrder)
        self.assertEqual(
            proxy.sort_stack,
            [
                (VideoTableModel.COL_SIZE, Qt.SortOrder.AscendingOrder),
                (VideoTableModel.COL_NAME, Qt.SortOrder.AscendingOrder),
            ],
        )
        self.assertTrue(all(a is b for a, b in zip(model.evaluations, expected, strict=True)))


class TestSelectionFollowsRow(unittest.TestCase):
    """정렬이 바뀌어도 선택한 영상의 영구 인덱스가 같은 영상을 가리키는지 검증"""

    def test_persistent_index_follows_same_evaluation(self) -> None:
        """정렬 뒤에도 선택 인덱스가 원래 선택한 영상 행을 가리키는지 확인"""
        model = VideoTableModel()
        model.set_data_source(_evaluations(["c.mp4", "a.mp4", "b.mp4"]), root_dir="C:/work")
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        target_row = 0
        persistent = QPersistentModelIndex(proxy.index(target_row, VideoTableModel.COL_NAME))
        selected_name = persistent.data(Qt.ItemDataRole.DisplayRole)
        proxy.sort(VideoTableModel.COL_NAME, Qt.SortOrder.AscendingOrder)
        self.assertTrue(persistent.isValid())
        self.assertEqual(persistent.data(Qt.ItemDataRole.DisplayRole), selected_name)


if __name__ == "__main__":
    unittest.main()
