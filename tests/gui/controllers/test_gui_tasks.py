"""GUI 비동기 작업 수명주기, 설정 적용 가드와 세션 동기화 검증"""

import os
import tempfile
import time
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.models import VideoProps
from src.core.settings.app_state import AppState, save_app_state
from src.gui.controllers.session_controller import apply_settings_to_classifier
from src.gui.controllers.task_runner import TaskRunner, format_completion_status
from src.gui.controllers.worker import ClassifierWorker
from src.gui.main_window import MainWindow
from src.service.cache import DEFAULT_CACHE_REGISTRY
from tests._gui_helpers import GuiTestCase


class TestGuiTasksAndSessionIntegration(GuiTestCase):
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
        # 상태 파일은 테스트마다 고유한 임시 폴더에 둬 동시 실행 프로세스가 같은 파일을 교체하지 않게 함
        with tempfile.TemporaryDirectory() as env_dir:
            state_path = os.path.join(env_dir, "data", "config.json")
            rootdir_path = os.path.join(env_dir, ".rootdir.test")
            with open(rootdir_path, "w", encoding="utf-8") as f:
                f.write(root_dir)
            with (
                patch("src.core.settings.app_state.get_state_path", return_value=state_path),
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
                    "src.service.analysis_probe.probe_files",
                    side_effect=RuntimeError("boom-probe"),
                ),
                patch("src.gui.controllers.task_runner.QMessageBox.critical") as mock_critical,
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
            self.assertEqual(created_clf.config.bitrate_model.base_target_kbps, 1800)

            # MainWindow 연동: 동일 경로 설정 변경 시 캐시 유지 재평가, 경로 변경 시 뷰 초기화
            with open(os.path.join(dir1, "clip.mp4"), "wb") as f:
                f.write(b"clip-bytes")
            clip = VideoProps(
                "clip.mp4", 1280, 720, 0, 30.0, 4000, 128, 80.0, 10.0, "h264", 1.0
            )
            with (
                self._isolated_env(dir1),
                patch("src.service.analysis_probe.probe_files", return_value=[clip]),
                patch("src.gui.controllers.task_runner.QMessageBox.critical") as mock_critical,
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
            patch("src.service.analysis_probe.probe_files", return_value=[]),
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

    def test_isolated_state_path_is_not_in_shared_temp_or_root_dir(self) -> None:
        """상태 파일이 시스템 임시 폴더 바로 아래의 고정 폴더나 대상 폴더 안에 생기지 않음을 검증"""
        with tempfile.TemporaryDirectory() as root_dir, self._isolated_env(root_dir) as (state_path, rootdir_path):
            data_parent = os.path.dirname(os.path.dirname(state_path))
            self.assertNotEqual(os.path.normcase(data_parent), os.path.normcase(tempfile.gettempdir()))
            for path in (state_path, rootdir_path):
                self.assertFalse(os.path.normcase(path).startswith(os.path.normcase(root_dir) + os.sep))
            self.assertFalse(os.path.exists(os.path.join(root_dir, "data")))
            self.assertFalse(os.path.exists(os.path.join(root_dir, ".rootdir.test")))

    def test_format_completion_status_maps_builtin_task_types(self) -> None:
        """작업 유형별 완료 상태 문구를 검증"""
        self.assertEqual(
            format_completion_status(ClassifierWorker.TASK_ANALYZE_FAST, None),
            "분석 완료: 총 0개 영상",
        )

    def test_full_analysis_completion_runs_the_follow_up_stage(self) -> None:
        """정밀 분석 완료가 상태 줄 정리 뒤에 후속 단계를 부르고, 실패와 취소 시 후속 예약을 비우는지 검증"""
        from unittest.mock import MagicMock

        host = MagicMock()
        events: list[str] = []
        host.set_status_text.side_effect = lambda text, *_a: events.append(f"status:{text}")
        host.frame_analysis.on_analysis_finished.side_effect = lambda: events.append("followup")
        runner = TaskRunner(host)
        runner.handle_completed(ClassifierWorker.TASK_ANALYZE_FULL, MagicMock(total_count=1))
        host.frame_analysis.on_analysis_finished.assert_called_once_with()
        self.assertEqual(events, ["status:", "followup"])
        runner.handle_completed(ClassifierWorker.TASK_ANALYZE_FAST, MagicMock(total_count=1))
        self.assertEqual(host.frame_analysis.on_analysis_finished.call_count, 1)
        runner.handle_cancelled("작업이 중단되었습니다.")
        host.frame_analysis.clear_pending_followup.assert_called_once_with()
        with patch("src.gui.controllers.task_runner.QMessageBox.critical"):
            runner.handle_failed("오류")
        self.assertEqual(host.frame_analysis.clear_pending_followup.call_count, 2)

    def test_fast_analyze_completion_shows_full_analysis_needed_when_keyframe_missing(self) -> None:
        """빠른 분석 완료 뒤 키프레임 미측정 영상이 있으면 정밀 분석 필요 안내와 툴팁을 띄우는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "clip.mp4"), "wb") as f:
                f.write(b"clip-bytes")
            clip = VideoProps(
                "clip.mp4", 1280, 720, 0, 30.0, 4000, 128, 80.0, 10.0, "h264", None
            )
            with (
                self._isolated_env(tmpdir),
                patch("src.service.analysis_probe.probe_files", return_value=[clip]),
            ):
                DEFAULT_CACHE_REGISTRY.invalidate()
                win = self._create_isolated_window()
                win.start_analyze(include_keyframe=False)
                self._wait_for_worker_idle(win)
                self.assertIn("정밀 분석이 필요한 영상 1개", win.footer_bar.lbl_status.text())
                self.assertIn(
                    "키프레임 간격을 재지 않은 영상은 키프레임 교정 대상인지 알 수 없습니다.",
                    win.footer_bar.lbl_status.toolTip(),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
