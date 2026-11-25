"""활성 화면 직접 편집 세션의 마우스 끌기, 취소, 되돌리기, 메뉴 요청 검증"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest import mock

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QContextMenuEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from src.core.models import ActiveFrame
from src.gui.preview.crop_edit_session import (
    CROP_FULL_TIP,
    CROP_RESET_TIP,
    UNDO_DONE_NOTICE,
    CropEditSession,
)
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])
_VIDEO_W = 1000
_VIDEO_H = 1000


def _mouse(
    event_type: QEvent.Type,
    point: QPoint,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
) -> QMouseEvent:
    """위젯 좌표 누름/이동/뗌 마우스 사건"""
    return QMouseEvent(
        event_type,
        QPointF(point),
        QPointF(point),
        button,
        button if event_type != QEvent.Type.MouseMove else Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _press(video: QWidget, point: QPoint) -> None:
    QApplication.sendEvent(video, _mouse(QEvent.Type.MouseButtonPress, point))


def _move(video: QWidget, point: QPoint) -> None:
    QApplication.sendEvent(video, _mouse(QEvent.Type.MouseMove, point))


def _release(video: QWidget, point: QPoint) -> None:
    QApplication.sendEvent(video, _mouse(QEvent.Type.MouseButtonRelease, point))


class CropEditSessionTests(GuiTestCase):
    """세션 상태와 저장 요청"""

    def setUp(self) -> None:
        self.video = QWidget()
        self.video.resize(_VIDEO_W, _VIDEO_H)
        self.addCleanup(self.video.deleteLater)
        self.session = CropEditSession(self.video, self.video)
        self.commits: list[ActiveFrame] = []
        self.undos: list[ActiveFrame | None] = []
        self.resets: list[None] = []
        self.fulls: list[None] = []
        self.session.commit_requested.connect(self.commits.append)
        self.session.undo_requested.connect(self.undos.append)
        self.session.reset_auto_requested.connect(lambda: self.resets.append(None))
        self.session.keep_full_requested.connect(lambda: self.fulls.append(None))
        self.session.set_video("a.mp4", (_VIDEO_W, _VIDEO_H), ActiveFrame(400, 400, 100, 100))
        self.session.set_enabled(True)

    def test_frame_starts_from_the_given_value(self) -> None:
        self.assertEqual(self.session.frame, ActiveFrame(400, 400, 100, 100))

    def test_no_frame_given_starts_from_the_full_frame(self) -> None:
        self.session.set_video("b.mp4", (_VIDEO_W, _VIDEO_H), None)
        self.assertEqual(self.session.frame, ActiveFrame(_VIDEO_W, _VIDEO_H, 0, 0))

    def test_dragging_the_right_edge_emits_a_commit_on_release(self) -> None:
        _press(self.video, QPoint(500, 300))
        _move(self.video, QPoint(560, 300))
        self.assertTrue(self.session.is_dragging)
        _release(self.video, QPoint(560, 300))
        self.assertFalse(self.session.is_dragging)
        self.assertEqual(len(self.commits), 1)
        self.assertGreater(self.commits[0].width, 400)

    def test_pressing_inside_moves_the_whole_frame(self) -> None:
        _press(self.video, QPoint(300, 300))
        _move(self.video, QPoint(340, 320))
        _release(self.video, QPoint(340, 320))
        self.assertEqual(self.commits[0], ActiveFrame(400, 400, 140, 120))

    def test_pressing_outside_the_rectangle_does_not_drag(self) -> None:
        _press(self.video, QPoint(10, 10))
        self.assertFalse(self.session.is_dragging)
        _release(self.video, QPoint(10, 10))
        self.assertEqual(self.commits, [])

    def test_click_without_movement_does_not_commit(self) -> None:
        _press(self.video, QPoint(300, 300))
        _release(self.video, QPoint(300, 300))
        self.assertEqual(self.commits, [])


    def test_escape_cancels_the_drag_without_saving(self) -> None:
        _press(self.video, QPoint(500, 300))
        _move(self.video, QPoint(560, 300))
        self.assertTrue(self.session.cancel_drag())
        self.assertFalse(self.session.is_dragging)
        self.assertEqual(self.session.frame, ActiveFrame(400, 400, 100, 100))
        self.assertEqual(self.commits, [])
        self.assertFalse(self.session.cancel_drag())

    def test_escape_without_a_drag_does_nothing(self) -> None:
        self.assertFalse(self.session.cancel_drag())

    def test_undo_restores_the_previous_frame_and_requests_a_save(self) -> None:
        _press(self.video, QPoint(500, 300))
        _move(self.video, QPoint(560, 300))
        _release(self.video, QPoint(560, 300))
        self.assertTrue(self.session.has_undo())
        self.assertTrue(self.session.undo())
        self.assertEqual(self.undos, [ActiveFrame(400, 400, 100, 100)])
        self.assertEqual(self.session.frame, ActiveFrame(400, 400, 100, 100))
        self.assertFalse(self.session.undo())

    def test_reset_auto_pushes_the_previous_value(self) -> None:
        self.session.request_reset_auto()
        self.assertEqual(self.resets, [None])
        self.assertTrue(self.session.has_undo())

    def test_keep_full_requests_exclusion_and_shows_the_full_frame(self) -> None:
        self.session.request_keep_full()
        self.assertEqual(self.fulls, [None])
        self.assertEqual(self.session.frame, ActiveFrame(_VIDEO_W, _VIDEO_H, 0, 0))

    def test_switching_videos_clears_the_undo_stack(self) -> None:
        self.session.request_reset_auto()
        self.session.set_video("c.mp4", (_VIDEO_W, _VIDEO_H), None)
        self.assertFalse(self.session.has_undo())

    def test_table_value_change_updates_the_display(self) -> None:
        self.session.set_video("a.mp4", (_VIDEO_W, _VIDEO_H), ActiveFrame(600, 600, 200, 200))
        self.assertEqual(self.session.frame, ActiveFrame(600, 600, 200, 200))

    def test_saved_value_is_not_overwritten_by_the_same_table_value(self) -> None:
        _press(self.video, QPoint(500, 300))
        _move(self.video, QPoint(560, 300))
        _release(self.video, QPoint(560, 300))
        self.session.set_video("a.mp4", (_VIDEO_W, _VIDEO_H), ActiveFrame(400, 400, 100, 100))
        self.assertEqual(self.session.frame, self.commits[0])

    def test_disabled_session_ignores_mouse_events(self) -> None:
        self.session.set_enabled(False)
        _press(self.video, QPoint(500, 300))
        self.assertFalse(self.session.is_dragging)

    def test_rects_are_none_without_a_video(self) -> None:
        session = CropEditSession(QWidget(), self.video)
        self.addCleanup(session.deleteLater)
        self.assertIsNone(session.rects())

    def test_context_menu_offers_reset_and_keep_full(self) -> None:
        """우클릭 메뉴가 두 요청을 실행하고 메뉴를 띄우는지 검증"""
        labels: list[str] = []

        def _fake_exec(menu: QMenu, _pos: object) -> None:
            actions = menu.actions()
            labels.extend(action.text() for action in actions)
            actions[0].trigger()
            actions[1].trigger()

        with mock.patch.object(QMenu, "exec", _fake_exec):
            QApplication.sendEvent(
                self.video,
                QContextMenuEvent(
                    QContextMenuEvent.Reason.Mouse,
                    QPoint(10, 10),
                    self.video.mapToGlobal(QPoint(10, 10)),
                ),
            )
        self.assertEqual(labels, ["자동 감지로 되돌리기", "전체 화면 유지"])
        self.assertEqual(self.resets, [None])
        self.assertEqual(self.fulls, [None])

    def test_tips_and_notice_are_standard_sentences(self) -> None:
        self.assertEqual(CROP_RESET_TIP, "자동으로 감지한 활성 화면으로 되돌립니다.")
        self.assertEqual(CROP_FULL_TIP, "이 영상은 잘라내지 않고 전체 화면을 유지합니다.")
        self.assertEqual(UNDO_DONE_NOTICE, "직접 지정한 활성 화면을 되돌렸습니다.")
