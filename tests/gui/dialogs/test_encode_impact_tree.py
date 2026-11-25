"""요약 탭 영상 변환 트리(encode_impact_tree)의 집계 제목, 분류 배지, 1, 2순위 펼침, 영상 행 동작과 선택 표시 검증"""

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QTreeWidgetItem, QWidget

from src.core.encode.encode_impact import (
    IMPACT_AUDIO_AAC,
    IMPACT_COVER_KEPT,
    IMPACT_CROP_BARS,
    IMPACT_DOWNSCALE_RESOLUTION,
    IMPACT_EXPLORER_TAGS_PRESERVED,
    EncodeImpactSummary,
    ImpactBucket,
)
from src.gui.dialogs import encode_impact_tree as tree_module
from src.gui.dialogs.encode_impact_tree import (
    BADGE_FONT_SCALE,
    BADGE_PAD_X_PX,
    CATEGORY_ROLE,
    CATEGORY_STREAM,
    CATEGORY_TAG,
    CATEGORY_VIDEO,
    DETAIL_COLUMN,
    NAME_COLUMN,
    EncodeImpactTree,
    _ImpactRowDelegate,
)
from src.gui.styles.icons import get_active_palette
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
# - 머리글 제목과 오른쪽 끝 배지가 한 줄에 들어가는 폭
_TREE_WIDTH_PX: int = 480
_TREE_HEIGHT_PX: int = 400


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
    """검은 여백, 해상도 축소, 태그 보존 항목과 정보 없음 하나를 가진 집계"""
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
        ImpactBucket(
            key=IMPACT_EXPLORER_TAGS_PRESERVED,
            label="탐색기 태그와 별점 보존 대상",
            filenames=("clip.mp4",),
        ),
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
        """변환 항목은 해당/전체 개수를 붙이고 영상 행의 내역은 오른쪽 칸에 두며 정보 없음은 마지막에 둠"""
        self.tree.set_summary(_summary(3), self.evaluations, self.root, "HEVC")
        self.assertEqual(self.tree.tree.topLevelItemCount(), 4)
        self.assertEqual(self._top(0).text(NAME_COLUMN), "검은 여백 잘라내기 (1/3개)")
        self.assertEqual(self._top(1).text(NAME_COLUMN), "해상도 축소 (1/3개)")
        self.assertEqual(self._top(2).text(NAME_COLUMN), "탐색기 태그와 별점 보존 대상 (1개)")
        self.assertEqual(self._top(3).text(NAME_COLUMN), "정보 없음 (1개)")
        child = self._child(0)
        self.assertEqual(child.text(NAME_COLUMN), "clip.mp4")
        self.assertEqual(child.text(DETAIL_COLUMN), "1920x1080 -> 1920x800")
        self.assertEqual(
            child.textAlignment(DETAIL_COLUMN),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
        )
        self.assertTrue(child.toolTip(NAME_COLUMN).startswith("파일명: clip.mp4"))
        self.assertIn("1920x1080 -> 1920x800", child.toolTip(NAME_COLUMN))
        self.assertEqual(child.toolTip(DETAIL_COLUMN), "1920x1080 -> 1920x800")
        self.assertIn("검은 여백", self._top(0).toolTip(NAME_COLUMN))
        self.assertEqual(self._top(3).toolTip(NAME_COLUMN), "분석 정보가 없어 집계하지 못했습니다.")

    def test_groups_carry_category_badges(self) -> None:
        """집계 항목 줄은 오른쪽 칸에 영상 변환, 스트림, 태그 분류 배지를 달고 정보 없음은 배지가 없음"""
        self.tree.set_summary(_summary(3), self.evaluations, self.root, "HEVC")
        self.assertEqual(self._top(0).data(DETAIL_COLUMN, CATEGORY_ROLE), CATEGORY_VIDEO)
        self.assertEqual(self._top(1).data(DETAIL_COLUMN, CATEGORY_ROLE), CATEGORY_VIDEO)
        self.assertEqual(self._top(2).data(DETAIL_COLUMN, CATEGORY_ROLE), CATEGORY_TAG)
        self.assertIn("태그와 별점", self._top(2).toolTip(DETAIL_COLUMN))
        self.assertFalse(self._top(3).data(DETAIL_COLUMN, CATEGORY_ROLE))
        stream_summary = EncodeImpactSummary(
            total_count=3,
            analyzed_count=3,
            unknown_filenames=(),
            buckets=(ImpactBucket(key=IMPACT_AUDIO_AAC, label="오디오 AAC로 변환", filenames=("clip.mp4",)),),
        )
        self.tree.set_summary(stream_summary, self.evaluations, self.root, "HEVC")
        self.assertEqual(self._top(0).data(DETAIL_COLUMN, CATEGORY_ROLE), CATEGORY_STREAM)

    def test_top_two_transform_groups_start_expanded_and_later_groups_stay_collapsed(self) -> None:
        """1, 2순위 영상 변환 항목만 기본으로 펼치고 나머지 항목은 접힌 채로 둠"""
        self.tree.set_summary(_summary(3), self.evaluations, self.root, "HEVC")
        self.assertTrue(self._top(0).isExpanded())
        self.assertTrue(self._top(1).isExpanded())
        self.assertFalse(self._top(2).isExpanded())
        self.assertFalse(self._top(3).isExpanded())

    def test_badge_delegate_paints_the_category_and_reserves_its_width(self) -> None:
        """머리글 줄 첫 칸이 병합한 줄 오른쪽 배지 폭을 확보하고 팔레트 색으로 알약을 그림"""
        self.tree.set_summary(_summary(3), self.evaluations, self.root, "HEVC")
        delegate = self.tree.tree.itemDelegate()
        assert isinstance(delegate, _ImpactRowDelegate)
        group = self._top(0)
        badge_index = self.tree.tree.indexFromItem(group, NAME_COLUMN)
        option = QStyleOptionViewItem()
        option.font = self.tree.tree.font()
        # - 배지가 놓일 오른쪽 끝까지 폭을 준 상태에서 그림
        option.rect = QRect(0, 0, _TREE_WIDTH_PX, 24)
        hint = delegate.sizeHint(option, badge_index)
        self.assertGreaterEqual(hint.width(), BADGE_PAD_X_PX * 2)
        pixmap = QPixmap(_TREE_WIDTH_PX, 24)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        try:
            delegate.paint(painter, option, badge_index)
        finally:
            painter.end()
        image = pixmap.toImage()
        pill_colors = {
            image.pixelColor(x, 12).name().lower()
            for x in range(_TREE_WIDTH_PX - hint.width(), _TREE_WIDTH_PX)
        }
        palette = get_active_palette()
        self.assertIn(palette.bg_elevated.lower(), pill_colors)
        detail_index = self.tree.tree.indexFromItem(group, DETAIL_COLUMN)
        self.assertFalse(delegate.sizeHint(option, detail_index).isEmpty())

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
        self.tree.tree.setCurrentItem(self._top(3).child(0))
        with mock.patch.object(tree_module.QMessageBox, "warning") as warning:
            self.tree.preview_current()
            self.tree.compare_current()
        self.assertEqual(warning.call_count, 2)

    def test_group_title_rows_span_the_first_column_with_a_badge(self) -> None:
        """집계 머리글 행이 두 열을 합쳐 제목을 자르지 않고 오른쪽 끝에 분류 배지를 그리는지 검증"""
        self._show_wide_tree()
        viewport = self.tree.tree.viewport()
        assert viewport is not None
        font = QFont(self.tree.tree.font())
        font.setPointSizeF(font.pointSizeF() * BADGE_FONT_SCALE)
        badge_width = QFontMetrics(font).horizontalAdvance(CATEGORY_TAG) + BADGE_PAD_X_PX * 2
        for index in (0, 2, 3):
            group = self._top(index)
            self.assertTrue(group.isFirstColumnSpanned())
            rect = self.tree.tree.visualItemRect(group)
            # - 병합한 줄은 첫 열만이 아니라 보이는 폭 전체를 씀
            self.assertEqual(rect.width(), viewport.width())
            title_width = QFontMetrics(self.tree.tree.font()).horizontalAdvance(group.text(NAME_COLUMN))
            self.assertLessEqual(title_width + self.tree.tree.indentation() + badge_width, rect.width())

    def test_badge_column_keeps_the_body_row_detail_width(self) -> None:
        """영상 행의 두 열 구조는 그대로이고 오른쪽 변환 내역 칸 폭이 내역 길이를 따르는지 검증"""
        self._show_wide_tree()
        child = self._child(0)
        self.assertFalse(child.isFirstColumnSpanned())
        detail_width = QFontMetrics(self.tree.tree.font()).horizontalAdvance(child.text(DETAIL_COLUMN))
        self.assertGreaterEqual(self.tree.tree.columnWidth(DETAIL_COLUMN), detail_width)

    def _show_wide_tree(self) -> None:
        """머리글 줄이 들어갈 만큼 넓게 표시한 트리에 요약을 채움"""
        self.tree.resize(_TREE_WIDTH_PX, _TREE_HEIGHT_PX)
        self.tree.show()
        self.addCleanup(self.tree.hide)
        self.tree.set_summary(_summary(3), self.evaluations, self.root, "HEVC")
        QApplication.processEvents()

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
        group = self._top(2)
        self.assertFalse(group.isExpanded())
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
