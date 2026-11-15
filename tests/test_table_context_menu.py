"""테이블 헤더, 행 컨텍스트 메뉴의 항목 구성과 호스트 호출 검증"""

import os
import unittest
from collections.abc import Callable
from unittest.mock import patch

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from src.core.config import ClassifierConfig
from src.core.models import VideoEvaluation
from src.gui.column_filters import ColumnFilterRule
from src.gui.proxy_model import VideoSortFilterProxyModel
from src.gui.table_model import VideoTableModel
from src.gui.widgets.column_filter_popover import ColumnFilterPopover
from src.gui.widgets.filter_header_view import FilterHeaderView
from src.gui.widgets.table_context_menu import (
    add_menu_action,
    show_header_context_menu,
    show_row_context_menu,
)
from tests._factories import make_eval, make_video

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])

_CODEC = VideoTableModel.COL_CODEC


class _Host:
    """TableContextHost 프로토콜을 만족하며 호출을 기록하는 테스트용 호스트"""

    def __init__(self) -> None:
        self.table_model = VideoTableModel(config=ClassifierConfig())
        self.proxy_model = VideoSortFilterProxyModel()
        self.proxy_model.setSourceModel(self.table_model)
        self.filter_header = FilterHeaderView()
        self.filter_header.setModel(self.proxy_model)
        self.filter_header.show()
        self.hidden: set[int] = set()
        self.calls: list[tuple[str, object]] = []
        self.viewport_widget = QWidget()

    def isColumnHidden(self, column: int, /) -> bool:
        return column in self.hidden

    def setColumnHidden(self, column: int, hide: bool, /) -> None:
        self.calls.append(("hide", (column, hide)))
        (self.hidden.add if hide else self.hidden.discard)(column)

    def open_column_filter_popover(self, col: int, anchor_global: QPoint | None = None) -> ColumnFilterPopover | None:
        self.calls.append(("popover", col))
        return None

    def reset_table_sort(self) -> None:
        self.calls.append(("reset_sort", None))

    def reset_columns(self) -> None:
        self.calls.append(("reset_columns", None))

    def play_selected(self, evals: list[VideoEvaluation] | None = None) -> int:
        self.calls.append(("play", evals))
        return 0

    def reveal_selected(self, target: VideoEvaluation | None = None) -> bool:
        self.calls.append(("reveal", target))
        return True

    def copy_selected_names(self, evals: list[VideoEvaluation] | None = None) -> int:
        self.calls.append(("names", evals))
        return 0

    def copy_selected_paths(self, evals: list[VideoEvaluation] | None = None) -> int:
        self.calls.append(("paths", evals))
        return 0

    def add_selected_to_exceptions(self, evals: list[VideoEvaluation] | None = None) -> int:
        self.calls.append(("add_exc", evals))
        return 0

    def remove_selected_from_exceptions(self, evals: list[VideoEvaluation] | None = None) -> int:
        self.calls.append(("remove_exc", evals))
        return 0

    def viewport(self) -> QWidget | None:
        return self.viewport_widget


_LIVE_MENUS: list[QMenu] = []


def _capture(show: Callable[[], None]) -> dict[str, QAction]:
    """메뉴 실행(exec)을 가로채 표시된 항목을 텍스트 기준 사전으로 반환

    부모 없는 메뉴가 반환 직후 해제되지 않도록 다음 캡처 전까지 참조를 유지
    """
    captured: list[QMenu] = []
    with patch.object(QMenu, "exec", new=lambda menu, *_a: captured.append(menu)):
        show()
    assert len(captured) == 1
    _LIVE_MENUS.append(captured[0])
    return {action.text(): action for action in captured[0].actions() if not action.isSeparator()}


