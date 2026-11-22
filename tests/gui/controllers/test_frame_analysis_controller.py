"""화질 분석 컨트롤러의 요청 구성, 워커 본문, 진행, 결과 반영, VR 보호, 후보 적용 검증"""

import os
import tempfile
import threading
import unittest
from collections.abc import Callable, Iterable
from typing import cast
from unittest import mock

from PyQt6.QtWidgets import QApplication, QWidget

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.config import ClassifierConfig
from src.core.encode.ffmpeg_capability import FfmpegCapability
from src.core.models import SummaryReport, VideoEvaluation
from src.gui.controllers.frame_analysis_controller import (
    FrameAnalysisController,
    FrameAnalysisFinished,
    FrameAnalysisHost,
    FrameAnalysisProgress,
    FrameAnalysisWindow,
    FrameAnalysisWorker,
    attach_frame_analysis,
    build_requests,
    completion_status_text,
    run_frame_analysis_body,
)
from src.gui.menus.menu_bar import MainMenuController
from src.gui.table.video_table_view import VideoTableView
from src.gui.widgets.footer_bar import FooterBar
from src.service.frame_analysis_service import (
    FrameAnalysisRequest,
    FrameAnalysisService,
    FrameAnalysisStore,
)
from tests._factories import make_eval, make_report, make_video

_APP = QApplication.instance() or QApplication([])


class _Signal:
    """connect한 슬롯을 기록하고 emit으로 직접 부르는 신호 대역"""

    def __init__(self) -> None:
        self.slots: list[Callable[..., None]] = []

    def connect(self, slot: Callable[..., None]) -> None:
        self.slots.append(slot)

    def emit(self, *args: object) -> None:
        for slot in list(self.slots):
            slot(*args)


class _FakeModel:
    def __init__(self) -> None:
        self.modelReset = _Signal()
        self.results: dict[str, FrameAnalysisResult] = {}
        self.manual_vr: frozenset[str] = frozenset()
        self.handlers: dict[str, object] = {}

    def set_frame_analysis_results(self, results: dict[str, FrameAnalysisResult]) -> None:
        self.results = dict(results)

    def set_manual_vr_overrides(self, names: Iterable[str]) -> None:
        self.manual_vr = frozenset(names)

    def is_manual_vr(self, filename: str) -> bool:
        return filename in self.manual_vr

    def set_frame_action_handlers(self, **handlers: object) -> None:
        self.handlers.update(handlers)

    def frame_candidates(self, evals: list[VideoEvaluation]) -> dict[str, str]:
        return {"a.mp4": "720p"}


class _FakeView:
    def __init__(self) -> None:
        self.selection_changed = _Signal()
        self.table_model = _FakeModel()
        self.selected: list[VideoEvaluation] = []
        self.hidden: set[int] = set()

    def get_selected_evaluations(self) -> list[VideoEvaluation]:
        return list(self.selected)

    def get_selected_filenames(self) -> set[str]:
        return {ev.video.filename for ev in self.selected}

    def select_filenames(self, names: set[str]) -> None:
        self.selected = [ev for ev in self.selected if ev.video.filename in names]

    def isColumnHidden(self, col: int) -> bool:
        return col in self.hidden

    def setColumnHidden(self, col: int, hidden: bool) -> None:
        (self.hidden.add if hidden else self.hidden.discard)(col)


class _FakeFooter:
    def __init__(self) -> None:
        self.frame_analysis_cancel_requested = _Signal()
        self.destroyed = _Signal()
        self.progress: tuple[int, int, str] | None = None
        self.cleared: int = 0

    def set_frame_analysis_progress(self, completed: int, total: int, filename: str) -> None:
        self.progress = (completed, total, filename)

    def clear_frame_analysis_progress(self) -> None:
        self.cleared += 1


class _FakeMenu:
    def __init__(self) -> None:
        self.act_frame_selected = mock.Mock()
        self.act_frame_all = mock.Mock()
        self.act_frame_selected.triggered = _Signal()
        self.act_frame_all.triggered = _Signal()
        self.calls: list[dict[str, object]] = []

    def sync_frame_analysis_state(self, **state: object) -> None:
        self.calls.append(state)


class _FakeHistory:
    def __init__(self) -> None:
        self.commands: list[Callable[[], None]] = []

    def run_command(self, _label: str, action: Callable[[], None]) -> None:
        self.commands.append(action)
        action()


