"""표 뷰의 우클릭 메뉴와 열 필터 팝오버가 열고 닫을 때마다 자식 위젯을 쌓지 않는지 검증"""

import os
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint
from PyQt6.QtWidgets import QApplication, QMenu

from src.core.config import ClassifierConfig
from src.gui.table import table_actions
from src.gui.table.column_filter_popover import ColumnFilterPopover
from src.gui.table.table_context_menu import show_header_context_menu, show_row_context_menu
from src.gui.table.table_model import VideoTableModel
from src.gui.table.video_table_view import VideoTableView
from tests._factories import make_eval, make_video
from tests._gui_helpers import GuiTestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])
_ROUNDS: int = 5
# - 한 번에 남아 있어도 되는 최대 개수. 재사용 방식이면 1, 닫을 때 삭제 방식이면 0
_MAX_LEFT_OVER: int = 1


def _flush_deferred_deletes() -> None:
    """이벤트 루프가 처리할 지연 삭제를 테스트에서 즉시 처리"""
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class TableChildWidgetLifetimeTests(GuiTestCase):
    """반복해서 열고 닫아도 표 뷰 아래 메뉴와 팝오버가 누적되지 않는지 검증"""

    def setUp(self) -> None:
        self.view = VideoTableView()
        self.addCleanup(self.view.close)
        self.ev = make_eval()
        self.view.table_model.set_data_source([self.ev], root_dir="x", config=ClassifierConfig())
        _APP.processEvents()

    def test_evaluation_at_path_finds_the_row_regardless_of_focus(self) -> None:
        """포커스 행과 상관없이 파일 경로로 평가 결과를 찾는지 검증"""
        root = os.path.dirname(os.path.abspath(__file__))
        other = make_eval(video=make_video(filename="clip.mp4"))
        view = VideoTableView(root_dir_provider=lambda: root)
        self.addCleanup(view.close)
        view.table_model.set_data_source([other], root_dir=root, config=ClassifierConfig())
        _APP.processEvents()
        path = os.path.join(root, "clip.mp4")
        self.assertIs(view.evaluation_at_path(path), other)
        self.assertIsNone(view.evaluation_at_path(os.path.join(root, "missing.mp4")))

    def test_path_index_is_reused_until_the_model_data_changes(self) -> None:
        """같은 표 내용으로 되풀이 조회하면 색인을 다시 만들지 않고, 데이터가 바뀌면 다시 만드는지 검증"""
        root = os.path.dirname(os.path.abspath(__file__))
        first = make_eval(video=make_video(filename="a.mp4"))
        second = make_eval(video=make_video(filename="b.mp4"))
        view = VideoTableView(root_dir_provider=lambda: root)
        self.addCleanup(view.close)
        view.table_model.set_data_source([first], root_dir=root, config=ClassifierConfig())
        _APP.processEvents()
        with patch(
            "src.gui.table.video_table_view.EvaluationPathIndex",
            wraps=table_actions.EvaluationPathIndex,
        ) as builder:
            self.assertIs(view.evaluation_at_path(os.path.join(root, "a.mp4")), first)
            built = builder.call_count
            for _ in range(3):
                self.assertIs(view.evaluation_at_path(os.path.join(root, "a.mp4")), first)
            self.assertEqual(builder.call_count, built)

            view.table_model.set_data_source([second], root_dir=root, config=ClassifierConfig())
            _APP.processEvents()
            self.assertIs(view.evaluation_at_path(os.path.join(root, "b.mp4")), second)
            self.assertEqual(builder.call_count, built + 1)

    def test_context_menus_do_not_accumulate_after_repeated_opens(self) -> None:
        """헤더와 행 우클릭 메뉴를 여러 번 띄워도 표 뷰 아래 메뉴 수가 늘지 않는지 검증"""
        # - 실행을 가로채 메뉴가 닫히지 않은 상태를 재현
        with patch.object(QMenu, "exec", new=lambda menu, *_a: None):
            for _ in range(_ROUNDS):
                show_header_context_menu(self.view, QPoint(0, 0))
                show_row_context_menu(self.view, QPoint(3, 3), self.ev, [self.ev], [], [])
        _flush_deferred_deletes()
        self.assertLessEqual(len(self.view.findChildren(QMenu)), _MAX_LEFT_OVER)

    def test_column_filter_popovers_do_not_accumulate_after_repeated_opens(self) -> None:
        """열 필터 팝오버를 여러 번 열고 닫아도 표 뷰 아래 팝오버 수가 늘지 않는지 검증"""
        for _ in range(_ROUNDS):
            popover = self.view.open_column_filter_popover(VideoTableModel.COL_CODEC)
            assert popover is not None
            popover.close()
        _flush_deferred_deletes()
        self.assertLessEqual(len(self.view.findChildren(ColumnFilterPopover)), _MAX_LEFT_OVER)


if __name__ == "__main__":
    unittest.main()
