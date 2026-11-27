"""화질 비교 뷰 검증: 세션 적재, 상단 접기, 사용자 지정 단축키, 재생 신호, 전체화면 요청, 오류, 정리

- 추출기는 가짜로 바꿔 실제 미디어와 FFmpeg 없이 확인
- 재생은 가짜 엔진으로 바꿔 시점, 재생 상태, 배속, 음소거 신호만 확인
"""

import os
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QEvent, QIODevice, QObject, QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QKeyEvent
from PyQt6.QtWidgets import QApplication, QLabel

from src.core.analysis.frame_analysis import TierScore
from src.core.analysis.frame_compare import (
    NO_TARGET_REASON,
    ComparePlan,
    FrameSource,
    resolve_compare_plan,
    result_target,
)
from src.core.models import ActiveFrame, VideoEvaluation
from src.core.resolution_tiers import KEEP_RESOLUTION_KEY, RESOLUTION_TIER_BY_KEY
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.core.smart_action import DownscaleReason, SmartAction
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs import frame_compare_view as view_module
from src.gui.dialogs.frame_compare_canvas import MODE_SIDE, MODE_SPLIT, MODE_TOGGLE
from src.gui.dialogs.frame_compare_meta import CompareSession
from src.gui.dialogs.frame_compare_view import FrameCompareView
from src.gui.dialogs.frame_compare_worker import ExtractOutcome
from src.gui.styles.palettes import LIGHT_PALETTE
from src.service.frame_compare_decoder import DecodedFrame
from src.service.frame_compare_service import (
    ExtractedFrames,
    FrameCompareExecutionError,
    FrameCompareFileMissing,
)
from tests._factories import make_eval, make_report, make_video
from tests._gui_helpers import GuiTestCase, release_modifier_keys

_APP = QApplication.instance() or QApplication([])
_WAIT_SECONDS: float = 5.0


def _bmp(color: str) -> bytes:
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "BMP")
    return bytes(buffer.data())


_ORIGINAL_BYTES: bytes = _bmp("#FF0000")
_COMPARED_BYTES: bytes = _bmp("#0000FF")