def _result() -> FrameAnalysisResult:
    return FrameAnalysisResult(
        active_region=ActiveRegion(1920, 1080, 0, 0, False),
        metric_used="vmaf",
        vmaf_model="vmaf_v0.6.1",
        tier_scores=(),
        candidate_tier="720p",
        valid_segment_count=5,
        total_segment_count=5,
        unmeasurable=False,
        is_vr_protected=False,
    )


class _FakeService:
    def __init__(self, behaviour: str = "ok", cached_count: int = 0) -> None:
        self.behaviour = behaviour
        self.progress_calls: list[tuple[int, int, str]] = []
        self.load_calls = 0
        self.cached_count = cached_count

    def analyze_batch(self, requests: list[object], *, cancel_event: threading.Event | None = None, on_progress: object = None) -> dict[str, FrameAnalysisResult]:
        if self.behaviour == "error":
            raise RuntimeError("boom")
        if self.behaviour == "cancel" and cancel_event is not None:
            cancel_event.set()
            raise InterruptedError("cancel")
        if callable(on_progress):
            on_progress(1, len(requests), "a.mp4", _result())
        return {}

    def count_cached(self, requests: list[object]) -> int:
        return self.cached_count if requests else 0

    def load_results(self, requests: list[object]) -> dict[str, FrameAnalysisResult]:
        self.load_calls += 1
        return {"a.mp4": _result()}


class _FakeWorker:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.progress = _Signal()
        self.completed = _Signal()
        self.started = 0
        self.waited = 0
        self.cancelled = 0

    def start(self) -> None:
        self.started += 1

    def wait(self) -> None:
        self.waited += 1

    def cancel(self) -> None:
        self.cancelled += 1


class _ControllerCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        with open(os.path.join(self.root, "a.mp4"), "wb") as handle:
            handle.write(b"video")
        self.ev = make_eval(video=make_video(filename="a.mp4"))
        self.report = make_report([self.ev], root_dir=self.root)
        self.store = FrameAnalysisStore(os.path.join(self.root, "video_cache.sqlite3"))
        self.addCleanup(self.store.close)
        self.view = _FakeView()
        self.footer = _FakeFooter()
        self.menu = _FakeMenu()
        self.shown = 0
        self.notifications: list[str] = []
        self.reevaluated = 0

    def _write_video(self, directory: str, filename: str) -> str:
        path = os.path.join(directory, filename)
        with open(path, "wb") as handle:
            handle.write(b"video")
        return path

    def _two_eval_report(self) -> tuple[VideoEvaluation, SummaryReport]:
        other = make_eval(video=make_video(filename="b.mp4"))
        self._write_video(self.root, "b.mp4")
        return other, make_report([self.ev, other], root_dir=self.root)

    def _re_evaluate(self, _done: Callable[[], None]) -> None:
        self.reevaluated += 1

    def make_controller(self, *, report: SummaryReport | None = None) -> FrameAnalysisController:
        host = FrameAnalysisHost(
            parent=QWidget(),
            table_view=cast("VideoTableView", self.view),
            footer=cast("FooterBar", self.footer),
            menu=cast("MainMenuController", self.menu),
            report_provider=lambda: report if report is not None else self.report,
            config_provider=ClassifierConfig,
            is_busy=lambda: False,
            re_evaluate=self._re_evaluate,
            apply_candidates=lambda evals: self.notifications.append(f"apply:{len(evals)}"),
            show_columns=lambda: setattr(self, "shown", self.shown + 1),
            notify=self.notifications.append,
        )
        return FrameAnalysisController(host, self.store)


class RequestBuildingTests(_ControllerCase):
    """평가 결과에서 화질 분석 요청을 만들고 VR 메타데이터와 수동 보호를 반영하는지 검증"""

    def test_build_requests_reads_stat_stream_summary_and_overrides(self) -> None:
        vr_summary = '[{"kind":"video","codec":"h264","attached_pic":false,"side_data_types":["Spherical Mapping"]}]'
        ev = make_eval(video=make_video(filename="a.mp4", stream_summary=vr_summary))
        requests = build_requests([ev], self.root, frozenset({os.path.join(self.root, "a.mp4")}))
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0].has_vr_metadata)
        self.assertTrue(requests[0].manual_vr)
        self.assertEqual(requests[0].filesize_bytes, 5)
        self.assertEqual(build_requests([ev], self.root, frozenset()), build_requests([ev], self.root, frozenset()))

    def test_build_requests_skips_missing_files(self) -> None:
        missing = make_eval(video=make_video(filename="missing.mp4"))
        self.assertEqual(build_requests([missing], self.root, frozenset()), [])