class TestHeaderContextMenu(unittest.TestCase):
    """헤더 우클릭 메뉴의 열 필터, 열 표시 토글, 복원 항목을 검증"""

    def setUp(self) -> None:
        """2500px 폭 헤더와 코덱 열 위 클릭 좌표를 준비"""
        self.host = _Host()
        self.addCleanup(self.host.filter_header.close)
        self.host.filter_header.resize(2500, 30)
        _APP.processEvents()
        header = self.host.filter_header
        self.codec_pos = QPoint(header.sectionViewportPosition(_CODEC) + 5, 5)

    def _header_menu(self, pos: QPoint | None = None) -> dict[str, QAction]:
        return _capture(lambda: show_header_context_menu(self.host, pos or self.codec_pos))

    def test_filter_entry_targets_clicked_column_and_clear_entries_need_active_filters(self) -> None:
        """클릭한 열의 필터 항목이 호스트 팝오버를 열고, 해제 항목은 필터가 있을 때만 나타남을 검증"""
        title = VideoTableModel.HEADERS[_CODEC]
        actions = self._header_menu()
        self.assertNotIn(f"'{title}' 열 필터 해제", actions)
        self.assertNotIn("모든 열 필터 초기화", actions)
        actions[f"'{title}' 열 필터..."].trigger()
        self.assertEqual(self.host.calls, [("popover", _CODEC)])

        self.host.proxy_model.set_column_filter(_CODEC, ColumnFilterRule(selected_values=frozenset({"h264"})))
        actions = self._header_menu()
        actions[f"'{title}' 열 필터 해제"].trigger()
        self.assertIsNone(self.host.proxy_model.get_column_filter(_CODEC))

        self.host.proxy_model.set_column_filter(_CODEC, ColumnFilterRule(selected_values=frozenset({"h264"})))
        self._header_menu()["모든 열 필터 초기화"].trigger()
        self.assertEqual(self.host.proxy_model.active_column_filter_count(), 0)

    def test_first_column_click_still_offers_its_filter(self) -> None:
        """첫 번째 열(이름)을 클릭해도 해당 열 필터 항목이 나옴을 검증"""
        actions = self._header_menu(QPoint(5, 5))
        self.assertIn(f"'{VideoTableModel.HEADERS[0]}' 열 필터...", actions)

    def test_click_outside_any_column_omits_filter_entries(self) -> None:
        """열 밖을 클릭하면 열 필터 항목 없이 표시 토글과 복원 항목만 나옴을 검증"""
        actions = self._header_menu(QPoint(5000, 5))
        self.assertFalse([text for text in actions if "열 필터" in text])
        self.assertIn("기본 정렬로 복원", actions)

    def test_column_toggles_follow_visibility_and_protect_name_and_last_column(self) -> None:
        """열마다 체크 토글이 제공되고, 이름 열과 마지막 남은 표시 열은 끌 수 없으며, 토글이 호스트에 전달됨을 검증"""
        actions = self._header_menu()
        titles = VideoTableModel.MENU_HEADERS
        self.assertTrue(all(actions[title].isCheckable() and actions[title].isChecked() for title in titles))
        self.assertFalse(actions[titles[VideoTableModel.COL_NAME]].isEnabled())
        self.assertTrue(actions[titles[_CODEC]].isEnabled())

        actions[titles[_CODEC]].trigger()
        self.assertEqual(self.host.calls[-1], ("hide", (_CODEC, True)))
        self.assertFalse(self._header_menu()[titles[_CODEC]].isChecked())

        self.host.hidden = {col for col in range(len(titles)) if col != _CODEC}
        only_visible = self._header_menu()[titles[_CODEC]]
        self.assertFalse(only_visible.isEnabled())

    def test_reset_sort_is_enabled_only_when_sorted_and_reset_columns_always(self) -> None:
        """정렬 복원은 정렬 상태가 있을 때만 활성화되고, 열 구성 복원은 항상 호출 가능함을 검증"""
        actions = self._header_menu()
        self.assertFalse(actions["기본 정렬로 복원"].isEnabled())
        self.host.proxy_model.sort(VideoTableModel.COL_SIZE, Qt.SortOrder.DescendingOrder)
        actions = self._header_menu()
        self.assertTrue(actions["기본 정렬로 복원"].isEnabled())

        actions["기본 정렬로 복원"].trigger()
        actions["기본 열 너비, 표시, 순서 복원"].trigger()
        self.assertEqual(self.host.calls, [("reset_sort", None), ("reset_columns", None)])


