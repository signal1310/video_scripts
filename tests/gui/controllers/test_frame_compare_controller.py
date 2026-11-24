"""화질 비교 컨트롤러 검증: 세션 계획 구성, 추출 위임, 미리 보기 라우팅, 종료 정리, 진입점 연결

- 프로빙은 가짜로 바꾸고, 캐시 저장소와 재인코딩 이력은 임시 DB를 씀
- 창은 미리 보기 컨트롤러가 하나만 가지므로 이 컨트롤러는 미리 보기 대역으로 라우팅만 확인
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

if TYPE_CHECKING:
    from src.gui.controllers.frame_compare_controller import FrameCompareWindowHost
    from src.gui.preview.preview_controller import PreviewController

from src.core.analysis.frame_analysis import CROP_SCAN_VERSION, FRAME_ANALYSIS_VERSION, TierScore
from src.core.analysis.frame_compare import FrameSource
from src.core.config import ClassifierConfig
from src.core.encode.encode_gate import GatePurpose
from src.core.encode.encode_state import EncodeState
from src.core.models import ActiveFrame, SummaryReport, VideoEvaluation
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.core.workspace_paths import resolve_source_filepath
from src.gui.controllers import frame_compare_controller as module
from src.gui.controllers.frame_compare_controller import (
    FrameCompareController,
    attach_frame_compare,
    build_compare_service,
)
from src.gui.table.video_table_view import VideoTableView
from src.service.encode.encode_history import EncodeHistory, NewItem
from src.service.encode.encode_job import EncodeJob, job_to_json
from src.service.frame_analysis_service import FrameAnalysisRecord, FrameAnalysisStore
from src.service.frame_compare_service import (
    ExtractedFrames,
    FrameCompareExecutionError,
    FrameCompareFileMissing,
    FrameCompareService,
    FrameSourceInfo,
)
from src.service.session import ClassifierSession
from tests._factories import make_eval, make_report, make_video
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


class _ControllerCase(GuiTestCase):
    """임시 파일, 저장소, 이력과 가짜 프로빙을 준비하는 기반"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = os.path.join(self.tmp.name, "video.mp4")
        self.archive = os.path.join(self.tmp.name, "video_원본보관.mp4")
        self.output = os.path.join(self.tmp.name, "video_out.mp4")
        for path in (self.source, self.archive, self.output):
            with open(path, "wb") as handle:
                handle.write(b"x")
        self.db_path = os.path.join(self.tmp.name, "cache.sqlite3")
        self.store = FrameAnalysisStore(self.db_path)
        self.addCleanup(self.store.close)
        self.history = EncodeHistory(self.db_path, sleep=lambda _s: None)
        self.addCleanup(self.history.close)
        self.service = build_compare_service(ClassifierConfig(), self.history)
        self.controller = FrameCompareController(self.service, self.store, history=self.history)
        self.addCleanup(self.controller.shutdown)

    def info(self, path: str, *, width: int = 3840, height: int = 2160) -> FrameSourceInfo:
        stat = os.stat(path)
        return FrameSourceInfo(
            path=path,
            filename=os.path.basename(path),
            width=width,
            height=height,
            duration_sec=100.0,
            fps=30.0,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )

    def plan_json(self, crop_filter: str | None = None) -> str:
        """작업 기록에 저장하는 계획 입력 JSON, 잘라내기 확정 영상이면 crop 필터를 담음"""
        return job_to_json(
            EncodeJob(
                filename="video.mp4",
                root_dir=self.tmp.name,
                source_path=self.source,
                source_size=1,
                source_mtime_ns=1,
                source_duration_sec=100.0,
                source_ext="mp4",
                fps=30.0,
                quality=22.0,
                purpose=GatePurpose.COMPAT,
                output_size=None,
                scale_filter=None,
                maxrate_kbps=None,
                crop_filter=crop_filter,
            )
        )

    def save_record(
        self,
        path: str | None = None,
        filename: str | None = None,
        candidate: str | None = "1080p",
        analysis_version: int = FRAME_ANALYSIS_VERSION,
    ) -> FrameAnalysisRecord:
        info = self.info(self.source if path is None else path)
        record = FrameAnalysisRecord(
            filename=info.filename if filename is None else filename,
            filesize_bytes=info.size_bytes,
            mtime_ns=info.mtime_ns,
            active_width=3840,
            active_height=2160,
            active_x=0,
            active_y=0,
            has_black_bars=False,
            metric_used="VMAF",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(TierScore(tier="1080p", ssim=0.9983, vmaf=98.7),),
            candidate_tier=candidate,
            valid_segments=3,
            total_segments=3,
            unmeasurable=False,
            is_vr_protected=False,
            measured_at="2026-11-22T00:00:00Z",
            analysis_version=analysis_version,
        )
        self.store.save(record)
        return record


