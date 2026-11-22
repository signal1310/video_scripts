"""화질 비교 창 검증: 세션 적재, 비교 대상 전환, 점수 줄, 3모드, 단축키, 로딩과 오류, 스레드 정리

- 추출기는 가짜로 바꿔 실제 미디어와 FFmpeg 없이 확인
"""

import os
import tempfile
import threading
import time
import unittest

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
from src.gui.dialogs.frame_compare_window import CompareSession, FrameCompareWindow
from src.gui.dialogs.frame_compare_worker import ExtractOutcome
from src.gui.styles.palettes import LIGHT_PALETTE
from src.service.frame_compare_service import (
    ExtractedFrames,
    FrameCompareExecutionError,
    FrameCompareFileMissing,
)
from tests._gui_helpers import GuiTestCase, release_modifier_keys

_APP = QApplication.instance() or QApplication([])
_WAIT_SECONDS: float = 5.0


def _png(color: str) -> bytes:
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


_ORIGINAL_PNG: bytes = _png("#FF0000")
_COMPARED_PNG: bytes = _png("#0000FF")


def _big_png(width: int, height: int, color: str) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


_BIG_ORIGINAL_PNG: bytes = _big_png(800, 600, "#FF0000")
_BIG_COMPARED_PNG: bytes = _big_png(800, 600, "#0000FF")


class _Extractor:
    """호출을 기록하고 정해진 PNG나 오류를 돌려주는 가짜 추출기"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float, int, int]] = []
        self.failure: Exception | None = None

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
        return ExtractedFrames(original_png=_ORIGINAL_PNG, compared_png=_COMPARED_PNG)


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
        return ExtractedFrames(original_png=_BIG_ORIGINAL_PNG, compared_png=_BIG_COMPARED_PNG)


def _wait(window: FrameCompareWindow) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while window._worker is not None and time.monotonic() < deadline:
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
        window._start_pending_extract()
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
        self.window._start_pending_extract()
        _wait(self.window)
        self.assertIn("boom", self.window._stage.body.text())
        self.extractor.failure = None
        self.window.set_time(30.0)
        self.window._start_pending_extract()
        _wait(self.window)
        self.assertTrue(self.window._stage.overlay.isHidden())

    def test_loading_overlay_uses_the_approved_text(self) -> None:
        self.load()
        self.window._show_loading()
        self.assertFalse(self.window._stage.overlay.isHidden())
        self.assertEqual(self.window._stage.title.text(), "프레임을 불러오는 중...")
        self.assertEqual(self.window._stage.body.text(), "")

    def test_unreadable_png_shows_a_decode_error(self) -> None:
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
                return ExtractedFrames(original_png=b"not a png", compared_png=b"not a png")

        window = FrameCompareWindow(_Broken(), lambda: 5, None)
        self.addCleanup(window.deleteLater)
        window.load_session(self.session())
        _wait(window)
        self.assertEqual(window._stage.title.text(), layout.ERROR_TITLE)
        self.assertEqual(window._stage.body.text(), "추출한 프레임 이미지를 읽지 못했습니다.")

    def test_stale_request_result_is_discarded(self) -> None:
        self.load()
        before = self.window._canvas._original
        self.window._request_id += 1
        stale = ExtractOutcome(
            self.window._request_id - 1,
            ExtractedFrames(original_png=_ORIGINAL_PNG, compared_png=_COMPARED_PNG),
            time_sec=1.0,
        )
        self.window._on_worker_completed(stale)
        self.assertIs(self.window._canvas._original, before)
        self.window._on_worker_completed("잘못된 값")
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

        window = FrameCompareWindow(_Blocking(), lambda: 5, None)
        self.addCleanup(window.deleteLater)
        window.load_session(self.session())
        deadline = time.monotonic() + _WAIT_SECONDS
        while not started.is_set() and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        self.assertTrue(started.is_set())
        window.close()
        self.assertIsNone(window._worker)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
