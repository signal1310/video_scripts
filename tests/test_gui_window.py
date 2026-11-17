"""MainWindow 통합 상호작용, 워커 비동기 제어, GUI 계층 경계 검증"""

import csv
import logging
import os
import tempfile
import time
import unittest
from dataclasses import replace
from typing import Any, cast
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
from PyQt6.QtGui import (
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QKeyEvent,
    QMouseEvent,
)
from PyQt6.QtWidgets import QApplication, QHeaderView, QLabel, QMessageBox, QWidget

from src.classifier import VideoClassifier
from src.core.app_state import load_app_state
from src.core.config import DEFAULT_THRESHOLDS
from src.core.file_manager import compute_dir_signature
from src.core.models import VideoProps
from src.core.ui_state import UiLayoutState
from src.core.workspace_paths import format_root_dirs, parse_root_dirs
from src.gui.column_filters import ColumnFilterRule
from src.gui.column_sizing import DEFAULT_HIDDEN_COLS
from src.gui.constants import APP_WINDOW_TITLE
from src.gui.dialogs.exception_rules_dialog import ExceptionRulesDialog
from src.gui.dialogs.help_dialog import HelpDialog
from src.gui.dialogs.settings_dialog import SettingsDialog
from src.gui.exporters import export_table_to_csv
from src.gui.main_window import MainWindow
from src.gui.session_controller import update_filter_exceptions
from src.gui.table_model import VideoTableModel
from src.gui.theme import ThemeManager
from src.gui.widgets.filter_bar import FilterTuningBar
from src.gui.widgets.video_table_view import VideoTableView
from src.gui.window_layout import (
    export_window_ui_state,
    format_window_title,
    restore_window_ui_state,
)
from src.gui.worker import ClassifierWorker
from src.service.analysis_store import load_manual_target_store
from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache
from tests._factories import make_video
from tests._gui_helpers import assert_independent_window, isolate_app_state
from tests._sandbox import reset_sandbox_files

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])
_ASYNC_WAIT_SECONDS: float = 5.0


def _settle_reevaluation(mw: "MainWindow") -> None:
    """설정 변경 뒤 백그라운드 재평가 결과가 화면에 적용될 때까지 이벤트를 처리하며 대기"""
    deadline = time.monotonic() + _ASYNC_WAIT_SECONDS
    while mw._evaluator.pending and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _wait_for_flag(flag: list[bool]) -> None:
    """백그라운드 작업의 완료 콜백이 flag에 값을 넣을 때까지 이벤트를 처리하며 대기"""
    deadline = time.monotonic() + _ASYNC_WAIT_SECONDS
    while not flag and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)
    if not flag:
        raise AssertionError("백그라운드 작업 완료 신호를 받지 못함")


