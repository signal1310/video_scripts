"""테이블 행 외부 파일 드래그, OS 커서 전용 드래그, 선택 보존 및 외부 변경 재동기화를 검증"""

import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QItemSelectionModel, QMimeData, QPoint, Qt, QUrl
from PyQt6.QtGui import QDrag, QDragEnterEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from src.classifier import VideoClassifier
from src.core.analysis.file_manager import compute_dir_signature
from src.gui.external_resync import ACTION_ANALYZE_FULL, plan_external_resync
from src.gui.main_window import MainWindow
from src.gui.widgets.table_drag import (
    start_table_file_drag,
)
from src.gui.widgets.video_table_view import VideoTableView
from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache
from tests._factories import make_eval, make_video
from tests._gui_helpers import GuiTestCase, isolate_app_state
from tests._sandbox import reset_sandbox_files

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def _mouse_event(
    etype: QEvent.Type,
    pos: QPoint,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
    buttons: Qt.MouseButton = Qt.MouseButton.LeftButton,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> QMouseEvent:
    posf = pos.toPointF()
    return QMouseEvent(etype, posf, posf, button, buttons, modifiers)


def _send_mouse(
    target: QWidget,
    etype: QEvent.Type,
    pos: QPoint,
    btns: Qt.MouseButton = Qt.MouseButton.LeftButton,
) -> None:
    posf = pos.toPointF()
    ev = QMouseEvent(
        etype, posf, target.mapToGlobal(posf), Qt.MouseButton.LeftButton, btns, Qt.KeyboardModifier.NoModifier
    )
    QApplication.sendEvent(target, ev)


class TestTableExternalDrag(GuiTestCase):
    """테이블 행 선택 후 외부 파일 드래그 MIME 생성, 배지 픽스맵, 선택 유지, 창 재동기화를 검증"""

    def setUp(self) -> None:
        reset_sandbox_files()
        self.addCleanup(reset_sandbox_files)

    def test_start_table_file_drag_uses_copy_action_without_floating_pixmap(self) -> None:
        """외부 드래그는 별도 드래그 픽스맵 창을 띄우지 않고 CopyAction만 허용해 실행됨을 검증"""
        view = VideoTableView()
        self.addCleanup(view.close)
        captured: list[tuple[Qt.DropAction, bool, list[str]]] = []

        def fake_exec(drag_self: QDrag, actions: Qt.DropAction) -> Qt.DropAction:
            mime = drag_self.mimeData()
            urls = [os.path.normpath(u.toLocalFile()) for u in mime.urls()] if mime is not None else []
            captured.append((actions, drag_self.pixmap().isNull(), urls))
            return Qt.DropAction.CopyAction

        with patch.object(QDrag, "exec", fake_exec):
            result = start_table_file_drag(view, ["C:/v/alpha.mp4", "C:/v/beta.mp4"])

        self.assertEqual(result, Qt.DropAction.CopyAction)
        self.assertEqual(
            captured,
            [
                (
                    Qt.DropAction.CopyAction,
                    True,
                    [os.path.normpath("C:/v/alpha.mp4"), os.path.normpath("C:/v/beta.mp4")],
                )
            ],
        )

    def test_dragging_selected_rows_keeps_multi_selection_and_uses_copy_action(self) -> None:
        """이미 선택된 다중 행 위에서 끌면 선택 전체가 유지된 채 외부 드래그가 시작되고, 단순 클릭 놓기는 단일 선택됨을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name in ("one.mp4", "two.mp4", "three.mp4"):
                p = os.path.join(tmp, name)
                with open(p, "wb") as f:
                    f.write(b"video")
                paths.append(os.path.normpath(p))

            view = VideoTableView(root_dir_provider=lambda: tmp)
            self.addCleanup(view.close)
            view.resize(600, 300)
            view.table_model.set_data_source(
                [
                    make_eval(make_video(filename="one.mp4")),
                    make_eval(make_video(filename="two.mp4")),
                    make_eval(make_video(filename="three.mp4")),
                ],
                root_dir=tmp,
            )
            view.show()
            QApplication.processEvents()
            view.selectAll()

            row0_center = view.visualRect(view.proxy_model.index(0, 0)).center()
            drag_end = row0_center + QPoint(QApplication.startDragDistance() + 8, 4)

            dragged_batches: list[list[str]] = []
            finished_signals: list[list[object]] = []
            view.external_drag_finished.connect(finished_signals.append)

            with patch(
                "src.gui.widgets.video_table_view.start_table_file_drag",
                side_effect=lambda _w, batch: dragged_batches.append(list(batch))
                or Qt.DropAction.CopyAction,
            ):
                view.mousePressEvent(
                    _mouse_event(QEvent.Type.MouseButtonPress, row0_center)
                )
                view.mouseMoveEvent(
                    _mouse_event(QEvent.Type.MouseMove, drag_end)
                )

            self.assertEqual(dragged_batches, [paths])
            self.assertEqual(finished_signals, [paths])

            # 끌지 않고 클릭만 하고 놓으면 해당 행만 단일 선택으로 전환
            row1_center = view.visualRect(view.proxy_model.index(1, 0)).center()
            view.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, row1_center))
            view.mouseReleaseEvent(
                _mouse_event(
                    QEvent.Type.MouseButtonRelease,
                    row1_center,
                    buttons=Qt.MouseButton.NoButton,
                )
            )
            self.assertEqual(view.get_selected_filenames(), {"two.mp4"})

    def test_dragging_row_with_empty_selection_selects_and_starts_external_drag(self) -> None:
        """아무 행도 선택되지 않은 상태에서 임의 행을 눌러 끌면 그 행이 선택되고 바로 외부 드래그가 시작됨을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            p0_path = os.path.normpath(os.path.join(tmp, "one.mp4"))
            with open(p0_path, "wb") as f:
                f.write(b"video")
            with open(os.path.join(tmp, "two.mp4"), "wb") as f:
                f.write(b"video")
            view = VideoTableView(root_dir_provider=lambda: tmp)
            self.addCleanup(view.close)
            view.resize(600, 300)
            view.table_model.set_data_source(
                [
                    make_eval(make_video(filename="one.mp4")),
                    make_eval(make_video(filename="two.mp4")),
                ],
                root_dir=tmp,
            )
            view.show()
            QApplication.processEvents()

            p0 = view.visualRect(view.proxy_model.index(0, 0)).center()
            drag_end = p0 + QPoint(QApplication.startDragDistance() + 8, 2)
            dragged: list[list[str]] = []
            with patch(
                "src.gui.widgets.video_table_view.start_table_file_drag",
                side_effect=lambda _w, batch: dragged.append(list(batch)) or Qt.DropAction.CopyAction,
            ):
                view.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, p0))
                self.assertEqual(view.get_selected_filenames(), {"one.mp4"})
                view.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, drag_end))
            self.assertEqual(dragged, [[p0_path]])

    def test_dragging_unselected_row_when_other_selected_switches_and_starts_external_drag(self) -> None:
        """다른 행이 선택된 상태에서 선택 안 된 행을 눌러 끌면 기존 선택을 비우고 그 행만 즉시 외부 드래그함을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            p0_path = os.path.normpath(os.path.join(tmp, "one.mp4"))
            with open(p0_path, "wb") as f:
                f.write(b"video")
            with open(os.path.join(tmp, "two.mp4"), "wb") as f:
                f.write(b"video")
            view = VideoTableView(root_dir_provider=lambda: tmp)
            self.addCleanup(view.close)
            view.resize(600, 300)
            view.table_model.set_data_source(
                [
                    make_eval(make_video(filename="one.mp4")),
                    make_eval(make_video(filename="two.mp4")),
                ],
                root_dir=tmp,
            )
            view.show()
            QApplication.processEvents()

            view.select_filenames({"two.mp4"})
            p0 = view.visualRect(view.proxy_model.index(0, 0)).center()
            drag_end = p0 + QPoint(QApplication.startDragDistance() + 8, 2)
            dragged: list[list[str]] = []
            with patch(
                "src.gui.widgets.video_table_view.start_table_file_drag",
                side_effect=lambda _w, batch: dragged.append(list(batch)) or Qt.DropAction.CopyAction,
            ):
                view.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, p0))
                self.assertEqual(view.get_selected_filenames(), {"one.mp4"})
                view.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, drag_end))
            self.assertEqual(dragged, [[p0_path]])

    def test_empty_area_click_clears_selection_without_drag(self) -> None:
        """테이블 안 빈 여백을 클릭하면 선택이 해제되고 드래그는 시작되지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            names = ("one.mp4", "two.mp4", "three.mp4")
            for name in names:
                with open(os.path.join(tmp, name), "wb") as f:
                    f.write(b"video")
            view = VideoTableView(root_dir_provider=lambda: tmp)
            self.addCleanup(view.close)
            view.resize(600, 400)
            view.table_model.set_data_source(
                [make_eval(make_video(filename=name)) for name in names],
                root_dir=tmp,
            )
            view.show()
            QApplication.processEvents()

            vp = view.viewport()
            assert vp is not None
            empty_pt = QPoint(80, 300)

            view.selectAll()
            with patch("src.gui.widgets.video_table_view.start_table_file_drag") as drag_mock:
                _send_mouse(vp, QEvent.Type.MouseButtonPress, empty_pt)
                _send_mouse(vp, QEvent.Type.MouseButtonRelease, empty_pt, Qt.MouseButton.NoButton)
            drag_mock.assert_not_called()
            self.assertEqual(view.get_selected_filenames(), set())

    def test_restore_previous_selection_recovers_lost_multi_selection_separately_from_undo(self) -> None:
        """다중 선택이 단일 클릭이나 빈 공간 클릭으로 해제되면 전용 명령과 Alt+Z로 복원되고, Ctrl+Z 스택은 분리됨을 검증"""
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        names = ["a.mp4", "b.mp4", "c.mp4", "d.mp4"]
        for name in names:
            with open(os.path.join(work.name, name), "wb") as f:
                f.write(b"video")

        clf = VideoClassifier(root_dir=work.name)
        DEFAULT_CACHE_REGISTRY.put(
            work.name,
            TableCache(work.name, [make_video(filename=name) for name in names], include_keyframe=True),
        )
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.show()
        mw.apply_report(clf.get_summary_report(ensure_keyframe=False))
        QApplication.processEvents()

        self.assertFalse(mw.menu.act_restore_selection.isEnabled())
        self.assertFalse(mw.menu.act_redo_selection.isEnabled())
        mw.table_view.select_filenames({"a.mp4", "b.mp4", "c.mp4"})
        mw.table_view.clearSelection()
        self.assertTrue(mw.menu.act_restore_selection.isEnabled())
        self.assertFalse(mw.menu.act_redo_selection.isEnabled())
        self.assertFalse(mw.menu.act_undo.isEnabled())

        mw.menu.act_restore_selection.trigger()
        self.assertEqual(mw.table_view.get_selected_filenames(), {"a.mp4", "b.mp4", "c.mp4"})
        self.assertFalse(mw.menu.act_restore_selection.isEnabled())
        self.assertTrue(mw.menu.act_redo_selection.isEnabled())

        # 선택 복원 되돌리기(Alt+Y)는 직전 해제 상태로 돌아가고, 다시 이전 선택 복원이 가능해짐
        mw.menu.act_redo_selection.trigger()
        self.assertEqual(mw.table_view.get_selected_filenames(), set())
        self.assertTrue(mw.menu.act_restore_selection.isEnabled())
        self.assertFalse(mw.menu.act_redo_selection.isEnabled())

        mw.menu.act_restore_selection.trigger()
        self.assertEqual(mw.table_view.get_selected_filenames(), {"a.mp4", "b.mp4", "c.mp4"})
        self.assertTrue(mw.menu.act_redo_selection.isEnabled())

        # 수동 새 선택을 하면 선택 다시 실행 스택은 비워짐
        idx_d = mw.proxy_model.index(3, 0)
        sel_model = mw.table_view.selectionModel()
        assert sel_model is not None
        sel_model.setCurrentIndex(
            idx_d, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
        )
        self.assertTrue(mw.menu.act_restore_selection.isEnabled())
        self.assertFalse(mw.menu.act_redo_selection.isEnabled())

    def test_selection_persists_in_db_across_window_restart_and_dir_switch(self) -> None:
        """선택한 파일 목록이 전용 DB 테이블에 작업 폴더별로 저장되어 재시작과 폴더 전환 후에도 유지됨을 검증"""
        base = tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        dir_a = os.path.join(base.name, "folder_a")
        dir_b = os.path.join(base.name, "folder_b")
        os.makedirs(dir_a)
        os.makedirs(dir_b)
        for folder, filenames in ((dir_a, ("a1.mp4", "a2.mp4", "a3.mp4")), (dir_b, ("b1.mp4", "b2.mp4"))):
            for name in filenames:
                with open(os.path.join(folder, name), "wb") as f:
                    f.write(b"video")

        isolate_app_state(self, dir_a)
        clf1 = VideoClassifier(root_dir=dir_a)
        DEFAULT_CACHE_REGISTRY.put(
            dir_a,
            TableCache(dir_a, [make_video(filename=n) for n in ("a1.mp4", "a2.mp4", "a3.mp4")], include_keyframe=True),
        )
        DEFAULT_CACHE_REGISTRY.put(
            dir_b,
            TableCache(dir_b, [make_video(filename=n) for n in ("b1.mp4", "b2.mp4")], include_keyframe=True),
        )

        mw1 = MainWindow(classifier=clf1)
        mw1.apply_report(clf1.get_summary_report(ensure_keyframe=False))
        mw1.table_view.select_filenames({"a1.mp4", "a3.mp4"})

        # 다른 폴더로 전환 후 복귀해도 각 폴더 선택이 DB를 통해 유지
        mw1.set_working_directory(dir_b)
        assert mw1.classifier is not None
        mw1.apply_report(mw1.classifier.get_summary_report(ensure_keyframe=False))
        mw1.table_view.select_filenames({"b2.mp4"})

        mw1.set_working_directory(dir_a)
        mw1.apply_report(mw1.classifier.get_summary_report(ensure_keyframe=False))
        self.assertEqual(mw1.table_view.get_selected_filenames(), {"a1.mp4", "a3.mp4"})
        mw1.close()

        # 새 창(재시작 시뮬레이션)으로 dir_a를 다시 열면 이전 선택이 그대로 복원됨
        clf2 = VideoClassifier(root_dir=dir_a)
        mw2 = MainWindow(classifier=clf2)
        self.addCleanup(mw2.close)
        mw2.apply_report(clf2.get_summary_report(ensure_keyframe=False))
        self.assertEqual(mw2.table_view.get_selected_filenames(), {"a1.mp4", "a3.mp4"})
        self.assertFalse(mw2.menu.act_restore_selection.isEnabled())

    def test_outside_click_and_report_reapply_preserve_table_selection(self) -> None:
        """창 바깥 여백 클릭과 같은 폴더 재평가, 이름 변경 반영 시 테이블 선택이 유지됨을 검증"""
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        for name in ("a.mp4", "b.mp4"):
            with open(os.path.join(work.name, name), "wb") as f:
                f.write(b"video")

        clf = VideoClassifier(root_dir=work.name)
        DEFAULT_CACHE_REGISTRY.put(
            work.name,
            TableCache(work.name, [make_video(filename="a.mp4"), make_video(filename="b.mp4")], include_keyframe=True),
        )
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.show()
        mw.apply_report(clf.get_summary_report(ensure_keyframe=False))
        QApplication.processEvents()

        mw.table_view.select_filenames({"a.mp4", "b.mp4"})
        header = mw.header_bar
        blank = QPoint(2, 2)
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            blank.toPointF(),
            header.mapToGlobal(blank).toPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(header, press)
        self.assertEqual(mw.table_view.get_selected_filenames(), {"a.mp4", "b.mp4"})

        # 이름 변경이 포함된 재평가 반영 시 새 파일명으로 선택 유지
        DEFAULT_CACHE_REGISTRY.put(
            work.name,
            TableCache(
                work.name,
                [make_video(filename="a [보존].mp4"), make_video(filename="b.mp4")],
                include_keyframe=True,
                renamed_pairs={"a.mp4": "a [보존].mp4"},
            ),
        )
        mw.apply_report(clf.get_summary_report(ensure_keyframe=False))
        self.assertEqual(mw.table_view.get_selected_filenames(), {"a [보존].mp4", "b.mp4"})
        self.assertFalse(mw.menu.act_restore_selection.isEnabled())

    def test_shift_or_ctrl_drag_keeps_range_selection_without_starting_external_drag(self) -> None:
        """Shift나 Ctrl을 누른 채 행을 끌면 외부 파일 드래그가 시작되지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("one.mp4", "two.mp4", "three.mp4"):
                with open(os.path.join(tmp, name), "wb") as f:
                    f.write(b"video")
            view = VideoTableView(root_dir_provider=lambda: tmp)
            self.addCleanup(view.close)
            view.resize(600, 300)
            view.table_model.set_data_source(
                [make_eval(make_video(filename=n)) for n in ("one.mp4", "two.mp4", "three.mp4")],
                root_dir=tmp,
            )
            view.show()
            QApplication.processEvents()

            p0 = view.visualRect(view.proxy_model.index(0, 0)).center()
            p2 = view.visualRect(view.proxy_model.index(2, 0)).center()
            with patch("src.gui.widgets.video_table_view.start_table_file_drag") as drag_mock:
                view.mousePressEvent(
                    _mouse_event(QEvent.Type.MouseButtonPress, p0, modifiers=Qt.KeyboardModifier.ShiftModifier)
                )
                view.mouseMoveEvent(
                    _mouse_event(QEvent.Type.MouseMove, p2, modifiers=Qt.KeyboardModifier.ShiftModifier)
                )
                view.mouseReleaseEvent(
                    _mouse_event(
                        QEvent.Type.MouseButtonRelease,
                        p2,
                        buttons=Qt.MouseButton.NoButton,
                        modifiers=Qt.KeyboardModifier.ShiftModifier,
                    )
                )
            drag_mock.assert_not_called()

    def test_dragging_middle_row_after_range_and_disjoint_selection_keeps_all_selected(self) -> None:
        """범위 선택이나 비연속 선택 뒤 가운데 행을 잡아 소거리 이동을 거쳐 끌어도 전체 선택이 잘리지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            names = [f"{i}.mp4" for i in range(1, 9)]
            for name in names:
                with open(os.path.join(tmp, name), "wb") as f:
                    f.write(b"video")

            view = VideoTableView(root_dir_provider=lambda: tmp)
            self.addCleanup(view.close)
            view.resize(600, 400)
            view.table_model.set_data_source(
                [make_eval(make_video(filename=name)) for name in names],
                root_dir=tmp,
            )
            view.show()
            QApplication.processEvents()
            vp = view.viewport()
            assert vp is not None

            def row_center(row: int) -> QPoint:
                return view.visualRect(view.proxy_model.index(row, 0)).center()

            dragged: list[list[str]] = []
            with patch(
                "src.gui.widgets.video_table_view.start_table_file_drag",
                side_effect=lambda _w, batch: dragged.append([os.path.basename(p) for p in batch])
                or Qt.DropAction.CopyAction,
            ):
                # 1) 1번 행 클릭 후 2번~8번 범위 드래그 선택(1..8) 대신 전체 선택 상태에서 2번 행 끌기
                _send_mouse(vp, QEvent.Type.MouseButtonPress, row_center(0))
                _send_mouse(vp, QEvent.Type.MouseButtonRelease, row_center(0), Qt.MouseButton.NoButton)
                view.selectAll()
                self.assertEqual(len(view.get_selected_filenames()), 8)

                _send_mouse(vp, QEvent.Type.MouseButtonPress, row_center(1))
                _send_mouse(vp, QEvent.Type.MouseMove, row_center(1) + QPoint(2, 0))
                self.assertEqual(len(view.get_selected_filenames()), 8)
                _send_mouse(
                    vp,
                    QEvent.Type.MouseMove,
                    row_center(1) + QPoint(QApplication.startDragDistance() + 8, 0),
                )
                self.assertEqual(dragged[-1], names)
                # 드래그 후 5번 행 클릭 시 이전 앵커(1번)로 범위 선택되지 않고 5번만 선택
                _send_mouse(vp, QEvent.Type.MouseButtonPress, row_center(4))
                _send_mouse(vp, QEvent.Type.MouseButtonRelease, row_center(4), Qt.MouseButton.NoButton)
                self.assertEqual(view.get_selected_filenames(), {"5.mp4"})

                # 2) 비연속 선택(1, 3, 6, 8) 후 3번 행(인덱스 2)을 잡아 끌기
                sel_model = view.selectionModel()
                assert sel_model is not None
                sel_model.clearSelection()
                for row in (0, 2, 5, 7):
                    sel_model.select(
                        view.proxy_model.index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                    )
                sel_model.setCurrentIndex(
                    view.proxy_model.index(7, 0), QItemSelectionModel.SelectionFlag.NoUpdate
                )
                _send_mouse(vp, QEvent.Type.MouseButtonPress, row_center(2))
                _send_mouse(vp, QEvent.Type.MouseMove, row_center(2) + QPoint(2, 0))
                self.assertEqual(view.get_selected_filenames(), {"1.mp4", "3.mp4", "6.mp4", "8.mp4"})
                _send_mouse(
                    vp,
                    QEvent.Type.MouseMove,
                    row_center(2) + QPoint(QApplication.startDragDistance() + 8, 0),
                )
                self.assertEqual(dragged[-1], ["1.mp4", "3.mp4", "6.mp4", "8.mp4"])

    def test_main_window_ignores_internal_drag_and_plans_external_resync(self) -> None:
        """내부 테이블에서 시작한 드래그는 메인 창 폴더 드롭 오버레이를 켜지 않으며, 외부 개명 시 기존 키프레임 모드로 재동기화를 계획함을 검증"""
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        vpath = os.path.join(work.name, "clip.mp4")
        with open(vpath, "wb") as f:
            f.write(b"video")

        clf = VideoClassifier(root_dir=work.name)
        DEFAULT_CACHE_REGISTRY.put(
            work.name,
            TableCache(work.name, [make_video(filename="clip.mp4", keyframe_interval=1.0)], include_keyframe=True),
        )
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.apply_report(clf.get_summary_report(ensure_keyframe=False))

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(work.name)])
        internal_ev = QDragEnterEvent(
            QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
        )
        with patch.object(internal_ev, "source", return_value=mw.table_view):
            mw.dragEnterEvent(internal_ev)
        self.assertFalse(internal_ev.isAccepted())
        self.assertFalse(mw._drop_overlay.isVisible())

        saved = compute_dir_signature(work.name)
        os.rename(vpath, os.path.join(work.name, "clip [보존].mp4"))
        self.assertEqual(
            plan_external_resync(saved, compute_dir_signature(work.name), include_keyframe=True),
            ACTION_ANALYZE_FULL,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
