"""화질 비교 창 검증: 세션 적재, 비교 대상 전환, 점수 줄, 3모드, 단축키, 재생, 로딩과 오류, 정리

- 추출기는 가짜로 바꿔 실제 미디어와 FFmpeg 없이 확인
- 재생은 가짜 재생 제어기로 바꿔 시점과 상태 전이만 확인
"""

import os
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QEvent, QIODevice, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.analysis.frame_analysis import TierScore
from src.core.analysis.frame_compare import (
    NO_TARGET_REASON,
    ComparePlan,
    FrameSource,
    resolve_compare_plan,
    result_target,
)
from src.gui.constants import make_ui_font
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs.frame_compare_canvas import MODE_SIDE, MODE_SPLIT, MODE_TOGGLE
from src.gui.dialogs.frame_compare_meta import CompareSession
from src.gui.dialogs.frame_compare_player import ComparePlaybackControls
from src.gui.dialogs.frame_compare_window import FrameCompareWindow
from src.gui.dialogs.frame_compare_worker import ExtractOutcome
from src.gui.styles.palettes import LIGHT_PALETTE
from src.service.frame_compare_decoder import DecodedFrame
from src.service.frame_compare_service import (
    ExtractedFrames,
    FrameCompareExecutionError,
    FrameCompareFileMissing,
)
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


def _big_bmp(width: int, height: int, color: str) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "BMP")
    return bytes(buffer.data())


_BIG_ORIGINAL_BYTES: bytes = _big_bmp(800, 600, "#FF0000")
_BIG_COMPARED_BYTES: bytes = _big_bmp(800, 600, "#0000FF")


