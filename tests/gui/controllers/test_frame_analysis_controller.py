"""화질 분석 컨트롤러의 요청 구성, 워커 본문, 진행, 결과 반영, VR 보호, 후보 적용 검증"""

import os
import tempfile
import threading
import time
import unittest
from collections.abc import Callable, Iterable
from typing import cast
from unittest import mock

from PyQt6.QtWidgets import QApplication, QWidget

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.config import FRAME_ANALYSIS_MIN_TIER_ALL, ClassifierConfig
from src.core.encode.ffmpeg_capability import FfmpegCapability
from src.core.models import ActiveFrame, SummaryReport, VideoEvaluation
from src.core.smart_action import SmartAction
from src.gui.controllers.frame_analysis_controller import (
    FRAME_ANALYSIS_FLUSH_MS,
    FrameAnalysisController,
    FrameAnalysisFinished,
    FrameAnalysisHost,
    FrameAnalysisProgress,
    FrameAnalysisStageProgress,
    FrameAnalysisWindow,
    FrameAnalysisWorker,
    _is_below_frame_min_tier,
    _skip_for_crop_scan,
    _skip_for_frame_analysis,
    attach_frame_analysis,
    build_requests,
    completion_status_text,
    frame_analysis_stage_text,
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
        self.partial_calls: list[dict[str, FrameAnalysisResult]] = []
        self.manual_vr: frozenset[str] = frozenset()
        self.crop_excluded: frozenset[str] = frozenset()
        self.manual_frames: dict[str, object] = {}
        self.handlers: dict[str, object] = {}

    def set_frame_analysis_results(self, results: dict[str, FrameAnalysisResult]) -> None:
        self.results = dict(results)

    def update_frame_analysis_results(self, partial: dict[str, FrameAnalysisResult]) -> None:
        self.partial_calls.append(dict(partial))
        self.results.update(partial)

    def set_crop_exclusions(self, names: Iterable[str]) -> None:
        self.crop_excluded = frozenset(names)

    def set_manual_active_frames(self, frames: dict[str, object]) -> None:
        self.manual_frames = dict(frames)

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
        self.detail: str = ""
        self.remaining: str = ""
        self.tooltip: str = ""
        self.crop_only: bool = False
        self.cleared: int = 0

    def set_frame_analysis_progress(
        self,
        completed: int,
        total: int,
        filename: str,
        *,
        detail: str = "",
        remaining: str = "",
        work_dir: str = "",
        crop_only: bool = False,
    ) -> None:
        self.progress = (completed, total, filename)
        self.detail = detail
        self.remaining = remaining
        self.crop_only = crop_only
        self.tooltip = f"임시 작업 폴더: {work_dir}" if work_dir else ""

    def clear_frame_analysis_progress(self) -> None:
        self.cleared += 1


class _FakeMenu:
    def __init__(self) -> None:
        self.act_frame_selected = mock.Mock()
        self.act_frame_all = mock.Mock()
        self.act_crop_selected = mock.Mock()
        self.act_crop_all = mock.Mock()
        self.act_cancel = mock.Mock()
        self.act_frame_selected.triggered = _Signal()
        self.act_frame_all.triggered = _Signal()
        self.act_crop_selected.triggered = _Signal()
        self.act_crop_all.triggered = _Signal()
        self.act_cancel.triggered = _Signal()
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
        self.crop_only: bool = False
        self.count_crop_only: bool = False

    def analyze_batch(
        self,
        requests: list[object],
        *,
        crop_only: bool = False,
        crop_only_names: frozenset[str] = frozenset(),
        cancel_event: threading.Event | None = None,
        on_progress: object = None,
        on_stage: object = None,
    ) -> dict[str, FrameAnalysisResult]:
        self.crop_only = crop_only
        self.crop_only_names = crop_only_names
        if self.behaviour == "error":
            raise RuntimeError("boom")
        if self.behaviour == "cancel" and cancel_event is not None:
            cancel_event.set()
            raise InterruptedError("cancel")
        if callable(on_stage):
            on_stage("a.mp4", "ssim", 2, 9)
        if callable(on_progress):
            on_progress(1, len(requests), "a.mp4", _result())
        return {}

    def count_cached(
        self,
        requests: list[object],
        *,
        crop_only: bool = False,
        crop_only_names: frozenset[str] = frozenset(),
    ) -> int:
        self.count_crop_only = crop_only
        self.count_crop_only_names = crop_only_names
        return self.cached_count if requests else 0

    def load_results(self, requests: list[object]) -> dict[str, FrameAnalysisResult]:
        self.load_calls += 1
        return {"a.mp4": _result()}


class _FakeWorker:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.progress = _Signal()
        self.stage_progress = _Signal()
        self.completed = _Signal()
        self.kwargs = _kwargs
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
        self.shown_modes: list[bool] = []
        self.notifications: list[str] = []
        self.reevaluated = 0
        self.started: list[tuple[bool, bool]] = []

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

    def _record_columns(self, crop_only: bool) -> None:
        self.shown += 1
        self.shown_modes.append(crop_only)

    def make_controller(
        self,
        *,
        report: SummaryReport | None = None,
        config: ClassifierConfig | None = None,
    ) -> FrameAnalysisController:
        host = FrameAnalysisHost(
            parent=QWidget(),
            table_view=cast("VideoTableView", self.view),
            footer=cast("FooterBar", self.footer),
            menu=cast("MainMenuController", self.menu),
            report_provider=lambda: report if report is not None else self.report,
            config_provider=(lambda: config) if config is not None else ClassifierConfig,
            classifier_provider=lambda: None,
            start_analysis=lambda include_keyframe, force_reanalyze: self.started.append(
                (include_keyframe, force_reanalyze)
            ),
            is_busy=lambda: False,
            re_evaluate=self._re_evaluate,
            apply_candidates=lambda evals: self.notifications.append(f"apply:{len(evals)}"),
            show_columns=self._record_columns,
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
        self.assertIsInstance(events[0], FrameAnalysisStageProgress)
        self.assertTrue(any(isinstance(event, FrameAnalysisProgress) for event in events))
        finished = events[-1]
        self.assertIsInstance(finished, FrameAnalysisFinished)
        assert isinstance(finished, FrameAnalysisFinished)
        self.assertEqual(set(finished.results), {"a.mp4"})
        self.assertFalse(finished.cancelled)

    def test_stage_text_matches_approved_wording(self) -> None:
        """하단 세부 단계 문구가 TERMINOLOGY 6.17절 표기와 같은지 검증"""
        self.assertEqual(frame_analysis_stage_text("scan", 3, 9), "검은 여백 확인")
        self.assertEqual(frame_analysis_stage_text("ssim", 2, 9), "축소 후보 선별 (2/9)")
        self.assertEqual(frame_analysis_stage_text("vmaf", 1, 3), "VMAF 정밀 확인 (1/3)")
        self.assertEqual(frame_analysis_stage_text("unknown", 1, 3), "")

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

    def test_crop_only_body_uses_the_crop_cache_and_reports_the_mode(self) -> None:
        """워커 본문이 감지 전용 여부를 서비스와 종료 이벤트에 그대로 넘기는지 검증"""
        events: list[object] = []
        service = _FakeService(cached_count=1)
        run_frame_analysis_body(
            cast("FrameAnalysisService", service),
            [],
            threading.Event(),
            events.append,
            crop_only=True,
        )
        self.assertTrue(service.count_crop_only)
        self.assertTrue(service.crop_only)
        finished = events[-1]
        assert isinstance(finished, FrameAnalysisFinished)
        self.assertTrue(finished.crop_only)

    def test_progress_events_carry_each_finished_result(self) -> None:
        """워커 본문이 완료된 영상마다 그 영상의 결과를 담아 넘기는지 검증"""
        events: list[object] = []
        run_frame_analysis_body(
            cast("FrameAnalysisService", _FakeService()), [], threading.Event(), events.append
        )
        progress = [event for event in events if isinstance(event, FrameAnalysisProgress)]
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0].filename, "a.mp4")
        self.assertIsInstance(progress[0].result, FrameAnalysisResult)

    def test_worker_emits_progress_and_completed_signals(self) -> None:
        worker = FrameAnalysisWorker(cast("FrameAnalysisService", _FakeService()), [], threading.Event())
        progress: list[tuple[int, int, str]] = []
        stages: list[tuple[str, int, int]] = []
        completed: list[object] = []
        worker.progress.connect(lambda c, t, n: progress.append((c, t, n)))
        worker.stage_progress.connect(lambda _c, _t, _n, stage, done, total: stages.append((stage, done, total)))
        worker.completed.connect(completed.append)
        worker.run()
        self.assertTrue(progress)
        self.assertEqual(stages, [("ssim", 2, 9)])
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
        self.assertEqual(self.shown_modes, [False])
        self.assertEqual(set(self.view.table_model.results), {"a.mp4"})

    def test_crop_and_quality_column_modes_are_tracked_separately(self) -> None:
        """감지 전용은 활성 화면 열만, 화질 분석은 두 열을 각각 한 번씩 표시하는지 검증"""
        controller = self.make_controller()
        controller._results = {"a.mp4": _result()}
        controller._apply_results(crop_only=True)
        controller._apply_results(crop_only=True)
        controller._apply_results(crop_only=False)
        self.assertEqual(self.shown_modes, [True, False])

    def test_toggle_manual_vr_updates_store_notice_and_results(self) -> None:
        controller = self.make_controller()
        controller._results = {"a.mp4": _result()}
        controller.toggle_manual_vr([self.ev], True)
        self.assertEqual(self.store.load_vr_overrides([os.path.join(self.root, "a.mp4")]), frozenset({os.path.join(self.root, "a.mp4")}))
        self.assertEqual(controller._results, {})
        self.assertIn("보호를 켰습니다", self.notifications[-1])
        controller.toggle_manual_vr([self.ev], False)
        self.assertIn("보호를 껐습니다", self.notifications[-1])

    def test_toggle_crop_exclusion_updates_store_model_and_notice(self) -> None:
        """잘라내기 제외 전환이 저장, 표 체크, 캐시 보존, 안내와 재평가를 함께 처리하는지 검증"""
        controller = self.make_controller()
        controller._results = {"a.mp4": _result()}
        path = os.path.join(self.root, "a.mp4")
        controller.toggle_crop_exclusion([self.ev], True)
        self.assertEqual(self.store.load_crop_exclusions([path]), frozenset({path}))
        self.assertEqual(self.view.table_model.crop_excluded, frozenset({"a.mp4"}))
        self.assertEqual(set(controller._results), {"a.mp4"})
        self.assertEqual(self.reevaluated, 1)
        self.assertEqual(
            self.notifications[-1],
            "검은 여백 잘라내기 제외를 켰습니다. 이 영상은 인코딩할 때 검은 여백을 잘라내지 않습니다.",
        )
        controller.toggle_crop_exclusion([self.ev], False)
        self.assertEqual(self.store.load_crop_exclusions([path]), frozenset())
        self.assertEqual(self.view.table_model.crop_excluded, frozenset())
        self.assertEqual(self.reevaluated, 2)
        self.assertEqual(
            self.notifications[-1],
            "검은 여백 잘라내기 제외를 껐습니다. 이 영상은 검은 여백이 감지되면 자동으로 잘라냅니다.",
        )

    def test_reload_cached_applies_crop_exclusions(self) -> None:
        """캐시 다시 읽기에서 저장된 잘라내기 제외가 표 체크 상태로 반영되는지 검증"""
        self.store.set_crop_exclusion(os.path.join(self.root, "a.mp4"), True)
        controller = self.make_controller()
        controller.reload_cached()
        self.assertEqual(self.view.table_model.crop_excluded, frozenset({"a.mp4"}))

    def test_set_manual_active_frame_saves_refreshes_and_clears(self) -> None:
        """사용자 지정 활성 화면이 저장, 표 반영, 재평가, 안내까지 처리되고 해제로 되돌아가는지 검증"""
        controller = self.make_controller()
        controller._results = {"a.mp4": _result()}
        manual = ActiveFrame(1280, 720, 0, 180)
        controller.set_manual_active_frame([self.ev], manual)
        path = os.path.join(self.root, "a.mp4")
        self.assertEqual(self.store.load_manual_active_frames({path: (1920, 1080)}), {path: manual})
        self.assertEqual(self.view.table_model.manual_frames, {"a.mp4": manual})
        self.assertEqual(set(controller._results), {"a.mp4"})
        self.assertEqual(self.reevaluated, 1)
        self.assertEqual(
            self.notifications[-1],
            "사용자 지정 활성 화면을 저장했습니다. 이 영상은 인코딩할 때 지정한 영역을 잘라냅니다.",
        )
        controller.set_manual_active_frame([self.ev], None)
        self.assertEqual(self.store.load_manual_active_frames({path: (1920, 1080)}), {})
        self.assertEqual(self.view.table_model.manual_frames, {})
        self.assertEqual(self.reevaluated, 2)
        self.assertEqual(
            self.notifications[-1],
            "사용자 지정 활성 화면을 지웠습니다. 이 영상은 자동으로 감지한 활성 화면을 씁니다.",
        )

    def test_set_manual_active_frame_returns_early_without_targets(self) -> None:
        """대상이 없으면 저장하지 않음"""
        controller = self.make_controller()
        controller.set_manual_active_frame([], ActiveFrame(1280, 720))
        self.assertEqual(self.reevaluated, 0)
        self.assertEqual(self.store.count_manual_active_frames(), 0)

    def test_reload_cached_applies_manual_active_frames(self) -> None:
        """캐시 다시 읽기에서 저장된 사용자 지정 활성 화면이 표에 반영되는지 검증"""
        manual = ActiveFrame(1280, 720, 0, 180)
        self.store.set_manual_active_frame(
            os.path.join(self.root, "a.mp4"), manual, frame_width=1920, frame_height=1080
        )
        controller = self.make_controller()
        controller.reload_cached()
        self.assertEqual(self.view.table_model.manual_frames, {"a.mp4": manual})

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
        self.assertIn("임시 작업 폴더", self.footer.tooltip)
        self.assertTrue(self.menu.calls[-1]["running"])
        controller._on_stage(0, 0, "", "scan", 1, 9)
        controller._stage_buffer.flush()
        self.assertEqual(self.footer.progress, (0, 1, "a.mp4"))
        self.assertEqual(self.footer.detail, "검은 여백 확인")
        controller._on_stage(0, 1, "a.mp4", "vmaf", 3, 9)
        controller._stage_buffer.flush()
        self.assertEqual(self.footer.detail, "VMAF 정밀 확인 (3/9)")
        controller._on_progress(1, 1, "a.mp4")
        self.assertEqual(self.footer.progress, (1, 1, "a.mp4"))
        controller._on_completed(FrameAnalysisFinished({"a.mp4": _result()}))
        self.assertFalse(controller.is_running)
        self.assertEqual(self.footer.cleared, 1)
        self.assertEqual(self.reevaluated, 1)
        self.assertFalse(self.menu.calls[-1]["running"])

    def test_stage_progress_keeps_current_stage_filename(self) -> None:
        """완료 수 이벤트의 직전 영상 이름 대신 세부 단계가 알린 현재 영상 이름을 표시하는지 검증"""
        controller = self.make_controller()
        controller._on_progress(1, 3, "a.mp4")
        controller._on_stage(0, 0, "b.mp4", "ssim", 1, 9)
        controller._stage_buffer.flush()
        self.assertEqual(self.footer.progress, (1, 3, "b.mp4"))
        self.assertEqual(self.footer.detail, "축소 후보 선별 (1/9)")

    def test_detect_crop_selected_reports_crop_progress_and_completion(self) -> None:
        """검은 여백 감지 단독 실행이 전용 진행 문구와 완료 문구, 활성 화면 열만 쓰는지 검증"""
        controller = self.make_controller()
        self.view.selected = [self.ev]
        capability = FfmpegCapability(
            executable="ffmpeg", version_text="", version=None, filters=frozenset({"libvmaf"}), usable=True
        )
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability", return_value=capability
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            controller.detect_crop_selected()
        self.assertTrue(controller.is_running)
        self.assertTrue(self.footer.crop_only)
        self.assertTrue(self.menu.calls[-1]["crop_running"])
        controller._on_stage(0, 0, "", "scan", 1, 9)
        controller._stage_buffer.flush()
        self.assertEqual(self.footer.detail, "")
        self.assertTrue(self.footer.crop_only)
        controller._on_completed(
            FrameAnalysisFinished({"a.mp4": _result()}, total=1, crop_only=True)
        )
        self.assertFalse(controller.is_running)
        self.assertEqual(self.notifications[-1], "검은 여백 감지 완료: 1개")
        self.assertEqual(self.shown_modes, [True])
        self.assertFalse(self.menu.calls[-1]["crop_running"])
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability", return_value=capability
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            controller.detect_crop_all()
        self.assertTrue(controller.is_running)
        self.assertTrue(controller._crop_scan)
        controller._on_completed(FrameAnalysisFinished({}, cancelled=True, crop_only=True))

    def test_crop_completion_mentions_cache_reuse(self) -> None:
        controller = self.make_controller()
        controller._start_root = self.root
        controller._on_completed(
            FrameAnalysisFinished(
                {"a.mp4": _result()}, total=1, cached_count=1, crop_only=True
            )
        )
        self.assertEqual(
            self.notifications[-1], "검은 여백 감지 완료: 1개 (캐시 재사용 1개)"
        )

    def test_crop_progress_hides_remaining_time_below_three_videos(self) -> None:
        """감지 전용 실행은 영상이 2개 이하일 때 남은 시간을 붙이지 않는지 검증"""
        controller = self.make_controller()
        controller._crop_scan = True
        controller._estimator = mock.Mock()
        controller._estimator.estimate.return_value = mock.Mock(remaining_sec=5.0)
        controller._on_progress(1, 2, "a.mp4")
        self.assertEqual(self.footer.remaining, "")
        controller._on_progress(1, 3, "a.mp4")
        self.assertTrue(self.footer.remaining)

    def test_crop_start_blocked_by_ffmpeg_uses_the_crop_reason(self) -> None:
        controller = self.make_controller()
        self.view.selected = [self.ev]
        failed = FfmpegCapability(executable="ffmpeg", version_text="", version=None, usable=False, reason="없음")
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability", return_value=failed
        ):
            controller.detect_crop_selected()
        self.assertIn("검은 여백 감지를 시작할 수 없습니다", self.notifications[-1])

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
        controller._pending_results = {"a.mp4": _result()}
        controller._flush_timer.start()
        controller.shutdown()
        self.assertEqual((worker.cancelled, worker.waited), (2, 1))
        # - 닫힌 창의 표로 결과를 밀어 넣지 않도록 묶음 타이머와 대기 결과를 비움
        self.assertFalse(controller._flush_timer.isActive())
        self.assertEqual(controller._pending_results, {})
        self.assertEqual(self.view.table_model.partial_calls, [])

    def test_shutdown_without_worker_still_stops_the_flush_timer(self) -> None:
        """실행 중인 워커가 없어도 종료 시 묶음 타이머를 멈추는지 검증"""
        controller = self.make_controller()
        controller._pending_results = {"a.mp4": _result()}
        controller._flush_timer.start()
        controller.shutdown()
        self.assertFalse(controller._flush_timer.isActive())
        self.assertEqual(controller._pending_results, {})


