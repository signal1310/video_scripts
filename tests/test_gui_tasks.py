"""GUI 비동기 작업 수명주기, 설정 적용 가드와 세션 동기화 검증"""

import os
import tempfile
import time
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox

from src.core.app_state import AppState, save_app_state
from src.core.config import ClassifierConfig
from src.core.messages import format_unclassify_done
from src.core.models import VideoProps
from src.gui.main_window import MainWindow
from src.gui.session_controller import apply_settings_to_classifier
from src.gui.task_runner import format_completion_status
from src.gui.worker import ClassifierWorker
from src.service.cache import DEFAULT_CACHE_REGISTRY


class TestGuiTasksAndSessionIntegration(unittest.TestCase):
    """GUI 비동기 QThread 작업 수명주기와 세션/설정 연동을 검증"""

    _app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        """테스트 전체에서 공유할 QApplication 인스턴스를 준비"""
        existing = QApplication.instance()
        if isinstance(existing, QApplication):
            cls._app = existing
        else:
            cls._app = QApplication([])

    @contextmanager
    def _isolated_env(self, root_dir: str) -> Generator[tuple[str, str]]:
        state_path = os.path.join(os.path.dirname(root_dir), "data", "config.json")
        rootdir_path = os.path.join(os.path.dirname(root_dir), ".rootdir.test")
        with open(rootdir_path, "w", encoding="utf-8") as f:
            f.write(root_dir)
        with (
            patch("src.core.app_state.get_state_path", return_value=state_path),
            patch("src.core.paths.get_rootdir_path", return_value=rootdir_path),
        ):
            save_app_state(AppState())
            yield state_path, rootdir_path

    def _wait_for_worker_idle(self, win: MainWindow, timeout_sec: float = 5.0) -> None:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            self._app.processEvents()
            worker = win.active_worker
            if worker is None or not worker.isRunning():
                if worker is not None:
                    worker.wait(1000)
                self._app.processEvents()
                time.sleep(0.015)
                self._app.processEvents()
                if not win.task_runner.is_running():
                    return
            time.sleep(0.01)
        self.fail("비동기 GUI 작업이 제한 시간 내에 종료되지 않았습니다.")

    def _create_isolated_window(self) -> MainWindow:
        win = MainWindow()
        self.addCleanup(win.close)
        self.addCleanup(win.task_runner.shutdown)
        return win

    def test_async_analyze_confirm_cancel_real_classify_and_unclassify_roundtrip(
        self,
    ) -> None:
        """QThread 스캔 -> 확인 다이얼로그 취소 가드 -> 실이동 분류 -> 전체 원복까지의 비동기 E2E 흐름을 검증"""
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate)
        with tempfile.TemporaryDirectory() as outer_dir:
            media_dir = os.path.join(outer_dir, "media")
            os.makedirs(media_dir, exist_ok=True)
            vid_path = os.path.join(media_dir, "over_async.mp4")
            with open(vid_path, "wb") as f:
                f.write(b"async-video-bytes")

            over_vid = VideoProps(
                "over_async.mp4",
                1280,
                720,
                0,
                30.0,
                5000,
                128,
                120.0,
                10.0,
                "h264",
                1.0,
            )
            with (
                self._isolated_env(media_dir),
                patch(
                    "src.service.cache.probe_directory",
                    return_value=[over_vid],
                ),
                patch("src.gui.task_runner.QMessageBox.critical") as mock_critical,
            ):
                DEFAULT_CACHE_REGISTRY.invalidate()
                win = self._create_isolated_window()

                # 1. 비동기 스캔 수행
                win.start_analyze(include_keyframe=False)
                self._wait_for_worker_idle(win)
                mock_critical.assert_not_called()
                self.assertIsNotNone(win.current_report)
                self.assertEqual(win.table_model.rowCount(), 1)
                self.assertTrue(os.path.isfile(vid_path))

                # 2. 실이동 확인 창에서 'No' 선택 시 워커를 띄우지 않고 중단
                with patch(
                    "src.gui.task_runner.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.No,
                ) as mock_q:
                    win.run_real_classify(confirm=True)
                mock_q.assert_called_once()
                self.assertFalse(win.task_runner.is_running())
                self.assertTrue(os.path.isfile(vid_path))

                # 3. 실이동 분류 수행
                win.run_real_classify(confirm=False)
                self._wait_for_worker_idle(win)
                mock_critical.assert_not_called()
                moved_dir = os.path.join(media_dir, "_1_표준인코딩_CQ22")
                moved_path = os.path.join(moved_dir, "over_async.mp4")
                self.assertFalse(os.path.isfile(vid_path))
                self.assertTrue(os.path.isfile(moved_path))
                self.assertEqual(over_vid.moved_dirname, "_1_표준인코딩_CQ22")

                # 4. 실이동 전체 원복 수행
                with (
                    patch("src.gui.task_runner.QMessageBox.warning") as mock_warning,
                    patch(
                        "src.gui.task_runner.QMessageBox.question",
                        return_value=QMessageBox.StandardButton.Yes,
                    ) as mock_confirm,
                ):
                    win.run_unclassify()
                    self._wait_for_worker_idle(win)
                mock_critical.assert_not_called()
                mock_confirm.assert_called_once()
                mock_warning.assert_not_called()
                self.assertTrue(os.path.isfile(vid_path))
                self.assertFalse(os.path.isdir(moved_dir))
                self.assertIsNone(over_vid.moved_dirname)

    def test_async_worker_failure_propagates_error_dialog_and_unlocks_busy_state(
        self,
    ) -> None:
        """백그라운드 QThread 예외 발생 시 task_failed 시그널이 상태 표시줄, 경고창, UI 잠금 해제로 전파되는지 검증"""
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate)
        with tempfile.TemporaryDirectory() as outer_dir:
            media_dir = os.path.join(outer_dir, "media")
            os.makedirs(media_dir, exist_ok=True)
            with open(os.path.join(media_dir, "fail.mp4"), "wb") as f:
                f.write(b"fail-video")

            with (
                self._isolated_env(media_dir),
                patch(
                    "src.service.cache.probe_directory",
                    side_effect=RuntimeError("boom-probe"),
                ),
                patch("src.gui.task_runner.QMessageBox.critical") as mock_critical,
            ):
                DEFAULT_CACHE_REGISTRY.invalidate()
                win = self._create_isolated_window()
                win.start_analyze(include_keyframe=False)
                self._wait_for_worker_idle(win)

                mock_critical.assert_called_once()
                self.assertIn("boom-probe", mock_critical.call_args[0][2])
                self.assertFalse(win.task_runner.is_running())
                self.assertEqual(win.footer_bar.progress_bar.value(), 0)
                self.assertIn("boom-probe", win.footer_bar.lbl_status.text())

    def test_apply_settings_session_controller_and_window_sync(self) -> None:
        """apply_settings_to_classifier 및 MainWindow.apply_settings의 경로 유지 재평가, 경로 변경 초기화를 검증"""
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate)
        with tempfile.TemporaryDirectory() as outer_dir:
            dir1 = os.path.join(outer_dir, "dir1")
            dir2 = os.path.join(outer_dir, "dir2")
            os.makedirs(dir1, exist_ok=True)
            os.makedirs(dir2, exist_ok=True)
            missing_dir = os.path.join(outer_dir, "missing")

            from src.core.config import BitrateModelConfig

            # current=None 초기 생성 및 무효 경로 방어 검증
            cfg_init = ClassifierConfig(bitrate_model=BitrateModelConfig(base_target_kbps=1800))
            none_clf, changed_invalid, valid_invalid = apply_settings_to_classifier(
                None, "", cfg_init, missing_dir
            )
            self.assertIsNone(none_clf)
            self.assertFalse(changed_invalid)
            self.assertIsNone(valid_invalid)

            created_clf, changed_created, valid_created = apply_settings_to_classifier(
                None, "", cfg_init, dir1
            )
            self.assertIsNotNone(created_clf)
            assert created_clf is not None
            self.assertTrue(changed_created)
            self.assertEqual(valid_created, dir1)
            self.assertTrue(created_clf.is_pseudo_classify)
            self.assertEqual(created_clf.config.bitrate_model.base_target_kbps, 1800)

            # MainWindow 연동: 동일 경로 설정 변경 시 캐시 유지 재평가, 경로 변경 시 뷰 초기화
            with open(os.path.join(dir1, "clip.mp4"), "wb") as f:
                f.write(b"clip-bytes")
            clip = VideoProps(
                "clip.mp4", 1280, 720, 0, 30.0, 4000, 128, 80.0, 10.0, "h264", 1.0
            )
            with (
                self._isolated_env(dir1),
                patch("src.service.cache.probe_directory", return_value=[clip]),
                patch("src.gui.task_runner.QMessageBox.critical") as mock_critical,
            ):
                DEFAULT_CACHE_REGISTRY.invalidate()
                win = self._create_isolated_window()
                win.start_analyze(include_keyframe=False)
                self._wait_for_worker_idle(win)
                mock_critical.assert_not_called()
                self.assertEqual(win.table_model.rowCount(), 1)

                # 동일 경로 + 설정 변경 -> 캐시 재사용으로 테이블 데이터 유지
                updated_cfg = ClassifierConfig(
                    bitrate_model=BitrateModelConfig(base_target_kbps=1600)
                )
                win.apply_settings(updated_cfg, dir1)
                self.assertIsNotNone(win.current_report)
                self.assertEqual(win.table_model.rowCount(), 1)
                self.assertIsNotNone(win.classifier)
                assert win.classifier is not None
                self.assertEqual(
                    win.classifier.config.bitrate_model.base_target_kbps, 1600
                )

                # 다른 유효 경로로 변경 -> path_edit 동기화 및 분석 뷰 초기화
                win.apply_settings(updated_cfg, dir2)
                self.assertEqual(
                    os.path.normpath(win.header_bar.path_edit.text()),
                    os.path.normpath(dir2),
                )
                self.assertIsNone(win.current_report)
                self.assertEqual(win.table_model.rowCount(), 0)

    def test_apply_settings_is_ignored_while_task_running(self) -> None:
        """작업 실행 중 MainWindow.apply_settings 호출이 세션 설정을 변경하지 않음을 검증"""
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate)
        from src.core.config import BitrateModelConfig

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            self._isolated_env(tmpdir),
            patch("src.service.cache.probe_directory", return_value=[]),
        ):
            DEFAULT_CACHE_REGISTRY.invalidate()
            win = self._create_isolated_window()
            win.start_analyze(include_keyframe=False)
            self._wait_for_worker_idle(win)
            assert win.classifier is not None
            base_kbps = win.classifier.config.bitrate_model.base_target_kbps

            changed_cfg = ClassifierConfig(
                bitrate_model=BitrateModelConfig(base_target_kbps=base_kbps + 100)
            )
            with patch.object(win.task_runner, "is_running", return_value=True):
                win.apply_settings(changed_cfg, tmpdir)
            self.assertEqual(
                win.classifier.config.bitrate_model.base_target_kbps, base_kbps
            )

    def test_unclassify_collision_logs_single_warn_and_clean_dialog(self) -> None:
        """분류 복원 중 이름 충돌 시 로그에는 '[WARN] '이 한 번만 기록되고 경고 대화상자에는 접두사 없는 안내가 표시되는지 검증"""
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate)
        with tempfile.TemporaryDirectory() as outer_dir:
            media_dir = os.path.join(outer_dir, "media")
            moved_dir = os.path.join(media_dir, "_1_표준인코딩_CQ22")
            os.makedirs(moved_dir, exist_ok=True)
            with open(os.path.join(media_dir, "dup.mp4"), "wb") as f:
                f.write(b"root-dup")
            with open(os.path.join(moved_dir, "dup.mp4"), "wb") as f:
                f.write(b"sub-dup")

            with (
                self._isolated_env(media_dir),
                patch("src.gui.task_runner.QMessageBox.critical") as mock_critical,
                patch("src.gui.task_runner.QMessageBox.warning") as mock_warning,
            ):
                DEFAULT_CACHE_REGISTRY.invalidate()
                win = self._create_isolated_window()
                with patch(
                    "src.gui.task_runner.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.Yes,
                ):
                    win.run_unclassify()
                self._wait_for_worker_idle(win)

                mock_critical.assert_not_called()
                mock_warning.assert_called_once()
                dialog_title = mock_warning.call_args[0][1]
                dialog_body = mock_warning.call_args[0][2]
                self.assertEqual(dialog_title, "작업 폴더로 복원 충돌 안내")
                self.assertNotIn("[WARN]", dialog_body)
                self.assertNotIn("[ERROR]", dialog_body)
                self.assertIn("dup.mp4", dialog_body)

                warn_logs = [line for line in win.dialogs.log_history if "dup.mp4" in line]
                self.assertEqual(len(warn_logs), 1)
                self.assertRegex(warn_logs[0], r"^\[\d{2}:\d{2}:\d{2}\] \[WARN\] ")
                self.assertNotIn("[WARN] [WARN]", warn_logs[0])

    def test_format_completion_status_maps_builtin_task_types(self) -> None:
        """작업 유형별 완료 상태 문구와 스캔 상태 줄 비움 조건을 검증"""
        self.assertEqual(
            format_completion_status(ClassifierWorker.TASK_ANALYZE_FAST, None, 0, []),
            "분석 완료: 총 0개 영상",
        )
        self.assertEqual(
            format_completion_status(ClassifierWorker.TASK_CLASSIFY_REAL, None, 2, []),
            "분류 폴더로 이동 완료: 2개 파일 이동됨",
        )
        self.assertEqual(
            format_completion_status(ClassifierWorker.TASK_UNCLASSIFY_ALL, None, 4, ["충돌"]),
            format_unclassify_done(4, 1),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