class WorkerBodyTests(unittest.TestCase):
    """QThread 밖 본문의 진행, 완료, 취소, 오류 처리를 검증"""

    def test_success_reports_progress_and_loads_cached_results(self) -> None:
        events: list[object] = []
        service = _FakeService()
        run_frame_analysis_body(cast("FrameAnalysisService", service), [], threading.Event(), events.append)
        self.assertIsInstance(events[0], FrameAnalysisProgress)
        finished = events[-1]
        self.assertIsInstance(finished, FrameAnalysisFinished)
        assert isinstance(finished, FrameAnalysisFinished)
        self.assertEqual(set(finished.results), {"a.mp4"})
        self.assertFalse(finished.cancelled)

    def test_cancel_and_error_still_finish(self) -> None:
        cancel = threading.Event()
        events: list[object] = []
        run_frame_analysis_body(cast("FrameAnalysisService", _FakeService("cancel")), [], cancel, events.append)
        finished = events[-1]
        assert isinstance(finished, FrameAnalysisFinished)
        self.assertTrue(finished.cancelled)
        error_events: list[object] = []
        run_frame_analysis_body(
            cast("FrameAnalysisService", _FakeService("error")), [], threading.Event(), error_events.append
        )
        error_finished = error_events[-1]
        assert isinstance(error_finished, FrameAnalysisFinished)
        self.assertIn("boom", error_finished.error)

    def test_body_reports_request_total_and_cache_reuse(self) -> None:
        """요청 수와 캐시 재사용 수를 실행 전에 재어 종료 이벤트에 담는지 검증"""
        request = FrameAnalysisRequest(
            filename="a.mp4",
            path="a.mp4",
            width=1920,
            height=1080,
            duration_sec=20.0,
            filesize_bytes=100,
            mtime_ns=1,
        )
        events: list[object] = []
        service = _FakeService(cached_count=1)
        run_frame_analysis_body(cast("FrameAnalysisService", service), [request], threading.Event(), events.append)
        finished = events[-1]
        assert isinstance(finished, FrameAnalysisFinished)
        self.assertEqual((finished.total, finished.cached_count), (1, 1))

    def test_worker_emits_progress_and_completed_signals(self) -> None:
        worker = FrameAnalysisWorker(cast("FrameAnalysisService", _FakeService()), [], threading.Event())
        progress: list[tuple[int, int, str]] = []
        completed: list[object] = []
        worker.progress.connect(lambda c, t, n: progress.append((c, t, n)))
        worker.completed.connect(completed.append)
        worker.run()
        self.assertTrue(progress)
        self.assertEqual(len(completed), 1)
        worker.cancel()