class SessionTests(_ControllerCase):
    """프로빙, 최근 결과, 캐시 점수로 만든 세션"""

    def _probe(self, path: str) -> FrameSourceInfo:
        return self.info(path)

    def test_session_maps_result_candidate_and_scores(self) -> None:
        self.save_record(path=self.archive, filename="video.mp4")
        batch = self.history.create_batch(
            self.tmp.name,
            '{"encoder": "libx264"}',
            [NewItem(source_path=self.source, source_size=1, source_mtime_ns=1, plan_json=self.plan_json())],
        )
        self.history.update_item(
            batch,
            self.source,
            {"state": EncodeState.DONE, "output_path": self.output, "archive_path": self.archive},
        )
        with mock.patch.object(self.service, "probe", side_effect=self._probe):
            session = self.controller.build_session(self.source, 250.0)
        self.assertEqual(session.filename, "video.mp4")
        self.assertEqual((session.width, session.height, session.fps), (3840, 2160, 30.0))
        self.assertEqual(session.position_sec, 100.0)
        self.assertEqual(session.plan.original.path, self.archive)
        self.assertEqual(session.plan.default_key, "result")
        target = session.plan.target("1080p")
        assert target is not None
        self.assertEqual(target.label, "1080p 축소 비교")
        self.assertEqual(session.scores[0].tier, "1080p")
        self.assertEqual(session.plan.targets[0].label, "결과")

    def test_session_carries_the_matching_report_evaluation(self) -> None:
        """비교 세션이 표 판정 결과를 실어 출력 해상도 1클릭 지정에 쓰는지 검증"""
        ev = make_eval(make_video(filename="video.mp4"))
        controller = FrameCompareController(
            self.service,
            self.store,
            history=self.history,
            report_provider=lambda: make_report([ev]),
        )
        self.addCleanup(controller.shutdown)
        with mock.patch.object(self.service, "probe", side_effect=self._probe):
            session = controller.build_session(self.source, 0.0)
        self.assertIs(session.evaluation, ev)

    def test_session_without_a_report_has_no_evaluation(self) -> None:
        with mock.patch.object(self.service, "probe", side_effect=self._probe):
            session = self.controller.build_session(self.source, 0.0)
        self.assertIsNone(session.evaluation)

    def test_cache_from_another_analysis_version_is_ignored(self) -> None:
        """분석 방식이 달라 버전이 어긋난 캐시는 후보와 점수에 쓰지 않는지 검증"""
        self.save_record(analysis_version=FRAME_ANALYSIS_VERSION - 1)
        with mock.patch.object(self.service, "probe", side_effect=self._probe):
            session = self.controller.build_session(self.source, 0.0)
        self.assertEqual(session.scores, ())
        self.assertEqual(session.active_frame, None)
        self.assertEqual(session.plan.default_key, "1440p")

    def test_result_comparison_uses_the_crop_recorded_in_the_encode_plan(self) -> None:
        """결과를 만든 계획의 crop 필터로 원본 프레임을 결과 파일 크기에 맞추는지 검증"""
        for plan_json, expected in (
            (self.plan_json("crop=1216:2160:1312:0"), ActiveFrame(1216, 2160, 1312, 0)),
            (self.plan_json(), None),
        ):
            with self.subTest(crop_filter=expected):
                batch = self.history.create_batch(
                    self.tmp.name,
                    '{"encoder": "libx264"}',
                    [
                        NewItem(
                            source_path=self.source,
                            source_size=1,
                            source_mtime_ns=1,
                            plan_json=plan_json,
                        )
                    ],
                )
                self.history.update_item(
                    batch,
                    self.source,
                    {"state": EncodeState.DONE, "output_path": self.output},
                )
                with mock.patch.object(self.service, "probe", side_effect=self._probe):
                    session = self.controller.build_session(self.source, 0.0)
                self.assertEqual(session.plan.original.crop, expected)
                result = session.plan.target("result")
                assert result is not None
                self.assertIsNone(result.crop)

    def test_probe_errors_become_error_sessions(self) -> None:
        with mock.patch.object(
            self.service, "probe", side_effect=FrameCompareFileMissing("파일 없음")
        ):
            session = self.controller.build_session(self.source, 0.0)
        self.assertEqual(session.plan.reason, "파일 없음")
        self.assertEqual(session.duration_sec, 0.0)
        with mock.patch.object(
            self.service,
            "probe",
            side_effect=FrameCompareExecutionError("영상 정보를 읽지 못했습니다: boom"),
        ):
            session = self.controller.build_session(self.source, 0.0)
        self.assertIn("boom", session.plan.reason)

    def test_result_probe_failure_keeps_downscale_targets_only(self) -> None:
        calls: list[str] = []

        def probe(path: str) -> FrameSourceInfo:
            calls.append(path)
            if path == self.output:
                raise FrameCompareFileMissing("파일 없음")
            return self.info(path)

        with mock.patch.object(self.service, "probe", side_effect=probe), mock.patch.object(
            self.service, "comparable_result", return_value=(self.source, self.output)
        ):
            session = self.controller.build_session(self.source, 3.0)
        self.assertEqual(calls, [self.source, self.output])
        self.assertNotEqual(session.plan.targets[0].key, "result")
        self.assertEqual(session.position_sec, 3.0)


    def test_archive_original_geometry_drives_the_plan(self) -> None:
        """보관 원본을 프로빙해 메타, 축소 비교 대상, 캐시 조회를 그 기준으로 만듦"""
        batch = self.history.create_batch(
            self.tmp.name,
            '{"encoder": "libx264"}',
            [NewItem(source_path=self.source, source_size=1, source_mtime_ns=1, plan_json=self.plan_json())],
        )
        self.history.update_item(
            batch,
            self.source,
            {"state": EncodeState.DONE, "output_path": self.output, "archive_path": self.archive},
        )
        info = self.info(self.archive)
        self.store.save(
            FrameAnalysisRecord(
                filename="video.mp4",
                filesize_bytes=info.size_bytes,
                mtime_ns=info.mtime_ns,
                active_width=3840,
                active_height=2160,
                active_x=0,
                active_y=0,
                has_black_bars=False,
                metric_used="VMAF",
                vmaf_model="vmaf_v0.6.1",
                tier_scores=(TierScore(tier="1080p", ssim=0.9983, vmaf=98.7),),
                candidate_tier="1080p",
                valid_segments=3,
                total_segments=3,
                unmeasurable=False,
                is_vr_protected=False,
                measured_at="2026-11-22T00:00:00Z",
                analysis_version=FRAME_ANALYSIS_VERSION,
            )
        )

        def probe(path: str) -> FrameSourceInfo:
            if path == self.archive:
                return self.info(path, width=3840, height=2160)
            return self.info(path, width=1920, height=1080)

        with mock.patch.object(self.service, "probe", side_effect=probe):
            session = self.controller.build_session(self.source, 0.0)
        self.assertEqual(session.filename, "video.mp4")
        self.assertEqual((session.width, session.height), (3840, 2160))
        self.assertEqual(session.plan.original.path, self.archive)
        self.assertIn("원본 3840x2160", f"원본 {session.plan.original.detail}")
        self.assertIn("1440p", [target.key for target in session.plan.targets])
        self.assertEqual(session.scores[0].tier, "1080p")

    def test_session_uses_crop_confirmed_active_frame(self) -> None:
        """잘라내기 확정 영상의 비교 세션이 활성 화면 크기와 크롭을 축소 비교에 넘기는지 검증"""
        info = self.info(self.source)
        record = FrameAnalysisRecord(
            filename="video.mp4",
            filesize_bytes=info.size_bytes,
            mtime_ns=info.mtime_ns,
            active_width=1600,
            active_height=1000,
            active_x=260,
            active_y=40,
            has_black_bars=True,
            metric_used="VMAF",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(TierScore(tier="720p", ssim=0.9983, vmaf=98.7),),
            candidate_tier="720p",
            valid_segments=3,
            total_segments=3,
            unmeasurable=False,
            is_vr_protected=False,
            measured_at="2026-11-22T00:00:00Z",
            analysis_version=FRAME_ANALYSIS_VERSION,
        )
        self.store.save(record)
        active = ActiveFrame(1600, 1000, 260, 40)
        with mock.patch.object(self.service, "probe", side_effect=self._probe):
            session = self.controller.build_session(self.source, 0.0)
        self.assertEqual((session.width, session.height), (1600, 1000))
        self.assertEqual(session.active_frame, active)
        target = session.plan.target("720p")
        assert target is not None
        self.assertEqual(target.crop, active)

        self.store.set_crop_exclusion(self.source, True)
        with mock.patch.object(self.service, "probe", side_effect=self._probe):
            excluded = self.controller.build_session(self.source, 0.0)
        self.assertIsNone(excluded.active_frame)
        self.assertEqual((excluded.width, excluded.height), (3840, 2160))

        config = ClassifierConfig()
        config.encode.crop_black_bars = False
        controller = FrameCompareController(
            self.service, self.store, config_provider=lambda: config
        )
        self.addCleanup(controller.shutdown)
        self.store.set_crop_exclusion(self.source, False)
        with mock.patch.object(self.service, "probe", side_effect=self._probe):
            disabled = controller.build_session(self.source, 0.0)
        self.assertIsNone(disabled.active_frame)

    def test_crop_only_cache_keeps_the_active_frame_without_scores(self) -> None:
        """검은 여백 감지 전용 행도 비교 창에서 활성 화면 크롭으로 쓰이고 점수는 비우는지 검증"""
        info = self.info(self.source)
        self.store.save(
            FrameAnalysisRecord(
                filename="video.mp4",
                filesize_bytes=info.size_bytes,
                mtime_ns=info.mtime_ns,
                active_width=1216,
                active_height=2160,
                active_x=1312,
                active_y=0,
                has_black_bars=True,
                metric_used="",
                vmaf_model="",
                tier_scores=(),
                candidate_tier=None,
                valid_segments=9,
                total_segments=9,
                unmeasurable=False,
                is_vr_protected=False,
                measured_at="2026-11-23T00:00:00Z",
                analysis_version=CROP_SCAN_VERSION,
            )
        )
        config = ClassifierConfig()
        config.encode.frame_analysis_mode = "precise"
        controller = FrameCompareController(
            self.service, self.store, config_provider=lambda: config
        )
        self.addCleanup(controller.shutdown)
        with mock.patch.object(self.service, "probe", side_effect=self._probe):
            session = controller.build_session(self.source, 0.0)
        self.assertEqual(session.active_frame, ActiveFrame(1216, 2160, 1312, 0))
        self.assertEqual((session.width, session.height), (1216, 2160))
        self.assertEqual(session.scores, ())
        target = session.plan.target("1080p")
        assert target is not None
        self.assertEqual(target.crop, ActiveFrame(1216, 2160, 1312, 0))

    def test_service_factory_rebuilds_on_each_session(self) -> None:
        config = ClassifierConfig()
        config.execution.ffmpeg_path = "old-ffmpeg"
        config.execution.ffprobe_path = "old-ffprobe"

        def factory() -> FrameCompareService:
            return build_compare_service(config, self.history)

        controller = FrameCompareController(
            factory(), self.store, history=self.history, service_factory=factory
        )
        self.addCleanup(controller.shutdown)
        with mock.patch.object(
            FrameCompareService, "probe", lambda service, path: self.info(path)
        ):
            controller.build_session(self.source, 0.0)
            self.assertEqual(controller.service.ffmpeg_executable, "old-ffmpeg")
            config.execution.ffmpeg_path = "new-ffmpeg"
            config.execution.ffprobe_path = "new-ffprobe"
            controller.build_session(self.source, 0.0)
        self.assertEqual(controller.service.ffmpeg_executable, "new-ffmpeg")
        self.assertEqual(controller.service.ffprobe_executable, "new-ffprobe")

    def test_extract_pair_delegates_to_the_current_service(self) -> None:
        """비교 뷰가 요청한 추출을 지금 서비스로 넘김"""
        frames = ExtractedFrames(original_bytes=b"a", compared_bytes=b"b")
        with mock.patch.object(self.service, "extract_pair", return_value=frames) as extract:
            result = self.controller.extract_pair(
                FrameSource(key="원본", path=self.source, label="원본"),
                FrameSource(key="result", path=self.output, label="결과"),
                time_sec=1.5,
                frame_width=3840,
                frame_height=2160,
                cancel_event=threading.Event(),
            )
        self.assertIs(result, frames)
        self.assertEqual(extract.call_args.kwargs["time_sec"], 1.5)

    def test_release_decoders_closes_the_service(self) -> None:
        """비교 뷰를 숨길 때 서비스가 연 메모리 디코더를 닫는지 검증"""
        with mock.patch.object(self.service, "close") as close:
            self.controller.release_decoders()
        close.assert_called_once()

    def test_shutdown_closes_the_history_and_clears_the_row_opener(self) -> None:
        """종료 때 저장소, 이력, 표 우클릭 함수를 모두 정리"""
        history = mock.Mock()
        controller = FrameCompareController(self.service, self.store, history=history)
        with mock.patch.object(module, "set_row_compare_opener") as set_opener, mock.patch.object(
            self.store, "close"
        ) as close_store:
            controller.shutdown()
        set_opener.assert_called_with(None)
        close_store.assert_called_once()
        history.close.assert_called_once()



