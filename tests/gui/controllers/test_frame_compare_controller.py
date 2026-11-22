"""화질 비교 컨트롤러 검증: 세션 계획 구성, 단일 창 재사용, 오류 세션, 종료 정리와 진입점 연결

- 프로빙은 가짜로 바꾸고, 캐시 저장소와 재인코딩 이력은 임시 DB를 씀
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from collections.abc import Callable
from typing import TYPE_CHECKING, cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

if TYPE_CHECKING:
    from src.gui.preview.preview_controller import PreviewController

from src.core.analysis.frame_analysis import TierScore
from src.core.analysis.frame_compare import FrameSource
from src.core.config import ClassifierConfig
from src.core.encode.encode_state import EncodeState
from src.core.models import SummaryReport
from src.core.workspace_paths import resolve_source_filepath
from src.gui.controllers import frame_compare_controller as module
from src.gui.controllers.frame_compare_controller import (
    FrameCompareController,
    attach_frame_compare,
    build_compare_service,
)
from src.gui.preview.preview_controller import PreviewController
from src.gui.table.video_table_view import VideoTableView
from src.service.encode.encode_history import EncodeHistory, NewItem
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
        self.controller = FrameCompareController(self.service, self.store, lambda: 5)
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

    def save_record(
        self, path: str | None = None, filename: str | None = None, candidate: str | None = "1080p"
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
            analysis_version=1,
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
            [NewItem(source_path=self.source, source_size=1, source_mtime_ns=1, plan_json="{}")],
        )
        self.history.update_item(
            batch,
            self.source,
            {"state": EncodeState.DONE, "output_path": self.output, "archive_path": self.archive},
        )
        with mock.patch.object(self.service, "probe", side_effect=self._probe):
            session = self.controller._build_session(self.source, 250.0)
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

    def test_probe_errors_become_error_sessions(self) -> None:
        with mock.patch.object(self.service, "probe", side_effect=FrameCompareFileMissing("파일 없음")):
            session = self.controller._build_session(self.source, 0.0)
        self.assertEqual(session.plan.reason, "파일 없음")
        self.assertEqual(session.duration_sec, 0.0)
        with mock.patch.object(
            self.service, "probe", side_effect=FrameCompareExecutionError("영상 정보를 읽지 못했습니다: boom")
        ):
            session = self.controller._build_session(self.source, 0.0)
        self.assertIn("boom", session.plan.reason)

    def test_archive_original_geometry_drives_the_plan(self) -> None:
        """보관 원본을 프로빙해 메타, 축소 비교 대상, 캐시 조회를 그 기준으로 만듦"""
        batch = self.history.create_batch(
            self.tmp.name,
            '{"encoder": "libx264"}',
            [NewItem(source_path=self.source, source_size=1, source_mtime_ns=1, plan_json="{}")],
        )
        self.history.update_item(
            batch,
            self.source,
            {"state": EncodeState.DONE, "output_path": self.output, "archive_path": self.archive},
        )
        archive_info = self.info(self.archive, width=3840, height=2160)
        record = FrameAnalysisRecord(
            filename="video.mp4",
            filesize_bytes=archive_info.size_bytes,
            mtime_ns=archive_info.mtime_ns,
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
            analysis_version=1,
        )
        self.store.save(record)

        def probe(path: str) -> FrameSourceInfo:
            return self.info(path, width=3840, height=2160) if path == self.archive else self.info(path, width=1920, height=1080)

        with mock.patch.object(self.service, "probe", side_effect=probe):
            session = self.controller._build_session(self.source, 0.0)
        self.assertEqual(session.filename, "video.mp4")
        self.assertEqual((session.width, session.height), (3840, 2160))
        self.assertEqual(session.plan.original.path, self.archive)
        self.assertIn("원본 3840x2160", f"원본 {session.plan.original.detail}")
        self.assertIn("1440p", [target.key for target in session.plan.targets])
        self.assertEqual(session.scores[0].tier, "1080p")

    def test_service_factory_rebuilds_with_the_current_config(self) -> None:
        """설정이 바뀐 뒤 새로 연 비교 창이 새 FFmpeg 경로를 쓰는지 확인"""
        config = ClassifierConfig()
        config.execution.ffmpeg_path = "old-ffmpeg"
        config.execution.ffprobe_path = "old-ffprobe"

        def factory() -> FrameCompareService:
            return build_compare_service(config, self.history)

        controller = FrameCompareController(
            factory(), self.store, lambda: 5, history=self.history, service_factory=factory
        )
        self.addCleanup(controller.shutdown)
        with mock.patch.object(
            FrameCompareService, "probe", lambda service, path: self.info(path)
        ):
            controller.open_path(self.source)
            self.assertEqual(controller.service.ffmpeg_executable, "old-ffmpeg")
            self.assertEqual(controller.service.ffprobe_executable, "old-ffprobe")
            config.execution.ffmpeg_path = "new-ffmpeg"
            config.execution.ffprobe_path = "new-ffprobe"
            controller.open_path(self.source)
        self.assertEqual(controller.service.ffmpeg_executable, "new-ffmpeg")
        self.assertEqual(controller.service.ffprobe_executable, "new-ffprobe")
        window = controller.window
        assert window is not None
        self.assertIs(window._extraction._extractor, controller)

    def test_extract_pair_delegates_to_the_current_service(self) -> None:
        """창이 요청한 추출을 지금 서비스로 넘김"""
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

    def test_shutdown_closes_the_history_and_clears_the_row_opener(self) -> None:
        """종료 때 창, 저장소, 이력, 표 우클릭 함수를 모두 정리"""
        history = mock.Mock()
        controller = FrameCompareController(self.service, self.store, lambda: 5, history=history)
        with mock.patch.object(self.service, "probe", side_effect=lambda path: self.info(path)), mock.patch.object(
            module, "set_row_compare_opener"
        ) as set_opener, mock.patch.object(self.store, "close") as close_store:
            controller.open_path(self.source)
            controller.shutdown()
        set_opener.assert_called_with(None)
        close_store.assert_called_once()
        history.close.assert_called_once()
        self.assertIsNone(controller.window)

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
            session = self.controller._build_session(self.source, 3.0)
        self.assertEqual(calls, [self.source, self.output])
        self.assertNotEqual(session.plan.targets[0].key, "result")
        self.assertEqual(session.position_sec, 3.0)


class WindowLifecycleTests(_ControllerCase):
    """단일 창 재사용과 종료 정리"""
    def test_open_path_reuses_one_window_and_switches_target(self) -> None:
        with mock.patch.object(self.service, "probe", side_effect=lambda path: self.info(path)):
            self.controller.open_path(self.source, 5.0)
            window = self.controller.window
            assert window is not None
            _APP.processEvents()
            self.assertEqual(window._toolbar.filename.text(), "video.mp4")
            self.assertEqual(window._position_sec, 5.0)
            self.controller.open_path(self.output, 0.0)
            self.assertIs(self.controller.window, window)
            self.assertEqual(window._toolbar.filename.text(), "video_out.mp4")
            window.close()

    def test_open_path_ignores_empty_path(self) -> None:
        self.controller.open_path("")
        self.assertIsNone(self.controller.window)

    def test_shutdown_closes_window(self) -> None:
        with mock.patch.object(self.service, "probe", side_effect=lambda path: self.info(path)):
            self.controller.open_path(self.source)
        window = self.controller.window
        assert window is not None
        self.controller.shutdown()
        self.assertIsNone(self.controller.window)
        self.assertTrue(window.isHidden())

    def _switchable(self, preview: _Preview) -> FrameCompareController:
        controller = FrameCompareController(
            self.service, self.store, lambda: 5, preview=cast("PreviewController", preview)
        )
        self.addCleanup(controller.shutdown)
        return controller

    def test_toggle_moves_from_builtin_preview_to_compare_and_hides_preview(self) -> None:
        """미리 보기 창이 보이면 경로, 시점, 영역을 넘겨 비교 창을 열고 미리 보기 창을 숨기는지 검증"""
        geometry = QRect(10, 20, 800, 450)
        preview_window = _PreviewWindow(self.source, 12.5, geometry)
        preview = _Preview(preview_window)
        controller = self._switchable(preview)
        with mock.patch.object(self.service, "probe", side_effect=lambda path: self.info(path)):
            controller.toggle()
        window = controller.window
        assert window is not None
        self.assertAlmostEqual(window._position_sec, 12.5)
        self.assertTrue(preview_window.hidden)
        self.assertEqual(preview.hidden, 1)

    def test_toggle_returns_from_compare_to_preview_with_time_and_geometry(self) -> None:
        """비교 창이 보이면 그 시점과 영역을 미리 보기로 넘기고 비교 창을 숨기는지 검증"""
        preview = _Preview()
        controller = self._switchable(preview)
        with mock.patch.object(self.service, "probe", side_effect=lambda path: self.info(path)):
            controller.open_path(self.source, 7.0)
            window = controller.window
            assert window is not None
            controller.toggle()
        self.assertTrue(window.isHidden())
        self.assertEqual(preview.preview_calls[-1][:2], (self.source, 7.0))
        self.assertIsNotNone(preview.preview_calls[-1][2])
        self.assertFalse(preview.preview_calls[-1][3])

    def test_open_from_preview_keeps_the_playing_state(self) -> None:
        """미리 보기가 재생 중이면 비교 창도 재생 상태로 열리는지 검증"""
        preview_window = _PreviewWindow(self.source, 12.5, QRect(0, 0, 800, 450))
        preview = _Preview(preview_window)
        controller = self._switchable(preview)
        with mock.patch.object(controller, "open_path") as open_path:
            controller.open_from_preview(self.source, 12.5)
        open_path.assert_called_once_with(
            self.source, 12.5, geometry=preview_window.geometry(), auto_play=True
        )
        self.assertEqual(preview.hidden, 1)

    def test_open_from_preview_keeps_the_paused_state(self) -> None:
        """미리 보기가 멈춰 있었으면 비교 창도 일시중지로 여는지 검증"""
        preview_window = _PreviewWindow(self.source, 12.5, QRect(0, 0, 800, 450))
        preview_window.is_playing = False
        preview = _Preview(preview_window)
        controller = self._switchable(preview)
        with mock.patch.object(controller, "open_path") as open_path:
            controller.open_from_preview(self.source, 12.5)
        self.assertFalse(open_path.call_args.kwargs["auto_play"])

    def test_switch_to_preview_forwards_the_playing_state(self) -> None:
        """비교 창이 재생 중이면 미리 보기도 재생 상태로 이어 가는지 검증"""
        preview = _Preview()
        controller = self._switchable(preview)
        with mock.patch.object(self.service, "probe", side_effect=lambda path: self.info(path)):
            controller.open_path(self.source, 4.0)
            window = controller.window
            assert window is not None
            with mock.patch.object(
                type(window), "is_playing", new_callable=mock.PropertyMock, return_value=True
            ):
                controller.toggle()
        self.assertEqual(preview.preview_calls[-1][:2], (self.source, 4.0))
        self.assertTrue(preview.preview_calls[-1][3])

    def test_release_decoders_closes_the_service(self) -> None:
        """창을 숨길 때 서비스가 연 메모리 디코더를 닫는지 검증"""
        controller = self._switchable(_Preview())
        with mock.patch.object(self.service, "close") as close:
            controller.release_decoders()
        close.assert_called_once()

    def test_toggle_with_no_open_window_opens_the_selected_video(self) -> None:
        """미리 보기와 비교 창이 모두 없으면 선택 영상의 비교 창을 여는지 검증"""
        controller = self._switchable(_Preview())
        controller._selected_path = lambda: self.source
        with mock.patch.object(self.service, "probe", side_effect=lambda path: self.info(path)):
            controller.toggle()
        self.assertIsNotNone(controller.window)

    def test_window_preview_signal_switches_back_to_preview(self) -> None:
        """비교 창의 Shift+Space 신호가 비교 창을 숨기고 미리 보기를 부르는지 검증"""
        preview = _Preview()
        controller = self._switchable(preview)
        with mock.patch.object(self.service, "probe", side_effect=lambda path: self.info(path)):
            controller.open_path(self.source, 3.0)
            window = controller.window
            assert window is not None
            window.preview_requested.emit(self.source, 3.0)
        self.assertTrue(window.isHidden())
        self.assertEqual(preview.preview_calls[-1][:2], (self.source, 3.0))


class _Host:
    """attach에 필요한 메인 창 인터페이스만 흉내 내는 호스트"""

    def __init__(self, report: SummaryReport | None) -> None:
        self.table_view = VideoTableView()
        self._report: SummaryReport | None = report

    @property
    def current_report(self) -> SummaryReport | None:
        return self._report

    @property
    def classifier(self) -> ClassifierSession | None:
        return None


class _PreviewWindow:
    """미리 보기 창 대역: 보임 여부, 경로, 시점, 재생 상태, 영역을 기록"""

    def __init__(self, path: str, position_sec: float, geometry: QRect) -> None:
        self.current_path = path
        self.position_sec = position_sec
        self.is_playing = True
        self._geometry = geometry
        self.visible = True
        self.hidden = False

    def isVisible(self) -> bool:
        return self.visible

    def geometry(self) -> QRect:
        return self._geometry

    def hide(self) -> None:
        self.visible = False
        self.hidden = True


class _Preview:
    """attach와 전환이 쓰는 미리 보기 인터페이스만 흉내 내는 대역"""

    def __init__(self, window: _PreviewWindow | None = None) -> None:
        self.seek_seconds = 5
        self.builtin_window = window
        self.handlers: list[Callable[[str, float], None]] = []
        self.hidden = 0
        self.preview_calls: list[tuple[str, float, QRect | None, bool]] = []

    def set_compare_handler(self, handler: Callable[[str, float], None]) -> None:
        self.handlers.append(handler)

    def hide_builtin(self) -> None:
        self.hidden += 1
        if self.builtin_window is not None:
            self.builtin_window.hide()

    def preview_path_at(
        self,
        path: str,
        position_sec: float,
        geometry: QRect | None = None,
        *,
        was_playing: bool = True,
    ) -> None:
        self.preview_calls.append((path, position_sec, geometry, was_playing))

    def get_audio(self) -> tuple[int, bool]:
        return 70, False

    def save_audio(self, volume_percent: int, muted: bool) -> None:
        del volume_percent, muted

    def get_playback_rate(self) -> float:
        return 1.5

    def save_playback_rate(self, rate: float) -> None:
        del rate


class AttachTests(GuiTestCase):
    """attach가 행 우클릭과 미리 보기 요청을 컨트롤러에 연결하는지"""

    def test_selected_source_path_reads_the_topmost_selected_row(self) -> None:
        """화질 비교 전환이 표에서 가장 위에 선택된 영상 경로를 읽는지 검증"""
        host = _Host(None)
        self.addCleanup(host.table_view.deleteLater)
        with mock.patch.object(host.table_view, "topmost_selected_video_row", return_value=2), mock.patch.object(
            host.table_view, "video_path_at_row", return_value="C:/work/a.mp4"
        ):
            self.assertEqual(module.selected_source_path(host), "C:/work/a.mp4")
        with mock.patch.object(host.table_view, "topmost_selected_video_row", return_value=None):
            self.assertIsNone(module.selected_source_path(host))

    def test_attach_registers_row_opener_and_preview_handler(self) -> None:
        host = _Host(None)
        self.addCleanup(host.table_view.deleteLater)
        preview = _Preview()
        with mock.patch.object(module, "set_row_compare_opener") as set_opener, mock.patch.object(
            module.FrameCompareController, "open_path"
        ) as open_path:
            controller = attach_frame_compare(host, cast("PreviewController", preview))
            set_opener.assert_called_once()
            self.assertEqual(len(preview.handlers), 1)
            self.assertEqual(preview.handlers[0], controller.open_from_preview)
            set_opener.call_args.args[0](make_eval(make_video(filename="clicked.mp4")))
            open_path.assert_not_called()
        self.addCleanup(controller.shutdown)

    def test_row_opener_resolves_the_clicked_video_under_the_report_root(self) -> None:
        ev = make_eval(make_video(filename="clicked.mp4"))
        host = _Host(make_report([ev], root_dir="C:/work"))
        self.addCleanup(host.table_view.deleteLater)
        preview = _Preview()
        with mock.patch.object(module, "set_row_compare_opener") as set_opener, mock.patch.object(
            module.FrameCompareController, "open_path"
        ) as open_path:
            controller = attach_frame_compare(host, cast("PreviewController", preview))
            set_opener.call_args.args[0](ev)
            open_path.assert_called_once_with(resolve_source_filepath("C:/work", "clicked.mp4"))
        self.addCleanup(controller.shutdown)


if __name__ == "__main__":
    unittest.main(verbosity=2)
