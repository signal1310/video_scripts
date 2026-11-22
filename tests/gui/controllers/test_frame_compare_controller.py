"""화질 비교 컨트롤러 검증: 세션 계획 구성, 단일 창 재사용, 오류 세션, 종료 정리와 진입점 연결

- 프로빙은 가짜로 바꾸고, 캐시 저장소와 재인코딩 이력은 임시 DB를 씀
"""

import os
import tempfile
import unittest
from collections.abc import Callable
from typing import TYPE_CHECKING, cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

if TYPE_CHECKING:
    from src.gui.preview.preview_controller import PreviewController

from src.core.analysis.frame_analysis import TierScore
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
    FrameCompareExecutionError,
    FrameCompareFileMissing,
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

    def save_record(self, candidate: str | None = "1080p") -> FrameAnalysisRecord:
        info = self.info(self.source)
        record = FrameAnalysisRecord(
            filename=info.filename,
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
        self.save_record()
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


class _FakePreview:
    """attach가 쓰는 미리 보기 인터페이스만 흉내 내는 대역"""

    def __init__(self) -> None:
        self.seek_seconds: int = 5
        self.handlers: list[Callable[[str, float], None]] = []

    def set_compare_handler(self, handler: Callable[[str, float], None]) -> None:
        self.handlers.append(handler)


class AttachTests(GuiTestCase):
    """attach가 행 우클릭과 미리 보기 요청을 컨트롤러에 연결하는지"""

    def test_attach_registers_row_opener_and_preview_handler(self) -> None:
        host = _Host(None)
        self.addCleanup(host.table_view.deleteLater)
        preview = _FakePreview()
        with mock.patch.object(module, "set_row_compare_opener") as set_opener, mock.patch.object(
            module.FrameCompareController, "open_path"
        ) as open_path:
            controller = attach_frame_compare(host, cast("PreviewController", preview))
            set_opener.assert_called_once()
            self.assertEqual(len(preview.handlers), 1)
            self.assertEqual(preview.handlers[0], controller.open_path)
            set_opener.call_args.args[0](make_eval(make_video(filename="clicked.mp4")))
            open_path.assert_not_called()
        self.addCleanup(controller.shutdown)

    def test_row_opener_resolves_the_clicked_video_under_the_report_root(self) -> None:
        ev = make_eval(make_video(filename="clicked.mp4"))
        host = _Host(make_report([ev], root_dir="C:/work"))
        self.addCleanup(host.table_view.deleteLater)
        preview = _FakePreview()
        with mock.patch.object(module, "set_row_compare_opener") as set_opener, mock.patch.object(
            module.FrameCompareController, "open_path"
        ) as open_path:
            controller = attach_frame_compare(host, cast("PreviewController", preview))
            set_opener.call_args.args[0](ev)
            open_path.assert_called_once_with(resolve_source_filepath("C:/work", "clicked.mp4"))
        self.addCleanup(controller.shutdown)


if __name__ == "__main__":
    unittest.main(verbosity=2)