class LiveRefreshTests(_ControllerCase):
    """화질 분석 결과가 도착하는 대로 짧은 묶음으로 표에 반영되는지 검증"""

    def _start_with_fake_worker(self, *, crop_only: bool = False) -> tuple[FrameAnalysisController, _FakeWorker]:
        controller = self.make_controller()
        self.view.selected = [self.ev]
        capability = FfmpegCapability(
            executable="ffmpeg", version_text="", version=None, filters=frozenset({"libvmaf"}), usable=True
        )
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability",
            return_value=capability,
        ), mock.patch(
            "src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker
        ):
            if crop_only:
                controller.detect_crop_selected()
            else:
                controller.analyze_selected()
        return controller, cast("_FakeWorker", controller._worker)

    def test_progress_events_are_flushed_once_per_debounce_window(self) -> None:
        """묶음 구간 안에 도착한 5건이 한 번의 부분 갱신으로 표에 반영되고, 완료에서 전체가 적용되는지 검증"""
        controller, worker = self._start_with_fake_worker()
        for index in range(5):
            worker.progress.emit(index + 1, 5, f"v{index}.mp4", _result())
        self.assertEqual(self.view.table_model.partial_calls, [])
        self.assertTrue(controller._flush_timer.isActive())
        self.assertEqual(controller._flush_timer.interval(), FRAME_ANALYSIS_FLUSH_MS)
        controller._flush_timer.timeout.emit()
        self.assertEqual(len(self.view.table_model.partial_calls), 1)
        self.assertEqual(len(self.view.table_model.partial_calls[0]), 5)
        self.assertFalse(controller._flush_timer.isActive())
        controller._on_completed(FrameAnalysisFinished({"a.mp4": _result()}, total=1))
        self.assertEqual(set(self.view.table_model.results), {"a.mp4"})

    def test_cancel_flushes_arrived_rows_and_stops_the_timer(self) -> None:
        """취소로 끝나도 도착한 결과를 남기고 묶음 타이머를 멈추는지 검증"""
        controller, worker = self._start_with_fake_worker()
        worker.progress.emit(1, 3, "a.mp4", _result())
        self.assertTrue(controller._flush_timer.isActive())
        controller._on_completed(FrameAnalysisFinished({"a.mp4": _result()}, cancelled=True))
        self.assertFalse(controller._flush_timer.isActive())
        self.assertEqual(set(self.view.table_model.results), {"a.mp4"})
        self.assertEqual(self.notifications[-1], "작업이 중단되었습니다.")

    def test_flushed_results_are_logged_per_video(self) -> None:
        """플러시된 영상마다 화질 분석 결과 줄을 남기고 표 문구를 그대로 쓰는지 검증"""
        controller, worker = self._start_with_fake_worker()
        with self.assertLogs("src.gui.controllers.frame_analysis_controller", level="INFO") as logs:
            worker.progress.emit(1, 1, "a.mp4", _result())
            controller._flush_timer.timeout.emit()
        self.assertEqual(logs.output, ["INFO:src.gui.controllers.frame_analysis_controller:화질 분석 결과: a.mp4, -, 720p, VMAF"])

    def test_crop_scan_results_are_logged_as_crop_scan_including_the_final_flush(self) -> None:
        """여백 감지의 결과 줄이 타이머 플러시와 완료 시 마지막 플러시 모두 여백 감지 이름으로 남는지 검증"""
        controller, worker = self._start_with_fake_worker(crop_only=True)
        with self.assertLogs("src.gui.controllers.frame_analysis_controller", level="INFO") as logs:
            worker.progress.emit(1, 2, "a.mp4", _result())
            controller._flush_timer.timeout.emit()
            worker.progress.emit(2, 2, "b.mp4", _result())
            controller._on_completed(FrameAnalysisFinished({"b.mp4": _result()}, crop_only=True, total=2))
        prefix = "INFO:src.gui.controllers.frame_analysis_controller:검은 여백 감지 결과: "
        self.assertEqual(len(logs.output), 2)
        self.assertTrue(all(line.startswith(prefix) for line in logs.output))

    def test_error_completion_keeps_rows_but_logs_no_result_lines(self) -> None:
        """오류로 끝난 실행은 도착분을 표에만 남기고 영상별 결과 줄은 기록하지 않는지 검증"""
        controller, worker = self._start_with_fake_worker()
        worker.progress.emit(1, 2, "a.mp4", _result())
        with self.assertNoLogs(
            "src.gui.controllers.frame_analysis_controller", level="INFO"
        ):
            controller._on_completed(FrameAnalysisFinished({}, error="boom"))
        self.assertEqual(set(self.view.table_model.results), {"a.mp4"})
        self.assertFalse(controller._flush_timer.isActive())

    def test_stage_events_are_flushed_once_per_window(self) -> None:
        """영상 하나의 단계 이벤트 여러 건이 한 번의 하단 표시 갱신으로 묶이고 마지막 단계가 남는지 검증"""
        controller, _worker = self._start_with_fake_worker()
        calls: list[str] = []
        original = self.footer.set_frame_analysis_progress

        def _record(*args: object, **kwargs: object) -> None:
            calls.append(str(kwargs.get("detail", "")))
            original(*args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(self.footer, "set_frame_analysis_progress", side_effect=_record):
            for done in range(1, 10):
                controller._on_stage(0, 0, "a.mp4", "vmaf", done, 9)
            self.assertEqual(calls, [])
            self.assertTrue(controller._stage_buffer.is_bundling())
            self.assertEqual(controller._stage_buffer.interval_ms, FRAME_ANALYSIS_FLUSH_MS)
            controller._stage_buffer.flush()
        self.assertEqual(calls, ["VMAF 정밀 확인 (9/9)"])
        self.assertFalse(controller._stage_buffer.is_bundling())

    def test_new_progress_drops_the_bundled_stage_update(self) -> None:
        """묶여 있던 단계 표시보다 새 진행 값이 먼저 오면 오래된 단계 표시를 버리는지 검증"""
        controller, _worker = self._start_with_fake_worker()
        controller._on_stage(0, 0, "a.mp4", "vmaf", 1, 9)
        self.assertTrue(controller._stage_buffer.is_bundling())
        controller._on_progress(1, 1, "a.mp4")
        self.assertFalse(controller._stage_buffer.is_bundling())
        controller._stage_buffer.flush()
        self.assertEqual(self.footer.progress, (1, 1, "a.mp4"))
        self.assertEqual(self.footer.detail, "")

    def test_completion_drops_the_bundled_stage_update(self) -> None:
        """완료로 끝나면 묶여 있던 단계 표시를 버리고 지운 진행 문구를 되살리지 않는지 검증"""
        controller, _worker = self._start_with_fake_worker()
        controller._on_stage(0, 0, "a.mp4", "scan", 1, 9)
        self.assertTrue(controller._stage_buffer.is_bundling())
        controller._on_completed(FrameAnalysisFinished({"a.mp4": _result()}, total=1, crop_only=True))
        self.assertFalse(controller._stage_buffer.is_bundling())
        self.assertEqual(self.footer.cleared, 1)
        self.assertEqual(self.footer.progress, (0, 1, "a.mp4"))
        self.assertEqual(self.footer.detail, "")

    def test_volume_guard_keeps_a_large_flush_cheap(self) -> None:
        """영상 결과 3000건을 한 번에 반영해도 기록과 표 갱신이 1초 미만인지 검증"""
        controller = self.make_controller()
        self._start_with_fake_worker()
        results = {f"v{index}.mp4": _result() for index in range(3000)}
        with self.assertLogs(
            "src.gui.controllers.frame_analysis_controller", level="INFO"
        ) as logs:
            started = time.perf_counter()
            controller._pending_results = dict(results)
            controller._flush_on_stop()
            self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual(len(logs.output), 3000)
        self.assertEqual(set(self.view.table_model.results), set(results))


class _FakeSplitAnalyze:
    def __init__(self) -> None:
        self.estimate_provider: Callable[[str], str] | None = None

    def set_estimate_provider(self, provider: Callable[[str], str]) -> None:
        self.estimate_provider = provider


class _FakeHeader:
    def __init__(self) -> None:
        self.analyze_frame_clicked = _Signal()
        self.frame_min_tier_changed = _Signal()
        self.split_analyze = _FakeSplitAnalyze()
        self.min_tier: str = ""
        self.saved_tier: str = ""

    def set_min_tier_provider(self, provider: Callable[[], str]) -> None:
        self.min_tier = provider()

    def set_frame_min_tier(self, tier_key: str) -> None:
        self.saved_tier = tier_key


class _FakeWindow:
    def __init__(self, case: _ControllerCase) -> None:
        self.table_view = case.view
        self.footer_bar = case.footer
        self.menu = case.menu
        self.manual_targets = mock.Mock()
        self.column_history = _FakeHistory()
        self.header_bar = _FakeHeader()
        self.current_report: SummaryReport | None = case.report
        self.classifier: object | None = mock.Mock(config=ClassifierConfig())
        self.statuses: list[str] = []
        self.started: list[tuple[bool, bool]] = []
        self.preset_syncs: int = 0

    def sync_preset(self) -> None:
        self.preset_syncs += 1

    def set_status_text(self, text: str, tooltip: str = "") -> None:
        self.statuses.append(text)

    def start_analyze(self, include_keyframe: bool = True, force_reanalyze: bool = False) -> None:
        self.started.append((include_keyframe, force_reanalyze))


class AttachTests(_ControllerCase):
    """메인 창에 붙일 때 메뉴, 하단 취소, 우클릭 요청을 연결하고 열 표시를 실행하는지 검증"""

    def test_attach_wires_actions_and_shows_columns(self) -> None:
        window = _FakeWindow(self)
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.open_default_store",
            return_value=mock.Mock(db_path=os.path.join(self.root, "video_cache.sqlite3")),
        ):
            controller = attach_frame_analysis(cast("FrameAnalysisWindow", window), self._re_evaluate, lambda: False)
        self.assertEqual(
            set(self.view.table_model.handlers),
            {"apply_candidates", "toggle_vr", "toggle_crop_exclusion", "set_manual_active_frame"},
        )
        self.view.hidden = {19, 20}
        controller._results = {"a.mp4": _result()}
        controller._apply_results()
        self.assertFalse(self.view.hidden)
        self.assertEqual(window.preset_syncs, 1)
        self.view.hidden = {19, 20}
        controller._apply_results(crop_only=True)
        self.assertEqual(self.view.hidden, {20})
        self.assertEqual(window.preset_syncs, 2)
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
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability", return_value=capability
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            window.menu.act_crop_all.triggered.emit()
        self.assertTrue(controller.is_running)
        self.assertTrue(controller._crop_scan)
        controller._on_completed(FrameAnalysisFinished({}, cancelled=True, crop_only=True))
        controller.shutdown()