def _left_click(widget: QWidget) -> None:
    center = QPointF(widget.rect().center())
    global_center = widget.mapToGlobal(center)
    for event_type, buttons in (
        (QMouseEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
        (QMouseEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        event = QMouseEvent(
            event_type, center, global_center, Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier
        )
        QApplication.sendEvent(widget, event)


class TestGUIWindowModule(unittest.TestCase):
    """MainWindow 통합 상호작용, ClassifierWorker 비동기/취소 제어 및 GUI 계층 경계를 검증"""

    def setUp(self) -> None:
        """샌드박스 파일을 테스트 전후로 초기화해 실제 사용자 데이터 접근을 차단"""
        reset_sandbox_files()
        self.addCleanup(reset_sandbox_files)
        self.videos = [
            VideoProps(
                filename="가나다_10_고화질.mp4",
                width=1920,
                height=1080,
                rotate_type=0,
                fps=60.0,
                vid_kbps=9500,
                aud_kbps=192,
                vid_size_MB=120.0,
                duration=100.0,
                codec="h264",
                keyframe_interval=1.0,
            ),
            VideoProps(
                filename="가나다_2_정상.mp4",
                width=1280,
                height=720,
                rotate_type=90,
                fps=30.0,
                vid_kbps=1200,
                aud_kbps=128,
                vid_size_MB=45.0,
                duration=240.0,
                codec="hevc",
                keyframe_interval=1.5,
            ),
            VideoProps(
                filename="저화질_KF불량_클립.mkv",
                width=854,
                height=480,
                rotate_type=0,
                fps=30.0,
                vid_kbps=400,
                aud_kbps=96,
                vid_size_MB=25.0,
                duration=400.0,
                codec="mpeg4",
                keyframe_interval=5.0,
            ),
        ]

    def test_table_has_focus_after_startup_so_space_previews_the_restored_selection(self) -> None:
        """표 전용 Space 미리 보기가 시작 직후 동작하도록 포커스가 표에 놓임을 검증"""
        win = MainWindow()
        self.addCleanup(win.close)
        QApplication.processEvents()
        self.assertIs(win.focusWidget(), win.table_view)

    def test_main_window_typed_path_switches_only_for_existing_directory(self) -> None:
        """직접 입력한 경로는 존재하는 폴더일 때만 작업 폴더로 전환되고, 아니면 확정 경로를 유지하고 입력을 남기는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            win = MainWindow()
            self.addCleanup(win.close)
            header = win.header_bar
            before = header.committed_path

            header.path_edit.setText("missing_folder_for_test")
            header.path_edit.returnPressed.emit()
            self.assertEqual(header.committed_path, before)
            self.assertEqual(header.path_edit.text(), "missing_folder_for_test")
            self.assertIn("폴더", win.footer_bar.lbl_status.text())

            header.path_edit.setText(tmpdir)
            header.path_edit.returnPressed.emit()
            self.assertEqual(
                os.path.normpath(win._current_root_dir()), os.path.normpath(tmpdir)
            )
            self.assertEqual(os.path.normpath(header.committed_path), os.path.normpath(tmpdir))

    def test_main_window_dashboard_and_live_threshold_tuning(self) -> None:
        """MainWindow가 VideoClassifier를 래핑하여 대시보드 카드를 갱신하고 실시간 임계값 변경을 즉시 반영하는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            clf = VideoClassifier(root_dir=tmpdir)
            cache = TableCache(tmpdir, list(self.videos), include_keyframe=True)
            DEFAULT_CACHE_REGISTRY.put(tmpdir, cache)

            win = MainWindow(classifier=clf)
            self.addCleanup(win.close)
            rep = clf.get_summary_report(ensure_keyframe=False)
            win.apply_report(rep)
            # 열 너비 계산은 재설정 뒤 이벤트 루프에서 이루어지므로 먼저 처리
            QApplication.processEvents()

            header = win.table_view.horizontalHeader()
            assert header is not None
            self.assertEqual(
                header.sectionResizeMode(VideoTableModel.COL_NAME),
                QHeaderView.ResizeMode.Interactive,
            )
            self.assertGreaterEqual(
                win.table_view.columnWidth(VideoTableModel.COL_NAME), 200
            )

            self.assertEqual(win.table_model.rowCount(), 3)
            self.assertFalse(win.table_view.filter_header.isSortIndicatorShown())
            self.assertEqual(win.table_view.filter_header.sortIndicatorSection(), -1)
            self.assertEqual(
                win.proxy_model.data(
                    win.proxy_model.index(0, VideoTableModel.COL_NAME)
                ),
                "가나다_2_정상.mp4",
            )
            self.assertEqual(
                win.proxy_model.data(
                    win.proxy_model.index(1, VideoTableModel.COL_NAME)
                ),
                "가나다_10_고화질.mp4",
            )
            self.assertIn("3개", win.dashboard.card_files.value_label.text())

            # 예외 규칙에 '가나다_10' 반영 시 즉시 인코딩 대상에서 제외되는지 검증
            win.filter_bar.set_exception_tokens(["가나다_10"])
            _settle_reevaluation(win)
            updated_rep = win.current_report
            assert updated_rep is not None
            emap = {e.video.filename: e for e in updated_rep.evaluations}
            self.assertIsNone(emap["가나다_10_고화질.mp4"].target_dirname)

            # 파이썬 람다/조건식 예외 규칙(v.duration < 150) 반영 시 즉시 인코딩 대상에서 제외되는지 검증
            win.filter_bar.set_exception_tokens(["v.duration < 150"])
            rep_lambda = win.current_report
            assert rep_lambda is not None
            emap_lambda = {e.video.filename: e for e in rep_lambda.evaluations}
            self.assertIsNone(emap_lambda["가나다_10_고화질.mp4"].target_dirname)

            # 환경설정 창 열기 및 Non-Modal 동작, 스크롤바 정책 검증
            win.menu.act_open_settings.trigger()
            dlg = win.dialogs.settings_dialog
            assert dlg is not None
            self.assertTrue(dlg.isVisible())
            self.assertEqual(dlg.windowModality(), Qt.WindowModality.NonModal)
            self.assertEqual(
                dlg.scroll_area.horizontalScrollBarPolicy(),
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )
            self.assertFalse(dlg.section_advanced.is_expanded())
            self.assertFalse(dlg.section_advanced.content_widget.isVisible())
            dlg.section_advanced.toggle()
            self.assertTrue(dlg.section_advanced.is_expanded())
            self.assertTrue(dlg.section_advanced.content_widget.isVisible())
            dlg.close()

    def test_re_evaluate_from_cache_does_not_probe_on_main_thread(self) -> None:
        """외부에서 폴더가 바뀐 뒤 실시간 재평가가 디스크 프로빙 없이 메모리 캐시로 테이블을 유지하는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            clf = VideoClassifier(root_dir=tmpdir)
            cache = TableCache(tmpdir, list(self.videos), include_keyframe=True)
            DEFAULT_CACHE_REGISTRY.put(tmpdir, cache)

            win = MainWindow(classifier=clf)
            self.addCleanup(win.close)
            win.apply_report(clf.get_summary_report(ensure_keyframe=False))
            self.assertEqual(win.table_model.rowCount(), 3)

            with open(os.path.join(tmpdir, "incoming.mp4"), "wb") as f:
                f.write(b"new-file")

            done: list[bool] = []
            with patch(
                "src.service.analysis_probe.probe_files",
                side_effect=AssertionError("메인 스레드 프로빙"),
            ):
                win._re_evaluate_async(lambda: done.append(True))
                _wait_for_flag(done)

            self.assertEqual(win.table_model.rowCount(), 3)

    def test_criteria_change_applies_report_only_after_background_eval(self) -> None:
        """판별 기준 변경의 재평가 적용이 호출 즉시 GUI 스레드에서 일어나지 않고 워커 완료 뒤 적용되는지 검증"""
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate)
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TableCache(tmpdir, list(self.videos), include_keyframe=True)
            DEFAULT_CACHE_REGISTRY.put(tmpdir, cache)
            clf = VideoClassifier(root_dir=tmpdir)
            mw = MainWindow(classifier=clf)
            self.addCleanup(mw.close)

            with patch.object(mw, "apply_report") as apply_report:
                mw._on_criteria_changed(
                    mw._app_state.analysis.thresholds,
                    mw._app_state.analysis.savings,
                )
                apply_report.assert_not_called()
                deadline = time.monotonic() + _ASYNC_WAIT_SECONDS
                while not apply_report.called and time.monotonic() < deadline:
                    _APP.processEvents()
                    time.sleep(0.01)
            apply_report.assert_called_once()

    def test_main_window_theme_switch_integration(self) -> None:
        """ThemeManager 모드 변경 시 MainWindow의 스타일시트가 실시간 동적 갱신되는지 검증"""
        win = MainWindow(initial_dir="C:/dummy")
        self.addCleanup(win.close)
        mgr = ThemeManager()
        self.addCleanup(mgr.set_mode, mgr.mode)

        mgr.set_mode(ThemeManager.MODE_LIGHT)
        self.assertIn("#F5F6F8", win.styleSheet())

        mgr.set_mode(ThemeManager.MODE_DARK)
        self.assertIn("#121212", win.styleSheet())

    def test_menu_icons_follow_live_theme_switch(self) -> None:
        """테마를 실시간 전환하면 메뉴 아이콘 색이 새 팔레트의 본문 글자색으로 다시 칠해지는지 검증"""
        win = MainWindow(initial_dir="C:/dummy")
        self.addCleanup(win.close)
        mgr = ThemeManager()
        self.addCleanup(mgr.set_mode, mgr.mode)

        def icon_color() -> str:
            image = win.menu.act_play_selected.icon().pixmap(14, 14).toImage()
            colors = [
                image.pixelColor(x, y)
                for x in range(image.width())
                for y in range(image.height())
                if image.pixelColor(x, y).alpha() > 200
            ]
            self.assertTrue(colors)
            return colors[0].name().upper()

        for mode in (ThemeManager.MODE_LIGHT, ThemeManager.MODE_DARK):
            mgr.set_mode(mode)
            text_color = mgr.current_palette.text_primary.upper()
            channels = [int(text_color[i : i + 2], 16) for i in (1, 3, 5)]
            icon = icon_color()
            icon_channels = [int(icon[i : i + 2], 16) for i in (1, 3, 5)]
            self.assertTrue(all(abs(a - b) <= 4 for a, b in zip(channels, icon_channels, strict=True)))

    def test_main_window_escape_key_cancels_worker(self) -> None:
        """메인 윈도우에서 작업 진행 중 Esc 키를 누르면 cancel_active_task가 호출됨을 검증"""
        mw = MainWindow(initial_dir="C:/dummy_path")
        self.addCleanup(mw.close)
        called: list[bool] = []
        mw.cancel_active_task = lambda: called.append(True)

        class DummyWorker:
            def isRunning(self) -> bool:
                return True

        mw.active_worker = cast("ClassifierWorker", DummyWorker())
        esc_event = QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
        )
        mw.keyPressEvent(esc_event)
        mw.active_worker = None
        self.assertEqual(called, [True])

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

                mw.task_runner.handle_completed("analyze_fast", None, 0, [])
                self.assertEqual(mw.footer_bar.lbl_status.text(), "")
                self.assertTrue(mw.footer_bar.lbl_status.isHidden())

                mw.task_runner.handle_completed("classify_real", None, 0, [])
                self.assertEqual(mw.footer_bar.lbl_status.text(), "분류 폴더로 이동 완료: 0개 파일 이동됨")

                mw.task_runner.handle_completed("unclassify_all", None, 5, [])
                self.assertEqual(mw.footer_bar.lbl_status.text(), "작업 폴더로 복원 완료: 5개 파일을 되돌림")

                mw.task_runner.handle_cancelled("사용자에 의해 분석 작업이 중단되었습니다.")
                self.assertEqual(mw.footer_bar.lbl_status.text(), "작업이 중단되었습니다.")

    def test_stability_busy_lock_close_event_and_dir_reset(self) -> None:
        """작업 중 FilterTuningBar 잠금, closeEvent 워커 안전 종료, 폴더 변경 시 결과 초기화를 검증"""
        with tempfile.TemporaryDirectory() as dir1, tempfile.TemporaryDirectory() as dir2:
            dummy_state = os.path.join(dir1, "data", "config.json")
            dummy_root = os.path.join(dir1, ".rootdir.test")
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                clf = VideoClassifier(root_dir=dir1)
                cache = TableCache(dir1, list(self.videos), include_keyframe=True)
                DEFAULT_CACHE_REGISTRY.put(dir1, cache)

                mw = MainWindow(classifier=clf)
                rep = clf.get_summary_report(ensure_keyframe=False)
                mw.apply_report(rep)
                self.assertEqual(mw.table_model.rowCount(), 3)
                self.assertIn("3개", mw.dashboard.card_files.value_label.text())

                # 1. 작업 중 튜닝 컨트롤 잠금 검증
                mw.open_tuner()
                tuner_dlg = mw.dialogs.settings_dialog
                assert tuner_dlg is not None
                self.assertEqual(
                    tuner_dlg.tab_widget.currentIndex(), SettingsDialog.TAB_CRITERIA
                )
                mw.set_busy_state(True, "진행 중...")
                self.assertTrue(mw.menu.act_open_settings.isEnabled())
                self.assertFalse(tuner_dlg.criteria_panel.spin_over_ratio.isEnabled())
                self.assertFalse(mw.filter_bar.btn_edit_exceptions.isEnabled())
                mw.set_busy_state(False)
                self.assertTrue(mw.menu.act_open_settings.isEnabled())
                self.assertTrue(tuner_dlg.criteria_panel.spin_over_ratio.isEnabled())
                self.assertTrue(mw.filter_bar.btn_edit_exceptions.isEnabled())
                tuner_dlg.close()

                # 2. 폴더 변경 시 이전 분석 결과 및 대시보드 초기화 검증
                mw.set_working_directory(dir2)
                self.assertIsNone(mw.current_report)
                self.assertEqual(mw.table_model.rowCount(), 0)
                self.assertEqual(mw.dashboard.card_files.value_label.text(), "0개")
                self.assertEqual(mw.footer_bar.lbl_summary.text(), "")
                self.assertTrue(mw.footer_bar.lbl_summary.isHidden())

                # 3. closeEvent 실행 중 워커 안전 중단 및 대기 검증
                events: list[str | tuple[str, int]] = []

                class DummyRunningWorker:
                    def isRunning(self) -> bool:
                        return True

                    def cancel(self) -> None:
                        events.append("cancel")

                    def wait(self, timeout_ms: int = 0) -> bool:
                        events.append(("wait", timeout_ms))
                        return True

                mw.active_worker = cast("ClassifierWorker", DummyRunningWorker())
                with patch.object(mw, "_save_ui_state"):
                    mw.close()
                self.assertEqual(events, ["cancel", ("wait", 3000)])

    def test_exception_validation_and_threshold_sync(self) -> None:
        """MainWindow 예외 식 검증 피드백 및 통합 설정 튜너-FilterTuningBar-분류기 실시간 동기화를 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_state = os.path.join(tmpdir, "data", "config.json")
            dummy_root = os.path.join(tmpdir, ".rootdir")
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                mw = MainWindow(initial_dir=tmpdir)
                self.addCleanup(mw.close)

                exc_dlg = ExceptionRulesDialog(active_tokens=[], parent=mw)
                self.addCleanup(exc_dlg.close)
                exc_dlg.input_rule.setText("v.duration <")
                exc_dlg.btn_add.click()
                self.assertFalse(exc_dlg.lbl_error.isHidden())
                self.assertIn("예외 규칙 오류:", exc_dlg.lbl_error.text())
                self.assertEqual(exc_dlg.rules_list.count(), 0)
                exc_dlg.input_rule.setText("v.duration < 150")
                exc_dlg.btn_add.click()
                self.assertTrue(exc_dlg.btn_apply.isEnabled())
                self.assertTrue(exc_dlg.lbl_error.isHidden())
                self.assertEqual(exc_dlg.rules_list.count(), 1)

                mw.open_tuner()
                tuner_dlg = mw.dialogs.settings_dialog
                assert tuner_dlg is not None
                tuner = tuner_dlg.criteria_panel
                tuner.spin_over_ratio.setValue(4.25)
                tuner.spin_kf_threshold.setValue(18.5)
                tuner.spin_min_saved.setValue(15.0)
                assert mw.classifier is not None
                self.assertAlmostEqual(
                    mw.classifier.config.thresholds.over_bitrate_ratio, 4.25, places=2
                )
                self.assertAlmostEqual(
                    mw.classifier.config.thresholds.keyframe_interval_sec, 18.5, places=1
                )
                self.assertAlmostEqual(
                    mw.classifier.config.savings.min_saved_mb, 15.0, places=1
                )
                tuner_dlg.close()

    def _loaded_window(self, *, with_report: bool = True) -> tuple[MainWindow, str]:
        """3개 영상 캐시가 있는 분류기에 바인딩된 표시 상태의 창과 작업 폴더를 반환"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clf = VideoClassifier(root_dir=tmp.name)
        DEFAULT_CACHE_REGISTRY.put(tmp.name, TableCache(tmp.name, list(self.videos), include_keyframe=True))
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.show()
        if with_report:
            mw.apply_report(clf.get_summary_report(ensure_keyframe=False))
        return mw, tmp.name

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

        _left_click(summary)
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
        _left_click(menubar)
        _left_click(mw.filter_bar.search_edit)
        self.assertEqual(selected(), 3)

        escape = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view, escape)
        self.assertEqual(selected(), 3)

        for passive in (mw.dashboard.card_files, mw.footer_bar.lbl_summary):
            _left_click(passive)
            self.assertEqual(selected(), 3)

    def test_exception_token_updates_ignore_case_duplicates(self) -> None:
        """예외 규칙 추가는 대소문자 중복을 제거하고, 제거는 해당 항목만 지움을 검증"""
        bar = FilterTuningBar()
        self.addCleanup(bar.close)
        update_filter_exceptions(bar, ["sample", "SAMPLE", "clip"], add=True)
        self.assertEqual(bar.get_exception_tokens(), ["sample", "clip"])
        update_filter_exceptions(bar, ["sample"], add=False)
        self.assertEqual(bar.get_exception_tokens(), ["clip"])

    def test_csv_export_matches_table_headers_and_rows(self) -> None:
        """CSV 파일로 내보내기가 표시 헤더와 전체 행을 BOM 포함 UTF-8로 기록함을 검증"""
        mw, root = self._loaded_window()
        csv_path = os.path.join(root, "out.csv")
        self.assertEqual(export_table_to_csv(csv_path, mw.proxy_model), 3)
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], mw.table_model.effective_headers())
        self.assertEqual(rows[0][VideoTableModel.COL_SIZE], "용량")

    def test_warning_logs_reach_history_and_log_dialog(self) -> None:
        """src 로거 경고가 로그 이력과 로그 창에 표시됨을 검증"""
        mw, _ = self._loaded_window(with_report=False)
        logging.getLogger("src").warning("테스트 경고 메시지")
        _APP.processEvents()
        self.assertTrue(any("테스트 경고 메시지" in line for line in mw.dialogs.log_history))
        mw.show_log_dialog()
        dialog = mw.dialogs.log_dialog
        assert dialog is not None
        self.assertIn("테스트 경고 메시지", dialog.text_edit.toPlainText())
        dialog.close()

    def test_classifier_worker_classify_real_unclassify_and_cancel(self) -> None:
        """ClassifierWorker의 실이동, 복원 시그널 방출과 중단 상태 콜백을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            for v in self.videos:
                with open(os.path.join(tmpdir, v.filename), "wb") as f:
                    f.write(b"00")

            clf = VideoClassifier(root_dir=tmpdir)
            cache = TableCache(tmpdir, [replace(v) for v in self.videos], include_keyframe=True)
            DEFAULT_CACHE_REGISTRY.put(
                tmpdir, cache, signature=compute_dir_signature(tmpdir)
            )

            completed_events: list[tuple[str, Any, Any, Any]] = []
            worker = ClassifierWorker(
                classifier=clf,
                task_type=ClassifierWorker.TASK_CLASSIFY_REAL,
            )
            worker.task_completed.connect(
                lambda t, r, m, w: completed_events.append((t, r, m, w))
            )
            mode_before = clf.is_pseudo_classify
            worker.run()

            self.assertEqual(len(completed_events), 1)
            task_type, rep, _, _ = completed_events[0]
            self.assertEqual(task_type, ClassifierWorker.TASK_CLASSIFY_REAL)
            self.assertEqual(rep.total_count, 3)
            self.assertTrue(any(v.moved_dirname is not None for v in clf.video_props))
            self.assertEqual(clf.is_pseudo_classify, mode_before)

            # 전체 복원 워커 검증
            worker_unclassify = ClassifierWorker(
                classifier=clf,
                task_type=ClassifierWorker.TASK_UNCLASSIFY_ALL,
            )
            worker_unclassify.run()
            self.assertTrue(all(v.moved_dirname is None for v in clf.video_props))

        worker_dummy = ClassifierWorker(
            classifier=cast("Any", MagicMock()),
            task_type=ClassifierWorker.TASK_ANALYZE_FAST,
        )
        self.assertFalse(worker_dummy.is_cancelled())
        worker_dummy.cancel()
        self.assertTrue(worker_dummy.is_cancelled())

    def test_action_button_availability_and_drag_drop_overlay(self) -> None:
        """하단 분류, 복원 버튼의 상태 연동 활성화 및 폴더 드래그 앤 드롭 안내 오버레이 동작을 검증"""
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate)
        with (
            tempfile.TemporaryDirectory() as meta_dir,
            tempfile.TemporaryDirectory() as empty_dir,
            tempfile.TemporaryDirectory() as classified_dir,
            tempfile.TemporaryDirectory() as drop_dir,
        ):
            dummy_state = os.path.join(meta_dir, "data", "config.json")
            dummy_root = os.path.join(meta_dir, ".rootdir.test")
            os.makedirs(os.path.join(classified_dir, "_1_표준인코딩_CQ22"), exist_ok=True)
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                clf = VideoClassifier(root_dir=empty_dir)
                mw = MainWindow(classifier=clf)
                self.addCleanup(mw.close)
                mw.show()

                # 1. 스캔 전 빈 폴더: 분류 이동, 복원 모두 비활성화
                self.assertFalse(mw.footer_bar.btn_real_classify.isEnabled())
                self.assertFalse(mw.footer_bar.btn_unclassify_all.isEnabled())

                # 2. 하위 폴더가 있는 경로 전환 시 분류 복원 활성화
                mw.set_working_directory(classified_dir)
                self.assertFalse(mw.footer_bar.btn_real_classify.isEnabled())
                self.assertTrue(mw.footer_bar.btn_unclassify_all.isEnabled())

                # 3. 리포트 적용(미이동 대상 존재): 폴더 분류 이동 활성화
                cache = TableCache(empty_dir, [replace(v) for v in self.videos], include_keyframe=True)
                DEFAULT_CACHE_REGISTRY.put(empty_dir, cache)
                mw.set_working_directory(empty_dir)
                rep = clf.get_summary_report(ensure_keyframe=False)
                mw.apply_report(rep)
                self.assertTrue(mw.footer_bar.btn_real_classify.isEnabled())

                # 4. 드래그 진입/이탈/드롭 시 오버레이 표시 및 경로 전환 검증
                self.assertTrue(mw._drop_overlay.isHidden())
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(drop_dir)])

                enter_ev = QDragEnterEvent(
                    QPointF(20.0, 20.0).toPoint(),
                    Qt.DropAction.CopyAction,
                    mime,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                mw.dragEnterEvent(enter_ev)
                self.assertTrue(enter_ev.isAccepted())
                self.assertFalse(mw._drop_overlay.isHidden())
                drop_desc = mw._drop_overlay.findChild(QLabel, "DropOverlayDesc")
                self.assertIsNotNone(drop_desc)
                assert drop_desc is not None
                self.assertIn("Shift", drop_desc.text())

                leave_ev = QDragLeaveEvent()
                mw.dragLeaveEvent(leave_ev)
                self.assertTrue(mw._drop_overlay.isHidden())

                mw.dragEnterEvent(enter_ev)
                self.assertFalse(mw._drop_overlay.isHidden())
                drop_ev = QDropEvent(
                    QPointF(20.0, 20.0),
                    Qt.DropAction.CopyAction,
                    mime,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                mw.dropEvent(drop_ev)
                self.assertTrue(drop_ev.isAccepted())
                self.assertTrue(mw._drop_overlay.isHidden())
                self.assertEqual(
                    os.path.normpath(mw.header_bar.path_edit.text()),
                    os.path.normpath(drop_dir),
                )

    def _persist_ui_changes(self) -> str:
        """분류기 없는 창에서 임계값, 예외, 검색어, 스캔 모드, 작업 폴더를 바꾼 뒤 닫고 작업 폴더를 반환"""
        state_root = tempfile.TemporaryDirectory()
        work = tempfile.TemporaryDirectory()
        self.addCleanup(state_root.cleanup)
        self.addCleanup(work.cleanup)
        isolate_app_state(self, state_root.name)

        mw = MainWindow()
        self.addCleanup(mw.close)
        self.assertIsNone(mw.classifier)
        mw.open_tuner()
        tuner = mw.dialogs.settings_dialog
        assert tuner is not None
        tuner.criteria_panel.spin_over_ratio.setValue(1.55)
        tuner.tab_widget.setCurrentIndex(SettingsDialog.TAB_ENVIRONMENT)
        tuner.close()
        mw.filter_bar.set_exception_tokens(["@hevc"])
        mw.filter_bar.search_edit.setText("clip")
        mw.header_bar.split_analyze.set_mode("full", emit_signal=True)
        mw.set_working_directory(work.name)
        mw.close()
        return work.name

    def test_ui_state_is_saved_without_classifier_and_restored_in_new_window(self) -> None:
        """분류기 미바인딩 상태의 변경이 즉시 저장되어 새 창에서 UI, 설정, 최근 폴더, 설정 탭으로 복원됨을 검증"""
        work = self._persist_ui_changes()
        mw = MainWindow()
        self.addCleanup(mw.close)
        self.assertEqual(os.path.normpath(mw.header_bar.path_edit.text()), os.path.normpath(work))

        mw.open_settings()
        restored = mw.dialogs.settings_dialog
        assert restored is not None
        self.assertEqual(restored.tab_widget.currentIndex(), SettingsDialog.TAB_ENVIRONMENT)
        mw.open_tuner()
        tuner = mw.dialogs.settings_dialog
        assert tuner is not None
        self.assertEqual(tuner.tab_widget.currentIndex(), SettingsDialog.TAB_CRITERIA)
        self.assertAlmostEqual(tuner.criteria_panel.spin_over_ratio.value(), 1.55)
        self.assertEqual(mw.filter_bar.get_exception_tokens(), ["@hevc"])
        self.assertEqual(mw.filter_bar.search_edit.text(), "clip")
        self.assertEqual(mw.header_bar.current_analyze_mode, "full")

        state = load_app_state()
        self.assertEqual(state.paths.recent_dirs, [os.path.normpath(work)])
        self.assertTrue(state.ui.layout.window_geometry)
        self.assertTrue(state.ui.layout.table_header_state)
        self.assertEqual(state.ui.layout.settings_tab, SettingsDialog.TAB_CRITERIA)

    def test_reset_defaults_from_settings_menu_syncs_main_window(self) -> None:
        """메뉴의 환경 설정에서 전체 복원하면 설정 창, 저장 파일, 메인 창의 예외, 검색어, 스캔 모드가 함께 초기화됨을 검증"""
        self._persist_ui_changes()
        mw = MainWindow()
        self.addCleanup(mw.close)
        mw.open_tuner()
        dialog = mw.dialogs.settings_dialog
        assert dialog is not None
        self.assertEqual(dialog.tab_widget.currentIndex(), SettingsDialog.TAB_CRITERIA)
        mw.menu.act_open_settings.trigger()
        self.assertIs(mw.dialogs.settings_dialog, dialog)
        self.assertEqual(dialog.tab_widget.currentIndex(), SettingsDialog.TAB_ENVIRONMENT)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog.btn_reset_defaults.click()
        default_ratio = DEFAULT_THRESHOLDS.over_bitrate_ratio
        self.assertAlmostEqual(dialog.criteria_panel.spin_over_ratio.value(), default_ratio)
        self.assertAlmostEqual(load_app_state().analysis.thresholds.over_bitrate_ratio, default_ratio)
        self.assertEqual(mw.filter_bar.get_exception_tokens(), [])
        self.assertEqual(mw.filter_bar.search_edit.text(), "")
        self.assertEqual(mw.header_bar.current_analyze_mode, "fast")
        dialog.close()

    def test_reset_defaults_clears_classifier_recursive_scan(self) -> None:
        """전체 복원 시 분류 세션의 하위 폴더 포함 상태도 해제되어 헤더 체크 상태와 일치함을 검증"""
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        clf = VideoClassifier(root_dir=work.name)
        clf.include_subdirectories(True)
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.open_settings()
        dialog = mw.dialogs.settings_dialog
        assert dialog is not None

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog.btn_reset_defaults.click()
        self.assertFalse(clf.recursive)
        self.assertFalse(mw.header_bar.is_recursive)
        dialog.close()

    def test_browse_asks_before_replacing_multi_roots_and_starts_at_primary(self) -> None:
        """여러 작업 폴더를 찾아보기로 바꿀 때 확인 후 교체하고, 시작 위치는 주 작업 폴더임을 검증"""
        state_root = tempfile.TemporaryDirectory()
        dirs = [tempfile.TemporaryDirectory() for _ in range(3)]
        for handle in (state_root, *dirs):
            self.addCleanup(handle.cleanup)
        isolate_app_state(self, state_root.name)
        primary, other, picked = (d.name for d in dirs)

        mw = MainWindow()
        self.addCleanup(mw.close)
        mw.set_working_directory(format_root_dirs([primary, other]))
        with (
            patch("src.gui.main_window.browse_directory", return_value=picked) as browse,
            patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.No
            ) as ask,
        ):
            mw._on_browse_dir()
        self.assertEqual(browse.call_args.args[2], primary)
        ask.assert_called_once()
        self.assertEqual(len(parse_root_dirs(mw.header_bar.committed_path)), 2)

        with (
            patch("src.gui.main_window.browse_directory", return_value=picked),
            patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
            ),
        ):
            mw._on_browse_dir()
        self.assertEqual(mw.header_bar.committed_path, picked)

    def test_browse_single_root_replaces_without_asking(self) -> None:
        """작업 폴더가 하나면 확인 없이 바로 교체함을 검증"""
        state_root = tempfile.TemporaryDirectory()
        first = tempfile.TemporaryDirectory()
        second = tempfile.TemporaryDirectory()
        for handle in (state_root, first, second):
            self.addCleanup(handle.cleanup)
        isolate_app_state(self, state_root.name)

        mw = MainWindow()
        self.addCleanup(mw.close)
        mw.set_working_directory(first.name)
        with (
            patch("src.gui.main_window.browse_directory", return_value=second.name),
            patch.object(QMessageBox, "question") as ask,
        ):
            mw._on_browse_dir()
        ask.assert_not_called()
        self.assertEqual(mw.header_bar.committed_path, second.name)

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

    def test_open_root_in_explorer_uses_primary_root_of_multi_roots(self) -> None:
        """작업 폴더가 여러 개일 때 탐색기는 주 작업 폴더로 열림을 검증"""
        state_root = tempfile.TemporaryDirectory()
        primary = tempfile.TemporaryDirectory()
        other = tempfile.TemporaryDirectory()
        for handle in (state_root, primary, other):
            self.addCleanup(handle.cleanup)
        isolate_app_state(self, state_root.name)

        mw = MainWindow()
        self.addCleanup(mw.close)
        mw.header_bar.path_edit.setText(format_root_dirs([primary.name, other.name]))
        opened: list[str] = []
        with patch("os.startfile", side_effect=opened.append, create=True):
            mw._on_open_root_in_explorer()
        self.assertEqual(opened, [primary.name])

    def test_cui_lambda_rule_survives_gui_token_edit(self) -> None:
        """CUI에서 등록한 람다 예외 규칙이 GUI 토큰 편집 후에도 유지됨을 검증"""
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        clf = VideoClassifier(root_dir=work.name)
        clf.add_exception_rule(lambda v: "keep_cui" in v.filename)
        videos = [
            make_video(filename="keep_cui_over.mp4", vid_kbps=9000, vid_size_MB=80.0, codec="hevc"),
            make_video(filename="normal_over.mp4", vid_kbps=9000, vid_size_MB=80.0),
        ]
        DEFAULT_CACHE_REGISTRY.put(work.name, TableCache(work.name, videos, include_keyframe=True))

        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.filter_bar.set_exception_tokens(["v.codec == 'h264'"])
        _settle_reevaluation(mw)
        report = mw.current_report
        assert report is not None
        targets = {e.video.filename: e.target_dirname for e in report.evaluations}
        self.assertEqual(targets, {"keep_cui_over.mp4": None, "normal_over.mp4": None})

    def test_column_filters_survive_restart(self) -> None:
        """열별 필터 조건을 저장한 뒤 새 창을 열면 같은 조건이 다시 적용됨을 검증"""
        from src.gui.column_filters import TEXT_MODE_CONTAINS

        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        videos = [make_video(filename="alpha.mp4"), make_video(filename="beta.mp4")]
        DEFAULT_CACHE_REGISTRY.put(work.name, TableCache(work.name, videos, include_keyframe=True))
        clf = VideoClassifier(root_dir=work.name)
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.apply_report(clf.get_summary_report(ensure_keyframe=False))
        rule = ColumnFilterRule(text_query="alp", text_mode=TEXT_MODE_CONTAINS)
        mw.proxy_model.set_column_filter(VideoTableModel.COL_NAME, rule)
        mw.proxy_model.sort(VideoTableModel.COL_BITRATE, Qt.SortOrder.DescendingOrder)
        mw.proxy_model.sort(VideoTableModel.COL_NAME, Qt.SortOrder.AscendingOrder)
        mw._session.persist()

        restarted = MainWindow(classifier=VideoClassifier(root_dir=work.name))
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.proxy_model.get_column_filter(VideoTableModel.COL_NAME), rule)
        self.assertEqual(restarted.proxy_model.active_column_filter_count(), 1)
        self.assertEqual(
            restarted.proxy_model.sort_stack,
            [(VideoTableModel.COL_NAME, Qt.SortOrder.AscendingOrder),
             (VideoTableModel.COL_BITRATE, Qt.SortOrder.DescendingOrder)],
        )

    def test_manual_target_menu_reevaluates_from_cache_and_persists(self) -> None:
        """출력 해상도 지정 선택이 재분석 없이 행과 분석 요약을 갱신하고 상태 파일에 저장함을 검증"""
        from src.core.models import SmartAction
        from src.service.session import normalize_root_key

        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        clf = VideoClassifier(root_dir=work.name)
        videos = [make_video(filename="big.mp4", vid_kbps=9000, vid_size_MB=500.0, duration=120.0)]
        DEFAULT_CACHE_REGISTRY.put(work.name, TableCache(work.name, videos, include_keyframe=True))

        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.apply_report(clf.get_summary_report(ensure_keyframe=False))
        before = mw.current_report
        assert before is not None
        self.assertNotEqual(before.evaluations[0].smart_action, SmartAction.ENCODE_DOWNSCALE)

        mw.table_view.selectAll()
        with patch(
            "src.service.analysis_probe.probe_files",
            side_effect=AssertionError("재평가 중 재분석"),
        ) as no_probe:
            mw.table_view.set_manual_target_for(None, "720p")
        no_probe.assert_not_called()

        deadline = time.monotonic() + _ASYNC_WAIT_SECONDS
        while mw.current_report is before and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        mw.menu.manual_target_menu.aboutToShow.emit()
        after = mw.current_report
        assert after is not None
        self.assertEqual(mw.table_view.get_selected_filenames(), {"big.mp4"})
        self.assertEqual(after.evaluations[0].smart_action, SmartAction.ENCODE_DOWNSCALE)
        after_tier = after.evaluations[0].target_tier
        assert after_tier is not None
        self.assertEqual(after_tier.key, "720p")
        self.assertNotEqual(after.evaluations[0].expected_after_mb, before.evaluations[0].expected_after_mb)
        row_eval = mw.table_model.get_evaluation_row(0)
        assert row_eval is not None
        assert row_eval.target_tier is not None
        self.assertEqual(row_eval.target_tier.key, "720p")
        self.assertEqual(
            load_manual_target_store({}),
            {normalize_root_key(work.name): {"big.mp4": "720p"}},
        )

        # - 단축키 표시가 탭 뒤에 붙으므로 라벨 부분으로 항목을 찾음
        items = {
            action.text().split("\t")[0]: action
            for action in mw.menu.manual_target_menu.actions()
            if not action.isSeparator()
        }
        self.assertTrue(items["720p (1280x720)"].isChecked())
        self.assertTrue(items["직접 지정 모두 해제"].isEnabled())
        applied = mw.current_report
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            items["직접 지정 모두 해제"].trigger()
        deadline = time.monotonic() + _ASYNC_WAIT_SECONDS
        while mw.current_report is applied and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        cleared = mw.current_report
        assert cleared is not None
        self.assertIsNone(cleared.evaluations[0].target_tier)
        self.assertEqual(load_manual_target_store({}), {})

    def test_clear_all_manual_targets_asks_for_confirmation(self) -> None:
        """직접 지정 모두 해제는 개수를 안내하는 확인을 거치고, 거절하면 지정이 유지되며, 지정이 없으면 묻지 않음을 검증"""
        from src.service.session import normalize_root_key

        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        clf = VideoClassifier(root_dir=work.name)
        videos = [make_video(filename="big.mp4", vid_kbps=9000, vid_size_MB=500.0, duration=120.0)]
        DEFAULT_CACHE_REGISTRY.put(work.name, TableCache(work.name, videos, include_keyframe=True))
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.apply_report(clf.get_summary_report(ensure_keyframe=False))
        expected = {normalize_root_key(work.name): {"big.mp4": "720p"}}

        with patch.object(QMessageBox, "question") as ask:
            mw.manual_targets.clear()
        ask.assert_not_called()

        mw.table_view.selectAll()
        mw.table_view.set_manual_target_for(None, "720p")
        self.assertEqual(load_manual_target_store({}), expected)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as ask:
            mw.manual_targets.clear()
        ask.assert_called_once()
        self.assertIn("1개", ask.call_args[0][2])
        self.assertEqual(ask.call_args[0][4], QMessageBox.StandardButton.No)
        self.assertEqual(load_manual_target_store({}), expected)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            mw.manual_targets.clear()
        self.assertEqual(load_manual_target_store({}), {})

    def test_manual_target_skips_exception_rows_until_auto_clears_them(self) -> None:
        """예외 행 포함 선택으로 지정해도 예외 행에는 저장되지 않아 예외를 풀어도 축소 대상이 되지 않고, 자동은 옛 값을 풀어줌을 검증"""
        from src.core.models import SmartAction
        from src.service.session import normalize_root_key

        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        key = normalize_root_key(work.name)
        clf = VideoClassifier(root_dir=work.name)
        videos = [
            make_video(filename="exc.mp4", codec="hevc", vid_kbps=9000, vid_size_MB=500.0, duration=120.0),
            make_video(filename="big.mp4", vid_kbps=9000, vid_size_MB=500.0, duration=120.0),
        ]
        DEFAULT_CACHE_REGISTRY.put(work.name, TableCache(work.name, videos, include_keyframe=True))

        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.filter_bar.set_exception_tokens(["v.codec == 'hevc'"])
        mw._on_exceptions_changed()
        _settle_reevaluation(mw)
        report = mw.current_report
        assert report is not None
        self.assertEqual(report.evaluations[0].smart_action, SmartAction.PASS_EXCEPTION)

        mw.table_view.selectAll()
        mw.table_view.set_manual_target_for(None, "720p")
        self.assertEqual(load_manual_target_store({}), {key: {"big.mp4": "720p"}})

        mw.filter_bar.set_exception_tokens([])
        mw._on_exceptions_changed()
        _settle_reevaluation(mw)
        report = mw.current_report
        assert report is not None
        exc_eval = next(ev for ev in report.evaluations if ev.video.filename == "exc.mp4")
        self.assertIsNone(exc_eval.target_tier)
        self.assertNotEqual(exc_eval.smart_action, SmartAction.ENCODE_DOWNSCALE)

        # - 옛 저장 값이 예외 행에 남아 있는 상황을 만든 뒤 자동 선택으로 풀리는지 확인
        mw.filter_bar.set_exception_tokens(["v.codec == 'hevc'"])
        mw._on_exceptions_changed()
        _settle_reevaluation(mw)
        stale_classifier = mw.classifier
        assert stale_classifier is not None
        stale_classifier.set_manual_target_tier(["exc.mp4"], "720p")
        mw.table_view.selectAll()
        mw.table_view.set_manual_target_for(None, None)
        self.assertEqual(load_manual_target_store({}), {})

    def _menu_window(self) -> tuple[MainWindow, str, str]:
        """격리된 임시 폴더 두 개를 오간 MainWindow를 만들어 (창, dir_a, dir_b)를 반환"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dir_a, dir_b = (os.path.join(tmp.name, name) for name in ("dir_a", "dir_b"))
        os.makedirs(dir_a)
        os.makedirs(dir_b)
        isolate_app_state(self, tmp.name)
        mw = MainWindow(initial_dir=dir_a)
        self.addCleanup(mw.close)
        mw.set_working_directory(dir_a)
        mw.set_working_directory(dir_b)
        return mw, dir_a, dir_b

    def test_menu_bar_structure_and_initial_state(self) -> None:
        """최상위 메뉴 구성, 선택 행 액션 초기 비활성, 모든 액션 아이콘 존재를 검증"""
        mw, _, _ = self._menu_window()
        menu_bar = mw.menuBar()
        assert menu_bar is not None
        self.assertEqual(
            [a.text() for a in menu_bar.actions()],
            ["파일(&F)", "편집(&E)", "보기(&V)", "분석(&A)", "분류(&C)", "도움말(&H)"],
        )
        menu = mw.menu
        self.assertFalse(menu.act_copy_paths.isEnabled())
        self.assertFalse(menu.act_play_selected.isEnabled())
        iconless = [
            name
            for name in (
                "act_open_dir", "recent_menu", "act_export_csv", "act_exception_editor", "act_open_settings",
                "theme_menu", "columns_menu", "act_reset_sort", "act_show_logs", "act_analyze_default",
                "act_force_reanalyze", "analyze_mode_menu", "act_play_selected", "act_copy_paths", "act_help",
                "act_open_presets_dir",
            )
            if getattr(menu, name).icon().isNull()
        ]
        self.assertEqual(iconless, [])

    def test_recent_menu_returns_to_previous_dir_and_clears(self) -> None:
        """최근 폴더 항목으로 복귀하고 마지막 항목으로 목록을 비움을 검증"""
        mw, dir_a, _ = self._menu_window()
        matches = [a for a in mw.menu.recent_menu.actions() if "dir_a" in a.text()]
        self.assertEqual(len(matches), 1)
        matches[0].trigger()
        self.assertEqual(os.path.normpath(mw.header_bar.path_edit.text()), os.path.normpath(dir_a))

        mw.menu.recent_menu.actions()[-1].trigger()
        self.assertEqual(load_app_state().paths.recent_dirs, [])

    def test_theme_menu_switches_theme_and_checks_action(self) -> None:
        """보기 메뉴의 테마 선택이 ThemeManager 모드와 체크 상태에 반영됨을 검증"""
        mw, _, _ = self._menu_window()
        theme_mgr = ThemeManager()
        self.addCleanup(theme_mgr.set_mode, theme_mgr.mode)
        light = mw.menu._theme_actions[ThemeManager.MODE_LIGHT]
        light.trigger()
        self.assertEqual(theme_mgr.mode, ThemeManager.MODE_LIGHT)
        self.assertTrue(light.isChecked())

    def test_analyze_mode_menu_and_force_reanalyze(self) -> None:
        """기본 스캔 모드 전환이 헤더와 저장 상태에 반영되고, 강제 재스캔이 fast/full 설정을 전달함을 검증"""
        mw, _, _ = self._menu_window()
        mw.menu._analyze_mode_actions["full"].trigger()
        self.assertEqual(mw.header_bar.current_analyze_mode, "full")
        self.assertEqual(load_app_state().ui.analyze_mode, "full")
        with patch.object(mw, "start_analyze") as mock_analyze:
            mw.menu.act_force_reanalyze.trigger()
        mock_analyze.assert_called_once_with(include_keyframe=True, force_reanalyze=True)

        mw.menu._analyze_mode_actions["fast"].trigger()
        self.assertEqual(mw.header_bar.current_analyze_mode, "fast")
        with patch.object(mw, "start_analyze") as mock_analyze:
            mw.menu.act_force_reanalyze.trigger()
        mock_analyze.assert_called_once_with(include_keyframe=False, force_reanalyze=True)

    def test_columns_menu_toggles_visibility_and_reset_sort_clears_indicator(self) -> None:
        """표시할 열 메뉴의 항목 구성과 토글, 기본 정렬 복원의 정렬 표시 해제를 검증"""
        mw, _, _ = self._menu_window()
        mw.menu._populate_columns_menu()
        col_acts = mw.menu.columns_menu.actions()
        self.assertEqual(len(col_acts), mw.table_model.columnCount())
        self.assertTrue(all(act.parent() is mw.menu.columns_menu for act in col_acts))
        self.assertFalse(col_acts[VideoTableModel.COL_NAME].isEnabled())

        opt_act = col_acts[VideoTableModel.COL_OPT_BITRATE]
        self.assertFalse(opt_act.isChecked())
        opt_act.trigger()
        self.assertFalse(mw.table_view.isColumnHidden(VideoTableModel.COL_OPT_BITRATE))

        mw.table_view.sortByColumn(VideoTableModel.COL_SIZE, Qt.SortOrder.DescendingOrder)
        self.assertTrue(mw.table_view.filter_header.isSortIndicatorShown())
        mw.menu.act_reset_sort.trigger()
        self.assertFalse(mw.table_view.filter_header.isSortIndicatorShown())

    def test_exception_editor_opens_independent_window_and_persists_apply(self) -> None:
        """예외 규칙 편집기가 독립 비모달 창으로 열리고, 적용 시 활성/비활성 목록이 분리 저장됨을 검증"""
        mw, _, _ = self._menu_window()
        mw.menu.act_exception_editor.trigger()
        dialog = mw.dialogs.exception_dialog
        assert dialog is not None
        assert_independent_window(self, dialog)

        dialog.sync_tokens(["keep_me"], ["paused_rule"])
        dialog.btn_apply.click()
        self.assertEqual(mw.filter_bar.get_exception_tokens(), ["keep_me"])
        self.assertIn("비활성 1", mw.filter_bar.btn_edit_exceptions.text())
        self.assertEqual(load_app_state().ui.disabled_exception_tokens, ["paused_rule"])

    def test_selection_actions_follow_selected_row_and_busy_state(self) -> None:
        """행 선택 시 경로 복사, 재생 액션이 활성화되고 작업 중에는 스캔이 잠기고 취소가 열림을 검증"""
        mw, dir_a, _ = self._menu_window()
        mw.set_working_directory(dir_a)
        DEFAULT_CACHE_REGISTRY.put(dir_a, TableCache(dir_a, list(self.videos), include_keyframe=True))
        clf = mw.ensure_classifier()
        assert clf is not None
        mw.apply_report(clf.get_summary_report(ensure_keyframe=False))

        mw.table_view.selectRow(0)
        self.assertTrue(mw.menu.act_copy_paths.isEnabled())
        self.assertTrue(mw.menu.act_play_selected.isEnabled())
        mw.menu.act_copy_paths.trigger()
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        self.assertIn(os.path.normpath(dir_a), clipboard.text())

        mw.set_busy_state(True)
        self.assertFalse(mw.menu.act_analyze_default.isEnabled())
        self.assertTrue(mw.menu.act_cancel.isEnabled())
        mw.set_busy_state(False)
        self.assertTrue(mw.menu.act_analyze_default.isEnabled())
        self.assertFalse(mw.menu.act_cancel.isEnabled())

    def test_help_dialog_is_independent_window_and_remembers_last_tab(self) -> None:
        """도움말이 독립 비모달 4탭 창으로 열리고, 탭 이동이 저장되어 다시 열 때 복원됨을 검증"""
        mw, _, _ = self._menu_window()
        self.assertFalse(hasattr(mw.menu, "act_help_guide"))
        self.assertFalse(hasattr(mw.menu, "act_help_concepts"))
        mw.menu.act_help.trigger()
        dialog = mw.dialogs.help_dialog
        assert dialog is not None
        assert_independent_window(self, dialog)
        self.assertFalse(hasattr(dialog, "btn_open_presets"))
        self.assertFalse(hasattr(dialog, "btn_close"))
        self.assertGreaterEqual(dialog.browser_guide.font().pointSize(), 11)
        self.assertEqual(dialog.tab_widget.currentIndex(), HelpDialog.TAB_GUIDE)
        self.assertIn("샤나인코더", dialog.browser_guide.toPlainText())

        dialog.tab_widget.setCurrentIndex(HelpDialog.TAB_CONCEPTS)
        concepts = dialog.browser_concepts.toPlainText()
        for term in (
            "CQ22",
            "최적 비트레이트",
            "실제 재인코딩을 실행할 때",
            "분당 절약(MB/분)",
            "코덱 전환",
            "코덱 호환",
            "10-bit",
        ):
            self.assertIn(term, concepts)

        dialog.tab_widget.setCurrentIndex(HelpDialog.TAB_GLOSSARY)
        self.assertEqual(dialog.tab_widget.tabText(HelpDialog.TAB_GLOSSARY), "용어 사전")
        self.assertIn("분당 절약", dialog.browser_glossary.toPlainText())
        dialog.glossary_search.setText("존재하지않는검색어")
        self.assertIn("검색 결과가 없습니다", dialog.browser_glossary.toPlainText())
        dialog.glossary_search.clear()

        dialog.tab_widget.setCurrentIndex(HelpDialog.TAB_SYNTAX_SHORTCUTS)
        self.assertIn("v.duration", dialog.browser_syntax.toPlainText())
        self.assertEqual(load_app_state().ui.layout.help_tab, HelpDialog.TAB_SYNTAX_SHORTCUTS)

        dialog.close()
        _APP.processEvents()
        mw.menu.act_help.trigger()
        reopened = mw.dialogs.help_dialog
        assert reopened is not None
        self.assertEqual(reopened.tab_widget.currentIndex(), HelpDialog.TAB_SYNTAX_SHORTCUTS)
        reopened.close()

    def test_presets_dir_action_reports_missing_folder(self) -> None:
        """프리셋 폴더가 없으면 탐색기 대신 안내 대화상자를 띄움을 검증"""
        mw, _, _ = self._menu_window()
        with patch("src.gui.main_window.QMessageBox.information") as info:
            mw.menu.act_open_presets_dir.trigger()
        info.assert_called_once()
        self.assertIn("shana_presets", info.call_args.args[2])

    def test_settings_and_log_dialogs_are_independent_windows(self) -> None:
        """설정, 로그 창이 부모 없는 독립 비모달 창으로 열림을 검증"""
        mw, _, _ = self._menu_window()
        mw.open_settings()
        settings = mw.dialogs.settings_dialog
        assert settings is not None
        log = mw.dialogs.show_log_dialog(export_model=mw.proxy_model, base_dir_provider=lambda: "")
        for dialog in (settings, log):
            assert_independent_window(self, dialog)
            dialog.close()

    def test_column_filters_window_sync_reset_and_dir_switch(self) -> None:
        """열 헤더 동적 필터 적용 시 FilterTuningBar 배지, 카운트, 필터 초기화 및 폴더 전환 연동을 검증"""
        with (
            tempfile.TemporaryDirectory() as dir_a,
            tempfile.TemporaryDirectory() as dir_b,
        ):
            dummy_state = os.path.join(dir_a, "data", "config.json")
            dummy_root = os.path.join(dir_a, ".rootdir.test")
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                clf = VideoClassifier(root_dir=dir_a)
                cache = TableCache(dir_a, list(self.videos), include_keyframe=True)
                DEFAULT_CACHE_REGISTRY.put(dir_a, cache)

                mw = MainWindow(classifier=clf)
                self.addCleanup(mw.close)
                mw.show()
                rep = clf.get_summary_report(ensure_keyframe=False)
                mw.apply_report(rep)
                self.assertEqual(mw.proxy_model.rowCount(), 3)

                # 1. 열 필터 적용 -> 배지 표시, 헤더 강조, 행 수 갱신
                mw.proxy_model.set_column_filter(
                    VideoTableModel.COL_CODEC,
                    ColumnFilterRule(selected_values=frozenset({"h264"})),
                )
                self.assertEqual(mw.proxy_model.rowCount(), 1)
                self.assertFalse(mw.filter_bar.btn_clear_col_filters.isHidden())
                self.assertIn("1개", mw.filter_bar.btn_clear_col_filters.text())
                self.assertIn("표시 1개", mw.footer_bar.lbl_summary.text())
                self.assertNotIn("/ 3개", mw.footer_bar.lbl_summary.text())
                self.assertEqual(
                    mw.table_view.filter_header.active_filter_columns(),
                    {VideoTableModel.COL_CODEC},
                )

                # 2. 테마 전환 후 배지, 헤더 유지 검증
                ThemeManager().set_mode(ThemeManager.MODE_DARK)
                self.assertEqual(
                    mw.table_view.filter_header.active_filter_columns(),
                    {VideoTableModel.COL_CODEC},
                )

                # 3. 배지 버튼 클릭 시 열 필터 일괄 해제
                mw.filter_bar.btn_clear_col_filters.click()
                self.assertEqual(mw.proxy_model.rowCount(), 3)
                self.assertTrue(mw.filter_bar.btn_clear_col_filters.isHidden())
                self.assertEqual(
                    mw.table_view.filter_header.active_filter_columns(), set()
                )

                # 4. 필터 재적용 후 작업 폴더 전환 시 열 필터 자동 초기화
                mw.proxy_model.set_column_filter(
                    VideoTableModel.COL_NAME,
                    ColumnFilterRule(text_query="저화질"),
                )
                self.assertEqual(mw.proxy_model.rowCount(), 1)
                mw.set_working_directory(dir_b)
                self.assertEqual(mw.proxy_model.active_column_filter_count(), 0)
                self.assertTrue(mw.filter_bar.btn_clear_col_filters.isHidden())

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

    def test_empty_root_analyze_prompts_directory_dialog(self) -> None:
        """작업 폴더가 비어 있을 때 스캔 실행 시 경고 대신 폴더 선택 창을 열고 선택 시 스캔을 시작함을 검증"""
        with tempfile.TemporaryDirectory() as outer_dir:
            dir_a = os.path.join(outer_dir, "dir_a")
            os.makedirs(dir_a, exist_ok=True)
            dummy_state = os.path.join(outer_dir, "data", "config.json")
            dummy_root = os.path.join(outer_dir, ".rootdir.test")
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
                patch("src.gui.session_controller.QMessageBox.warning") as mock_warn,
            ):
                mw = MainWindow()
                try:
                    self.assertEqual(mw.header_bar.path_edit.text(), "")

                    # 1. 폴더 선택 취소 -> 경고 팝업 없이 스캔 미실행
                    with (
                        patch("src.gui.main_window.browse_directory", return_value="") as mock_browse,
                        patch.object(mw._tasks, "start_analyze") as mock_task_analyze,
                    ):
                        mw.header_bar.split_analyze.btn_main.click()
                        mock_browse.assert_called_once()
                        mock_task_analyze.assert_not_called()
                        mock_warn.assert_not_called()

                    # 2. 폴더 선택 완료 -> 경로 적용 후 즉시 스캔 시작
                    with (
                        patch("src.gui.main_window.browse_directory", return_value=dir_a) as mock_browse,
                        patch.object(mw._tasks, "start_analyze") as mock_task_analyze,
                    ):
                        mw.table_view.empty_overlay.btn_analyze.btn_main.click()
                        mock_browse.assert_called_once()
                        self.assertEqual(
                            os.path.normpath(mw.header_bar.path_edit.text()),
                            os.path.normpath(dir_a),
                        )
                        self.assertIsNotNone(mw.classifier)
                        mock_task_analyze.assert_called_once_with(
                            include_keyframe=False, force_reanalyze=False
                        )
                finally:
                    mw.close()
                    mw.deleteLater()
                    _APP.processEvents()

    def test_settings_root_dir_live_sync_with_main_window(self) -> None:
        """설정 창에서 작업 폴더 선택, 입력, 초기화 시 메인 창과 실시간 양방향 연동됨을 검증"""
        with (
            tempfile.TemporaryDirectory() as outer_dir,
            tempfile.TemporaryDirectory() as dir_a,
            tempfile.TemporaryDirectory() as dir_b,
        ):
            dummy_state = os.path.join(outer_dir, "data", "config.json")
            dummy_root = os.path.join(outer_dir, ".rootdir.test")
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                mw = MainWindow()
                dlg: SettingsDialog | None = None
                try:
                    mw.show()
                    mw.open_settings(active_tab=SettingsDialog.TAB_ENVIRONMENT)
                    dlg = mw.dialogs.settings_dialog
                    self.assertIsNotNone(dlg)
                    assert dlg is not None

                    # 1. 설정 창 찾아보기 버튼으로 dir_a 선택 -> 메인 창 즉시 반영
                    with patch(
                        "src.gui.dialogs.settings_dialog.browse_directory", return_value=dir_a
                    ):
                        dlg.btn_browse_root.click()
                    _APP.processEvents()
                    self.assertEqual(
                        os.path.normpath(mw.header_bar.path_edit.text()),
                        os.path.normpath(dir_a),
                    )
                    self.assertIsNotNone(mw.classifier)
                    assert mw.classifier is not None
                    self.assertEqual(
                        os.path.normpath(mw.classifier.root_dir), os.path.normpath(dir_a)
                    )

                    # 2. 설정 창 입력란에 유효 경로(dir_b) 직접 입력 -> 포커스 이동 전에도 메인 창 실시간 반영
                    dlg.edit_root_dir.setText(dir_b)
                    _APP.processEvents()
                    self.assertEqual(
                        os.path.normpath(mw.header_bar.path_edit.text()),
                        os.path.normpath(dir_b),
                    )

                    # 3. 설정 창 기본값 복원(빈 경로) -> 메인 창 작업 폴더도 즉시 비워짐
                    dlg._rows["root_dir"].btn_reset.click()
                    _APP.processEvents()
                    self.assertEqual(mw.header_bar.path_edit.text(), "")
                    self.assertIsNone(mw.classifier)

                    # 4. 메인 창에서 폴더 선택 시 열려 있는 설정 창에도 실시간 반영
                    mw.set_working_directory(dir_a)
                    _APP.processEvents()
                    self.assertEqual(
                        os.path.normpath(dlg.edit_root_dir.text()),
                        os.path.normpath(dir_a),
                    )
                finally:
                    if dlg is not None:
                        dlg.close()
                        dlg.deleteLater()
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

    def test_re_evaluate_from_cache_does_not_probe_after_external_change(self) -> None:
        """외부에서 폴더가 바뀐 뒤 실시간 재평가가 메인 스레드에서 프로빙이나 재스캔을 일으키지 않음을 검증"""
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate)
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TableCache(tmpdir, list(self.videos), include_keyframe=True)
            DEFAULT_CACHE_REGISTRY.put(tmpdir, cache)

            clf = VideoClassifier(root_dir=tmpdir)
            mw = MainWindow(classifier=clf)
            self.addCleanup(mw.close)

            with open(os.path.join(tmpdir, "external_added.mp4"), "wb") as f:
                f.write(b"external-file")

            done: list[bool] = []
            with patch(
                "src.service.analysis_probe.probe_files",
                side_effect=AssertionError("메인 스레드에서 프로빙이 호출됨"),
            ):
                mw._re_evaluate_async(lambda: done.append(True))
                _wait_for_flag(done)

            self.assertEqual(mw.table_model.rowCount(), len(self.videos))


if __name__ == "__main__":
    unittest.main(verbosity=2)
