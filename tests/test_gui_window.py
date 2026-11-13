import ast
import csv
import logging
import os
import tempfile
import unittest
from typing import Any, cast
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QKeyEvent
from PyQt6.QtWidgets import QApplication, QHeaderView, QMessageBox

from src.classifier import VideoClassifier
from src.core.app_state import load_app_state
from src.core.config import DEFAULT_CONFIGS
from src.core.file_manager import compute_dir_signature
from src.core.models import VideoProps
from src.gui.main_window import MainWindow
from src.gui.table_model import VideoTableModel
from src.gui.theme import ThemeManager
from src.gui.widgets.video_table_view import VideoTableView
from src.gui.worker import ClassifierWorker
from src.service.cache import TableCache
from src.service.classifier_service import VideoClassifierService
from tests._sandbox import reset_sandbox_files

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestGUIWindowModule(unittest.TestCase):
    """MainWindow 통합 상호작용, ClassifierWorker 비동기/취소 제어 및 GUI 계층 경계를 검증"""

    def setUp(self) -> None:
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

    def test_main_window_dashboard_and_live_threshold_tuning(self) -> None:
        """MainWindow가 VideoClassifier를 래핑하여 대시보드 카드를 갱신하고 실시간 임계값 변경을 즉시 반영하는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            clf = VideoClassifier(root_dir=tmpdir)
            cache = TableCache(tmpdir)
            cache._data = list(self.videos)
            cache._include_keyframe = True
            VideoClassifierService.inject_cache(tmpdir, cache)

            win = MainWindow(classifier=clf)
            rep = clf.get_summary_report(ensure_keyframe=False)
            win._update_ui_with_report(rep)

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
            self.assertIn("3개", win.dashboard.card_files.value_label.text())

            # 예외 키워드에 '가나다_10' 입력 시 즉시 인코딩 대상에서 제외되는지 검증
            win.filter_bar.edit_exceptions.setText("가나다_10")
            updated_rep = win.current_report
            assert updated_rep is not None
            emap = {e.video.filename: e for e in updated_rep.evaluations}
            self.assertIsNone(emap["가나다_10_고화질.mp4"].target_dirname)

            # 파이썬 람다/조건식 예외 규칙(v.duration < 150) 입력 시 즉시 인코딩 대상에서 제외되는지 검증
            win.filter_bar.edit_exceptions.setText("v.duration < 150")
            rep_lambda = win.current_report
            assert rep_lambda is not None
            emap_lambda = {e.video.filename: e for e in rep_lambda.evaluations}
            self.assertIsNone(emap_lambda["가나다_10_고화질.mp4"].target_dirname)

            # 환경설정 창 열기 및 Non-Modal 동작, 스크롤바 정책 검증
            win._on_open_settings()
            dlg = win._dialogs.settings_dialog
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

    def test_main_window_escape_key_cancels_worker(self) -> None:
        """메인 윈도우에서 작업 진행 중 Esc 키를 누르면 cancel_active_task가 호출됨을 검증"""
        mw = MainWindow(initial_dir="C:/dummy_path")
        called: list[bool] = []
        mw.cancel_active_task = lambda: called.append(True)

        class DummyWorker:
            def isRunning(self) -> bool:
                return True

        mw._worker = cast("ClassifierWorker", DummyWorker())
        esc_event = QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
        )
        mw.keyPressEvent(esc_event)
        self.assertEqual(called, [True])

    def test_clean_status_messages(self) -> None:
        """폴더 변경/설정/임계값 조작 시 불필요한 상태 메시지가 출력되지 않고, 작업 진행/완료 메시지가 간결한지 검증"""
        with tempfile.TemporaryDirectory() as tmp_dir, tempfile.TemporaryDirectory() as another_dir:
            dummy_state = os.path.join(tmp_dir, "data", "config.json")
            dummy_root = os.path.join(tmp_dir, ".rootdir.test")
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.config.get_rootdir_path", return_value=dummy_root),
            ):
                mw = MainWindow(initial_dir=tmp_dir)
                self.addCleanup(mw.close)
                self.assertEqual(mw.footer_bar.lbl_status.text(), "")

                mw.set_working_directory(another_dir)
                self.assertEqual(mw.footer_bar.lbl_status.text(), "")

                mw._on_threshold_changed()
                self.assertEqual(mw.footer_bar.lbl_status.text(), "")

                mw._on_worker_progress(3, 10, "clip.mp4")
                self.assertEqual(mw.footer_bar.lbl_status.text(), "[3/10] clip.mp4")

                logging.getLogger("src").info("작업 폴더: C:/another_path")
                _APP.processEvents()
                self.assertEqual(mw.footer_bar.lbl_status.text(), "[3/10] clip.mp4")

                mw._on_worker_completed("scan_fast", None, 0, [])
                self.assertEqual(mw.footer_bar.lbl_status.text(), "분석 완료: 총 0개 영상")

                mw._on_worker_completed("classify_real", None, 0, [])
                self.assertEqual(mw.footer_bar.lbl_status.text(), "폴더 분류 완료: 0개 파일 이동됨")

                mw._on_worker_completed("unclassify_all", None, 5, [])
                self.assertEqual(mw.footer_bar.lbl_status.text(), "분류 복원 완료: 5개 파일이 작업 폴더로 복원됨")

                mw._on_worker_cancelled("사용자에 의해 분석 작업이 중단되었습니다.")
                self.assertEqual(mw.footer_bar.lbl_status.text(), "작업이 중단되었습니다.")

    def test_stability_busy_lock_close_event_and_dir_reset(self) -> None:
        """작업 중 FilterTuningBar 잠금, closeEvent 워커 안전 종료, 폴더 변경 시 결과 초기화를 검증"""
        with tempfile.TemporaryDirectory() as dir1, tempfile.TemporaryDirectory() as dir2:
            dummy_state = os.path.join(dir1, "data", "config.json")
            dummy_root = os.path.join(dir1, ".rootdir.test")
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.config.get_rootdir_path", return_value=dummy_root),
            ):
                clf = VideoClassifier(root_dir=dir1)
                cache = TableCache(dir1)
                cache._data = list(self.videos)
                cache._include_keyframe = True
                VideoClassifierService.inject_cache(dir1, cache)

                mw = MainWindow(classifier=clf)
                rep = clf.get_summary_report(ensure_keyframe=False)
                mw._update_ui_with_report(rep)
                self.assertEqual(mw.table_model.rowCount(), 3)
                self.assertIn("3개", mw.dashboard.card_files.value_label.text())

                # 1. 작업 중 튜닝 컨트롤 잠금 검증
                mw.set_busy_state(True, "진행 중...")
                self.assertFalse(mw.filter_bar.spin_over_ratio.isEnabled())
                self.assertFalse(mw.filter_bar.spin_kf_limit.isEnabled())
                self.assertFalse(mw.filter_bar.spin_small_mb.isEnabled())
                self.assertFalse(mw.filter_bar.edit_exceptions.isEnabled())
                mw.set_busy_state(False)
                self.assertTrue(mw.filter_bar.spin_over_ratio.isEnabled())
                self.assertTrue(mw.filter_bar.edit_exceptions.isEnabled())

                # 2. 폴더 변경 시 이전 분석 결과 및 대시보드 초기화 검증
                mw.set_working_directory(dir2)
                self.assertIsNone(mw.current_report)
                self.assertEqual(mw.table_model.rowCount(), 0)
                self.assertEqual(mw.dashboard.card_files.value_label.text(), "0개")
                self.assertEqual(mw.filter_bar.lbl_filter_count.text(), "표시: 0 / 0개")

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

                mw._worker = cast("ClassifierWorker", DummyRunningWorker())
                with patch.object(mw, "_save_ui_state"):
                    mw.close()
                self.assertEqual(events, ["cancel", ("wait", 3000)])

    def test_exception_validation_and_threshold_sync(self) -> None:
        """MainWindow 예외 식 검증 피드백 및 FilterTuningBar-SettingsDialog 실시간 동기화를 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_state = os.path.join(tmpdir, "data", "config.json")
            dummy_root = os.path.join(tmpdir, ".rootdir")
            with (
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.config.get_rootdir_path", return_value=dummy_root),
            ):
                mw = MainWindow(initial_dir=tmpdir)

                mw.filter_bar.edit_exceptions.setText("v.duration <")
                self.assertEqual(mw.filter_bar.edit_exceptions.property("invalid"), "true")
                self.assertFalse(mw.filter_bar.lbl_exception_error.isHidden())
                self.assertIn("예외 규칙 오류:", mw.filter_bar.lbl_exception_error.text())
                mw.filter_bar.edit_exceptions.setText("v.duration < 150")
                self.assertEqual(mw.filter_bar.edit_exceptions.property("invalid"), "false")
                self.assertTrue(mw.filter_bar.lbl_exception_error.isHidden())

                mw._on_open_settings()
                dlg = mw._dialogs.settings_dialog
                assert dlg is not None
                mw.filter_bar.spin_over_ratio.setValue(4.25)
                mw.filter_bar.spin_kf_limit.setValue(18.5)
                self.assertAlmostEqual(dlg.spin_over_ratio.value(), 4.25, places=2)
                self.assertAlmostEqual(dlg.spin_kf_threshold.value(), 18.5, places=1)
                dlg.close()

    def test_table_ux_empty_state_selection_exceptions_and_export(self) -> None:
        """Empty State 전환, 다중 선택 요약, 중복 없는 예외 추가/제거, 열 너비 합계 및 CSV/로그 내보내기를 검증"""
        self.assertLessEqual(sum(VideoTableView.DEFAULT_COL_WIDTHS.values()), 1540)

        with tempfile.TemporaryDirectory() as tmpdir:
            clf = VideoClassifier(root_dir=tmpdir)
            cache = TableCache(tmpdir)
            cache._data = list(self.videos)
            cache._include_keyframe = True
            VideoClassifierService.inject_cache(tmpdir, cache)

            mw = MainWindow(classifier=clf)
            mw.show()

            # 1. 초기 데이터 없음 상태 오버레이
            self.assertTrue(mw.table_view._empty_overlay.isVisible())
            self.assertEqual(mw.table_view._empty_mode, "no_data")

            # 2. 데이터 로드 후 오버레이 숨김
            rep = clf.get_summary_report(ensure_keyframe=False)
            mw._update_ui_with_report(rep)
            self.assertFalse(mw.table_view._empty_overlay.isVisible())

            # 3. 검색 결과 0건 시 필터 초기화 모드 노출 및 초기화 동작
            mw.filter_bar.search_edit.setText("__no_such_video__")
            self.assertTrue(mw.table_view._empty_overlay.isVisible())
            self.assertEqual(mw.table_view._empty_mode, "filtered_empty")
            mw.table_view._btn_empty_action.click()
            self.assertEqual(mw.filter_bar.search_edit.text(), "")
            self.assertFalse(mw.table_view._empty_overlay.isVisible())

            # 4. 다중 선택 및 요약 표시 검증
            mw.table_view.selectAll()
            self.assertIn("선택 3개", mw.filter_bar.lbl_filter_count.text())

            # 5. 중복 없는 예외 추가 및 제거
            mw._on_append_exceptions(["sample", "SAMPLE", "clip"])
            self.assertEqual(mw.filter_bar.edit_exceptions.text(), "sample, clip")
            mw._on_remove_exceptions(["sample"])
            self.assertEqual(mw.filter_bar.edit_exceptions.text(), "clip")

            # 6. CSV 내보내기 검증
            csv_path = os.path.join(tmpdir, "out.csv")
            exported_count = mw.export_report_to_csv(csv_path)
            self.assertEqual(exported_count, 3)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0], VideoTableModel.HEADERS)

            # 7. 로그 이력 및 로그 창 검증
            logging.getLogger("src").warning("테스트 경고 메시지")
            _APP.processEvents()
            self.assertTrue(
                any("테스트 경고 메시지" in line for line in mw._dialogs.log_history)
            )
            mw.show_log_dialog()
            log_dlg = mw._dialogs.log_dialog
            assert log_dlg is not None
            self.assertIn("테스트 경고 메시지", log_dlg.text_edit.toPlainText())
            log_dlg.close()

    def test_classifier_worker_pseudo_classify_unmark_and_cancel(self) -> None:
        """ClassifierWorker의 가분류/가분류 해제 시그널 방출과 중단 상태 콜백을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            for v in self.videos:
                with open(os.path.join(tmpdir, v.filename), "wb") as f:
                    f.write(b"00")

            clf = VideoClassifier(root_dir=tmpdir)
            cache = TableCache.__new__(TableCache)
            cache._root_dir = tmpdir
            cache._data = [VideoProps(**{**v.__dict__}) for v in self.videos]
            cache._include_keyframe = True
            VideoClassifierService.inject_cache(
                tmpdir, cache, signature=compute_dir_signature(tmpdir)
            )

            completed_events: list[tuple[str, Any, Any, Any]] = []
            worker = ClassifierWorker(
                classifier=clf,
                task_type=ClassifierWorker.TASK_CLASSIFY_PSEUDO,
            )
            worker.task_completed.connect(
                lambda t, r, m, w: completed_events.append((t, r, m, w))
            )
            worker.run()

            self.assertEqual(len(completed_events), 1)
            task_type, rep, _, _ = completed_events[0]
            self.assertEqual(task_type, ClassifierWorker.TASK_CLASSIFY_PSEUDO)
            self.assertEqual(rep.total_count, 3)
            self.assertTrue(any(v.moved_dirname is not None for v in clf.video_props))

            # 가분류 표시 초기화 워커 검증
            worker_unmark = ClassifierWorker(
                classifier=clf,
                task_type=ClassifierWorker.TASK_UNCLASSIFY_PSEUDO,
            )
            worker_unmark.run()
            self.assertTrue(all(v.moved_dirname is None for v in clf.video_props))

        worker_dummy = ClassifierWorker(
            classifier=cast("Any", MagicMock()),
            task_type=ClassifierWorker.TASK_SCAN_FAST,
        )
        self.assertFalse(worker_dummy.is_cancelled())
        worker_dummy.cancel()
        self.assertTrue(worker_dummy.is_cancelled())

    def test_action_button_availability_and_drag_drop_overlay(self) -> None:
        """하단 분류·복원 버튼의 상태 연동 활성화 및 폴더 드래그 앤 드롭 안내 오버레이 동작을 검증"""
        self.addCleanup(VideoClassifierService.invalidate_shared_cache)
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
                patch("src.core.config.get_rootdir_path", return_value=dummy_root),
            ):
                clf = VideoClassifier(root_dir=empty_dir)
                mw = MainWindow(classifier=clf)
                self.addCleanup(mw.close)
                mw.show()

                # 1. 스캔 전 빈 폴더: 분류 이동·복원 모두 비활성화
                self.assertFalse(mw.footer_bar.btn_real_classify.isEnabled())
                self.assertFalse(mw.footer_bar.btn_unclassify_all.isEnabled())

                # 2. 하위 폴더가 있는 경로 전환 시 분류 복원 활성화
                mw.set_working_directory(classified_dir)
                self.assertFalse(mw.footer_bar.btn_real_classify.isEnabled())
                self.assertTrue(mw.footer_bar.btn_unclassify_all.isEnabled())

                # 3. 리포트 적용(미이동 대상 존재): 폴더 분류 이동 활성화
                cache = TableCache(empty_dir)
                cache._data = [VideoProps(**{**v.__dict__}) for v in self.videos]
                cache._include_keyframe = True
                VideoClassifierService.inject_cache(empty_dir, cache)
                mw.set_working_directory(empty_dir)
                rep = clf.get_summary_report(ensure_keyframe=False)
                mw._update_ui_with_report(rep)
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

    def test_gui_does_not_depend_on_cui_layer(self) -> None:
        """GUI 계층이 CUI 계층에 의존하지 않고 공용 core 정렬·예외 규칙 모듈을 직접 참조함을 검증"""
        gui_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "src", "gui")
        )
        for dirpath, _dirnames, filenames in os.walk(gui_root):
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(dirpath, fname)
                with open(fpath, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=fpath)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertFalse(
                                alias.name.startswith("src.cui"),
                                f"GUI->CUI 역방향 임포트 발견: {fpath} ({alias.name})",
                            )
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        self.assertFalse(
                            node.module.startswith("src.cui"),
                            f"GUI->CUI 역방향 임포트 발견: {fpath} ({node.module})",
                        )

    def test_persistent_ui_state_without_classifier_and_cui_rule_preservation(
        self,
    ) -> None:
        """분류기 미바인딩 상태에서도 임계값·예외·필터·스캔모드가 즉시 저장·복원되고 CUI 람다 규칙이 보존됨을 검증"""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            tempfile.TemporaryDirectory() as dir_work,
        ):
            dummy_root = os.path.join(tmpdir, ".rootdir")
            dummy_state = os.path.join(tmpdir, "data", "config.json")

            with (
                patch("src.core.config.get_rootdir_path", return_value=dummy_root),
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
            ):
                # 1. 작업 폴더가 없는 초기 상태(_classifier is None)에서도 변경 즉시 저장
                mw1 = MainWindow()
                self.addCleanup(mw1.close)
                self.assertIsNone(mw1.classifier)

                mw1.filter_bar.spin_over_ratio.setValue(1.55)
                mw1.filter_bar.edit_exceptions.setText("@hevc")
                mw1.filter_bar.search_edit.setText("clip")
                mw1.filter_bar.combo_zone.setCurrentIndex(
                    mw1.filter_bar.combo_zone.findData("OVER")
                )
                mw1.filter_bar.combo_target.setCurrentIndex(
                    mw1.filter_bar.combo_target.findData("ENCODE_ONLY")
                )
                mw1.header_bar.split_scan.set_mode("full", emit_signal=True)
                mw1.set_working_directory(dir_work)
                mw1.close()

                # 2. 새 창 실행 시 이전 UI·설정·최근 폴더 자동 복원 확인
                mw2 = MainWindow()
                self.addCleanup(mw2.close)
                self.assertEqual(
                    os.path.normpath(mw2.header_bar.path_edit.text()),
                    os.path.normpath(dir_work),
                )
                self.assertAlmostEqual(mw2.filter_bar.spin_over_ratio.value(), 1.55)
                self.assertEqual(mw2.filter_bar.get_exception_tokens(), ["@hevc"])
                self.assertEqual(mw2.filter_bar.search_edit.text(), "clip")
                self.assertEqual(mw2.filter_bar.combo_zone.currentData(), "OVER")
                self.assertEqual(
                    mw2.filter_bar.combo_target.currentData(), "ENCODE_ONLY"
                )
                self.assertEqual(mw2.header_bar.current_scan_mode, "full")
                state_after = load_app_state()
                self.assertEqual(state_after.recent_dirs, [os.path.normpath(dir_work)])
                self.assertTrue(bool(state_after.window_geometry))
                self.assertTrue(bool(state_after.table_header_state))

                # 3. 환경설정 대화상자 전체 복원 시 메인 창 컨트롤·분류기 동기화 확인
                mw2._on_open_settings()
                dlg = mw2._dialogs.settings_dialog
                assert dlg is not None
                with patch.object(
                    QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.Yes,
                ):
                    dlg.btn_reset_defaults.click()
                self.assertAlmostEqual(
                    mw2.filter_bar.spin_over_ratio.value(),
                    float(DEFAULT_CONFIGS["THRESHOLD_OPTIMAL_BITRATE_RATE"]),
                )
                self.assertEqual(mw2.filter_bar.get_exception_tokens(), [])
                self.assertEqual(mw2.filter_bar.search_edit.text(), "")
                self.assertEqual(mw2.filter_bar.combo_zone.currentData(), "ALL")
                self.assertEqual(mw2.header_bar.current_scan_mode, "fast")
                dlg.close()

                # 4. CUI에서 등록한 람다 예외 규칙이 GUI 토큰 편집 후에도 유지됨을 검증
                clf = VideoClassifier(root_dir=dir_work)
                clf.add_exception_rule(lambda v: "keep_cui" in v.filename)
                cache = TableCache(dir_work)
                cache._data = [
                    VideoProps(
                        filename="keep_cui_over.mp4",
                        width=1920,
                        height=1080,
                        rotate_type=0,
                        fps=30.0,
                        vid_kbps=9000,
                        aud_kbps=128,
                        vid_size_MB=80.0,
                        duration=60.0,
                        codec="hevc",
                        keyframe_interval=1.0,
                    ),
                    VideoProps(
                        filename="normal_over.mp4",
                        width=1920,
                        height=1080,
                        rotate_type=0,
                        fps=30.0,
                        vid_kbps=9000,
                        aud_kbps=128,
                        vid_size_MB=80.0,
                        duration=60.0,
                        codec="h264",
                        keyframe_interval=1.0,
                    ),
                ]
                cache._include_keyframe = True
                VideoClassifierService.inject_cache(dir_work, cache)

                mw3 = MainWindow(classifier=clf)
                self.addCleanup(mw3.close)
                mw3.filter_bar.edit_exceptions.setText("v.codec == 'h264'")
                rep = mw3.current_report
                assert rep is not None
                emap = {e.video.filename: e for e in rep.evaluations}
                self.assertIsNone(emap["keep_cui_over.mp4"].target_dirname)
                self.assertIsNone(emap["normal_over.mp4"].target_dirname)


if __name__ == "__main__":
    unittest.main(verbosity=2)
