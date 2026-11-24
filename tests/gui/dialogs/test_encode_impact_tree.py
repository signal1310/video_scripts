"""요약 탭 영상 변환 트리(encode_impact_tree)의 집계 제목, 영상 행 동작과 선택 표시 검증"""

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QTreeWidgetItem, QWidget

from src.core.encode.encode_impact import (
    IMPACT_COVER_KEPT,
    IMPACT_CROP_BARS,
    IMPACT_DOWNSCALE_RESOLUTION,
    EncodeImpactSummary,
    ImpactBucket,
)
from src.gui.dialogs import encode_impact_tree as tree_module
from src.gui.dialogs.encode_impact_tree import EncodeImpactTree, _ImpactRowDelegate
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])


class _FakeMedia:
    """트리 영상 행의 열기, 미리 보기, 비교 요청을 기록하는 대역"""

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


def _summary(total: int, filenames: tuple[str, ...] = ("clip.mp4",)) -> EncodeImpactSummary:
    """검은 여백, 해상도 축소, 커버 유지 항목과 정보 없음 하나를 가진 집계"""
    buckets = (
        ImpactBucket(
            key=IMPACT_CROP_BARS,
            label="검은 여백 잘라내기",
            filenames=filenames,
            details=tuple((name, "1920x1080 -> 1920x800") for name in filenames),
        ),
        ImpactBucket(
            key=IMPACT_DOWNSCALE_RESOLUTION,
            label="해상도 축소",
            filenames=("scale.mp4",),
            details=(("scale.mp4", "1920x800 -> 1280x532 (720p)"),),
        ),
        ImpactBucket(key=IMPACT_COVER_KEPT, label="커버 유지", filenames=()),
    )
    return EncodeImpactSummary(
        total_count=total,
        analyzed_count=total - 1,
        unknown_filenames=("broken.mp4",),
        buckets=buckets,
    )