class ControllerTests(_ControllerCase):
    """컨트롤러가 캐시 반영, 메뉴 상태, 시작, 완료, VR 보호, 후보 적용을 조율하는지 검증"""

    def test_completion_status_text_mentions_cache_reuse(self) -> None:
        """완료 상태 줄이 요청 수와 캐시 재사용 수를 승인 문구로 알리는지 검증"""
        reused = FrameAnalysisFinished({"a.mp4": _result()}, total=1, cached_count=1)
        fresh = FrameAnalysisFinished({"a.mp4": _result()}, total=1)
        self.assertEqual(
            completion_status_text(reused), "화질 분석 완료: 1개 (캐시 재사용 1개)"
        )
        self.assertEqual(completion_status_text(fresh), "화질 분석 완료: 1개")
        self.assertEqual(completion_status_text(FrameAnalysisFinished({})), "")

    def test_completion_announces_the_request_and_cache_reuse(self) -> None:
        """캐시만으로 끝난 분석도 상태 표시줄에서 완료와 재사용 수를 알리는지 검증"""
        controller = self.make_controller()
        controller._start_root = self.root
        controller._on_completed(FrameAnalysisFinished({"a.mp4": _result()}, total=1, cached_count=1))
        self.assertEqual(self.notifications[-1], "화질 분석 완료: 1개 (캐시 재사용 1개)")

    def test_reload_cached_reflects_empty_and_loaded_results(self) -> None:
        controller = self.make_controller()
        controller.reload_cached()
        self.assertEqual(self.view.table_model.results, {})
        self.view.table_model.modelReset.emit()
        self.assertTrue(self.menu.calls)
        controller.refresh_actions()

    def test_reload_cached_applies_manual_vr_names(self) -> None:
        self.store.set_vr_override(
            source_path=os.path.join(self.root, "a.mp4"), filename="a.mp4", manual_vr=True
        )
        controller = self.make_controller()
        controller.reload_cached()
        self.assertEqual(self.view.table_model.manual_vr, frozenset({"a.mp4"}))

    def test_apply_results_shows_columns_once_per_root(self) -> None:
        controller = self.make_controller()
        controller._results = {"a.mp4": _result()}
        controller._apply_results()
        controller._apply_results()
        self.assertEqual(self.shown, 1)
        self.assertEqual(set(self.view.table_model.results), {"a.mp4"})

    def test_toggle_manual_vr_updates_store_notice_and_results(self) -> None:
        controller = self.make_controller()
        controller._results = {"a.mp4": _result()}
        controller.toggle_manual_vr([self.ev], True)
        self.assertEqual(self.store.load_vr_overrides([os.path.join(self.root, "a.mp4")]), frozenset({os.path.join(self.root, "a.mp4")}))
        self.assertEqual(controller._results, {})
        self.assertIn("보호를 켰습니다", self.notifications[-1])
        controller.toggle_manual_vr([self.ev], False)
        self.assertIn("보호를 껐습니다", self.notifications[-1])

    def test_apply_candidates_delegates_to_host(self) -> None:
        controller = self.make_controller()
        controller.apply_candidates([self.ev])
        self.assertEqual(self.notifications, ["apply:1"])

    def test_start_early_returns_for_no_targets_or_error_capability(self) -> None:
        controller = self.make_controller()
        controller.analyze_selected()
        self.assertIsNone(controller._worker)
        self.view.selected = [self.ev]
        failed = FfmpegCapability(executable="ffmpeg", version_text="", version=None, usable=False, reason="없음")
        with mock.patch("src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability", return_value=failed):
            controller.analyze_selected()
        self.assertIn("FFmpeg를 쓸 수 없어", self.notifications[-1])
        with mock.patch("src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability", return_value=failed), mock.patch(
            "src.gui.controllers.frame_analysis_controller.build_requests", return_value=[]
        ):
            controller.analyze_all()
        self.assertIn("찾지 못했습니다", self.notifications[-1])

    def test_start_runs_worker_and_completion_clears_progress(self) -> None:
        controller = self.make_controller()
        self.view.selected = [self.ev]
        capability = FfmpegCapability(
            executable="ffmpeg", version_text="", version=None, filters=frozenset({"libvmaf"}), usable=True
        )
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability", return_value=capability
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            controller.analyze_selected()
        self.assertTrue(controller.is_running)
        self.assertEqual(self.footer.progress, (0, 1, "a.mp4"))
        self.assertTrue(self.menu.calls[-1]["running"])
        controller._on_progress(1, 1, "a.mp4")
        self.assertEqual(self.footer.progress, (1, 1, "a.mp4"))
        controller._on_completed(FrameAnalysisFinished({"a.mp4": _result()}))
        self.assertFalse(controller.is_running)
        self.assertEqual(self.footer.cleared, 1)
        self.assertEqual(self.reevaluated, 1)
        self.assertFalse(self.menu.calls[-1]["running"])

    def test_cancel_keeps_previous_results_and_reevaluates_partial_results(self) -> None:
        """선택 영상 분석을 취소해도 기존 다른 영상 결과를 유지하고 완료분은 재평가하는지 검증"""
        other, report = self._two_eval_report()
        self.report = report
        controller = self.make_controller()
        controller._results = {"a.mp4": _result()}
        self.view.selected = [other]
        capability = FfmpegCapability(
            executable="ffmpeg", version_text="", version=None, filters=frozenset({"libvmaf"}), usable=True
        )
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability", return_value=capability
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            controller.analyze_selected()
        controller._on_completed(FrameAnalysisFinished({"b.mp4": _result()}, cancelled=True))
        self.assertEqual(set(controller._results), {"a.mp4", "b.mp4"})
        self.assertEqual(set(self.view.table_model.results), {"a.mp4", "b.mp4"})
        self.assertEqual(self.reevaluated, 1)
        self.assertEqual(self.notifications[-1], "작업이 중단되었습니다.")

    def test_manual_vr_after_stopped_selection_analysis_updates_other_rows(self) -> None:
        """선택 분석이 오류로 끝난 뒤 다른 행의 VR 보호를 켜도 표 체크 상태가 반영되는지 검증"""
        other, report = self._two_eval_report()
        self.report = report
        controller = self.make_controller()
        self.view.selected = [self.ev]
        capability = FfmpegCapability(
            executable="ffmpeg", version_text="", version=None, filters=frozenset({"libvmaf"}), usable=True
        )
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability", return_value=capability
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            controller.analyze_selected()
        controller._on_completed(FrameAnalysisFinished({}, error="boom"))
        controller.toggle_manual_vr([other], True)
        self.assertTrue(self.view.table_model.is_manual_vr("b.mp4"))
        self.assertFalse(self.view.table_model.is_manual_vr("a.mp4"))

    def test_folder_switch_cancels_running_analysis_and_ignores_stale_results(self) -> None:
        """작업 폴더가 바뀌면 실행 중인 화질 분석을 취소하고 이전 폴더의 지연 완료를 반영하지 않는지 검증"""
        _other, report = self._two_eval_report()
        self.report = report
        controller = self.make_controller()
        self.view.selected = [self.ev]
        capability = FfmpegCapability(
            executable="ffmpeg", version_text="", version=None, filters=frozenset({"libvmaf"}), usable=True
        )
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability", return_value=capability
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            controller.analyze_selected()
        worker = cast("_FakeWorker", controller._worker)
        other_root = os.path.join(self.root, "other")
        os.makedirs(other_root)
        self._write_video(other_root, "c.mp4")
        other = make_eval(video=make_video(filename="c.mp4"))
        self.report = make_report([other], root_dir=other_root)
        controller.reload_cached()
        self.assertEqual(worker.cancelled, 1)
        controller._on_completed(FrameAnalysisFinished({"a.mp4": _result()}))
        self.assertEqual(controller._results, {})
        self.assertEqual(self.view.table_model.results, {})
        self.assertEqual(self.reevaluated, 0)
        self.assertEqual(self.notifications, [])

    def test_completion_cancel_error_and_shutdown(self) -> None:
        controller = self.make_controller()
        controller._start_root = self.root
        controller._on_completed(FrameAnalysisFinished({}, cancelled=True))
        self.assertEqual(self.notifications[-1], "작업이 중단되었습니다.")
        controller._on_completed(FrameAnalysisFinished({}, error="boom"))
        self.assertIn("오류가 발생했습니다", self.notifications[-1])
        controller._on_completed(object())
        self.assertEqual(self.footer.cleared, 3)
        worker = _FakeWorker()
        controller._worker = cast("FrameAnalysisWorker", worker)
        controller.cancel()
        controller.shutdown()
        self.assertEqual((worker.cancelled, worker.waited), (2, 1))


