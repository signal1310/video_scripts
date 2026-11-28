"""인코딩 소유자의 시작 상태 계산, 확인 창 수락과 거절, 미리 보기 연결, 메뉴 콜백 연결, 창 종료 정리를 검증

- FFmpeg 확인, 인코더 시험, 확인 창, 배치 시작은 모두 가짜로 바꿔 실제 FFmpeg와 사용자 data 폴더에 닿지 않음
"""

import dataclasses
import json
import os
import tempfile
import unittest
from typing import Any
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QListWidget, QWidget

from src.core.config import RESULT_POLICY_REPLACE, ClassifierConfig
from src.core.encode.encode_batch_book import (
    CHIP_STATE_FINISHED,
    CHIP_STATE_PAUSED,
    CHIP_STATE_RUNNING,
)
from src.core.encode.encode_close import CloseChoice
from src.core.encode.encode_gate import GatePurpose
from src.core.encode.encode_start_state import (
    ANALYSIS_BUSY_TEXT,
    BATCH_FINISHED_TEXT,
    ENCODER_CHECKING_TEXT,
    NO_TARGET_TEXT,
)
from src.core.encode.encode_state import EncodeState
from src.core.encode.encoder_trial import NOT_LISTED_REASON, EncoderTrialCache
from src.core.encode.ffmpeg_capability import FfmpegCapability
from src.core.models import VideoEvaluation
from src.core.quality_target import BASELINE_CQ_DEFAULT
from src.core.smart_action import SmartAction
from src.core.workspace_paths import resolve_source_filepath
from src.gui.controllers import encode_coordinator as coordinator_module
from src.gui.controllers import encode_item_actions as item_actions_module
from src.gui.controllers.encode_controller import EncodeController
from src.gui.controllers.encode_coordinator import CompareOpener, EncodeCoordinator
from src.gui.controllers.encode_worker import BatchFinished
from src.gui.controllers.session_controller import SessionCoordinator
from src.gui.dialogs.encode_progress_window import EncodeProgressWindow
from src.gui.dialogs.encode_resume_dialogs import ResumeChoice
from src.gui.preview.preview_controller import PreviewController
from src.gui.widgets.header_bar import HeaderBar
from src.service.analysis_store import AnalysisStore
from src.service.encode.encode_events import EVENT_LONG_PAUSE, EVENT_PROGRESS, EVENT_STATE, EncodeEvent
from src.service.encode.encode_history import EncodeHistory, ItemRow, NewItem
from src.service.encode.encode_job import EncodeJob, EncodeSettings, job_to_json
from src.service.encode.encode_reasons import PART_LEFT
from tests._factories import make_eval, make_video
from tests._gui_helpers import make_menu_callbacks

_APP = QApplication.instance() or QApplication([])
_CODEC: str = "libx264"
_USABLE = FfmpegCapability("ffmpeg", "ffmpeg version 6.1", (6, 1), encoders=frozenset({_CODEC}), usable=True)
_UNUSABLE = FfmpegCapability("ffmpeg", "", None, usable=False, reason="찾을 수 없음")


def _sync_cache(returncode: int = 0) -> EncoderTrialCache:
    """시험을 바로 끝내는 캐시. 백그라운드 스레드를 만들지 않음"""
    return EncoderTrialCache(runner=lambda _command, _timeout: returncode, spawn=lambda task: task())


def _report(root: str, evaluations: list[VideoEvaluation]) -> Any:
    report = mock.Mock()
    report.root_dir = root
    report.evaluations = evaluations
    return report


def _recording_opener(calls: list[tuple[str, object]]) -> CompareOpener:
    """비교 열기 요청을 기록하는 CompareOpener 대역"""

    def open_compare(source: str, *, owner: QWidget | None = None) -> None:
        calls.append((source, owner))

    return open_compare



def _plan_job(path: str) -> EncodeJob:
    """이력 행의 계획 입력으로 쓸 작업 하나"""
    return EncodeJob(
        filename=os.path.basename(path),
        root_dir=os.path.dirname(path),
        source_path=path,
        source_size=2000,
        source_mtime_ns=1,
        source_duration_sec=60.0,
        source_ext="mp4",
        fps=30.0,
        quality=22.0,
        purpose=GatePurpose.SAVING,
        output_size=None,
        scale_filter=None,
        maxrate_kbps=None,
    )