class EncodeImpactTreeTests(unittest.TestCase):
    """집계 항목과 영상 행 구성, 스페이스바, Ctrl+B, 더블클릭, 없는 파일 안내를 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        for name in ("clip.mp4", "scale.mp4"):
            open(os.path.join(self.root, name), "wb").close()
        self.media = _FakeMedia()
        self.tree = EncodeImpactTree(self.media)
        self.addCleanup(self.tree.deleteLater)
        self.evaluations = [
            make_eval(video=make_video(filename=name)) for name in ("clip.mp4", "scale.mp4", "broken.mp4")
        ]

    def _top(self, index: int) -> QTreeWidgetItem:
        item = self.tree.tree.topLevelItem(index)
        assert item is not None
        return item

    def _child(self, index: int = 0) -> QTreeWidgetItem:
        child = self._top(0).child(index)
        assert child is not None
        return child

    def test_summary_builds_transform_titles_details_and_unknown_last(self) -> None:
        """변환 항목은 해당/전체 개수를 붙이고 영상 행에 변환 내역을 함께 쓰며 정보 없음은 마지막에 둠"""
        self.tree.set_summary(_summary(3), self.evaluations, self.root, "HEVC")
        self.assertEqual(self.tree.tree.topLevelItemCount(), 3)
        self.assertEqual(self._top(0).text(0), "검은 여백 잘라내기 (1/3개)")
        self.assertEqual(self._top(1).text(0), "해상도 축소 (1/3개)")
        self.assertEqual(self._top(2).text(0), "정보 없음 (1개)")
        child = self._child(0)
        self.assertEqual(child.text(0), "clip.mp4  1920x1080 -> 1920x800")
        self.assertTrue(child.toolTip(0).startswith("파일명: clip.mp4"))
        self.assertIn("1920x1080 -> 1920x800", child.toolTip(0))
        self.assertIn("검은 여백", self._top(0).toolTip(0))
        self.assertEqual(self._top(2).toolTip(0), "분석 정보가 없어 집계하지 못했습니다.")

    def test_space_previews_and_ctrl_b_compares_the_current_row(self) -> None:
        """스페이스바는 미리 보기, Ctrl+B는 화질 비교로 절대 경로를 넘김"""
        self.tree.set_summary(_summary(2, ("clip.mp4", "scale.mp4")), self.evaluations, self.root, "HEVC")
        self.tree.tree.setCurrentItem(self._child(1))
        self.tree.tree.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
        )
        self.tree.tree.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
        )
        self.assertEqual(self.media.compared, [os.path.join(self.root, "scale.mp4")])
        self.assertEqual(self.media.previewed, [os.path.join(self.root, "scale.mp4")])

    def test_double_click_opens_the_video_and_missing_rows_show_a_notice(self) -> None:
        """더블클릭은 기본 플레이어 열기로 넘기고, 파일이 없으면 안내를 부름"""
        self.tree.set_summary(_summary(2, ("clip.mp4",)), self.evaluations, self.root, "HEVC")
        self.tree.tree.itemDoubleClicked.emit(self._child(0), 0)
        self.assertEqual(self.media.opened, [os.path.join(self.root, "clip.mp4")])
        self.tree.tree.itemDoubleClicked.emit(self._top(1).child(0), 0)
        self.tree.tree.setCurrentItem(self._top(2).child(0))
        with mock.patch.object(tree_module.QMessageBox, "warning") as warning:
            self.tree.preview_current()
            self.tree.compare_current()
        self.assertEqual(warning.call_count, 2)

    def test_group_title_rows_do_nothing(self) -> None:
        """집계 제목 줄에서는 미리 보기와 비교를 요청하지 않음"""
        self.tree.set_summary(_summary(1), self.evaluations, self.root, "HEVC")
        self.tree.tree.setCurrentItem(self._top(0))
        self.tree.preview_current()
        self.tree.compare_current()
        self.assertEqual(self.media.previewed, [])
        self.assertEqual(self.media.compared, [])

    def test_group_row_click_toggles_once_and_delegate_hides_selection(self) -> None:
        """항목 줄 클릭은 펼침을 한 번만 바꾸고, 항목 줄은 선택 배경과 포커스 테두리를 그리지 않음"""
        self.tree.set_summary(_summary(1), self.evaluations, self.root, "HEVC")
        self.tree.show()
        self.addCleanup(self.tree.hide)
        group = self._top(0)
        viewport = self.tree.tree.viewport()
        assert viewport is not None
        center = self.tree.tree.visualItemRect(group).center()
        _left_click(viewport, self.tree.tree.mapToGlobal(center))
        self.assertTrue(group.isExpanded())
        _left_click(viewport, self.tree.tree.mapToGlobal(center))
        self.assertFalse(group.isExpanded())

        delegate = self.tree.tree.itemDelegate()
        self.assertIsInstance(delegate, _ImpactRowDelegate)
        assert isinstance(delegate, _ImpactRowDelegate)
        active = QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus
        group_option = QStyleOptionViewItem()
        group_option.state = active
        delegate.initStyleOption(group_option, self.tree.tree.indexFromItem(group))
        self.assertFalse(group_option.state & QStyle.StateFlag.State_Selected)
        child_option = QStyleOptionViewItem()
        child_option.state = active
        delegate.initStyleOption(child_option, self.tree.tree.indexFromItem(self._child(0)))
        self.assertTrue(child_option.state & QStyle.StateFlag.State_HasFocus)


def _left_click(widget: QWidget, global_pos: QPoint) -> None:
    """위젯의 지정 전역 위치에 왼쪽 누름과 놓음 이벤트를 보냄"""
    local = QPointF(widget.mapFromGlobal(global_pos))
    global_point = QPointF(global_pos)
    QApplication.sendEvent(
        widget,
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            local,
            global_point,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    QApplication.sendEvent(
        widget,
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            local,
            global_point,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
