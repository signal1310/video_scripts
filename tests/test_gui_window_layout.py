"""MainWindow 표 레이아웃, 오버레이, 바닥글 요약, 창 크기와 헤더 복원 검증"""

import logging
import os
import tempfile
import unittest
from typing import cast
from unittest.mock import patch

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

from src.classifier import VideoClassifier
from src.core.ui_state import UiLayoutState
from src.gui.column_sizing import DEFAULT_HIDDEN_COLS
from src.gui.constants import APP_WINDOW_TITLE
from src.gui.main_window import MainWindow
from src.gui.table_model import VideoTableModel
from src.gui.widgets.video_table_view import VideoTableView
from src.gui.window_layout import export_window_ui_state, format_window_title, restore_window_ui_state
from tests._gui_helpers import isolate_app_state
from tests._gui_window_base import GuiWindowTestCase, left_click

_APP = QApplication.instance() or QApplication([])


class TestGUIWindowLayout(GuiWindowTestCase):
    """MainWindow 표 레이아웃, 오버레이, 바닥글 요약, 창 크기와 헤더 복원 검증"""

    def test_table_has_focus_after_startup_so_space_previews_the_restored_selection(self) -> None:
        """표 전용 Space 미리 보기가 시작 직후 동작하도록 포커스가 표에 놓임을 검증"""
        win = MainWindow()
        self.addCleanup(win.close)
        QApplication.processEvents()
        self.assertIs(win.focusWidget(), win.table_view)

    def test_clean_status_messages(self) -> None:
        """폴더 변경/설정/임계값 조작 시 불필요한 상태 메시지가 출력되지 않고, 작업 진행/완료 메시지가 간결한지 검증"""
        with tempfile.TemporaryDirectory() as tmp_dir, tempfile.TemporaryDirectory() as another_dir:
            dummy_state = os.path.join(tmp_dir, "data", "config.json")
            dummy_root = os.path.join(tmp_dir, ".rootdir.test")
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                mw = MainWindow(initial_dir=tmp_dir)
                self.addCleanup(mw.close)
                self.assertEqual(mw.footer_bar.lbl_status.text(), "")

                mw.set_working_directory(another_dir)
                self.assertEqual(mw.footer_bar.lbl_status.text(), "")

                mw._on_criteria_changed(
                    mw._app_state.analysis.thresholds,
                    mw._app_state.analysis.savings,
                )
                self.assertEqual(mw.footer_bar.lbl_status.text(), "")

                mw.task_runner.handle_progress(3, 10, "clip.mp4")
                self.assertEqual(mw.footer_bar.lbl_status.text(), "[3/10] clip.mp4")

                logging.getLogger("src").info("작업 폴더: C:/another_path")
                _APP.processEvents()
                self.assertEqual(mw.footer_bar.lbl_status.text(), "[3/10] clip.mp4")

                mw.task_runner.handle_completed("analyze_fast", None)
                self.assertEqual(mw.footer_bar.lbl_status.text(), "")
                self.assertTrue(mw.footer_bar.lbl_status.isHidden())

                mw.task_runner.handle_cancelled("사용자에 의해 분석 작업이 중단되었습니다.")
                self.assertEqual(mw.footer_bar.lbl_status.text(), "작업이 중단되었습니다.")

    def test_table_min_column_widths_fit_default_viewport(self) -> None:
        """열 최소 너비 합계가 기본 창 뷰포트와 1280px 안에 들어옴을 검증"""
        visible_min_total = sum(
            width for col, width in VideoTableView.MIN_COL_WIDTHS.items() if col not in DEFAULT_HIDDEN_COLS
        )
        self.assertLessEqual(visible_min_total, 1280)
        mw, _ = self._loaded_window(with_report=False)
        viewport = mw.table_view.viewport()
        assert viewport is not None
        self.assertLessEqual(visible_min_total, viewport.width())

    def test_empty_overlay_shows_no_data_then_hides_and_syncs_analyze_mode(self) -> None:
        """데이터가 없으면 스캔 안내 오버레이를 보이고 헤더 스캔 버튼과 모드가 동기화되며, 로드 후 숨김을 검증"""
        mw, _ = self._loaded_window(with_report=False)
        view = mw.table_view
        self.assertTrue(view.empty_overlay.isVisible())
        self.assertEqual(view.empty_overlay.mode, "no_data")
        self.assertTrue(view.empty_overlay.btn_analyze.isVisible())
        self.assertFalse(view.empty_overlay.btn_action.isVisible())

        mw.header_bar.split_analyze.set_mode("full", emit_signal=True)
        self.assertEqual(view.empty_overlay.btn_analyze.current_mode, "full")
        view.empty_overlay.btn_analyze.set_mode("fast", emit_signal=True)
        self.assertEqual(mw.header_bar.current_analyze_mode, "fast")

        mw.apply_report(cast("VideoClassifier", mw.classifier).get_summary_report(ensure_keyframe=False))
        self.assertFalse(view.empty_overlay.isVisible())

    def test_empty_overlay_offers_filter_reset_when_search_matches_nothing(self) -> None:
        """검색 결과가 0건이면 필터 초기화 모드가 나타나고 동작 버튼이 검색을 비움을 검증"""
        mw, _ = self._loaded_window()
        view = mw.table_view
        mw.filter_bar.search_edit.setText("__no_such_video__")
        self.assertFalse(mw.filter_bar.btn_reset_filters.isHidden())
        self.assertTrue(view.empty_overlay.isVisible())
        self.assertEqual(view.empty_overlay.mode, "filtered_empty")

        view.empty_overlay.btn_action.click()
        self.assertEqual(mw.filter_bar.search_edit.text(), "")
        self.assertTrue(mw.filter_bar.btn_reset_filters.isHidden())
        self.assertFalse(view.empty_overlay.isVisible())

    def test_footer_summary_follows_selection(self) -> None:
        """선택이 없으면 푸터 요약이 숨겨지고, 원본 보존 행 선택은 참고 표기 없이 구간 합계만, 다중 선택은 개수와 길이를 표시함을 검증"""
        mw, _ = self._loaded_window()
        summary = mw.footer_bar.lbl_summary
        self.assertEqual(summary.text(), "")
        self.assertTrue(summary.isHidden())

        kept_row = next(
            row for row, ev in enumerate(mw.proxy_model.iter_visible_evaluations()) if not ev.is_encode_target
        )
        mw.table_view.selectRow(kept_row)
        _APP.processEvents()
        self.assertFalse(summary.isHidden())
        for expected in ("선택 1개", "예상 절약", "예상 시간"):
            self.assertIn(expected, summary.text())
        for unexpected in ("표시:", "(참고)", "가상"):
            self.assertNotIn(unexpected, summary.text())
        mw.table_view.clearSelection()
        _APP.processEvents()
        self.assertTrue(summary.isHidden())

        mw.table_view.selectAll()
        self.assertIn("선택 3개", summary.text())
        self.assertIn("길이", summary.text())
        self.assertIn("빈 여백, 카드 클릭", summary.toolTip())

    def test_footer_summary_shows_filter_and_selection_together(self) -> None:
        """필터로 줄어든 표시 행에서 선택하면 요약 문구 클릭으로 표시와 선택 상세를 전환하고 선택이 유지됨을 검증"""
        mw, _ = self._loaded_window()
        summary = mw.footer_bar.lbl_summary
        mw.filter_bar.search_edit.setText("고화질")
        _APP.processEvents()
        visible_count = len(list(mw.proxy_model.iter_visible_evaluations()))
        self.assertGreater(visible_count, 0)
        self.assertLess(visible_count, mw.table_model.rowCount())
        self.assertIn(f"표시 {visible_count}개", summary.text())
        self.assertNotIn("선택 ", summary.text())

        mw.table_view.selectAll()
        _APP.processEvents()
        self.assertIn(f"표시 {visible_count}개", summary.text())
        self.assertIn(f"선택 {visible_count}개", summary.text())
        self.assertEqual(summary.text().count("용량"), 1)
        self.assertNotIn("<table", summary.text())
        first_html = summary.text()

        left_click(summary)
        _APP.processEvents()
        self.assertNotEqual(summary.text(), first_html)
        self.assertEqual(summary.text().count("용량"), 1)
        self.assertEqual(len(mw.table_view.get_selected_evaluations()), visible_count)

    def test_click_outside_table_and_controls_preserve_selection(self) -> None:
        """테이블 바깥 여백이나 메뉴바, 검색창 클릭과 Esc 입력이 테이블 선택을 유지함을 검증"""
        mw, _ = self._loaded_window()
        view = mw.table_view

        def selected() -> int:
            return len(view.get_selected_evaluations())

        view.selectAll()
        menubar = mw.menuBar()
        assert menubar is not None
        left_click(menubar)
        left_click(mw.filter_bar.search_edit)
        self.assertEqual(selected(), 3)

        escape = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view, escape)
        self.assertEqual(selected(), 3)

        for passive in (mw.dashboard.card_files, mw.footer_bar.lbl_summary):
            left_click(passive)
            self.assertEqual(selected(), 3)

    def test_blank_area_click_releases_path_edit_focus(self) -> None:
        """경로 입력 중 창의 빈 영역을 누르면 포커스가 빠져 확정 입력은 이름 표시로, 바뀐 입력은 남음을 검증"""
        state_root = tempfile.TemporaryDirectory()
        work = tempfile.TemporaryDirectory()
        self.addCleanup(state_root.cleanup)
        self.addCleanup(work.cleanup)
        isolate_app_state(self, state_root.name)

        mw = MainWindow()
        self.addCleanup(mw.close)
        mw.show()
        mw.activateWindow()
        QApplication.processEvents()
        mw.set_working_directory(work.name)
        header = mw.header_bar
        blank = QPointF(2, 2)
        press = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            blank,
            QPointF(header.mapToGlobal(blank.toPoint())),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        header.begin_path_edit()
        self.assertTrue(header.path_edit.hasFocus())
        QApplication.sendEvent(header, press)
        self.assertFalse(header.path_edit.hasFocus())
        self.assertIs(header.path_stack.currentWidget(), header.folder_name_btn)

        header.begin_path_edit()
        header.path_edit.setText("draft_dir")
        QApplication.sendEvent(header, press)
        self.assertFalse(header.path_edit.hasFocus())
        self.assertEqual(header.path_edit.text(), "draft_dir")
        self.assertIs(header.path_stack.currentWidget(), header.path_edit)

    def test_restore_window_ui_state_preserves_movable_and_neutral_sort(self) -> None:
        """이전 설정의 헤더 상태를 복원해도 열 드래그 이동, 클릭, 초기 무화살표 정책이 유지됨을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_state = os.path.join(tmpdir, "data", "config.json")
            dummy_root = os.path.join(tmpdir, ".rootdir.test")
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                src_win = MainWindow()
                self.addCleanup(src_win.close)
                src_hdr = src_win.table_view.filter_header
                src_hdr.setSectionsMovable(False)
                src_hdr.setSectionsClickable(False)
                src_hdr.setSortIndicatorShown(True)
                src_hdr.setSortIndicator(
                    VideoTableModel.COL_SAVED_SIZE, Qt.SortOrder.DescendingOrder
                )
                src_win.table_view.setColumnWidth(VideoTableModel.COL_NAME, 310)
                geom_b64, hdr_b64 = export_window_ui_state(src_win, src_win.table_view)

                dst_win = MainWindow()
                self.addCleanup(dst_win.close)
                restore_window_ui_state(
                    dst_win,
                    UiLayoutState(window_geometry=geom_b64, table_header_state=hdr_b64),
                    dst_win.table_view,
                )

                dst_hdr = dst_win.table_view.filter_header
                self.assertTrue(dst_hdr.sectionsMovable())
                self.assertTrue(dst_hdr.sectionsClickable())
                self.assertFalse(dst_hdr.isSortIndicatorShown())
                self.assertEqual(dst_hdr.sortIndicatorSection(), -1)
                self.assertEqual(
                    dst_win.table_view.columnWidth(VideoTableModel.COL_NAME), 310
                )

                # 버전이 다른 열 상태는 무시하고 기본 열 너비, 숨김 상태를 적용함을 검증
                restore_window_ui_state(
                    dst_win,
                    UiLayoutState(table_header_state='{"version": 1, "columns": {}}'),
                    dst_win.table_view,
                )
                self.assertEqual(
                    dst_win.table_view.columnWidth(VideoTableModel.COL_NAME),
                    VideoTableView.MIN_COL_WIDTHS[VideoTableModel.COL_NAME],
                )
                for hidden_col in VideoTableView.DEFAULT_HIDDEN_COLS:
                    self.assertTrue(dst_win.table_view.isColumnHidden(hidden_col))
                restore_window_ui_state(
                    dst_win, UiLayoutState(table_header_state="c3RhbGUtaGVhZGVy"), dst_win.table_view
                )
                self.assertEqual(
                    dst_win.table_view.columnWidth(VideoTableModel.COL_NAME),
                    VideoTableView.MIN_COL_WIDTHS[VideoTableModel.COL_NAME],
                )

    def test_window_title_shows_committed_working_directory(self) -> None:
        """창 제목이 앱 이름 뒤에 확정된 작업 폴더 경로를 붙이고, 작업 폴더가 없으면 앱 이름만 표시함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_state = os.path.join(tmpdir, "data", "config.json")
            dummy_root = os.path.join(tmpdir, ".rootdir.test")
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                win = MainWindow()
                self.addCleanup(win.close)
                win.header_bar.set_path(tmpdir)
                self.assertEqual(win.windowTitle(), f"{APP_WINDOW_TITLE} \u2014 {tmpdir}")
                win.header_bar.set_path("")
                self.assertEqual(win.windowTitle(), APP_WINDOW_TITLE)
        self.assertEqual(format_window_title(""), APP_WINDOW_TITLE)
        self.assertEqual(
            format_window_title("C:/a;C:/b;C:/c"),
            f"{APP_WINDOW_TITLE} \u2014 C:/a 외 2개",
        )

    def test_main_window_has_no_artificial_min_size(self) -> None:
        """메인 윈도우에 인위적인 최소 창 크기 제한이 없고 작은 크기로 축소 가능함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_state = os.path.join(tmpdir, "data", "config.json")
            dummy_root = os.path.join(tmpdir, ".rootdir.test")
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                mw = MainWindow()
                try:
                    mw.show()
                    self.assertLess(mw.minimumSize().width(), 1020)
                    self.assertLess(mw.minimumSize().height(), 640)
                    mw.resize(700, 420)
                    _APP.processEvents()
                    self.assertLess(mw.width(), 1020)
                    self.assertLess(mw.height(), 640)
                finally:
                    mw.close()
                    mw.deleteLater()
                    _APP.processEvents()

    def test_restored_header_state_keeps_auto_fill_on_resize(self) -> None:
        """이전 창 헤더 상태를 복원한 뒤에도 창 확대, 축소 시 이름 열이 뷰포트 너비를 채워 오른쪽 여백이 생기지 않음을 검증"""
        with tempfile.TemporaryDirectory() as outer_dir:
            dummy_state = os.path.join(outer_dir, "data", "config.json")
            dummy_root = os.path.join(outer_dir, ".rootdir.test")
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                mw1 = MainWindow()
                mw1.resize(1100, 720)
                mw1.show()
                _APP.processEvents()
                mw1.close()
                mw1.deleteLater()
                _APP.processEvents()

                mw2 = MainWindow()
                try:
                    mw2.show()
                    _APP.processEvents()
                    self.assertFalse(mw2.table_view._user_resized_name_col)

                    mw2.resize(1520, 760)
                    _APP.processEvents()
                    vp = mw2.table_view.viewport()
                    assert vp is not None
                    visible_total_w = sum(
                        mw2.table_view.columnWidth(c)
                        for c in range(mw2.table_model.columnCount())
                        if not mw2.table_view.isColumnHidden(c)
                    )
                    self.assertGreaterEqual(visible_total_w, vp.width())
                finally:
                    mw2.close()
                    mw2.deleteLater()
                    _APP.processEvents()

    def test_main_window_can_shrink_horizontally_with_scrollable_dashboard(self) -> None:
        """대시보드가 가로 스크롤 가능해져 메인 창을 기존 한계(959px)보다 훨씬 좁게 줄일 수 있음을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "app_state.json")
            dummy_root = os.path.join(tmpdir, ".rootdir")
            with (
                patch("src.core.app_state.get_state_path", return_value=state_path),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                mw = MainWindow()
                try:
                    self.assertLess(mw.minimumSizeHint().width(), 600)
                    mw.resize(520, 620)
                    mw.show()
                    _APP.processEvents()
                    self.assertLessEqual(mw.width(), 560)
                    h_bar = mw.dashboard._scroll_area.horizontalScrollBar()
                    assert h_bar is not None
                    self.assertGreater(h_bar.maximum(), 0)
                finally:
                    mw.close()
                    mw.deleteLater()
                    _APP.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