class _Preview:
    """미리 보기 컨트롤러 대역: 비교 진입과 전환, 연결만 기록"""

    def __init__(self) -> None:
        self.opened: list[tuple[str, float, bool]] = []
        self.toggled: int = 0
        self.attached: tuple[Callable[[str, float], object], object] | None = None
        self.apply_manual_target: Callable[[VideoEvaluation, str | None], bool] | None = None
        self.shortcuts: Mapping[str, str] = {}

    def open_compare(
        self, path: str, position_sec: float = 0.0, *, auto_play: bool = False
    ) -> None:
        self.opened.append((path, position_sec, auto_play))

    def toggle_compare(self) -> None:
        self.toggled += 1

    def attach_compare(
        self,
        build_session: Callable[[str, float], object],
        extractor: object,
        *,
        apply_manual_target: Callable[[VideoEvaluation, str | None], bool] | None = None,
    ) -> None:
        self.attached = (build_session, extractor)
        self.apply_manual_target = apply_manual_target

    def sync_shortcuts(self, shortcuts: Mapping[str, str]) -> None:
        self.shortcuts = dict(shortcuts)


class RoutingTests(_ControllerCase):
    """진입점을 미리 보기 창의 비교 모드로 넘기는지"""

    def _controller(self, preview: _Preview) -> FrameCompareController:
        controller = FrameCompareController(
            self.service, self.store, preview=cast("PreviewController", preview)
        )
        self.addCleanup(controller.shutdown)
        return controller

    def test_open_path_forwards_to_the_preview_compare_mode(self) -> None:
        preview = _Preview()
        controller = self._controller(preview)
        controller.open_path(self.source, 12.5, auto_play=True)
        self.assertEqual(preview.opened, [(self.source, 12.5, True)])

    def test_open_path_ignores_empty_path_and_missing_preview(self) -> None:
        preview = _Preview()
        self._controller(preview).open_path("")
        self.assertEqual(preview.opened, [])
        controller = FrameCompareController(self.service, self.store)
        self.addCleanup(controller.shutdown)
        controller.open_path(self.source)

    def test_toggle_forwards_to_the_preview_controller(self) -> None:
        preview = _Preview()
        self._controller(preview).toggle()
        self.assertEqual(preview.toggled, 1)
        controller = FrameCompareController(self.service, self.store)
        self.addCleanup(controller.shutdown)
        controller.toggle()


