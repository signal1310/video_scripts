"""MainWindow 분석 흐름, 기준 변경 재평가, 예외와 수동 대상, 폴더 선택과 상태 복원 검증"""

import csv
import os
import tempfile
import time
import unittest
from typing import cast
from unittest.mock import patch

from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QKeyEvent
from PyQt6.QtWidgets import QApplication, QHeaderView, QLabel, QMessageBox

from src.classifier import VideoClassifier
from src.core.settings.app_state import load_app_state
from src.core.workspace_paths import format_root_dirs, parse_root_dirs
from src.gui.controllers.session_controller import update_filter_exceptions
from src.gui.controllers.worker import ClassifierWorker
from src.gui.dialogs.exception_rules_dialog import ExceptionRulesDialog
from src.gui.dialogs.exporters import export_table_to_csv
from src.gui.main_window import MainWindow
from src.gui.settings.settings_dialog import SettingsDialog
from src.gui.table.column_filters import ColumnFilterRule
from src.gui.table.table_model import VideoTableModel
from src.gui.widgets.filter_bar import FilterTuningBar
from src.service.analysis_store import load_manual_target_store
from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache
from tests._factories import make_video
from tests._gui_helpers import isolate_app_state
from tests._gui_window_base import ASYNC_WAIT_SECONDS, GuiWindowTestCase, settle_reevaluation, wait_for_flag

_APP = QApplication.instance() or QApplication([])


class TestGUIWindowWorkflow(GuiWindowTestCase):
    """MainWindow 분석 흐름, 기준 변경 재평가, 예외와 수동 대상, 폴더 선택과 상태 복원 검증"""

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
            settle_reevaluation(win)
            updated_rep = win.current_report
            assert updated_rep is not None
            emap = {e.video.filename: e for e in updated_rep.evaluations}
            self.assertFalse(emap["가나다_10_고화질.mp4"].is_encode_target)

            # 파이썬 람다/조건식 예외 규칙(v.duration < 150) 반영 시 즉시 인코딩 대상에서 제외되는지 검증
            win.filter_bar.set_exception_tokens(["v.duration < 150"])
            rep_lambda = win.current_report
            assert rep_lambda is not None
            emap_lambda = {e.video.filename: e for e in rep_lambda.evaluations}
            self.assertFalse(emap_lambda["가나다_10_고화질.mp4"].is_encode_target)

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
                wait_for_flag(done)

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
                deadline = time.monotonic() + ASYNC_WAIT_SECONDS
                while not apply_report.called and time.monotonic() < deadline:
                    _APP.processEvents()
                    time.sleep(0.01)
            apply_report.assert_called_once()

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

    def test_stability_busy_lock_close_event_and_dir_reset(self) -> None:
        """작업 중 FilterTuningBar 잠금, closeEvent 워커 안전 종료, 폴더 변경 시 결과 초기화를 검증"""
        with tempfile.TemporaryDirectory() as dir1, tempfile.TemporaryDirectory() as dir2:
            dummy_state = os.path.join(dir1, "data", "config.json")
            dummy_root = os.path.join(dir1, ".rootdir.test")
            with (
                patch("src.core.settings.app_state.get_state_path", return_value=dummy_state),
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
                patch("src.core.settings.app_state.get_state_path", return_value=dummy_state),
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

    def test_drag_drop_overlay_and_directory_switch(self) -> None:
        """폴더 드래그 앤 드롭 안내 오버레이 표시와 드롭 시 작업 폴더 전환 동작을 검증"""
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate)
        with (
            tempfile.TemporaryDirectory() as meta_dir,
            tempfile.TemporaryDirectory() as empty_dir,
            tempfile.TemporaryDirectory() as drop_dir,
        ):
            dummy_state = os.path.join(meta_dir, "data", "config.json")
            dummy_root = os.path.join(meta_dir, ".rootdir.test")
            with (
                patch("src.core.settings.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                clf = VideoClassifier(root_dir=empty_dir)
                mw = MainWindow(classifier=clf)
                self.addCleanup(mw.close)
                mw.show()

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
        settle_reevaluation(mw)
        report = mw.current_report
        assert report is not None
        targets = {e.video.filename: e.is_encode_target for e in report.evaluations}
        self.assertEqual(targets, {"keep_cui_over.mp4": False, "normal_over.mp4": False})

    def test_column_filters_survive_restart(self) -> None:
        """열별 필터 조건을 저장한 뒤 새 창을 열면 같은 조건이 다시 적용됨을 검증"""
        from src.gui.table.column_filters import TEXT_MODE_CONTAINS

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
        from src.core.smart_action import SmartAction
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

        deadline = time.monotonic() + ASYNC_WAIT_SECONDS
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
        deadline = time.monotonic() + ASYNC_WAIT_SECONDS
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
        from src.core.smart_action import SmartAction
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
        settle_reevaluation(mw)
        report = mw.current_report
        assert report is not None
        self.assertEqual(report.evaluations[0].smart_action, SmartAction.PASS_EXCEPTION)

        mw.table_view.selectAll()
        mw.table_view.set_manual_target_for(None, "720p")
        self.assertEqual(load_manual_target_store({}), {key: {"big.mp4": "720p"}})

        mw.filter_bar.set_exception_tokens([])
        mw._on_exceptions_changed()
        settle_reevaluation(mw)
        report = mw.current_report
        assert report is not None
        exc_eval = next(ev for ev in report.evaluations if ev.video.filename == "exc.mp4")
        self.assertIsNone(exc_eval.target_tier)
        self.assertNotEqual(exc_eval.smart_action, SmartAction.ENCODE_DOWNSCALE)

        # - 옛 저장 값이 예외 행에 남아 있는 상황을 만든 뒤 자동 선택으로 풀리는지 확인
        mw.filter_bar.set_exception_tokens(["v.codec == 'hevc'"])
        mw._on_exceptions_changed()
        settle_reevaluation(mw)
        stale_classifier = mw.classifier
        assert stale_classifier is not None
        stale_classifier.set_manual_target_tier(["exc.mp4"], "720p")
        mw.table_view.selectAll()
        mw.table_view.set_manual_target_for(None, None)
        self.assertEqual(load_manual_target_store({}), {})

    def test_empty_root_analyze_prompts_directory_dialog(self) -> None:
        """작업 폴더가 비어 있을 때 스캔 실행 시 경고 대신 폴더 선택 창을 열고 선택 시 스캔을 시작함을 검증"""
        with tempfile.TemporaryDirectory() as outer_dir:
            dir_a = os.path.join(outer_dir, "dir_a")
            os.makedirs(dir_a, exist_ok=True)
            dummy_state = os.path.join(outer_dir, "data", "config.json")
            dummy_root = os.path.join(outer_dir, ".rootdir.test")
            with (
                patch("src.core.settings.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
                patch("src.gui.controllers.session_controller.QMessageBox.warning") as mock_warn,
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
                patch("src.core.settings.app_state.get_state_path", return_value=dummy_state),
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
                        "src.gui.settings.settings_dialog.browse_directory", return_value=dir_a
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
                wait_for_flag(done)

            self.assertEqual(mw.table_model.rowCount(), len(self.videos))


if __name__ == "__main__":
    unittest.main(verbosity=2)