class FrameAnalysisStartRulesTests(_ControllerCase):
    """전체 화질 분석의 자동 건너뛰기와 대상 해상도 필터 검증"""

    def test_skip_rules_cover_exception_encoded_hdr_manual_and_interlaced(self) -> None:
        """예외 규칙, 이미 인코딩, HDR, 인터레이스, 직접 지정 영상은 자동 대상에서 빠짐"""
        for action in (
            SmartAction.PASS_EXCEPTION,
            SmartAction.PASS_ALREADY_ENCODED,
            SmartAction.PASS_HDR,
        ):
            with self.subTest(action=action):
                self.assertTrue(_skip_for_frame_analysis(make_eval(smart_action=action)))
        self.assertTrue(_skip_for_frame_analysis(make_eval(manual_target="keep")))
        self.assertTrue(_skip_for_frame_analysis(make_eval(manual_target="720p")))
        self.assertFalse(_skip_for_frame_analysis(make_eval()))
        interlaced = make_eval(
            video=make_video(
                stream_summary=(
                    '[{"kind":"video","codec":"h264","attached_pic":false,'
                    '"field_order":"tt"}]'
                )
            )
        )
        self.assertTrue(_skip_for_frame_analysis(interlaced))
        self.assertTrue(_skip_for_crop_scan(interlaced))

    def test_crop_scan_skip_rules_keep_manual_target_videos(self) -> None:
        """검은 여백 감지는 직접 지정 영상을 건너뛰지 않고 보존 대상과 인터레이스만 제외"""
        manual = make_eval(manual_target="720p")
        self.assertFalse(_skip_for_crop_scan(manual))
        self.assertFalse(_skip_for_crop_scan(make_eval(manual_target="keep")))
        self.assertTrue(_skip_for_crop_scan(make_eval(smart_action=SmartAction.PASS_HDR)))
        self.assertFalse(_skip_for_crop_scan(make_eval()))

    def test_min_tier_filter_uses_source_tier(self) -> None:
        """원본 규격이 대상 해상도 미만인지 판정하고 전체와 모르는 값은 걸러내지 않음"""
        self.assertTrue(_is_below_frame_min_tier(1280, 720, "1080p"))
        self.assertFalse(_is_below_frame_min_tier(1920, 1080, "1080p"))
        self.assertFalse(_is_below_frame_min_tier(1280, 720, FRAME_ANALYSIS_MIN_TIER_ALL))
        self.assertFalse(_is_below_frame_min_tier(1280, 720, "unknown"))

    def _capability(self) -> FfmpegCapability:
        return FfmpegCapability(
            executable="ffmpeg", version_text="", version=None,
            filters=frozenset({"libvmaf"}), usable=True,
        )

    def test_analyze_all_skips_excluded_and_marks_low_resolution_crop_only(self) -> None:
        """전체 분석은 건너뛸 영상을 빼고, 대상 해상도 미만 영상은 검은 여백만 감지로 넘김"""
        low = make_eval(video=make_video(filename="b.mp4", width=1280, height=720))
        self._write_video(self.root, "b.mp4")
        excluded = make_eval(
            video=make_video(filename="c.mp4"), smart_action=SmartAction.PASS_EXCEPTION
        )
        self._write_video(self.root, "c.mp4")
        report = make_report([self.ev, low, excluded], root_dir=self.root)
        controller = self.make_controller(report=report)
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability",
            return_value=self._capability(),
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            controller.analyze_all()
        self.assertEqual([request.filename for request in controller._requests], ["a.mp4", "b.mp4"])
        worker = cast("_FakeWorker", controller._worker)
        self.assertEqual(worker.kwargs["crop_only_names"], frozenset({"b.mp4"}))
        controller._on_completed(FrameAnalysisFinished({}, cancelled=True))

    def test_manual_analysis_ignores_skip_rules(self) -> None:
        """수동 지정 분석은 건너뛰기 규칙과 대상 해상도 필터를 적용하지 않음"""
        excluded = make_eval(
            video=make_video(filename="c.mp4"), smart_action=SmartAction.PASS_ALREADY_ENCODED
        )
        self._write_video(self.root, "c.mp4")
        controller = self.make_controller()
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability",
            return_value=self._capability(),
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            controller.analyze_evaluations([excluded], crop_only=False)
        self.assertEqual([request.filename for request in controller._requests], ["c.mp4"])
        worker = cast("_FakeWorker", controller._worker)
        self.assertEqual(worker.kwargs["crop_only_names"], frozenset())
        controller._on_completed(FrameAnalysisFinished({}, cancelled=True))

    def test_all_targets_skipped_announces_nothing_to_measure(self) -> None:
        """대상이 모두 빠지면 측정할 영상이 없다는 안내를 남김"""
        excluded = make_eval(
            video=make_video(filename="c.mp4"), smart_action=SmartAction.PASS_HDR
        )
        self._write_video(self.root, "c.mp4")
        report = make_report([excluded], root_dir=self.root)
        controller = self.make_controller(report=report)
        controller.analyze_all()
        self.assertEqual(
            self.notifications[-1],
            "화질 분석 대상이 없습니다. "
            "예외 규칙, 이미 인코딩, HDR, 인터레이스, 직접 지정 영상은 건너뜁니다.",
        )

    def test_detect_crop_all_keeps_manual_target_videos(self) -> None:
        """전체 검은 여백 감지는 직접 지정 영상도 대상에 넣는지 검증"""
        manual = make_eval(video=make_video(filename="b.mp4"), manual_target="720p")
        self._write_video(self.root, "b.mp4")
        report = make_report([self.ev, manual], root_dir=self.root)
        controller = self.make_controller(report=report)
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability",
            return_value=self._capability(),
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            controller.detect_crop_all()
        self.assertEqual(
            [request.filename for request in controller._requests], ["a.mp4", "b.mp4"]
        )
        self.assertTrue(controller._crop_scan)
        controller._on_completed(FrameAnalysisFinished({}, cancelled=True, crop_only=True))

    def test_run_frame_mode_chains_precise_analysis_when_keyframes_are_missing(self) -> None:
        """키프레임이 없으면 정밀 분석을 먼저 시작하고, 완료 뒤 전체 화질 분석을 잇는지 검증"""
        ev = make_eval(video=make_video(filename="a.mp4", keyframe_interval=None))
        controller = self.make_controller(report=make_report([ev], root_dir=self.root))
        controller.run_frame_mode()
        self.assertEqual(self.started, [(True, False)])
        self.assertFalse(controller.is_running)
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability",
            return_value=self._capability(),
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            controller.on_analysis_finished()
        self.assertTrue(controller.is_running)
        self.assertEqual([request.filename for request in controller._requests], ["a.mp4"])
        controller._on_completed(FrameAnalysisFinished({}, cancelled=True))

    def test_run_frame_mode_analyzes_immediately_when_keyframes_exist(self) -> None:
        controller = self.make_controller()
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability",
            return_value=self._capability(),
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            controller.run_frame_mode()
        self.assertEqual(self.started, [])
        self.assertTrue(controller.is_running)
        controller._on_completed(FrameAnalysisFinished({}, cancelled=True))

    def test_run_full_analysis_chains_crop_detection_only_when_enabled(self) -> None:
        """정밀 분석 뒤 검은 여백 감지 연속 실행이 설정을 따르는지 검증"""
        enabled = ClassifierConfig()
        enabled.encode.crop_black_bars = True
        controller = self.make_controller(config=enabled)
        controller.run_full_analysis()
        self.assertEqual(self.started, [(True, False)])
        with mock.patch(
            "src.gui.controllers.frame_analysis_controller.probe_ffmpeg_capability",
            return_value=self._capability(),
        ), mock.patch("src.gui.controllers.frame_analysis_controller.FrameAnalysisWorker", _FakeWorker):
            controller.on_analysis_finished()
        self.assertTrue(controller.is_running)
        self.assertTrue(controller._crop_scan)
        controller._on_completed(FrameAnalysisFinished({}, cancelled=True, crop_only=True))

        disabled = ClassifierConfig()
        disabled.encode.crop_black_bars = False
        controller = self.make_controller(config=disabled)
        controller.run_full_analysis()
        controller.on_analysis_finished()
        self.assertFalse(controller.is_running)

    def test_run_mode_dispatches_by_header_mode(self) -> None:
        """상단 분할 버튼 모드별로 화질 분석, 정밀 분석, 빠른 분석을 고르는지 검증"""
        controller = self.make_controller()
        controller.run_mode("fast")
        self.assertEqual(self.started, [(False, False)])
        controller = self.make_controller()
        controller.run_full_analysis = mock.Mock()
        controller.run_mode("full")
        controller.run_full_analysis.assert_called_once_with(force_reanalyze=False)
        controller = self.make_controller()
        controller.run_frame_mode = mock.Mock()
        controller.run_mode("frame")
        controller.run_frame_mode.assert_called_once_with(force_reanalyze=False)

    def test_cancel_clears_the_pending_follow_up_stage(self) -> None:
        """정밀 분석이 중단되면 예약된 후속 단계가 실행되지 않는지 검증"""
        ev = make_eval(video=make_video(filename="a.mp4", keyframe_interval=None))
        controller = self.make_controller(report=make_report([ev], root_dir=self.root))
        controller.run_frame_mode()
        self.assertEqual(self.started, [(True, False)])
        controller.clear_pending_followup()
        controller.on_analysis_finished()
        self.assertFalse(controller.is_running)
        self.assertEqual(self.notifications, [])

    def test_estimates_count_frame_targets_and_delegate_other_modes(self) -> None:
        """화질 분석 예상은 대상 수를, 나머지 모드는 분류 세션 예상값을 쓰는지 검증"""
        low = make_eval(video=make_video(filename="b.mp4", width=1280, height=720))
        self._write_video(self.root, "b.mp4")
        controller = self.make_controller(
            report=make_report([self.ev, low], root_dir=self.root)
        )
        self.assertEqual(controller.frame_estimate_text(), "대상 1개")
        self.assertEqual(controller.analysis_estimate_text("frame"), "대상 1개")
        self.assertEqual(controller.analysis_estimate_text("fast"), "")


if __name__ == "__main__":
    unittest.main()