class _ManualTargets:
    """출력 해상도 직접 지정 요청만 기록하는 대역"""

    def __init__(self) -> None:
        self.calls: list[tuple[list[VideoEvaluation], str | None]] = []

    def request(self, evals: list[VideoEvaluation], value: str | None) -> bool:
        self.calls.append((evals, value))
        return True


class _Host:
    """attach에 필요한 메인 창 인터페이스만 흉내 내는 호스트"""

    def __init__(self, report: SummaryReport | None) -> None:
        self.table_view = VideoTableView()
        self.dialogs = _Dialogs()
        self.manual_targets = _ManualTargets()
        self._report: SummaryReport | None = report

    @property
    def current_report(self) -> SummaryReport | None:
        return self._report

    @property
    def classifier(self) -> ClassifierSession | None:
        return None


class _Dialogs:
    """단축키 리스너 등록만 흉내 내는 대화상자 조율자"""

    def __init__(self) -> None:
        self.listeners: list[Callable[[Mapping[str, str]], None]] = []

    def register_shortcut_listener(
        self, listener: Callable[[Mapping[str, str]], None]
    ) -> None:
        self.listeners.append(listener)


class AttachTests(GuiTestCase):
    """attach가 행 우클릭과 미리 보기 비교 연결을 컨트롤러에 연결하는지"""

    def test_selected_source_path_reads_the_topmost_selected_row(self) -> None:
        """화질 비교 전환이 표에서 가장 위에 선택된 영상 경로를 읽는지 검증"""
        host = _Host(None)
        self.addCleanup(host.table_view.deleteLater)
        with mock.patch.object(
            host.table_view, "topmost_selected_video_row", return_value=2
        ), mock.patch.object(
            host.table_view, "video_path_at_row", return_value="C:/work/a.mp4"
        ):
            self.assertEqual(
                module.selected_source_path(cast("FrameCompareWindowHost", host)),
                "C:/work/a.mp4",
            )
        with mock.patch.object(host.table_view, "topmost_selected_video_row", return_value=None):
            self.assertIsNone(module.selected_source_path(cast("FrameCompareWindowHost", host)))

    def test_attach_registers_the_row_opener_and_one_shortcut_listener(self) -> None:
        host = _Host(None)
        self.addCleanup(host.table_view.deleteLater)
        preview = _Preview()
        with mock.patch.object(module, "set_row_compare_opener") as set_opener, mock.patch.object(
            module.FrameCompareController, "open_path"
        ) as open_path:
            controller = attach_frame_compare(
                cast("FrameCompareWindowHost", host), cast("PreviewController", preview)
            )
            set_opener.assert_called_once()
            attached = preview.attached
            assert attached is not None
            self.assertEqual(attached[1], controller)
            set_opener.call_args.args[0](make_eval(make_video(filename="clicked.mp4")))
            open_path.assert_not_called()
        self.assertEqual(host.dialogs.listeners, [preview.sync_shortcuts])
        host.dialogs.listeners[0](DEFAULT_SHORTCUTS)
        self.assertEqual(preview.shortcuts, dict(DEFAULT_SHORTCUTS))
        self.addCleanup(controller.shutdown)

    def test_row_opener_resolves_the_clicked_video_under_the_report_root(self) -> None:
        ev = make_eval(make_video(filename="clicked.mp4"))
        host = _Host(make_report([ev], root_dir="C:/work"))
        self.addCleanup(host.table_view.deleteLater)
        with mock.patch.object(module, "set_row_compare_opener") as set_opener, mock.patch.object(
            module.FrameCompareController, "open_path"
        ) as open_path:
            controller = attach_frame_compare(
                cast("FrameCompareWindowHost", host), cast("PreviewController", _Preview())
            )
            set_opener.call_args.args[0](ev)
            open_path.assert_called_once_with(resolve_source_filepath("C:/work", "clicked.mp4"))
        self.addCleanup(controller.shutdown)

    def test_compare_output_apply_routes_to_the_table_manual_target(self) -> None:
        """비교 창의 이 규격으로 지정이 표의 직접 지정 경로로 넘어가는지 검증"""
        ev = make_eval(make_video(filename="clicked.mp4"))
        host = _Host(make_report([ev], root_dir="C:/work"))
        self.addCleanup(host.table_view.deleteLater)
        preview = _Preview()
        controller = attach_frame_compare(
            cast("FrameCompareWindowHost", host), cast("PreviewController", preview)
        )
        self.addCleanup(controller.shutdown)
        apply_target = preview.apply_manual_target
        assert apply_target is not None
        self.assertTrue(apply_target(ev, "1080p"))
        self.assertEqual(host.manual_targets.calls, [([ev], "1080p")])


if __name__ == "__main__":
    unittest.main(verbosity=2)

