"""재인코딩 소유자의 시작 상태 계산, 확인 창 수락과 거절, 미리 보기 연결, 메뉴 콜백 연결, 창 종료 정리를 검증

- FFmpeg 확인, 인코더 시험, 확인 창, 배치 시작은 모두 가짜로 바꿔 실제 FFmpeg와 사용자 data 폴더에 닿지 않음
"""

import os
import tempfile
import unittest
from collections.abc import Callable
from typing import Any, ClassVar
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QWidget

from src.core.config import ClassifierConfig
from src.core.encode.encode_start_state import ANALYSIS_BUSY_TEXT, ENCODER_CHECKING_TEXT, NO_TARGET_TEXT
from src.core.encode.encoder_trial import NOT_LISTED_REASON, EncoderTrialCache
from src.core.encode.ffmpeg_capability import FfmpegCapability
from src.core.models import VideoEvaluation
from src.core.smart_action import SmartAction
from src.gui.controllers import encode_coordinator as coordinator_module
from src.gui.controllers.encode_controller import EncodeController
from src.gui.controllers.encode_coordinator import EncodeCoordinator
from src.gui.preview.preview_controller import PreviewController
from src.service.analysis_store import AnalysisStore
from tests._factories import make_eval, make_video
from tests._gui_helpers import make_menu_callbacks

_APP = QApplication.instance() or QApplication([])
_CODEC: str = "libx264"
_USABLE = FfmpegCapability("ffmpeg", "ffmpeg version 6.1", (6, 1), encoders=frozenset({_CODEC}), usable=True)
_UNUSABLE = FfmpegCapability("ffmpeg", "", None, usable=False, reason="찾을 수 없음")


class _FakeDialog:
    """확인 창 대신 쓰는 가짜. 생성 인자를 기록하고 정해진 결과 코드를 돌려줌"""

    result_code: ClassVar[int] = int(QDialog.DialogCode.Accepted)
    instances: ClassVar[list["_FakeDialog"]] = []

    def __init__(self, *args: Any) -> None:
        self.args: tuple[Any, ...] = args
        self.deleted: bool = False
        _FakeDialog.instances.append(self)

    def exec(self) -> int:
        return _FakeDialog.result_code

    def deleteLater(self) -> None:
        self.deleted = True


def _sync_cache(returncode: int = 0) -> EncoderTrialCache:
    """시험을 바로 끝내는 캐시. 백그라운드 스레드를 만들지 않음"""
    return EncoderTrialCache(runner=lambda _command, _timeout: returncode, spawn=lambda task: task())


def _report(root: str, evaluations: list[VideoEvaluation]) -> Any:
    report = mock.Mock()
    report.root_dir = root
    report.evaluations = evaluations
    return report


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
        self._patch("src.gui.controllers.encode_coordinator.EncodeStartDialog", _FakeDialog)
        self._patch("src.gui.controllers.encode_coordinator.probe_ffmpeg_capability", self._probe)
        _FakeDialog.instances = []
        _FakeDialog.result_code = int(QDialog.DialogCode.Accepted)
        self.coordinator = EncodeCoordinator(
            self.parent, self.session, lambda: self.busy, self.preview
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

    def test_missing_encoder_in_listing_blocks_with_the_listing_reason(self) -> None:
        """목표 인코딩 방식이 FFmpeg 인코더 목록에 없으면 목록 사유로 막히는지 검증"""
        self.config.encode.encode_codec = "av1_nvenc"
        state = self.coordinator.start_state()
        self.assertEqual(state.reason, NOT_LISTED_REASON)

    def test_pending_trial_reports_checking_and_asks_for_recheck(self) -> None:
        """인코더 시험이 끝나지 않으면 확인 중이고 다시 확인이 필요하다고 알리는지 검증"""
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

    def test_menu_start_does_nothing_while_disabled(self) -> None:
        """시작 항목이 비활성이면 확인 창을 열지 않는지 검증"""
        self.busy = True
        self.coordinator.start_from_menu()
        self.assertEqual(_FakeDialog.instances, [])

    def test_accepted_dialog_starts_the_batch_and_reports_the_change(self) -> None:
        """확인 창을 수락하면 판정 결과로 배치를 시작하고 상태 변경을 알리는지 검증"""
        with mock.patch.object(EncodeController, "start", return_value=1) as start:
            self.coordinator.start_from_menu()
        self.assertEqual(len(_FakeDialog.instances), 1)
        start.assert_called_once()
        config, evaluations, root_spec, root_dir = start.call_args.args
        self.assertIs(config, self.config)
        self.assertEqual(root_spec, self.root)
        self.assertEqual(root_dir, self.root)
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(self.changed_count, 1)
        self.assertTrue(_FakeDialog.instances[0].deleted)

    def test_rejected_dialog_starts_nothing_but_releases_the_preview_owner(self) -> None:
        """확인 창을 취소하면 배치를 시작하지 않고, 미리 보기 창의 확인 창 지정은 풀리는지 검증"""
        _FakeDialog.result_code = int(QDialog.DialogCode.Rejected)
        with mock.patch.object(EncodeController, "start") as start:
            self.coordinator.start_from_menu()
        start.assert_not_called()
        self.assertEqual(self.changed_count, 0)
        self.preview.release_dialog_owner.assert_called_once()

    def test_dialog_preview_callback_forwards_the_chosen_video_and_the_dialog(self) -> None:
        """확인 창의 미리 보기 요청이 고른 영상과 확인 창을 함께 미리 보기 컨트롤러에 넘기는지 검증"""
        with mock.patch.object(EncodeController, "start"):
            self.coordinator.start_from_menu()
        dialog = _FakeDialog.instances[0]
        preview_callback: Callable[[VideoEvaluation, str], None] = dialog.args[5]
        chosen = self.report.evaluations[0]
        preview_callback(chosen, "clip.mp4")
        self.preview.preview_for_dialog.assert_called_once_with(chosen, "clip.mp4", dialog)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