class TestRowContextMenu(unittest.TestCase):
    """행 우클릭 메뉴의 단수, 복수 문구와 호스트 호출을 검증"""

    def setUp(self) -> None:
        """호스트와 클릭한 행, 다른 행 평가를 준비"""
        self.host = _Host()
        self.addCleanup(self.host.filter_header.close)
        self.clicked = make_eval(make_video(filename="clicked.mp4"))
        self.other = make_eval(make_video(filename="other.mp4"))

    def _row_menu(self, targets: list[VideoEvaluation], *, to_add: list[str], to_remove: list[str]) -> dict[str, QAction]:
        return _capture(lambda: show_row_context_menu(self.host, QPoint(3, 3), self.clicked, targets, to_add, to_remove))

    def test_single_target_uses_singular_labels_and_forwards_calls(self) -> None:
        """대상이 1개면 단수 문구를 쓰고 각 항목이 대상 목록, 클릭한 행을 호스트에 전달함을 검증"""
        targets = [self.clicked]
        actions = self._row_menu(targets, to_add=["clicked"], to_remove=[])
        self.assertEqual(
            list(actions),
            [
                "기본 플레이어로 재생",
                "탐색기에서 위치 열기",
                "파일명 클립보드 복사",
                "전체 경로 클립보드 복사",
                "이 파일을 예외 키워드에 추가 (원본 보존)",
            ],
        )
        for action in actions.values():
            action.trigger()
        self.assertEqual(
            self.host.calls,
            [("play", targets), ("reveal", self.clicked), ("names", targets), ("paths", targets), ("add_exc", targets)],
        )

    def test_multiple_targets_use_counts_and_removal_entry(self) -> None:
        """대상이 여러 개면 개수를 문구에 넣고, 제거할 키워드가 있을 때만 제거 항목이 나타남을 검증"""
        targets = [self.clicked, self.other]
        actions = self._row_menu(targets, to_add=["a", "b"], to_remove=["c", "d", "e"])
        self.assertEqual(
            list(actions),
            [
                "선택한 2개 영상 재생",
                "탐색기에서 위치 열기",
                "선택한 2개 파일명 클립보드 복사",
                "선택한 2개 전체 경로 클립보드 복사",
                "선택한 2개 파일을 예외 키워드에 추가",
                "선택한 3개 파일을 예외 키워드에서 제거",
            ],
        )
        actions["선택한 3개 파일을 예외 키워드에서 제거"].trigger()
        self.assertEqual(self.host.calls, [("remove_exc", targets)])

        only_remove = self._row_menu([self.clicked], to_add=[], to_remove=["c"])
        self.assertIn("이 파일을 예외 키워드에서 제거", only_remove)
        self.assertFalse([text for text in only_remove if "추가" in text])

    def test_menu_is_not_shown_without_viewport(self) -> None:
        """뷰포트가 없으면 메뉴를 띄우지 않음을 검증"""
        self.host.viewport = lambda: None
        with patch.object(QMenu, "exec") as exec_mock:
            show_row_context_menu(self.host, QPoint(0, 0), self.clicked, [self.clicked], [], [])
        exec_mock.assert_not_called()


class TestAddMenuAction(unittest.TestCase):
    """add_menu_action의 아이콘, 활성 상태, 슬롯 연결을 검증"""

    def test_action_enabled_state_icon_and_slot(self) -> None:
        """팔레트와 아이콘 이름이 모두 있어야 아이콘이 붙고, 슬롯은 체크 인자 없이 호출됨을 검증"""
        from src.gui.styles.icons import get_active_palette

        menu = QMenu()
        self.addCleanup(menu.close)
        calls: list[str] = []
        with_icon = add_menu_action(menu, "재생", lambda: calls.append("play"), icon_name="play", palette=get_active_palette())
        no_palette = add_menu_action(menu, "복사", lambda: calls.append("copy"), icon_name="copy")
        disabled = add_menu_action(menu, "불가", lambda: calls.append("never"), enabled=False)

        self.assertFalse(with_icon.icon().isNull())
        self.assertTrue(no_palette.icon().isNull())
        self.assertFalse(disabled.isEnabled())
        with_icon.trigger()
        no_palette.trigger()
        self.assertEqual(calls, ["play", "copy"])
        self.assertEqual(menu.actions(), [with_icon, no_palette, disabled])


if __name__ == "__main__":
    unittest.main()
