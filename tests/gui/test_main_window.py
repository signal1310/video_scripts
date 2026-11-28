"""메인 창의 작은 동작 함수(검색 포커스, 폴더 추가, 미리 보기 설정 반영, 작업 취소, CSV 내보내기, 예상 문구)를 검증"""

import os
import tempfile
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.models import SummaryReport
from src.gui.dialogs.log_dialog import LogTab
from src.gui.main_window import MainWindow
from src.service.analysis_store import AnalysisStore
from src.service.session import ClassifierSession
from tests._factories import make_report
from tests._gui_helpers import GuiTestCase, isolate_app_state

_APP = QApplication.instance() or QApplication([])
_WAIT_SEC: float = 5.0
_RELEASE_DELAY_SEC: float = 0.05


class MainWindowActionTests(GuiTestCase):
    """메인 창 동작 함수가 연결된 위젯과 세션, 대화상자에 올바른 호출을 하는지 검증"""

    def setUp(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        os.makedirs(os.path.join(work.name, "data"))
        store = AnalysisStore(os.path.join(work.name, "data", "analysis.sqlite3"))
        classifier = ClassifierSession(root_dir=work.name, config=ClassifierConfig(), store=store)
        self.window = MainWindow(classifier=classifier)
        self.addCleanup(self.window.close)

    def test_focus_search_moves_focus_and_selects_the_search_text(self) -> None:
        """검색 포커스 동작이 검색 입력의 글을 전체 선택하는지 검증"""
        self.window.filter_bar.search_edit.setText("clip")
        self.window._focus_search()
        self.assertEqual(self.window.filter_bar.search_edit.selectedText(), "clip")

    def test_add_directory_menu_passes_the_chosen_folder_on(self) -> None:
        """작업 폴더 추가 동작이 고른 폴더를 추가 함수에 넘기는지 검증"""
        with (
            mock.patch("src.gui.main_window.browse_directory", return_value="D:/extra") as browse,
            mock.patch.object(self.window, "add_working_directory") as add,
        ):
            self.window._on_add_dir()
        browse.assert_called_once()
        add.assert_called_once_with("D:/extra")

    def test_add_directory_cancelled_by_user_adds_nothing(self) -> None:
        """작업 폴더 추가 대화상자를 취소하면 추가 함수를 부르지 않는지 검증"""
        with (
            mock.patch("src.gui.main_window.browse_directory", return_value=""),
            mock.patch.object(self.window, "add_working_directory") as add,
        ):
            self.window._on_add_dir()
        add.assert_not_called()

    def test_add_working_directory_resets_view_only_when_session_changed(self) -> None:
        """폴더 추가가 세션에서 바뀌었을 때만 분석 뷰를 초기화하고 복원을 시작하는지 검증"""
        with (
            mock.patch.object(self.window._session, "add_directory", return_value=False),
            mock.patch.object(self.window, "_clear_analysis_view") as clear,
        ):
            self.window.add_working_directory("D:/extra")
        clear.assert_not_called()
        with (
            mock.patch.object(self.window._session, "add_directory", return_value=True),
            mock.patch.object(self.window, "_clear_analysis_view") as clear,
            mock.patch.object(self.window._restore_flow, "start") as restore,
        ):
            self.window.add_working_directory("D:/extra")
        clear.assert_called_once()
        restore.assert_called_once()

    def test_add_working_directory_notes_the_folder_change(self) -> None:
        """폴더 추가가 성공하면 폴더 분류로 변경 기록을 남기는지 검증"""
        with (
            mock.patch.object(self.window._session, "add_directory", return_value=True),
            mock.patch.object(self.window, "_clear_analysis_view"),
            mock.patch.object(self.window._restore_flow, "start"),
            mock.patch.object(self.window, "_current_root_dir", return_value="C:/media/movies"),
            mock.patch("src.gui.main_window.note_event") as noted,
        ):
            self.window.add_working_directory("D:/extra")
        self.assertEqual(noted.call_args.args, ("작업 폴더", "변경: movies"))

    def test_set_working_directory_notes_the_change_only_when_the_folder_changed(self) -> None:
        """작업 폴더 전환이 실제로 바뀐 경우에만 폴더 변경을 남기는지 검증"""
        with (
            mock.patch.object(self.window._session, "switch_directory", return_value=False),
            mock.patch("src.gui.main_window.note_event") as unchanged,
        ):
            self.window.set_working_directory("C:/media")
        unchanged.assert_not_called()
        with (
            mock.patch.object(self.window._session, "switch_directory", return_value=True),
            mock.patch.object(self.window, "_clear_analysis_view"),
            mock.patch.object(self.window._restore_flow, "start"),
            mock.patch.object(self.window, "_current_root_dir", return_value="D:/clips"),
            mock.patch("src.gui.main_window.note_event") as changed,
        ):
            self.window.set_working_directory("D:/clips")
        self.assertEqual(changed.call_args.args, ("작업 폴더", "변경: clips"))

    def test_refresh_current_folder_requests_an_immediate_resync(self) -> None:
        """새로고침 요청이 현재 작업 폴더를 즉시 다시 읽도록 점검을 요청하는지 검증"""
        with (
            mock.patch.object(self.window, "_current_root_dir", return_value="C:/media"),
            mock.patch.object(self.window._external_resync, "request") as request,
            mock.patch.object(self.window, "set_status_text") as status,
        ):
            self.window.refresh_current_folder()
        request.assert_called_once_with(immediate=True)
        status.assert_not_called()

    def test_refresh_without_a_folder_only_shows_a_status_note(self) -> None:
        """작업 폴더가 없으면 점검을 요청하지 않고 안내만 남기는지 검증"""
        with (
            mock.patch.object(self.window, "_current_root_dir", return_value=""),
            mock.patch.object(self.window._external_resync, "request") as request,
            mock.patch.object(self.window, "set_status_text") as status,
        ):
            self.window.refresh_current_folder()
        request.assert_not_called()
        self.assertIn("작업 폴더", status.call_args.args[0])

    def test_refresh_button_click_goes_through_the_refresh_slot(self) -> None:
        """헤더 새로고침 버튼과 단축키 명령이 같은 다시 읽기 동작으로 이어지는지 검증"""
        with (
            mock.patch.object(self.window._external_resync, "request") as request,
            mock.patch.object(self.window, "set_busy_state"),
        ):
            self.window.header_bar.btn_refresh.click()
            self.assertEqual(self.window.shortcuts.effective_shortcuts["file.refresh"], "Ctrl+R")
            self.assertTrue(self.window.shortcuts.trigger_command_for_key("Ctrl+R"))
        self.assertEqual(request.call_args_list, [mock.call(immediate=True)] * 2)

    def test_recursive_toggle_resets_view_only_when_scan_changed(self) -> None:
        """하위 폴더 포함 전환이 스캔 설정을 바꿨을 때만 분석 뷰를 초기화하는지 검증"""
        with (
            mock.patch.object(self.window._session, "set_recursive_scan", return_value=False),
            mock.patch.object(self.window, "_clear_analysis_view") as clear,
        ):
            self.window._on_recursive_toggled(True)
        clear.assert_not_called()
        with (
            mock.patch.object(self.window._session, "set_recursive_scan", return_value=True),
            mock.patch.object(self.window, "_clear_analysis_view") as clear,
            mock.patch.object(self.window._restore_flow, "start"),
        ):
            self.window._on_recursive_toggled(True)
        clear.assert_called_once()

    def test_preview_audio_change_is_saved_in_the_session_state(self) -> None:
        """미리 보기 창의 음량과 음소거 변경이 세션 상태에 저장되는지 검증"""
        self.window._on_preview_audio_changed(40, True)
        ui = self.window._session.state.ui
        self.assertEqual((ui.preview_volume, ui.preview_muted), (40, True))

    def test_preview_rate_change_is_saved_in_the_session_state(self) -> None:
        """미리 보기 창의 배속 변경이 세션 상태에 저장되는지 검증"""
        self.window._on_preview_rate_changed(1.5)
        self.assertEqual(self.window._session.state.ui.preview_playback_rate, 1.5)

    def test_preview_settings_change_schedules_a_delayed_save(self) -> None:
        """미리 보기 설정 변경이 지연 저장으로 예약되는지 검증"""
        with mock.patch.object(self.window._session, "schedule_preview_persist") as schedule:
            self.window._on_preview_settings_changed()
        schedule.assert_called_once()

    def test_analysis_estimate_is_empty_without_a_classifier(self) -> None:
        """분류 세션이 없으면 분석 예상 문구가 빈 문자열인지 검증"""
        with mock.patch.object(self.window._session, "classifier", None):
            self.assertEqual(self.window.frame_analysis.analysis_estimate_text("fast"), "")

    def test_analysis_estimate_with_a_classifier_is_text(self) -> None:
        """분류 세션이 있으면 분석 예상 문구를 문자열로 돌려주고 화질 분석은 대상 수를 쓰는지 검증"""
        self.assertIsInstance(
            self.window.frame_analysis.analysis_estimate_text("full"), str
        )
        self.assertEqual(self.window.frame_analysis.analysis_estimate_text("frame"), "")
        self.window._current_report = make_report([])
        self.assertEqual(self.window.frame_analysis.analysis_estimate_text("frame"), "대상 0개")
        provider = self.window.header_bar.split_analyze._estimate_provider
        assert provider is not None
        self.assertIs(getattr(provider, "__self__", None), self.window.frame_analysis)

    def test_header_frame_button_runs_frame_mode_once(self) -> None:
        """상단 화질 분석 버튼을 눌러도 이중 연결 없이 _start가 한 번만 실행되는지 검증"""
        from tests._factories import make_eval, make_video

        self.window._current_report = make_report(
            [make_eval(video=make_video(filename="a.mp4", keyframe_interval=1.0))]
        )
        self.window.header_bar.split_analyze.set_mode("frame")
        with mock.patch.object(self.window.frame_analysis, "_start") as start_mock:
            self.window.header_bar.split_analyze.btn_main.click()
        start_mock.assert_called_once()

    def test_cancelled_chained_analysis_clears_pending_followup(self) -> None:
        """연속 분석이 폴더 미선택 등으로 시작되지 않으면 예약된 후속 단계가 비워지는지 검증"""
        with (
            mock.patch.object(self.window, "_current_root_dir", return_value=""),
            mock.patch("src.gui.main_window.browse_directory", return_value=""),
        ):
            self.window.frame_analysis.run_frame_mode()
        self.assertEqual(self.window.frame_analysis._pending_followup, "")

    def test_analysis_busy_covers_metadata_and_frame_analysis(self) -> None:
        """인코딩 시작을 막는 판정이 메타데이터 분석과 화질 분석 실행을 모두 포함하는지 검증"""
        with mock.patch.object(self.window._tasks, "is_running", return_value=False):
            self.assertFalse(self.window._is_analysis_busy())
        with mock.patch.object(self.window._tasks, "is_running", return_value=True):
            self.assertTrue(self.window._is_analysis_busy())
        with (
            mock.patch.object(self.window._tasks, "is_running", return_value=False),
            mock.patch.object(
                type(self.window.frame_analysis),
                "is_running",
                new_callable=mock.PropertyMock,
                return_value=True,
            ),
        ):
            self.assertTrue(self.window._is_analysis_busy())

    def test_running_encode_batch_keeps_the_start_item_enabled_during_frame_analysis(self) -> None:
        """인코딩 배치 중 화질 분석이 겹쳐도 시작 상태가 진행 안내를 유지하는지 검증"""
        from src.core.encode.encode_start_state import (
            ENCODE_RUNNING_TEXT,
            evaluate_start_state,
        )
        from src.core.encode.encoder_trial import EncoderTrialResult
        from src.core.encode.ffmpeg_capability import FfmpegCapability

        with (
            mock.patch.object(self.window._tasks, "is_running", return_value=False),
            mock.patch.object(
                type(self.window.frame_analysis),
                "is_running",
                new_callable=mock.PropertyMock,
                return_value=True,
            ),
            mock.patch.object(
                type(self.window._encode), "is_running", new_callable=mock.PropertyMock, return_value=True
            ),
        ):
            self.assertFalse(self.window._is_analysis_busy())
            state = evaluate_start_state(
                analysis_busy=self.window._is_analysis_busy(),
                encode_running=self.window._encode.is_running,
                has_targets=True,
                capability=FfmpegCapability("ffmpeg", "", (6, 1), usable=True),
                encoder=EncoderTrialResult("libx264", usable=True, reason=""),
            )
        self.assertTrue(state.enabled)
        self.assertEqual(state.reason, ENCODE_RUNNING_TEXT)

    def test_cancel_active_task_stops_the_worker_and_the_restore_flow(self) -> None:
        """작업 취소가 백그라운드 작업과 이전 결과 판정 중단을 함께 요청하는지 검증"""
        with (
            mock.patch.object(self.window._tasks, "cancel") as cancel_task,
            mock.patch.object(self.window._restore_flow, "cancel") as cancel_restore,
        ):
            self.window.cancel_active_task()
        cancel_task.assert_called_once()
        cancel_restore.assert_called_once()

    def test_cancel_active_task_stops_a_running_frame_analysis_first(self) -> None:
        """화질 분석이 도는 중이면 헤더 중단과 Esc가 화질 분석만 취소하는지 검증"""
        with (
            mock.patch.object(
                type(self.window.frame_analysis),
                "is_running",
                new_callable=mock.PropertyMock,
                return_value=True,
            ),
            mock.patch.object(self.window.frame_analysis, "cancel") as cancel_frame,
            mock.patch.object(self.window._tasks, "cancel") as cancel_task,
        ):
            self.window.cancel_active_task()
        cancel_frame.assert_called_once()
        cancel_task.assert_not_called()

    def test_escape_cancels_a_running_frame_analysis(self) -> None:
        """일반 분석 없이 화질 분석만 도는 중에도 Esc가 중단 요청을 보내는지 검증"""
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        with (
            mock.patch.object(
                type(self.window.frame_analysis),
                "is_running",
                new_callable=mock.PropertyMock,
                return_value=True,
            ),
            mock.patch.object(self.window, "cancel_active_task") as cancel,
        ):
            self.window.keyPressEvent(event)
        cancel.assert_called_once()
        self.assertTrue(event.isAccepted())

    def test_start_analyze_is_blocked_while_frame_analysis_runs(self) -> None:
        """화질 분석이 진행 중이면 일반 분석을 시작하지 않고 진행 사유를 알리는지 검증"""
        from src.gui.menus.menu_bar import FRAME_ANALYSIS_RUNNING_TEXT

        with (
            mock.patch.object(
                type(self.window.frame_analysis),
                "is_running",
                new_callable=mock.PropertyMock,
                return_value=True,
            ),
            mock.patch.object(self.window._tasks, "start_analyze") as start,
        ):
            self.window.start_analyze()
        start.assert_not_called()
        self.assertEqual(self.window.footer_bar.lbl_status.text(), FRAME_ANALYSIS_RUNNING_TEXT)

    def test_clear_analysis_view_cancels_a_running_frame_analysis(self) -> None:
        """분석 뷰를 비울 때 진행 중인 화질 분석을 자동으로 취소하는지 검증"""
        with (
            mock.patch.object(
                type(self.window.frame_analysis),
                "is_running",
                new_callable=mock.PropertyMock,
                return_value=True,
            ),
            mock.patch.object(self.window.frame_analysis, "cancel") as cancel,
        ):
            self.window._clear_analysis_view()
        cancel.assert_called_once()

    def test_csv_export_opens_the_export_tab_of_the_log_window(self) -> None:
        """CSV 내보내기 동작이 작업 기록 창의 내보내기 탭을 여는지 검증"""
        with mock.patch.object(self.window, "show_log_dialog") as show:
            self.window.export_csv()
        show.assert_called_once_with(LogTab.EXPORT)

    def test_close_waits_for_a_running_reevaluation_before_returning(self) -> None:
        """창을 닫을 때 진행 중인 재평가가 끝난 뒤 반환해, 분석 DB 연결이 닫힌 뒤 다시 열려 남지 않는지 검증"""
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def job() -> SummaryReport:
            entered.set()
            release.wait(_WAIT_SEC)
            finished.set()
            return make_report([])

        self.window._evaluator.submit(job, lambda _report: None)
        self.assertTrue(entered.wait(_WAIT_SEC))
        threading.Timer(_RELEASE_DELAY_SEC, release.set).start()
        self.window.close()
        self.assertTrue(finished.is_set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
