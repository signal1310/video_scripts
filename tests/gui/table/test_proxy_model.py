"""검색과 열 필터, 다중 정렬 우선순위를 담당하는 표 프록시 모델 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.gui.table.column_filters import ColumnFilterRule
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_model import VideoTableModel
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])


class ProxyModelTests(unittest.TestCase):
    """검색, 열 필터, 정렬 우선순위, 표시 행 순회 검증"""

    def setUp(self) -> None:
        self.evals = [
            make_eval(video=make_video(filename="a.mp4", codec="h264")),
            make_eval(video=make_video(filename="b.mkv", codec="hevc")),
            make_eval(video=make_video(filename="c.mp4", codec="mpeg4")),
        ]
        self.model = VideoTableModel(config=ClassifierConfig())
        self.model.set_data_source(self.evals, root_dir="C:/work")
        self.proxy = VideoSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)

    def test_search_and_column_filters(self) -> None:
        self.proxy.set_search_text("b.mkv")
        self.assertEqual([ev.video.filename for ev in self.proxy.iter_visible_evaluations()], ["b.mkv"])
        self.proxy.set_search_text("")
        self.proxy.set_column_filter(VideoTableModel.COL_CODEC, ColumnFilterRule(selected_values=frozenset({"h264", "mpeg4"})))
        visible = {ev.video.filename for ev in self.proxy.iter_visible_evaluations()}
        self.assertEqual(visible, {"a.mp4", "c.mp4"})
        self.assertEqual(self.proxy.active_column_filter_count(), 1)
        self.assertTrue(self.proxy.is_any_filter_active())
        self.assertIn("선택:", self.proxy.get_column_filter_summaries()[VideoTableModel.COL_CODEC])
        self.proxy.clear_column_filter(VideoTableModel.COL_CODEC)
        self.assertIsNone(self.proxy.get_column_filter(VideoTableModel.COL_CODEC))
        self.proxy.set_column_filter(VideoTableModel.COL_CODEC, ColumnFilterRule(selected_values=frozenset({"h264"})))
        self.proxy.clear_all_column_filters()
        self.assertEqual(self.proxy.active_column_filter_count(), 0)

    def test_sort_stack_is_delegated_to_source_model(self) -> None:
        from PyQt6.QtCore import Qt

        self.proxy.sort(VideoTableModel.COL_NAME, Qt.SortOrder.DescendingOrder)
        self.assertEqual(self.model.sort_stack, [(VideoTableModel.COL_NAME, Qt.SortOrder.DescendingOrder)])
        self.proxy.sort(-1)
        self.assertEqual(self.model.sort_stack, [])

    def test_config_change_invalidates_filters(self) -> None:
        changes: list[bool] = []
        self.proxy.column_filters_changed.connect(lambda: changes.append(True))
        self.proxy.set_column_filter(VideoTableModel.COL_CODEC, ColumnFilterRule(selected_values=frozenset({"h264"})))
        changes.clear()
        self.model.set_config(ClassifierConfig())
        self.assertEqual(changes, [True])
        self.assertEqual(self.proxy.get_active_column_filters(), {VideoTableModel.COL_CODEC: ColumnFilterRule(selected_values=frozenset({"h264"}))})
        self.proxy.setSourceModel(None)
        self.assertEqual(self.proxy.iter_visible_evaluations(), [])


if __name__ == "__main__":
    unittest.main()
