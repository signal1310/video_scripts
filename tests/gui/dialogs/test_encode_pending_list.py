"""인코딩 시작 전 대기열 목록(encode_pending_list)의 채우기, 제외 체크, 영상 행 동작, 테마 검증"""

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QLabel, QListWidget, QListWidgetItem

from src.gui.dialogs import encode_pending_list as pending_module
from src.gui.dialogs.encode_pending_list import (
    PENDING_TIP,
    PENDING_TITLE,
    PendingQueueList,
    PendingRow,
    _PendingListWidget,
)
from src.gui.styles.palettes import DARK_PALETTE
from src.gui.table.table_actions import MSG_MISSING_REFRESH

_APP = QApplication.instance() or QApplication([])


class _FakeMedia:
    """대기열 영상 행의 열기, 미리 보기, 화질 비교 요청을 기록하는 대역"""

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.previewed: list[str] = []
        self.compared: list[str] = []

    def open_file(self, path: str) -> None:
        self.opened.append(path)

    def preview_file(self, path: str) -> None:
        self.previewed.append(path)

    def compare_files(self, source: str) -> None:
        self.compared.append(source)


class PendingQueueListTests(unittest.TestCase):
    """대기열 목록이 상대 경로 행과 제외 목록을 다루고, 영상 행 동작을 미디어로 넘기는지 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.path = os.path.join(self.root, "sub", "a.mp4")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        open(self.path, "wb").close()
        self.missing = os.path.join(self.root, "gone.mp4")
        self.media = _FakeMedia()
        self.list = PendingQueueList(self.media)
        self.addCleanup(self.list.deleteLater)

    def _items(self) -> QListWidget:
        widget = self.list.findChild(QListWidget, "EncodePendingList")
        assert widget is not None
        return widget

    def _row(self, index: int) -> QListWidgetItem:
        """대기열 행 항목(없으면 테스트 실패)"""
        item = self._items().item(index)
        assert item is not None
        return item

    def _set_rows(self, rows: list[PendingRow]) -> None:
        self.list.set_rows(rows)

    def _double_click(self) -> None:
        """목록의 현재 행에 더블클릭 이벤트를 보냄"""
        self._items().mouseDoubleClickEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonDblClick,
                QPointF(0, 0),
                QPointF(0, 0),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    def _press_space(self) -> None:
        """목록의 현재 행에 스페이스바 입력을 보냄"""
        self._items().keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
        )

    def test_rows_keep_relative_paths_and_row_tooltips_with_all_checked(self) -> None:
        """행 이름은 작업 폴더 기준 상대 경로이고, 툴팁은 표 행 상세와 제외 안내이며 모두 체크됨"""
        emitted: list[bool] = []
        self.list.exclusion_changed.connect(lambda: emitted.append(True))
        rows = [
            PendingRow(self.path, "sub/a.mp4", f"파일명: sub/a.mp4\n{PENDING_TIP}"),
            PendingRow(self.missing, "gone.mp4", PENDING_TIP),
        ]
        self._set_rows(rows)
        self.assertEqual(self.list.count(), 2)
        self.assertEqual(self.list.names(), ["sub/a.mp4", "gone.mp4"])
        self.assertEqual(self.list.excluded_paths(), frozenset())
        self.assertEqual(emitted, [True])
        self.assertFalse(self._items().isHidden())
        self.assertEqual(self._row(0).toolTip(), f"파일명: sub/a.mp4\n{PENDING_TIP}")
        self.assertEqual(self._row(0).checkState(), Qt.CheckState.Checked)

    def test_unchecking_a_row_excludes_that_path(self) -> None:
        """체크를 해제한 영상만 제외 목록에 들어가고 변경 신호를 보냄"""
        self._set_rows(
            [PendingRow(self.path, "sub/a.mp4", PENDING_TIP), PendingRow(self.missing, "gone.mp4", PENDING_TIP)]
        )
        emitted: list[bool] = []
        self.list.exclusion_changed.connect(lambda: emitted.append(True))
        self._row(1).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(self.list.excluded_paths(), frozenset({self.missing}))
        self.assertEqual(emitted, [True])
        title = self.list.findChild(QLabel, "GroupHeaderTitle")
        assert title is not None
        self.assertEqual(title.text(), PENDING_TITLE)

    def test_current_path_follows_the_selected_row(self) -> None:
        """선택한 행의 절대 경로를 돌려주고, 행이 없으면 None"""
        self.assertIsNone(self.list.current_path())
        self._set_rows([PendingRow(self.path, "sub/a.mp4", PENDING_TIP)])
        self._items().setCurrentRow(0)
        self.assertEqual(self.list.current_path(), self.path)

    def test_row_actions_open_preview_and_compare_the_selected_video(self) -> None:
        """더블클릭은 재생, 스페이스바는 미리 보기, 화질 비교 단축키는 비교로 절대 경로를 넘김"""
        self._set_rows([PendingRow(self.path, "sub/a.mp4", PENDING_TIP)])
        self._items().setCurrentRow(0)
        self._press_space()
        self._items().keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
        )
        self._double_click()
        self.assertEqual(self.media.previewed, [self.path])
        self.assertEqual(self.media.compared, [self.path])
        self.assertEqual(self.media.opened, [self.path])

    def test_missing_video_shows_the_main_table_notice_and_sends_nothing(self) -> None:
        """파일이 없는 행은 메인 표와 같은 안내를 보이고 미디어 요청을 보내지 않음"""
        self._set_rows([PendingRow(self.missing, "gone.mp4", PENDING_TIP)])
        self._items().setCurrentRow(0)
        with mock.patch.object(pending_module.QMessageBox, "warning") as warning:
            self._press_space()
            self._double_click()
        self.assertEqual(warning.call_count, 2)
        self.assertEqual(warning.call_args.args[2], MSG_MISSING_REFRESH)
        self.assertEqual(self.media.previewed, [])
        self.assertEqual(self.media.opened, [])

    def test_row_keys_do_nothing_without_a_selected_row(self) -> None:
        """선택한 행이 없으면 목록 키 입력이 요청을 보내지 않음"""
        self._set_rows([PendingRow(self.path, "sub/a.mp4", PENDING_TIP)])
        list_widget = self._items()
        self.assertIsInstance(list_widget, _PendingListWidget)
        list_widget.setCurrentRow(-1)
        self._press_space()
        self.assertEqual(self.media.previewed, [])
        self.assertIsNone(self.list.current_path())

    def test_clear_hides_the_list_and_palette_sets_tokens(self) -> None:
        """대기열을 비우면 목록이 숨고, 팔레트 적용이 목록 스타일에 토큰을 반영"""
        self._set_rows([PendingRow(self.path, "sub/a.mp4", PENDING_TIP)])
        self.list.apply_palette(DARK_PALETTE)
        self.assertIn(DARK_PALETTE.bg_card, self._items().styleSheet())
        self.list.clear()
        self.assertEqual(self.list.count(), 0)
        self.assertTrue(self._items().isHidden())
        title = self.list.findChild(QLabel, "GroupHeaderTitle")
        assert title is not None
        self.assertTrue(title.isHidden())


if __name__ == "__main__":
    unittest.main(verbosity=2)