class _Extractor:
    """호출을 기록하고 정해진 프레임 바이트나 오류를 돌려주는 가짜 추출기"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float, int, int]] = []
        self.crops: list[tuple[object, object]] = []
        self.failure: Exception | None = None
        self.released: int = 0

    def extract_pair(
        self,
        original: FrameSource,
        compared: FrameSource,
        *,
        time_sec: float,
        frame_width: int,
        frame_height: int,
        cancel_event: threading.Event,
    ) -> ExtractedFrames:
        self.calls.append((original.path, compared.path, time_sec, frame_width, frame_height))
        self.crops.append((original.crop, compared.crop))
        if self.failure is not None:
            raise self.failure
        return ExtractedFrames(original_bytes=_ORIGINAL_BYTES, compared_bytes=_COMPARED_BYTES)

    def release_decoders(self) -> None:
        self.released += 1


class _FakeEngine(QObject):
    """재생 엔진 없이 뷰의 재생 분기와 신호만 확인하는 가짜 엔진"""

    frames_ready = pyqtSignal(object, object)
    position_changed = pyqtSignal(int)
    playing_changed = pyqtSignal(bool)
    end_reached = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.playing: bool = False
        self.position: int = 0
        self.rate_value: float = 1.0
        self.muted_value: bool = False
        self.volume: int = 100
        self.loaded: dict[str, object] = {}

    def load(
        self,
        *,
        original_path: str,
        compared_path: str,
        tier_key: str | None,
        frame_width: int,
        frame_height: int,
        position_ms: int,
        crop: object = None,
        scale_algorithm: str = "bicubic",
    ) -> None:
        self.loaded = {
            "original_path": original_path,
            "compared_path": compared_path,
            "tier_key": tier_key,
            "position_ms": position_ms,
            "crop": crop,
            "scale_algorithm": scale_algorithm,
        }
        self.calls.append("load")

    def play(self) -> None:
        self.playing = True
        self.calls.append("play")
        self.playing_changed.emit(True)

    def pause(self) -> None:
        self.playing = False
        self.calls.append("pause")
        self.playing_changed.emit(False)

    def seek(self, position_ms: int) -> None:
        self.position = position_ms
        self.calls.append("seek")

    def stop(self) -> None:
        self.playing = False
        self.calls.append("stop")

    def is_playing(self) -> bool:
        return self.playing

    def position_ms(self) -> int:
        return self.position

    def rate(self) -> float:
        return self.rate_value

    def muted(self) -> bool:
        return self.muted_value

    def volume_percent(self) -> int:
        return self.volume

    def set_rate(self, rate: float) -> None:
        self.rate_value = rate
        self.calls.append("set_rate")

    def apply_audio(self, volume_percent: int, muted: bool) -> None:
        self.volume = volume_percent
        self.muted_value = muted
        self.calls.append("apply_audio")


def _wait(view: FrameCompareView) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while view._extraction.busy and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)
    _APP.processEvents()


def _press(
    view: FrameCompareView,
    key: Qt.Key,
    modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    *,
    repeat: bool = False,
) -> bool:
    handled = view.handle_key_event(QKeyEvent(QEvent.Type.KeyPress, key, modifier, "", repeat))
    if modifier != Qt.KeyboardModifier.NoModifier:
        release_modifier_keys()
    return handled


class _ViewCase(GuiTestCase):
    """임시 미디어 파일과 비교 계획, 세션을 준비하는 기반"""

    fake_engine: _FakeEngine | None = None

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = os.path.join(self.tmp.name, "video.mp4")
        self.result = os.path.join(self.tmp.name, "video_out.mp4")
        for path in (self.source, self.result):
            with open(path, "wb") as handle:
                handle.write(b"x")
        self.extractor = _Extractor()
        self.shortcuts: dict[str, str] = dict(DEFAULT_SHORTCUTS)
        self.guide_state = {"open": False}
        self.applied: list[tuple[VideoEvaluation, str | None]] = []
        self.apply_result: bool = True
        self.view = self._create_view()
        self.addCleanup(self.view.deleteLater)
        self.view.show()
        _APP.processEvents()

    def _apply_target(self, ev: VideoEvaluation, value: str | None) -> bool:
        """비교 창의 1클릭 지정을 표 대신 기록"""
        self.applied.append((ev, value))
        return self.apply_result

    def _create_view(self) -> FrameCompareView:
        def close_guide() -> bool:
            if not self.guide_state["open"]:
                return False
            self.guide_state["open"] = False
            return True

        def is_guide_open() -> bool:
            return self.guide_state["open"]

        if self.fake_engine is None:
            return FrameCompareView(
                self.extractor,
                lambda: 5,
                lambda: self.shortcuts,
                close_guide,
                is_guide_open,
                apply_manual_target=self._apply_target,
            )
        with mock.patch.object(view_module, "ComparePlaybackEngine", return_value=self.fake_engine):
            return FrameCompareView(
                self.extractor,
                lambda: 5,
                lambda: self.shortcuts,
                close_guide,
                is_guide_open,
                apply_manual_target=self._apply_target,
            )

    def session(
        self,
        *,
        with_result: bool = True,
        scores: tuple[TierScore, ...] = (),
        active_frame: ActiveFrame | None = None,
        result_crop: ActiveFrame | None = None,
        evaluation: VideoEvaluation | None = None,
        scale_algorithm: str = "bicubic",
        source_path: str | None = None,
        filename: str = "video.mp4",
        position_sec: float = 10.0,
    ) -> CompareSession:
        source = self.source if source_path is None else source_path
        result = (
            result_target(self.result, width=1920, height=1080, size_bytes=580 * 1024 * 1024)
            if with_result
            else None
        )
        plan = resolve_compare_plan(
            source_path=source,
            width=3840,
            height=2160,
            size_bytes=1024**3,
            result=result,
            candidate_tier="1080p",
            active_frame=active_frame,
            result_crop=result_crop,
        )
        return CompareSession(
            plan=plan,
            duration_sec=100.0,
            fps=30.0,
            filename=filename,
            position_sec=position_sec,
            scores=scores,
            width=3840 if active_frame is None else active_frame.width,
            height=2160 if active_frame is None else active_frame.height,
            source_path=source,
            active_frame=active_frame,
            scale_algorithm=scale_algorithm,
            evaluation=evaluation,
        )

    def load(
        self,
        *,
        with_result: bool = True,
        scores: tuple[TierScore, ...] = (),
        active_frame: ActiveFrame | None = None,
        result_crop: ActiveFrame | None = None,
        evaluation: VideoEvaluation | None = None,
        scale_algorithm: str = "bicubic",
    ) -> None:
        self.view.load_session(
            self.session(
                with_result=with_result,
                scores=scores,
                active_frame=active_frame,
                result_crop=result_crop,
                evaluation=evaluation,
                scale_algorithm=scale_algorithm,
            )
        )
        _wait(self.view)

    def make_view(self, extractor: _Extractor) -> FrameCompareView:
        """다른 추출기로 뷰를 하나 더 만들어 오류와 로딩 분기를 확인"""
        view = FrameCompareView(
            extractor,
            lambda: 5,
            lambda: self.shortcuts,
            lambda: False,
            lambda: False,
            apply_manual_target=self._apply_target,
        )
        self.addCleanup(view.deleteLater)
        return view



class LoadSessionTests(_ViewCase):
    """세션 적재 뒤 상단 바, 메타, 대상, 프레임 상태"""

    def test_default_target_is_result_and_frames_load(self) -> None:
        self.load()
        self.assertEqual(self.view._top.filename.text(), "video.mp4")
        self.assertEqual(self.view._top.target_button.text(), "결과")
        self.assertIn("원본 3840x2160", self.view._top.meta.original.text())
        self.assertIn("결과 1920x1080", self.view._top.meta.target.text())
        self.assertEqual(self.view._top.meta.time.text(), "시점 00:10.000")
        labels = [action.text() for action in self.view._top.target_menu.actions()]
        self.assertEqual(labels[0], "결과")
        self.assertIn("1080p 축소 비교 (축소 후보)", labels)
        self.assertIn("900p 축소 비교", labels)
        self.assertIsNotNone(self.view._canvas._original)
        self.assertTrue(self.view._stage.overlay.isHidden())
        self.assertEqual(self.extractor.calls[0][2], 10.0)
        self.assertEqual(self.extractor.calls[0][3:], (3840, 2160))
        self.assertEqual(self.view.source_path, self.source)
        self.assertAlmostEqual(self.view.duration_sec, 100.0)

    def test_open_a_second_video_replaces_the_previous_frame(self) -> None:
        """다른 영상을 열면 이전 영상의 프레임을 지우고 새 프레임을 그리는지 검증"""
        other = os.path.join(self.tmp.name, "other.mp4")
        with open(other, "wb") as handle:
            handle.write(b"x")
        arrived = threading.Event()
        released = threading.Event()
        self.addCleanup(released.set)

        class _NewVideo(_Extractor):
            def extract_pair(
                self,
                original: FrameSource,
                compared: FrameSource,
                *,
                time_sec: float,
                frame_width: int,
                frame_height: int,
                cancel_event: threading.Event,
            ) -> ExtractedFrames:
                del frame_width, frame_height, cancel_event
                if original.path == other:
                    arrived.set()
                    released.wait(_WAIT_SECONDS)
                self.calls.append((original.path, compared.path, time_sec, 0, 0))
                return ExtractedFrames(
                    original_bytes=_bmp("#00FF00"), compared_bytes=_bmp("#0000FF")
                )

        view = self.make_view(_NewVideo())
        view.load_session(self.session(position_sec=0.0))
        _wait(view)
        self.assertTrue(view._canvas.has_image())
        view.load_session(self.session(source_path=other, filename="other.mp4", position_sec=0.0))
        self.assertTrue(arrived.wait(_WAIT_SECONDS))
        self.assertFalse(view._canvas.has_image())
        released.set()
        _wait(view)
        image = view._canvas._original
        self.assertIsNotNone(image)
        assert image is not None
        self.assertEqual(image.pixelColor(0, 0).name(), "#00ff00")

    def test_seeking_inside_one_video_keeps_the_previous_frame(self) -> None:
        """같은 영상에서 시점을 옮길 때는 새 프레임이 올 때까지 이전 프레임을 남기는지 검증"""
        arrived = threading.Event()
        released = threading.Event()
        self.addCleanup(released.set)
        extractor = self.extractor

        class _Blocking(_Extractor):
            def extract_pair(
                self,
                original: FrameSource,
                compared: FrameSource,
                *,
                time_sec: float,
                frame_width: int,
                frame_height: int,
                cancel_event: threading.Event,
            ) -> ExtractedFrames:
                if time_sec >= 5.0:
                    arrived.set()
                    released.wait(_WAIT_SECONDS)
                return extractor.extract_pair(
                    original,
                    compared,
                    time_sec=time_sec,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    cancel_event=cancel_event,
                )

        view = self.make_view(_Blocking())
        view.load_session(self.session(position_sec=0.0))
        _wait(view)
        self.assertTrue(view._canvas.has_image())
        view.set_time(5.0, immediate=True)
        self.assertTrue(arrived.wait(_WAIT_SECONDS))
        self.assertTrue(view._canvas.has_image())
        released.set()
        _wait(view)

    def test_cached_score_is_shown_inline_only_for_its_tier(self) -> None:
        scores = (TierScore(tier="1080p", ssim=0.9983, vmaf=98.7),)
        self.load(scores=scores)
        self.assertTrue(self.view._top.score.isHidden())
        self.view.set_target("1080p")
        _wait(self.view)
        self.assertFalse(self.view._top.score.isHidden())
        self.assertEqual(self.view._top.score.text(), "화질 분석 1080p: VMAF 98.7, SSIM 0.9983")
        self.assertIn("VMAF는 100에 가까울수록", self.view._top.score.toolTip())
        self.assertEqual(self.view._top.target_button.text(), "1080p 축소 비교")

    def test_plan_without_target_shows_error_overlay(self) -> None:
        plan = ComparePlan(
            original=FrameSource(key="원본", path=self.source, label="원본"),
            targets=(),
            default_key=None,
            reason=NO_TARGET_REASON,
        )
        session = CompareSession(plan=plan, duration_sec=100.0, fps=30.0, filename="video.mp4")
        self.view.load_session(session)
        self.assertFalse(self.view._stage.overlay.isHidden())
        self.assertEqual(self.view._stage.title.text(), layout.ERROR_TITLE)
        self.assertEqual(self.view._stage.body.text(), NO_TARGET_REASON)

    def test_missing_original_uses_the_approved_reason(self) -> None:
        os.remove(self.source)
        self.view.load_session(self.session())
        self.assertEqual(self.view._stage.body.text(), "원본 파일이 없어 비교할 수 없습니다.")

    def test_meta_tooltips_show_the_full_paths_of_both_files(self) -> None:
        self.load()
        self.assertEqual(self.view._top.meta.original.toolTip(), f"원본 파일: {self.source}")
        self.assertEqual(self.view._top.meta.target.toolTip(), f"결과 파일: {self.result}")

    def test_downscale_target_keeps_its_roundtrip_tooltip(self) -> None:
        self.load()
        self.view.set_target("1080p")
        _wait(self.view)
        self.assertIn("축소", self.view._top.meta.target.toolTip())
        self.assertEqual(self.view._top.target_button.text(), "1080p 축소 비교")

    def test_notice_area_starts_below_the_top_inset(self) -> None:
        self.load()
        self.view._canvas.set_top_inset(40)
        area = self.view.notice_area_global()
        canvas_top = self.view._canvas.mapToGlobal(QPoint(0, 0)).y()
        self.assertEqual(area.top(), canvas_top + 40 + 10)
        self.assertEqual(area.width(), self.view._canvas.width())
        self.assertIsInstance(area, QRect)



class InteractionTests(_ViewCase):
    """모드, 배율, 시점, 사용자 지정 단축키"""

    def test_mode_keys_switch_three_modes(self) -> None:
        self.load()
        _press(self.view, Qt.Key.Key_2)
        self.assertEqual(self.view._canvas.mode(), MODE_TOGGLE)
        self.assertTrue(self.view._top.mode_buttons[MODE_TOGGLE].isChecked())
        _press(self.view, Qt.Key.Key_3)
        self.assertEqual(self.view._canvas.mode(), MODE_SIDE)
        _press(self.view, Qt.Key.Key_1)
        self.assertEqual(self.view._canvas.mode(), MODE_SPLIT)

    def test_c_key_toggles_original_only_in_toggle_mode(self) -> None:
        self.load()
        _press(self.view, Qt.Key.Key_C)
        self.assertFalse(self.view._canvas.original_pinned())
        _press(self.view, Qt.Key.Key_2)
        _press(self.view, Qt.Key.Key_C)
        self.assertTrue(self.view._canvas.original_pinned())
        _press(self.view, Qt.Key.Key_C)
        self.assertFalse(self.view._canvas.original_pinned())

    def test_auto_repeat_does_not_flip_the_pinned_original(self) -> None:
        self.load()
        self.view.set_mode(MODE_TOGGLE)
        _press(self.view, Qt.Key.Key_C, repeat=True)
        self.assertFalse(self.view._canvas.original_pinned())

    def test_zero_and_plus_minus_change_zoom(self) -> None:
        self.load()
        self.view.set_zoom_preset("1")
        _press(self.view, Qt.Key.Key_Equal, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(self.view._canvas.scale(), 1.25)
        self.assertFalse(any(button.isChecked() for button in self.view._top.zoom_buttons.values()))
        _press(self.view, Qt.Key.Key_Equal)
        self.assertAlmostEqual(self.view._canvas.scale(), 1.25 * 1.25)
        _press(self.view, Qt.Key.Key_Minus)
        self.assertAlmostEqual(self.view._canvas.scale(), 1.25)
        _press(self.view, Qt.Key.Key_0)
        self.assertTrue(self.view._top.zoom_buttons["fit"].isChecked())

    def test_custom_mode_key_replaces_the_default(self) -> None:
        """사용자가 모드 키를 바꾸면 기본 숫자 키 대신 새 키로 동작하는지 검증"""
        self.load()
        self.view.set_mode(MODE_TOGGLE)
        self.shortcuts["compare.mode_split"] = "F2"
        _press(self.view, Qt.Key.Key_1)
        self.assertEqual(self.view._canvas.mode(), MODE_TOGGLE)
        _press(self.view, Qt.Key.Key_F2)
        self.assertEqual(self.view._canvas.mode(), MODE_SPLIT)

    def test_arrow_keys_seek_by_preview_interval_and_shift_is_six_times(self) -> None:
        self.load()
        _press(self.view, Qt.Key.Key_Right)
        self.assertAlmostEqual(self.view.position_sec, 15.0)
        _press(self.view, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(self.view.position_sec, 45.0)
        _press(self.view, Qt.Key.Key_Left)
        self.assertAlmostEqual(self.view.position_sec, 40.0)
        _press(self.view, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(self.view.position_sec, 10.0)

    def test_comma_and_period_step_one_frame(self) -> None:
        self.load()
        _press(self.view, Qt.Key.Key_Period)
        self.assertAlmostEqual(self.view.position_sec, 10.0 + 1 / 30)
        _press(self.view, Qt.Key.Key_Comma)
        self.assertAlmostEqual(self.view.position_sec, 10.0)

    def test_custom_frame_keys_are_used(self) -> None:
        self.load()
        self.shortcuts["preview.frame_forward"] = "X"
        _press(self.view, Qt.Key.Key_Period)
        self.assertAlmostEqual(self.view.position_sec, 10.0)
        _press(self.view, Qt.Key.Key_X)
        self.assertAlmostEqual(self.view.position_sec, 10.0 + 1 / 30)

    def test_typed_zoom_applies_and_clears_preset_checks(self) -> None:
        self.load()
        self.view.set_zoom_preset("1")
        spin = self.view._top.zoom_input
        spin.setValue(250)
        self.view._top.commit_zoom_input()
        self.assertAlmostEqual(self.view._canvas.scale(), 2.5)
        self.assertFalse(any(button.isChecked() for button in self.view._top.zoom_buttons.values()))

    def test_manual_zoom_and_sync_keep_the_input_in_step(self) -> None:
        self.load()
        self.view._canvas.zoom_step(1)
        self.view._top.on_manual_zoom()
        self.assertEqual(
            self.view._top.zoom_input.value(), round(self.view._canvas.scale() * 100)
        )

    def test_shortcut_keys_report_state_through_the_shared_notice(self) -> None:
        """키보드로 실행한 명령의 결과가 공용 토스트 요청으로 나가는지 검증"""
        self.load()
        notices: list[str] = []
        self.view.notice_requested.connect(notices.append)
        _press(self.view, Qt.Key.Key_2)
        _press(self.view, Qt.Key.Key_C)
        _press(self.view, Qt.Key.Key_Right)
        _press(self.view, Qt.Key.Key_Period)
        _press(self.view, Qt.Key.Key_H)
        _press(self.view, Qt.Key.Key_H)
        _press(self.view, Qt.Key.Key_M)
        self.assertEqual(
            notices,
            [
                "즉시 전환 모드",
                "원본 고정",
                "구간 이동 +5초",
                "다음 프레임",
                "조작 바 접기",
                "조작 바 펼치기",
                "음소거",
            ],
        )

    def test_previous_and_next_request_the_table_step(self) -> None:
        """비교 모드에서도 위아래 방향키가 이전, 다음 영상 전환을 요청하는지 검증"""
        self.load()
        steps: list[int] = []
        self.view.step_requested.connect(steps.append)
        _press(self.view, Qt.Key.Key_Down)
        _press(self.view, Qt.Key.Key_Up)
        self.assertEqual(steps, [1, -1])



class BackgroundCycleViewTests(_ViewCase):
    """화질 비교 뷰의 Shift+B 배경 색 전환이 캔버스와 무대 틀에 반영되는지"""

    def test_shift_b_without_a_parent_keeps_the_current_background(self) -> None:
        """부모 콜백 없이 Shift+B를 눌러도 배경 색이 바뀌지 않는지 검증"""
        self.load()
        self.assertEqual(self.view._stage_background, "black")
        self.assertTrue(_press(self.view, Qt.Key.Key_B, Qt.KeyboardModifier.ShiftModifier))
        self.assertEqual(self.view._stage_background, "black")
        self.assertEqual(self.view._canvas._background, "black")

    def test_shift_b_uses_the_parent_cycle_and_repaints_the_canvas(self) -> None:
        """부모가 돌려준 새 색을 캔버스와 무대 틀에 바로 적용하는지 검증"""
        self.view._cycle_background = lambda: "white"
        self.load()
        self.assertTrue(_press(self.view, Qt.Key.Key_B, Qt.KeyboardModifier.ShiftModifier))
        self.assertEqual(self.view._stage_background, "white")
        self.assertEqual(self.view._canvas._background, "white")
        wrap = self.view._canvas.parentWidget()
        assert wrap is not None
        self.assertEqual(wrap.property("stagebg"), "white")


class CloseAndFullscreenTests(_ViewCase):
    """Space, Esc 닫기 순서와 전체화면 요청"""

    def test_space_requests_the_window_close(self) -> None:
        self.load()
        closes: list[bool] = []
        self.view.close_requested.connect(lambda: closes.append(True))
        _press(self.view, Qt.Key.Key_Space)
        self.assertEqual(closes, [True])

    def test_escape_requests_the_window_close(self) -> None:
        self.load()
        closes: list[bool] = []
        self.view.close_requested.connect(lambda: closes.append(True))
        _press(self.view, Qt.Key.Key_Escape)
        self.assertEqual(closes, [True])

    def test_space_closes_the_open_target_menu_first(self) -> None:
        self.load()
        self.view._top.target_menu.popup(QPoint(10, 10))
        _APP.processEvents()
        closes: list[bool] = []
        self.view.close_requested.connect(lambda: closes.append(True))
        _press(self.view, Qt.Key.Key_Space)
        _APP.processEvents()
        self.assertFalse(self.view._top.target_menu.isVisible())
        self.assertEqual(closes, [])

    def test_escape_leaves_fullscreen_before_closing(self) -> None:
        self.load()
        self.view.showFullScreen()
        _APP.processEvents()
        self.assertTrue(self.view.isFullScreen())
        requests: list[bool] = []
        closes: list[bool] = []
        self.view.fullscreen_requested.connect(lambda: (requests.append(True), self.view.showNormal()))
        self.view.close_requested.connect(lambda: closes.append(True))
        _press(self.view, Qt.Key.Key_Escape)
        _APP.processEvents()
        self.assertEqual(requests, [True])
        self.assertFalse(self.view.isFullScreen())
        self.assertEqual(closes, [])
        _press(self.view, Qt.Key.Key_Escape)
        self.assertEqual(closes, [True])

    def test_space_closes_the_open_shortcut_guide_first(self) -> None:
        self.load()
        self.guide_state["open"] = True
        closes: list[bool] = []
        self.view.close_requested.connect(lambda: closes.append(True))
        _press(self.view, Qt.Key.Key_Space)
        self.assertFalse(self.guide_state["open"])
        self.assertEqual(closes, [])

    def test_ctrl_b_requests_the_preview_with_time_and_path(self) -> None:
        self.load()
        self.view.set_time(30.0)
        requests: list[bool] = []
        self.view.preview_requested.connect(lambda: requests.append(True))
        _press(self.view, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(requests, [True])
        self.assertAlmostEqual(self.view.position_sec, 30.0)
        self.assertEqual(self.view.source_path, self.source)

    def test_fullscreen_key_requests_the_parent_window(self) -> None:
        self.load()
        requests: list[bool] = []
        self.view.fullscreen_requested.connect(lambda: requests.append(True))
        _press(self.view, Qt.Key.Key_F)
        self.assertEqual(requests, [True])

    def test_auto_hide_is_blocked_while_menu_or_editor_is_open(self) -> None:
        self.load()
        self.assertTrue(self.view._can_auto_hide())
        self.view._top.target_menu.popup(QPoint(10, 10))
        _APP.processEvents()
        self.assertFalse(self.view._can_auto_hide())
        self.view._top.target_menu.close()
        self.guide_state["open"] = True
        self.assertFalse(self.view._can_auto_hide())
        self.guide_state["open"] = False
        self.assertTrue(self.view._can_auto_hide())

    def test_h_key_collapses_and_reveals_the_top_bar(self) -> None:
        self.load()
        _press(self.view, Qt.Key.Key_H)
        self.assertTrue(self.view._top.is_collapsed())
        self.view._top.note_mouse_activity(QPoint(10, 10))
        self.assertTrue(self.view._top.is_overlay_visible())
        _press(self.view, Qt.Key.Key_H)
        self.assertFalse(self.view._top.is_collapsed())

    def test_escape_in_the_zoom_input_restores_the_value_and_the_focus(self) -> None:
        """배율 입력에서 Esc를 누르면 값이 되돌아가고 뷰가 다시 단축키를 받는지 검증"""
        self.load()
        top = self.view._top
        self.view.set_zoom_preset("1")
        top.zoom_input.setValue(250)
        top.zoom_input.setFocus(Qt.FocusReason.MouseFocusReason)
        _APP.processEvents()
        self.assertTrue(top.zoom_input.hasFocus())
        QApplication.sendEvent(
            top.zoom_input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        )
        _APP.processEvents()
        self.assertFalse(top.zoom_input.hasFocus())
        self.assertEqual(top.zoom_input.value(), 100)
        self.assertAlmostEqual(self.view._canvas.scale(), 1.0)
        self.assertTrue(top.zoom_buttons["1"].isChecked())
        requests: list[bool] = []
        self.view.preview_requested.connect(lambda: requests.append(True))
        _press(self.view, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(requests, [True])



class PlaybackTests(_ViewCase):
    """재생 단축키, 일시중지 정밀 프레임, 재생 중 이동, 끝 도달, 정리"""

    def setUp(self) -> None:
        self.fake_engine = _FakeEngine()
        super().setUp()
        self.engine = self.fake_engine
        assert self.engine is not None

    def test_load_session_loads_the_engine_at_the_session_time(self) -> None:
        self.load()
        self.assertEqual(self.engine.loaded["original_path"], self.source)
        self.assertEqual(self.engine.loaded["compared_path"], self.result)
        self.assertIsNone(self.engine.loaded["tier_key"])
        self.assertEqual(self.engine.loaded["position_ms"], 10_000)

    def test_load_session_passes_the_configured_scale_algorithm_to_playback(self) -> None:
        """세션의 축소 보간 방식을 재생 엔진 로드에 넘기는지 검증"""
        self.load(scale_algorithm="lanczos")
        self.assertEqual(self.engine.loaded["scale_algorithm"], "lanczos")

    def test_result_comparison_crops_the_original_for_extraction_and_playback(self) -> None:
        """결과 비교가 기록된 활성 화면 크롭을 정밀 프레임과 원본 재생에 넘기는지 검증"""
        active = ActiveFrame(1216, 2160, 1312, 0)
        self.load(active_frame=active, result_crop=active)
        self.assertEqual(self.extractor.crops[-1], (active, None))
        self.assertEqual(self.engine.loaded["crop"], active)
        self.view.set_target("1080p")
        _wait(self.view)
        self.assertEqual(self.extractor.crops[-1], (active, active))
        self.assertEqual(self.engine.loaded["crop"], active)

    def test_legacy_result_without_a_crop_keeps_the_original_full_frame(self) -> None:
        """crop 필터가 없는 과거 결과는 원본을 자르지 않고, 축소 비교만 활성 화면을 쓰는지 검증"""
        active = ActiveFrame(1216, 2160, 1312, 0)
        self.load(active_frame=active)
        self.assertEqual(self.extractor.crops[-1], (None, None))
        self.assertIsNone(self.engine.loaded["crop"])
        self.view.set_target("1080p")
        _wait(self.view)
        self.assertEqual(self.extractor.crops[-1], (active, active))
        self.assertEqual(self.engine.loaded["crop"], active)

    def test_k_starts_and_pauses_keeping_the_current_position(self) -> None:
        self.load()
        _press(self.view, Qt.Key.Key_K)
        self.assertIn("play", self.engine.calls)
        self.assertTrue(self.view.is_playing)
        self.view.set_time(50.0)
        self.assertIn("seek", self.engine.calls)
        self.engine.position = 50_000
        _press(self.view, Qt.Key.Key_K)
        self.assertIn("pause", self.engine.calls)
        self.assertAlmostEqual(self.view.position_sec, 50.0)

    def test_pause_requests_a_precise_frame(self) -> None:
        self.load()
        self.engine.playing = True
        self.engine.position = 12_000
        with mock.patch.object(self.view, "_request_precise_frame") as request:
            self.view.toggle_playback()
        request.assert_called_once()

    def test_step_keys_pause_before_moving(self) -> None:
        self.load()
        self.engine.playing = True
        _press(self.view, Qt.Key.Key_Period)
        self.assertIn("pause", self.engine.calls)
        self.assertAlmostEqual(self.view.position_sec, 10.0 + 1 / 30)

    def test_rate_and_mute_keys_update_the_engine_and_report(self) -> None:
        self.load()
        rates: list[float] = []
        mutes: list[bool] = []
        self.view.rate_changed.connect(rates.append)
        self.view.mute_toggled.connect(mutes.append)
        _press(self.view, Qt.Key.Key_BracketRight)
        _press(self.view, Qt.Key.Key_BracketLeft)
        _press(self.view, Qt.Key.Key_Backslash)
        _press(self.view, Qt.Key.Key_M)
        self.assertEqual(rates, [1.25, 1.0, 1.0])
        self.assertEqual(self.engine.rate_value, 1.0)
        self.assertEqual(mutes, [True])
        self.assertTrue(self.engine.muted_value)

    def test_shared_settings_are_applied_to_the_engine(self) -> None:
        self.load()
        self.view.set_playback_rate(2.5)
        self.view.set_audio(35, True)
        self.assertEqual(self.engine.rate_value, 2.5)
        self.assertEqual(self.engine.volume, 35)
        self.assertTrue(self.engine.muted_value)

    def test_target_change_while_playing_keeps_playing(self) -> None:
        self.load()
        self.engine.playing = True
        self.view.set_target("1080p")
        self.assertEqual(self.engine.loaded["tier_key"], "1080p")
        self.assertIn("play", self.engine.calls)

    def test_playback_frames_and_position_update_the_view(self) -> None:
        self.load()
        image = QImage(40, 20, QImage.Format.Format_RGB32)
        image.fill(QColor("#00FF00"))
        self.engine.playing = True
        self.engine.frames_ready.emit(image, image)
        self.assertIsNotNone(self.view._canvas._compared)
        self.engine.position_changed.emit(20_000)
        self.assertAlmostEqual(self.view.position_sec, 20.0)
        self.engine.frames_ready.emit("잘못된 값", image)
        self.engine.playing = False
        self.engine.position_changed.emit(30_000)
        self.assertAlmostEqual(self.view.position_sec, 20.0)

    def test_playing_state_is_reported_to_the_parent(self) -> None:
        states: list[bool] = []
        self.view.playing_changed.connect(states.append)
        self.view.start_playback()
        self.view.pause_playback()
        self.assertEqual(states, [True, False])

    def test_end_of_media_keeps_the_last_position(self) -> None:
        self.load()
        self.engine.end_reached.emit()
        self.assertAlmostEqual(self.view.position_sec, 100.0)
        self.assertEqual(self.view._top.meta.time.text(), "시점 01:40.000")

    def test_stop_playback_stops_the_engine_and_releases_the_extraction(self) -> None:
        self.load()
        self.engine.playing = True
        self.view.stop_playback()
        self.assertIn("stop", self.engine.calls)
        self.assertFalse(self.view._extraction.busy)
        self.assertGreaterEqual(self.extractor.released, 1)

    def test_duration_is_reported_after_loading(self) -> None:
        durations: list[float] = []
        self.view.duration_changed.connect(durations.append)
        self.load()
        self.assertEqual(durations, [100.0])



class ErrorAndThreadTests(_ViewCase):
    """원인별 오류 표시, 지연 결과 폐기와 종료 시 취소, 로딩 배지"""

    def test_file_missing_and_execution_errors_use_different_bodies(self) -> None:
        self.extractor.failure = FrameCompareFileMissing("파일이 이동되었거나 삭제되었는지 확인해 주세요.")
        self.load()
        self.assertEqual(self.view._stage.title.text(), layout.ERROR_TITLE)
        self.assertEqual(
            self.view._stage.body.text(), "파일이 이동되었거나 삭제되었는지 확인해 주세요."
        )
        self.extractor.failure = FrameCompareExecutionError(
            "FFmpeg로 프레임을 추출하지 못했습니다: boom"
        )
        self.view.set_time(20.0)
        self.view._request_precise_frame()
        _wait(self.view)
        self.assertIn("boom", self.view._stage.body.text())
        self.extractor.failure = None
        self.view.set_time(30.0)
        self.view._request_precise_frame()
        _wait(self.view)
        self.assertTrue(self.view._stage.overlay.isHidden())

    def test_loading_overlay_uses_the_approved_text_when_no_frame(self) -> None:
        view = self.make_view(_Extractor())
        view._show_loading()
        self.assertFalse(view._stage.overlay.isHidden())
        self.assertEqual(view._stage.title.text(), "프레임을 불러오는 중...")
        self.assertEqual(view._stage.body.text(), "")

    def test_loading_after_a_frame_shows_only_the_pill(self) -> None:
        """프레임이 있는 상태에서는 전체 오버레이 대신 알약 배지만 쓰는지 검증"""
        self.load()
        self.view._show_loading()
        self.assertTrue(self.view._stage.overlay.isHidden())
        self.assertTrue(self.view._stage.pill.isHidden())
        self.view._status.reveal()
        self.assertFalse(self.view._stage.pill.isHidden())
        self.assertTrue(self.view._stage.overlay.isHidden())
        self.view._hide_overlay()
        self.assertTrue(self.view._stage.pill.isHidden())

    def test_raw_rgb_frames_from_the_memory_decoder_are_shown(self) -> None:
        """BMP 바이트 없이 RGB24 프레임만 와도 캔버스에 픽셀 그대로 표시하는지 검증"""
        frame = DecodedFrame(
            width=2, height=1, bytes_per_line=6, data=bytes([255, 0, 0, 0, 255, 0])
        )

        class _Raw(_Extractor):
            def extract_pair(
                self,
                original: FrameSource,
                compared: FrameSource,
                *,
                time_sec: float,
                frame_width: int,
                frame_height: int,
                cancel_event: threading.Event,
            ) -> ExtractedFrames:
                del original, compared, time_sec, frame_width, frame_height, cancel_event
                return ExtractedFrames(
                    original_bytes=b"",
                    compared_bytes=b"",
                    original_frame=frame,
                    compared_frame=frame,
                )

        view = self.make_view(_Raw())
        view.load_session(self.session())
        _wait(view)
        image = view._canvas._original
        self.assertIsNotNone(image)
        assert image is not None
        self.assertEqual(image.pixelColor(0, 0).name(), "#ff0000")
        self.assertEqual(image.pixelColor(1, 0).name(), "#00ff00")

    def test_unreadable_frame_bytes_show_a_decode_error(self) -> None:
        class _Broken(_Extractor):
            def extract_pair(
                self,
                original: FrameSource,
                compared: FrameSource,
                *,
                time_sec: float,
                frame_width: int,
                frame_height: int,
                cancel_event: threading.Event,
            ) -> ExtractedFrames:
                del original, compared, time_sec, frame_width, frame_height, cancel_event
                return ExtractedFrames(
                    original_bytes=b"not an image", compared_bytes=b"not an image"
                )

        view = self.make_view(_Broken())
        view.load_session(self.session())
        _wait(view)
        self.assertEqual(view._stage.title.text(), layout.ERROR_TITLE)
        self.assertEqual(view._stage.body.text(), "추출한 프레임 이미지를 읽지 못했습니다.")

    def test_malformed_outcomes_are_ignored(self) -> None:
        self.load()
        before = self.view._canvas._original
        self.view._on_frames_extracted("잘못된 값")
        self.view._on_extraction_failed("잘못된 값")
        self.view._on_frames_extracted(ExtractOutcome(1, None))
        self.assertIs(self.view._canvas._original, before)



    def test_stop_playback_cancels_a_running_extraction(self) -> None:
        started = threading.Event()
        cancelled: list[bool] = []

        class _Blocking(_Extractor):
            def extract_pair(
                self,
                original: FrameSource,
                compared: FrameSource,
                *,
                time_sec: float,
                frame_width: int,
                frame_height: int,
                cancel_event: threading.Event,
            ) -> ExtractedFrames:
                del original, compared, time_sec, frame_width, frame_height
                started.set()
                cancel_event.wait(_WAIT_SECONDS)
                cancelled.append(cancel_event.is_set())
                raise InterruptedError("취소")

        view = self.make_view(_Blocking())
        view.load_session(self.session())
        deadline = time.monotonic() + _WAIT_SECONDS
        while not started.is_set() and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        self.assertTrue(started.is_set())
        view.stop_playback()
        self.assertFalse(view._extraction.busy)
        self.assertTrue(cancelled)


class ThemeAndLayoutTests(_ViewCase):
    """다크 시네마 스타일과 상단 바 구성"""

    def test_theme_applies_the_canvas_and_top_bar_palette(self) -> None:
        self.load()
        self.view.apply_theme(LIGHT_PALETTE)
        self.assertIs(self.view._canvas._palette, LIGHT_PALETTE)
        self.assertFalse(self.view._top.collapse_button.icon().isNull())

    def test_top_bar_has_two_rows_and_no_extra_score_row(self) -> None:
        self.load()
        self.assertTrue(self.view._top.score.parent() is not None)
        self.assertIsNone(self.view.findChild(QLabel, "CompareScore"))
        self.assertIsNone(self.view.findChild(QLabel, "CompareShortcuts"))
        self.assertEqual(self.view._top.collapse_button.accessibleName(), "조작 바 접기")

    def test_zoom_input_has_a_visible_inner_editor(self) -> None:
        """전역 입력 규칙이 겹쳐도 배율 숫자 칸이 최소 높이를 갖는지 검증"""
        self.load()
        spin = self.view._top.zoom_input
        editor = spin.lineEdit()
        self.assertIsNotNone(editor)
        assert editor is not None
        self.assertGreaterEqual(editor.sizeHint().height(), editor.fontMetrics().height())
        self.assertGreaterEqual(spin.sizeHint().height(), spin.fontMetrics().height())

    def test_minimum_width_fits_the_default_font(self) -> None:
        self.load()
        self.view.resize(720, 520)
        _APP.processEvents()
        self.assertLessEqual(self.view.minimumSizeHint().width(), 720)


class ShortcutSyncTests(_ViewCase):
    """단축키 변경이 상단 바 툴팁에 반영되는지"""

    def test_sync_shortcuts_updates_top_bar_tooltips(self) -> None:
        self.load(
            evaluation=make_eval(
                make_video(filename="video.mp4"),
                target_tier=RESOLUTION_TIER_BY_KEY["1080p"],
                target_width=1920,
                target_height=1080,
            )
        )
        self.view.set_target("720p")
        effective = dict(self.shortcuts)
        effective["compare.mode_split"] = "F2"
        effective["edit.undo"] = "Ctrl+Alt+Z"
        effective["manual_target.keep"] = "Ctrl+Shift+K"
        self.shortcuts.update(effective)
        self.view.sync_shortcuts(effective)
        _APP.processEvents()
        self.assertIn("[F2]", self.view._top.mode_buttons[MODE_SPLIT].toolTip())
        self.assertIn("[Ctrl+Alt+Z]", self.view._top.apply_output_button.toolTip())
        labels = [action.text() for action in self.view._top.output_menu.actions()]
        self.assertTrue(any("Ctrl+Shift+K" in label for label in labels))


class OutputControlTests(_ViewCase):
    """화질 비교 창 출력 해상도 드롭다운과 이 규격으로 지정 버튼"""

    def evaluation(self, **overrides: object) -> VideoEvaluation:
        """표 판정 결과 대역, 기본은 화질 분석에서 1080p 축소 후보가 나온 영상"""
        fields: dict[str, object] = {
            "target_tier": RESOLUTION_TIER_BY_KEY["1080p"],
            "target_width": 1920,
            "target_height": 1080,
            **overrides,
        }
        return make_eval(make_video(filename="video.mp4"), **fields)

    def test_without_a_report_the_output_controls_stay_disabled(self) -> None:
        self.load()
        top = self.view._top
        self.assertEqual(top.output_button.text(), "출력 -")
        self.assertFalse(top.output_button.isEnabled())
        self.assertEqual(top.output_button.toolTip(), layout.OUTPUT_UNAVAILABLE_TIP)
        self.assertFalse(top.apply_output_button.isEnabled())
        self.assertEqual(top.apply_output_button.toolTip(), layout.APPLY_OUTPUT_NO_EVAL_TIP)
        self.view._apply_output("1080p")
        self.assertEqual(self.applied, [])

    def test_auto_output_shows_the_judged_tier_and_marks_the_candidate(self) -> None:
        """자동 판정 규격을 함께 보이고, 후보 규격과 단축키를 출력 메뉴에 표시하는지 검증"""
        self.load(evaluation=self.evaluation())
        top = self.view._top
        self.assertEqual(top.output_button.text(), "출력 자동 (1080p)")
        labels = [action.text() for action in top.output_menu.actions()]
        marked = [label for label in labels if "(축소 후보)" in label]
        self.assertEqual(len(marked), 1)
        self.assertTrue(marked[0].startswith("1080p"))
        self.assertTrue(any("\t" in label for label in labels))
        self.assertFalse(top.apply_output_button.isEnabled())
        self.assertEqual(top.apply_output_button.toolTip(), layout.APPLY_OUTPUT_RESULT_TIP)

    def test_apply_button_writes_the_compared_tier_through_the_table(self) -> None:
        """이 규격으로 지정이 비교 중인 규격과 되돌리기 단축키를 안내하고 한 번에 적용하는지 검증"""
        ev = self.evaluation()
        self.load(evaluation=ev)
        self.view.set_target("720p")
        top = self.view._top
        self.assertTrue(top.apply_output_button.isEnabled())
        self.assertIn("720p", top.apply_output_button.toolTip())
        self.assertIn("[Ctrl+Z]", top.apply_output_button.toolTip())
        notices: list[str] = []
        self.view.notice_requested.connect(notices.append)
        top.apply_output_button.click()
        self.assertEqual(self.applied, [(ev, "720p")])
        self.assertEqual(notices, ["출력 해상도 지정: 720p"])
        self.assertEqual(top.output_button.text(), "출력 720p")
        self.assertFalse(top.apply_output_button.isEnabled())
        self.assertEqual(
            top.apply_output_button.toolTip(),
            layout.APPLY_OUTPUT_SAME_TIP_TEMPLATE.format(tier="720p", reason="직접 지정"),
        )

    def test_keep_and_auto_values_from_the_menu_are_applied(self) -> None:
        """같은 값을 다시 고르면 요청하지 않고 축소 안 함과 자동은 그대로 지정하는지 검증"""
        ev = self.evaluation()
        self.load(evaluation=ev)
        self.view.set_target("720p")
        self.view._apply_output(None)
        self.view._apply_output(KEEP_RESOLUTION_KEY)
        self.view._apply_output(None)
        self.assertEqual(
            [value for _ev, value in self.applied], [KEEP_RESOLUTION_KEY, None]
        )
        self.assertEqual(self.view._top.output_button.text(), "출력 자동")
        self.assertTrue(self.view._top.apply_output_button.isEnabled())

    def test_busy_table_keeps_the_previous_value(self) -> None:
        self.apply_result = False
        self.load(evaluation=self.evaluation())
        self.view.set_target("720p")
        notices: list[str] = []
        self.view.notice_requested.connect(notices.append)
        self.view._top.apply_output_button.click()
        self.assertEqual(notices, [view_module.OUTPUT_BUSY_NOTICE])
        self.assertEqual(self.view._top.output_button.text(), "출력 자동 (1080p)")

    def test_blocked_rows_show_the_reason_and_ignore_the_button(self) -> None:
        self.load(evaluation=self.evaluation(smart_action=SmartAction.PASS_EXCEPTION))
        self.view.set_target("720p")
        top = self.view._top
        self.assertFalse(top.apply_output_button.isEnabled())
        self.assertEqual(top.apply_output_button.toolTip(), layout.APPLY_OUTPUT_BLOCKED_TIP)
        notices: list[str] = []
        self.view.notice_requested.connect(notices.append)
        self.view._apply_selected_target()
        self.assertEqual(notices, [view_module.OUTPUT_BLOCKED_NOTICE])
        self.assertEqual(self.applied, [])

    def test_manual_target_value_is_shown_and_disables_the_same_tier(self) -> None:
        self.load(evaluation=self.evaluation(manual_target="720p"))
        self.view.set_target("720p")
        self.assertEqual(self.view._top.output_button.text(), "출력 720p")
        self.assertFalse(self.view._top.apply_output_button.isEnabled())
        self.assertEqual(
            self.view._top.apply_output_button.toolTip(),
            layout.APPLY_OUTPUT_SAME_TIP_TEMPLATE.format(tier="720p", reason="직접 지정"),
        )

    def test_auto_applied_tier_disables_the_same_tier_with_the_reason(self) -> None:
        """자동 판정으로 이미 같은 규격이면 사유와 함께 버튼을 비활성화하는지 검증"""
        self.load(evaluation=self.evaluation(downscale_reason=DownscaleReason.FRAME_ANALYSIS))
        self.view.set_target("1080p")
        self.assertFalse(self.view._top.apply_output_button.isEnabled())
        self.assertEqual(
            self.view._top.apply_output_button.toolTip(),
            layout.APPLY_OUTPUT_SAME_TIP_TEMPLATE.format(tier="1080p", reason="화질 분석 축소"),
        )

    def test_apply_on_the_result_target_does_nothing(self) -> None:
        self.load(evaluation=self.evaluation())
        self.view._apply_selected_target()
        self.assertEqual(self.applied, [])

    def test_open_output_menu_blocks_auto_hide(self) -> None:
        self.load(evaluation=self.evaluation())
        self.view._top.output_menu.popup(QPoint(10, 10))
        _APP.processEvents()
        self.assertFalse(self.view._can_auto_hide())
        self.assertTrue(self.view._top.close_open_menu())
        _APP.processEvents()
        self.assertTrue(self.view._can_auto_hide())

    def test_sync_report_matches_the_table_state(self) -> None:
        """표 재평가 결과가 출력 문구, 메뉴 체크, 지정 버튼 상태에 반영되는지 검증"""
        self.load(evaluation=self.evaluation())
        self.view.set_target("720p")
        self.view._apply_output("720p")
        self.assertEqual(self.view._top.output_button.text(), "출력 720p")
        updated = make_eval(
            make_video(filename="video.mp4"),
            target_tier=RESOLUTION_TIER_BY_KEY["720p"],
            target_width=1280,
            target_height=720,
            downscale_reason=DownscaleReason.FRAME_ANALYSIS,
        )
        self.view.sync_report(make_report([updated]))
        self.assertEqual(self.view._top.output_button.text(), "출력 자동 (720p)")
        self.assertFalse(self.view._top.apply_output_button.isEnabled())
        self.assertEqual(
            self.view._top.apply_output_button.toolTip(),
            layout.APPLY_OUTPUT_SAME_TIP_TEMPLATE.format(tier="720p", reason="화질 분석 축소"),
        )
        checked = [
            action.text() for action in self.view._top.output_menu.actions() if action.isChecked()
        ]
        self.assertEqual(len(checked), 1)
        self.assertTrue(checked[0].startswith("자동"))

    def test_cross_tier_sync_reenables_the_apply_button(self) -> None:
        """표에서 직접 지정이 다른 규격으로 바뀌면 지정 버튼이 다시 활성화되는지 검증"""
        self.load(evaluation=self.evaluation(manual_target="1080p"))
        self.view.set_target("720p")
        self.assertTrue(self.view._top.apply_output_button.isEnabled())
        self.view.sync_evaluation(self.evaluation(manual_target="720p"))
        self.assertFalse(self.view._top.apply_output_button.isEnabled())
        self.assertEqual(self.view._top.output_button.text(), "출력 720p")


class CompareTopBarStateTests(GuiTestCase):
    """비교 뷰가 저장된 상단 조작 바 접힘 상태를 적용하고 변경을 저장 쪽에 알리는지"""

    def test_initial_collapsed_state_is_restored_and_h_toggle_is_reported(self) -> None:
        changes: list[bool] = []
        view = FrameCompareView(
            _Extractor(),
            lambda: 5,
            lambda: DEFAULT_SHORTCUTS,
            lambda: False,
            lambda: False,
            collapsed=True,
            on_collapsed_changed=changes.append,
        )
        self.addCleanup(view.deleteLater)
        self.assertTrue(view._top.is_collapsed())
        self.assertEqual(changes, [])
        self.assertTrue(_press(view, Qt.Key.Key_H))
        self.assertFalse(view._top.is_collapsed())
        self.assertEqual(changes, [False])


if __name__ == "__main__":
    unittest.main(verbosity=2)