class _Extractor:
    """호출을 기록하고 정해진 프레임 바이트나 오류를 돌려주는 가짜 추출기"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float, int, int]] = []
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
        if self.failure is not None:
            raise self.failure
        return ExtractedFrames(original_bytes=_ORIGINAL_BYTES, compared_bytes=_COMPARED_BYTES)

    def release_decoders(self) -> None:
        self.released += 1


class _BigExtractor(_Extractor):
    """확대와 이동을 확인할 수 있도록 큰 프레임을 돌려주는 가짜 추출기"""

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
        return ExtractedFrames(
            original_bytes=_BIG_ORIGINAL_BYTES, compared_bytes=_BIG_COMPARED_BYTES
        )


class _FakePlayer(ComparePlaybackControls):
    """재생 엔진 없이 창의 재생 분기만 확인하는 가짜 제어기"""

    def __init__(self, timeline: layout.CompareTimeline) -> None:
        self.calls: list[str] = []
        self.playing: bool = False
        self.position: int = 0
        self.rate_value: float = 1.0
        self.muted_value: bool = False
        self.volume: int = 100
        self.loaded: dict[str, object] = {}
        super().__init__(timeline, None)

    def load(
        self,
        *,
        original_path: str,
        compared_path: str,
        tier_key: str | None,
        frame_width: int,
        frame_height: int,
        position_ms: int,
    ) -> None:
        self.loaded = {
            "original_path": original_path,
            "compared_path": compared_path,
            "tier_key": tier_key,
            "position_ms": position_ms,
        }
        self.calls.append("load")

    def start(self) -> None:
        self.playing = True
        self.calls.append("start")

    def pause(self) -> None:
        self.playing = False
        self.calls.append("pause")

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

    def select_rate(self, rate: float) -> None:
        self.rate_value = rate
        self.calls.append("rate")

    def step_rate(self, step: int) -> None:
        self.calls.append(f"step_rate:{step}")

    def reset_rate(self) -> None:
        self.calls.append("reset_rate")

    def toggle_mute(self) -> None:
        self.muted_value = not self.muted_value
        self.calls.append("mute")

    def set_volume(self, value: int) -> None:
        self.volume = value
        self.calls.append("volume")

    def save_audio(self) -> None:
        self.calls.append("save_audio")

    def sync_controls(self) -> None:
        self.calls.append("sync")

    def reload_settings(self) -> None:
        self.calls.append("reload")



def _wait(window: FrameCompareWindow) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while window._extraction.busy and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)
    _APP.processEvents()


def _press(widget: FrameCompareWindow, key: Qt.Key, modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier) -> None:
    """창에 키 누름 사건을 직접 보내 수식키 상태를 남기지 않음"""
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, modifier))


def _press_repeat(widget: FrameCompareWindow, key: Qt.Key) -> None:
    """키를 길게 눌렀을 때 오는 자동 반복 사건"""
    QApplication.sendEvent(
        widget, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, "", True)
    )


def _mouse(event_type: QEvent.Type, x: float, y: float, button: Qt.MouseButton) -> QMouseEvent:
    point = QPointF(x, y)
    buttons = Qt.MouseButton.LeftButton if event_type == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton
    return QMouseEvent(event_type, point, QPointF(x, y), button, buttons, Qt.KeyboardModifier.NoModifier)


def _wheel(x: float, y: float, delta: int) -> QWheelEvent:
    point = QPointF(x, y)
    return QWheelEvent(
        point,
        QPointF(x, y),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


class _WindowCase(GuiTestCase):
    """임시 미디어 파일과 비교 계획, 세션을 준비하는 기반"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = os.path.join(self.tmp.name, "video.mp4")
        self.result = os.path.join(self.tmp.name, "video_out.mp4")
        for path in (self.source, self.result):
            with open(path, "wb") as handle:
                handle.write(b"x")
        self.extractor = _Extractor()
        self.window = FrameCompareWindow(self.extractor, lambda: 5, None)
        self.addCleanup(self.window.deleteLater)
        self.window.show()
        _APP.processEvents()

    def session(self, *, with_result: bool = True, scores: tuple[TierScore, ...] = ()) -> CompareSession:
        result = (
            result_target(self.result, width=1920, height=1080, size_bytes=580 * 1024 * 1024)
            if with_result
            else None
        )
        plan = resolve_compare_plan(
            source_path=self.source,
            width=3840,
            height=2160,
            size_bytes=1024**3,
            result=result,
            candidate_tier="1080p",
        )
        return CompareSession(
            plan=plan,
            duration_sec=100.0,
            fps=30.0,
            filename="video.mp4",
            position_sec=10.0,
            scores=scores,
            width=3840,
            height=2160,
            source_path=self.source,
        )

    def load(self, *, with_result: bool = True, scores: tuple[TierScore, ...] = ()) -> None:
        self.window.load_session(self.session(with_result=with_result, scores=scores))
        _wait(self.window)


class LoadSessionTests(_WindowCase):
    """세션 적재 뒤 제목, 메타, 비교 대상, 프레임 상태"""

    def test_default_target_is_result_and_frames_load(self) -> None:
        self.load()
        self.assertEqual(self.window._toolbar.filename.text(), "video.mp4")
        self.assertIn("video.mp4", self.window.windowTitle())
        self.assertEqual(self.window._toolbar.target_button.text(), "결과")
        self.assertIn("원본 3840x2160", self.window._meta.original.text())
        self.assertIn("결과 1920x1080", self.window._meta.target.text())
        self.assertEqual(self.window._meta.time.text(), "시점 00:10.000")
        labels = [action.text() for action in self.window._toolbar.target_menu.actions()]
        self.assertEqual(labels[0], "결과")
        self.assertIn("1080p 축소 비교", labels)
        self.assertIsNotNone(self.window._canvas._original)
        self.assertTrue(self.window._stage.overlay.isHidden())
        self.assertEqual(self.extractor.calls[0][2], 10.0)
        self.assertEqual(self.extractor.calls[0][3:], (3840, 2160))

    def test_cached_score_is_shown_only_for_its_tier(self) -> None:
        scores = (TierScore(tier="1080p", ssim=0.9983, vmaf=98.7),)
        self.load(scores=scores)
        self.assertTrue(self.window._score_label.isHidden())
        self.window.set_target("1080p")
        _wait(self.window)
        self.assertFalse(self.window._score_label.isHidden())
        self.assertEqual(self.window._score_label.text(), "화질 분석 1080p: VMAF 98.7, SSIM 0.9983")
        self.assertIn("VMAF는 100에 가까울수록", self.window._score_label.toolTip())
        self.assertEqual(self.window._toolbar.target_button.text(), "1080p 축소 비교")

    def test_plan_without_target_shows_error_overlay(self) -> None:
        plan = ComparePlan(
            original=FrameSource(key="원본", path=self.source, label="원본"),
            targets=(),
            default_key=None,
            reason=NO_TARGET_REASON,
        )
        session = CompareSession(plan=plan, duration_sec=100.0, fps=30.0, filename="video.mp4")
        self.window.load_session(session)
        self.assertFalse(self.window._stage.overlay.isHidden())
        self.assertEqual(self.window._stage.title.text(), layout.ERROR_TITLE)
        self.assertEqual(self.window._stage.body.text(), NO_TARGET_REASON)

    def test_missing_original_uses_the_approved_reason(self) -> None:
        os.remove(self.source)
        session = self.session()
        self.window.load_session(session)
        self.assertEqual(self.window._stage.body.text(), "원본 파일이 없어 비교할 수 없습니다.")

    def test_meta_tooltips_show_the_full_paths_of_both_files(self) -> None:
        self.load()
        self.assertEqual(self.window._meta.original.toolTip(), f"원본 파일: {self.source}")
        self.assertEqual(self.window._meta.target.toolTip(), f"결과 파일: {self.result}")

    def test_downscale_target_keeps_its_roundtrip_tooltip(self) -> None:
        self.load()
        self.window.set_target("1080p")
        _wait(self.window)
        self.assertIn("축소", self.window._meta.target.toolTip())