class EncodeCoordinatorTests(unittest.TestCase):
    """소유자 하나의 시작 흐름과 정리 동작을 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.config = ClassifierConfig()
        self.config.encode.encode_codec = _CODEC
        self.session = mock.Mock()
        self.session.state.to_classifier_config.return_value = self.config
        self.busy = False
        self.preview = mock.Mock(spec=PreviewController)
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.capability = _USABLE
        self.trial = _sync_cache()
        self.probe_calls: list[str] = []
        self._patch(
            "src.gui.controllers.encode_coordinator.open_default_store",
            lambda: AnalysisStore(os.path.join(self.root, "video_cache.sqlite3")),
        )
        self._patch("src.gui.controllers.encode_coordinator.SHARED_ENCODER_TRIAL_CACHE", self.trial)
        self._patch("src.gui.controllers.encode_coordinator.probe_ffmpeg_capability", self._probe)
        self.ready_to_close: int = 0
        self.coordinator = EncodeCoordinator(
            self.parent, self.session, lambda: self.busy, self.preview, self._on_ready_to_close
        )
        self.report: Any = _report(self.root, [make_eval(make_video(filename="a.mp4"))])
        self.coordinator.attach_menu(
            make_menu_callbacks(),
            get_report=lambda: self.report,
            open_settings=self._open_settings,
            on_changed=self._on_changed,
        )
        self.opened_settings: int = 0
        self.changed_count: int = 0
        self.addCleanup(self.coordinator.close)

    def _patch(self, target: str, replacement: Any) -> None:
        patcher = mock.patch(target, replacement)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _probe(self, path: str) -> FfmpegCapability:
        self.probe_calls.append(path)
        return self.capability

    def _open_settings(self) -> None:
        self.opened_settings += 1

    def _on_changed(self) -> None:
        self.changed_count += 1

    def _on_ready_to_close(self) -> None:
        self.ready_to_close += 1

    def _reload_session_with_sandbox_db(self) -> mock.Mock:
        """reload_manual_targets가 샌드박스 DB를 실제로 다시 읽도록 세션 조율자를 세우고 반환"""
        session = self.session
        store = AnalysisStore(os.path.join(self.root, "manual_targets.sqlite3"))
        self.addCleanup(store.close)
        self._sandbox_store = store

        def reload() -> None:
            loaded = store.read_manual_targets({})
            session.state.ui.manual_target_tiers = loaded
            session.classifier.manual_target_store = loaded

        session.reload_manual_targets.side_effect = reload
        return session

    def test_done_event_reloads_manual_targets_from_the_db(self) -> None:
        """완료 이벤트가 실행기가 지운 인코딩 지정을 DB에서 다시 읽어 메모리에서도 지우는지 검증"""
        session = self._reload_session_with_sandbox_db()
        session.state.ui.manual_target_tiers = {"root": {"a.mp4": "encode"}}
        session.classifier.manual_target_store = {"root": {"a.mp4": "encode"}}
        session.classifier.set_manual_target_store = mock.Mock()
        self.coordinator._on_event(EncodeEvent(EVENT_STATE, 1, "C:/work/a.mp4", state=EncodeState.DONE))
        self.assertEqual(session.state.ui.manual_target_tiers, {})
        self.assertEqual(session.classifier.manual_target_store, {})

    def test_manual_target_edit_after_done_does_not_restore_cleared_value(self) -> None:
        """완료 뒤 다른 영상의 직접 지정을 저장해도 지워진 인코딩 지정이 DB에 되살아나지 않는지 검증"""
        self._reload_session_with_sandbox_db()
        stale = {"root": {"a.mp4": "encode"}}
        session = self.session
        session.state.ui.manual_target_tiers = stale
        session.classifier.manual_target_store = stale
        self.coordinator._on_event(EncodeEvent(EVENT_STATE, 1, "C:/work/a.mp4", state=EncodeState.DONE))
        store = self._sandbox_store
        # - 완료 뒤 다른 영상에 직접 지정을 더하고 저장 기준은 완료 전 메모리 맵
        saver = SessionCoordinator.__new__(SessionCoordinator)
        saver.state = mock.Mock()
        saver.state.ui.manual_target_tiers = {**session.state.ui.manual_target_tiers, "root": {"b.mp4": "720p"}}
        saver._saved_manual_targets = stale
        with mock.patch(
            "src.gui.controllers.session_controller.save_manual_target_store",
            lambda value: store.write_manual_targets(value),
        ):
            saver._save_manual_targets_if_changed()
        self.assertEqual(store.read_manual_targets({}), {"root": {"b.mp4": "720p"}})


    def test_not_running_before_any_start(self) -> None:
        """시작 전에는 실행 중이 아닌지 검증"""
        self.assertFalse(self.coordinator.is_running)

    def test_ready_state_enables_the_start_item(self) -> None:
        """대상, FFmpeg, 인코더가 모두 갖춰지면 시작 항목이 활성인지 검증"""
        state = self.coordinator.start_state()
        self.assertTrue(state.enabled)
        self.assertEqual(state.reason, "")

    def test_no_report_or_no_target_gives_the_no_target_reason(self) -> None:
        """판정 결과가 없거나 대상이 없으면 대상 없음 사유를 주는지 검증"""
        self.report = None
        self.assertEqual(self.coordinator.start_state().reason, NO_TARGET_TEXT)
        self.report = _report(self.root, [make_eval(make_video(filename="b.mp4"), smart_action=SmartAction.PASS_OPTIMAL)])
        self.assertEqual(self.coordinator.start_state().reason, NO_TARGET_TEXT)

    def test_busy_analysis_blocks_the_start_item(self) -> None:
        """분석 중이면 분석 사유로 막히는지 검증"""
        self.busy = True
        self.assertEqual(self.coordinator.start_state().reason, ANALYSIS_BUSY_TEXT)

    def test_finished_batch_without_targets_keeps_the_start_item_enabled(self) -> None:
        """배치를 마친 뒤 남은 대상이 없어도 결과를 확인할 수 있게 시작 항목이 활성인지 검증"""
        self.report = _report(
            self.root, [make_eval(make_video(filename="b.mp4"), smart_action=SmartAction.PASS_OPTIMAL)]
        )
        self.coordinator.book.start([resolve_source_filepath(self.root, "a.mp4")])
        state = self.coordinator.start_state()
        self.assertTrue(state.enabled)
        self.assertEqual(state.reason, BATCH_FINISHED_TEXT)
        self.assertFalse(state.pending)

    def test_missing_encoder_in_listing_blocks_with_the_listing_reason(self) -> None:
        """목표 인코딩 방식이 FFmpeg 인코더 목록에 없으면 목록 사유로 막히는지 검증"""
        self.config.encode.encode_codec = "av1_nvenc"
        state = self.coordinator.start_state()
        self.assertEqual(state.reason, NOT_LISTED_REASON)

    def test_pending_trial_reports_checking_and_asks_for_recheck(self) -> None:
        """인코더 시험이 끝나지 않으면 확인하는 중 문구이고 다시 확인이 필요하다고 알리는지 검증"""
        pending = EncoderTrialCache(runner=lambda _command, _timeout: 0, spawn=lambda _task: None)
        with mock.patch.object(coordinator_module, "SHARED_ENCODER_TRIAL_CACHE", pending):
            state = self.coordinator.start_state()
        self.assertEqual(state.reason, ENCODER_CHECKING_TEXT)
        self.assertTrue(state.pending)

    def test_unusable_ffmpeg_blocks_the_start_item(self) -> None:
        """FFmpeg를 쓸 수 없으면 FFmpeg 사유로 막히는지 검증"""
        self.capability = _UNUSABLE
        self.assertIn("찾을 수 없음", self.coordinator.start_state().reason)

    def test_ffmpeg_is_probed_once_per_path(self) -> None:
        """같은 경로의 FFmpeg는 상태를 여러 번 계산해도 한 번만 확인하고, 경로가 바뀌면 다시 확인하는지 검증"""
        self.coordinator.start_state()
        self.coordinator.start_state()
        self.assertEqual(self.probe_calls, [""])
        self.config.execution.ffmpeg_path = "tools/ffmpeg.exe"
        self.coordinator.start_state()
        self.assertEqual(self.probe_calls, ["", "tools/ffmpeg.exe"])

    def test_menu_start_does_nothing_while_analysis_is_busy(self) -> None:
        """분석 중이면 인코딩 창을 열지 않는지 검증"""
        self.busy = True
        self.coordinator.start_from_menu()
        self.assertIsNone(self.coordinator._progress_window)

    def test_menu_start_opens_the_window_with_the_summary_plan(self) -> None:
        """시작 항목이 확인 창 없이 인코딩 창을 열고 요약 탭에 브리핑을 채우는지 검증"""
        self.coordinator.start_from_menu()
        window = self.coordinator._progress_window
        assert window is not None
        self.assertTrue(window.isVisible())
        self.assertEqual(window._summary.target_text, "1개")
        self.assertEqual(window._pending.count(), 1)
        self.assertEqual(self.changed_count, 1)

    def test_start_button_starts_the_batch_and_reports_the_change(self) -> None:
        """창의 시작 버튼이 판정 결과로 배치를 시작하고 상태 변경과 대기열을 알리는지 검증"""
        self.coordinator.start_from_menu()
        window = self.coordinator._progress_window
        assert window is not None
        with mock.patch.object(EncodeController, "start", return_value=1) as start:
            window._start_new_batch()
        start.assert_called_once()
        config, evaluations, root_spec, root_dir = start.call_args.args
        self.assertIs(config, self.config)
        self.assertEqual(root_spec, self.root)
        self.assertEqual(root_dir, self.root)
        self.assertEqual(len(evaluations), 1)
        self.assertTrue(window.isVisible())
        self.assertEqual(self.changed_count, 2)

    def test_start_and_finish_note_the_encode_batch_in_breadcrumbs(self) -> None:
        """인코딩 배치 시작과 종료가 인코딩 분류로 기록되는지 검증"""
        self.coordinator.start_from_menu()
        window = self.coordinator._progress_window
        assert window is not None
        with (
            mock.patch.object(EncodeController, "start", return_value=1),
            mock.patch.object(coordinator_module, "note_event") as noted,
        ):
            window._start_new_batch()
            self.coordinator._on_batch_finished(None)
        self.assertEqual(
            [call.args for call in noted.call_args_list],
            [("인코딩", "인코딩 배치 시작"), ("인코딩", "인코딩 배치 완료")],
        )

    def test_start_button_excludes_unchecked_targets(self) -> None:
        """시작 전 대기열에서 체크를 해제한 영상은 배치 대상에서 빠지는지 검증"""
        path = resolve_source_filepath(self.root, "a.mp4")
        with mock.patch.object(EncodeController, "start", return_value=1) as start:
            self.coordinator._start_from_window(frozenset({path}))
        start.assert_not_called()

    def test_start_button_explains_why_the_batch_cannot_start(self) -> None:
        """창의 시작 버튼이 분석 중 등으로 막히면 사유를 안내하는지 검증"""
        self.busy = True
        self.coordinator.start_from_menu()
        self.assertIsNone(self.coordinator._progress_window)
        with mock.patch.object(
            coordinator_module, "notify_text"
        ) as notify:
            self.coordinator._start_from_window(frozenset())
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[1], ANALYSIS_BUSY_TEXT)

    def test_finished_batch_shows_a_new_brief_when_targets_remain(self) -> None:
        """배치를 마친 뒤에도 새 대상이 있으면 기존 창에 새 브리핑을 채워 요약 탭을 엶"""
        self.coordinator.book.start([resolve_source_filepath(self.root, "a.mp4")])
        self.coordinator.start_from_menu()
        window = self.coordinator._progress_window
        assert window is not None
        self.assertTrue(window.isVisible())
        self.assertEqual(window._summary.target_text, "1개")
        self.assertEqual(window._pending.count(), 1)
        self.assertEqual(self.changed_count, 1)

    def test_finished_batch_without_new_targets_reopens_the_window(self) -> None:
        """새 대상이 없고 완료된 배치만 있으면 기존 창을 전면에 보여 주고 시작 버튼을 막음"""
        self.report = _report(
            self.root, [make_eval(make_video(filename="b.mp4"), smart_action=SmartAction.PASS_OPTIMAL)]
        )
        self.coordinator.book.start([resolve_source_filepath(self.root, "a.mp4")])
        self.coordinator.start_from_menu()
        window = self.coordinator._progress_window
        assert window is not None
        self.assertTrue(window.isVisible())
        self.assertFalse(window._start_btn.isEnabled())
        self.assertEqual(window._start_btn.toolTip(), "인코딩할 영상이 선택되지 않았습니다.")
        self.assertEqual(self.changed_count, 0)

    def test_start_from_window_confirms_replace_with_the_current_settings(self) -> None:
        """창을 연 뒤 설정이 원본 교체로 바뀌어도 시작 시점 설정과 포함 대상 수로 다시 확인"""
        self.coordinator.start_from_menu()
        window = self.coordinator._progress_window
        assert window is not None
        self.config.execution.result_policy = RESULT_POLICY_REPLACE
        with (
            mock.patch.object(EncodeController, "start", return_value=1) as start,
            mock.patch.object(EncodeProgressWindow, "confirm_replace", return_value=False) as confirm,
        ):
            window._start_new_batch()
        confirm.assert_called_once_with(1)
        start.assert_not_called()
        with (
            mock.patch.object(EncodeController, "start", return_value=1) as start,
            mock.patch.object(EncodeProgressWindow, "confirm_replace", return_value=True),
        ):
            window._start_new_batch()
        start.assert_called_once()

    def test_start_from_window_confirms_replace_after_a_resumed_batch(self) -> None:
        """이어서 하기로 브리핑이 없어진 창에서도 시작 시점 설정이 원본 교체면 확인을 거침"""
        window = self.coordinator._ensure_progress_window()
        window.set_resumed()
        self.config.execution.result_policy = RESULT_POLICY_REPLACE
        with (
            mock.patch.object(EncodeController, "start", return_value=1) as start,
            mock.patch.object(EncodeProgressWindow, "confirm_replace", return_value=False) as confirm,
        ):
            window._start_new_batch()
        confirm.assert_called_once_with(1)
        start.assert_not_called()

    def test_start_button_leaves_the_excluded_targets_out_of_the_plan(self) -> None:
        """시작 전 대기열에서 체크를 푼 영상은 배치 계획과 요약 브리핑에서 함께 빠짐"""
        self.report = _report(
            self.root, [make_eval(make_video(filename="a.mp4")), make_eval(make_video(filename="b.mp4"))]
        )
        self.coordinator.start_from_menu()
        window = self.coordinator._progress_window
        assert window is not None
        pending_list = window._pending.findChild(QListWidget, "EncodePendingList")
        assert pending_list is not None
        item = pending_list.item(1)
        assert item is not None
        item.setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(window._summary.target_text, "1/2개")
        with mock.patch.object(EncodeController, "start", return_value=1) as start:
            window._start_btn.click()
        start.assert_called_once()
        evaluations = start.call_args.args[1]
        self.assertEqual([ev.video.filename for ev in evaluations], ["a.mp4"])
        self.assertEqual(window._summary.target_text, "1개")
        summary_plan = window._summary._plan
        assert summary_plan is not None
        self.assertEqual([ev.video.filename for ev in summary_plan.evaluations], ["a.mp4"])

    def test_window_menu_item_is_connected_and_opens_the_window(self) -> None:
        """메뉴 창 열기 콜백이 배치 시작 여부와 무관하게 진행 창을 여는지 검증"""
        callbacks = make_menu_callbacks()
        self.coordinator.attach_menu(
            callbacks, get_report=lambda: self.report, open_settings=self._open_settings, on_changed=self._on_changed
        )
        assert callbacks.on_start_encode is not None
        self.assertFalse(self.coordinator.has_encode_batch())
        self._start_with_views()
        self.assertTrue(self.coordinator.has_encode_batch())
        window = self.coordinator._progress_window
        assert window is not None
        window.hide()
        callbacks.on_start_encode()
        self.assertTrue(window.isVisible())

    def test_menu_callbacks_are_connected_to_the_coordinator(self) -> None:
        """메뉴 연결이 시작, 설정 콜백과 상태 조회 함수를 소유자에 묶는지 검증"""
        callbacks = make_menu_callbacks()
        self.coordinator.attach_menu(
            callbacks, get_report=lambda: self.report, open_settings=self._open_settings, on_changed=self._on_changed
        )
        assert callbacks.on_start_encode is not None
        assert callbacks.on_open_encode_settings is not None
        assert callbacks.get_encode_state is not None
        callbacks.on_open_encode_settings()
        self.assertEqual(self.opened_settings, 1)
        self.assertTrue(callbacks.get_encode_state().enabled)

    def test_batch_finish_reports_the_state_change_to_the_menu(self) -> None:
        """배치가 끝나 종료 항목이 오면 시작 항목 상태 갱신을 알리는지 검증"""
        self.coordinator._on_batch_finished(None)
        self.assertEqual(self.changed_count, 1)

    def test_close_shuts_down_the_controller_and_releases_stores(self) -> None:
        """창 종료 때 실행 컨트롤러를 정리하고 이력과 분석 DB 연결을 닫는지 검증"""
        with mock.patch.object(EncodeController, "shutdown") as shutdown:
            self.coordinator.close()
        shutdown.assert_called_once()

    def test_close_hides_the_independent_progress_window_so_the_app_can_exit(self) -> None:
        """메인 창의 자식이 아닌 진행 창이 떠 있어도 종료 때 숨겨서 앱이 끝날 수 있게 함"""
        self.coordinator.open_progress_window()
        window = self.coordinator._ensure_progress_window()
        self.assertTrue(window.isVisible())
        self.coordinator.close()
        self.assertFalse(window.isVisible())

    def test_idle_close_hides_the_progress_window_without_asking(self) -> None:
        """배치가 없을 때 메인 창 닫기 요청은 확인 없이 바로 닫고, 떠 있던 인코딩 창도 함께 숨김"""
        self.coordinator.open_progress_window()
        window = self.coordinator._ensure_progress_window()
        event = self._close_event()
        self.assertTrue(window.isVisible())
        with mock.patch.object(coordinator_module, "ask_close") as ask:
            self.assertTrue(self.coordinator.can_close(event))
            ask.assert_not_called()
        self.coordinator.close()
        self.assertFalse(window.isVisible())
        self.assertTrue(window.isHidden())

    def test_chip_status_reports_running_and_paused_from_book_and_controller(self) -> None:
        """칩 상태가 실행 여부와 일시중지에 따라 running, paused, finished로 바뀌는지 검증"""
        self._start_with_views()
        path = resolve_source_filepath(self.root, "a.mp4")
        with mock.patch.object(EncodeController, "is_running", new_callable=mock.PropertyMock) as running:
            running.return_value = True
            self.assertEqual(self.coordinator.chip_status().state, CHIP_STATE_RUNNING)
            self.coordinator._on_pause_changed(True)
            self.assertEqual(self.coordinator.chip_status().state, CHIP_STATE_PAUSED)
        self.assertEqual(self.coordinator.chip_status().state, CHIP_STATE_FINISHED)
        self.assertEqual(self.coordinator.book.state_of(path), EncodeState.PENDING)

    def test_window_title_gets_the_percent_prefix_while_running_and_restores(self) -> None:
        """진행 중에는 창 제목 앞에 퍼센트가 붙고, 완료 뒤에는 기본 제목으로 돌아오는지 검증"""
        self.parent.setWindowTitle("기본 제목")
        self._start_with_views()
        path = resolve_source_filepath(self.root, "a.mp4")
        with mock.patch.object(EncodeController, "is_running", new_callable=mock.PropertyMock) as running:
            running.return_value = True
            self.coordinator._on_event(EncodeEvent(EVENT_STATE, 1, path, state=EncodeState.DONE))
            self.assertEqual(self.parent.windowTitle(), "[100%] 기본 제목")
        self.coordinator._on_event(EncodeEvent(EVENT_STATE, 1, path, state=EncodeState.FAILED))
        self.assertEqual(self.parent.windowTitle(), "기본 제목")

    def test_header_button_status_follows_the_batch(self) -> None:
        """헤더 인코딩 버튼의 상태가 배치 상태와 함께 켜지고 꺼지는지 검증"""
        header = mock.Mock(spec=HeaderBar)
        self.coordinator.attach_menu(
            make_menu_callbacks(),
            get_report=lambda: self.report,
            open_settings=self._open_settings,
            on_changed=self._on_changed,
            header_bar=header,
        )
        self._start_with_views()
        with mock.patch.object(EncodeController, "is_running", new_callable=mock.PropertyMock) as running:
            running.return_value = True
            self.coordinator._notify_book_changed()
            status = header.set_encode_status.call_args.args[0]
            self.assertEqual(status.state, CHIP_STATE_RUNNING)
        self.coordinator._notify_book_changed()
        status = header.set_encode_status.call_args.args[0]
        self.assertNotEqual(status.state, CHIP_STATE_RUNNING)

    def test_start_state_feeds_the_target_count_to_the_header_badge(self) -> None:
        """시작 항목 상태를 계산할 때 그 계산에 쓴 대상 수를 헤더 배지로 넘기는지 검증"""
        header = mock.Mock(spec=HeaderBar)
        self.coordinator.attach_menu(
            make_menu_callbacks(),
            get_report=lambda: self.report,
            open_settings=self._open_settings,
            on_changed=self._on_changed,
            header_bar=header,
        )
        header.set_encode_target_count.reset_mock()
        self.coordinator.start_state()
        expected = len(self.coordinator._targets(self.report))
        self.assertEqual(expected, 1)
        header.set_encode_target_count.assert_called_with(expected)

    def _start_with_views(self) -> tuple[mock.Mock, mock.Mock, str]:
        """배치를 시작하고 표와 하단 칩 함수를 연결한 뒤 대상 경로를 돌려줌"""
        book_sink = mock.Mock()
        status_sink = mock.Mock()
        self.coordinator.attach_view(book_sink, status_sink)
        path = resolve_source_filepath(self.root, "a.mp4")
        self.coordinator.start_from_menu()
        window = self.coordinator._progress_window
        assert window is not None
        with mock.patch.object(EncodeController, "start", return_value=1):
            window._start_new_batch()
        return book_sink, status_sink, path

    def test_accepted_start_marks_targets_pending_and_shows_the_chip(self) -> None:
        """시작하면 대상 항목을 대기로 두고 표와 하단 칩에 상태를 넘기는지 검증"""
        book_sink, status_sink, path = self._start_with_views()
        self.assertEqual(self.coordinator.book.state_of(path), EncodeState.PENDING)
        book_sink.assert_called_with(self.coordinator.book)
        status = status_sink.call_args.args[0]
        self.assertEqual(status.state, CHIP_STATE_FINISHED)
        self.assertEqual((status.done, status.total, status.percent), (0, 1, 0))

    def test_state_event_updates_the_book_and_the_chip(self) -> None:
        """상태 이벤트가 항목 상태와 처리 수, 하단 칩 상태를 갱신하고 진행 이벤트는 상태를 바꾸지 않는지 검증"""
        _book_sink, status_sink, path = self._start_with_views()
        self.coordinator._on_event(EncodeEvent(EVENT_PROGRESS, 1, path))
        self.assertEqual(self.coordinator.book.state_of(path), EncodeState.PENDING)
        self.coordinator._on_event(EncodeEvent(EVENT_STATE, 1, path, state=EncodeState.DONE))
        self.assertEqual(self.coordinator.book.state_of(path), EncodeState.DONE)
        status = status_sink.call_args.args[0]
        self.assertEqual((status.done, status.total, status.percent), (1, 1, 100))

    def test_done_event_hands_the_output_path_and_size_to_the_progress_window(self) -> None:
        """완료 이벤트가 이력 행을 한 번 읽어 결과 경로와 원본, 결과 크기를 진행 창에 넘기는지 검증"""
        _book_sink, _summary_sink, path = self._start_with_views()
        output = os.path.join(self.root, "out", "a.mp4")
        row = ItemRow(
            batch_id=1,
            source_path=path,
            source_size=2000,
            source_mtime_ns=1,
            plan_json=job_to_json(_plan_job(path)),
            state=EncodeState.DONE,
            reason="",
            part_path=None,
            output_path=output,
            archive_path=None,
            started_at=None,
            ended_at=None,
            output_size=800,
            decode_retry=False,
            stderr_tail="",
        )
        with (
            mock.patch.object(EncodeHistory, "get_item", return_value=row),
            mock.patch.object(EncodeProgressWindow, "set_result") as set_result,
        ):
            self.coordinator._on_event(EncodeEvent(EVENT_STATE, 1, path, state=EncodeState.DONE))
        result = set_result.call_args.args[0]
        self.assertEqual((result.source_bytes, result.output_bytes, result.output_path), (2000, 800, output))
        self.assertIsNone(result.expected_delta_mb)

    def test_open_progress_window_shows_it_and_item_pause_reaches_the_book(self) -> None:
        """진행 창을 열면 보이고, 항목 일시중지 신호가 상태 저장소의 진행 중 표기에 반영되는지 검증"""
        _book_sink, _summary_sink, path = self._start_with_views()
        self.coordinator.open_progress_window()
        window = self.coordinator._progress_window
        self.assertIsNotNone(window)
        self.assertTrue(window is not None and window.isVisible())
        self.coordinator._on_event(EncodeEvent(EVENT_STATE, 1, path, state=EncodeState.RUNNING))
        self.coordinator._controller.item_pause_changed.emit(path, True)
        self.assertTrue(self.coordinator.book.is_item_paused(path))
        self.assertEqual(self.coordinator.book.label_of(path), "진행 중 (일시중지)")

    def test_pause_change_is_shown_on_running_items_and_cleared_when_the_batch_ends(self) -> None:
        """일시중지 전환이 진행 중 항목에 표시되고, 배치가 끝나면 일시중지가 풀리는지 검증"""
        book_sink, _summary_sink, path = self._start_with_views()
        self.coordinator._on_event(EncodeEvent(EVENT_STATE, 1, path, state=EncodeState.RUNNING))
        self.coordinator._on_pause_changed(True)
        self.assertEqual(self.coordinator.book.label_of(path), "진행 중 (일시중지)")
        book_sink.assert_called_with(self.coordinator.book)
        self.coordinator._on_batch_finished(None)
        self.assertEqual(self.coordinator.book.label_of(path), "진행 중")
        self.assertFalse(self.coordinator.book.paused)

    def test_state_event_is_logged_with_file_name_label_and_reason(self) -> None:
        """상태 이벤트가 파일 이름, 상태 표기, 결과 사유와 함께 INFO 로그로 남는지 검증"""
        _book_sink, _summary_sink, path = self._start_with_views()
        with self.assertLogs("src.gui.controllers.encode_coordinator", level="INFO") as logs:
            self.coordinator._on_event(
                EncodeEvent(EVENT_STATE, 1, path, state=EncodeState.DISCARDED, reason="결과가 원본보다 큼")
            )
        self.assertEqual(
            logs.output,
            ["INFO:src.gui.controllers.encode_coordinator:인코딩 상태: a.mp4, 결과 폐기, 사유: 결과가 원본보다 큼"],
        )

    def test_batch_start_and_end_are_logged_with_target_and_processed_counts(self) -> None:
        """배치 시작은 대상 수, 종료는 처리 수를 INFO 로그로 남기는지 검증"""
        with self.assertLogs("src.gui.controllers.encode_coordinator", level="INFO") as logs:
            _book_sink, _summary_sink, path = self._start_with_views()
            self.coordinator._on_event(EncodeEvent(EVENT_STATE, 1, path, state=EncodeState.DONE))
            self.coordinator._on_batch_finished(None)
        self.assertIn("INFO:src.gui.controllers.encode_coordinator:인코딩 배치 시작: 대상 1개", logs.output)
        self.assertEqual(logs.output[-1], "INFO:src.gui.controllers.encode_coordinator:인코딩 배치 종료: 1/1개 처리")

    def _close_event(self) -> QCloseEvent:
        return QCloseEvent()

    def _window(self) -> EncodeProgressWindow:
        window = self.coordinator._progress_window
        if window is None:
            self.fail("진행 창이 아직 만들어지지 않았습니다")
        return window

    def test_closing_when_nothing_runs_closes_at_once(self) -> None:
        """인코딩 중이 아니면 확인 없이 바로 닫을 수 있는지 검증"""
        event = self._close_event()
        self.assertTrue(self.coordinator.can_close(event))
        self.assertEqual(self.ready_to_close, 0)

    def test_immediate_choice_cancels_the_batch_and_closes_when_it_ends(self) -> None:
        """즉시 중지 후 종료는 전체 중지를 부르고, 배치가 끝난 뒤에야 닫기를 다시 요청하는지 검증"""
        event = self._close_event()
        with (
            mock.patch.object(EncodeController, "is_running", new_callable=mock.PropertyMock, return_value=True),
            mock.patch.object(coordinator_module, "ask_close", return_value=CloseChoice.IMMEDIATE),
            mock.patch.object(EncodeController, "cancel_all") as cancel_all,
        ):
            self.assertFalse(self.coordinator.can_close(event))
            self.assertFalse(event.isAccepted())
            cancel_all.assert_called_once()
            self.assertEqual(self.ready_to_close, 0)
            with mock.patch.object(EncodeController, "is_running", new_callable=mock.PropertyMock, return_value=False):
                self.coordinator._on_batch_finished(None)
        self.assertEqual(self.ready_to_close, 1)

    def test_keep_choice_ignores_the_close_and_asks_again_next_time(self) -> None:
        """계속을 고르면 아무 것도 바꾸지 않고 닫기를 무시하며, 다음 닫기에서 다시 묻는지 검증"""
        with (
            mock.patch.object(EncodeController, "is_running", new_callable=mock.PropertyMock, return_value=True),
            mock.patch.object(coordinator_module, "ask_close", return_value=CloseChoice.KEEP) as ask,
            mock.patch.object(EncodeController, "cancel_all") as cancel_all,
            mock.patch.object(EncodeController, "request_stop_after_current") as stop_after,
        ):
            self.assertFalse(self.coordinator.can_close(self._close_event()))
            self.assertFalse(self.coordinator.can_close(self._close_event()))
            self.assertEqual(ask.call_count, 2)
            cancel_all.assert_not_called()
            stop_after.assert_not_called()

    def test_after_current_choice_reserves_the_stop_and_waits_without_asking_again(self) -> None:
        """현재 영상 후 중지하고 종료는 예약하고 진행 창에 배너를 띄우며, 예약 뒤에는 다시 묻지 않는지 검증"""
        with (
            mock.patch.object(EncodeController, "is_running", new_callable=mock.PropertyMock, return_value=True),
            mock.patch.object(coordinator_module, "ask_close", return_value=CloseChoice.AFTER_CURRENT) as ask,
            mock.patch.object(EncodeController, "request_stop_after_current") as stop_after,
        ):
            self.assertFalse(self.coordinator.can_close(self._close_event()))
            stop_after.assert_called_once()
            self.assertFalse(self._window().isHidden())
            self.assertFalse(self._window()._banner.isHidden())
            self.assertFalse(self.coordinator.can_close(self._close_event()))
            ask.assert_called_once()

    def test_cancelling_the_reservation_restores_the_stop_after_state_and_hides_the_banner(self) -> None:
        """종료 예약 취소는 현재 영상 후 중지를 풀고 배너를 숨기며, 배치는 닫히지 않고 계속되는지 검증"""
        with (
            mock.patch.object(EncodeController, "is_running", new_callable=mock.PropertyMock, return_value=True),
            mock.patch.object(coordinator_module, "ask_close", return_value=CloseChoice.AFTER_CURRENT),
            mock.patch.object(EncodeController, "request_stop_after_current"),
            mock.patch.object(EncodeController, "cancel_stop_after_current") as cancel_stop,
        ):
            self.coordinator.can_close(self._close_event())
            self._window().close_cancel_requested.emit()
        cancel_stop.assert_called_once()
        self.assertTrue(self._window()._banner.isHidden())
        self.assertEqual(self.ready_to_close, 0)


    def _settings_with(self, after_complete: str) -> EncodeSettings:
        return dataclasses.replace(EncodeSettings.from_config(self.config), after_complete=after_complete)

    def test_normal_finish_hands_the_batch_action_to_the_completion_flow(self) -> None:
        """정상 종료한 배치의 완료 후 동작을 카운트다운 흐름에 넘김"""
        self._start_with_views()
        completion = mock.Mock()
        self.coordinator._completion = completion
        self.coordinator._batch_settings = self._settings_with("sleep")
        with mock.patch.object(EncodeController, "interrupted", new_callable=mock.PropertyMock, return_value=False):
            self.coordinator._on_batch_finished(BatchFinished(batch_id=1, error=""))
        completion.start.assert_called_once_with("sleep")

    def test_interrupted_or_failed_batches_start_no_completion_action(self) -> None:
        """즉시 중지나 현재 영상 후 중지, 실행 오류로 끝난 배치는 완료 후 동작을 실행하지 않음"""
        self._start_with_views()
        completion = mock.Mock()
        self.coordinator._completion = completion
        self.coordinator._batch_settings = self._settings_with("shutdown")
        with mock.patch.object(EncodeController, "interrupted", new_callable=mock.PropertyMock, return_value=True):
            self.coordinator._on_batch_finished(BatchFinished(batch_id=1, error=""))
        with mock.patch.object(EncodeController, "interrupted", new_callable=mock.PropertyMock, return_value=False):
            self.coordinator._on_batch_finished(BatchFinished(batch_id=1, error="boom"))
        completion.start.assert_not_called()

    def test_long_pause_event_writes_one_work_log_line_with_the_file_name(self) -> None:
        """장시간 일시중지 이벤트는 작업 기록에 파일 이름과 10분 안내를 한 줄 남김"""
        self._start_with_views()
        path = resolve_source_filepath(self.root, "a.mp4")
        with self.assertLogs(coordinator_module.logger, level="INFO") as logs:
            self.coordinator._on_event(EncodeEvent(EVENT_LONG_PAUSE, 1, path))
        self.assertEqual(len(logs.output), 1)
        self.assertIn("인코딩 장시간 일시중지: a.mp4, 10분을 넘게 멈춰 있습니다.", logs.output[0])

    def test_retry_without_a_batch_does_nothing_and_forfeit_needs_confirmation(self) -> None:
        """배치가 없으면 재시도와 포기 모두 아무 일도 하지 않고, 포기는 확인 창을 거쳐야 함"""
        path = resolve_source_filepath(self.root, "a.mp4")
        with (
            mock.patch.object(EncodeController, "retry_replace") as retry,
            mock.patch.object(item_actions_module, "ask_forfeit") as ask,
        ):
            self.coordinator._items.retry_replace(path)
            self.coordinator._items.forfeit_replace(path)
            retry.assert_not_called()
            ask.assert_not_called()
        self._start_with_views()
        with (
            mock.patch.object(item_actions_module, "ask_forfeit", return_value=False) as ask,
            mock.patch.object(EncodeController, "forfeit_replace") as forfeit,
        ):
            self.coordinator._items.forfeit_replace(path)
            ask.assert_called_once()
            forfeit.assert_not_called()

    def test_busy_retry_and_forfeit_tell_the_user_to_wait(self) -> None:
        """실행 중이라 재시도나 포기를 시작하지 못하면 끝난 뒤에 하라고 안내"""
        self._start_with_views()
        path = resolve_source_filepath(self.root, "a.mp4")
        with (
            mock.patch.object(EncodeController, "retry_replace", return_value=False),
            mock.patch.object(EncodeController, "forfeit_replace", return_value=False),
            mock.patch.object(item_actions_module, "ask_forfeit", return_value=True),
            mock.patch.object(item_actions_module.QMessageBox, "information") as notice,
        ):
            self.coordinator._items.retry_replace(path)
            self.coordinator._items.forfeit_replace(path)
        self.assertEqual(notice.call_count, 2)
        self.assertIn(item_actions_module.RETRY_BUSY_TEXT, notice.call_args_list[0].args)

    def test_discard_replace_needs_confirmation_and_refreshes_after_it(self) -> None:
        """폐기는 확인 창을 거쳐야 하고, 확인하면 결과 파일을 지운 뒤 행을 갱신함(AGENTS 3.1절)"""
        self._start_with_views()
        path = resolve_source_filepath(self.root, "a.mp4")
        with (
            mock.patch.object(item_actions_module, "ask_discard_replace", return_value=False) as ask,
            mock.patch.object(EncodeController, "discard_replace") as discard,
        ):
            self.coordinator._items.discard_replace(path)
            ask.assert_called_once()
            discard.assert_not_called()
        with (
            mock.patch.object(item_actions_module, "ask_discard_replace", return_value=True),
            mock.patch.object(EncodeController, "discard_replace", return_value=(True, "")) as discard,
            mock.patch.object(self.coordinator._items, "_refresh_batch") as refresh,
        ):
            self.coordinator._items.discard_replace(path)
        discard.assert_called_once()
        refresh.assert_called_once()

    def test_failed_discard_notice_keeps_the_row_and_explains_why(self) -> None:
        """임시 파일을 지우지 못한 폐기는 사유를 안내하고 행을 다시 읽지 않음"""
        self._start_with_views()
        path = resolve_source_filepath(self.root, "a.mp4")
        with (
            mock.patch.object(item_actions_module, "ask_discard_replace", return_value=True),
            mock.patch.object(
                EncodeController,
                "discard_replace",
                return_value=(False, "임시 결과 파일을 삭제하지 못했습니다: 공유 위반"),
            ),
            mock.patch.object(self.coordinator._items, "_refresh_batch") as refresh,
            mock.patch.object(item_actions_module.QMessageBox, "information") as notice,
        ):
            self.coordinator._items.discard_replace(path)
        refresh.assert_not_called()
        self.assertIn("공유 위반", notice.call_args.args[2])

    def test_replace_designated_needs_no_confirmation_and_refreshes_after_it(self) -> None:
        """그대로 교체는 확인 없이 실행기에 넘기고 성공하면 행을 다시 읽음(TERMINOLOGY)"""
        self._start_with_views()
        path = resolve_source_filepath(self.root, "a.mp4")
        with (
            mock.patch.object(EncodeController, "replace_designated", return_value=True) as replace,
            mock.patch.object(self.coordinator._items, "_refresh_batch") as refresh,
        ):
            self.coordinator._items.replace_designated(path)
        replace.assert_called_once()
        refresh.assert_called_once()

    def test_media_actions_open_or_preview_existing_files_and_warn_when_missing(self) -> None:
        """진행 창의 열기와 미리 보기는 파일이 있을 때만 실행하고, 없으면 안내"""
        existing = os.path.join(self.root, "present.mp4")
        with open(existing, "wb") as handle:
            handle.write(b"x")
        missing = os.path.join(self.root, "missing.mp4")
        with (
            mock.patch.object(item_actions_module.QMessageBox, "information") as notice,
            mock.patch("os.startfile", create=True) as start,
        ):
            self.coordinator._items.open_file(existing)
            self.coordinator._items.open_file(missing)
            self.coordinator._items.preview_file(existing)
            self.coordinator._items.preview_file(missing)
        start.assert_called_once_with(existing)
        self.preview.preview_path.assert_called_once_with(existing, owner=self.coordinator._progress_window)
        self.assertEqual(notice.call_count, 2)

    def test_compare_files_from_the_progress_window_passes_the_window_as_owner(self) -> None:
        """진행 창의 화질 비교 요청이 미리 보기 창의 소유자로 진행 창을 넘기는지 검증"""
        calls: list[tuple[str, object]] = []
        self.coordinator._compare_files = _recording_opener(calls)
        self.coordinator._ensure_progress_window()
        self.coordinator._items.compare_files("C:/work/a.mp4")
        self.assertEqual(calls, [("C:/work/a.mp4", self.coordinator._progress_window)])

    def test_compare_files_without_an_opener_does_nothing(self) -> None:
        """비교 열기 함수가 없으면 진행 창 비교 요청이 조용히 끝나는지 검증"""
        self.coordinator._compare_files = None
        self.coordinator._items.compare_files("C:/work/a.mp4")
        self.assertIsNone(self.coordinator._progress_window)

    def test_replace_retry_finished_signal_refreshes_the_rows_from_the_record(self) -> None:
        """재시도 워커가 끝나 신호를 보내면 기록을 다시 읽어 진행 창을 갱신"""
        self._start_with_views()
        with mock.patch.object(EncodeProgressWindow, "refresh_rows") as refresh:
            self.coordinator._controller.replace_retry_finished.emit("")
        refresh.assert_called_once()

    def test_confirmed_forfeit_refreshes_the_rows_from_the_record(self) -> None:
        """교체 포기를 확인하면 기록을 다시 읽어 진행 창의 행을 갱신"""
        self._start_with_views()
        path = resolve_source_filepath(self.root, "a.mp4")
        with (
            mock.patch.object(item_actions_module, "ask_forfeit", return_value=True),
            mock.patch.object(EncodeController, "forfeit_replace", return_value=True),
            mock.patch.object(EncodeProgressWindow, "refresh_rows") as refresh,
        ):
            self.coordinator._items.forfeit_replace(path)
        refresh.assert_called_once()

    def _resumable_batch(self, root: str, name: str) -> int:
        """이어서 인코딩 대상 배치 하나를 기록 DB에 만듦"""
        source = os.path.join(root, name)
        settings_json = EncodeSettings.from_config(self.config).to_json()
        return self.coordinator._history.create_batch(
            root, settings_json, [NewItem(source, 4, 1, job_to_json(_plan_job(source)))]
        )

    def test_resume_is_asked_once_per_batch_in_the_same_folder(self) -> None:
        """다른 폴더의 배치는 묻지 않고, 같은 폴더의 배치는 나중에를 골라도 같은 실행 안에서 다시 묻지 않음"""
        other = os.path.join(self.root, "other")
        os.makedirs(other)
        self._resumable_batch(self.root, "a.mp4")
        with mock.patch.object(coordinator_module, "ask_resume", return_value=ResumeChoice.LATER) as ask:
            self.coordinator.check_resume(other)
            self.coordinator.check_resume(self.root)
            self.coordinator.check_resume(self.root)
        ask.assert_called_once()

    def test_several_open_batches_in_a_folder_prompt_only_once_per_run(self) -> None:
        """열린 배치가 여럿이어도 한 실행에서는 한 번만 묻고, 이어 하지 않음 뒤에도 다음 배치를 이어서 묻지 않음"""
        self._resumable_batch(self.root, "a.mp4")
        self._resumable_batch(self.root, "b.mp4")
        with mock.patch.object(coordinator_module, "ask_resume", return_value=ResumeChoice.DISCARD) as ask:
            self.coordinator.check_resume(self.root)
        ask.assert_called_once()
        self.assertEqual(len(self.coordinator._history.list_open_batches()), 1)

    def test_several_open_batches_in_a_folder_ask_about_the_most_recent_one(self) -> None:
        """열린 배치가 여럿이면 가장 최근 배치를 묻고, 그 배치를 이어 하지 않음으로 닫아도 같은 실행에서 다시 묻지 않음"""
        self._resumable_batch(self.root, "a.mp4")
        newest = self._resumable_batch(self.root, "b.mp4")
        with (
            mock.patch.object(coordinator_module, "ask_resume", return_value=ResumeChoice.DISCARD) as ask,
            mock.patch.object(self.coordinator._cleanup, "discard_batch") as discard,
        ):
            self.coordinator.check_resume(self.root)
            self.coordinator.check_resume(self.root)
        ask.assert_called_once()
        discard.assert_called_once_with(newest)

    def test_previous_discard_of_an_unchanged_source_is_not_a_target_again(self) -> None:
        """이전에 결과 폐기된 원본이 그대로면 인코딩 대상에서 빼고, 원본이 바뀌면 다시 대상으로 넣음"""
        source = os.path.join(self.root, "a.mp4")
        with open(source, "wb") as handle:
            handle.write(b"source")
        stat = os.stat(source)
        settings = EncodeSettings.from_config(self.coordinator._session.state.to_classifier_config())
        plan = json.dumps({"quality": BASELINE_CQ_DEFAULT})
        batch_id = self.coordinator._history.create_batch(
            self.root, settings.to_json(), [NewItem(source, stat.st_size, stat.st_mtime_ns, plan)]
        )
        self.coordinator._history.update_item(batch_id, source, {"state": EncodeState.DISCARDED})
        self.assertEqual(self.coordinator._targets(self.report), [])
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        self.assertEqual(len(self.coordinator._targets(self.report)), 1)

    def test_progress_window_is_an_independent_nonmodal_window_like_the_settings_dialog(self) -> None:
        """진행 창은 메인 창의 자식이 아닌 독립 창이고 비모달이어야 함. 설정 창과 같은 방식"""
        window = self.coordinator._ensure_progress_window()
        self.assertIsNone(window.parentWidget())
        self.assertEqual(window.windowModality(), Qt.WindowModality.NonModal)
        self.assertTrue(window.windowFlags() & Qt.WindowType.Window)

    def test_discarded_row_shows_its_estimate_and_size_in_the_result_list(self) -> None:
        """조기 중단으로 폐기된 항목은 기록된 추정 크기를 결과 목록의 크기 변화 칸에 씀"""
        batch_id = self._resumable_batch(self.root, "d.mp4")
        source = os.path.join(self.root, "d.mp4")
        self.coordinator.book.start([source])
        self.coordinator._history.update_item(
            batch_id,
            source,
            {"state": EncodeState.DISCARDED, "reason": "진행 중 중단, 예상 크기가 원본보다 큼", "early_stop_estimate_bytes": 150},
        )
        self.coordinator._on_event(EncodeEvent(EVENT_STATE, batch_id, source, state=EncodeState.DISCARDED))
        results = self.coordinator._ensure_progress_window()._results
        self.assertIn("추정", results.size_text(0))

    def test_discard_closes_the_batch_and_keeps_its_files(self) -> None:
        """이어 하지 않음을 고르면 배치를 닫고 목록에서 뺌"""
        self._resumable_batch(self.root, "b.mp4")
        with mock.patch.object(coordinator_module, "ask_resume", return_value=ResumeChoice.DISCARD):
            self.coordinator.check_resume(self.root)
        self.assertEqual(self.coordinator._history.list_open_batches(), [])

    def test_resume_hands_the_batch_to_the_controller_with_its_saved_settings(self) -> None:
        """이어서 인코딩을 고르면 저장된 설정으로 배치를 실행기에 넘기고 진행 창을 연다"""
        batch_id = self._resumable_batch(self.root, "c.mp4")
        with (
            mock.patch.object(coordinator_module, "ask_resume", return_value=ResumeChoice.RESUME),
            mock.patch.object(EncodeController, "resume") as resume,
        ):
            self.coordinator.check_resume(self.root)
        resume.assert_called_once()
        self.assertEqual(resume.call_args.args[0], batch_id)
        self.assertEqual(resume.call_args.args[1].encoder, EncodeSettings.from_config(self.config).encoder)
        self.assertIsNotNone(self.coordinator._progress_window)

    def test_resume_cleanup_choice_opens_the_cleanup_and_asks_the_same_batch_again(self) -> None:
        """이어서 인코딩 확인에서 정리를 고르면 정리 창을 연 뒤 같은 배치를 다시 묻는다"""
        self._resumable_batch(self.root, "f.mp4")
        with (
            mock.patch.object(coordinator_module, "ask_resume", side_effect=[ResumeChoice.CLEANUP, ResumeChoice.LATER]) as ask,
            mock.patch.object(coordinator_module.LeftoverCleanup, "open") as opened,
        ):
            self.coordinator.check_resume(self.root)
        self.assertEqual(ask.call_count, 2)
        opened.assert_called_once()

    def test_trash_leftover_asks_only_for_part_left_rows_and_uses_the_planned_part(self) -> None:
        """임시 파일 때문에 실패한 행만 정리 흐름으로 넘기고, 그 행이 쓰려던 임시 파일 경로를 넘김"""
        planned = os.path.join(self.root, "_인코딩결과", "g.mp4")
        source = self._resume_with_row("g.mp4", {"state": EncodeState.FAILED, "reason": PART_LEFT, "output_path": planned})
        with mock.patch.object(coordinator_module.LeftoverCleanup, "trash_part") as trash:
            self.coordinator._items.trash_leftover(source)
        trash.assert_called_once_with(planned + ".part")
        other = self._resume_with_row("h.mp4", {"state": EncodeState.FAILED, "reason": "다른 실패", "output_path": planned})
        with mock.patch.object(coordinator_module.LeftoverCleanup, "trash_part") as trash:
            self.coordinator._items.trash_leftover(other)
        trash.assert_not_called()

    def _batch_at(self, root_spec: str, name: str) -> int:
        """여러 작업 폴더를 세미콜론으로 이은 배치를 기록 DB에 만듦. 항목 경로는 첫 폴더 아래"""
        first = root_spec.split(";")[0]
        source = os.path.join(first, name)
        settings_json = EncodeSettings.from_config(self.config).to_json()
        return self.coordinator._history.create_batch(
            root_spec, settings_json, [NewItem(source, 4, 1, job_to_json(_plan_job(source)))]
        )

    def _resume_with_row(self, name: str, fields: dict[str, object]) -> str:
        """배치를 만들고 항목 행을 주어진 값으로 바꾼 뒤 이어서 인코딩으로 연다. 항목의 원본 경로를 돌려줌"""
        source = os.path.join(self.root, name)
        batch_id = self._resumable_batch(self.root, name)
        self.coordinator._history.update_item(batch_id, source, fields)
        with (
            mock.patch.object(coordinator_module, "ask_resume", return_value=ResumeChoice.RESUME),
            mock.patch.object(EncodeController, "resume"),
        ):
            self.coordinator.check_resume(self.root)
        return source

    def test_resume_into_a_fresh_book_fills_its_items_and_the_result_list(self) -> None:
        """앱을 새로 띄운 빈 상태 저장소에서 이어 하면 배치 항목이 대상 수와 상태에 들어가고, 교체 대기 행이 결과 목록에 나오는지 검증"""
        output = os.path.join(self.root, "_인코딩결과", "e.mp4")
        os.makedirs(os.path.dirname(output))
        with open(output + ".part", "wb") as handle:
            handle.write(b"partial")
        source = self._resume_with_row(
            "e.mp4",
            {"state": EncodeState.REPLACE_PENDING, "reason": "교체 실패", "part_path": output + ".part", "output_path": output, "output_size": 7},
        )
        self.assertEqual(self.coordinator.book.total(), 1)
        self.assertIs(self.coordinator.book.state_of(source), EncodeState.REPLACE_PENDING)
        window = self.coordinator._progress_window
        assert window is not None
        self.assertEqual(window._results.row_count(), 1)

    def test_resume_asks_for_a_batch_whose_folders_all_lie_inside_a_wider_working_folder(self) -> None:
        """작업 폴더를 더 넓게 열어도 배치의 모든 폴더가 그 안에 있으면 같은 폴더로 보고 묻는지 검증"""
        first = os.path.join(self.root, "first")
        second = os.path.join(self.root, "second")
        self._batch_at(f"{first};{second}", "f.mp4")
        with mock.patch.object(coordinator_module, "ask_resume", return_value=ResumeChoice.LATER) as ask:
            self.coordinator.check_resume(self.root)
        ask.assert_called_once()

    def test_unrecorded_parts_are_listed_from_every_folder_of_a_batch(self) -> None:
        """여러 폴더 배치의 기록 밖 임시 파일은 두 번째 폴더에 있어도 안내 목록에 들어가는지 검증"""
        first = os.path.join(self.root, "first")
        second = os.path.join(self.root, "second")
        os.makedirs(second)
        stray = os.path.join(second, "stray.mp4.part")
        with open(stray, "wb") as handle:
            handle.write(b"x")
        self._batch_at(f"{first};{second}", "g.mp4")
        with mock.patch.object(coordinator_module, "ask_resume", return_value=ResumeChoice.LATER) as ask:
            self.coordinator.check_resume(self.root)
        self.assertEqual(ask.call_args.args[2], [stray])

    def test_batch_folders_must_all_lie_inside_the_working_folders(self) -> None:
        """배치 폴더 판정은 모든 폴더가 작업 폴더 안에 있을 때만 참이고, 이름만 같은 접두 폴더는 안이 아님"""
        a = os.path.join(self.root, "a")
        ab = os.path.join(self.root, "ab")
        b = os.path.join(self.root, "b")
        self.assertTrue(coordinator_module.batch_in_roots(f"{a};{b}", f"{a};{b};{ab}"))
        self.assertFalse(coordinator_module.batch_in_roots(f"{a};{b}", a))
        self.assertTrue(coordinator_module.batch_in_roots(os.path.join(a, "sub"), a))
        self.assertFalse(coordinator_module.batch_in_roots(ab, a))

    def test_reveal_leftover_selects_the_recorded_part_of_a_replace_pending_row(self) -> None:
        """교체 대기 행의 더블클릭은 원본이 아니라 그 결과 임시 파일을 탐색기에서 선택"""
        output = os.path.join(self.root, "_인코딩결과", "h.mp4")
        os.makedirs(os.path.dirname(output))
        source = self._resume_with_row(
            "h.mp4", {"state": EncodeState.REPLACE_PENDING, "reason": "교체 실패", "part_path": output + ".part", "output_path": output}
        )
        with mock.patch.object(item_actions_module, "reveal_path", return_value=True) as reveal:
            self.assertTrue(self.coordinator._items.reveal_leftover(source))
        reveal.assert_called_once_with(output + ".part")

    def test_reveal_leftover_uses_the_planned_part_of_a_part_left_failure(self) -> None:
        """임시 파일 때문에 실패한 행은 기록된 결과 경로에 붙은 임시 파일을 선택"""
        planned = os.path.join(self.root, "_인코딩결과", "i.mp4")
        source = self._resume_with_row("i.mp4", {"state": EncodeState.FAILED, "reason": PART_LEFT, "output_path": planned})
        with mock.patch.object(item_actions_module, "reveal_path", return_value=True) as reveal:
            self.assertTrue(self.coordinator._items.reveal_leftover(source))
        reveal.assert_called_once_with(planned + ".part")

    def test_reveal_leftover_is_false_for_done_rows_so_their_result_opens(self) -> None:
        """완료 행은 임시 파일이 없으므로 위치 선택을 하지 않고 거짓을 돌려줘 결과 파일 열기로 넘어감"""
        output = os.path.join(self.root, "_인코딩결과", "j.mp4")
        source = self._resume_with_row("j.mp4", {"state": EncodeState.DONE, "output_path": output, "part_path": None})
        with mock.patch.object(item_actions_module, "reveal_path") as reveal:
            self.assertFalse(self.coordinator._items.reveal_leftover(source))
        reveal.assert_not_called()

    def test_reveal_leftover_says_the_file_is_missing_when_the_part_is_gone(self) -> None:
        """임시 파일이 이미 없으면 파일 없음 안내를 띄움"""
        output = os.path.join(self.root, "_인코딩결과", "k.mp4")
        source = self._resume_with_row("k.mp4", {"state": EncodeState.REPLACE_PENDING, "part_path": output + ".part", "output_path": output})
        with (
            mock.patch.object(item_actions_module, "reveal_path", return_value=False),
            mock.patch.object(item_actions_module.QMessageBox, "information") as notice,
        ):
            self.assertTrue(self.coordinator._items.reveal_leftover(source))
        notice.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