class _FakeWindow:
    def __init__(self, case: _ControllerCase) -> None:
        self.table_view = case.view
        self.footer_bar = case.footer
        self.menu = case.menu
        self.manual_targets = mock.Mock()
        self.column_history = _FakeHistory()
        self.current_report: SummaryReport | None = case.report
        self.classifier: object | None = mock.Mock(config=ClassifierConfig())
        self.statuses: list[str] = []

    def set_status_text(self, text: str, tooltip: str = "") -> None:
        self.statuses.append(text)


class AttachTests(_ControllerCase):
    """메인 창에 붙일 때 메뉴, 하단 취소, 우클릭 요청을 연결하고 열 표시를 실행하는지 검증"""

    def test_attach_wires_actions_and_shows_columns(self) -> None:
        window = _FakeWindow(self)
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.open_default_store",
            return_value=mock.Mock(db_path=os.path.join(self.root, "video_cache.sqlite3")),
        ):
            controller = attach_frame_analysis(cast("FrameAnalysisWindow", window), self._re_evaluate, lambda: False)
        self.assertEqual(set(self.view.table_model.handlers), {"apply_candidates", "toggle_vr"})
        self.view.hidden = {19, 20}
        controller._results = {"a.mp4": _result()}
        controller._apply_results()
        self.assertFalse(self.view.hidden)
        self.assertEqual(window.column_history.commands, [])
        capability = FfmpegCapability(
            executable="ffmpeg", version_text="", version=None, filters=frozenset({"libvmaf"}), usable=True
        )
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability", return_value=capability
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            window.menu.act_frame_all.triggered.emit()
        self.assertTrue(controller.is_running)
        controller._on_completed(FrameAnalysisFinished({}, cancelled=True))
        self.assertEqual(window.statuses[-1], "작업이 중단되었습니다.")
        controller.shutdown()


if __name__ == "__main__":
    unittest.main()