class InteractionTests(_WindowCase):
    """단축키, 모드 전환, 배율, 시점 이동"""

    def test_mode_keys_switch_three_modes(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_2)
        self.assertEqual(self.window._canvas.mode(), MODE_TOGGLE)
        self.assertTrue(self.window._toolbar.mode_buttons[MODE_TOGGLE].isChecked())
        _press(self.window, Qt.Key.Key_3)
        self.assertEqual(self.window._canvas.mode(), MODE_SIDE)
        _press(self.window, Qt.Key.Key_1)
        self.assertEqual(self.window._canvas.mode(), MODE_SPLIT)

    def test_c_key_toggles_original_only_in_toggle_mode(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_C)
        self.assertFalse(self.window._canvas.original_pinned())
        _press(self.window, Qt.Key.Key_2)
        _press(self.window, Qt.Key.Key_C)
        self.assertTrue(self.window._canvas.original_pinned())
        _press(self.window, Qt.Key.Key_C)
        self.assertFalse(self.window._canvas.original_pinned())

    def test_zero_and_plus_minus_change_zoom_and_button_checks(self) -> None:
        self.load()
        self.window.set_zoom_preset("1")
        _press(self.window, Qt.Key.Key_Plus)
        self.assertAlmostEqual(self.window._canvas.scale(), 1.25)
        self.assertFalse(any(button.isChecked() for button in self.window._toolbar.zoom_buttons.values()))
        _press(self.window, Qt.Key.Key_Equal)
        self.assertAlmostEqual(self.window._canvas.scale(), 1.25 * 1.25)
        _press(self.window, Qt.Key.Key_Minus)
        self.assertAlmostEqual(self.window._canvas.scale(), 1.25)
        _press(self.window, Qt.Key.Key_0)
        self.assertTrue(self.window._toolbar.zoom_buttons["fit"].isChecked())

    def test_arrow_keys_seek_by_preview_interval_and_shift_is_six_times(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_Right)
        self.assertAlmostEqual(self.window._position_sec, 15.0)
        _press(self.window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(self.window._position_sec, 45.0)
        _press(self.window, Qt.Key.Key_Left)
        self.assertAlmostEqual(self.window._position_sec, 40.0)
        _press(self.window, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(self.window._position_sec, 10.0)
        release_modifier_keys()

    def test_comma_and_period_step_one_frame(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_Period)
        self.assertAlmostEqual(self.window._position_sec, 10.0 + 1 / 30)
        _press(self.window, Qt.Key.Key_Comma)
        self.assertAlmostEqual(self.window._position_sec, 10.0)

    def test_space_closes_the_open_target_menu_first_then_the_window(self) -> None:
        self.load()
        self.window._toolbar.target_menu.popup(QPoint(10, 10))
        _APP.processEvents()
        self.assertTrue(self.window._toolbar.target_menu.isVisible())
        _press(self.window, Qt.Key.Key_Space)
        _APP.processEvents()
        self.assertFalse(self.window._toolbar.target_menu.isVisible())
        self.assertFalse(self.window.isHidden())
        _press(self.window, Qt.Key.Key_Space)
        self.assertTrue(self.window.isHidden())

    def test_wheel_zoom_clears_preset_checks_and_updates_badge(self) -> None:
        self.load()
        self.window.set_zoom_preset("1")
        self.assertTrue(self.window._toolbar.zoom_buttons["1"].isChecked())
        QApplication.sendEvent(self.window._canvas, _wheel(300, 200, 120))
        self.assertFalse(any(button.isChecked() for button in self.window._toolbar.zoom_buttons.values()))
        self.assertEqual(
            self.window._toolbar.zoom_input.value(), round(self.window._canvas.scale() * 100)
        )

    def test_typed_zoom_applies_immediately_and_clears_preset_checks(self) -> None:
        self.load()
        self.window.set_zoom_preset("1")
        spin = self.window._toolbar.zoom_input
        spin.setFocus()
        spin.setValue(250)
        spin.editingFinished.emit()
        self.assertAlmostEqual(self.window._canvas.scale(), 2.5)
        self.assertFalse(any(button.isChecked() for button in self.window._toolbar.zoom_buttons.values()))
        self.assertEqual(spin.text(), "250%")

    def test_auto_repeat_does_not_flip_the_pinned_original(self) -> None:
        self.load()
        self.window.set_mode(MODE_TOGGLE)
        _press_repeat(self.window, Qt.Key.Key_C)
        self.assertFalse(self.window._canvas.original_pinned())
        _press(self.window, Qt.Key.Key_C)
        self.assertTrue(self.window._canvas.original_pinned())

    def test_new_frames_keep_zoom_and_pan_until_the_next_session(self) -> None:
        window = FrameCompareWindow(_BigExtractor(), lambda: 5, None)
        self.addCleanup(window.deleteLater)
        window.show()
        window.load_session(self.session())
        _wait(window)
        window.set_zoom_preset("200")
        QApplication.sendEvent(
            window._canvas, _mouse(QEvent.Type.MouseButtonPress, 600, 400, Qt.MouseButton.LeftButton)
        )
        QApplication.sendEvent(window._canvas, _mouse(QEvent.Type.MouseMove, 500, 350, Qt.MouseButton.NoButton))
        QApplication.sendEvent(
            window._canvas, _mouse(QEvent.Type.MouseButtonRelease, 500, 350, Qt.MouseButton.NoButton)
        )
        before = (window._canvas.scale(), window._canvas._transform.offset_x, window._canvas._transform.offset_y)
        window.set_time(20.0)
        window._request_precise_frame()
        _wait(window)
        self.assertEqual(
            (window._canvas.scale(), window._canvas._transform.offset_x, window._canvas._transform.offset_y),
            before,
        )
        self.assertFalse(window._canvas._fit_mode)
        window.load_session(self.session())
        _wait(window)
        self.assertTrue(window._canvas._fit_mode)

    def test_mark_chip_moves_to_its_ratio_of_the_duration(self) -> None:
        self.load()
        self.window._timeline.mark_buttons[2].click()
        self.assertAlmostEqual(self.window._position_sec, 30.0)
        self.assertEqual(self.window._meta.time.text(), "시점 00:30.000")
        self.assertTrue(self.window._timeline.mark_buttons[2].isChecked())

    def test_escape_closes_the_window(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_Escape)
        self.assertTrue(self.window.isHidden())

    def test_shift_space_requests_preview_with_the_current_time_and_path(self) -> None:
        self.load()
        self.window.set_time(30.0)
        requests: list[tuple[str, float]] = []
        self.window.preview_requested.connect(lambda path, position: requests.append((path, position)))
        _press(self.window, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(requests, [(self.source, 30.0)])
        self.assertFalse(self.window.isHidden())
        release_modifier_keys()


class ErrorAndThreadTests(_WindowCase):
    """원인별 오류 표시, 지연 결과 폐기와 종료 시 취소"""

    def test_file_missing_and_execution_errors_use_different_bodies(self) -> None:
        self.extractor.failure = FrameCompareFileMissing("파일이 이동되었거나 삭제되었는지 확인해 주세요.")
        self.load()
        self.assertEqual(self.window._stage.title.text(), layout.ERROR_TITLE)
        self.assertEqual(self.window._stage.body.text(), "파일이 이동되었거나 삭제되었는지 확인해 주세요.")
        self.extractor.failure = FrameCompareExecutionError("FFmpeg로 프레임을 추출하지 못했습니다: boom")
        self.window.set_time(20.0)
        self.window._request_precise_frame()
        _wait(self.window)
        self.assertIn("boom", self.window._stage.body.text())
        self.extractor.failure = None
        self.window.set_time(30.0)
        self.window._request_precise_frame()
        _wait(self.window)
        self.assertTrue(self.window._stage.overlay.isHidden())

    def test_loading_overlay_uses_the_approved_text(self) -> None:
        window = FrameCompareWindow(_Extractor(), lambda: 5, None)
        self.addCleanup(window.deleteLater)
        window._show_loading()
        self.assertFalse(window._stage.overlay.isHidden())
        self.assertEqual(window._stage.title.text(), "프레임을 불러오는 중...")
        self.assertEqual(window._stage.body.text(), "")

    def test_loading_after_a_frame_shows_only_the_pill(self) -> None:
        """프레임이 있는 상태에서는 전체 오버레이 대신 알약 배지만 쓰는지 검증"""
        self.load()
        self.window._show_loading()
        self.assertTrue(self.window._stage.overlay.isHidden())
        self.assertTrue(self.window._stage.pill.isHidden())
        self.window._reveal_loading()
        self.assertFalse(self.window._stage.pill.isHidden())
        self.assertTrue(self.window._stage.overlay.isHidden())
        self.window._on_playback_frames(
            QImage(40, 20, QImage.Format.Format_RGB32), QImage(40, 20, QImage.Format.Format_RGB32)
        )
        self.assertTrue(self.window._stage.pill.isHidden())
        self.window._hide_overlay()
        self.assertTrue(self.window._stage.pill.isHidden())

    def test_raw_rgb_frames_from_the_memory_decoder_are_shown(self) -> None:
        """BMP 바이트 없이 RGB24 프레임만 와도 캔버스에 픽셀 그대로 표시하는지 검증"""
        frame = DecodedFrame(
            width=2,
            height=1,
            bytes_per_line=6,
            data=bytes([255, 0, 0, 0, 255, 0]),
        )

        class _Raw:
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
                return ExtractedFrames(original_bytes=b"", compared_bytes=b"", original_frame=frame, compared_frame=frame)

            def release_decoders(self) -> None:
                pass

        window = FrameCompareWindow(_Raw(), lambda: 5, None)
        self.addCleanup(window.deleteLater)
        window.load_session(self.session())
        _wait(window)
        image = window._canvas._original
        self.assertIsNotNone(image)
        assert image is not None
        self.assertEqual(image.pixelColor(0, 0).name(), "#ff0000")
        self.assertEqual(image.pixelColor(1, 0).name(), "#00ff00")

    def test_unreadable_frame_bytes_show_a_decode_error(self) -> None:
        class _Broken:
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
                return ExtractedFrames(
                    original_bytes=b"not an image", compared_bytes=b"not an image"
                )

            def release_decoders(self) -> None:
                pass

        window = FrameCompareWindow(_Broken(), lambda: 5, None)
        self.addCleanup(window.deleteLater)
        window.load_session(self.session())
        _wait(window)
        self.assertEqual(window._stage.title.text(), layout.ERROR_TITLE)
        self.assertEqual(window._stage.body.text(), "추출한 프레임 이미지를 읽지 못했습니다.")

    def test_malformed_outcomes_are_ignored(self) -> None:
        self.load()
        before = self.window._canvas._original
        self.window._on_frames_extracted("잘못된 값")
        self.window._on_extraction_failed("잘못된 값")
        self.window._on_frames_extracted(ExtractOutcome(1, None))
        self.assertIs(self.window._canvas._original, before)

    def test_close_cancels_a_running_extraction(self) -> None:
        started = threading.Event()
        cancelled: list[bool] = []

        class _Blocking:
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
                started.set()
                cancel_event.wait(_WAIT_SECONDS)
                cancelled.append(cancel_event.is_set())
                raise InterruptedError("취소")

            def release_decoders(self) -> None:
                pass

        window = FrameCompareWindow(_Blocking(), lambda: 5, None)
        self.addCleanup(window.deleteLater)
        window.load_session(self.session())
        deadline = time.monotonic() + _WAIT_SECONDS
        while not started.is_set() and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        self.assertTrue(started.is_set())
        window.close()
        self.assertFalse(window._extraction.busy)
        self.assertTrue(cancelled)

    def _assert_controls_fit(self, container: QWidget, window: FrameCompareWindow) -> None:
        for child in container.findChildren(QWidget):
            if not child.isVisibleTo(window):
                continue
            right = child.mapTo(window, child.rect().topRight()).x()
            self.assertLessEqual(right, window.width(), f"{child.objectName()} 넘침")

    def test_theme_styles_and_thirteen_point_layout_fits_minimum_width(self) -> None:
        self.load()
        self.window.apply_theme(LIGHT_PALETTE)
        self.assertIn(LIGHT_PALETTE.bg_card, self.window.styleSheet())
        original = QApplication.font()
        self.addCleanup(QApplication.setFont, original)
        QApplication.setFont(make_ui_font(13))
        self.window.resize(720, 520)
        _APP.processEvents()
        self.assertLessEqual(self.window.minimumSizeHint().width(), 720)
        self._assert_controls_fit(self.window, self.window)
        buttons = list(self.window._toolbar.mode_buttons.values()) + list(
            self.window._toolbar.zoom_buttons.values()
        )
        for button in buttons:
            self.assertGreaterEqual(
                button.sizeHint().width(), button.fontMetrics().horizontalAdvance(button.text())
            )
        self.window.resize(1180, 820)
        _APP.processEvents()
        self._assert_controls_fit(self.window, self.window)
        self.assertLessEqual(
            self.window._timeline.shortcuts.mapTo(self.window, self.window._timeline.shortcuts.rect().topRight()).x(),
            self.window.width(),
        )


class PlaybackTests(_WindowCase):
    """재생 단축키, 일시중지 정밀 프레임, 재생 중 이동, 끝 도달, 창 정리"""

    def setUp(self) -> None:
        super().setUp()
        self.player = _FakePlayer(self.window._timeline)
        self.addCleanup(self.player.deleteLater)
        self.window._player = self.player

    def test_load_session_loads_the_player_at_the_session_time(self) -> None:
        self.load()
        self.assertEqual(self.player.loaded["original_path"], self.source)
        self.assertEqual(self.player.loaded["compared_path"], self.result)
        self.assertIsNone(self.player.loaded["tier_key"])
        self.assertEqual(self.player.loaded["position_ms"], 10_000)

    def test_k_starts_and_pauses_keeping_the_current_position(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_K)
        self.assertIn("start", self.player.calls)
        self.player.playing = True
        self.window.set_time(50.0)
        self.assertIn("seek", self.player.calls)
        self.player.position = 50_000
        _press(self.window, Qt.Key.Key_K)
        self.assertIn("pause", self.player.calls)
        self.assertAlmostEqual(self.window.position_sec, 50.0)

    def test_pause_requests_a_precise_frame(self) -> None:
        self.load()
        self.player.playing = True
        self.player.position = 12_000
        with mock.patch.object(self.window, "_request_precise_frame") as request:
            self.window.toggle_playback()
        request.assert_called_once()

    def test_step_keys_pause_before_moving(self) -> None:
        self.load()
        self.player.playing = True
        _press(self.window, Qt.Key.Key_Period)
        self.assertIn("pause", self.player.calls)
        self.assertAlmostEqual(self.window.position_sec, 10.0 + 1 / 30)

    def test_rate_and_mute_keys_use_the_shared_player(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_BracketLeft)
        _press(self.window, Qt.Key.Key_BracketRight)
        _press(self.window, Qt.Key.Key_Backslash)
        _press(self.window, Qt.Key.Key_M)
        self.assertIn("step_rate:-1", self.player.calls)
        self.assertIn("step_rate:1", self.player.calls)
        self.assertIn("reset_rate", self.player.calls)
        self.assertIn("mute", self.player.calls)

    def test_space_stops_playback_then_hides(self) -> None:
        self.load()
        self.player.playing = True
        _press(self.window, Qt.Key.Key_Space)
        self.assertIn("stop", self.player.calls)
        self.assertTrue(self.window.isHidden())

    def test_shift_space_stops_and_requests_preview(self) -> None:
        self.load()
        requests: list[tuple[str, float]] = []
        self.window.preview_requested.connect(
            lambda path, position: requests.append((path, position))
        )
        _press(self.window, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)
        self.assertIn("stop", self.player.calls)
        self.assertEqual(requests, [(self.source, 10.0)])
        release_modifier_keys()

    def test_target_change_while_playing_keeps_playing(self) -> None:
        self.load()
        self.player.playing = True
        self.window.set_target("1080p")
        self.assertEqual(self.player.loaded["tier_key"], "1080p")
        self.assertIn("start", self.player.calls)

    def test_playback_frames_and_position_update_the_view(self) -> None:
        self.load()
        image = QImage(40, 20, QImage.Format.Format_RGB32)
        image.fill(QColor("#00FF00"))
        self.player.playing = True
        self.window._on_playing_changed(True)
        self.assertIn("sync", self.player.calls)
        self.window._on_playback_frames(image, image)
        self.assertIsNotNone(self.window._canvas._compared)
        self.window._on_playback_position(20_000)
        self.assertAlmostEqual(self.window.position_sec, 20.0)
        self.window._on_playback_frames("잘못된 값", image)
        self.player.playing = False
        self.window._on_playback_position(30_000)
        self.assertAlmostEqual(self.window.position_sec, 20.0)

    def test_end_of_media_keeps_the_last_position(self) -> None:
        self.load()
        self.window._on_playback_end()
        self.assertAlmostEqual(self.window.position_sec, 100.0)
        self.assertEqual(self.window._meta.time.text(), "시점 01:40.000")

    def test_hide_stops_playback_and_releases_the_extraction(self) -> None:
        self.load()
        self.player.playing = True
        self.window.hide()
        self.assertIn("stop", self.player.calls)
        self.assertFalse(self.window._extraction.busy)
        self.assertGreaterEqual(self.extractor.released, 1)

    def test_load_session_reloads_shared_playback_settings(self) -> None:
        """창을 다시 열 때 미리 보기와 공유하는 음량, 배속을 다시 읽는지 검증"""
        self.load()
        self.assertIn("reload", self.player.calls)

    def test_start_playback_only_starts_when_paused(self) -> None:
        """이미 재생 중이면 다시 시작하지 않는지 검증"""
        self.load()
        self.window.start_playback()
        starts = self.player.calls.count("start")
        self.window.start_playback()
        self.assertEqual(self.player.calls.count("start"), starts)
        self.assertTrue(self.window.is_playing)

    def test_is_playing_follows_the_player(self) -> None:
        """미리 보기로 넘길 재생 상태를 재생기에서 읽는지 검증"""
        self.load()
        self.assertFalse(self.window.is_playing)
        self.player.playing = True
        self.assertTrue(self.window.is_playing)

    def test_volume_slider_changes_are_forwarded_to_the_player(self) -> None:
        self.load()
        self.window._timeline.volume_slider.setValue(35)
        self.assertIn("volume", self.player.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)

